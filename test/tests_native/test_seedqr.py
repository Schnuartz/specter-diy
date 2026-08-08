import ast
import sys
from pathlib import Path

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs
    setup_native_stubs()

from unittest import TestCase

import seedqr

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"

# SeedSigner docs/seed_qr/README.md worked example.
DOC_MNEMONIC = "vacuum bridge buddy supreme exclude milk consider tail expand wasp pattern nuclear"
DOC_STANDARD_PAYLOAD = "192402220235174306311124037817700641198012901210"

# Fixed valid 24-word mnemonic (BIP-39 test vector, all-zero entropy).
MNEMONIC_24 = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon art"
)


def _fake_matrix(size, fill=seedqr.MODULE_BLACK):
    """A deterministic size x size matrix for pure geometry tests."""
    return tuple(bytes([fill]) * size for _ in range(size))


def _identifiable_matrix(size):
    """
    A size x size matrix where module (x, y) encodes its own coordinates
    (as parity bits), so section extraction/recombination can be checked
    against exact expected values rather than a uniform fill.
    """
    return tuple(bytes(((x + y) % 2) for x in range(size)) for y in range(size))


class EncodingTest(TestCase):
    def test_standard_payload_matches_seedsigner_doc_example(self):
        self.assertEqual(seedqr.standard_payload(DOC_MNEMONIC), DOC_STANDARD_PAYLOAD)

    def test_standard_12_word_matrix_is_25x25(self):
        matrix = seedqr.generate_matrix(DOC_STANDARD_PAYLOAD)
        self.assertEqual(len(matrix), 25)
        self.assertTrue(all(len(row) == 25 for row in matrix))

    def test_compact_12_word_matrix_is_21x21(self):
        payload = seedqr.compact_payload(DOC_MNEMONIC)
        self.assertIsInstance(payload, bytes)
        matrix = seedqr.generate_matrix(payload)
        self.assertEqual(len(matrix), 21)

    def test_standard_24_word_matrix_is_29x29(self):
        payload = seedqr.standard_payload(MNEMONIC_24)
        matrix = seedqr.generate_matrix(payload)
        self.assertEqual(len(matrix), 29)

    def test_compact_24_word_matrix_is_25x25(self):
        payload = seedqr.compact_payload(MNEMONIC_24)
        matrix = seedqr.generate_matrix(payload)
        self.assertEqual(len(matrix), 25)

    def test_compact_payload_is_raw_bytes_not_hex(self):
        payload = seedqr.compact_payload(DOC_MNEMONIC)
        self.assertIsInstance(payload, bytes)
        # A hex string would be twice as long and be str, not bytes.
        self.assertNotIsInstance(payload, str)
        self.assertEqual(len(payload), 16)  # 12 words -> 128 bits entropy

    def test_compact_qr_encoder_receives_bytes_object(self):
        """
        The matrix generator must hand the encoder the exact bytes object,
        never a hex-encoded string representation of it.
        """
        import qrcode as qrcode_stub

        seen = {}
        original = qrcode_stub.encode_to_string

        def spy(payload):
            seen["payload"] = payload
            return original(payload)

        qrcode_stub.encode_to_string = spy
        try:
            entropy = seedqr.compact_payload(DOC_MNEMONIC)
            seedqr.generate_matrix(entropy)
        finally:
            qrcode_stub.encode_to_string = original

        self.assertIs(seen["payload"], entropy)
        self.assertIsInstance(seen["payload"], bytes)


class ValidateMatrixTest(TestCase):
    def test_rejects_empty_matrix(self):
        with self.assertRaises(seedqr.MatrixError):
            seedqr.validate_matrix(())

    def test_rejects_non_square_matrix(self):
        with self.assertRaises(seedqr.MatrixError):
            seedqr.validate_matrix((bytes([0, 0]), bytes([0])))

    def test_rejects_invalid_qr_module_count(self):
        with self.assertRaises(seedqr.MatrixError):
            seedqr.validate_matrix(_fake_matrix(22))

    def test_accepts_valid_sizes(self):
        for size in (21, 25, 29):
            self.assertEqual(seedqr.validate_matrix(_fake_matrix(size)), size)

    def test_rejects_matrix_with_invalid_module_values(self):
        # MODULE_OUTSIDE (or any value other than 0/1) may legitimately
        # appear in a section extracted for the zoom view, but a full
        # canonical/scanned matrix must contain only real black/white
        # modules -- this is what compare_matrices() relies on too.
        bad = _fake_matrix(21, fill=seedqr.MODULE_OUTSIDE)
        with self.assertRaises(seedqr.MatrixError):
            seedqr.validate_matrix(bad)


class SectionGeometryTest(TestCase):
    def test_21x21_is_3x3_sections_of_7x7(self):
        self.assertEqual(seedqr.modules_per_section(21), 7)
        self.assertEqual(seedqr.num_sections(21), 3)

    def test_25x25_is_5x5_sections_of_5x5(self):
        self.assertEqual(seedqr.modules_per_section(25), 5)
        self.assertEqual(seedqr.num_sections(25), 5)

    def test_29x29_is_6x6_sections_with_partial_final_row_and_col(self):
        self.assertEqual(seedqr.modules_per_section(29), 5)
        self.assertEqual(seedqr.num_sections(29), 6)
        # Last row/col section only has 29 - 5*5 = 4 real modules.
        x0, y0, w, h = seedqr.section_bounds(29, 5, 5)
        self.assertEqual((w, h), (4, 4))
        # A non-final section is fully real.
        x0, y0, w, h = seedqr.section_bounds(29, 0, 0)
        self.assertEqual((w, h), (5, 5))

    def test_section_labels(self):
        self.assertEqual(seedqr.section_label(0, 0), "A-1")
        self.assertEqual(seedqr.section_label(1, 2), "B-3")
        self.assertEqual(seedqr.section_label(5, 5), "F-6")

    def test_every_real_module_is_in_exactly_one_section(self):
        for size in (21, 25, 29):
            n = seedqr.num_sections(size)
            mps = seedqr.modules_per_section(size)
            covered = [[0] * size for _ in range(size)]
            for row in range(n):
                for col in range(n):
                    x0, y0, w, h = seedqr.section_bounds(size, row, col)
                    for dy in range(h):
                        for dx in range(w):
                            covered[y0 + dy][x0 + dx] += 1
            for y in range(size):
                for x in range(size):
                    self.assertEqual(
                        covered[y][x], 1,
                        "module (%d, %d) in a %dx%d matrix covered %d times" % (
                            x, y, size, size, covered[y][x],
                        ),
                    )

    def test_recombining_sections_reproduces_original_matrix_exactly(self):
        for size in (21, 25, 29):
            matrix = _identifiable_matrix(size)
            n = seedqr.num_sections(size)
            mps = seedqr.modules_per_section(size)
            rebuilt = [[None] * size for _ in range(size)]
            for row in range(n):
                for col in range(n):
                    section = seedqr.extract_section(matrix, row, col)
                    x0, y0, w, h = seedqr.section_bounds(size, row, col)
                    for dy in range(mps):
                        for dx in range(mps):
                            value = section[dy][dx]
                            in_bounds = dx < w and dy < h
                            if in_bounds:
                                self.assertNotEqual(value, seedqr.MODULE_OUTSIDE)
                                rebuilt[y0 + dy][x0 + dx] = value
                            else:
                                self.assertEqual(value, seedqr.MODULE_OUTSIDE)
            for y in range(size):
                for x in range(size):
                    self.assertEqual(rebuilt[y][x], matrix[y][x])

    def test_partial_section_distinguishes_outside_from_white(self):
        # 29x29 all-white matrix: real modules in the partial corner section
        # must read as MODULE_WHITE, padding cells as MODULE_OUTSIDE.
        matrix = _fake_matrix(29, fill=seedqr.MODULE_WHITE)
        section = seedqr.extract_section(matrix, 5, 5)
        for dy in range(5):
            for dx in range(5):
                if dx < 4 and dy < 4:
                    self.assertEqual(section[dy][dx], seedqr.MODULE_WHITE)
                else:
                    self.assertEqual(section[dy][dx], seedqr.MODULE_OUTSIDE)

    def test_out_of_range_section_raises(self):
        with self.assertRaises(ValueError):
            seedqr.section_bounds(21, 3, 0)
        with self.assertRaises(ValueError):
            seedqr.extract_section(_fake_matrix(21), 0, 3)

    def test_extract_real_section_has_no_outside_padding(self):
        # 29x29: partial trailing row/col sections must come back sized to
        # their true (smaller) extent, never padded up to 5x5 with filler.
        matrix = _identifiable_matrix(29)
        full = seedqr.extract_real_section(matrix, 0, 0)
        self.assertEqual((len(full), len(full[0])), (5, 5))

        corner = seedqr.extract_real_section(matrix, 5, 5)  # F-6: 4x4 real
        self.assertEqual((len(corner), len(corner[0])), (4, 4))

        edge_row = seedqr.extract_real_section(matrix, 5, 0)  # F-1: 4 rows x 5 cols
        self.assertEqual((len(edge_row), len(edge_row[0])), (4, 5))

        edge_col = seedqr.extract_real_section(matrix, 0, 5)  # A-6: 5 rows x 4 cols
        self.assertEqual((len(edge_col), len(edge_col[0])), (5, 4))

        for section in (full, corner, edge_row, edge_col):
            for row in section:
                self.assertNotIn(seedqr.MODULE_OUTSIDE, row)

    def test_extract_real_section_matches_true_matrix_values(self):
        for size in (21, 25, 29):
            matrix = _identifiable_matrix(size)
            n = seedqr.num_sections(size)
            for row in range(n):
                for col in range(n):
                    x0, y0, w, h = seedqr.section_bounds(size, row, col)
                    section = seedqr.extract_real_section(matrix, row, col)
                    for dy in range(h):
                        for dx in range(w):
                            self.assertEqual(section[dy][dx], matrix[y0 + dy][x0 + dx])


class TouchMappingTest(TestCase):
    def test_top_left_tap_maps_to_a1(self):
        section = seedqr.coord_to_section(0, 0, 21, module_px=10)
        self.assertEqual(section, (0, 0))
        self.assertEqual(seedqr.section_label(*section), "A-1")

    def test_bottom_right_valid_module_maps_to_final_section(self):
        size = 29
        module_px = 10
        last_px = size * module_px - 1  # inside the very last module
        section = seedqr.coord_to_section(last_px, last_px, size, module_px)
        self.assertEqual(section, (5, 5))
        self.assertEqual(seedqr.section_label(*section), "F-6")

    def test_taps_outside_matrix_bounds_are_ignored(self):
        size, module_px = 21, 10
        matrix_px = size * module_px
        self.assertIsNone(seedqr.coord_to_module(-1, 0, size, module_px))
        self.assertIsNone(seedqr.coord_to_module(0, -1, size, module_px))
        self.assertIsNone(seedqr.coord_to_module(matrix_px, 0, size, module_px))
        self.assertIsNone(seedqr.coord_to_module(0, matrix_px, size, module_px))

    def test_taps_in_quiet_zone_are_ignored_by_caller_contract(self):
        # coord_to_module takes coordinates with the quiet zone already
        # excluded; anything the caller maps to a negative offset (i.e. a
        # tap that landed in the quiet zone) must be rejected the same way
        # as any other out-of-bounds tap.
        size, module_px = 21, 10
        self.assertIsNone(seedqr.coord_to_module(-5, -5, size, module_px))

    def test_taps_exactly_on_section_boundary_are_handled_consistently(self):
        size, module_px = 25, 10
        mps = seedqr.modules_per_section(size)
        boundary_px = mps * module_px  # first pixel of the *next* section
        # last pixel column of the first section
        self.assertEqual(
            seedqr.coord_to_section(boundary_px - 1, 0, size, module_px),
            (0, 0),
        )
        # first pixel column of the second section
        self.assertEqual(
            seedqr.coord_to_section(boundary_px, 0, size, module_px),
            (0, 1),
        )

    def test_screen_offset_is_the_callers_responsibility_and_composes_linearly(self):
        # Simulates a screen translating a raw touch point into
        # matrix-relative coordinates before calling coord_to_section.
        size, module_px = 21, 10
        origin_x, origin_y = 37, 84  # matrix top-left on screen
        raw_x, raw_y = origin_x + 15, origin_y + 5  # inside module (1, 0)
        rel_x, rel_y = raw_x - origin_x, raw_y - origin_y
        self.assertEqual(seedqr.coord_to_module(rel_x, rel_y, size, module_px), (1, 0))


class NavigationTest(TestCase):
    def test_left_unavailable_in_column_1(self):
        nav = seedqr.ZoomNavigator(size=25, row=2, col=0)
        self.assertFalse(nav.can_move("left"))
        self.assertEqual(nav.move("left"), (2, 0))

    def test_up_unavailable_in_row_a(self):
        nav = seedqr.ZoomNavigator(size=25, row=0, col=2)
        self.assertFalse(nav.can_move("up"))
        self.assertEqual(nav.move("up"), (0, 2))

    def test_right_and_down_stop_at_correct_edge(self):
        size = 25
        n = seedqr.num_sections(size)
        nav = seedqr.ZoomNavigator(size=size, row=0, col=0)
        for _ in range(n + 2):
            nav.move("right")
        self.assertEqual(nav.col, n - 1)
        for _ in range(n + 2):
            nav.move("down")
        self.assertEqual(nav.row, n - 1)

    def test_navigation_into_partial_final_section_works(self):
        size = 29
        n = seedqr.num_sections(size)
        nav = seedqr.ZoomNavigator(size=size, row=0, col=0)
        for _ in range(n - 1):
            self.assertTrue(nav.can_move("right"))
            nav.move("right")
        for _ in range(n - 1):
            self.assertTrue(nav.can_move("down"))
            nav.move("down")
        self.assertEqual(nav.section, (n - 1, n - 1))
        self.assertEqual(nav.label, "F-6")
        x0, y0, w, h = seedqr.section_bounds(size, *nav.section)
        self.assertEqual((w, h), (4, 4))

    def test_returning_to_overview_preserves_current_section(self):
        nav = seedqr.ZoomNavigator(size=25, row=0, col=0)
        nav.move("right")
        nav.move("down")
        selected = nav.section
        # Simulate closing the zoom view and reopening it at the section it
        # was last on -- the overview screen just needs to remember the
        # tuple and hand it back as the initial position.
        reopened = seedqr.ZoomNavigator(size=25, row=selected[0], col=selected[1])
        self.assertEqual(reopened.section, selected)

    def test_unknown_direction_raises(self):
        with self.assertRaises(ValueError):
            seedqr.neighbor_section(0, 0, 25, "sideways")


def _flip(matrix, y, x):
    """Return a copy of `matrix` with module (y, x) flipped 0<->1."""
    rows = [bytearray(row) for row in matrix]
    rows[y][x] = 1 - rows[y][x]
    return tuple(bytes(row) for row in rows)


class PayloadVerificationTest(TestCase):
    """
    Covers seedqr.verify_payload / seedqr.verify_scanned_payload -- the
    truthful, payload-only verification actually wired into the GUI (see
    the "Verification / hardware limitation" note at the top of seedqr.py:
    today's scanner hardware can't expose module-level data, only the
    decoded payload).
    """

    def test_standard_payload_matches_itself(self):
        payload = DOC_STANDARD_PAYLOAD
        scanned = payload.encode()
        self.assertTrue(seedqr.verify_payload(payload, scanned))
        self.assertEqual(seedqr.verify_scanned_payload(payload, scanned), seedqr.VERIFY_MATCH)

    def test_compact_payload_matches_itself(self):
        payload = seedqr.compact_payload(DOC_MNEMONIC)
        scanned = bytes(payload)  # the scanner hands back identical raw bytes
        self.assertTrue(seedqr.verify_payload(payload, scanned))
        self.assertEqual(seedqr.verify_scanned_payload(payload, scanned), seedqr.VERIFY_MATCH)

    def test_standard_24_word_payload_matches_itself(self):
        payload = seedqr.standard_payload(MNEMONIC_24)
        scanned = payload.encode()
        self.assertEqual(seedqr.verify_scanned_payload(payload, scanned), seedqr.VERIFY_MATCH)

    def test_compact_24_word_payload_matches_itself(self):
        payload = seedqr.compact_payload(MNEMONIC_24)
        self.assertEqual(seedqr.verify_scanned_payload(payload, bytes(payload)), seedqr.VERIFY_MATCH)

    def test_standard_payload_mismatch(self):
        payload = DOC_STANDARD_PAYLOAD
        flipped_last_digit = "1" if payload[-1] != "1" else "2"
        scanned = (payload[:-1] + flipped_last_digit).encode()
        self.assertFalse(seedqr.verify_payload(payload, scanned))
        self.assertEqual(seedqr.verify_scanned_payload(payload, scanned), seedqr.VERIFY_MISMATCH)

    def test_compact_payload_mismatch(self):
        payload = seedqr.compact_payload(DOC_MNEMONIC)
        other = seedqr.compact_payload(MNEMONIC_24)
        self.assertFalse(seedqr.verify_payload(payload, other))
        self.assertEqual(seedqr.verify_scanned_payload(payload, other), seedqr.VERIFY_MISMATCH)

    def test_truncated_standard_payload_is_mismatch(self):
        payload = DOC_STANDARD_PAYLOAD
        truncated = payload[:-4].encode()  # missing the last word's digits
        self.assertFalse(seedqr.verify_payload(payload, truncated))
        self.assertEqual(seedqr.verify_scanned_payload(payload, truncated), seedqr.VERIFY_MISMATCH)

    def test_truncated_compact_payload_is_mismatch(self):
        payload = seedqr.compact_payload(DOC_MNEMONIC)
        truncated = payload[:-1]
        self.assertFalse(seedqr.verify_payload(payload, truncated))
        self.assertEqual(seedqr.verify_scanned_payload(payload, truncated), seedqr.VERIFY_MISMATCH)

    def test_compact_payload_never_coerced_through_text_decoding(self):
        # Compact SeedQR entropy is arbitrary bytes and need not be valid
        # UTF-8; it must be compared byte-for-byte, never via .decode().
        payload = bytes(range(16))
        self.assertTrue(seedqr.verify_payload(payload, bytes(range(16))))

    def test_wrong_type_scan_is_a_mismatch_not_a_crash(self):
        # A Standard SeedQR is expected (str), but the scan returned bytes
        # that aren't valid text (e.g. a Compact SeedQR was scanned by
        # mistake) -- must classify as a mismatch, not raise.
        payload = DOC_STANDARD_PAYLOAD
        scanned = bytes([0xFF, 0xFE, 0x00, 0x01])
        self.assertFalse(seedqr.verify_payload(payload, scanned))
        self.assertEqual(seedqr.verify_scanned_payload(payload, scanned), seedqr.VERIFY_MISMATCH)

    def test_empty_or_missing_scan_is_unreadable(self):
        self.assertEqual(
            seedqr.verify_scanned_payload(DOC_STANDARD_PAYLOAD, b""), seedqr.VERIFY_UNREADABLE
        )
        self.assertEqual(
            seedqr.verify_scanned_payload(DOC_STANDARD_PAYLOAD, None), seedqr.VERIFY_UNREADABLE
        )

    def test_verify_payload_rejects_non_str_non_bytes_expected(self):
        with self.assertRaises(TypeError):
            seedqr.verify_payload(12345, b"12345")


class MatrixComparisonTest(TestCase):
    """
    Covers seedqr.compare_matrices / mismatch_stats / matrix_diff /
    diff_grid: true module-level comparison. Nothing in the live GUI flow
    feeds these a physically-scanned matrix today (see the module
    docstring), but they're kept fully implemented and tested so a diff
    view can be wired up the moment scanner hardware exposes raw module
    data.
    """

    def test_identical_matrix_has_zero_mismatches(self):
        matrix = _identifiable_matrix(21)
        mismatches = seedqr.compare_matrices(matrix, matrix)
        self.assertEqual(mismatches, ())
        stats = seedqr.mismatch_stats(mismatches, 21)
        self.assertEqual(stats, {"count": 0, "total": 441, "percent": 0.0})

    def test_one_missing_module(self):
        # expected BLACK, observed WHITE -- something the user forgot to draw
        expected = _fake_matrix(21, fill=seedqr.MODULE_BLACK)
        observed = _flip(expected, 0, 0)
        mismatches = seedqr.compare_matrices(expected, observed)
        self.assertEqual(mismatches, ((0, 0, seedqr.MISMATCH_MISSING),))

    def test_one_extra_module(self):
        # expected WHITE, observed BLACK -- an extra mark that shouldn't be there
        expected = _fake_matrix(21, fill=seedqr.MODULE_WHITE)
        observed = _flip(expected, 3, 4)
        mismatches = seedqr.compare_matrices(expected, observed)
        self.assertEqual(mismatches, ((3, 4, seedqr.MISMATCH_EXTRA),))

    def test_multiple_mismatches(self):
        size = 25
        expected = _identifiable_matrix(size)
        observed = expected
        coords = [(0, 0), (1, 1), (2, 3), (10, 10), (24, 24)]
        for y, x in coords:
            observed = _flip(observed, y, x)
        mismatches = seedqr.compare_matrices(expected, observed)
        self.assertEqual(len(mismatches), len(coords))
        self.assertEqual({(y, x) for y, x, _ in mismatches}, set(coords))
        stats = seedqr.mismatch_stats(mismatches, size)
        self.assertEqual(stats["count"], len(coords))
        self.assertEqual(stats["total"], size * size)
        self.assertAlmostEqual(stats["percent"], len(coords) * 100.0 / (size * size))

    def test_mismatch_percentage_seedsigner_style_example(self):
        # "7 of 841 modules differ (0.83%)" -- 841 = 29x29
        stats = seedqr.mismatch_stats([None] * 7, 29)
        self.assertEqual(stats["total"], 841)
        self.assertEqual(stats["count"], 7)
        self.assertAlmostEqual(stats["percent"], 7 * 100.0 / 841, places=6)
        self.assertEqual("%.2f" % stats["percent"], "0.83")

    def test_matrix_size_mismatch_raises(self):
        with self.assertRaises(seedqr.MatrixError):
            seedqr.compare_matrices(_fake_matrix(21), _fake_matrix(25))
        with self.assertRaises(seedqr.MatrixError):
            seedqr.diff_grid(_fake_matrix(21), _fake_matrix(25))

    def test_invalid_matrix_input_raises(self):
        bad = _fake_matrix(21, fill=seedqr.MODULE_OUTSIDE)
        with self.assertRaises(seedqr.MatrixError):
            seedqr.compare_matrices(bad, _fake_matrix(21))
        with self.assertRaises(seedqr.MatrixError):
            seedqr.compare_matrices(_fake_matrix(21), bad)

    def test_21x21_qr_full_comparison(self):
        expected = seedqr.generate_matrix(seedqr.compact_payload(DOC_MNEMONIC))
        self.assertEqual(len(expected), 21)
        self.assertEqual(seedqr.compare_matrices(expected, expected), ())

    def test_larger_qr_sizes(self):
        for size in (25, 29):
            matrix = _identifiable_matrix(size)
            self.assertEqual(seedqr.compare_matrices(matrix, matrix), ())

    def test_partial_trailing_section_size_29x29(self):
        # 29x29 has a partial trailing section (see SectionGeometryTest),
        # but compare_matrices() operates on the full canonical matrix,
        # which has no MODULE_OUTSIDE cells -- every one of its 841
        # modules, including ones only reachable through that trailing
        # section, is real and comparable.
        size = 29
        expected = _identifiable_matrix(size)
        observed = _flip(expected, size - 1, size - 1)
        mismatches = seedqr.compare_matrices(expected, observed)
        self.assertEqual(mismatches, ((size - 1, size - 1, mismatches[0][2]),))

    def test_diff_grid_classifies_every_module(self):
        size = 21
        expected = _identifiable_matrix(size)
        observed = _flip(expected, 0, 0)
        grid = seedqr.diff_grid(expected, observed)
        self.assertEqual(len(grid), size)
        self.assertEqual(len(grid[0]), size)
        self.assertIn(grid[0][0], (seedqr.MISMATCH_MISSING, seedqr.MISMATCH_EXTRA))
        for y in range(size):
            for x in range(size):
                if (y, x) == (0, 0):
                    continue
                self.assertIn(grid[y][x], (seedqr.DIFF_CORRECT_BLACK, seedqr.DIFF_CORRECT_WHITE))

    def test_matrix_diff_combines_compare_and_stats(self):
        size = 21
        expected = _fake_matrix(size, fill=seedqr.MODULE_BLACK)
        observed = _flip(expected, 0, 0)
        mismatches, stats = seedqr.matrix_diff(expected, observed)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["total"], size * size)


class NoUnconditionalPrintTest(TestCase):
    """
    Regression tests ensuring secret QR content (mnemonic, SeedQR digits,
    Compact SeedQR bytes, the QR matrix) can't reach stdout/logs.
    """

    def _assert_no_unconditional_print(self, path, qualnames):
        """
        For each dotted qualname (e.g. "QRCode._set_text"), assert the
        function body contains no `print(...)` call at its own top level
        (i.e. every print call, if any, is nested inside a conditional).
        """
        tree = ast.parse(path.read_text(), filename=str(path))
        found = {}

        class ClassVisitor(ast.NodeVisitor):
            def visit_ClassDef(self, node):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        found["%s.%s" % (node.name, item.name)] = item
                self.generic_visit(node)

        ClassVisitor().visit(tree)

        for qualname in qualnames:
            self.assertIn(qualname, found, "%s not found in %s" % (qualname, path))
            func = found[qualname]
            for stmt in func.body:
                is_print_expr = (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "print"
                )
                self.assertFalse(
                    is_print_expr,
                    "%s in %s contains an unconditional print() call" % (qualname, path),
                )

    def test_qrcode_component_has_no_unconditional_print(self):
        path = SRC_DIR / "gui" / "components" / "qrcode.py"
        self._assert_no_unconditional_print(path, ["QRCode._set_text", "QRCode.set_text"])

    def test_seedqr_pure_functions_never_print(self):
        import builtins

        calls = []
        original_print = builtins.print
        builtins.print = lambda *a, **kw: calls.append((a, kw))
        try:
            matrix = seedqr.generate_matrix(DOC_STANDARD_PAYLOAD)
            for size in (21, 25, 29):
                m = _identifiable_matrix(size)
                n = seedqr.num_sections(size)
                for row in range(n):
                    for col in range(n):
                        seedqr.extract_section(m, row, col)
            nav = seedqr.ZoomNavigator(size=29, row=0, col=0)
            for direction in ("right", "down", "left", "up"):
                nav.move(direction)
            payload = seedqr.compact_payload(DOC_MNEMONIC)
            # Verification helpers, exercised across all three outcomes.
            seedqr.verify_payload(payload, bytes(payload))
            seedqr.verify_scanned_payload(payload, bytes(payload))
            seedqr.verify_scanned_payload(DOC_STANDARD_PAYLOAD, b"not a match")
            seedqr.verify_scanned_payload(DOC_STANDARD_PAYLOAD, b"")
            other = _flip(matrix, 0, 0)
            seedqr.compare_matrices(matrix, other)
            seedqr.mismatch_stats(seedqr.compare_matrices(matrix, other), len(matrix))
            seedqr.matrix_diff(matrix, other)
            seedqr.diff_grid(matrix, other)
        finally:
            builtins.print = original_print
        self.assertEqual(calls, [])

    def _assert_no_unconditional_print_in_functions(self, path, names):
        """
        Like _assert_no_unconditional_print, but for named functions
        anywhere in the module (module-level `async def`s included, not
        just class methods) -- used for the verification flow, which is
        driven by module-level coroutines rather than Screen methods.
        """
        tree = ast.parse(path.read_text(), filename=str(path))
        found = {}

        class FuncVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                if node.name in names:
                    found[node.name] = node
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node):
                if node.name in names:
                    found[node.name] = node
                self.generic_visit(node)

        FuncVisitor().visit(tree)

        for name in names:
            self.assertIn(name, found, "%s not found in %s" % (name, path))
            func = found[name]
            for stmt in func.body:
                is_print_expr = (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "print"
                )
                self.assertFalse(
                    is_print_expr,
                    "%s in %s contains an unconditional print() call" % (name, path),
                )

    def test_verification_flow_has_no_unconditional_print(self):
        path = SRC_DIR / "gui" / "screens" / "seedqr.py"
        self._assert_no_unconditional_print_in_functions(
            path, ["_verify_seedqr", "show_seedqr"],
        )

    def test_scan_qr_host_has_no_unconditional_print(self):
        path = SRC_DIR / "specter.py"
        self._assert_no_unconditional_print_in_functions(path, ["scan_qr"])

    def test_ram_keystore_does_not_display_raw_seedqr_payload_as_text(self):
        """
        Standard/Compact SeedQR selections must route to the dedicated
        transcription viewer, not to QRAlert(message=<raw payload>), which
        would render the digit string / hex underneath the QR code.
        """
        path = SRC_DIR / "keystore" / "ram.py"
        src = path.read_text()
        self.assertNotIn("hexlify(qr_msg)", src)

    def test_verify_result_screen_never_embeds_scanned_bytes_as_text(self):
        """
        The verification result screens must only ever show the fixed,
        pre-written outcome copy -- never interpolate the scanned bytes
        (or the expected payload) into a label.
        """
        path = SRC_DIR / "gui" / "screens" / "seedqr.py"
        src = path.read_text()
        self.assertNotIn("scanned_bytes)", src)
        self.assertNotIn("% scanned", src)
