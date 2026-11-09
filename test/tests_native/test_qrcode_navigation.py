import importlib.util
import os
import sys
import types
import unittest
from unittest import mock


class QRNavigationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def style():
            return types.SimpleNamespace(body=types.SimpleNamespace(), text=types.SimpleNamespace())

        lv = types.ModuleType("lvgl")
        lv.style_t = style
        lv.color_hex = lambda value: value
        lv.obj = object
        lv.EVENT = types.SimpleNamespace(RELEASED=1)
        lvqr = types.ModuleType("lvqr")
        qr = types.ModuleType("qrcode")
        encoder = types.ModuleType("qrencoder")
        encoder.QREncoder = object
        path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "gui", "components", "qrcode.py")
        spec = importlib.util.spec_from_file_location("qr_navigation_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"lvgl": lv, "lvqr": lvqr,
                                           "qrcode": qr, "qrencoder": encoder}):
            spec.loader.exec_module(module)
        cls.qrcode = module.QRCode

    def test_navigation_waits_until_frames_exist(self):
        widget = types.SimpleNamespace(set_frame=lambda: self.fail("no frame should be shown"))
        for method in (self.qrcode.on_next, self.qrcode.on_prev):
            method(widget, None, 1)
            widget.idx = None
            method(widget, None, 1)
            widget.idx = 0
            widget.frame_num = 0
            method(widget, None, 1)
            del widget.idx
            del widget.frame_num

    def test_navigation_wraps_between_frames(self):
        shown = []
        widget = types.SimpleNamespace(idx=0, frame_num=3, set_frame=lambda: shown.append(widget.idx))
        self.qrcode.on_prev(widget, None, 1)
        self.qrcode.on_next(widget, None, 1)
        self.qrcode.on_next(widget, None, 0)
        self.assertEqual(shown, [2, 0])
