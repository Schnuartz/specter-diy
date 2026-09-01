"""Issue #326 end-to-end reproduction using a REAL BlueWallet v7.2.2 PSBT.

The PSBT in ``BW_PSBT_B64`` was produced by BlueWallet v7.2.2 code
(tag v7.2.2, commit 839a1a6595f3115c5e5fa4cf63aca5556c81202b) in
``tests/unit/issue326-repro.test.ts`` of a v7.2.2 checkout, by:

  1. deriving m/86'/0'/0' from the Specter native-test seed
     ("ability "*11 + "acid"),
  2. feeding BlueWallet's watch-only import the exact string Specter's
     "Master Public Keys -> Show more keys -> Single Taproot" QR encodes:
     ``[fb7c1f11/86h/0h/0h]xpub6D8vj8g...`` ,
  3. calling ``WatchOnlyWallet.createTransaction()`` (the external-signer
     path) for a 1-in / 2-out spend with BIP86 change.

BlueWallet wrote **fb7c1f11** (the real Specter fingerprint), NOT
``00000000``, into both the input and the change-output
``tapBip32Derivation`` - see the BlueWallet-side report in that test.

This test drives Specter's real ``WalletManager.preprocess_psbt`` pipeline
with that PSBT and prints what Specter concludes.
"""
import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase
from io import BytesIO
import gc

from tests.util import get_keystore, get_wallets_app, clear_testdir
from embit.networks import NETWORKS

def _load_fixture():
    import os
    here = os.path.dirname(__file__)
    path = os.path.join(here, "..", "fixtures", "bluewallet-7.2.2-issue-326.psbt")
    with open(path) as f:
        return f.read().strip()


# Real BlueWallet v7.2.2 output (see module docstring / fixture .md).
BW_PSBT_B64 = _load_fixture()

MNEMONIC = "ability " * 11 + "acid"


class BlueWalletTaprootRecognition(TestCase):
    NETWORK = "main"

    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore(MNEMONIC)
        self.wallets_app = get_wallets_app(self.keystore, self.NETWORK)
        self.manager = self.wallets_app.manager
        self.net = NETWORKS[self.NETWORK]

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def _import_specter_taproot_wallet(self):
        fp = self.keystore.fingerprint.hex()
        xpub = self.keystore.get_xpub("m/86h/0h/0h").to_base58(self.net["xpub"])
        desc = "tr([%s/86h/0h/0h]%s/{0,1}/*)" % (fp, xpub)
        w = self.manager.WalletClass.parse("BW Taproot 0&" + desc)
        self.manager.add_wallet(w)
        return w, desc

    def _run(self):
        raw = _b64decode(BW_PSBT_B64)
        return self.manager.preprocess_psbt(BytesIO(raw), BytesIO())

    def test_report(self):
        w, desc = self._import_specter_taproot_wallet()
        print("\n--- imported Specter wallet descriptor ---")
        print(desc)
        wallets, meta = self._run()
        none_present = None in wallets
        print("--- Specter preprocess_psbt result (network=%s) ---" % self.NETWORK)
        print("wallets:", {(k.name if k else None): v for k, v in wallets.items()})
        print("None in wallets (=> 'Unknown wallet in inputs'):", none_present)
        for i, mi in enumerate(meta["inputs"]):
            print("  input %d:" % i, {k: mi[k] for k in ("label", "value") if k in mi})
        for i, mo in enumerate(meta["outputs"]):
            print("  output %d:" % i,
                  {k: mo.get(k) for k in ("label", "value", "change", "address", "warning")})
        # This is the assertion that matters for issue #326:
        self.assertNotIn(
            None, wallets,
            "Specter reports 'Unknown wallet in inputs' for a REAL BlueWallet "
            "v7.2.2 BIP86 PSBT whose derivations carry the correct fingerprint",
        )
        # change output (index 1) must be recognised as change, recipient (0) must not
        self.assertFalse(meta["outputs"][0]["change"])
        self.assertTrue(meta["outputs"][1]["change"])


class BlueWalletTaprootNotImported(TestCase):
    """The issue #326 STR never imports the wallet descriptor into Specter.
    In that case 'Unknown wallet in inputs' is CORRECT, and signing still works.
    """
    NETWORK = "main"

    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore(MNEMONIC)
        self.wallets_app = get_wallets_app(self.keystore, self.NETWORK)
        self.manager = self.wallets_app.manager

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def test_unknown_without_import_but_signs(self):
        raw = _b64decode(BW_PSBT_B64)
        pre = BytesIO()
        wallets, meta = self.manager.preprocess_psbt(BytesIO(raw), pre)
        print("\n[no wallet imported] None in wallets:", None in wallets,
              "| input label:", meta["inputs"][0]["label"])
        self.assertIn(None, wallets)   # correct: nothing owns it
        # signing must still succeed (issue: "possible to skip the screen and still sign it")
        pre.seek(0)
        psbtv = self.manager.PSBTViewClass.view(pre, compress=True)
        out = BytesIO()
        self.manager.sign_psbtview(psbtv, out, wallets, None)  # raises WalletError if 0 sigs
        from embit.psbt import PSBT
        signed = PSBT.parse(out.getvalue())
        tap_sig = signed.inputs[0].final_scriptwitness or getattr(
            signed.inputs[0], "taproot_key_sig", None)
        print("[no wallet imported] input0 taproot key sig present:", bool(tap_sig))
        self.assertTrue(tap_sig)


def _b64decode(s):
    try:
        from binascii import a2b_base64
        return a2b_base64(s)
    except Exception:
        import base64
        return base64.b64decode(s)
