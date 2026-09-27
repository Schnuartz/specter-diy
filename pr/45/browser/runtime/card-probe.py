"""Cross-browser-transport APDU test using Specter's real MemoryCard applet."""
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
import display
display.init(False)
from keystore.javacard.util import get_connection
from keystore.javacard.applets.memorycard import MemoryCardApplet, SecureError
from keystore.memorycard import MemoryCard
connection = get_connection()
print('CARD_PROBE_READY')
while not connection.isCardInserted():
    display.update(30)
assert MemoryCard.is_available()
connection.connect(connection.T1_protocol)
applet = MemoryCardApplet(connection)
applet.select()
applet.open_secure_channel()
applet.ping()
assert not applet.is_pin_set
applet.set_pin('1234')
assert applet.is_pin_set
applet.save_secret(b'test card secret')
assert applet.get_secret() == b'test card secret'
applet.lock()
try:
    applet.unlock('bad')
    raise AssertionError('Wrong PIN accepted')
except SecureError as error:
    assert str(error) == '0502'
assert applet.pin_attempts_left == 9
applet.unlock('1234')
assert applet.get_secret() == b'test card secret'
print('CARD_PROBE_SLOT1_PASS')
while connection._active() != 2:
    display.update(30)
assert connection.isCardInserted()
connection.connect(connection.T1_protocol)
applet = MemoryCardApplet(connection)
applet.select()
applet.open_secure_channel()
assert not applet.is_pin_set
assert applet.get_secret() == b''
applet.set_pin('5678')
applet.save_secret(b'separate slot two')
print('CARD_PROBE_SLOT2_PASS')
while connection._active() != 1:
    display.update(30)
assert connection.isCardInserted()
connection.connect(connection.T1_protocol)
applet = MemoryCardApplet(connection)
applet.select()
applet.open_secure_channel()
assert applet.is_pin_set
applet.unlock('1234')
assert applet.get_secret() == b'test card secret'
old_identity = connection._state('private.key')
print('CARD_PROBE_SLOT1_RETURN_PASS')
while connection._state('private.key') == old_identity:
    display.update(30)
assert connection.isCardInserted()
connection.connect(connection.T1_protocol)
applet = MemoryCardApplet(connection)
applet.select()
applet.open_secure_channel()
assert not applet.is_pin_set
assert applet.get_secret() == b''
print('CARD_PROBE_PASS')
