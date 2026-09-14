import sys
sys.path.insert(0, '/browser')
sys.path.append('')
import browser_sd
browser_sd.install()
import platform
print('SD_PROBE_PRESENT', platform.sdcard.is_present)
with platform.sdcard as card:
    with card.open('probe.bin', 'rb') as stream:
        print('SD_PROBE_BYTES', stream.read())
    with card.open('written-by-specter.txt', 'wb') as stream:
        stream.write(b'firmware-created file')
print('SD_PROBE_WRITTEN')
