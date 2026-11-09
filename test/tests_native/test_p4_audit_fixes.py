"""Regression checks for the P4 RAM block device."""
import importlib.util
import os
import sys
import types
import unittest


class RAMDeviceBoundsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "ports", "esp32p4", "lib", "sdram.py")
        saved = sys.modules.get("uctypes")
        sys.modules["uctypes"] = types.ModuleType("uctypes")
        try:
            spec = importlib.util.spec_from_file_location("p4_sdram_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.device_class = module.RAMDevice
        finally:
            if saved is None:
                del sys.modules["uctypes"]
            else:
                sys.modules["uctypes"] = saved

    def test_bounds_never_resize_storage(self):
        device = self.device_class(block_size=4, blocks=2)
        device.writeblocks(1, b"test")
        result = bytearray(4)
        device.readblocks(1, result)
        self.assertEqual(result, b"test")
        for block, offset, data in ((-1, 0, b"x"), (2, 0, b"x"),
                                    (1, 3, b"xx"), (0, -1, b"x")):
            with self.subTest(block=block, offset=offset):
                with self.assertRaises(ValueError):
                    device.writeblocks(block, data, offset)
                self.assertEqual(len(device.data), 8)
