"""SEC1210 protocol bounds against hostile card responses."""
import importlib.util
import os
import sys
import types
import unittest


def load_uscard():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "ports", "esp32p4", "lib", "uscard.py")
    previous = sys.modules.get("machine")
    sys.modules["machine"] = types.ModuleType("machine")
    try:
        spec = importlib.util.spec_from_file_location("p4_uscard_bounds", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if previous is None:
            del sys.modules["machine"]
        else:
            sys.modules["machine"] = previous


class SmartcardBoundsTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_uscard()
        self.connection = self.mod.CardConnection(None)

    def test_t1_wtx_loop_is_bounded(self):
        self.mod._MAX_WTX_BLOCKS = 2
        self.connection._t1_exchange = lambda block, wait: (0xC3, b"\x01")
        with self.assertRaisesRegex(self.mod.CardConnectionException, "WTX"):
            self.connection._transmit_t1(b"\x00" * 5)

    def test_t1_chained_response_has_size_limit(self):
        self.mod._MAX_RESPONSE_BYTES = 3
        responses = iter([(0x20, b"ab"), (0x60, b"cd")])
        self.connection._t1_exchange = lambda block, wait: next(responses)
        with self.assertRaisesRegex(self.mod.CardConnectionException, "response too long"):
            self.connection._transmit_t1(b"\x00" * 5)

    def test_t0_get_response_loop_is_bounded(self):
        self.mod._MAX_EXCHANGE_BLOCKS = 3
        self.connection._reader = types.SimpleNamespace(exchange=lambda data: b"\x61\x01")
        with self.assertRaisesRegex(self.mod.CardConnectionException, "exchange too long"):
            self.connection._transmit_t0(b"\x00" * 4)
