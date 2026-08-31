"""
Regression tests for issue #393 - Taproot wallet creation must use BIP86
(m/86') while keeping the non-standard "legacy Specter Taproot" m/84' + tr()
wallets explicitly recoverable.
"""
import sys

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs

    setup_native_stubs()

import asyncio
import gc
from binascii import hexlify
from io import BytesIO
from unittest import TestCase

from embit import bip32
from embit.descriptor import Descriptor
from embit.networks import NETWORKS

from tests.util import get_keystore, get_xpubs_app, clear_testdir

# Official BIP86 test vector (mnemonic + first receive address).
BIP86_MNEMONIC = "abandon " * 11 + "about"
BIP86_FIRST_ADDR = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ScriptedScreens:
    """Scriptable show_screen double. Answers keyed by screen class name."""

    def __init__(self, answers):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.log = []

    async def __call__(self, screen):
        name = type(screen).__name__
        self.log.append(name)
        queue = self.answers.get(name)
        if queue:
            return queue.pop(0)
        return None


class WalletSink:
    """Captures `addwallet` descriptors sent to the wallets app."""

    def __init__(self, existing=None):
        self.existing = existing or []
        self.added = []

    async def __call__(self, stream, app=None):
        data = stream.read()
        if data == b"listwallets":
            import json
            return BytesIO(json.dumps(self.existing).encode()), {}
        text = data.decode()
        assert text.startswith("addwallet "), text
        name, desc = text[len("addwallet "):].split("&", 1)
        self.added.append((name, desc))
        return BytesIO(b""), {}


def _shown_xpub(app, derivation):
    """Reproduce what show_xpub() hands to create_wallet()."""
    net = NETWORKS[app.network]
    x = app.keystore.get_xpub(derivation)
    canonical = x.to_base58(net["xpub"])
    fp = hexlify(app.keystore.fingerprint).decode()
    prefix = "[%s%s]" % (fp, derivation[1:])
    return derivation, canonical, prefix


def _expected_descriptor(app, derivation, script_tmpl):
    net = NETWORKS[app.network]
    x = app.keystore.get_xpub(derivation)
    fp = hexlify(app.keystore.fingerprint).decode()
    prefix = "[%s/%s]" % (fp, derivation[2:])
    return script_tmpl % (prefix, x.to_base58(net["xpub"]))


def _address(descriptor, net_name, branch, index):
    net = NETWORKS[net_name]
    d = Descriptor.from_string(descriptor)
    return d.derive(index, branch_index=branch).address(net)


class TaprootWalletCreationTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore()
        self.app = get_xpubs_app(self.keystore, "main")

    def tearDown(self):
        clear_testdir()
        gc.collect()

    # -- helpers ----------------------------------------------------------
    def _create(self, derivation, answers, network="main", account=0,
                keystore=None):
        if keystore is not None:
            self.keystore = keystore
        self.app = get_xpubs_app(self.keystore, network)
        self.app.account = account
        sink = WalletSink()
        self.app.communicate = sink
        screens = ScriptedScreens(answers)
        d, x, p = _shown_xpub(self.app, derivation)
        run(self.app.create_wallet(d, x, p, screens))
        return sink, screens

    # -- A: BIP86 standard path -----------------------------------------
    def test_standard_taproot_uses_bip86(self):
        sink, _ = self._create(
            "m/86h/0h/0h",
            {"Menu": ["taproot"], "InputScreen": ["TR"]},
        )
        self.assertEqual(len(sink.added), 1)
        name, desc = sink.added[0]
        self.assertIn("/86h/0h/0h]", desc)
        self.assertNotIn("/84h/", desc)
        self.assertTrue(desc.startswith("tr("))
        self.assertEqual(
            desc, _expected_descriptor(self.app, "m/86h/0h/0h", "tr(%s%s/{0,1}/*)")
        )

    # -- B: official BIP86 test vector ---------------------------------
    def test_bip86_official_vector(self):
        ks = get_keystore(mnemonic=BIP86_MNEMONIC)
        sink, screens = self._create(
            "m/84h/0h/0h",
            {"Menu": ["taproot", "standard"], "InputScreen": ["v"]},
            keystore=ks,
        )
        _, desc = sink.added[0]
        self.assertIn("/86h/0h/0h]", desc)
        self.assertEqual(_address(desc, "main", 0, 0), BIP86_FIRST_ADDR)

    # -- C: legacy Specter regression --------------------------------
    def test_legacy_specter_taproot_recovery_reproduces_m84_address(self):
        sink, _ = self._create(
            "m/84h/0h/0h",
            {"Menu": ["taproot", "legacy"], "Prompt": [True],
             "InputScreen": ["legacy"]},
        )
        _, desc = sink.added[0]
        self.assertIn("/84h/0h/0h]", desc)
        self.assertTrue(desc.startswith("tr("))
        expected = _expected_descriptor(
            self.app, "m/84h/0h/0h", "tr(%s%s/{0,1}/*)"
        )
        self.assertEqual(desc, expected)
        # address matches an independent m/84'/0'/0'/0/0 P2TR derivation
        indep = _address(expected, "main", 0, 0)
        self.assertEqual(_address(desc, "main", 0, 0), indep)

    def test_legacy_taproot_menu_entry_without_migration(self):
        sink, _ = self._create(
            "m/86h/0h/0h",
            {"Menu": ["legacy_taproot"], "Prompt": [True],
             "InputScreen": ["l"]},
        )
        _, desc = sink.added[0]
        self.assertIn("/84h/0h/0h]", desc)
        self.assertEqual(
            desc, _expected_descriptor(self.app, "m/84h/0h/0h", "tr(%s%s/{0,1}/*)")
        )

    # -- D: standard and legacy differ ------------------------------
    def test_standard_and_legacy_addresses_differ(self):
        std = self._create(
            "m/86h/0h/0h", {"Menu": ["taproot"], "InputScreen": ["s"]}
        )[0].added[0][1]
        leg = self._create(
            "m/84h/0h/0h",
            {"Menu": ["taproot", "legacy"], "Prompt": [True],
             "InputScreen": ["l"]},
        )[0].added[0][1]
        self.assertNotEqual(
            _address(std, "main", 0, 0), _address(leg, "main", 0, 0)
        )

    # -- E: change addresses ---------------------------------------
    def test_change_branch_addresses(self):
        for der, answers in (
            ("m/86h/0h/0h", {"Menu": ["taproot"], "InputScreen": ["x"]}),
            ("m/84h/0h/0h",
             {"Menu": ["taproot", "legacy"], "Prompt": [True],
              "InputScreen": ["x"]}),
        ):
            sink, _ = self._create(der, answers)
            _, desc = sink.added[0]
            recv = _address(desc, "main", 0, 0)
            chg = _address(desc, "main", 1, 0)
            self.assertNotEqual(recv, chg)
            self.assertEqual(
                recv, _address(
                    _expected_descriptor(self.app, der, "tr(%s%s/{0,1}/*)"),
                    "main", 0, 0),
            )
            self.assertEqual(
                chg, _address(
                    _expected_descriptor(self.app, der, "tr(%s%s/{0,1}/*)"),
                    "main", 1, 0),
            )

    # -- F: account numbers --------------------------------------
    def test_non_zero_account(self):
        sink, _ = self._create(
            "m/86h/0h/5h",
            {"Menu": ["taproot"], "InputScreen": ["a5"]},
            account=5,
        )
        _, desc = sink.added[0]
        self.assertIn("/86h/0h/5h]", desc)

        sink, _ = self._create(
            "m/84h/0h/5h",
            {"Menu": ["taproot", "legacy"], "Prompt": [True],
             "InputScreen": ["a5"]},
            account=5,
        )
        _, desc = sink.added[0]
        self.assertIn("/84h/0h/5h]", desc)

    # -- G: testnet --------------------------------------------
    def test_testnet_derivation(self):
        sink, _ = self._create(
            "m/86h/1h/0h",
            {"Menu": ["taproot"], "InputScreen": ["t"]},
            network="test",
        )
        _, desc = sink.added[0]
        self.assertIn("/86h/1h/0h]", desc)

        sink, _ = self._create(
            "m/84h/1h/0h",
            {"Menu": ["taproot", "legacy"], "Prompt": [True],
             "InputScreen": ["t"]},
            network="test",
        )
        _, desc = sink.added[0]
        self.assertIn("/84h/1h/0h]", desc)

    # -- H: UI / menu behaviour --------------------------------
    def test_taproot_from_m84_shows_migration_choice(self):
        sink, screens = self._create(
            "m/84h/0h/0h",
            {"Menu": ["taproot", "standard"], "InputScreen": ["m"]},
        )
        # two Menu screens: wallet-type menu, then the migration choice
        self.assertEqual(screens.log.count("Menu"), 2)
        _, desc = sink.added[0]
        self.assertIn("/86h/0h/0h]", desc)
        # the m/86' key is actually re-derived, not the displayed m/84' one
        net = NETWORKS["main"]
        m86 = self.keystore.get_xpub("m/86h/0h/0h").to_base58(net["xpub"])
        m84 = self.keystore.get_xpub("m/84h/0h/0h").to_base58(net["xpub"])
        self.assertIn(m86, desc)
        self.assertNotIn(m84, desc)

    def test_migration_cancel_creates_no_wallet(self):
        sink, _ = self._create(
            "m/84h/0h/0h",
            {"Menu": ["taproot", 255]},
        )
        self.assertEqual(sink.added, [])

    def test_wallet_type_menu_back_creates_no_wallet(self):
        sink, _ = self._create("m/86h/0h/0h", {"Menu": [255]})
        self.assertEqual(sink.added, [])

    def test_legacy_prompt_decline_creates_no_wallet(self):
        sink, _ = self._create(
            "m/86h/0h/0h",
            {"Menu": ["legacy_taproot"], "Prompt": [False]},
        )
        self.assertEqual(sink.added, [])

    def test_native_segwit_still_bip84(self):
        sink, _ = self._create(
            "m/84h/0h/0h", {"Menu": ["wpkh"], "InputScreen": ["n"]}
        )
        _, desc = sink.added[0]
        self.assertTrue(desc.startswith("wpkh("))
        self.assertIn("/84h/0h/0h]", desc)

    def test_taproot_rederived_from_legacy_context_for_standard(self):
        # from m/49' nested context, picking Taproot re-derives m/86'
        sink, _ = self._create(
            "m/49h/0h/0h", {"Menu": ["taproot"], "InputScreen": ["x"]}
        )
        _, desc = sink.added[0]
        self.assertIn("/86h/0h/0h]", desc)
        self.assertTrue(desc.startswith("tr("))


class LegacyDescriptorImportTest(TestCase):
    """Old-style tr([fpr/84h/..]xpub/..) descriptors must still import."""

    def setUp(self):
        clear_testdir()
        from tests.util import get_wallets_app
        self.keystore = get_keystore()
        self.wallets_app = get_wallets_app(self.keystore, "main")
        self.manager = self.wallets_app.manager

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def test_legacy_m84_taproot_descriptor_parses(self):
        net = NETWORKS["main"]
        der = "m/84h/0h/0h"
        x = self.keystore.get_xpub(der).to_base58(net["xpub"])
        fp = hexlify(self.keystore.fingerprint).decode()
        desc = "Legacy TR&tr([%s/84h/0h/0h]%s/{0,1}/*)" % (fp, x)
        wallet = self.manager.parse_wallet(desc)
        self.assertEqual(wallet.name, "Legacy TR")
        self.assertIn("tr(", str(wallet.descriptor))
        self.assertIn("84h/0h/0h", str(wallet.descriptor))
