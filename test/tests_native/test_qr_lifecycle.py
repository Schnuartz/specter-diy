"""QR popup and camera cleanup on scanner failures."""
import ast
import asyncio
import os
import types
import unittest
from unittest import mock

from hosts.qr import QRHost


def progress_coroutine():
    # Load the actual GUI method without requiring LVGL on CPython.
    path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "gui", "specter.py")
    with open(path, encoding="utf8") as source:
        tree = ast.parse(source.read())
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "SpecterGUI")
    method = next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "coro")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"asyncio": asyncio}
    exec(compile(module, path, "exec"), namespace)
    return namespace["coro"]


class QRScanLifecycleTest(unittest.TestCase):
    def test_start_failure_closes_popup_and_deinitializes_camera(self):
        async def run():
            class Camera:
                is_camera = True
                stopped = False
                def deinit(self):
                    self.stopped = True

            class Screen:
                waiting = True
                def set_progress(self, value):
                    pass

            class Gui:
                coro = progress_coroutine()
                background = None
                async def show_progress(self, host, title, message):
                    self.background = object()
                    self.task = asyncio.create_task(self.coro(host, Screen()))
                async def close_popup(self):
                    self.background = None

            host = QRHost.__new__(QRHost)
            host.path = "unused"
            host.scanning = False
            host.uart = Camera()
            host.manager = types.SimpleNamespace(gui=Gui())
            async def fail(**kwargs):
                raise OSError("camera unavailable")
            host.scan = fail
            async def sleep_ms(ms):
                await asyncio.sleep(0)
            with mock.patch("hosts.qr.delete_recursively"), \
                 mock.patch.object(asyncio, "sleep_ms", sleep_ms, create=True):
                with self.assertRaisesRegex(OSError, "camera unavailable"):
                    await asyncio.wait_for(host.get_data(), timeout=1)
            self.assertIsNone(host.manager.gui.background)
            self.assertTrue(host.uart.stopped)
            await host.manager.gui.task
        asyncio.run(run())
