"""
Regression tests for two hardening properties of the scratch-file
reconciler (keystore.flash.reconcile_scratch_dir):

1. Directory scanning is streamed and filtered. An SD card holding
   thousands of unrelated files must not be turned into a list of every
   filename in RAM, and unrelated files must never be renamed or deleted.

2. A scratch-named file far larger than any key Specter writes is treated
   as a faulty/tampered card: automatic reconciliation preserves it
   instead of starting a multi-minute secure overwrite during key
   loading, keeps reconciling every other entry, and offers the user an
   explicit confirmed secure delete afterwards. No path falls back to a
   plain unlink.

These do NOT test recovery from a power loss *inside* a FatFs rename -
that is an accepted, documented limitation (see docs/data-storage.md).
"""
import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

import os
from unittest import TestCase

import platform
import keystore.flash as flash_module
from keystore.core import KeyStoreError
from keystore.flash import FlashKeyStore, SAVE_TMP_SUFFIX, SAVE_OLD_SUFFIX
from tests.util import clear_testdir


class _FakeScreen:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Base(TestCase):
    def setUp(self):
        clear_testdir()
        platform.maybe_mkdir("testdir")
        self.ks = FlashKeyStore()
        self.ks.path = "testdir"
        self.prefix = self.ks.fileprefix(self.ks.flashpath)
        self.target = "testdir/%s.mykey" % self.prefix
        self.tmp = "testdir/.%s.mykey%s" % (self.prefix, SAVE_TMP_SUFFIX)
        self.old = "testdir/.%s.mykey%s" % (self.prefix, SAVE_OLD_SUFFIX)

        self.deleted = []
        self.unlinked = []
        self._real_delete = platform.secure_delete_file
        self._real_remove = os.remove
        real_delete = self._real_delete
        real_remove = self._real_remove

        def recording_delete(path):
            self.deleted.append(path)
            return real_delete(path)

        def recording_remove(path):
            self.unlinked.append(path)
            return real_remove(path)

        platform.secure_delete_file = recording_delete
        os.remove = recording_remove

    def tearDown(self):
        platform.secure_delete_file = self._real_delete
        os.remove = self._real_remove
        clear_testdir()

    def _write(self, path, data=b"x"):
        with open(path, "wb") as f:
            f.write(data)

    def _reconcile(self):
        return self.ks.reconcile_scratch_dir(self.ks.flashpath)


class DirectoryScanStreamingTest(_Base):
    def _noisy_ilistdir(self, extra_names, count=5000):
        real_listdir = os.listdir

        def fake(path):
            for name in real_listdir(path):
                yield (name, 0x8000, 0, 0)
            for name in extra_names:
                yield (name, 0x8000, 0, 0)
            for i in range(count):
                yield ("unrelated_%d.dat" % i, 0x8000, 0, 0)

        return fake

    def test_only_specter_entries_are_retained_not_every_name(self):
        """The scan must filter as it streams: a directory of thousands of
        unrelated entries yields a names list of only Specter's own."""
        self._write(self.target, b"enc:key")
        self._write(self.old, b"enc:previous")

        real_ilistdir = os.ilistdir
        os.ilistdir = self._noisy_ilistdir(
            [".foreign.old", ".other.tmp", "randomfile"]
        )
        try:
            names, leftovers = self.ks._relevant_scratch_entries(
                "testdir", self.prefix
            )
        finally:
            os.ilistdir = real_ilistdir

        self.assertEqual(names, ["%s.mykey" % self.prefix])
        self.assertEqual(list(leftovers), ["%s.mykey" % self.prefix])
        self.assertLess(len(names), 5)

    def test_reconcile_over_a_noisy_directory_still_works(self):
        """State D (target + stale .old) reconciles normally even with
        thousands of unrelated entries around it, and touches none of
        them."""
        self._write(self.target, b"enc:key")
        self._write(self.old, b"enc:previous")
        self._write("testdir/.foreign.old", b"user data")
        self._write("testdir/keepme.txt", b"user data")

        real_ilistdir = os.ilistdir
        os.ilistdir = self._noisy_ilistdir([".foreign.old", "keepme.txt"])
        try:
            names = self._reconcile()
        finally:
            os.ilistdir = real_ilistdir

        self.assertEqual(names, ["%s.mykey" % self.prefix])
        self.assertEqual(self.deleted, [self.old])
        self.assertFalse(platform.file_exists(self.old))
        self.assertTrue(platform.file_exists(self.target))
        self.assertTrue(platform.file_exists("testdir/.foreign.old"))
        self.assertTrue(platform.file_exists("testdir/keepme.txt"))

    def test_foreign_dotfiles_and_unrelated_files_are_untouched(self):
        self._write("testdir/.something.old", b"user data")
        self._write("testdir/.other.tmp", b"user data")
        self._write("testdir/notes.old", b"user data")

        names = self._reconcile()

        self.assertEqual(names, [])
        self.assertEqual(self.deleted, [])
        self.assertEqual(self.unlinked, [])
        for p in ("testdir/.something.old", "testdir/.other.tmp",
                  "testdir/notes.old"):
            self.assertTrue(platform.file_exists(p))


class _OversizedBase(_Base):
    def _patch_stat_big(self, big_paths):
        real_stat = os.stat
        big = set(big_paths)
        oversize = platform.SCRATCH_RECONCILE_MAX_BYTES + 1

        def fake_stat(path):
            if path in big:
                return (0, 0, 0, 0, 0, 0, oversize, 0, 0, 0)
            return real_stat(path)

        os.stat = fake_stat
        self.addCleanup(lambda: setattr(os, "stat", real_stat))


class OversizedScratchReconcileTest(_OversizedBase):
    def test_oversized_stale_old_is_preserved_not_overwritten(self):
        self._write(self.target, b"enc:key")
        self._write(self.old, b"enc:previous")
        self._patch_stat_big([self.old])

        names = self._reconcile()

        self.assertEqual(names, ["%s.mykey" % self.prefix])
        self.assertNotIn(self.old, self.deleted)
        self.assertEqual(self.unlinked, [])
        self.assertTrue(platform.file_exists(self.old))
        self.assertIn(self.old, getattr(self.ks, "_oversized_scratch", {}))

    def test_oversized_tmp_is_preserved_not_overwritten(self):
        self._write(self.target, b"enc:key")
        self._write(self.tmp, b"enc:leftover")
        self._patch_stat_big([self.tmp])

        self._reconcile()

        self.assertNotIn(self.tmp, self.deleted)
        self.assertEqual(self.unlinked, [])
        self.assertTrue(platform.file_exists(self.tmp))
        self.assertIn(self.tmp, self.ks._oversized_scratch)

    def test_oversized_old_without_target_is_not_installed_as_key(self):
        """A multi-megabyte blob must not be renamed onto the key name."""
        self._write(self.old, b"enc:previous")
        self._patch_stat_big([self.old])

        names = self._reconcile()

        self.assertEqual(names, [])
        self.assertFalse(platform.file_exists(self.target))
        self.assertTrue(platform.file_exists(self.old))
        self.assertIn(self.old, self.ks._oversized_scratch)

    def test_oversized_entry_does_not_block_other_entries(self):
        # normal stale .old -> retired; oversized stale .old -> preserved;
        # normal leftover .tmp -> discarded.
        a_target = "testdir/%s.aaa" % self.prefix
        a_old = "testdir/.%s.aaa%s" % (self.prefix, SAVE_OLD_SUFFIX)
        b_target = "testdir/%s.bbb" % self.prefix
        b_old = "testdir/.%s.bbb%s" % (self.prefix, SAVE_OLD_SUFFIX)
        c_target = "testdir/%s.ccc" % self.prefix
        c_tmp = "testdir/.%s.ccc%s" % (self.prefix, SAVE_TMP_SUFFIX)
        for p in (a_target, a_old, b_target, b_old, c_target, c_tmp):
            self._write(p, b"enc:data")
        self._patch_stat_big([b_old])

        self._reconcile()

        self.assertIn(a_old, self.deleted)
        self.assertIn(c_tmp, self.deleted)
        self.assertNotIn(b_old, self.deleted)
        self.assertFalse(platform.file_exists(a_old))
        self.assertFalse(platform.file_exists(c_tmp))
        self.assertTrue(platform.file_exists(b_old))
        self.assertEqual(list(self.ks._oversized_scratch), [b_old])

    def test_unrelated_oversized_file_is_ignored_entirely(self):
        self._write("testdir/.huge.old", b"user data")
        self._patch_stat_big(["testdir/.huge.old"])

        names = self._reconcile()

        self.assertEqual(names, [])
        self.assertEqual(self.deleted, [])
        self.assertTrue(platform.file_exists("testdir/.huge.old"))
        self.assertEqual(getattr(self.ks, "_oversized_scratch", {}), {})

    def test_normal_sized_scratch_still_auto_deleted(self):
        self._write(self.target, b"enc:key")
        self._write(self.old, b"enc:previous")

        self._reconcile()

        self.assertEqual(self.deleted, [self.old])
        self.assertFalse(platform.file_exists(self.old))
        self.assertEqual(getattr(self.ks, "_oversized_scratch", {}), {})

    def test_no_silent_plain_unlink_on_the_oversized_path(self):
        self._write(self.target, b"enc:key")
        self._write(self.old, b"enc:previous")
        self._write(self.tmp, b"enc:leftover")
        self._patch_stat_big([self.old, self.tmp])

        self._reconcile()

        self.assertEqual(self.unlinked, [])
        self.assertEqual(self.deleted, [])


class OversizedReviewUITest(_OversizedBase):
    def setUp(self):
        super().setUp()
        self._real_prompt = flash_module.Prompt
        self._real_alert = flash_module.Alert
        flash_module.Prompt = _FakeScreen
        flash_module.Alert = _FakeScreen
        self.addCleanup(self._restore_screens)

    def _restore_screens(self):
        flash_module.Prompt = self._real_prompt
        flash_module.Alert = self._real_alert

    def _prompt_answers(self, answers):
        seq = list(answers)
        self.shown = []

        async def show(screen):
            self.shown.append(screen)
            if screen.args and screen.args[0] in ("Deleted",
                                                  "Could not delete"):
                return None
            return seq.pop(0)

        self.ks.show = show

    def test_review_delete_uses_secure_deletion(self):
        self._write(self.old, b"enc:previous")
        self.ks._oversized_scratch = {self.old: 600 * 1024 * 1024}
        self._prompt_answers([True])

        _run(self.ks._review_oversized_scratch())

        self.assertEqual(self.deleted, [self.old])
        self.assertFalse(platform.file_exists(self.old))
        self.assertEqual(self.ks._oversized_scratch, {})

    def test_review_skip_preserves_the_file(self):
        self._write(self.old, b"enc:previous")
        self.ks._oversized_scratch = {self.old: 600 * 1024 * 1024}
        self._prompt_answers([False])

        _run(self.ks._review_oversized_scratch())

        self.assertEqual(self.deleted, [])
        self.assertEqual(self.unlinked, [])
        self.assertTrue(platform.file_exists(self.old))
        self.assertEqual(self.ks._oversized_scratch, {})

    def test_storage_menu_offers_the_review_button_and_routes_to_it(self):
        self._write(self.target, b"enc:key")
        self._write(self.old, b"enc:previous")
        self._patch_stat_big([self.old])
        real_menu = flash_module.Menu
        flash_module.Menu = _FakeScreen
        self.addCleanup(lambda: setattr(flash_module, "Menu", real_menu))

        seen_menus = []
        # Menu -> pick "review"; Prompt -> confirm delete; Alert -> ack;
        # Menu -> back out of the storage menu.
        seq = [3, True, None, 255]

        async def show(screen):
            if isinstance(screen, _FakeScreen) and screen.args \
                    and isinstance(screen.args[0], list):
                seen_menus.append(screen.args[0])
            return seq.pop(0)

        self.ks.show = show

        _run(self.ks.storage_menu())

        self.assertTrue(
            any(btn[0] == 3 for btn in seen_menus[0]),
            "storage menu should list the review option",
        )
        self.assertEqual(self.deleted, [self.old])
        self.assertFalse(platform.file_exists(self.old))
        self.assertEqual(self.ks._oversized_scratch, {})


class EstimateHelpersTest(TestCase):
    def test_threshold_is_far_above_a_real_key_file(self):
        # a 24-word phrase + AEAD wrapper is a few hundred bytes
        self.assertGreater(platform.SCRATCH_RECONCILE_MAX_BYTES, 256 * 1024)

    def test_format_size_is_human_readable(self):
        self.assertEqual(platform.format_size(512), "512 bytes")
        self.assertEqual(platform.format_size(512 * 1024 * 1024), "512.0 MB")

    def test_duration_estimate_buckets(self):
        self.assertEqual(
            platform.secure_delete_duration_estimate(64 * 1024), "< 1 minute"
        )
        self.assertIn(
            "minute",
            platform.secure_delete_duration_estimate(512 * 1024 * 1024),
        )
        # strictly increasing coarseness with size
        small = platform.secure_delete_duration_estimate(1 * 1024 * 1024)
        large = platform.secure_delete_duration_estimate(2 * 1024 * 1024 * 1024)
        self.assertNotEqual(small, large)
