import asyncio
import os
import tempfile
import unittest
from unittest import mock
import platform

from specter import Specter, SpecterError


class SettingsRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.specter = Specter.__new__(Specter)
        self.specter.SETTINGS_DIR = self.folder.name

        class Keystore:
            settings_key = b"key"

            def load_aead(self, path, key):
                raise ValueError("authentication failed")

        class GUI:
            answer = False

            async def prompt(self, title, message):
                return self.answer

        self.specter.keystore = Keystore()
        self.specter.gui = GUI()
        self.path = self.specter.settings_fname

    def test_missing_settings_use_defaults(self):
        self.assertEqual(asyncio.run(self.specter.load_settings_with_recovery()), {})

    def test_declining_recovery_preserves_file(self):
        with open(self.path, "wb") as stream:
            stream.write(b"original")
        with self.assertRaises(SpecterError):
            asyncio.run(self.specter.load_settings_with_recovery())
        with open(self.path, "rb") as stream:
            self.assertEqual(stream.read(), b"original")

    def test_recovery_preserves_old_file_and_existing_backup(self):
        with open(self.path, "wb") as stream:
            stream.write(b"original")
        with open(self.path + ".unreadable.1", "wb") as stream:
            stream.write(b"older")
        self.specter.gui.answer = True
        self.assertEqual(asyncio.run(self.specter.load_settings_with_recovery()), {})
        with open(self.path + ".unreadable.1", "rb") as stream:
            self.assertEqual(stream.read(), b"older")
        with open(self.path + ".unreadable.2", "rb") as stream:
            self.assertEqual(stream.read(), b"original")
        self.assertFalse(os.path.exists(self.path))

    def test_setup_retries_without_growing_the_call_stack(self):
        calls = []

        class Keystore:
            async def init(self, *args):
                calls.append(None)
                if len(calls) < 1100:
                    raise ValueError("temporary failure")

        async def done(*args):
            pass

        self.specter.keystore = Keystore()
        self.specter.gui.show_screen = lambda: None
        self.specter.gui.show_loader = lambda *args, **kwargs: None
        self.specter.load_network = lambda *args: None
        self.specter.path = ""
        self.specter.network = "main"
        self.specter.handle_exception = done
        self.specter.unlock = done
        self.specter.main = done
        asyncio.run(self.specter.setup())
        self.assertEqual(len(calls), 1100)

    def test_keystore_selection_yields_while_waiting(self):
        calls = []

        class Absent:
            @classmethod
            def is_available(cls):
                return False

        class EventuallyPresent:
            @classmethod
            def is_available(cls):
                return bool(calls)

        async def sleep(ms):
            calls.append(ms)

        self.specter.keystores = [Absent, EventuallyPresent]
        with mock.patch.object(asyncio, "sleep_ms", sleep, create=True):
            asyncio.run(self.specter.select_keystore())
        self.assertEqual(calls, [50])
        self.assertIsInstance(self.specter.keystore, EventuallyPresent)

    def test_failed_sd_mount_releases_power_and_led(self):
        class Device:
            def __init__(self):
                self.power_calls = []

            def present(self):
                return True

            def power(self, value):
                self.power_calls.append(value)

        class LED:
            def __init__(self):
                self.on_state = False

            def on(self):
                self.on_state = True

            def off(self):
                self.on_state = False

        device, led = Device(), LED()
        card = platform.SDCard(device, led)
        with mock.patch.object(platform.os, "mount", side_effect=OSError("failed"), create=True):
            with self.assertRaises(OSError):
                card.mount()
        self.assertEqual(device.power_calls, [True, False])
        self.assertFalse(led.on_state)
        self.assertFalse(card._mounted)
