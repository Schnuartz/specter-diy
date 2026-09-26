"""Browser replacements for the Unix simulator's TCP hardware transports.

The QR UART consumes decoded scanner bytes placed in /bridge/qr.bin by the
browser shell. No socket or network API is exposed to the wallet runtime.
"""
import os


class _CPU:
    A2 = "A2"
    A4 = "A4"
    G10 = "G10"
    C2 = "C2"
    C5 = "C5"


class Pin:
    IN = 0
    OUT = 1
    cpu = _CPU

    def __init__(self, *args, **kwargs):
        pass

    def on(self):
        # The scanner trigger is active-low. Expose only its transport state
        # to the browser shell; Specter's scan and QR parsing stay unchanged.
        try:
            os.remove("/bridge/scan-active")
        except OSError:
            pass

    def off(self):
        with open("/bridge/scan-active", "wb") as marker:
            marker.write(b"1")


class LED(Pin):
    pass


class UART:
    def __init__(self, name, *args, **kwargs):
        self.path = "/bridge/qr.bin" if name == "YA" else None

    def init(self, *args, **kwargs):
        pass

    def deinit(self):
        pass

    def write(self, data):
        return len(data)

    def any(self):
        if self.path is None:
            return 0
        try:
            return os.stat(self.path)[6]
        except OSError:
            return 0

    def read(self, size=None):
        if not self.any():
            return None
        with open(self.path, "rb") as stream:
            data = stream.read() if size is None else stream.read(size)
            remainder = stream.read() if size is not None else b''
        if remainder:
            with open(self.path, "wb") as stream:
                stream.write(remainder)
        else:
            os.remove(self.path)
        return data


class USB_VCP(UART):
    RTS = 1
    CTS = 2

    def __init__(self, *args, **kwargs):
        super().__init__(None)

    def any(self):
        return False


_usb_mode = None


def usb_mode(value=None):
    global _usb_mode
    if value is not None:
        _usb_mode = value
    return _usb_mode
