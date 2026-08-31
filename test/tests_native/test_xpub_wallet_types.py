import sys

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs

    setup_native_stubs()

import asyncio
import gc
from unittest import TestCase

from tests.util import get_keystore, clear_testdir

import apps.xpubs.xpubs as xpubs_module
from apps.xpubs.xpubs import XpubApp
from embit.liquid.networks import NETWORKS


class _FakeMenu:
    """Captures the buttons it is constructed with instead of building GUI."""

    def __init__(self, buttons, *args, **kwargs):
        self.buttons = buttons


def _offered_descriptors(derivation, network="main"):
    """Run create_wallet up to the menu and return the descriptor strings
    that would be offered to the user (recommended + other)."""
    net = NETWORKS[network]

    app = XpubApp(None)
    app.account = 0
    app.network = network
    app.keystore = get_keystore()

    captured = {}

    async def show_screen(screen):
        captured["buttons"] = screen.buttons
        return 255  # back -> abort before any communicate()

    prefix = "[00000000%s]" % derivation[1:]
    version = net["xpub"]

    real_menu = xpubs_module.Menu
    xpubs_module.Menu = _FakeMenu
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            app.create_wallet(derivation, "xpub", prefix, version, show_screen)
        )
    finally:
        loop.close()
        xpubs_module.Menu = real_menu

    return [b[0] for b in captured["buttons"] if b[0] is not None]


class XpubPurposeTest(TestCase):
    def test_purpose_reads_first_path_component(self):
        self.assertEqual(XpubApp._purpose("m/86h/0h/0h"), 86)
        self.assertEqual(XpubApp._purpose("m/84h/0h/0h"), 84)
        self.assertEqual(XpubApp._purpose("m/49h/0h/0h"), 49)
        self.assertEqual(XpubApp._purpose("m/44h/0h/0h"), 44)
        self.assertEqual(XpubApp._purpose("m/48h/0h/0h/2h"), 48)

    def test_purpose_is_not_fooled_by_later_components(self):
        # the bug this fix addresses: 86h anywhere but the first position
        self.assertEqual(XpubApp._purpose("m/44h/86h/0h"), 44)
        self.assertEqual(XpubApp._purpose("m/84h/86h/0h"), 84)

    def test_purpose_none_for_bad_or_soft_paths(self):
        self.assertIsNone(XpubApp._purpose("m"))
        self.assertIsNone(XpubApp._purpose("m/86/0/0"))  # not hardened
        self.assertIsNone(XpubApp._purpose("not a path"))


class XpubWalletTypeOfferTest(TestCase):
    def setUp(self):
        clear_testdir()

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def _assert_taproot(self, derivation, expected):
        offered = _offered_descriptors(derivation)
        has_taproot = any(d.startswith("tr(") for d in offered)
        self.assertEqual(has_taproot, expected, "%s -> %s" % (derivation, offered))

    def test_taproot_offered_only_for_bip86(self):
        self._assert_taproot("m/86h/0h/0h", True)
        self._assert_taproot("m/84h/0h/0h", False)
        self._assert_taproot("m/49h/0h/0h", False)
        self._assert_taproot("m/44h/0h/0h", False)
        self._assert_taproot("m/48h/0h/0h/2h", False)

    def test_taproot_not_offered_for_non_bip86_paths_containing_86h(self):
        self._assert_taproot("m/44h/86h/0h", False)
        self._assert_taproot("m/84h/86h/0h", False)

    def test_bip86_recommends_taproot(self):
        offered = _offered_descriptors("m/86h/0h/0h")
        self.assertTrue(offered[0].startswith("tr("), offered)
