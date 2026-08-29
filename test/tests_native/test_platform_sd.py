"""
Tests for platform.SDCard.erase_and_format() and secure_delete_tree().

The critical property under test for erase_and_format: the chunked
overwrite loop must keep yielding to the asyncio event loop between
chunks *even when a progress callback is provided* - a callback that
never awaits anything itself (e.g. one that only redraws a progress bar)
would otherwise run synchronously and starve the GUI's update loop for
the entire format, freezing the screen on device.

For secure_delete_tree: a directory with more than
SECURE_DELETE_MAX_ENTRIES entries must be rejected BEFORE any overwrite happens, so a partial wipe
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


class _RecordingMkfs:
    """Context manager that records every os.VfsFat.mkfs() call - and
    optionally makes it fail - so tests can assert the filesystem is created
    exactly once, against the right block device, and only after a complete
    overwrite."""

    def __init__(self, error=None):
        self.calls = []
        self._error = error
        self._real = None

    def __enter__(self):
        self._real = os.VfsFat.mkfs

        def fake_mkfs(bdev):
            self.calls.append(bdev)
            if self._error is not None:
                raise self._error

        os.VfsFat.mkfs = staticmethod(fake_mkfs)
        return self

    def __exit__(self, *args):
        os.VfsFat.mkfs = self._real


def _dir_exists(path):
    """platform.file_exists() opens the path as a file, so it reports False
    for a directory whether or not that directory is still there. Tests that
    care about directory removal must stat instead."""
    try:
        os.stat(path)
        return True
    except OSError:
        return False


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
        return getattr(self, "write_result", None)

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
            [
                (start, count * 512)
                for start, count in [
                    (i, min(256, 4100 - i))
                    for i in range(0, 4100, 256)
                ]
            ],
        )
        self.assertEqual(len(progress), 17)
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

    def test_writeblocks_failure_return_is_not_ignored(self):
        for failure_result in (False, -1):
            dev = _FakeBlockDevice(block_count=4100, block_size=512)
            dev.write_result = failure_result
            sd = platform.SDCard(sd=dev)

            async def main():
                with self.assertRaises(RuntimeError) as ctx:
                    await sd.erase_and_format()
                self.assertIn("returned", str(ctx.exception))

            _run(main())
            self.assertEqual(dev.power_states, [True, False])
            self.assertEqual(len(dev.writes), 1)

    def test_filesystem_created_once_on_the_same_device_after_full_erase(self):
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        with _RecordingMkfs() as mkfs:
            _run(sd.erase_and_format())

        # Exactly one filesystem, on the device we just overwrote.
        self.assertEqual(len(mkfs.calls), 1)
        self.assertIs(mkfs.calls[0], dev)
        # And only after every block was written: 4100 blocks of 512 bytes
        # in 128 KiB chunks is 16 full chunks plus a 4-block remainder.
        self.assertEqual(sum(n for _, n in dev.writes), 4100 * 512)
        self.assertEqual(dev.power_states, [True, False])

    def test_no_filesystem_is_created_when_the_overwrite_fails(self):
        class FailingWriteDevice(_FakeBlockDevice):
            def writeblocks(self, start, data):
                self.writes.append((start, len(data)))
                raise OSError("card removed mid-erase")

        dev = FailingWriteDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        with _RecordingMkfs() as mkfs:
            with self.assertRaises(RuntimeError):
                _run(sd.erase_and_format())

        # A half-overwritten card must not be handed a fresh filesystem -
        # that would present it as clean while most of it is untouched.
        self.assertEqual(mkfs.calls, [])
        self.assertEqual(dev.power_states, [True, False])

    def test_mkfs_failure_is_translated_and_card_is_powered_off(self):
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)
        cause = OSError("mkfs failed")

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            message = str(ctx.exception)
            # The user has to be told the data is gone but the card is not
            # usable yet - those are different recovery steps.
            self.assertIn("Overwrite completed", message)
            self.assertIn("no valid filesystem", message)
            self.assertIs(ctx.exception.__cause__, cause)

        with _RecordingMkfs(error=cause) as mkfs:
            _run(main())

        self.assertEqual(len(mkfs.calls), 1)
        self.assertEqual(sum(n for _, n in dev.writes), 4100 * 512)
        self.assertEqual(dev.power_states, [True, False])

    def test_write_buffer_is_allocated_once_and_reused(self):
        """os.urandom() allocates a fresh object per call, so asking it for
        a whole 128 KiB chunk on every iteration is the repeated large
        allocation that fragments the MicroPython heap. Every allocation in
        the loop must stay small."""
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)
        sizes = []
        real_urandom = os.urandom

        def counting_urandom(n):
            sizes.append(n)
            return real_urandom(n)

        os.urandom = counting_urandom
        try:
            _run(sd.erase_and_format())
        finally:
            os.urandom = real_urandom

        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), 256)
        # The card is still fully overwritten.
        self.assertEqual(sum(n for _, n in dev.writes), 4100 * 512)

    def test_allocation_failure_aborts_before_touching_the_card(self):
        """The buffer is allocated before the first destructive write, so a
        MemoryError there must report that nothing was changed - not leave
        the user with a half-wiped card."""
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        def refuses(size):
            raise MemoryError("out of memory")

        platform.bytearray = refuses
        try:
            with _RecordingMkfs() as mkfs:
                async def main():
                    with self.assertRaises(RuntimeError) as ctx:
                        await sd.erase_and_format()
                    message = str(ctx.exception)
                    self.assertIn("Not enough memory", message)
                    self.assertIn("No data has been changed", message)
                    self.assertIsInstance(ctx.exception.__cause__, MemoryError)

                _run(main())
        finally:
            del platform.bytearray

        self.assertEqual(dev.writes, [])
        self.assertEqual(mkfs.calls, [])
        self.assertEqual(dev.power_states, [True, False])

    def test_memory_error_mid_wipe_is_reported_as_interrupted(self):
        """MemoryError is not an OSError, so without an explicit handler it
        would escape as a raw traceback and the user would never be told the
        card is half-overwritten."""
        dev = _FakeBlockDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)
        real_fill = platform.fill_random
        calls = []

        def failing_fill(buf):
            calls.append(len(buf))
            if len(calls) == 3:
                raise MemoryError("out of memory")
            return real_fill(buf)

        platform.fill_random = failing_fill
        try:
            with _RecordingMkfs() as mkfs:
                async def main():
                    with self.assertRaises(RuntimeError) as ctx:
                        await sd.erase_and_format()
                    self.assertIn("interrupted", str(ctx.exception))
                    self.assertIsInstance(ctx.exception.__cause__, MemoryError)

                _run(main())
        finally:
            platform.fill_random = real_fill

        self.assertEqual(len(dev.writes), 2)
        self.assertEqual(mkfs.calls, [])
        self.assertEqual(dev.power_states, [True, False])

    def test_cleanup_failure_does_not_mask_the_write_error(self):
        """An exception raised in a finally block supplants the one already
        propagating. The half-overwritten-card message must survive a
        power-off that also fails."""
        class _FailingCleanupDevice(_FakeBlockDevice):
            def writeblocks(self, start, data):
                self.writes.append((start, len(data)))
                raise OSError("card removed mid-erase")

            def power(self, state):
                self.power_states.append(state)
                if not state:
                    raise OSError("power-off failed too")

        dev = _FailingCleanupDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            message = str(ctx.exception)
            self.assertIn("half-overwritten", message)
            self.assertIn("card may have been removed", message)

        _run(main())
        self.assertEqual(dev.power_states, [True, False])

    def test_cleanup_failure_is_reported_when_the_erase_succeeded(self):
        """With no failure in flight there is nothing to mask, so a cleanup
        error must not be swallowed either."""
        class _FailingPowerOffDevice(_FakeBlockDevice):
            def power(self, state):
                self.power_states.append(state)
                if not state:
                    raise OSError("power-off failed")

        dev = _FailingPowerOffDevice(block_count=8, block_size=512)
        sd = platform.SDCard(sd=dev)

        with _RecordingMkfs() as mkfs:
            with self.assertRaises(OSError) as ctx:
                _run(sd.erase_and_format())
            self.assertIn("power-off failed", str(ctx.exception))

        # The erase itself did complete before cleanup failed.
        self.assertEqual(sum(n for _, n in dev.writes), 8 * 512)
        self.assertEqual(len(mkfs.calls), 1)

    def test_oversized_block_size_is_rejected(self):
        """chunk_blocks falls to 1 for a huge block size, so the buffer
        would be one block - unbounded unless the geometry check caps it."""
        class _HugeBlockDevice(_FakeBlockDevice):
            def ioctl(self, op, arg):
                if op == 4:
                    return 4
                if op == 5:
                    return platform.MAX_SANE_BLOCK_SIZE + 1
                raise OSError("unsupported ioctl")

        dev = _HugeBlockDevice(block_count=4, block_size=1)
        sd = platform.SDCard(sd=dev)

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            self.assertIn("invalid geometry", str(ctx.exception))
            self.assertIn("maximum", str(ctx.exception))

        _run(main())
        self.assertEqual(dev.writes, [])

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

    def test_power_on_returning_false_is_reported_before_any_write(self):
        """pyb.SDCard.power() signals a failed power-on with a False return,
        not an exception. If that is ignored, the failure resurfaces as
        ioctl(4) == 0 - the block count of an inactive card - and a routine
        init failure gets reported as bogus geometry."""
        class _RefusesPowerDevice(_FakeBlockDevice):
            def power(self, state):
                self.power_states.append(state)
                return False if state else True

            def ioctl(self, op, arg):
                # What the pinned firmware really reports for an inactive
                # card: a valid block size, and zero capacity.
                if op == 4:
                    return 0
                if op == 5:
                    return 512
                raise OSError("unsupported ioctl")

        dev = _RefusesPowerDevice(block_count=4100, block_size=512)
        sd = platform.SDCard(sd=dev)

        async def main():
            with self.assertRaises(RuntimeError) as ctx:
                await sd.erase_and_format()
            message = str(ctx.exception)
            self.assertIn("Could not access", message)
            self.assertIn("could not be initialized", message)
            # Not the geometry error - that would misdescribe the fault.
            self.assertNotIn("invalid geometry", message)

        with _RecordingMkfs() as mkfs:
            _run(main())

        self.assertEqual(dev.writes, [])
        self.assertEqual(mkfs.calls, [])
        self.assertEqual(dev.power_states, [True, False])

    def test_power_on_returning_none_is_accepted(self):
        """Block devices that report no status return None from power().
        Only an explicit False may be treated as a failure."""
        dev = _FakeBlockDevice(block_count=8, block_size=512)
        self.assertIsNone(dev.power(True))
        dev.power_states.clear()
        sd = platform.SDCard(sd=dev)

        with _RecordingMkfs() as mkfs:
            _run(sd.erase_and_format())

        self.assertEqual(sum(n for _, n in dev.writes), 8 * 512)
        self.assertEqual(len(mkfs.calls), 1)

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
        self.assertEqual(dev.writes, [(0, 128 * 1024)])
        self.assertEqual(dev.power_states, [True, False])


class SDCardUnmountTest(TestCase):
    def _patch_vfs(self, sync_fn, umount_fn):
        had_sync = hasattr(os, "sync")
        had_umount = hasattr(os, "umount")
        old_sync = getattr(os, "sync", None)
        old_umount = getattr(os, "umount", None)
        os.sync = sync_fn
        os.umount = umount_fn
        return had_sync, had_umount, old_sync, old_umount

    def _restore_vfs(self, state):
        had_sync, had_umount, old_sync, old_umount = state
        if had_sync:
            os.sync = old_sync
        else:
            del os.sync
        if had_umount:
            os.umount = old_umount
        else:
            del os.umount

    def test_sync_failure_still_attempts_umount_and_clears_state_on_success(self):
        dev = _FakeBlockDevice(block_count=1, block_size=512)
        sd = platform.SDCard(sd=dev)
        sd._mounted = True
        calls = []

        def failing_sync():
            calls.append("sync")
            raise OSError("sync failed")

        def successful_umount(path):
            calls.append(path)

        state = self._patch_vfs(failing_sync, successful_umount)
        try:
            with self.assertRaises(OSError):
                sd.unmount()
        finally:
            self._restore_vfs(state)
        self.assertEqual(calls, ["sync", "/sd"])
        self.assertFalse(sd._mounted)
        self.assertEqual(dev.power_states, [False])

    def test_umount_failure_keeps_card_powered_for_a_retry(self):
        """If the VFS mount could not be removed, /sd is still mounted.
        Cutting power there would leave the VFS pointing at a dead block
        device, and the retry path syncs before it umounts - so the card
        must stay powered while _mounted is still True."""
        dev = _FakeBlockDevice(block_count=1, block_size=512)
        sd = platform.SDCard(sd=dev)
        sd._mounted = True

        def failing_umount(path):
            raise OSError("umount failed")

        state = self._patch_vfs(lambda: None, failing_umount)
        try:
            with self.assertRaises(OSError):
                sd.unmount()
        finally:
            self._restore_vfs(state)
        self.assertTrue(sd._mounted)
        self.assertEqual(dev.power_states, [])

    def test_retry_after_umount_failure_powers_down(self):
        """The state left behind above must be recoverable: a second
        unmount() on a still-powered card succeeds and powers it down."""
        dev = _FakeBlockDevice(block_count=1, block_size=512)
        sd = platform.SDCard(sd=dev)
        sd._mounted = True
        attempts = []

        def flaky_umount(path):
            attempts.append(path)
            if len(attempts) == 1:
                raise OSError("umount failed")

        state = self._patch_vfs(lambda: None, flaky_umount)
        try:
            with self.assertRaises(OSError):
                sd.unmount()
            self.assertEqual(dev.power_states, [])
            sd.unmount()
        finally:
            self._restore_vfs(state)
        self.assertEqual(attempts, ["/sd", "/sd"])
        self.assertFalse(sd._mounted)
        self.assertEqual(dev.power_states, [False])

    def test_umount_failure_on_removed_card_still_powers_down(self):
        """A card that is gone cannot be retried against, so holding the
        interface powered would serve no purpose - power it down."""
        class _AbsentDevice(_FakeBlockDevice):
            def present(self):
                return False

        dev = _AbsentDevice(block_count=1, block_size=512)
        sd = platform.SDCard(sd=dev)
        sd._mounted = True

        def failing_umount(path):
            raise OSError("umount failed")

        state = self._patch_vfs(lambda: None, failing_umount)
        try:
            with self.assertRaises(OSError):
                sd.unmount()
        finally:
            self._restore_vfs(state)
        self.assertTrue(sd._mounted)
        self.assertEqual(dev.power_states, [False])

    def test_umount_failure_with_failing_presence_check_powers_down(self):
        """A presence check that raises must not mask the umount error, and
        a card we cannot interrogate is treated as gone."""
        class _BrokenPresenceDevice(_FakeBlockDevice):
            def present(self):
                raise OSError("card interface unresponsive")

        dev = _BrokenPresenceDevice(block_count=1, block_size=512)
        sd = platform.SDCard(sd=dev)
        sd._mounted = True

        def failing_umount(path):
            raise OSError("umount failed")

        state = self._patch_vfs(lambda: None, failing_umount)
        try:
            with self.assertRaises(OSError) as ctx:
                sd.unmount()
            self.assertIn("umount failed", str(ctx.exception))
        finally:
            self._restore_vfs(state)
        self.assertEqual(dev.power_states, [False])


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

    def test_overwrites_once_then_unlinks(self):
        """One pass is deliberate (NIST SP 800-88: extra passes buy nothing
        on flash). Assert the file's whole length is rewritten exactly once
        and the file is then gone."""
        with open(self.path, "rb") as f:
            original = f.read()
        writes = []

        class RecordingFile:
            def __init__(self, f):
                self._f = f

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._f.close()

            def seek(self, offset, whence=0):
                return self._f.seek(offset, whence)

            def tell(self):
                return self._f.tell()

            def flush(self):
                return self._f.flush()

            def fileno(self):
                return self._f.fileno()

            def write(self, data):
                writes.append(len(data))
                return self._f.write(data)

        real_open = getattr(platform, "open", None)
        platform.open = lambda path, mode: RecordingFile(open(path, mode))
        try:
            size = platform.secure_delete_file(self.path)
        finally:
            if real_open is None:
                del platform.open
            else:
                platform.open = real_open
        self.assertEqual(size, len(original))
        self.assertEqual(sum(writes), len(original))
        self.assertFalse(platform.file_exists(self.path))

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
                platform.secure_delete_file(self.path)
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
                platform.secure_delete_file(self.path)
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
        self.assertTrue(_dir_exists(self.path))
        platform.secure_delete_tree(self.path)
        # stat, not file_exists: file_exists() opens the path as a file and
        # so reports False for a directory that is still very much there.
        self.assertFalse(_dir_exists(self.path))
        self.assertNotIn("deltree", [e[0] for e in os.ilistdir("testdir")])

    def test_nested_tree_deleted(self):
        self._make_files(2)
        platform.maybe_mkdir("%s/inner" % self.path)
        platform.maybe_mkdir("%s/inner/deeper" % self.path)
        with open("%s/inner/deeper/leaf.bin" % self.path, "wb") as f:
            f.write(b"y" * 10)
        platform.secure_delete_tree(self.path)
        self.assertFalse(_dir_exists("%s/inner/deeper" % self.path))
        self.assertFalse(_dir_exists("%s/inner" % self.path))
        self.assertFalse(_dir_exists(self.path))

    def test_tree_at_cap_deleted(self):
        self._make_files(platform.SECURE_DELETE_MAX_ENTRIES)
        platform.secure_delete_tree(self.path)
        self.assertFalse(_dir_exists(self.path))
        self.assertNotIn("deltree", [e[0] for e in os.ilistdir("testdir")])

    def test_tree_over_cap_rejected_before_overwrite(self):
        self._make_files(platform.SECURE_DELETE_MAX_ENTRIES + 1)
        before = {}
        for i in range(platform.SECURE_DELETE_MAX_ENTRIES + 1):
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
            for i in range(platform.SECURE_DELETE_MAX_ENTRIES + 1000):
                seen.append(i)
                yield ("file_%d.bin" % i, 0x8000, 0, 0)

        real_ilistdir = os.ilistdir
        os.ilistdir = many_entries
        try:
            with self.assertRaises(RuntimeError):
                platform._collect_files(
                    "virtual", platform.SECURE_DELETE_MAX_ENTRIES
                )
        finally:
            os.ilistdir = real_ilistdir
        # Enumeration must abort on the first over-cap entry rather than
        # walking the whole (adversarial) directory first.
        self.assertEqual(len(seen), platform.SECURE_DELETE_MAX_ENTRIES + 1)

    def test_collector_rejects_too_many_directories(self):
        seen = []

        def many_dirs(path):
            if path != "virtual":
                return
            for i in range(platform.SECURE_DELETE_MAX_ENTRIES + 1000):
                seen.append(i)
                yield ("dir_%d" % i, 0x4000, 0, 0)

        real_ilistdir = os.ilistdir
        os.ilistdir = many_dirs
        try:
            with self.assertRaises(RuntimeError) as ctx:
                platform._collect_files(
                    "virtual", platform.SECURE_DELETE_MAX_ENTRIES
                )
            self.assertIn("entries", str(ctx.exception))
        finally:
            os.ilistdir = real_ilistdir
        self.assertEqual(
            len(seen), platform.SECURE_DELETE_MAX_ENTRIES + 1
        )

    def test_collector_rejects_excessive_depth(self):
        seen = []

        def deep_dirs(path):
            depth = path.count("/")
            seen.append(depth)
            yield ("child", 0x4000, 0, 0)

        real_ilistdir = os.ilistdir
        os.ilistdir = deep_dirs
        try:
            with self.assertRaises(RuntimeError) as ctx:
                platform._collect_files(
                    "virtual", platform.SECURE_DELETE_MAX_ENTRIES
                )
            self.assertIn("deeper", str(ctx.exception))
        finally:
            os.ilistdir = real_ilistdir
        self.assertEqual(
            max(seen), platform.SECURE_DELETE_MAX_DEPTH
        )

    def test_collector_rejects_oversized_file_before_retaining_it(self):
        real_ilistdir = os.ilistdir
        real_stat = os.stat

        def one_file(path):
            yield ("large.bin", 0x8000, 0, 0)

        os.ilistdir = one_file
        os.stat = lambda path: (0, 0, 0, 0, 0, 0,
                                platform.SECURE_DELETE_MAX_FILE_BYTES + 1)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                platform._collect_files(
                    "virtual",
                    platform.SECURE_DELETE_MAX_ENTRIES,
                    max_file_bytes=platform.SECURE_DELETE_MAX_FILE_BYTES,
                    max_total_bytes=platform.SECURE_DELETE_MAX_TOTAL_BYTES,
                )
            self.assertIn("maximum", str(ctx.exception))
        finally:
            os.ilistdir = real_ilistdir
            os.stat = real_stat

    def test_collector_rejects_oversized_total_before_retaining_file(self):
        real_ilistdir = os.ilistdir
        real_stat = os.stat

        seen = []

        def three_files(path):
            for name in ("first.bin", "second.bin", "third.bin"):
                seen.append(name)
                yield (name, 0x8000, 0, 0)

        os.ilistdir = three_files
        os.stat = lambda path: (0, 0, 0, 0, 0, 0,
                                platform.SECURE_DELETE_MAX_FILE_BYTES)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                platform._collect_files(
                    "virtual",
                    platform.SECURE_DELETE_MAX_ENTRIES,
                    max_file_bytes=platform.SECURE_DELETE_MAX_FILE_BYTES,
                    max_total_bytes=platform.SECURE_DELETE_MAX_TOTAL_BYTES,
                )
            self.assertIn("bytes", str(ctx.exception))
        finally:
            os.ilistdir = real_ilistdir
            os.stat = real_stat
        # Two 4 MiB files fit the 8 MiB budget; the third must abort the
        # walk immediately rather than after enumerating anything further.
        self.assertEqual(seen, ["first.bin", "second.bin", "third.bin"])


class BlockGeometryTest(TestCase):
    def test_min_blocks_rejects_a_device_too_small_for_the_range(self):
        """platform.wipe() writes a hardcoded block range. A device passing
        the size/count checks can still be too small to contain it, so the
        required minimum has to be part of the check."""
        with self.assertRaises(RuntimeError) as ctx:
            platform.validate_block_geometry(
                512, 300, "internal flash",
                min_blocks=platform.WIPE_LAST_BLOCK + 1,
            )
        message = str(ctx.exception)
        self.assertIn("only 300 blocks", message)
        self.assertIn("450 are required", message)

    def test_min_blocks_accepts_a_large_enough_device(self):
        platform.validate_block_geometry(
            512, platform.WIPE_LAST_BLOCK + 1, "internal flash",
            min_blocks=platform.WIPE_LAST_BLOCK + 1,
        )

    def test_wipe_range_is_covered_by_its_own_minimum(self):
        """The range written and the minimum geometry demanded to write it
        must not drift apart."""
        self.assertEqual(
            max(range(platform.WIPE_FIRST_BLOCK,
                      platform.WIPE_LAST_BLOCK + 1)),
            platform.WIPE_LAST_BLOCK,
        )

    def test_block_size_upper_bound(self):
        platform.validate_block_geometry(
            platform.MAX_SANE_BLOCK_SIZE, 10, "SD card")
        with self.assertRaises(RuntimeError):
            platform.validate_block_geometry(
                platform.MAX_SANE_BLOCK_SIZE + 1, 10, "SD card")


class FillRandomTest(TestCase):
    def test_fills_whole_buffer_and_changes_between_calls(self):
        buf = bytearray(1000)
        platform.fill_random(buf)
        self.assertNotEqual(bytes(buf), bytes(1000))
        first = bytes(buf)
        platform.fill_random(buf)
        # Fresh data every pass, not a repeat of the first fill.
        self.assertNotEqual(first, bytes(buf))

    def test_fills_a_memoryview_slice_without_touching_the_rest(self):
        buf = bytearray(600)
        platform.fill_random(memoryview(buf)[:300])
        self.assertNotEqual(bytes(buf[:300]), bytes(300))
        self.assertEqual(bytes(buf[300:]), bytes(300))
