"""Session-local MemoryCard APDU transport for Specter's unmodified JavaCard host.

The command and secure-channel format follow cryptoadvance/specter-javacard's
MemoryCardApplet. Card state lives in separate /state/cards/<slot> files; the
browser alone controls which slot is in the reader. This is simulation, not a
secure element, and must never hold real secrets.
"""
import hashlib
import hmac
import secp256k1
from ucryptolib import aes
from rng import get_random_bytes


class SmartcardException(Exception):
    pass


class CardConnectionException(SmartcardException):
    pass


class NoCardException(SmartcardException):
    pass


def _read(path, default=b''):
    try:
        with open(path, 'rb') as stream:
            return stream.read()
    except OSError:
        return default


def _write(path, data):
    with open(path, 'wb') as stream:
        stream.write(data)


def _sha(data):
    return hashlib.sha256(data).digest()


def _mac(key, data):
    return hmac.new(key, data, digestmod='sha256').digest()[:14]


def _pad(data):
    data += b'\x80'
    return data + b'\x00' * ((-len(data)) % 16)


def _unpad(data):
    end = data.rfind(b'\x80')
    if end < 0 or any(data[end + 1:]):
        raise CardConnectionException('Invalid secure-channel padding')
    return data[:end]


class CardConnection:
    T1_protocol = 1
    T0_protocol = 2
    AID = b'\xb0\x0b\x51\x11\xcb\x01'

    def __init__(self):
        self.slot = None
        self.identity = None
        self.selected = False
        self.channel = False
        self.iv = 0
        self.unlocked = False

    def _active(self):
        marker = _read('/bridge/card-slot')
        if len(marker) != 1 or marker[0] not in (1, 2, 3):
            return None
        return marker[0]

    def isCardInserted(self):
        active = self._active()
        identity = _read('/state/cards/%d/private.key' % active) if active else None
        if active != self.slot or identity != self.identity:
            self.slot = active
            self.identity = identity
            self.selected = False
            self.channel = False
            self.unlocked = False
        return active is not None

    def connect(self, protocol):
        if not self.isCardInserted():
            raise NoCardException('No virtual card inserted')
        if protocol not in (self.T1_protocol, self.T0_protocol):
            raise CardConnectionException('Unsupported protocol')
        self.protocol = protocol

    def disconnect(self):
        self.channel = False
        self.unlocked = False

    def getATR(self):
        return b'Specter browser MemoryCard simulator'

    @property
    def path(self):
        return '/state/cards/%d/' % self.slot

    def _state(self, name, default=b''):
        return _read(self.path + name, default)

    def _save(self, name, data):
        _write(self.path + name, data)

    def _private_key(self):
        secret = self._state('private.key')
        if len(secret) != 32 or not secp256k1.ec_seckey_verify(secret):
            raise CardConnectionException('Virtual card key missing or invalid')
        return secret

    def _public_key(self):
        pub = secp256k1.ec_pubkey_create(self._private_key())
        return secp256k1.ec_pubkey_serialize(pub, secp256k1.EC_UNCOMPRESSED)

    def _open_channel(self, host_pub):
        if len(host_pub) != 65:
            raise CardConnectionException('Invalid host public key')
        pub = secp256k1.ec_pubkey_parse(host_pub)
        secp256k1.ec_pubkey_tweak_mul(pub, self._private_key())
        x = secp256k1.ec_pubkey_serialize(pub, secp256k1.EC_UNCOMPRESSED)[1:33]
        nonce = get_random_bytes(32)
        secret = _sha(x + nonce)
        self.host_aes = _sha(b'host_aes' + secret)
        self.card_aes = _sha(b'card_aes' + secret)
        self.host_mac = _sha(b'host_mac' + secret)
        self.card_mac = _sha(b'card_mac' + secret)
        self.iv = 0
        self.channel = True
        self.unlocked = False
        tag = _mac(self.card_mac, nonce)
        signature = secp256k1.ecdsa_sign(_sha(nonce + tag), self._private_key())
        return nonce + tag + secp256k1.ecdsa_signature_serialize_der(signature)

    def _pin_status(self):
        pin = self._state('pin.bin')
        attempts = self._state('attempts', b'\x0a')[0]
        status = 0 if not pin else 3 if attempts == 0 else 2 if self.unlocked else 1
        return bytes((attempts, 10, status))

    def _pin_check(self, digest):
        pin = self._state('pin.bin')
        if not pin or not self._state('attempts', b'\x0a')[0]:
            return False
        if digest == pin:
            self.unlocked = True
            self._save('attempts', b'\x0a')
            return True
        self.unlocked = False
        attempts = self._state('attempts', b'\x0a')[0] - 1
        self._save('attempts', bytes((attempts,)))
        return False

    def _command(self, plain):
        if len(plain) < 2:
            return b'\x04\x03'
        cmd, sub = plain[:2]
        data = plain[2:]
        if cmd == 0 and sub == 0:
            return b'\x90\x00' + data
        if cmd == 1 and sub == 0:
            return b'\x90\x00' + get_random_bytes(32)
        if cmd == 3:
            if sub == 0:
                return b'\x90\x00' + self._pin_status()
            if sub == 4:
                if self._state('pin.bin'):
                    return b'\x05\x06'
                if len(data) != 32:
                    return b'\x04\x03'
                self._save('pin.bin', data)
                self._save('attempts', b'\x0a')
                self.unlocked = True
                return b'\x90\x00'
            if sub == 1:
                if not self._state('pin.bin'):
                    return b'\x05\x05'
                if not self._state('attempts', b'\x0a')[0]:
                    return b'\x05\x03'
                if self._pin_check(data):
                    return b'\x90\x00'
                return b'\x05\x03' if not self._state('attempts')[0] else b'\x05\x02'
            if sub == 2:
                self.unlocked = False
                return b'\x90\x00'
            if sub == 3:
                if len(data) != 66 or data[0] != 32 or data[33] != 32:
                    return b'\x04\x03'
                if not self._pin_check(data[1:33]):
                    return b'\x05\x02'
                self._save('pin.bin', data[34:66])
                return b'\x90\x00'
            return b'\x04\x05'
        if cmd == 5:
            if self._state('pin.bin') and not self.unlocked:
                return b'\x05\x01'
            if sub == 0:
                return b'\x90\x00' + self._state('secret.bin')
            if sub == 1:
                if len(data) > 255:
                    return b'\x04\x03'
                self._save('secret.bin', data)
                return b'\x90\x00' + data
            return b'\x04\x05'
        return b'\x04\x04'

    def _secure_message(self, message):
        if not self.channel or len(message) < 30 or (len(message) - 14) % 16:
            raise CardConnectionException('Secure channel unavailable')
        iv = self.iv.to_bytes(16, 'big')
        cipher, tag = message[:-14], message[-14:]
        if _mac(self.host_mac, iv + cipher) != tag:
            self.channel = False
            self.unlocked = False
            raise CardConnectionException('Secure-channel MAC mismatch')
        plain = _unpad(aes(self.host_aes, 2, iv).decrypt(cipher))
        response = self._command(plain)
        encrypted = aes(self.card_aes, 2, iv).encrypt(_pad(response))
        self.iv += 1
        return encrypted + _mac(self.card_mac, iv + encrypted)

    def transmit(self, apdu):
        if not self.isCardInserted():
            raise NoCardException('No virtual card inserted')
        apdu = bytes(apdu)
        if len(apdu) < 4:
            return b'\x67\x00'
        if apdu[:4] == b'\x00\xa4\x04\x00':
            self.selected = len(apdu) >= 5 and apdu[4] == 6 and apdu[5:11] == self.AID
            self.channel = False
            self.unlocked = False
            return b'\x90\x00' if self.selected else b'\x6a\x82'
        if not self.selected:
            return b'\x69\x99'
        if apdu[0] != 0xb0:
            return b'\x6e\x00'
        ins = apdu[1]
        data = apdu[5:5 + apdu[4]] if len(apdu) >= 5 else b''
        try:
            if ins == 0xb2:
                response = self._public_key()
            elif ins == 0xb4:
                response = self._open_channel(data)
            elif ins == 0xb6:
                response = self._secure_message(data)
            elif ins == 0xb7:
                self.channel = False
                self.unlocked = False
                response = b''
            elif ins == 0xb1:
                response = get_random_bytes(32)
            else:
                return b'\x6d\x00'
            return response + b'\x90\x00'
        except Exception as exc:
            print('Virtual card APDU error:', exc)
            return b'\x6f\x00'


class Reader:
    def __init__(self, *args, **kwargs):
        pass

    def createConnection(self):
        return CardConnection()
