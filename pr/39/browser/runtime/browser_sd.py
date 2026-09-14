"""Session-only removable-card presence at Specter's platform.SDCard seam."""
import os
import platform


class BrowserSDCard(platform.SDCard):
    @property
    def is_present(self):
        try:
            os.stat('/bridge/sd-inserted')
            return True
        except OSError:
            return False


def install():
    platform.sdcard = BrowserSDCard()
    platform.build_type = 'browser-wasm'
