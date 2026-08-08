"""
Touch-friendly SeedQR transcription viewer.

Shows the complete Standard/Compact SeedQR, split into labeled sections
(matching SeedSigner's SeedQR transcription workflow), so the user can tap
any section and view it greatly enlarged -- with crisp, individually
distinguishable modules -- while copying it onto paper or a metal plate.

Both the overview and the zoomed section views render the *same* canonical
module matrix (see ``seedqr.generate_matrix``); nothing here re-encodes the
payload, so overview and zoom can never disagree about module data.

This screen never displays the raw SeedQR digit string / Compact SeedQR
bytes / mnemonic as text, and never prints or persists the matrix or
payload (see the ``sensitive`` handling in gui.components.qrcode.QRCode and
the DELETE handlers below, which drop references to the secret matrix as
soon as a screen is torn down).
"""
import lvgl as lv

import seedqr
from .screen import Screen
from .alert import Alert
from .prompt import Prompt
from ..common import add_label, add_button, add_button_pair, HOR_RES
from ..decorators import on_release, cb_with_args, feed_touch

MODULE_WHITE_COLOR = 0xFFFFFF
MODULE_BLACK_COLOR = 0x000000
MODULE_OUTSIDE_COLOR = 0x313E50  # matches the app's muted "highlight" theme color
SECTION_LINE_COLOR = 0x2372B2
ACTIVE_TEXT_COLOR = 0xFFFFFF  # text color used on top of a solid-filled active header
#: module grid line used in the zoomed view, where it must read clearly for
#: accurate transcription
GRID_LINE_COLOR = 0x808080
#: much subtler module grid line used only on the overview -- just enough
#: of a "graph paper" hint to see individual modules, without competing
#: with the bolder section-boundary overlay
OVERVIEW_GRID_LINE_COLOR = 0xB0B0B0
OVERVIEW_GRID_LINE_OPA = 90

# Verification result colors/icons -- kept visually distinct from the
# neutral black/white module colors above so a result screen is
# unmistakable at a glance.
SUCCESS_COLOR = 0x1FC161
ERROR_COLOR = 0xE0402A


def _new_style():
    """A fresh lv.style_t() seeded from the base theme, ready to customize."""
    style = lv.style_t()
    lv.style_copy(style, lv.style_plain)
    return style


def _flat_cell_style(color_hex, border_width=0, border_color=GRID_LINE_COLOR, border_opa=255):
    style = _new_style()
    style.body.main_color = lv.color_hex(color_hex)
    style.body.grad_color = lv.color_hex(color_hex)
    style.body.opa = 255
    style.body.radius = 0
    style.body.border.width = border_width
    style.body.border.color = lv.color_hex(border_color)
    style.body.border.opa = border_opa
    style.body.shadow.width = 0
    return style


def _transparent_style():
    style = _new_style()
    style.body.opa = 0
    style.body.border.width = 0
    style.body.shadow.width = 0
    return style


def _axis_label_style(color_hex, font):
    style = _new_style()
    style.body.opa = 0
    style.text.color = lv.color_hex(color_hex)
    style.text.font = font
    return style


def _axis_box_style(color, filled=False):
    """
    Header-cell box style. `filled=False` (the default, non-active state)
    gives a transparent box with just a thin border. `filled=True` (the
    currently active row/column) gives a solid block of `color` -- much
    stronger visual contrast than a border alone.
    """
    style = _new_style()
    style.body.radius = 0
    style.body.shadow.width = 0
    style.body.border.width = 1
    style.body.border.color = lv.color_hex(color)
    if filled:
        style.body.opa = 255
        style.body.main_color = lv.color_hex(color)
        style.body.grad_color = lv.color_hex(color)
        style.body.border.opa = 255
    else:
        style.body.opa = 0
        style.body.border.opa = 200
    return style


def _module_color(value):
    if value == seedqr.MODULE_BLACK:
        return MODULE_BLACK_COLOR
    if value == seedqr.MODULE_WHITE:
        return MODULE_WHITE_COLOR
    return MODULE_OUTSIDE_COLOR


def _build_grid(parent, rows, cols, module_px):
    """
    Creates a `cols * module_px` x `rows * module_px` transparent container
    on `parent` with one square, unstyled child cell per (row, col).
    Returns (container, cells) where cells[y][x] are the per-module lv.obj;
    call `_paint_cells` to actually color them in.
    """
    container = lv.obj(parent)
    container.set_click(False)
    container.set_style(_transparent_style())
    container.set_size(cols * module_px, rows * module_px)

    cells = []
    for y in range(rows):
        row_cells = []
        for x in range(cols):
            cell = lv.obj(container)
            cell.set_click(False)
            cell.set_size(module_px, module_px)
            cell.set_pos(x * module_px, y * module_px)
            row_cells.append(cell)
        cells.append(row_cells)
    return container, cells


def _paint_cells(cells, matrix_rows, grid_lines, grid_color=GRID_LINE_COLOR, grid_opa=255):
    border_width = 1 if grid_lines else 0
    style_cache = {}
    for y, row in enumerate(matrix_rows):
        for x, value in enumerate(row):
            color = _module_color(value)
            style = style_cache.get(color)
            if style is None:
                style = _flat_cell_style(color, border_width, grid_color, grid_opa)
                style_cache[color] = style
            cells[y][x].set_style(style)


def _build_header_cell(scr, x, y, w, h, text, box_style, text_style):
    """
    One bordered/filled header cell (box + centered label), shared by every
    row/column header this screen draws -- whether it's one of many
    per-section cells (overview) or a single cell spanning the whole axis
    (zoomed view).

    The label's width is capped to the box's shorter side so text can never
    overflow past a narrow header strip; the real text is set *before*
    centering, since aligning against a still-empty label bakes in a
    position based on its (near-zero) placeholder size and a later
    set_text() would not re-center it.
    """
    box = lv.obj(scr)
    box.set_click(False)
    box.set_style(box_style)
    box.set_size(w, h)
    box.set_pos(x, y)

    lbl = lv.label(box)
    lbl.set_long_mode(lv.label.LONG.BREAK)
    lbl.set_style(0, text_style)
    lbl.set_width(min(w, h))
    lbl.set_align(lv.label.ALIGN.CENTER)
    lbl.set_text(text)
    lbl.align(box, lv.ALIGN.CENTER, 0, 0)
    return box, lbl


def _build_axis_labels(scr, grid_x, grid_y, size, module_px, axis_size, axis_gap, color, font):
    """
    Builds a column-number header row above (1, 2, 3, ...) and a row-letter
    header column to the left of (A, B, C, ...) a grid positioned at
    (grid_x, grid_y) on `scr`, one bordered header cell per section --
    matching the section grid below/right of it, like a spreadsheet's
    row/column headers. A partial trailing row/column (e.g. the 29x29
    Standard SeedQR's last row/column) gets a correspondingly smaller
    header cell, matching its true, smaller extent, rather than a full-size
    one that would overhang past the real modules it labels. The corner
    where the two header strips meet is left empty; it labels nothing.
    Returns (col_labels, row_labels), each a list of (box, label) pairs, so
    callers can highlight them later.
    """
    mps = seedqr.modules_per_section(size)
    n_sections = seedqr.num_sections(size)
    box_style = _axis_box_style(color)
    text_style = _axis_label_style(color, font)

    col_labels = []
    x = grid_x
    for col in range(n_sections):
        w = min(mps, size - col * mps) * module_px
        col_labels.append(_build_header_cell(
            scr, x, grid_y - axis_size, w, axis_size, seedqr.COL_LABELS[col], box_style, text_style,
        ))
        x += w

    row_labels = []
    y = grid_y
    for row in range(n_sections):
        h = min(mps, size - row * mps) * module_px
        row_labels.append(_build_header_cell(
            scr, grid_x - axis_size - axis_gap, y, axis_size, h, seedqr.ROW_LABELS[row], box_style, text_style,
        ))
        y += h

    return col_labels, row_labels


def _highlight_axis_labels(col_labels, row_labels, active_col, active_row, dim_color, active_color, font):
    dim_box = _axis_box_style(dim_color)
    active_box = _axis_box_style(active_color, filled=True)
    dim_text = _axis_label_style(dim_color, font)
    active_text = _axis_label_style(ACTIVE_TEXT_COLOR, font)
    for col, (box, lbl) in enumerate(col_labels):
        is_active = col == active_col
        box.set_style(active_box if is_active else dim_box)
        lbl.set_style(0, active_text if is_active else dim_text)
    for row, (box, lbl) in enumerate(row_labels):
        is_active = row == active_row
        box.set_style(active_box if is_active else dim_box)
        lbl.set_style(0, active_text if is_active else dim_text)


class SeedQROverviewScreen(Screen):
    """
    Shows the complete SeedQR matrix with subtle section boundary overlays.
    Tapping a section returns its (row, col) coordinate; the Close/Back
    button returns None.
    """

    #: largest square area (in px) the module grid is allowed to occupy
    MAX_GRID_PX = 400
    #: width of the row-letter column / height of the column-number row
    AXIS_SIZE = 30
    AXIS_GAP = 4
    LEFT_MARGIN = 20
    GRID_TOP = 175
    AXIS_COLOR = 0x808A9C

    def __init__(self, matrix, format_label, initial_section=None, can_verify=False):
        super().__init__()
        self.matrix = matrix
        self.size = len(matrix)
        self.mps = seedqr.modules_per_section(self.size)
        self.n_sections = seedqr.num_sections(self.size)

        grid_area = HOR_RES - self.LEFT_MARGIN - self.AXIS_SIZE - self.AXIS_GAP - self.LEFT_MARGIN
        self.module_px = seedqr.fit_module_pixels(self.size, min(self.MAX_GRID_PX, grid_area))
        self.grid_x = self.LEFT_MARGIN + self.AXIS_SIZE + self.AXIS_GAP

        self.title = add_label(format_label, scr=self, style="title")
        self.subtitle = add_label(
            "%dx%d modules" % (self.size, self.size),
            y=55, scr=self, style="hint",
        )
        # generate_matrix() returns the bare module grid, with no quiet
        # zone (unlike the padded bitmap qrcode_encode() produces for
        # every other on-screen QR code) -- callers who only copy what's
        # drawn here would end up with a physical code missing its
        # required blank border, which a real scanner may then fail to
        # detect at all. Remind the user explicitly, anchored below the
        # subtitle (rather than a fixed y) so it can never collide with
        # the grid/axis header below it, however many lines it wraps to.
        self.quiet_zone_hint = add_label(
            "Leave a blank quiet zone (4+ modules) around the QR you draw",
            y=0, scr=self, style="hint",
        )
        self.quiet_zone_hint.align(self.subtitle, lv.ALIGN.OUT_BOTTOM_MID, 0, 8)

        self.grid, self.cells = _build_grid(self, self.size, self.size, self.module_px)
        _paint_cells(
            self.cells, self.matrix, grid_lines=True,
            grid_color=OVERVIEW_GRID_LINE_COLOR, grid_opa=OVERVIEW_GRID_LINE_OPA,
        )
        self.grid.set_pos(self.grid_x, self.GRID_TOP)
        self.grid.set_click(True)
        self.grid.set_event_cb(self.on_grid_touch)

        self.col_labels, self.row_labels = _build_axis_labels(
            self, self.grid_x, self.GRID_TOP, self.size, self.module_px,
            self.AXIS_SIZE, self.AXIS_GAP, self.AXIS_COLOR, lv.font_roboto_22,
        )

        self._draw_section_overlay()

        self.instruction = add_label(
            "Tap a section to enlarge", y=0, scr=self, style="hint"
        )
        self.instruction.align(self.grid, lv.ALIGN.OUT_BOTTOM_MID, 0, 20)

        if can_verify:
            self.verify_button, self.close_button = add_button_pair(
                lv.SYMBOL.OK + " Done",
                on_release(self.verify),
                lv.SYMBOL.CLOSE + " Close",
                on_release(self.close),
                scr=self,
            )
        else:
            self.verify_button = None
            self.close_button = add_button(
                lv.SYMBOL.CLOSE + " Close", on_release(self.close), scr=self
            )

        self.set_event_cb(self.cb)

        if initial_section is not None:
            self._outline_selected(initial_section)

    def cb(self, obj, event):
        if event == lv.EVENT.DELETE:
            # Drop references to the secret matrix as soon as this screen
            # is torn down.
            self.matrix = None
            self.cells = None

    def _draw_section_overlay(self):
        """Thin, subtle lines boxing in every section -- including the
        outer edge of the whole grid, so edge sections are fully enclosed
        too -- drawn on top of the module grid as separate overlay bars,
        never altering the underlying module cells, so scannability is
        unaffected."""
        line_style = _new_style()
        line_style.body.main_color = lv.color_hex(SECTION_LINE_COLOR)
        line_style.body.grad_color = lv.color_hex(SECTION_LINE_COLOR)
        line_style.body.opa = 90
        line_style.body.border.width = 0

        grid_px = self.size * self.module_px
        # i=0 and i=n_sections are the grid's own outer edges: including
        # them boxes in the first/last row and column of sections too,
        # instead of leaving their outer side unmarked.
        for i in range(self.n_sections + 1):
            offset = min(i * self.mps * self.module_px, grid_px - 1)
            vline = lv.obj(self.grid)
            vline.set_click(False)
            vline.set_style(line_style)
            vline.set_size(1, grid_px)
            vline.set_pos(offset, 0)

            hline = lv.obj(self.grid)
            hline.set_click(False)
            hline.set_style(line_style)
            hline.set_size(grid_px, 1)
            hline.set_pos(0, offset)

    def _outline_selected(self, section):
        """Outline the section the user last zoomed into, for as long as
        this overview screen is shown, so returning to the overview
        visibly preserves their place."""
        row, col = section
        try:
            x0, y0, w, h = seedqr.section_bounds(self.size, row, col)
        except ValueError:
            return
        marker = lv.obj(self.grid)
        marker.set_click(False)
        style = _new_style()
        style.body.opa = 0
        style.body.border.width = 2
        style.body.border.color = lv.color_hex(SECTION_LINE_COLOR)
        style.body.border.opa = 255
        marker.set_style(style)
        marker.set_size(w * self.module_px, h * self.module_px)
        marker.set_pos(x0 * self.module_px, y0 * self.module_px)

        _highlight_axis_labels(
            self.col_labels, self.row_labels, col, row,
            self.AXIS_COLOR, SECTION_LINE_COLOR, lv.font_roboto_22,
        )

    def on_grid_touch(self, obj, event):
        if event == lv.EVENT.PRESSING:
            feed_touch()
            return
        if event != lv.EVENT.RELEASED:
            return
        point = lv.point_t()
        indev = lv.indev_get_act()
        lv.indev_get_point(indev, point)
        rel_x = point.x - self.grid.get_x()
        rel_y = point.y - self.grid.get_y()
        section = seedqr.coord_to_section(rel_x, rel_y, self.size, self.module_px)
        if section is None:
            return
        self.set_value(section)

    def close(self):
        self.set_value(None)

    def verify(self):
        self.set_value("verify")


class SeedQRZoomScreen(Screen):
    """
    Shows one section of the SeedQR matrix greatly enlarged, with crisp
    square modules, visible grid lines, and Left/Right/Up/Down navigation
    to neighboring sections. Returns ("close", section) or
    ("overview", section) depending on which control the user pressed.
    """

    #: largest square area (in px) the module grid is allowed to occupy
    MAX_GRID_PX = 400
    NAV_BTN_W = 100
    NAV_BTN_H = 60
    NAV_GAP = 20
    #: order matches how the four nav buttons are laid out left-to-right
    NAV_ORDER = (("left", lv.SYMBOL.LEFT), ("up", lv.SYMBOL.UP),
                 ("down", lv.SYMBOL.DOWN), ("right", lv.SYMBOL.RIGHT))

    #: width of the row-letter column / height of the column-number row
    AXIS_SIZE = 36
    AXIS_GAP = 4
    LEFT_MARGIN = 20
    GRID_TOP = 140
    AXIS_ACTIVE_COLOR = SECTION_LINE_COLOR

    def __init__(self, matrix, row, col):
        super().__init__()
        self.matrix = matrix
        self.size = len(matrix)
        self.navigator = seedqr.ZoomNavigator(self.size, row, col)
        self.n_sections = seedqr.num_sections(self.size)
        self.mps = seedqr.modules_per_section(self.size)

        grid_area = HOR_RES - self.LEFT_MARGIN - self.AXIS_SIZE - self.AXIS_GAP - self.LEFT_MARGIN
        self.module_px = seedqr.fit_module_pixels(self.mps, min(self.MAX_GRID_PX, grid_area))
        self.grid_x = self.LEFT_MARGIN + self.AXIS_SIZE + self.AXIS_GAP

        self.label = add_label(self.navigator.label, scr=self, style="title")

        # The grid and its axis header boxes are (re)built fresh every time
        # the current section changes: most sections are a full mps x mps
        # square, but the trailing row/column of a non-square-divisible
        # SeedQR (e.g. 29x29) is smaller, and should render at its true,
        # smaller size rather than padding it out with filler modules.
        self.grid = self.cells = None
        self.col_box = self.col_header = None
        self.row_box = self.row_header = None

        self._build_nav_controls()
        self._render_section()

        self.back_button, self.close_button = add_button_pair(
            lv.SYMBOL.LEFT + " Overview",
            on_release(self.back_to_overview),
            lv.SYMBOL.CLOSE + " Close",
            on_release(self.close),
            scr=self,
        )

        self.set_event_cb(self.cb)

    def cb(self, obj, event):
        if event == lv.EVENT.DELETE:
            self.matrix = None
            self.cells = None

    def _build_nav_controls(self):
        """
        A single row of Left/Up/Down/Right buttons below the grid, at a
        fixed position based on the largest a section can ever be (a full
        mps x mps square) -- not the current section's possibly-smaller
        size -- so the buttons stay put as the user pans between full and
        partial (trailing row/column) sections.
        """
        max_grid_px = self.mps * self.module_px
        nav_y = self.GRID_TOP + max_grid_px + 20
        total_w = 4 * self.NAV_BTN_W + 3 * self.NAV_GAP
        start_x = (HOR_RES - total_w) // 2
        self.nav_buttons = {}
        for i, (direction, symbol) in enumerate(self.NAV_ORDER):
            btn = add_button(symbol, on_release(cb_with_args(self._move, direction)), scr=self)
            btn.set_size(self.NAV_BTN_W, self.NAV_BTN_H)
            btn.set_pos(start_x + i * (self.NAV_BTN_W + self.NAV_GAP), nav_y)
            self.nav_buttons[direction] = btn

    def _move(self, direction):
        self.navigator.move(direction)
        self._render_section()

    def _render_section(self):
        row, col = self.navigator.section
        _, _, w, h = seedqr.section_bounds(self.size, row, col)
        real_section = seedqr.extract_real_section(self.matrix, row, col)

        for obj in (self.grid, self.col_box, self.row_box):
            if obj is not None:
                obj.del_async()

        self.grid, self.cells = _build_grid(self, h, w, self.module_px)
        _paint_cells(self.cells, real_section, grid_lines=True)
        self.grid.set_pos(self.grid_x, self.GRID_TOP)

        # A single header box above the grid showing the current section's
        # column number, and one to its left showing the current row
        # letter, each sized to match the (possibly smaller, for a partial
        # trailing row/column) grid it labels.
        w_px, h_px = w * self.module_px, h * self.module_px
        box_style = _axis_box_style(self.AXIS_ACTIVE_COLOR, filled=True)
        text_style = _axis_label_style(ACTIVE_TEXT_COLOR, lv.font_roboto_28)
        self.col_box, self.col_header = _build_header_cell(
            self, self.grid_x, self.GRID_TOP - self.AXIS_SIZE, w_px, self.AXIS_SIZE,
            seedqr.COL_LABELS[col], box_style, text_style,
        )
        self.row_box, self.row_header = _build_header_cell(
            self, self.LEFT_MARGIN, self.GRID_TOP, self.AXIS_SIZE, h_px,
            seedqr.ROW_LABELS[row], box_style, text_style,
        )

        self.label.set_text(self.navigator.label)
        for direction, btn in self.nav_buttons.items():
            if self.navigator.can_move(direction):
                btn.set_state(lv.btn.STATE.REL)
            else:
                btn.set_state(lv.btn.STATE.INA)

    def back_to_overview(self):
        self.set_value(("overview", self.navigator.section))

    def close(self):
        self.set_value(("close", self.navigator.section))


class SeedQRVerifyResultScreen(Screen):
    """
    Shows the outcome of scanning a manually-copied SeedQR back in.

    Today's QR scanner hardware only ever exposes the *decoded payload* of
    a scan (see the "Verification / hardware limitation" note in
    seedqr.py), so there are exactly three possible outcomes:
      - seedqr.VERIFY_MATCH: the scan decoded to the correct SeedQR. Shown
        as a clear green success state -- worded carefully to claim only
        what's actually known (the content is right), not that every
        module was drawn perfectly.
      - seedqr.VERIFY_MISMATCH: the scan decoded to something else. Hard
        failure -- this backup must not be trusted.
      - seedqr.VERIFY_UNREADABLE: nothing decoded (unreadable QR, or the
        user cancelled the scan).

    Returns "rescan" (try scanning again), "redraw" (go back to the
    transcription viewer), or "done" (finish verifying).
    """

    TITLES = {
        seedqr.VERIFY_MATCH: lv.SYMBOL.OK + " SeedQR contents verified",
        seedqr.VERIFY_MISMATCH: lv.SYMBOL.WARNING + " SeedQR does not match",
        seedqr.VERIFY_UNREADABLE: lv.SYMBOL.WARNING + " QR could not be read",
    }
    MESSAGES = {
        seedqr.VERIFY_MATCH: (
            "The QR code decodes to the correct SeedQR.\n\n"
            "The scanner checks the decoded content, not every individual "
            "module -- it can't tell whether error correction was needed."
        ),
        seedqr.VERIFY_MISMATCH: (
            "The scanned QR does not contain the expected SeedQR.\n\n"
            "Do not use this backup."
        ),
        seedqr.VERIFY_UNREADABLE: (
            "The QR code could not be read.\n\n"
            "Check the transcription and try again."
        ),
    }
    COLORS = {
        seedqr.VERIFY_MATCH: SUCCESS_COLOR,
        seedqr.VERIFY_MISMATCH: ERROR_COLOR,
        seedqr.VERIFY_UNREADABLE: ERROR_COLOR,
    }

    def __init__(self, outcome):
        super().__init__()
        self.outcome = outcome

        self.title = add_label(self.TITLES[outcome], scr=self, style="title")
        self.title.set_style(0, _axis_label_style(self.COLORS[outcome], lv.font_roboto_28))
        self.message = add_label(self.MESSAGES[outcome], y=70, scr=self)

        if outcome == seedqr.VERIFY_MATCH:
            self.secondary_button, self.primary_button = add_button_pair(
                lv.SYMBOL.LEFT + " Back to QR", on_release(cb_with_args(self.set_value, "redraw")),
                lv.SYMBOL.OK + " Done", on_release(cb_with_args(self.set_value, "done")),
                scr=self,
            )
        elif outcome == seedqr.VERIFY_MISMATCH:
            self.primary_button, self.secondary_button = add_button_pair(
                "Redraw / Try again", on_release(cb_with_args(self.set_value, "redraw")),
                lv.SYMBOL.CLOSE + " Close", on_release(cb_with_args(self.set_value, "done")),
                scr=self,
            )
        else:  # VERIFY_UNREADABLE
            self.primary_button, self.secondary_button = add_button_pair(
                "Try again", on_release(cb_with_args(self.set_value, "rescan")),
                lv.SYMBOL.LEFT + " Back to QR", on_release(cb_with_args(self.set_value, "redraw")),
                scr=self,
            )


async def _verify_seedqr(show, payload, scan_qr):
    """
    Drives the "scan the copy back in" verification loop: explains what's
    about to happen, triggers a scan via the app's existing QR scanner
    infrastructure (`scan_qr`, e.g. Specter.scan_qr -- this never
    reimplements scanning), shows the result, and lets the user retry the
    scan or go back to the transcription viewer.

    The raw scan result is only ever held long enough to classify it (via
    seedqr.verify_scanned_payload) and is never logged, displayed as text,
    or persisted.

    Returns "done" once the user is finished verifying, or "redraw" to
    reopen the transcription viewer.
    """
    proceed = await show(Prompt(
        "Verify your SeedQR",
        "Scan the QR code you just created.",
        confirm_text="Scan",
        cancel_text=lv.SYMBOL.LEFT + " Back",
    ))
    if not proceed:
        return "redraw"

    outcome = seedqr.verify_scanned_payload(payload, await scan_qr())
    while True:
        result = SeedQRVerifyResultScreen(outcome)
        action = await show(result)
        if action == "rescan":
            outcome = seedqr.verify_scanned_payload(payload, await scan_qr())
            continue
        return action  # "redraw" or "done"


async def show_seedqr(show, payload, format_label, scan_qr=None):
    """
    Drives the SeedQR transcription overview <-> zoom navigation loop, plus
    -- if `scan_qr` is provided -- the "Done" transcription-verification
    flow: View/copy QR -> Done -> Scan copied QR -> Verification result.

    `show` is the app's async show_screen callable (e.g. RAMKeyStore.show):
    takes a Screen instance, displays it, and returns its result.

    `scan_qr` is an optional async callable, taking no arguments, that
    triggers a single scan on the app's existing QR scanner host and
    returns the raw decoded bytes (or None/empty if nothing was scanned).
    When it's None, the transcription viewer works exactly as before --
    no "Done"/verify button is shown, only Close.

    Returns once the user closes the viewer (from the overview, a zoomed
    section, or after finishing verification).
    """
    try:
        matrix = seedqr.generate_matrix(payload)
    except ValueError:
        # Covers both seedqr.MatrixError (bad matrix shape) and a ValueError
        # raised by the native QR encoder itself (e.g. encoding failure).
        await show(Alert("Error", "Could not build a SeedQR from this recovery phrase."))
        return

    can_verify = scan_qr is not None
    selected = None
    while True:
        overview = SeedQROverviewScreen(
            matrix, format_label, initial_section=selected, can_verify=can_verify,
        )
        action = await show(overview)
        if action is None:
            return
        if action == "verify":
            outcome = await _verify_seedqr(show, payload, scan_qr)
            if outcome == "done":
                return
            # outcome == "redraw": loop back, reopening the overview
            continue
        zoom = SeedQRZoomScreen(matrix, *action)
        zoom_action, selected = await show(zoom)
        if zoom_action == "close":
            return
        # zoom_action == "overview": loop back, reopening the overview at `selected`
