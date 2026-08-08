import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase

import rng
from errors import BaseError


class RNGSanityCheckTest(TestCase):
    def setUp(self):
        self.original_get_trng_bytes = rng.get_trng_bytes
        self.original_entropy_pool = rng.entropy_pool

    def tearDown(self):
        rng.get_trng_bytes = self.original_get_trng_bytes
        rng.entropy_pool = self.original_entropy_pool

    def test_looks_dead_ignores_short_repeated_buffers(self):
        self.assertFalse(rng._looks_dead(b""))
        self.assertFalse(rng._looks_dead(b"\x00"))
        self.assertFalse(rng._looks_dead(b"\x00\x00\x00"))
        self.assertFalse(rng._looks_dead(b"\xff\xff\xff"))

    def test_looks_dead_rejects_repeated_buffers_from_four_bytes(self):
        self.assertTrue(rng._looks_dead(b"\x00\x00\x00\x00"))
        self.assertTrue(rng._looks_dead(b"\xff\xff\xff\xff"))
        self.assertTrue(rng._looks_dead(b"\x11" * 32))

    def test_looks_dead_allows_non_repeated_buffers(self):
        self.assertFalse(rng._looks_dead(b"\x00\x00\x00\x01"))
        self.assertFalse(rng._looks_dead(bytes(range(32))))

    def test_get_random_bytes_raises_before_feeding_dead_output(self):
        rng.entropy_pool = b"A" * 64
        rng.get_trng_bytes = lambda nbytes: b"\x00" * nbytes

        try:
            rng.get_random_bytes(32)
        except rng.RNGError:
            pass
        else:
            self.fail("Expected RNGError for repeated TRNG output")

        self.assertEqual(rng.entropy_pool, b"A" * 64)

    def test_get_random_bytes_keeps_one_byte_requests_working(self):
        rng.get_trng_bytes = lambda nbytes: b"\x00" * nbytes
        self.assertEqual(len(rng.get_random_bytes(1)), 1)

    def test_get_random_bytes_returns_requested_length_for_live_output(self):
        rng.get_trng_bytes = lambda nbytes: bytes(range(nbytes))
        self.assertEqual(len(rng.get_random_bytes(32)), 32)

    def test_looks_dead_rejects_partially_stalled_output(self):
        # an intermittently stalling peripheral returns mostly-repeated output
        # with a few live bytes - a plain "all bytes equal" test misses this
        self.assertTrue(rng._looks_dead(b"\x00" * 31 + b"\x2a"))
        self.assertTrue(rng._looks_dead(b"\x00" * 26 + bytes(range(1, 7))))

    def test_looks_dead_allows_healthy_long_buffers(self):
        # distinct byte values saturate at 256, so the threshold must be
        # capped - 245 distinct values in 1000 bytes is healthy TRNG output
        data = bytes(range(245)) + b"\x00" * 755
        self.assertEqual(len(set(data)), 245)
        self.assertFalse(rng._looks_dead(data))

    def test_get_random_bytes_checks_trng_on_the_raw_path(self):
        # requests over 64 bytes return TRNG output directly, without mixing
        # in the entropy pool, so the sanity check is the only defence there
        rng.get_trng_bytes = lambda nbytes: b"\x00" * nbytes
        try:
            rng.get_random_bytes(100)
        except rng.RNGError:
            pass
        else:
            self.fail("Expected RNGError for dead TRNG output above 64 bytes")

    def test_get_random_bytes_returns_raw_trng_above_64_bytes(self):
        rng.get_trng_bytes = lambda nbytes: bytes(range(nbytes))
        self.assertEqual(rng.get_random_bytes(100), bytes(range(100)))

    def test_rng_error_is_a_base_error(self):
        # BaseError subclasses get a readable alert in specter.py instead of
        # an "unexpected error" traceback
        self.assertTrue(issubclass(rng.RNGError, BaseError))
        self.assertEqual(rng.RNGError.NAME, "RNG Error")
