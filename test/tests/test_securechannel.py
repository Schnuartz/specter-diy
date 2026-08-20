from unittest import TestCase
from keystore.javacard.applets.securechannel import SecureChannel, SecureChannelError
import hmac
from ucryptolib import aes

AES_BLOCK = 16
IV_SIZE = 16
AES_CBC = 2
MAC_SIZE = 14


def card_encrypt(sc, data):
    """
    Builds a ciphertext the way the real card would encrypt a response
    to the host, using sc's card_aes_key/card_mac_key/iv - i.e. exactly
    what SecureChannel.decrypt() is expected to be able to open.
    """
    d = data + b"\x80"
    if len(d) % AES_BLOCK != 0:
        d += b"\x00" * (AES_BLOCK - (len(d) % AES_BLOCK))
    iv = sc.iv.to_bytes(IV_SIZE, "big")
    crypto = aes(sc.card_aes_key, AES_CBC, iv)
    ct = crypto.encrypt(d)
    h = hmac.new(sc.card_mac_key, digestmod="sha256")
    h.update(iv)
    h.update(ct)
    ct += h.digest()[:MAC_SIZE]
    return ct


def get_channel():
    sc = SecureChannel(applet=None)
    sc.card_aes_key = b"1" * 32
    sc.card_mac_key = b"2" * 32
    sc.iv = 0
    return sc


class SecureChannelDecryptTest(TestCase):
    """
    Regression tests for the L6 constant-time HMAC check in
    SecureChannel.decrypt(). No smartcard/simulator is needed: decrypt()
    only depends on card_aes_key/card_mac_key/iv, which are set directly.
    """

    def test_valid_mac_decrypts(self):
        sc = get_channel()
        ct = card_encrypt(sc, b"hello from card")
        self.assertEqual(sc.decrypt(ct), b"hello from card")

    def test_first_mac_byte_flipped_rejected(self):
        sc = get_channel()
        ct = bytearray(card_encrypt(sc, b"hello from card"))
        ct[-MAC_SIZE] ^= 0xFF
        with self.assertRaises(SecureChannelError):
            sc.decrypt(bytes(ct))

    def test_middle_mac_byte_flipped_rejected(self):
        sc = get_channel()
        ct = bytearray(card_encrypt(sc, b"hello from card"))
        ct[-(MAC_SIZE // 2)] ^= 0xFF
        with self.assertRaises(SecureChannelError):
            sc.decrypt(bytes(ct))

    def test_last_mac_byte_flipped_rejected(self):
        sc = get_channel()
        ct = bytearray(card_encrypt(sc, b"hello from card"))
        ct[-1] ^= 0xFF
        with self.assertRaises(SecureChannelError):
            sc.decrypt(bytes(ct))

    def test_tampered_ciphertext_rejected(self):
        """A flipped ciphertext byte must be caught by the MAC check,
        never silently decrypted to different plaintext."""
        sc = get_channel()
        ct = bytearray(card_encrypt(sc, b"hello from card"))
        ct[0] ^= 0xFF
        with self.assertRaises(SecureChannelError):
            sc.decrypt(bytes(ct))

    def test_wrong_key_rejected(self):
        sc = get_channel()
        ct = card_encrypt(sc, b"hello from card")
        wrong = get_channel()
        wrong.card_mac_key = b"3" * 32
        with self.assertRaises(SecureChannelError):
            wrong.decrypt(ct)
