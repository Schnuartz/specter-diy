# detect if it's a hardware device or linuxport
import sys
import os
import pyb

simulator = (sys.platform in ["linux", "darwin"])


# Build metadata injected at boot time. Defaults represent the minimum
# information we can know without platform-specific boot scripts.
bootloader_locked = None
build_type = "unknown"

if simulator:
    build_type = "unix"
    bootloader_locked = False

try:
    import config
except:
    import config_default as config

if not simulator:
    import sdram
    import stm

    sdram.init()
else:
    _PREALLOCATED = bytes(0x100000)
    stm = None

# injected by the boot.py
i2c = None # I2C to talk to the battery


class CriticalErrorWipeImmediately(Exception):
    """
    This exception should be raised when device needs to be wiped
    because something terrible happened
    """

    pass


def maybe_mkdir(path):
    try:
        os.mkdir(path)
    except:
        pass
    if not simulator:
        os.sync()


class SDCard:
    _mounted = False

    def __init__(self, sd = None, led = None):
        self._sd = sd
        self._led = led
        if led is not None:
            led.off()

    @property
    def is_present(self):
        """
        Checks if SD card is inserted
        """
        # simulator
        if self._sd is None:
            return True
        return self._sd.present()

    def mount(self):
        """Mounts SD card"""
        if not self.is_present:
            raise RuntimeError("SD card is not present")
        if self._sd is None:
            return
        if self._led is not None:
            self._led.on()
        self._sd.power(True)
        os.mount(self._sd, "/sd")
        self._mounted = True

    def open(self, filename, *args, **kwargs):
        return open(
            fpath("/sd/" + filename.lstrip("/")),
            *args, **kwargs
        )

    def file_exists(self, filename) -> bool:
        return file_exists(fpath("/sd/" + filename.lstrip("/")))

    def unmount(self):
        """Unmounts SD card"""
        # sync file system before unmounting
        if not self._mounted:
            return
        self._mounted = False
        if self._sd is None:
            return
        os.sync()
        os.umount("/sd")
        self._sd.power(False)
        if self._led is not None:
            self._led.off()

    def __enter__(self):
        self.mount()
        return self

    def __exit__(self, *args, **kwargs):
        self.unmount()


def fpath(fname):
    """A small function to avoid % storage_root everywhere"""
    return "%s%s" % (config.storage_root, fname)


if simulator:
    # create folders for simulator
    maybe_mkdir(config.storage_root)
    maybe_mkdir(fpath("/flash"))
    maybe_mkdir(fpath("/qspi"))
    maybe_mkdir(fpath("/sd"))
    sdcard = SDCard(None, None)
else:
    storage_root = ""
    sdcard = SDCard(pyb.SDCard(), pyb.LED(4))

def get_git_info():
    """Return repository metadata embedded into the firmware build."""

    repo = "unknown"
    branch = "unknown"
    commit = "unknown"

    try:
        from git_info import REPOSITORY, BRANCH, COMMIT

        if REPOSITORY:
            repo = REPOSITORY
        if BRANCH:
            branch = BRANCH
        if COMMIT:
            commit = COMMIT
    except:
        pass

    return repo, branch, commit


def get_version() -> str:
    # version is coming from boot.py if running on the hardware
    try:
        ver = version.split(">")[1].split("</")[0]
        major = int(ver[:2])
        minor = int(ver[2:5])
        patch = int(ver[5:8])
        rc = int(ver[8:])
        ver = "%d.%d.%d" % (major, minor, patch)
        if rc != 99:
            ver += "-rc%d" % rc
        return ver
    except:
        return "unknown"


def get_bootloader_lock_status() -> str:
    if bootloader_locked is True:
        return "locked"
    if bootloader_locked is False:
        return "unlocked"
    return "unknown"


def get_build_type() -> str:
    return build_type


def get_firmware_boot_mode() -> str:
    """Return boot mode based on the current vector table address."""

    if simulator:
        return "simulator"

    try:
        vtor = stm.mem32[0xE000ED08]
    except Exception:
        return "unknown"

    if vtor >= 0x08020000:
        return "bootloader"
    if vtor >= 0x08000000:
        return "open"
    return "unknown"


def get_flash_read_protection_status() -> str:
    """Return human readable read protection status."""

    if simulator:
        return "not applicable"

    try:
        option_control = stm.mem32[0x40023C14]
    except Exception:
        return "unknown"

    read_level = (option_control >> 8) & 0xFF

    if read_level == 0xAA:
        return "disabled"
    if read_level == 0xCC:
        return "enabled (level 2)"
    return "enabled (level 1)"


def get_flash_write_protection_status() -> str:
    """Return human readable write protection status."""

    if simulator:
        return "not applicable"

    try:
        option_control = stm.mem32[0x40023C14]
    except Exception:
        return "unknown"

    lower = (option_control >> 16) & 0xFFFF
    upper = 0xFFFF

    if stm is not None:
        try:
            option_control_1 = stm.mem32[0x40023C18]
            upper = option_control_1 & 0xFFFF
        except Exception:
            pass

    if lower == 0xFFFF and upper == 0xFFFF:
        return "disabled"
    return "enabled"

def mount_sdram():
    path = fpath("/ramdisk")
    if simulator:
        # not a real RAM on simulator
        maybe_mkdir(path)
        # cleanup
        delete_recursively(path)
    else:
        bdev = sdram.RAMDevice(512)
        os.VfsFat.mkfs(bdev)
        os.mount(bdev, path)
    return path

def get_preallocated_ram():
    """Returns pointer and size of preallocated memory"""
    if simulator:
        import ctypes
        return ctypes.addressof(_PREALLOCATED), len(_PREALLOCATED)
    else:
        return sdram.preallocated_ptr(), sdram.preallocated_size()

def sync():
    try:
        os.sync()
    except:
        pass


def file_exists(fname: str) -> bool:
    try:
        with open(fname, "rb"):
            pass
        return True
    except:
        return False


def delete_recursively(path, include_self=False):
    # remove trailing slash
    if path is None:
        raise RuntimeError("Path is not specified")
    path = path.rstrip("/")
    files = os.ilistdir(path)
    for _file in files:
        if _file[0] in [".", ".."]:
            continue
        f = "%s/%s" % (path, _file[0])
        # regular file
        if _file[1] == 0x8000:
            os.remove(f)
        # directory
        elif _file[1] == 0x4000:
            delete_recursively(f)
            os.rmdir(f)

    files = os.ilistdir(path)
    num_of_files = sum(1 for _ in files)
    if (num_of_files == 2 and simulator) or num_of_files == 0:
        """
        Directory is empty - it contains exactly 2 directories -
        current directory and parent directory (unix) or
        0 directories (stm32)
        """
        if include_self:
            os.rmdir(path)
        return True
    raise RuntimeError("Failed to delete folder %s" % path)


if not simulator:
    stlk = pyb.UART("YB", 9600)

def enable_usb():
    pyb.usb_mode("VCP")

def disable_usb():
    pyb.usb_mode(None)

def set_usb_mode(dev=False, usb=False):
    if simulator:
        print("dev:", dev, ", usb:", usb)
    # now get correct mode
    if usb: # and not dev:
        pyb.usb_mode("VCP")
        if not simulator:
            os.dupterm(None, 0)
            os.dupterm(None, 1)
    # elif usb and dev:
    #     pyb.usb_mode("VCP+MSC")
    #     if not simulator:
    #         # duplicate repl to stlink
    #         # as usb is busy for communication
    #         os.dupterm(stlk, 0)
    #         os.dupterm(None, 1)
    # elif not usb and dev:
    #     pyb.usb_mode("VCP+MSC")
    #     usb = pyb.USB_VCP()
    #     if not simulator:
    #         os.dupterm(None, 0)
    #         os.dupterm(usb, 1)
    else:
        pyb.usb_mode(None)
        if not simulator:
            os.dupterm(None, 0)
            os.dupterm(None, 1)


def reboot():
    if simulator:
        sys.exit()
    else:
        pyb.hard_reset()


# Verified block map of pyb.Flash() on the STM32F469 Discovery board, as
# built by Specter's firmware (make disco USE_DBOOT=1). pyb.Flash() (no
# args) exposes the whole board's storage as one block device addressed
# with these ABSOLUTE block numbers:
#
#   block 0            software-emulated MBR (storage.c:storage_read/
#                       write_block); not backed by real flash - writes
#                       to it are silently discarded by the block device
#   blocks 1   - 255    unmapped: not part of any partition, writeblocks()
#                       on these fails (storage_write_block() returns
#                       false -> -EIO). Physically these blocks fall in
#                       the bootloader/ISR sector (0x08000000, 16K) and
#                       the reserved sector (FLASH_RSV, 0x08004000, 16K)
#                       of ports/stm32/boards/stm32f469xi_dboot.ld - never
#                       reachable through this API regardless
#   blocks 256 - 447    internal flash, the "/flash" filesystem
#                       (FLASH_PART1_START_BLOCK = 0x100, storage.h);
#                       192 blocks = 96 KiB, matching FLASH_MEM_SEG1
#                       (flashbdev.c, STM32F469xx: sectors 2-4, 0x08008000)
#                       which is exactly the FLASH_FS region of
#                       stm32f469xi_dboot.ld - disjoint from FLASH_TEXT
#                       (the application firmware, sectors 5-21 starting
#                       at 0x08020000) and from FLASH_BOOT1/FLASH_BOOT2
#                       (the bootloader slots, sectors 22-23)
#   blocks 448 - 33215  external QSPI flash, the "/qspi" filesystem
#                       (FLASH_PART2_START_BLOCK = FLASH_PART1_START_BLOCK
#                       + 192, storage.h); 32768 blocks = 16 MiB, the full
#                       size of the on-board QSPI NOR flash chip
#                       (MICROPY_HW_SPIFLASH_SIZE_BITS = 128 Mbit,
#                       boards/STM32F469DISC/mpconfigboard.h)
#
# The QSPI chip is addressed as plain NOR flash - no wear-leveling,
# remapping or translation layer sits between the block numbers above and
# the physical sectors (see spi_bdev_writeblocks()/mp_spiflash_cached_write()
# in ports/stm32/spibdev.c and drivers/memory/spiflash.c), so overwriting
# every block in a range overwrites all addressable QSPI sectors, which
# prevents recovery through normal digital reads of the flash contents
# (this says nothing about charge-remanence/decapping-level analog
# recovery, which is out of scope for this threat model). Required
# sector-erase-before-write is handled transparently by that driver.
# Sources checked: diybitcoinhardware/f469-disco (submodule pin db3ce3e),
# diybitcoinhardware/micropython (submodule pin 6bdf1b6),
# ports/stm32/{storage.h,storage.c,flashbdev.c,spibdev.c},
# ports/stm32/boards/STM32F469DISC/{mpconfigboard.h,mpconfigboard.mk},
# ports/stm32/boards/stm32f469xi_dboot.ld, drivers/memory/spiflash.c.
FLASH_BLOCK_SIZE = 512

INTERNAL_FLASH_START_BLOCK = 0x100                             # FLASH_PART1_START_BLOCK
INTERNAL_FLASH_END_BLOCK = INTERNAL_FLASH_START_BLOCK + 192     # + FLASH_MEM_SEG1_NUM_BLOCKS

QSPI_START_BLOCK = INTERNAL_FLASH_END_BLOCK                     # FLASH_PART2_START_BLOCK
QSPI_END_BLOCK = QSPI_START_BLOCK + 32768                       # + QSPI flash block count

# How many blocks get overwritten per writeblocks() call. Kept small, and
# a multiple of the QSPI erase-sector size (8 blocks == 4096 bytes), so
# the wipe never needs a whole region's worth of RAM (up to 16MiB for
# QSPI) at once.
WIPE_CHUNK_BLOCKS = 16

# extmod/vfs.h: MP_BLOCKDEV_IOCTL_BLOCK_COUNT / MP_BLOCKDEV_IOCTL_SYNC.
MP_BLOCKDEV_IOCTL_BLOCK_COUNT = 4
MP_BLOCKDEV_IOCTL_SYNC = 3
# Both the internal-flash driver (flashbdev.c) and the QSPI driver
# (drivers/memory/spiflash.c, built with USE_WR_DELAY) keep a
# *write-behind* RAM cache: writeblocks() only guarantees the data
# reaches that RAM cache, not the physical chip. The cache is flushed to
# physical storage on a sector-boundary crossing, on this ioctl, or -
# eventually - by a periodic background IRQ. Nothing forces the very
# last sector each overwrite loop below touches to be flushed before
# reboot() other than calling this ioctl explicitly, so wipe() must do
# that itself: a hard reset with a still-dirty cache would lose our
# overwrite of that sector from RAM and leave the original, pre-wipe
# bytes physically in place on the chip.
#
# Known limitation: on the QSPI path, a genuine erase/program failure
# during that flush (in mp_spiflash_cache_flush_internal(), spiflash.c)
# is not propagated up through mp_spiflash_cache_flush() -> the SYNC
# ioctl -> here - the C driver clears its dirty flag and returns before
# checking the erase/write result. The same applies to a sector-boundary
# flush triggered mid-loop by a writeblocks() call. So a hardware write
# failure at that level can go undetected by wipe() even though the
# higher-level checks below (writeblocks()'s own return code, and the
# geometry check below) still catch what they can. Fixing that requires
# propagating real error codes through the pinned MicroPython fork's
# QSPI driver - out of scope here.


def _secure_overwrite_blocks(f, start_block, end_block, block_size,
                              chunk_blocks=WIPE_CHUNK_BLOCKS):
    """
    Overwrites blocks [start_block, end_block) of the raw flash block
    device `f` with a fixed zero pattern, `chunk_blocks` blocks at a
    time, so the whole region never needs to fit in RAM at once.

    Uses zeros rather than os.urandom(): the QSPI/internal-flash drivers
    already erase (and thus fully overwrite) every sector they touch, so
    randomness buys nothing against this threat model (a chip pulled off
    the board and read directly) beyond what any fixed pattern gives -
    while os.urandom() on this port draws one hardware RNG sample per
    *byte* (ports/stm32/rng.c: ~10us each, 10ms timeout), which would
    cost minutes just generating bytes for the 16 MiB QSPI region alone,
    on top of the actual erase/program time. A single pre-built buffer is
    reused (sliced for the final, possibly shorter chunk) instead of
    allocating fresh memory every iteration.

    A failed chunk does not stop the wipe: we keep going so as much of
    the remaining sensitive data as possible still gets destroyed. But
    the return value reflects whether *every* chunk succeeded - callers
    must not treat a False result as if the region were fully wiped.
    """
    zeros = bytes(chunk_blocks * block_size)
    ok = True
    block = start_block
    while block < end_block:
        n = min(chunk_blocks, end_block - block)
        buf = zeros if n == chunk_blocks else zeros[:n * block_size]
        try:
            ret = f.writeblocks(block, buf)
            if ret:
                ok = False
        except Exception:
            ok = False
        block += n
    return ok


def wipe():
    """
    Securely wipes user data.

    Deletes the "/flash" and "/qspi" filesystems, and on real hardware
    also physically overwrites the underlying internal-flash and QSPI
    block ranges (see the verified block map above) with a fixed
    pattern. Logical file deletion alone is not enough: an attacker with
    physical access to the external QSPI flash chip could still recover
    "deleted" files straight from it, since deleting a file only drops
    its filesystem entry - the QSPI chip itself is unaffected. Firmware,
    bootloader and reserved blocks are never touched.
    """
    f = None
    block_size = None
    if not simulator:
        # Check the actual flash geometry against what the block map
        # above assumes, before deleting anything. The constants are
        # correct for the hardware this was verified against, but if a
        # future board revision, MicroPython fork update, or QSPI chip
        # change ever shifted them, that mismatch could otherwise go
        # unnoticed - the tests only check the code against itself, not
        # against real hardware. Fail closed instead of guessing: refuse
        # to touch either filesystem rather than wipe based on assumed
        # boundaries that may no longer hold.
        f = pyb.Flash()
        block_size = f.ioctl(5, None)
        block_count = f.ioctl(MP_BLOCKDEV_IOCTL_BLOCK_COUNT, None)
        if block_size != FLASH_BLOCK_SIZE or block_count != QSPI_END_BLOCK:
            raise RuntimeError(
                "Unexpected flash geometry (block_size=%r, block_count=%r); "
                "refusing to wipe" % (block_size, block_count)
            )

    # delete files normally in simulator (best-effort on hardware too,
    # though the block overwrite below is what actually destroys data there)
    try:
        delete_recursively(fpath("/flash"))
        delete_recursively(fpath("/qspi"))
    except:
        pass
    # on real hardware overwrite flash with a fixed pattern
    if not simulator:
        # /flash and /qspi are unmounted independently, and a failure on
        # either is tracked rather than swallowed: a clean unmount is what
        # flushes any write-behind cache left over from delete_recursively()
        # above (see flashbdev.c/spiflash.c), and losing that guarantee is
        # itself a reason not to trust the wipe. It does not, however, stop
        # us from attempting the raw overwrite below - that's still the
        # best available defense even if the unmount failed.
        flash_unmount_ok = True
        try:
            os.umount("/flash")
        except Exception:
            flash_unmount_ok = False

        qspi_unmount_ok = True
        try:
            os.umount("/qspi")
        except Exception:
            qspi_unmount_ok = False

        internal_ok = _secure_overwrite_blocks(
            f, INTERNAL_FLASH_START_BLOCK, INTERNAL_FLASH_END_BLOCK, block_size
        )
        qspi_ok = _secure_overwrite_blocks(
            f, QSPI_START_BLOCK, QSPI_END_BLOCK, block_size
        )

        # Force the write-behind cache out to physical flash now - see
        # MP_BLOCKDEV_IOCTL_SYNC above for why this can't be skipped.
        sync_ok = True
        try:
            f.ioctl(MP_BLOCKDEV_IOCTL_SYNC, None)
        except Exception:
            sync_ok = False

        if not (flash_unmount_ok and qspi_unmount_ok and internal_ok and qspi_ok and sync_ok):
            # Whatever could be destroyed already has been (see
            # _secure_overwrite_blocks), but a partial wipe must never be
            # allowed to look like a successful one. Raise instead of
            # rebooting so the caller (specter.py's Specter.wipe_or_halt())
            # surfaces the failure instead of silently trusting the device
            # is clean.
            raise RuntimeError(
                "Secure wipe failed to overwrite all flash blocks; "
                "device may still contain recoverable data"
            )
    # mpy will reformat fs on reboot
    reboot()


def usb_connected():
    if simulator:
        return True
    return bool(pyb.Pin.board.USB_VBUS.value())

BATTERY_TABLE = [
    (4.2,  100),
    (4.0,  75),
    (3.85, 50),
    (3.75, 35),
    (3.6,  0),
]

def get_battery_status():
    # simulator or no i2c
    if i2c is None:
        return None, None
    try:
        # check if battery monitor exists
        if 112 not in i2c.scan():
            return None, None
        voltage = int.from_bytes(i2c.mem_read(2, 112, 8),'little')*2.44e-3
        level = 0
        for i, (v, lvl) in enumerate(BATTERY_TABLE):
            if voltage > v:
                # max voltage
                if i == 0:
                    level = lvl
                    break
                # linear interpolation
                prevV, prevLvl = BATTERY_TABLE[i-1]
                level = int(lvl + (prevLvl-lvl)*(voltage-v)/(prevV-v))
                break
        charging = (int.from_bytes(i2c.mem_read(2, 112, 6),'little') < 8192)
        return level, charging
    except Exception as e:
        print(e)
        return None, None
