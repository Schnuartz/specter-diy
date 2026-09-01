"""
Regression tests: saving over an existing key file must neither leave the
old one recoverable nor destroy it before the new one exists.

Two failure modes pull against each other here.

`save_aead()` opens the path "wb". On FAT that truncates the file, which
frees its cluster chain **without** overwriting it, and the new contents
are written wherever the allocator puts them - so the previous encrypted
mnemonic stays readable in free space, where a later secure delete can no
longer reach it and where the unrotated `enc_secret` still decrypts it.

Securely deleting the old file first fixes that and introduces the
opposite problem: the file being replaced may be the user's only
persistent copy, and a pulled card, a failed write or a power cut between
the delete and the new write leaves them with none at all.

`_save_key_file()` therefore writes and verifies the new copy under a
scratch name, renames the target aside to .old, renames the scratch onto
the target, and only then securely deletes .old.

The one state a power cut can leave behind - target missing, .old present -
is reconciled by `reconcile_scratch_dir()`, which runs before keys are
listed, loaded or saved, so an interrupted replacement can never make a
surviving key look gone.
"""
import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

import os
from unittest import TestCase

import platform
import keystore.flash as flash_module
import keystore.sdcard as sdcard_module
from keystore.core import KeyStoreError
from keystore.flash import FlashKeyStore, SAVE_TMP_SUFFIX, SAVE_OLD_SUFFIX
from keystore.sdcard import SDKeyStore
from tests.util import clear_testdir


class _FakePrompt:
    """The overwrite confirmation. The native GUI stub's Prompt takes no
    arguments, so stand in for it - self.ks.show() is what decides the
    answer here anyway."""

    def __init__(self, *args, **kwargs):
        pass


class PromptImportTest(TestCase):
    def test_flash_keystore_can_build_the_overwrite_prompt(self):
        """FlashKeyStore.save_mnemonic() referenced Prompt without importing
        it, so confirming an overwrite on internal flash raised NameError
        instead of asking. Nothing here reaches that branch unless the name
        resolves."""
        self.assertIsNotNone(flash_module.Prompt)


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _SaveMnemonicBase(TestCase):
    keystore_cls = None

    OLD = b"old-encrypted-key-material"

    def setUp(self):
        clear_testdir()
        platform.maybe_mkdir("testdir")
        self.calls = []
        self.ks = self._make_keystore()
        self.ks.path = "testdir"
        self.ks.pin = None  # not locked
        self.ks.mnemonic = "ability " * 11 + "acid"
        self.ks.enc_secret = b"\x00" * 32
        self.target = "testdir/reckless.mykey"
        self.tmp = "testdir/.reckless.mykey" + SAVE_TMP_SUFFIX
        self.old = "testdir/.reckless.mykey" + SAVE_OLD_SUFFIX
        self.plaintext = self.ks.mnemonic.encode()

        test = self

        async def get_input(*args, **kwargs):
            return "mykey"

        async def show(*args, **kwargs):
            return True

        async def load_mnemonic(path, *args, **kwargs):
            return None

        def save_aead(path, adata=b"", plaintext=b"", key=None, strict=False):
            # Stands in for the real AEAD: recognisable on disk, readable
            # by the load_aead() below, and it records the call order.
            test.calls.append(("save_aead", path, strict))
            with open(path, "wb") as f:
                f.write(b"enc:" + plaintext)

        def load_aead(path, key=None):
            test.calls.append(("load_aead", path))
            with open(path, "rb") as f:
                data = f.read()
            if not data.startswith(b"enc:"):
                raise ValueError("not an aead file: %s" % path)
            return b"", data[4:]

        self.ks.get_input = get_input
        self.ks.show = show
        self.ks.load_mnemonic = load_mnemonic
        self.ks.save_aead = save_aead
        self.ks.load_aead = load_aead

        self._real_prompts = (flash_module.Prompt, sdcard_module.Prompt)
        flash_module.Prompt = _FakePrompt
        sdcard_module.Prompt = _FakePrompt

        self._real_delete = platform.secure_delete_file

        def recording_delete(path):
            test.calls.append(("secure_delete", path))
            return test._real_delete(path)

        platform.secure_delete_file = recording_delete

    def tearDown(self):
        flash_module.Prompt, sdcard_module.Prompt = self._real_prompts
        platform.secure_delete_file = self._real_delete
        clear_testdir()

    def _make_keystore(self):
        return self.keystore_cls()

    def _write_existing(self):
        with open(self.target, "wb") as f:
            f.write(self.OLD)

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def _kinds(self):
        return [(c[0], c[1]) for c in self.calls]

    def test_new_copy_is_written_and_verified_before_the_old_one_dies(self):
        self._write_existing()

        _run(self.ks.save_mnemonic())

        self.assertEqual(
            self._kinds(),
            [
                ("save_aead", self.tmp),
                ("load_aead", self.tmp),
                ("secure_delete", self.old),
            ],
        )
        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)
        self.assertFalse(platform.file_exists(self.tmp))
        self.assertFalse(platform.file_exists(self.old))

    def test_the_replacing_write_is_synced_strictly(self):
        """platform.sync() swallows every sync error. Losing this write
        means losing the only copy of a recovery phrase, so it is the one
        save that must hear about a failed sync."""
        self._write_existing()

        _run(self.ks.save_mnemonic())

        saves = [c for c in self.calls if c[0] == "save_aead"]
        self.assertTrue(all(c[2] for c in saves), saves)

    def test_a_new_file_is_saved_strictly_and_without_a_temp_file(self):
        self.assertFalse(platform.file_exists(self.target))

        _run(self.ks.save_mnemonic())

        self.assertEqual(self.calls, [("save_aead", self.target, True)])
        self.assertFalse(platform.file_exists(self.tmp))

    def test_a_failed_write_of_the_new_copy_keeps_the_old_file(self):
        """The exact case that makes destroying the old file first
        unacceptable: the replacement never reaches the medium."""
        self._write_existing()

        def failing_save(path, adata=b"", plaintext=b"", key=None, strict=False):
            self.calls.append(("save_aead", path, strict))
            raise OSError("simulated I/O failure")

        self.ks.save_aead = failing_save

        # Reported as a KeyStoreError rather than a bare OSError: the
        # device shows an unhandled exception as a raw traceback, and "the
        # write failed" is something the user can act on.
        with self.assertRaises(KeyStoreError):
            _run(self.ks.save_mnemonic())

        self.assertEqual(self._read(self.target), self.OLD)
        self.assertNotIn(("secure_delete", self.old), self._kinds())
        self.assertFalse(platform.file_exists(self.tmp))

    def test_a_new_copy_that_does_not_read_back_keeps_the_old_file(self):
        self._write_existing()

        def wrong_load(path, key=None):
            self.calls.append(("load_aead", path))
            return b"", b"something else entirely"

        self.ks.load_aead = wrong_load

        with self.assertRaises(KeyStoreError):
            _run(self.ks.save_mnemonic())

        self.assertEqual(self._read(self.target), self.OLD)
        self.assertNotIn(("secure_delete", self.old), self._kinds())
        self.assertFalse(platform.file_exists(self.tmp))

    def test_the_replacement_is_in_place_before_the_old_copy_dies(self):
        """The reason the swap renames instead of deleting: when the copy
        being replaced is destroyed, the new one must already be under the
        real name. Otherwise a fault in between leaves the key only under a
        scratch name that no picker shows."""
        self._write_existing()
        seen = {}

        real_delete = platform.secure_delete_file

        def watching_delete(path):
            if platform.file_exists(self.target):
                with open(self.target, "rb") as f:
                    seen[path] = f.read()
            else:
                seen[path] = None
            self.calls.append(("secure_delete", path))
            return self._real_delete(path)

        platform.secure_delete_file = watching_delete
        try:
            _run(self.ks.save_mnemonic())
        finally:
            platform.secure_delete_file = real_delete

        self.assertEqual(seen.get(self.old), b"enc:" + self.plaintext)

    def test_a_failed_overwrite_of_the_old_file_still_stores_the_key(self):
        """Once the swap is done the save has succeeded. A failure to
        retire the replaced copy is a residue problem worth reporting, but
        it must not cost the user the key they just saved."""
        self._write_existing()

        def failing_delete(path):
            self.calls.append(("secure_delete", path))
            raise OSError("simulated I/O failure")

        platform.secure_delete_file = failing_delete

        with self.assertRaises(KeyStoreError):
            _run(self.ks.save_mnemonic())

        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)

    def test_a_failed_swap_rolls_the_existing_key_back(self):
        self._write_existing()

        real_rename = os.rename
        state = {"n": 0}

        def failing_rename(src, dst):
            state["n"] += 1
            if state["n"] == 2:  # tmp -> target
                raise OSError("simulated rename failure")
            return real_rename(src, dst)

        os.rename = failing_rename
        try:
            with self.assertRaises(KeyStoreError):
                _run(self.ks.save_mnemonic())
        finally:
            os.rename = real_rename

        self.assertEqual(self._read(self.target), self.OLD)
        self.assertFalse(platform.file_exists(self.tmp))
        self.assertFalse(platform.file_exists(self.old))

    def test_rollback_is_synced_before_tmp_is_destroyed(self):
        self._write_existing()
        real_rename = os.rename
        real_sync = platform.strict_sync
        real_delete = platform.secure_delete_file
        events = []
        state = {"n": 0}

        def failing_rename(src, dst):
            state["n"] += 1
            if state["n"] == 2:  # tmp -> target
                raise OSError("simulated rename failure")
            return real_rename(src, dst)

        def recording_sync(*args):
            events.append("sync")
            return real_sync(*args)

        def recording_delete(path):
            if path == self.tmp:
                events.append("delete_tmp")
            return real_delete(path)

        os.rename = failing_rename
        platform.strict_sync = recording_sync
        platform.secure_delete_file = recording_delete
        try:
            with self.assertRaises(KeyStoreError):
                _run(self.ks.save_mnemonic())
        finally:
            os.rename = real_rename
            platform.strict_sync = real_sync
            platform.secure_delete_file = real_delete

        self.assertLess(events.index("sync"), events.index("delete_tmp"))
        self.assertEqual(self._read(self.target), self.OLD)

    def test_failed_rollback_keeps_both_recovery_copies(self):
        self._write_existing()
        real_rename = os.rename
        state = {"n": 0}

        def failing_rename(src, dst):
            state["n"] += 1
            if state["n"] in (2, 3):  # swap and rollback both fail
                raise OSError("simulated rename failure")
            return real_rename(src, dst)

        os.rename = failing_rename
        try:
            with self.assertRaises(KeyStoreError):
                _run(self.ks.save_mnemonic())
        finally:
            os.rename = real_rename

        self.assertFalse(platform.file_exists(self.target))
        self.assertTrue(platform.file_exists(self.old))
        self.assertTrue(platform.file_exists(self.tmp))

        # The listing-time reconciler can undo the interrupted swap later.
        self.ks.reconcile_scratch_dir(self.ks.flashpath)
        self.assertEqual(self._read(self.target), self.OLD)
        self.assertFalse(platform.file_exists(self.old))
        self.assertFalse(platform.file_exists(self.tmp))

    def test_swap_sync_failure_keeps_old_copy_for_recovery(self):
        self._write_existing()
        real_sync = platform.strict_sync

        def failing_sync(*args):
            raise OSError("simulated sync failure")

        platform.strict_sync = failing_sync
        try:
            with self.assertRaises(KeyStoreError) as ctx:
                _run(self.ks.save_mnemonic())
            self.assertIn("previous copy was kept", str(ctx.exception))
        finally:
            platform.strict_sync = real_sync

        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)
        self.assertEqual(self._read(self.old), self.OLD)

        self.ks.reconcile_scratch_dir(self.ks.flashpath)
        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)
        self.assertFalse(platform.file_exists(self.old))

    def test_an_interrupted_swap_restores_the_only_surviving_copy(self):
        """Power cut between the two renames: the target is gone and .old
        is the only copy of the key. It has to come back, not be discarded."""
        with open(self.old, "wb") as f:
            f.write(b"enc:interrupted-key")
        self.assertFalse(platform.file_exists(self.target))

        _run(self.ks.save_mnemonic())

        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)
        self.assertFalse(platform.file_exists(self.old))
        self.assertFalse(platform.file_exists(self.tmp))

    def test_a_stale_old_copy_is_retired_when_the_target_survived(self):
        """Power cut after the swap but before the overwrite: the target is
        current, so .old is stale and gets overwritten, not restored."""
        self._write_existing()
        with open(self.old, "wb") as f:
            f.write(b"enc:stale-replaced-key")

        _run(self.ks.save_mnemonic())

        self.assertIn(("secure_delete", self.old), self._kinds())
        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)
        self.assertFalse(platform.file_exists(self.old))

    def test_a_leftover_temp_file_is_overwritten_not_truncated(self):
        """A save interrupted after the temp write leaves an encrypted
        phrase behind under the temp name. Truncating over it would free
        those clusters unoverwritten - the very thing this avoids."""
        self._write_existing()
        with open(self.tmp, "wb") as f:
            f.write(b"enc:leftover-from-an-interrupted-save")

        _run(self.ks.save_mnemonic())

        self.assertEqual(self._kinds()[0], ("secure_delete", self.tmp))
        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)
        self.assertFalse(platform.file_exists(self.tmp))

    def test_the_scratch_names_are_not_listed_as_keys(self):
        """A leftover scratch file must not show up in the key menus as
        something loadable."""
        prefix = self.ks.fileprefix(self.ks.flashpath)
        for path in (self.tmp, self.old):
            name = path.rsplit("/", 1)[1]
            self.assertFalse(name.lower().startswith(prefix), name)


class FlashSaveMnemonicTest(_SaveMnemonicBase):
    keystore_cls = FlashKeyStore

    def test_a_save_recovers_an_interrupted_replacement_of_another_key(self):
        """Recovery is per-directory, not per-save: an interrupted
        replacement of key A must become visible again when key B is
        saved, not stay hidden until A's name is saved again."""
        otherkey = "testdir/reckless.otherkey"
        with open("testdir/.reckless.otherkey" + SAVE_OLD_SUFFIX, "wb") as f:
            f.write(b"enc:interrupted-key")

        _run(self.ks.save_mnemonic())

        self.assertEqual(self._read(otherkey), b"enc:interrupted-key")
        self.assertEqual(self._read(self.target), b"enc:" + self.plaintext)


class SDSaveMnemonicTest(_SaveMnemonicBase):
    """SDKeyStore.save_mnemonic() has its own copy of the overwrite prompt,
    with the SD unmount bookkeeping around it - the same guarantees have to
    hold there. The target here is the internal-flash path, so the card
    handling is not involved."""

    keystore_cls = SDKeyStore

    def setUp(self):
        super().setUp()

        async def get_keypath(*args, **kwargs):
            return self.ks.flashpath

        self.ks.get_keypath = get_keypath


class ReconcileOnListingTest(TestCase):
    """A power cut between the two renames of a replacement leaves the
    target missing and only .old (and the verified .tmp) behind. The next
    save used to be the only recovery point - a user who loads the key
    instead of saving it again would see the key as gone. Reconciliation
    therefore happens before any listing or load, which is what the main
    menu's is_key_saved check and the file picker go through."""

    KEY = b"enc:saved-key"
    OLD = b"enc:previous-key"

    def setUp(self):
        clear_testdir()
        platform.maybe_mkdir("testdir")
        self.ks = FlashKeyStore()
        self.ks.path = "testdir"
        self.target = "testdir/reckless.mykey"
        self.tmp = "testdir/.reckless.mykey" + SAVE_TMP_SUFFIX
        self.old = "testdir/.reckless.mykey" + SAVE_OLD_SUFFIX
        self.deleted = []
        self._real_delete = platform.secure_delete_file
        real = self._real_delete

        def recording_delete(path):
            self.deleted.append(path)
            return real(path)

        platform.secure_delete_file = recording_delete

    def tearDown(self):
        platform.secure_delete_file = self._real_delete
        clear_testdir()

    def _write(self, path, data):
        with open(path, "wb") as f:
            f.write(data)

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def _listed_paths(self):
        return [b[0] for b in self.ks.load_files(self.ks.flashpath)
                if b[0] is not None]

    def test_state_a_normal_listing_is_untouched(self):
        self._write(self.target, self.KEY)

        self.assertEqual(self._listed_paths(), [self.target])
        self.assertEqual(self.deleted, [])
        self.assertTrue(platform.file_exists(self.target))

    def test_state_b_unverified_tmp_never_becomes_the_key(self):
        """Target authoritative: an interrupted new write leaves .tmp behind,
        but it must not be loadable merely because it exists."""
        self._write(self.target, self.KEY)
        self._write(self.tmp, b"enc:unverified-new-key")

        self.assertEqual(self._listed_paths(), [self.target])
        self.assertFalse(platform.file_exists(self.tmp))
        self.assertEqual(self.deleted, [self.tmp])
        self.assertEqual(self._read(self.target), self.KEY)

    def test_state_b_tmp_cleanup_failure_is_reported(self):
        """Continuing after this failure would let the next save truncate
        .tmp and free its old clusters without overwriting them."""
        self._write(self.target, self.KEY)
        self._write(self.tmp, b"enc:temporary-key")

        def failing_delete(path):
            raise OSError("simulated I/O failure")

        platform.secure_delete_file = failing_delete
        try:
            with self.assertRaises(KeyStoreError) as ctx:
                self.ks.load_files(self.ks.flashpath)
            self.assertIn("temporary encrypted copy", str(ctx.exception))
        finally:
            platform.secure_delete_file = self._real_delete
        self.assertEqual(self._read(self.target), self.KEY)
        self.assertTrue(platform.file_exists(self.tmp))

    def test_state_c_cut_between_the_renames_recovers_the_only_copy(self):
        """.old is the only surviving copy of the key. The listing must make
        it visible again as the target, not discard it."""
        self._write(self.old, self.OLD)
        self._write(self.tmp, b"enc:verified-new-key")

        self.assertEqual(self._listed_paths(), [self.target])
        self.assertEqual(self._read(self.target), self.OLD)
        self.assertFalse(platform.file_exists(self.old))
        self.assertFalse(platform.file_exists(self.tmp))

    def test_state_c_rename_failure_keeps_both_recovery_copies(self):
        self._write(self.old, self.OLD)
        self._write(self.tmp, b"enc:verified-new-key")
        real_rename = os.rename

        def failing_rename(src, dst):
            raise OSError("simulated rename failure")

        os.rename = failing_rename
        try:
            with self.assertRaises(KeyStoreError) as ctx:
                self.ks.load_files(self.ks.flashpath)
            self.assertIn("Failed to recover", str(ctx.exception))
        finally:
            os.rename = real_rename
        self.assertTrue(platform.file_exists(self.old))
        self.assertTrue(platform.file_exists(self.tmp))
        self.assertFalse(platform.file_exists(self.target))

    def test_state_d_stale_old_is_securely_retired(self):
        """New target installed, old version not yet destroyed: the target
        is authoritative and .old must be overwritten, not truncated."""
        self._write(self.target, self.KEY)
        self._write(self.old, self.OLD)

        self.assertEqual(self._listed_paths(), [self.target])
        self.assertEqual(self.deleted, [self.old])
        self.assertFalse(platform.file_exists(self.old))

    def test_state_d_retirement_failure_is_reported(self):
        """Failing to retire .old leaves the previous encrypted key
        recoverable - security-relevant, must not be swallowed."""
        self._write(self.target, self.KEY)
        self._write(self.old, self.OLD)

        def failing_delete(path):
            raise OSError("simulated I/O failure")

        platform.secure_delete_file = failing_delete
        try:
            with self.assertRaises(KeyStoreError) as ctx:
                self.ks.load_files(self.ks.flashpath)
            self.assertIn("recoverable from free space", str(ctx.exception))
        finally:
            platform.secure_delete_file = self._real_delete
        # The surviving target is untouched by the failed cleanup.
        self.assertEqual(self._read(self.target), self.KEY)

    def test_state_e_impossible_state_keeps_the_only_potential_copy(self):
        """.tmp without target and without .old cannot come from the save
        order - do not destroy the only potential copy, report instead."""
        self._write(self.tmp, self.KEY)

        with self.assertRaises(KeyStoreError) as ctx:
            self.ks.load_files(self.ks.flashpath)
        self.assertIn("inconsistent state", str(ctx.exception))
        self.assertTrue(platform.file_exists(self.tmp))
        self.assertEqual(self.deleted, [])

    def test_is_key_saved_sees_a_recoverable_key(self):
        """The main menu gates the Load button on is_key_saved. An
        interrupted replacement must still count as a saved key."""
        self._write(self.old, self.OLD)

        self.assertTrue(self.ks.is_key_saved)
        self.assertEqual(self._read(self.target), self.OLD)

    def test_is_key_saved_never_hides_a_key_on_recovery_failure(self):
        """A failed .old retirement raises (the residue is reported on the
        load path), but the menu gate must not hide a surviving key."""
        self._write(self.target, self.KEY)
        self._write(self.old, self.OLD)

        def failing_delete(path):
            raise OSError("simulated I/O failure")

        platform.secure_delete_file = failing_delete
        try:
            self.assertTrue(self.ks.is_key_saved)
        finally:
            platform.secure_delete_file = self._real_delete
        self.assertTrue(platform.file_exists(self.target))
        self.assertTrue(platform.file_exists(self.old))

    def test_foreign_dotfiles_are_not_treated_as_scratch(self):
        """Only Specter's own file prefix is scratch territory: a user's
        own dotted files on the same card must not be renamed or deleted."""
        foreign_old = "testdir/.something.old"
        foreign_tmp = "testdir/.other.tmp"
        self._write(foreign_old, b"user data")
        self._write(foreign_tmp, b"user data")

        self.assertEqual(self._listed_paths(), [])
        self.assertEqual(self.deleted, [])
        self.assertTrue(platform.file_exists(foreign_old))
        self.assertTrue(platform.file_exists(foreign_tmp))

    def test_directory_iterator_is_closed_before_reconciliation(self):
        closed = []

        class Entries:
            def __init__(self):
                self.items = iter([])

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.items)

            def close(self):
                closed.append(True)

        real_ilistdir = os.ilistdir
        os.ilistdir = lambda path: Entries()
        try:
            self.ks.reconcile_scratch_dir(self.ks.flashpath)
        finally:
            os.ilistdir = real_ilistdir
        self.assertEqual(closed, [True])

    def test_equal_flash_path_does_not_depend_on_string_identity(self):
        equal_path = ("_" + self.ks.flashpath)[1:]
        self.assertEqual(equal_path, self.ks.flashpath)
        self.assertIsNot(equal_path, self.ks.flashpath)
        self.assertEqual(self.ks.fileprefix(equal_path), "reckless")

    def test_leftovers_of_other_keys_do_not_collide(self):
        """Scratch names are per-key. Reconciling one key's leftover must
        not touch another key's files."""
        other_target = "testdir/reckless.other"
        other_old = "testdir/.reckless.other" + SAVE_OLD_SUFFIX
        self._write(self.target, self.KEY)
        self._write(other_old, self.OLD)

        self.assertEqual(sorted(self._listed_paths()),
                         sorted([self.target, other_target]))
        # .old was the only copy of "other", so it was restored by rename,
        # not destroyed. The unrelated target was left untouched.
        self.assertEqual(self.deleted, [])
        self.assertEqual(self._read(other_target), self.OLD)
        self.assertEqual(self._read(self.target), self.KEY)

    def test_cleanup_failure_does_not_block_another_keys_recovery(self):
        stale_target = "testdir/reckless.aaa"
        stale_old = "testdir/.reckless.aaa" + SAVE_OLD_SUFFIX
        recovered_target = "testdir/reckless.zzz"
        recovered_old = "testdir/.reckless.zzz" + SAVE_OLD_SUFFIX
        self._write(stale_target, self.KEY)
        self._write(stale_old, self.OLD)
        self._write(recovered_old, self.OLD)

        def failing_delete(path):
            if path == stale_old:
                raise OSError("simulated I/O failure")
            return self._real_delete(path)

        platform.secure_delete_file = failing_delete
        try:
            with self.assertRaises(KeyStoreError):
                self.ks.load_files(self.ks.flashpath)
        finally:
            platform.secure_delete_file = self._real_delete

        self.assertEqual(self._read(recovered_target), self.OLD)
        self.assertFalse(platform.file_exists(recovered_old))
        self.assertEqual(self._read(stale_target), self.KEY)


class SDReconcileOnListingTest(TestCase):
    """The same recovery rule applies to the SD card directory: reconcile
    inside the mount, before the card's keys are listed."""

    KEY = b"enc:previous-key"

    def setUp(self):
        clear_testdir()
        platform.maybe_mkdir("testdir")
        platform.maybe_mkdir("testdir/sd")
        self.ks = SDKeyStore()
        self.ks.path = "testdir"
        self.ks.secret = b"\x01" * 32
        self._real_sdpath = SDKeyStore.sdpath
        SDKeyStore.sdpath = property(lambda self: "testdir/sd")
        self.mounts = 0
        self.unmounts = 0
        test = self

        class FakeCard:
            is_present = True

            def mount(self):
                test.mounts += 1

            def unmount(self):
                test.unmounts += 1

            def __enter__(self):
                self.mount()
                return self

            def __exit__(self, *args):
                self.unmount()

        self._real_sdcard = platform.sdcard
        platform.sdcard = FakeCard()
        self._real_menu = sdcard_module.Menu
        sdcard_module.Menu = _FakePrompt
        self.prefix = self.ks.fileprefix(self.ks.sdpath)
        self.target = "%s/%s.mykey" % (self.ks.sdpath, self.prefix)
        self.old = "%s/.%s.mykey%s" % (self.ks.sdpath, self.prefix,
                                       SAVE_OLD_SUFFIX)

    def tearDown(self):
        platform.sdcard = self._real_sdcard
        sdcard_module.Menu = self._real_menu
        SDKeyStore.sdpath = self._real_sdpath
        clear_testdir()

    def test_is_key_saved_recovers_an_interrupted_replacement(self):
        with open(self.old, "wb") as f:
            f.write(self.KEY)

        self.assertTrue(self.ks.is_key_saved)

        self.assertGreaterEqual(self.mounts, 1)
        self.assertEqual(self.mounts, self.unmounts)
        with open(self.target, "rb") as f:
            self.assertEqual(f.read(), self.KEY)
        self.assertFalse(platform.file_exists(self.old))

    def test_is_key_saved_preserves_recovery_error_over_unmount_error(self):
        logs = []
        real_reconcile = self.ks.reconcile_scratch_dir
        real_unmount = platform.sdcard.unmount
        had_print = "print" in sdcard_module.__dict__
        real_print = sdcard_module.__dict__.get("print")

        self.ks.reconcile_scratch_dir = lambda path: (_ for _ in ()).throw(
            OSError("recovery failed")
        )
        platform.sdcard.unmount = lambda: (_ for _ in ()).throw(
            OSError("unmount failed")
        )
        sdcard_module.print = lambda error: logs.append(str(error))
        try:
            self.assertTrue(self.ks.is_key_saved)
        finally:
            self.ks.reconcile_scratch_dir = real_reconcile
            platform.sdcard.unmount = real_unmount
            if had_print:
                sdcard_module.print = real_print
            else:
                del sdcard_module.print

        self.assertEqual(logs, ["unmount failed", "recovery failed"])

    def test_the_card_picker_recovers_an_interrupted_replacement(self):
        with open(self.old, "wb") as f:
            f.write(self.KEY)

        async def cancel_show(screen):
            return None

        self.ks.show = cancel_show
        _run(self.ks.select_file())

        self.assertTrue(platform.file_exists(self.target))
        self.assertFalse(platform.file_exists(self.old))
