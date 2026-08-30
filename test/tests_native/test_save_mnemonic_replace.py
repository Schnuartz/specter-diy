"""
Regression tests: saving over an existing key file must destroy the old
file first.

save_aead() opens the path "wb". On FAT that truncates the file, which
frees its cluster chain without overwriting it, and the new contents are
then usually written elsewhere. The previous encrypted mnemonic therefore
stays readable in free space - and a later secure delete of the current
file cannot reach it any more, while enc_secret, which decrypts it, is not
rotated by a delete either.

save_mnemonic() now overwrites the file it is about to replace, and
refuses the save if that overwrite fails.
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
from keystore.flash import FlashKeyStore
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

        async def get_input(*args, **kwargs):
            return "mykey"

        async def show(*args, **kwargs):
            return True

        async def load_mnemonic(path, *args, **kwargs):
            return None

        def save_aead(path, adata=b"", plaintext=b"", key=None):
            self.calls.append(("save_aead", path))
            with open(path, "wb") as f:
                f.write(b"new-encrypted-key-material")

        self.ks.get_input = get_input
        self.ks.show = show
        self.ks.load_mnemonic = load_mnemonic
        self.ks.save_aead = save_aead

        self._real_prompts = (flash_module.Prompt, sdcard_module.Prompt)
        flash_module.Prompt = _FakePrompt
        sdcard_module.Prompt = _FakePrompt

        self._real_delete = platform.secure_delete_file

        def recording_delete(path):
            self.calls.append(("secure_delete", path))
            return self._real_delete(path)

        platform.secure_delete_file = recording_delete

    def tearDown(self):
        flash_module.Prompt, sdcard_module.Prompt = self._real_prompts
        platform.secure_delete_file = self._real_delete
        clear_testdir()

    def _make_keystore(self):
        return self.keystore_cls()

    def _write_existing(self):
        with open(self.target, "wb") as f:
            f.write(b"old-encrypted-key-material")

    def test_existing_file_is_overwritten_before_it_is_replaced(self):
        self._write_existing()

        _run(self.ks.save_mnemonic())

        self.assertEqual(
            self.calls,
            [("secure_delete", self.target), ("save_aead", self.target)],
        )
        with open(self.target, "rb") as f:
            self.assertEqual(f.read(), b"new-encrypted-key-material")

    def test_a_failed_overwrite_aborts_the_save(self):
        """If the old file cannot be destroyed, writing the new one over it
        would free the old clusters unoverwritten - exactly what this
        prevents. Nothing is written in that case."""
        self._write_existing()

        def failing_delete(path):
            self.calls.append(("secure_delete", path))
            raise OSError("simulated I/O failure")

        platform.secure_delete_file = failing_delete

        with self.assertRaises(KeyStoreError):
            _run(self.ks.save_mnemonic())

        self.assertEqual(self.calls, [("secure_delete", self.target)])
        with open(self.target, "rb") as f:
            self.assertEqual(f.read(), b"old-encrypted-key-material")

    def test_a_new_file_is_not_deleted_first(self):
        self.assertFalse(platform.file_exists(self.target))

        _run(self.ks.save_mnemonic())

        self.assertEqual(self.calls, [("save_aead", self.target)])


class FlashSaveMnemonicTest(_SaveMnemonicBase):
    keystore_cls = FlashKeyStore


class SDSaveMnemonicTest(_SaveMnemonicBase):
    """SDKeyStore.save_mnemonic() has its own copy of the overwrite prompt,
    with the SD unmount bookkeeping around it - the same guarantee has to
    hold there. The target here is the internal-flash path, so the card
    handling is not involved."""

    keystore_cls = SDKeyStore

    def setUp(self):
        super().setUp()

        async def get_keypath(*args, **kwargs):
            return self.ks.flashpath

        self.ks.get_keypath = get_keypath
