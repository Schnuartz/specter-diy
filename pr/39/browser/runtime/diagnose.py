import sys
sys.path.insert(0, '/browser')
sys.path.append('')
print('DIAG_START')
import hashlib
import microur.util.xoshiro256
import microur.util.random_sampler
import microur.util.fountain
import microur.util.ur
import microur.util.bytewords
import microur.encoder
import microur.decoder
import display
print('DIAG_DISPLAY_IMPORTED')
display.init(False)
print('DIAG_DISPLAY_INITIALIZED')
import os
import main
print('DIAG_MAIN_IMPORTED')
rampath = main.platform.mount_sdram()
print('DIAG_SDRAM', rampath)
main.Host.SETTINGS_DIR = main.platform.fpath('/qspi/hosts')
main.Specter.SETTINGS_DIR = main.platform.fpath('/qspi/global')
hosts = [main.USBHost(rampath + '/usb'), main.QRHost(rampath + '/qr'), main.SDHost(rampath + '/sd')]
print('DIAG_HOSTS', len(hosts))
main.BaseApp.TEMPDIR = rampath + '/tmp'
from gui.tcp_gui import TCPGUI
gui = TCPGUI()
print('DIAG_GUI')
main.KeyStore.path = main.platform.fpath('/flash/keystore')
apps = main.load_apps()
print('DIAG_APPS', len(apps))
specter = main.Specter(gui=gui, keystores=[main.MemoryCard, main.SDKeyStore], hosts=hosts,
                       apps=apps, settings_path=main.platform.fpath('/flash'), network='main')
print('DIAG_SPECTER_CREATED')
specter.start()
