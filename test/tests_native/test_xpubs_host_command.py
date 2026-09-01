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
        stream = BytesIO(b"xpub m/84h/1h/0h")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertIsNotNone(res)
        result_stream, meta = res
        expected = self.keystore.get_xpub("m/84h/1h/0h").to_base58(
            NETWORKS["test"]["xpub"]
        )
        self.assertEqual(result_stream.read().decode(), expected)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], Prompt)

    def test_xpub_rejected_returns_no_xpub(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/1h/0h")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertFalse(res)
        self.assertEqual(len(seen), 1)

    def test_confirmation_shows_requested_path_first_case(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/1h/0h")
        self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        self.assertIn("m/84h/1h/0h", message)

    def test_confirmation_shows_requested_path_second_case(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/48h/1h/7h/2h")
        self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        self.assertIn("m/48h/1h/7h/2h", message)
        self.assertNotIn("m/84h/1h/0h", message)

    def test_confirmation_shows_actual_xpub_value(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/1h/0h")
        self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        expected = self.keystore.get_xpub("m/84h/1h/0h").to_base58(
            NETWORKS["test"]["xpub"]
        )
        self.assertIn(expected, message)

    def test_fingerprint_returns_immediately_without_confirmation(self):
        # fingerprint is used for non-interactive device discovery/
        # identification by companion software and must never prompt
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"fingerprint")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertIsNotNone(res)
        result_stream, meta = res
        expected = hexlify(self.keystore.fingerprint).decode()
        self.assertEqual(result_stream.read().decode(), expected)
        self.assertEqual(seen, [])

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

    # self.app is on "test" (coin type 1'): a standard-purpose path with
    # mainnet's coin type (0') must be refused outright - never answered
    # with the normal "Share Xpub?" Confirm/Cancel - and the host must get
    # a machine-parseable reason back, not a silently-derived key.
    @staticmethod
    def _is_mismatch_screen(scr):
        title = scr.args[0] if scr.args else scr.kwargs.get("title", "")
        return "Host tried to get access" in title

    def test_xpub_wrong_network_shows_the_refusal_screen_not_the_share_prompt(self):
        show_screen, seen = self._show_screen(False)  # tap "OK"
        stream = BytesIO(b"xpub m/84h/0h/0h")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(len(seen), 1)
        self.assertTrue(self._is_mismatch_screen(seen[0]))

    def test_xpub_wrong_network_error_names_the_active_network(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        try:
            self._run(self.app.process_host_command(stream, show_screen))
            self.fail("expected an exception")
        except Exception as e:
            self.assertIn("test", str(e))

    def test_xpub_wrong_network_screen_shows_path_and_active_network(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/48h/0h/7h/2h")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        self.assertIn("m/48h/0h/7h/2h", message)
        self.assertIn(NETWORKS["test"]["name"], message)

    def test_xpub_wrong_network_screen_names_the_target_network(self):
        # app is on "test"; a mainnet-coin-type request should be told to
        # switch to Mainnet, by name
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        shown = seen[0]
        message = shown.args[1] if len(shown.args) > 1 else shown.kwargs.get("message")
        self.assertIn("Mainnet", message)

    def test_xpub_wrong_network_offers_a_network_settings_button(self):
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/84h/0h/0h")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(seen[0].kwargs.get("confirm_text"), "Network settings")

    def test_xpub_wrong_network_button_opens_the_network_picker(self):
        sent = []

        async def rec_communicate(stream, app=None, **kw):
            sent.append((stream.read(), app))
            return None

        self.app.communicate = rec_communicate
        show_screen, seen = self._show_screen(True)  # tap "Network settings"
        stream = BytesIO(b"xpub m/84h/0h/0h")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(sent, [(b"select_network", "")])

    def test_xpub_wrong_network_never_reaches_derivation_confirm(self):
        # dismissing with "OK" must not fall through to the normal prompt
        show_screen, seen = self._show_screen(False)
        stream = BytesIO(b"xpub m/49h/0h/0h")
        with self.assertRaises(Exception):
            self._run(self.app.process_host_command(stream, show_screen))
        self.assertEqual(len(seen), 1)

    def test_xpub_matching_network_still_uses_normal_prompt(self):
        # same standard purpose, but the coin type matches the app's
        # active "test" network - normal Confirm/Cancel flow, unaffected
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"xpub m/84h/1h/0h")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertIsNotNone(res)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], Prompt)

    def test_xpub_non_standard_purpose_is_never_network_checked(self):
        # a non-standard/custom purpose carries no implied network
        # semantics and must go through the normal prompt regardless of
        # its second component
        show_screen, seen = self._show_screen(True)
        stream = BytesIO(b"xpub m/1234h/0h/0h")
        res = self._run(self.app.process_host_command(stream, show_screen))
        self.assertIsNotNone(res)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], Prompt)
