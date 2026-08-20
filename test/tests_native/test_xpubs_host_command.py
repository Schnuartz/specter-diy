import sys

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase
from unittest.mock import patch, PropertyMock
from io import BytesIO
from binascii import hexlify
import gc

from tests.util import get_keystore, get_xpubs_app, clear_testdir
from embit import bip32
from embit.liquid.networks import NETWORKS
from gui.screens import Prompt


class XpubsHostCommandTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore()
        self.app = get_xpubs_app(self.keystore, "test")

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def _show_screen(self, response):
        seen = []

        async def show_screen(scr):
            seen.append(scr)
            return response

        return show_screen, seen

    def _run(self, coro):
        try:
            coro.send(None)
        except StopIteration as e:
            return e.value
        raise AssertionError("coroutine did not complete in one step")

    def test_locked_device_rejects_xpub_request(self):
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        with patch.object(
            type(self.keystore), "is_locked", new_callable=PropertyMock, return_value=True
        ):
            with self.assertRaises(Exception):
                self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(seen, [])

    def test_locked_device_rejects_fingerprint_request(self):
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"fingerprint")
        with patch.object(
            type(self.keystore), "is_locked", new_callable=PropertyMock, return_value=True
        ):
            with self.assertRaises(Exception):
                self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(seen, [])

    def test_xpub_approved_matches_direct_derivation(self):
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertIsNotNone(res)
        result_stream, meta = res
        expected = self.keystore.get_xpub("m/84h/0h/0h").to_base58(
            NETWORKS["test"]["xpub"]
        )
        self.assertEqual(result_stream.read().decode(), expected)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], Prompt)

    def test_xpub_rejected_returns_no_xpub(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertFalse(res)
        self.assertEqual(len(seen), 1)

    def test_confirmation_shows_requested_path_first_case(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        self.assertIn("m/84h/0h/0h", message)

    def test_confirmation_shows_requested_path_second_case(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/48h/0h/7h/2h")
        self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        self.assertIn("m/48h/0h/7h/2h", message)
        self.assertNotIn("m/84h/0h/0h", message)

    def test_confirmation_shows_actual_xpub_value(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        expected = self.keystore.get_xpub("m/84h/0h/0h").to_base58(
            NETWORKS["test"]["xpub"]
        )
        self.assertIn(expected, message)

    def test_fingerprint_approved_matches_direct_value(self):
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"fingerprint")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertIsNotNone(res)
        result_stream, meta = res
        expected = hexlify(self.keystore.fingerprint).decode()
        self.assertEqual(result_stream.read().decode(), expected)
        self.assertEqual(len(seen), 1)

    def test_fingerprint_rejected_returns_no_fingerprint(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"fingerprint")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertFalse(res)
        self.assertEqual(len(seen), 1)

    def test_invalid_derivation_fails_before_confirmation(self):
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"xpub garbage")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(seen, [])

    def test_empty_path_fails_before_confirmation(self):
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"xpub ")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(seen, [])
