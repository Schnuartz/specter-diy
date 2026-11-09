import asyncio
import io
import unittest

from hosts import HostError
from specter import Specter


class SpecterLockTest(unittest.TestCase):
    def test_locked_host_request_is_rejected(self):
        class Keystore:
            is_locked = True

        specter = Specter.__new__(Specter)
        specter.keystore = Keystore()
        with self.assertRaises(HostError):
            asyncio.run(specter.process_host_request(io.BytesIO(b"x")))

    def test_device_lock_disables_every_host(self):
        states = []

        class Keystore:
            def lock(self):
                states.append("locked")

        class Host:
            async def disable(self):
                states.append("disabled")

        specter = Specter.__new__(Specter)
        specter.keystore = Keystore()
        specter.hosts = [Host(), Host()]
        asyncio.run(specter.lock())
        self.assertEqual(states, ["locked", "disabled", "disabled"])
