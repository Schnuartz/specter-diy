"""
Pure logic for the SeedQR transcription viewer: canonical QR module matrix
generation, zone/section geometry, and transcription verification.

Deliberately free of any lvgl / hardware dependency (only ``math`` at import
time; ``qrcode`` -- the native usermod -- is imported lazily inside
``generate_matrix`` only) so this module can be unit-tested on a desktop
Python interpreter without a display or firmware build.

Section geometry mirrors SeedSigner's SeedQR transcription workflow
(https://github.com/SeedSigner/seedsigner/blob/dev/docs/seed_qr/README.md):
a 21x21 QR is grouped into 7x7-module sections (a 3x3 section grid); every
larger SeedQR size is grouped into 5x5-module sections.

Verification / hardware limitation
-----------------------------------
Specter DIY's QR scanner hardware (see ``hosts/qr.py``: a GM65 or M3Y
barcode-scanner module talking over UART) only ever exposes the *decoded
payload* of a scanned code. It does not expose the raw camera image or the
detected module grid, so there is no way to compare the physically drawn
QR against the expected one module-by-module -- only whether it decodes to
the right content.

Because QR codes carry error correction, "decodes to the right content" is
a strictly weaker guarantee than "every module was drawn correctly": a
transcription with a handful of wrong modules can still decode perfectly.
``verify_scanned_payload`` below reflects exactly this weaker guarantee and
nothing more -- it must never be presented to the user as proof that the
drawing itself is pixel-perfect.

``compare_matrices``/``mismatch_stats``/``matrix_diff``/``diff_grid`` *do*
implement true module-level comparison, so that a real diff view can be
wired up the moment scanner hardware/firmware exposes raw module data.
Today nothing calls them with a physically-scanned matrix -- doing so would
require regenerating a matrix from the decoded payload, which would just
reproduce the *expected* matrix and silently hide any drawing error that
error correction happened to paper over. They are exercised only with
directly-supplied matrices (e.g. in tests, or a future raw-capable
scanner).
"""
import math

# Row letters / column numbers used for section labels, e.g. "B-3".
ROW_LABELS = "ABCDEF"
COL_LABELS = "123456"

# Module values. Real QR modules are 0 (white) or 1 (black/dark). OUTSIDE
# marks a cell that lies past the edge of the real QR matrix -- present only
# in the partial final row/column of sections on non-square-divisible sizes
# (e.g. the 29x29 Standard 24-word SeedQR) -- so callers never confuse "no
# module here" with a valid white module.
MODULE_WHITE = 0
MODULE_BLACK = 1
MODULE_OUTSIDE = 2


class MatrixError(ValueError):
    """Raised when a QR payload can't be turned into a valid module matrix."""


def generate_matrix(payload):
    """
    Build the canonical QR module matrix for a SeedQR payload.

    ``payload`` must be either:
      - ``str``: Standard SeedQR digit string (numeric QR mode)
      - ``bytes``: Compact SeedQR raw entropy (binary QR mode)

    Returns a tuple of ``bytes`` rows (one element per module, 0 or 1). This
    is the single source of truth for both the overview and every zoomed
    section: it must never be regenerated separately for those two views.
    """
    import qrcode  # native firmware module (qrcodegen-backed)

    raw = qrcode.encode_to_string(payload)
    return parse_matrix(raw)


def parse_matrix(raw):
    """
    Parse the newline-separated '0'/'1' grid produced by
    ``qrcode.encode_to_string`` into a tuple of ``bytes`` rows.
    """
    rows = raw.strip("\n").split("\n")
    matrix = tuple(bytes(1 if c == "1" else 0 for c in row) for row in rows)
    validate_matrix(matrix)
    return matrix


def validate_matrix(matrix):
    """
    Raise MatrixError if ``matrix`` isn't a square, standards-compliant QR
    module grid (size = 21 + 4*(version-1), version 1..40) containing only
    real MODULE_WHITE/MODULE_BLACK values. Returns the size.
    """
    size = len(matrix)
    if size == 0:
        raise MatrixError("Empty QR matrix")
    for row in matrix:
        if len(row) != size:
            raise MatrixError("QR matrix must be square")
        for value in row:
            if value not in (MODULE_WHITE, MODULE_BLACK):
                raise MatrixError("Invalid module value: %r" % (value,))
    if size < 21 or size > 177 or (size - 21) % 4 != 0:
        raise MatrixError("Not a valid QR module count: %d" % size)
    return size


# Module-level diff classification. MISMATCH_MISSING/MISMATCH_EXTRA are also
# used as compare_matrices() mismatch kinds; DIFF_CORRECT_* only appear in
# diff_grid()'s full-grid output.
MISMATCH_MISSING = "missing"  # expected BLACK, observed WHITE
MISMATCH_EXTRA = "extra"      # expected WHITE, observed BLACK
DIFF_CORRECT_BLACK = "correct_black"
DIFF_CORRECT_WHITE = "correct_white"


def compare_matrices(expected, observed):
    """
    Compare two same-size canonical QR module matrices (as returned by
    generate_matrix/parse_matrix) module-by-module.

    Returns a tuple of ``(y, x, kind)`` for every module that differs,
    where ``kind`` is MISMATCH_MISSING (expected BLACK, observed WHITE --
    something the user was supposed to draw is missing) or MISMATCH_EXTRA
    (expected WHITE, observed BLACK -- an extra mark that shouldn't be
    there).

    Raises MatrixError if either matrix is invalid, or if they aren't the
    same size (there's nothing meaningful to compare module-by-module
    otherwise).
    """
    validate_matrix(expected)
    validate_matrix(observed)
    if len(expected) != len(observed):
        raise MatrixError(
            "Matrix size mismatch: expected %dx%d, observed %dx%d"
            % (len(expected), len(expected), len(observed), len(observed))
        )
    mismatches = []
    for y, (erow, orow) in enumerate(zip(expected, observed)):
        for x, (e, o) in enumerate(zip(erow, orow)):
            if e == o:
                continue
            kind = MISMATCH_MISSING if e == MODULE_BLACK else MISMATCH_EXTRA
            mismatches.append((y, x, kind))
    return tuple(mismatches)


def mismatch_stats(mismatches, size):
    """
    Summarize a compare_matrices() result for a ``size`` x ``size`` matrix:
    mismatch count, total module count, and mismatch percentage
    (0.0-100.0), e.g. for a "7 of 841 modules differ (0.83%)" message.
    """
    total = size * size
    count = len(mismatches)
    percent = (count * 100.0 / total) if total else 0.0
    return {"count": count, "total": total, "percent": percent}


def matrix_diff(expected, observed):
    """Convenience wrapper: compare_matrices() + mismatch_stats() in one call."""
    mismatches = compare_matrices(expected, observed)
    stats = mismatch_stats(mismatches, len(expected))
    return mismatches, stats


def diff_grid(expected, observed):
    """
    Build a full ``size`` x ``size`` grid classifying every module for
    rendering an error-diff view: each cell is one of DIFF_CORRECT_BLACK,
    DIFF_CORRECT_WHITE, MISMATCH_MISSING, or MISMATCH_EXTRA. Pure data --
    no lvgl -- a GUI layer decides how to color each class.

    Raises the same errors as compare_matrices().
    """
    validate_matrix(expected)
    validate_matrix(observed)
    if len(expected) != len(observed):
        raise MatrixError(
            "Matrix size mismatch: expected %dx%d, observed %dx%d"
            % (len(expected), len(expected), len(observed), len(observed))
        )
    rows = []
    for erow, orow in zip(expected, observed):
        row = []
        for e, o in zip(erow, orow):
            if e == MODULE_BLACK:
                row.append(DIFF_CORRECT_BLACK if o == MODULE_BLACK else MISMATCH_MISSING)
            else:
                row.append(DIFF_CORRECT_WHITE if o == MODULE_WHITE else MISMATCH_EXTRA)
        rows.append(tuple(row))
    return tuple(rows)


# Outcomes of verify_scanned_payload(): the only three states the current
# (payload-only) scanner hardware can truthfully distinguish.
VERIFY_MATCH = "match"
VERIFY_MISMATCH = "mismatch"
VERIFY_UNREADABLE = "unreadable"


def verify_payload(expected_payload, scanned_bytes):
    """
    True if ``scanned_bytes`` -- the raw bytes read back from the QR
    scanner -- represent the same SeedQR payload as ``expected_payload``
    (the same value that was passed to generate_matrix()).

    ``expected_payload`` is either a Standard SeedQR digit string (str) or
    a Compact SeedQR's raw entropy (bytes); ``scanned_bytes`` is always
    bytes. A Standard payload is matched by decoding ``scanned_bytes`` as
    text; a Compact payload is matched against the raw bytes directly --
    it is never hex-encoded or otherwise string-converted, so a Compact
    SeedQR's binary payload can never be confused with a Standard one.
    """
    if isinstance(expected_payload, bytes):
        return scanned_bytes == expected_payload
    if isinstance(expected_payload, str):
        if not scanned_bytes:
            return False
        try:
            decoded = bytes(scanned_bytes).decode()
        except UnicodeError:
            return False
        return decoded == expected_payload
    raise TypeError("expected_payload must be str or bytes")


def verify_scanned_payload(expected_payload, scanned_bytes):
    """
    Classify a scan of a manually-copied SeedQR against the expected
    payload, using only what the scanner hardware exposes (see the module
    docstring): the decoded payload, nothing about individual modules.

    Returns one of:
      - VERIFY_MATCH: the scan decoded to exactly the expected payload.
        This proves the copy is *readable and correct* -- it does NOT
        prove every module was drawn identically; error correction can
        mask drawing mistakes.
      - VERIFY_MISMATCH: the scanner decoded something, but it doesn't
        match -- a hard failure, this backup must not be trusted.
      - VERIFY_UNREADABLE: the scanner produced no data at all (nothing
        decoded, or the user cancelled the scan).
    """
    if not scanned_bytes:
        return VERIFY_UNREADABLE
    if verify_payload(expected_payload, scanned_bytes):
        return VERIFY_MATCH
    return VERIFY_MISMATCH


def modules_per_section(size):
    """Edge length (in modules) of one transcription section."""
    return 7 if size == 21 else 5


def num_sections(size):
    """Edge length (in sections) of the section grid, i.e. ceil(size / mps)."""
    return math.ceil(size / modules_per_section(size))


def section_label(row, col):
    """0-indexed (row, col) section coordinate -> 'A-1'-style label."""
    if not (0 <= row < len(ROW_LABELS)) or not (0 <= col < len(COL_LABELS)):
        raise ValueError("Section coordinate out of supported range: (%r, %r)" % (row, col))
    return "%s-%d" % (ROW_LABELS[row], col + 1)


def section_bounds(size, row, col):
    """
    Module-space bounding box of section (row, col) as (x0, y0, w, h), where
    x0/y0 are the top-left module coordinates and w/h are the number of REAL
    modules covered (< modules_per_section on a partial trailing row/column).
    """
    mps = modules_per_section(size)
    n = num_sections(size)
    if not (0 <= row < n) or not (0 <= col < n):
        raise ValueError("Section coordinate out of range: (%r, %r)" % (row, col))
    x0 = col * mps
    y0 = row * mps
    w = min(mps, size - x0)
    h = min(mps, size - y0)
    return x0, y0, w, h


def extract_section(matrix, row, col):
    """
    Extract section (row, col) from ``matrix`` as a tuple of
    ``modules_per_section(size)`` rows, each of that same length. Cells past
    the true QR matrix edge are filled with MODULE_OUTSIDE.
    """
    size = len(matrix)
    mps = modules_per_section(size)
    x0, y0, w, h = section_bounds(size, row, col)
    out = []
    for dy in range(mps):
        if dy < h:
            real = matrix[y0 + dy][x0:x0 + w]
            if w < mps:
                real = real + bytes([MODULE_OUTSIDE]) * (mps - w)
            out.append(real)
        else:
            out.append(bytes([MODULE_OUTSIDE]) * mps)
    return tuple(out)


def extract_real_section(matrix, row, col):
    """
    Extract section (row, col) from ``matrix`` as exactly its real modules
    -- h rows of w columns each, per ``section_bounds`` -- with no
    MODULE_OUTSIDE padding. Use this to render only the modules that
    actually exist (e.g. a partial trailing section shown at its true,
    smaller size) instead of a fixed modules_per_section(size) square with
    filler cells.
    """
    size = len(matrix)
    x0, y0, w, h = section_bounds(size, row, col)
    return tuple(matrix[y0 + dy][x0:x0 + w] for dy in range(h))


def neighbor_section(row, col, size, direction):
    """
    Returns the (row, col) of the section adjacent to (row, col) in
    ``direction`` ("up"/"down"/"left"/"right"), or None if that would fall
    outside the section grid.
    """
    n = num_sections(size)
    if direction == "up":
        row -= 1
    elif direction == "down":
        row += 1
    elif direction == "left":
        col -= 1
    elif direction == "right":
        col += 1
    else:
        raise ValueError("Unknown direction: %r" % direction)
    if 0 <= row < n and 0 <= col < n:
        return row, col
    return None


def fit_module_pixels(count, available_px):
    """
    Largest integer per-module pixel size such that ``count`` modules fit
    within ``available_px``, so modules render as crisp whole-pixel squares
    instead of being smoothed/interpolated to a fractional size. Always
    returns at least 1.
    """
    return max(1, int(available_px) // int(count))


def coord_to_module(px, py, size, module_px):
    """
    Map a touch point (px, py) -- given in whole pixels relative to the
    top-left corner of the rendered module grid, with any quiet zone already
    excluded by the caller -- to a (module_x, module_y) coordinate.

    ``module_px`` is the on-screen pixel edge length of a single module
    (matrix assumed square, uniform module size). Returns None if the point
    falls outside the matrix or entirely outside the [0, size) grid.
    """
    if module_px <= 0:
        raise ValueError("module_px must be positive")
    matrix_px_size = module_px * size
    if px < 0 or py < 0 or px >= matrix_px_size or py >= matrix_px_size:
        return None
    mx = min(px // module_px, size - 1)
    my = min(py // module_px, size - 1)
    return mx, my


def coord_to_section(px, py, size, module_px):
    """Map a touch point straight to its (row, col) section, or None."""
    m = coord_to_module(px, py, size, module_px)
    if m is None:
        return None
    mx, my = m
    mps = modules_per_section(size)
    return my // mps, mx // mps


def standard_payload(mnemonic):
    """
    Standard SeedQR payload: each BIP-39 word's wordlist index, zero-padded
    to 4 digits, concatenated with no separators. Equivalent to (and must
    stay equivalent to) RAMKeyStore.show_mnemonic()'s original inline
    computation -- this only relocates it so it can be unit-tested.
    """
    from embit import bip39

    words = mnemonic.split()
    return "".join("%04d" % bip39.WORDLIST.index(w) for w in words)


def compact_payload(mnemonic):
    """
    Compact SeedQR payload: the raw BIP-39 entropy bytes. Must never be
    hex-encoded before being handed to the QR encoder.
    """
    from embit import bip39

    return bip39.mnemonic_to_bytes(mnemonic)


class ZoomNavigator:
    """
    Tracks the section currently shown by the zoomed transcription view as
    the user pans around. Pure state machine (no lvgl dependency) so
    SeedQRZoomScreen can delegate all of its navigation bookkeeping here.
    """

    def __init__(self, size, row=0, col=0):
        self.size = size
        self.row = row
        self.col = col

    @property
    def section(self):
        return self.row, self.col

    @property
    def label(self):
        return section_label(self.row, self.col)

    def can_move(self, direction):
        return neighbor_section(self.row, self.col, self.size, direction) is not None

    def move(self, direction):
        """Move if possible; always returns the (possibly unchanged) section."""
        nxt = neighbor_section(self.row, self.col, self.size, direction)
        if nxt is not None:
            self.row, self.col = nxt
        return self.section
