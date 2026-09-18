import lvgl as lv
from .prompt import Prompt
from ..common import add_label, add_button
from ..decorators import on_release, cb_with_args
from .screen import Screen


class SettingsMenu(Screen):
    """Playground-style settings page for the legacy LVGL renderer.

    The playground presents a status card, a Device section and a Security
    section.  This screen keeps that topology while using the older LVGL
    primitives available in the firmware build.
    """

    def __init__(self, interfaces=None, has_sd=False, has_smartcard=False,
                 battery_available=False, can_lock=True):
        super().__init__()
        self.interfaces = interfaces or []
        self.title = add_label("Manage Settings", scr=self, style="title")
        self._add_interface_card()

        y = 170
        self._add_section("Device", y)
        y += 30
        self._add_info_row("Power", "Available" if battery_available else "Unavailable", y)
        y += 50
        if can_lock:
            self._add_nav_row(8, "Lock device", "LOCK", y)
            y += 50
        self._add_nav_row(4, "Manage Interfaces", "IO", y)
        y += 50
        if has_sd:
            self._add_nav_row(1, "SD Card", "SD", y)
            y += 50
        if has_smartcard:
            self._add_nav_row(1, "Smartcard", "SC", y)
            y += 50
        self._add_nav_row(3, "Language", "A", y)
        y += 50
        self._add_nav_row(2, "Theme", "◐", y)
        y += 55

        self._add_section("Security", y)
        y += 25
        self._add_nav_row(0, "Security Settings", lv.SYMBOL.SETTINGS, y)
        self.add_back_button(255)

    def _add_interface_card(self):
        card = lv.obj(self)
        card.set_size(440, 84)
        card.set_pos(20, 65)
        style = lv.style_t()
        lv.style_copy(style, self.title.get_style(0))
        style.body.main_color = lv.color_hex(0x263544)
        style.body.grad_color = style.body.main_color
        style.body.opa = 255
        style.body.radius = 10
        style.body.border.width = 0
        card.set_style(style)

        if not self.interfaces:
            labels = [("IO", True)]
        else:
            labels = self.interfaces
        step = 440 // len(labels)
        for i, item in enumerate(labels):
            label, active = item
            lbl = lv.label(card)
            lbl.set_text(label)
            lbl.set_width(step)
            lbl.set_align(lv.label.ALIGN.CENTER)
            lbl.set_x(i * step)
            lbl.set_y(25)
            item_style = lv.style_t()
            lv.style_copy(item_style, self.title.get_style(0))
            item_style.text.font = lv.font_roboto_16
            item_style.text.color = lv.color_hex(0x20D060 if active else 0x708092)
            lbl.set_style(0, item_style)

    def _add_section(self, text, y):
        add_label(text.upper(), y=y, scr=self, style="hint")

    def _button_style(self, btn):
        style = lv.style_t()
        lv.style_copy(style, btn.get_style(lv.btn.STYLE.REL))
        style.body.main_color = lv.color_hex(0x263544)
        style.body.grad_color = style.body.main_color
        style.body.radius = 10
        style.body.border.width = 0
        style.body.shadow.width = 0
        btn.set_style(lv.btn.STYLE.REL, style)

    def _add_info_row(self, text, status, y):
        btn = add_button(text + "    " + status, scr=self, y=y)
        btn.set_height(44)
        self._button_style(btn)

    def _add_nav_row(self, value, text, icon, y):
        btn = add_button(scr=self, y=y)
        btn.set_height(44)
        self._button_style(btn)
        lbl = lv.label(btn)
        lbl.set_text("%s   %s                                      %s" % (icon, text, lv.SYMBOL.RIGHT))
        lbl.set_align(lv.label.ALIGN.CENTER)
        lbl.set_width(420)
        lbl.set_x(10)
        lbl.set_y(9)
        btn.set_event_cb(on_release(cb_with_args(self.set_value, value)))

    def add_back_button(self, value):
        add_button(lv.SYMBOL.LEFT + " Back", on_release(cb_with_args(self.set_value, value)), scr=self)

class HostSettings(Prompt):
    def __init__(self, controls, title="Host setttings", note=None, controls_empty_text="No settings available"):
        super().__init__(title, "")
        y = 40
        if note is not None:
            self.note = add_label(note, style="hint", scr=self)
            self.note.align(self.title, lv.ALIGN.OUT_BOTTOM_MID, 0, 5)
            y += self.note.get_height()
        self.controls = controls
        self.switches = []
        for control in controls:
            label = add_label(control["label"], y, scr=self.page)
            hint = add_label(
                control.get("hint", ""),
                y + 30,
                scr=self.page,
                style="hint",
            )
            switch = lv.sw(self.page)
            switch.align(hint, lv.ALIGN.OUT_BOTTOM_MID, 0, 10)
            lbl = add_label(" OFF                              ON  ", scr=self.page)
            lbl.align(switch, lv.ALIGN.CENTER, 0, 0)
            if control.get("value", False):
                switch.on(lv.ANIM.OFF)
            self.switches.append(switch)
            y = lbl.get_y() + 80
        self.next_y = y
        if not controls:
            label = add_label(controls_empty_text, y, scr=self.page)
            self.next_y = label.get_y() + label.get_height() + 40
        self.confirm_button.set_event_cb(on_release(self.update))
        self.cancel_button.set_event_cb(on_release(lambda: self.set_value(None)))

    def update(self):
        self.set_value([switch.get_state() for switch in self.switches])

class DevSettings(Prompt):
    def __init__(self, dev=False, usb=False, note=None):
        super().__init__("Device settings", "")
        if note is not None:
            self.note = add_label(note, style="hint", scr=self)
            self.note.align(self.title, lv.ALIGN.OUT_BOTTOM_MID, 0, 5)
        y = 70
        usb_label = add_label("USB communication", y, scr=self.page)
        usb_hint = add_label(
            "If USB is enabled the device will be able "
            "to talk to your computer. This increases "
            "attack surface but sometimes makes it "
            "more convenient to use.",
            y + 40,
            scr=self.page,
            style="hint",
        )
        self.usb_switch = lv.sw(self.page)
        self.usb_switch.align(usb_hint, lv.ALIGN.OUT_BOTTOM_MID, 0, 20)
        lbl = add_label(" OFF                              ON  ", scr=self.page)
        lbl.align(self.usb_switch, lv.ALIGN.CENTER, 0, 0)
        if usb:
            self.usb_switch.on(lv.ANIM.OFF)

        # y += 200
        # dev_label = add_label("Developer mode", y, scr=self.page)
        # dev_hint = add_label(
        #     "In developer mode internal flash will "
        #     "be mounted to your computer so you could "
        #     "edit files, but your secrets will be visible as well. "
        #     "Also enables interactive shell through miniUSB port.",
        #     y + 40,
        #     scr=self.page,
        #     style="hint",
        # )
        # self.dev_switch = lv.sw(self.page)
        # self.dev_switch.align(dev_hint, lv.ALIGN.OUT_BOTTOM_MID, 0, 20)
        # lbl = add_label(" OFF                              ON  ", scr=self.page)
        # lbl.align(self.dev_switch, lv.ALIGN.CENTER, 0, 0)
        # if dev:
        #     self.dev_switch.on(lv.ANIM.OFF)
        self.confirm_button.set_event_cb(on_release(self.update))
        self.cancel_button.set_event_cb(on_release(lambda: self.set_value(None)))

        self.wipebtn = add_button(
            lv.SYMBOL.TRASH + " Wipe device", on_release(self.wipe), scr=self
        )
        self.wipebtn.align(self, lv.ALIGN.IN_BOTTOM_MID, 0, -140)
        style = lv.style_t()
        lv.style_copy(style, self.wipebtn.get_style(lv.btn.STYLE.REL))
        style.body.main_color = lv.color_hex(0x951E2D)
        style.body.grad_color = lv.color_hex(0x951E2D)
        self.wipebtn.set_style(lv.btn.STYLE.REL, style)

    def wipe(self):
        self.set_value(
            {
                "dev": False, # self.dev_switch.get_state(),
                "usb": self.usb_switch.get_state(),
                "wipe": True,
            }
        )

    def update(self):
        self.set_value(
            {
                "dev": False, # self.dev_switch.get_state(),
                "usb": self.usb_switch.get_state(),
                "wipe": False,
            }
        )
