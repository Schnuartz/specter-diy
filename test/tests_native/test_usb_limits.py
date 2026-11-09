"""Regression tests for bounded USB requests and incomplete replies."""
import os
import tempfile
import unittest
from unittest import mock

from hosts.usb import USBHost


class FakeUSB:
    def __init__(self, chunks=(), short=False):
        self.chunks = list(chunks)
        self.output = bytearray()
        self.short = short

    def read(self, size):
        return self.chunks.pop(0) if self.chunks else None

    def write(self, data):
        data = bytes(data)
        if self.short:
            return max(0, len(data) - 1)
        self.output.extend(data)
        return len(data)


class USBRequestLimitsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = USBHost(self.temp.name)
        self.host.MAX_REQUEST_SIZE = 8
        self.patch = mock.patch("hosts.usb.platform.delete_recursively", self.delete_contents)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def delete_contents(self, path):
        for name in os.listdir(path):
            os.remove(os.path.join(path, name))

    def test_exact_limit_is_accepted(self):
        self.host.usb = FakeUSB([b"1234", b"5678\n"])
        self.assertIsNone(self.host.read_to_file())
        path = self.host.read_to_file()
        with open(path, "rb") as source:
            self.assertEqual(source.read(), b"12345678")

    def test_oversized_line_is_discarded_until_terminator(self):
        self.host.usb = FakeUSB([b"12345678", b"9", b"command\n", b"ok\n"])
        for _ in range(3):
            self.assertIsNone(self.host.read_to_file())
        self.assertFalse(os.path.exists(os.path.join(self.temp.name, "data")))
        self.assertEqual(self.host.usb.output, b"error: Request too large\r\n")
        path = self.host.read_to_file()
        with open(path, "rb") as source:
            self.assertEqual(source.read(), b"ok")

    def test_oversized_complete_line_is_rejected(self):
        self.host.usb = FakeUSB([b"123456789\n"])
        self.assertIsNone(self.host.read_to_file())
        self.assertIn(b"too large", self.host.usb.output)
        self.assertFalse(os.path.exists(os.path.join(self.temp.name, "data")))

    def test_short_write_is_an_error(self):
        self.host.usb = FakeUSB(short=True)
        with self.assertRaises(OSError):
            self.host.respond(b"success")
