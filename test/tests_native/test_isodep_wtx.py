"""A lost S(WTX) response must not turn into a different protocol block."""
import time
import unittest
from unittest import mock

from nfc import NFCTimeout
from nfc.isodep import IsoDep


class WaitTimeRetryTest(unittest.TestCase):
    def test_retries_wtx_with_extended_timeout(self):
        class Link:
            reader = object()
        dep = IsoDep(Link())
        dep.active = True
        dep.fwt_ms = 10
        sent = []
        outcomes = iter([b"\xf2\x03", NFCTimeout("lost"), b"\x02\x90\x00"])

        def exchange(frame, timeout):
            sent.append((frame, timeout))
            value = next(outcomes)
            if isinstance(value, Exception):
                raise value
            return value

        dep._frame = exchange
        with mock.patch.object(time, "ticks_ms", lambda: 0, create=True), \
             mock.patch.object(time, "ticks_add", lambda a, b: a + b, create=True), \
             mock.patch.object(time, "ticks_diff", lambda a, b: a - b, create=True):
            self.assertEqual(dep.exchange(b"\x00\x00\x00\x00"), b"\x90\x00")
        self.assertEqual(sent[1:], [(b"\xf2\x03", 30), (b"\xf2\x03", 30)])
