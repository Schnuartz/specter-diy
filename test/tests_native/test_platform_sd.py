"""
Tests for platform.SDCard.erase_and_format() and secure_delete_tree().

The critical property under test for erase_and_format: the chunked
overwrite loop must keep yielding to the asyncio event loop between
chunks *even when a progress callback is provided* - a callback that
never awaits anything itself (e.g. one that only redraws a progress bar)
would otherwise run synchronously and starve the GUI's update loop for
the entire format, freezing the screen on device.

For secure_delete_tree: a directory with more than SECURE_DELETE_MAX_FILES
files must be rejected BEFORE any overwrite happens, so a partial wipe
is never left behind and an adversarial directory cannot force
unbounded overwrite+sync work.
"""
import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

import asyncio
import os
from unittest import TestCase

import platform

from tests.util import clear_testdir

# CPython's asyncio has no sleep_ms (MicroPython does); the platform
# module under test uses it. Provide the usual shim if missing.
if not hasattr(asyncio, "sleep_ms"):
    async def _sleep_ms(ms):
        await asyncio.sleep(ms / 1000.0)
    asyncio.sleep_ms = _sleep_ms

# os.VfsFat only exists in MicroPython; stub mkfs for the native run.
if not hasattr(os, "VfsFat"):
    class _VfsFat:
        @staticmethod
        def mkfs(bdev):
            pass
    os.VfsFat = _VfsFat


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeBlockDevice:
    """Just enough of the MicroPython block-device protocol for
    erase_and_format(): ioctl(4)=block count, ioctl(5)=block size,
    writeblocks(), power()."""

    def __init__(self, block_count, block_size=512):
        self.block_count = block_count
        self.block_size = block_size
        self.writes = []
        self.power_states = []

    def present(self):
        return True

    def ioctl(self, op, arg):
        if op == 4:
            return self.block_count
        if op == 5:
            return self.block_size
        raise OSError("unsupported ioctl")

    def writeblocks(self, start, data):
        self.writes.append((start, len(data)))

    def power(self, state):
        self.power_states.append(state)


class EraseAndFormatTest(TestCase):
    def test_overwrites_every_block_and_reformats(self):
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)
        progress = []

        async def main():
            async def cb(fraction):
                progress.append(fraction)
            await sd.erase_and_format(progress_cb=cb)

        _run(main())

        self.assertEqual(
            dev.writes,
            [(0, 2048 * 512), (2048, 2048 * 512), (4096, 4 * 512)],
        )
        self.assertEqual(len(progress), 3)
        self.assertEqual(progress[-1], 1.0)
        self.assertEqual(dev.power_states, [True, False])

    def test_event_loop_keeps_running_with_progress_callback(self):
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)
        ticks = []
        progress = []

        async def ticker():
            try:
                while True:
                    ticks.append(1)
                    await asyncio.sleep_ms(0)
            except asyncio.CancelledError:
                pass

        async def main():
            task = asyncio.ensure_future(ticker())

            async def cb(fraction):
                progress.append(fraction)

            await sd.erase_and_format(progress_cb=cb)
            task.cancel()
            await task

        _run(main())

        self.assertGreaterEqual(len(ticks), len(progress))

    def test_powered_off_even_on_write_error(self):
        class FailingDevice(_FakeBlockDevice):
            def writeblocks(self, start, data):
                raise OSError("card removed")

        dev = FailingDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            self.assertIsInstance(ctx.exception.__cause__, OSError)

        _run(main())
        self.assertEqual(dev.power_states, [True, False])

    def test_invalid_geometry_rejected_before_any_write(self):
        class _BadGeometryDevice(_FakeBlockDevice):
            def __init__(self, block_count, block_size):
                self.block_count = block_count
                self.block_size = block_size
                self.writes = []
                self.power_states = []

        bad_geometries = [
            (0, 4100),
            (512, 0),
            (None, 4100),
            (512, None),
            (-512, 4100),
            (512, -1),
            (512.0, 4100),
            (512, "4100"),
            (True, 4100),
            (512, False),
        ]
        for bad_size, bad_count in bad_geometries:
            dev = _BadGeometryDevice(block_count=bad_count, block_size=bad_size)
            sd = platform.SDCard(sd=dev)

            async def main():
                with self.assertRaises(RuntimeError) as ctx:
                    await sd.erase_and_format()
                self.assertIn("invalid geometry", str(ctx.exception))

            _run(main())
            self.assertEqual(dev.writes, [])

    def test_setup_io_error_is_translated_and_card_is_powered_off(self):
        class FailingIoctlDevice(_FakeBlockDevice):
            def ioctl(self, op, arg):
                raise OSError("card removed during setup")

        dev = FailingIoctlDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            self.assertIn("Could not access", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, OSError)

        _run(main())
        self.assertEqual(dev.power_states, [True, False])
        self.assertEqual(dev.writes, [])

    def test_power_on_error_is_translated_and_cleanup_is_attempted(self):
        class FailingPowerDevice(_FakeBlockDevice):
            def power(self, state):
                self.power_states.append(state)
                if state:
                    raise OSError("card removed while powering on")

        dev = FailingPowerDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            self.assertIn("Could not access", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, OSError)

        _run(main())
        self.assertEqual(dev.power_states, [True, False])
        self.assertEqual(dev.writes, [])

    def test_cancellation_reports_interrupted_wipe(self):
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        async def main():
            async def cancel_after_first_chunk(fraction):
                raise asyncio.CancelledError()

            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format(progress_cb=cancel_after_first_chunk)
            self.assertIn("interrupted", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, asyncio.CancelledError)

        _run(main())
        self.assertEqual(dev.writes, [(0, 2048 * 512)])
        self.assertEqual(dev.power_states, [True, False])


class SecureDeleteFileTest(TestCase):
    def setUp(self):
        clear_testdir()
        platform.maybe_mkdir("testdir")
        self.path = "testdir/secret.bin"
        with open(self.path, "wb") as f:
            f.write(b"sensitive material")

    def tearDown(self):
        if platform.file_exists(self.path):
            os.remove(self.path)
        try:
            platform.delete_recursively("testdir", include_self=True)
        except OSError:
            pass

    def test_invalid_pass_count_is_rejected_before_opening_file(self):
        with open(self.path, "rb") as f:
            original = f.read()
        for passes in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                platform.secure_delete_file(self.path, passes=passes)
            self.assertTrue(platform.file_exists(self.path))
            with open(self.path, "rb") as f:
                self.assertEqual(f.read(), original)

    def test_short_write_aborts_without_unlinking(self):
        class ShortWriteFile:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def seek(self, offset, whence=0):
                pass

            def tell(self):
                return 10

            def write(self, data):
                return len(data) - 1

        had_open = hasattr(platform, "open")
        real_open = getattr(platform, "open", None)
        platform.open = lambda *args, **kwargs: ShortWriteFile()
        try:
            with self.assertRaises(OSError) as ctx:
                platform.secure_delete_file(self.path, passes=1)
            self.assertIn("short write", str(ctx.exception))
        finally:
            if had_open:
                platform.open = real_open
            else:
                del platform.open
        self.assertTrue(platform.file_exists(self.path))

    def test_sync_error_aborts_without_unlinking(self):
        real_sync = platform._strict_file_sync

        def failing_sync(f):
            raise OSError("simulated sync failure")

        platform._strict_file_sync = failing_sync
        try:
            with self.assertRaises(OSError) as ctx:
                platform.secure_delete_file(self.path, passes=1)
            self.assertIn("sync failure", str(ctx.exception))
        finally:
            platform._strict_file_sync = real_sync
        self.assertTrue(platform.file_exists(self.path))


class SecureDeleteTreeTest(TestCase):
    def setUp(self):
        clear_testdir()
        platform.maybe_mkdir("testdir")
        platform.maybe_mkdir("testdir/deltree")
        self.path = "testdir/deltree"

    def tearDown(self):
        try:
            platform.delete_recursively("testdir", include_self=True)
        except OSError:
            pass

    def _make_files(self, n):
        for i in range(n):
            with open("%s/file_%d.bin" % (self.path, i), "wb") as f:
                f.write(b"x" * 10)

    def test_normal_tree_deleted(self):
        self._make_files(3)
        platform.secure_delete_tree(self.path)
        self.assertFalse(platform.file_exists(self.path))

    def test_tree_at_cap_deleted(self):
        self._make_files(platform.SECURE_DELETE_MAX_FILES)
        platform.secure_delete_tree(self.path)
        self.assertFalse(platform.file_exists(self.path))

    def test_tree_over_cap_rejected_before_overwrite(self):
        self._make_files(platform.SECURE_DELETE_MAX_FILES + 1)
        before = {}
        for i in range(platform.SECURE_DELETE_MAX_FILES + 1):
            path = "%s/file_%d.bin" % (self.path, i)
            with open(path, "rb") as f:
                before[path] = f.read()
        with self.assertRaises(RuntimeError) as ctx:
            platform.secure_delete_tree(self.path)
        self.assertIn("Format", str(ctx.exception))
        # Every file and every byte must be unchanged (no partial wipe).
        for path, expected in before.items():
            self.assertTrue(platform.file_exists(path))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), expected)

    def test_collector_stops_immediately_when_cap_is_exceeded(self):
        seen = []

        def many_entries(path):
            for i in range(platform.SECURE_DELETE_MAX_FILES + 1000):
                seen.append(i)
                yield ("file_%d.bin" % i, 0x8000, 0, 0)

        real_ilistdir = os.ilistdir
        os.ilistdir = many_entries
        files = []
        try:
            with self.assertRaises(RuntimeError):
                platform._collect_files(
                    "virtual", files, platform.SECURE_DELETE_MAX_FILES
                )
        finally:
            os.ilistdir = real_ilistdir
        self.assertEqual(len(files), platform.SECURE_DELETE_MAX_FILES)
        self.assertEqual(len(seen), platform.SECURE_DELETE_MAX_FILES + 1)
