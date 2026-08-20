import sys

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase

import pyb
import platform
from tests.util import clear_testdir


class FakeFlash:
    """
    Records every writeblocks() call so tests can check exactly which
    blocks were (or were not) overwritten, without touching real hardware.
    """

    def __init__(self, block_size=512, fail_at_blocks=None, raise_at_blocks=None):
        self.block_size = block_size
        self.calls = []  # list of (block_num, num_blocks)
        self.fail_at_blocks = set(fail_at_blocks or [])
        self.raise_at_blocks = set(raise_at_blocks or [])

    def ioctl(self, cmd, arg):
        if cmd == 5:  # MP_BLOCKDEV_IOCTL_BLOCK_SIZE
            return self.block_size
        return None

    def writeblocks(self, block_num, buf):
        assert len(buf) % self.block_size == 0
        n = len(buf) // self.block_size
        self.calls.append((block_num, n))
        if block_num in self.raise_at_blocks:
            raise OSError("simulated flash failure")
        if block_num in self.fail_at_blocks:
            return -5  # negative errno == failure, per block protocol
        return 0

    def written_blocks(self):
        """Returns the set of individual block numbers passed a buffer."""
        blocks = set()
        for start, n in self.calls:
            blocks.update(range(start, start + n))
        return blocks


class BlockMapConstantsTest(TestCase):
    """
    Pins down the verified hardware block map (see platform.py) so a
    future edit can't silently reintroduce an off-by-one or shrink the
    wiped range.
    """

    def test_internal_flash_range(self):
        self.assertEqual(platform.INTERNAL_FLASH_START_BLOCK, 256)
        self.assertEqual(platform.INTERNAL_FLASH_END_BLOCK, 448)
        # 192 blocks * 512 bytes == 96 KiB == FLASH_MEM_SEG1 on STM32F469xx
        self.assertEqual(
            platform.INTERNAL_FLASH_END_BLOCK - platform.INTERNAL_FLASH_START_BLOCK,
            192,
        )

    def test_qspi_range(self):
        self.assertEqual(platform.QSPI_START_BLOCK, 448)
        self.assertEqual(platform.QSPI_END_BLOCK, 33216)
        # 32768 blocks * 512 bytes == 16 MiB == the on-board QSPI chip
        self.assertEqual(
            platform.QSPI_END_BLOCK - platform.QSPI_START_BLOCK,
            32768,
        )

    def test_qspi_starts_immediately_after_internal_flash(self):
        self.assertEqual(platform.QSPI_START_BLOCK, platform.INTERNAL_FLASH_END_BLOCK)

    def test_reserved_region_is_excluded(self):
        # blocks 0-255 (fake MBR + reserved/bootloader sectors) must never
        # be part of either wiped range
        self.assertGreaterEqual(platform.INTERNAL_FLASH_START_BLOCK, 256)


class SecureOverwriteBlocksTest(TestCase):
    def test_wipes_full_range_with_no_gaps_or_overrun(self):
        f = FakeFlash()
        ok = platform._secure_overwrite_blocks(
            f, platform.INTERNAL_FLASH_START_BLOCK, platform.INTERNAL_FLASH_END_BLOCK, 512
        )
        self.assertTrue(ok)
        self.assertEqual(
            f.written_blocks(),
            set(range(platform.INTERNAL_FLASH_START_BLOCK, platform.INTERNAL_FLASH_END_BLOCK)),
        )
        # first and last block of the range must be included (off-by-one guard)
        self.assertIn(platform.INTERNAL_FLASH_START_BLOCK, f.written_blocks())
        self.assertIn(platform.INTERNAL_FLASH_END_BLOCK - 1, f.written_blocks())
        # the end block itself (exclusive bound) must never be written
        self.assertNotIn(platform.INTERNAL_FLASH_END_BLOCK, f.written_blocks())

    def test_wipes_full_qspi_range(self):
        f = FakeFlash()
        ok = platform._secure_overwrite_blocks(
            f, platform.QSPI_START_BLOCK, platform.QSPI_END_BLOCK, 512
        )
        self.assertTrue(ok)
        self.assertEqual(
            f.written_blocks(),
            set(range(platform.QSPI_START_BLOCK, platform.QSPI_END_BLOCK)),
        )
        self.assertIn(platform.QSPI_START_BLOCK, f.written_blocks())
        self.assertIn(platform.QSPI_END_BLOCK - 1, f.written_blocks())
        self.assertNotIn(platform.QSPI_END_BLOCK, f.written_blocks())

    def test_never_touches_blocks_outside_requested_range(self):
        f = FakeFlash()
        platform._secure_overwrite_blocks(f, 300, 320, 512)
        for start, n in f.calls:
            self.assertGreaterEqual(start, 300)
            self.assertLessEqual(start + n, 320)

    def test_uses_given_block_size_for_buffers(self):
        f = FakeFlash(block_size=4096)
        platform._secure_overwrite_blocks(f, 0, 32, 4096, chunk_blocks=8)
        for start, n in f.calls:
            self.assertLessEqual(n * 4096, 8 * 4096)

    def test_chunking_never_loads_whole_region_at_once(self):
        f = FakeFlash()
        platform._secure_overwrite_blocks(f, 0, 1000, 512, chunk_blocks=16)
        for _, n in f.calls:
            self.assertLessEqual(n, 16)
        self.assertGreater(len(f.calls), 1)

    def test_write_failure_does_not_stop_the_wipe(self):
        f = FakeFlash(fail_at_blocks={16})
        ok = platform._secure_overwrite_blocks(f, 0, 64, 512, chunk_blocks=16)
        self.assertFalse(ok)
        # every chunk should still have been attempted despite the failure
        self.assertEqual(f.written_blocks(), set(range(0, 64)))

    def test_write_exception_does_not_stop_the_wipe(self):
        f = FakeFlash(raise_at_blocks={32})
        ok = platform._secure_overwrite_blocks(f, 0, 64, 512, chunk_blocks=16)
        self.assertFalse(ok)
        self.assertEqual(f.written_blocks(), set(range(0, 64)))


class WipeSimulatorTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.rebooted = False

        def fake_reboot():
            self.rebooted = True

        self._orig_reboot = platform.reboot
        platform.reboot = fake_reboot

        def fail_if_called(*args, **kwargs):
            raise AssertionError("hardware block device must not be touched in simulator mode")

        self._orig_flash = getattr(pyb, "Flash", None)
        pyb.Flash = fail_if_called

    def tearDown(self):
        platform.reboot = self._orig_reboot
        if self._orig_flash is None:
            del pyb.Flash
        else:
            pyb.Flash = self._orig_flash
        clear_testdir()

    def test_wipe_deletes_files_and_never_touches_hardware(self):
        platform.maybe_mkdir(platform.fpath("/flash"))
        platform.maybe_mkdir(platform.fpath("/qspi"))
        with open(platform.fpath("/flash/secret.txt"), "w") as f:
            f.write("do not recover me")
        with open(platform.fpath("/qspi/wallet.json"), "w") as f:
            f.write("{}")

        platform.wipe()

        self.assertTrue(self.rebooted)
        self.assertFalse(platform.file_exists(platform.fpath("/flash/secret.txt")))
        self.assertFalse(platform.file_exists(platform.fpath("/qspi/wallet.json")))


class WipeHardwareTest(TestCase):
    """
    Exercises the `if not simulator:` branch of wipe() by flipping the
    module-level `simulator` flag, without requiring real STM32 hardware.
    """

    def setUp(self):
        clear_testdir()
        self._orig_simulator = platform.simulator
        platform.simulator = False

        self.rebooted = False

        def fake_reboot():
            self.rebooted = True

        self._orig_reboot = platform.reboot
        platform.reboot = fake_reboot

        self._orig_umount = platform.os.umount if hasattr(platform.os, "umount") else None
        self.umounted = []
        platform.os.umount = lambda path: self.umounted.append(path)

    def tearDown(self):
        platform.simulator = self._orig_simulator
        platform.reboot = self._orig_reboot
        if self._orig_umount is not None:
            platform.os.umount = self._orig_umount
        else:
            del platform.os.umount
        if hasattr(pyb, "Flash"):
            del pyb.Flash
        clear_testdir()

    def test_successful_wipe_overwrites_both_regions_and_reboots(self):
        fake = FakeFlash()
        pyb.Flash = lambda: fake

        platform.wipe()

        self.assertTrue(self.rebooted)
        self.assertEqual(sorted(self.umounted), ["/flash", "/qspi"])
        expected = set(range(platform.INTERNAL_FLASH_START_BLOCK, platform.INTERNAL_FLASH_END_BLOCK))
        expected |= set(range(platform.QSPI_START_BLOCK, platform.QSPI_END_BLOCK))
        self.assertEqual(fake.written_blocks(), expected)
        # never touch the fake MBR or the reserved/bootloader blocks
        for start, n in fake.calls:
            self.assertGreaterEqual(start, 256)

    def test_failed_wipe_raises_and_does_not_reboot(self):
        fake = FakeFlash(fail_at_blocks={platform.QSPI_START_BLOCK})
        pyb.Flash = lambda: fake

        with self.assertRaises(Exception):
            platform.wipe()

        # a partial wipe must never look like a successful one
        self.assertFalse(self.rebooted)
