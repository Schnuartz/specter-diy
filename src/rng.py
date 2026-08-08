# random number generator
# if os.urandom is available - entropy goes from hardware TRNG
# in simulator just use /dev/urandom
import hashlib
from errors import BaseError

entropy_pool = b"7" * 64

try:
    from os import urandom as get_trng_bytes
except:

    def get_trng_bytes(nbytes):
        with open("/dev/urandom", "rb") as f:
            return f.read(nbytes)


class RNGError(BaseError):
    """Raised when the hardware TRNG output fails a basic sanity check."""

    NAME = "RNG Error"


def _looks_dead(data):
    """Detect a stalled or failed TRNG.

    rng_get() in the STM32 port returns 0 on timeout (ports/stm32/rng.c) and
    os.urandom() calls it once per byte, so a dead peripheral surfaces as
    all-zero output - or, more generally, as a single repeated byte. A
    peripheral that stalls only intermittently surfaces as mostly-repeated
    output with a few live bytes mixed in, which is why we count distinct
    values rather than only rejecting a fully uniform buffer.

    This is a liveness check, not a proof of health: no cheap check can
    distinguish a healthy TRNG from a subtly biased one. It only catches the
    failure mode where the peripheral stops responding, which is currently
    silent.

    The check is skipped for very small requests, where a repeated byte can
    occur legitimately and is not evidence of failure.
    """
    n = len(data)
    if n < 4:
        return False
    # iterating bytes yields one int per byte, so set() collapses the buffer
    # to the distinct byte values it contains and len() counts them:
    # b"\x00\x00\x00\x00" -> {0} -> 1, bytes(range(8)) -> {0..7} -> 8
    distinct = len(set(data))
    if n < 16:
        # too short for a meaningful distribution - only reject a dead flatline
        return distinct <= 1
    # a healthy TRNG gives ~30 distinct values in 32 bytes, so a buffer far
    # below that is broken hardware, not bad luck: P(<=8 distinct in 32
    # random bytes) is about 2^-121.
    #
    # The cap matters: there are only 256 possible byte values, so distinct
    # saturates near 256 for long buffers while n // 4 keeps growing. Without
    # min(..., 32) the check would reject ~25% of healthy 1000-byte requests
    # (getrandom allows up to 1000) and almost every request above that.
    return distinct < min(n // 4, 32)


# assuming that entropy_pool has some real entropy
# we can generate bytes using it as well
def get_random_bytes(nbytes):
    global entropy_pool
    d = get_trng_bytes(nbytes)
    if _looks_dead(d):
        raise RNGError("TRNG returned no entropy")
    feed(d)
    # if more than 64 - just do trng
    if nbytes > 64:
        return d
    else:
        h = hashlib.sha512(entropy_pool)
        h.update(d)
        return h.digest()[:nbytes]


# we hash together entropy pool and data we got
def feed(data):
    global entropy_pool
    h = hashlib.sha512(entropy_pool)
    h.update(data)
    entropy_pool = h.digest()
