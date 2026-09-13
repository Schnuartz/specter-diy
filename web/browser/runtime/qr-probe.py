"""CI probe across the browser QR transport and Specter's QRHost parser."""
import sys
sys.path.insert(0, '/browser')
sys.path.append('')
import hashlib
import microur.util.xoshiro256
import microur.util.random_sampler
import microur.util.fountain
import microur.util.ur
import microur.util.bytewords
import microur.encoder
import microur.decoder
import os
import display
display.init(False)
os.mkdir('/state/qrprobe')
from hosts.qr import QRHost
from microur.decoder import FileURDecoder
host = QRHost('/state/qrprobe')
host.scanning = True
host.animated = False
host.raw = True
host.chunk_timeout = 0.01
host.decoder = FileURDecoder(host.path)
host.bcur = False
host.bcur2 = False
with open(host.tmpfile, 'wb'):
    pass
host._start_scanner()
print('QR_PROBE_READY')
import asyncio
while host.scanning:
    display.update(30)
    if host.uart.any():
        asyncio.run(host.update())
with open('/state/qrprobe/data.txt', 'rb') as stream:
    import binascii
    print('QR_PROBE_HEX', binascii.hexlify(stream.read()).decode())
