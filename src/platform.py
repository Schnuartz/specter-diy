# detect if it's a hardware device or linuxport
import sys
import os
import pyb
import gc
import asyncio

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


def validate_block_geometry(block_size, block_count, what="block device"):
    """
    Sanity-checks the geometry a block device reports via ioctl(5)/ioctl(4)
    before it is used to drive an overwrite loop. A negative or non-integer
    count would silently turn `range(0, block_count, ...)` into an empty
    loop, i.e. "wipe" a device without writing a single block.

    Shared by every wipe path (SD card, internal flash, QSPI) so the check
    lives in one place.
    """
    for name, value in (("block size", block_size),
                        ("block count", block_count)):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise RuntimeError(
                "%s reported invalid geometry (%s %r) - cannot erase."
                % (what, name, value)
            )
    return block_size, block_count


class SDCard:
    """
    A single SD card slot: the block device, the activity LED and the
    mount state that belong to it.

    `self` here is the card, not the platform module that happens to hold
    the module-level `sdcard` singleton - every method below (is_present,
    mount, unmount, erase_and_format, ...) is about this one card. On the
    simulator build there is no block device behind it and /sd is an
    ordinary host directory instead (see has_block_device).
    """

    _mounted = False

    def __init__(self, sd = None, led = None):
        self._sd = sd
        self._led = led
        if led is not None:
            led.off()

    @property
    def has_block_device(self):
        """
        False when there is no real SD block device behind this object -
        the simulator build, where /sd is an ordinary directory on the
        host filesystem. Prefer this over checking the module-level
        `simulator` flag: it is the property the SD code actually depends
        on, and it stays correct when a block device is injected into an
        SDCard instance (as the native tests do).
        """
        return self._sd is not None

    @property
    def is_present(self):
        """
        True when a card is inserted in this slot. Without a real block
        device behind it (the simulator build, see has_block_device)
        there is nothing to ask: the /sd directory that stands in for the
        card is always there.
        """
        if not self.has_block_device:
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
        if self._sd is None:
            self._mounted = False
            return
        error = None
        unmounted = False
        try:
            # A failed sync must not prevent us from trying to remove the
            # VFS mount. Keep the first error for the caller, but always run
            # both cleanup operations.
            try:
                os.sync()
            except Exception as e:
                error = e
            try:
                os.umount("/sd")
                unmounted = True
            except Exception as e:
                if error is None:
                    error = e
        finally:
            # Only clear this flag after a successful VFS unmount. If the
            # result is unknown, a later cleanup attempt must not be skipped.
            if unmounted:
                self._mounted = False
            try:
                self._sd.power(False)
            except Exception as e:
                if error is None:
                    error = e
            try:
                if self._led is not None:
                    self._led.off()
            except Exception as e:
                if error is None:
                    error = e
        if error is not None:
            raise error

    def __enter__(self):
        self.mount()
        return self

    def __exit__(self, *args, **kwargs):
        self.unmount()

    async def erase_and_format(self, progress_cb=None):
        """
        Securely erases the SD card - overwrites every block with random
        data - and then creates a fresh, empty FAT filesystem on it.

        This is irreversible and destroys everything on the card. If the
        task is cancelled at an await point, the erase raises instead of
        returning, so a half-overwritten card is never reported as a
        completed format. Warning the user before starting, and telling
        them not to reset the device while it runs, is the caller's job -
        see the messages raised from here for the wording.

        progress_cb(fraction), if given, is awaited after every chunk
        with the fraction (0..1) of blocks written so far, so a caller
        can drive a progress screen without blocking the event loop for
        the whole operation. The event loop is allowed to run after every
        chunk either way (asyncio.sleep_ms(0)) - also when a progress_cb
        is given, since a callback that never awaits anything itself
        (like a simple screen redraw) would otherwise starve the GUI's
        update loop for the entire operation.
        """
        if not self.is_present:
            raise RuntimeError("SD card is not present")
        self.unmount()
        if not self.has_block_device:
            # Simulator build: no block device to overwrite - just clear
            # out the directory that stands in for the card.
            delete_recursively(fpath("/sd"))
            if progress_cb is not None:
                await progress_cb(1.0)
            return
        if self._led is not None:
            self._led.on()
        try:
            try:
                self._sd.power(True)
                block_size = self._sd.ioctl(5, None)
                block_count = self._sd.ioctl(4, None)
            except OSError as e:
                raise RuntimeError(
                    "Could not access the SD card before secure erase "
                    "(card may have been removed):\n\n%s" % e
                ) from e
            validate_block_geometry(block_size, block_count, "SD card")
            # Keep the temporary random-data buffer bounded. A 1 MB
            # allocation is needlessly risky on a fragmented MicroPython
            # heap; 128 KiB is still large enough to amortize SD writes
            # while keeping the wipe usable on the target hardware.
            chunk_blocks = max(1, (128 * 1024) // block_size)
            for start in range(0, block_count, chunk_blocks):
                n = min(chunk_blocks, block_count - start)
                try:
                    result = self._sd.writeblocks(
                        start, os.urandom(block_size * n)
                    )
                except OSError as e:
                    raise RuntimeError(
                        "Could not write to the SD card during secure erase "
                        "(card may have been removed):\n\n%s\n\n"
                        "The card is now in a half-overwritten, unusable "
                        "state and must be reformatted before it can be "
                        "used again." % e
                    ) from e
                # MicroPython block devices conventionally return None (or
                # 0), while the STM32 binding used here returns True/False.
                # Accept only those known success values; any other result
                # must fail closed so a missed chunk can never be reported
                # as a successful secure erase.
                if not (
                    result is None
                    or result is True
                    or (
                        isinstance(result, int)
                        and not isinstance(result, bool)
                        and result == 0
                    )
                ):
                    raise RuntimeError(
                        "Could not write to the SD card during secure erase "
                        "(block device returned %r). The card is now in a "
                        "half-overwritten, unusable state and must be "
                        "reformatted before it can be used again." % result
                    )
                gc.collect()
                try:
                    if progress_cb is not None:
                        await progress_cb((start + n) / block_count)
                    # Always yield to the event loop, even with a
                    # progress_cb: a callback that never awaits (e.g. one
                    # that only redraws a progress bar) runs synchronously
                    # and would otherwise keep every other task - including
                    # the GUI update loop - from running until the whole
                    # card is overwritten.
                    await asyncio.sleep_ms(0)
                except asyncio.CancelledError as e:
                    raise RuntimeError(
                        "Secure erase was interrupted before completion. "
                        "The SD card is now in a half-overwritten, unusable "
                        "state and must be reformatted before it can be "
                        "used again."
                    ) from e
            try:
                os.VfsFat.mkfs(self._sd)
            except OSError as e:
                raise RuntimeError(
                    "Overwrite completed, but creating a fresh filesystem "
                    "failed:\n\n%s\n\nThe card's old data has been wiped, "
                    "but it has no valid filesystem and must be reformatted "
                    "on a computer before it can be used." % e
                ) from e
        finally:
            self._sd.power(False)
            if self._led is not None:
                self._led.off()


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


def secure_delete_file(path):
    """
    Overwrites a file's contents with random data, syncs, and only then
    deletes it.

    A plain os.remove() only unlinks the directory entry - the file's old
    bytes are still physically on the card until that space happens to be
    reused, and can often be recovered with an undelete tool in the
    meantime. This closes that gap for individual files (see
    SDCard.erase_and_format() for the equivalent whole-card operation).

    A single random pass is deliberate. Multiple passes are a leftover
    from 1990s magnetic media; on the flash storage this device writes to,
    a second pass adds wear and time without adding recoverable-data
    protection, and it cannot reach blocks the flash translation layer has
    already remapped anyway. Callers that need that guarantee must erase
    the whole card.

    The file is opened once and the size is read from that same handle
    (seek(0, 2)/tell()) rather than stat-then-open: a stat/open gap would
    let an attacker with write access swap the path between the size check
    and the overwrite, causing us to wipe the wrong file.

    Size limits are not enforced here - a caller that needs to bound the
    work it takes on checks that before it starts (see secure_delete_tree).
    """
    with open(path, "r+b") as f:
        f.seek(0, 2)  # seek to end
        size = f.tell()
        f.seek(0)
        remaining = size
        while remaining > 0:
            chunk = min(remaining, 4096)
            written = f.write(os.urandom(chunk))
            if written != chunk:
                raise OSError(
                    "short write during secure delete (%r of %d bytes)"
                    % (written, chunk)
                )
            remaining -= written
        _strict_file_sync(f)
    os.remove(path)


def _strict_file_sync(f):
    """Flushes an overwrite and propagates every persistence error."""
    f.flush()
    if hasattr(os, "sync"):
        os.sync()
    elif hasattr(os, "fsync"):
        # CPython on platforms without os.sync (notably Windows).
        os.fsync(f.fileno())
    else:
        raise OSError("no filesystem sync primitive is available")


# An adversarial directory with thousands of entries would otherwise make
# secure_delete_tree() enumerate, walk through, and later remove entries
# for an unbounded amount of time (DoS). A BitBox backup directory has only a
# few entries, so this leaves ample room for legitimate trees.
SECURE_DELETE_MAX_FILES = 200
SECURE_DELETE_MAX_ENTRIES = SECURE_DELETE_MAX_FILES
SECURE_DELETE_MAX_DEPTH = 16
SECURE_DELETE_MAX_FILE_BYTES = 4 * 1024 * 1024
SECURE_DELETE_MAX_TOTAL_BYTES = 8 * 1024 * 1024


def secure_delete_tree(path):
    """
    Walks the whole tree under `path` and secure_delete_file()s every
    regular file in it (see its docstring), then removes the now-empty
    directories, including `path` itself. Used for multi-file items such as a BitBox
    backup directory, where each of the redundant copies must be
    overwritten, not just unlinked.

    Enumerates every entry under the tree BEFORE overwriting anything, and
    rejects a tree that is too large to wipe up front: more than
    SECURE_DELETE_MAX_ENTRIES entries, a file larger than
    SECURE_DELETE_MAX_FILE_BYTES, more than SECURE_DELETE_MAX_TOTAL_BYTES
    of file data in total, or a depth greater than SECURE_DELETE_MAX_DEPTH.
    This is where those limits belong: refusing the whole operation before
    the first overwrite is what keeps a rejected tree from being left half
    wiped. The caller can offer "Format entire SD card" as the fallback.
    """
    files, _entries, _size = _collect_files(
        path,
        max_entries=SECURE_DELETE_MAX_ENTRIES,
        max_depth=SECURE_DELETE_MAX_DEPTH,
        max_file_bytes=SECURE_DELETE_MAX_FILE_BYTES,
        max_total_bytes=SECURE_DELETE_MAX_TOTAL_BYTES,
    )
    for file_path in files:
        secure_delete_file(file_path)
    _remove_empty_dirs(path)


def _close_dir_iter(entries):
    """
    Releases whatever os.ilistdir() handed back.

    On the STM32 build this is a real iterator holding an open directory
    handle, which has to be closed even when the traversal is abandoned
    half way through (a cap was exceeded, a stat() failed). Elsewhere -
    the simulator, the native test stubs - it can be a plain generator or
    a list, so the close method may not exist at all.

    Shared by every directory walk below so the "close it if it can be
    closed" dance exists in exactly one place.
    """
    close = getattr(entries, "close", None)
    if callable(close):
        close()


def _collect_files(
        path, max_entries=SECURE_DELETE_MAX_ENTRIES,
        max_depth=SECURE_DELETE_MAX_DEPTH,
        max_file_bytes=None, max_total_bytes=None):
    """Returns `(files, entry_count, total_bytes)`, where `files` is a list
    of the full paths of all regular files under `path`. Aborts before
    traversing more than `max_entries` total entries or descending beyond
    `max_depth`. When size limits are provided, regular files are checked
    with `os.stat()` before their paths are retained. Does not modify or
    delete anything.

    The walk is iterative (an explicit stack of (directory, depth) pairs)
    rather than recursive: on this device the Python stack is small and a
    deep tree is attacker-controlled input, so nesting must not consume
    call frames. It also means only one directory iterator is open at a
    time - a recursive walk keeps the whole chain of parent handles open
    while it descends."""
    files = []
    entry_count = 0
    total_bytes = 0
    stack = [(path, 0)]
    while stack:
        dir_path, depth = stack.pop()
        if depth > max_depth:
            raise RuntimeError(
                "directory is deeper than %d levels - use 'Format entire SD "
                "card' instead" % max_depth
            )
        entries = os.ilistdir(dir_path)
        try:
            for name, entry_type, *_rest in entries:
                if name in (".", ".."):
                    continue
                entry_path = "%s/%s" % (dir_path, name)
                entry_count += 1
                if entry_count > max_entries:
                    raise RuntimeError(
                        "directory contains more than %d entries - use "
                        "'Format entire SD card' instead" % max_entries
                    )
                if entry_type == 0x8000:
                    if (max_file_bytes is not None
                            or max_total_bytes is not None):
                        try:
                            size = os.stat(entry_path)[6]
                        except Exception as e:
                            raise RuntimeError(
                                "Could not inspect file before secure "
                                "delete: %s" % entry_path
                            ) from e
                        if (
                            not isinstance(size, int)
                            or isinstance(size, bool)
                            or size < 0
                        ):
                            raise RuntimeError(
                                "file has invalid size metadata: %s"
                                % entry_path
                            )
                        if (max_file_bytes is not None
                                and size > max_file_bytes):
                            raise RuntimeError(
                                "file is %d bytes (maximum %d) - use 'Format "
                                "entire SD card' instead"
                                % (size, max_file_bytes)
                            )
                        total_bytes += size
                        if (
                            max_total_bytes is not None
                            and total_bytes > max_total_bytes
                        ):
                            raise RuntimeError(
                                "tree contains more than %d bytes - use "
                                "'Format entire SD card' instead"
                                % max_total_bytes
                            )
                    files.append(entry_path)
                elif entry_type == 0x4000:
                    stack.append((entry_path, depth + 1))
        finally:
            _close_dir_iter(entries)
    return files, entry_count, total_bytes


def _remove_empty_dirs(path, max_entries=SECURE_DELETE_MAX_ENTRIES,
                       max_depth=SECURE_DELETE_MAX_DEPTH):
    """Removes every empty directory under `path`, then `path` itself.
    Must be called after secure_delete_file() has already unlinked every
    regular file, so each directory is in fact empty.

    Iterative for the same reason as _collect_files(): the directories are
    gathered top-down onto an explicit stack and then removed in reverse,
    which is the post-order a recursive walk would have produced, without
    the recursion. A parent is always recorded before the children it
    pushes, so walking the list backwards always removes children first.
    The same entry cap applies, so an adversarial tree cannot grow this
    list without bound either."""
    dirs = []
    stack = [(path, 0)]
    while stack:
        dir_path, depth = stack.pop()
        if depth > max_depth:
            raise RuntimeError(
                "directory is deeper than %d levels - use 'Format entire SD "
                "card' instead" % max_depth
            )
        dirs.append(dir_path)
        if len(dirs) > max_entries:
            raise RuntimeError(
                "directory contains more than %d entries - use 'Format "
                "entire SD card' instead" % max_entries
            )
        entries = os.ilistdir(dir_path)
        try:
            for name, entry_type, *_rest in entries:
                if name in (".", ".."):
                    continue
                if entry_type == 0x4000:
                    stack.append(("%s/%s" % (dir_path, name), depth + 1))
        finally:
            _close_dir_iter(entries)
    for i in range(len(dirs) - 1, -1, -1):
        os.rmdir(dirs[i])


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


def wipe():
    """
    Blocks map in disco board
    0: MBR
    1   - 255:   reserved
    256 - 447:   internal flash
    448 - 33215: QSPI
    """
    # delete files normally in simulator
    try:
        delete_recursively(fpath("/flash"))
        delete_recursively(fpath("/qspi"))
    except:
        pass
    # on real hardware overwrite flash with random data
    if not simulator:
        os.umount("/flash")
        os.umount("/qspi")
        f = pyb.Flash()
        block_size = f.ioctl(5, None)
        validate_block_geometry(block_size, f.ioctl(4, None), "internal flash")
        # wipe internal flash with random bytes
        for i in range(256, 450):
            b = os.urandom(block_size)
            f.writeblocks(i, b)
            del b
            gc.collect()
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
