"""Defensive tests for the taproot zero-master-fingerprint shim.

``WalletManager.fill_zero_fingerprint()`` normalises an all-zero master
fingerprint (``00000000``) that a coordinator may write into a PSBT: it
re-derives the key from the local seed and, only if it matches, replaces the
zero fingerprint with the real one so ``Descriptor.owns()`` can recognise the
wallet.  Historically it only inspected the legacy ``bip32_derivations`` map;
this change applies the identical authenticated repair to
``taproot_bip32_derivations`` (BIP-371), keeping the two maps symmetric.

Note: BlueWallet v7.2.2-v7.2.6 does *not* actually emit a zero fingerprint for
an imported BIP86 wallet - see ``test_bluewallet_taproot_recognition.py`` and
``test/fixtures/bluewallet-7.2.2-issue-326.psbt`` for the real output, which
carries the correct fingerprint and is already recognised without this change.
These tests therefore lock down the shim's contract in the abstract: an
authenticated zero fingerprint is repaired, everything else stays "unknown".

All tests drive the real ``WalletManager.preprocess_psbt`` pipeline; the
zero-fingerprint condition is injected directly into the PSBT scope.
"""
import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase
from io import BytesIO
import gc

from tests.util import get_keystore, get_wallets_app, clear_testdir

from embit import bip32, script
from embit.descriptor import Descriptor
from embit.psbt import PSBT, DerivationPath
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from embit.networks import NETWORKS
import embit.ec as ec

ZERO_FP = b"\x00\x00\x00\x00"
OTHER_MNEMONIC = "test " * 11 + "junk"   # BIP39 test vector, unrelated seed
EXTERNAL_ADDR = "bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"


def _bip86_descriptor(keystore, net, account=0, coin=1):
    fp = keystore.fingerprint.hex()
    path = "m/86h/%dh/%dh" % (coin, account)
    xpub = keystore.get_xpub(path).to_base58(net["xpub"])
    return "tr([%s/86h/%dh/%dh]%s/{0,1}/*)" % (fp, coin, account, xpub)


def _leaf(desc_str, idx, branch):
    d = Descriptor.from_string(desc_str.split("#")[0])
    leaf = d.derive(idx, branch_index=branch)
    return leaf.script_pubkey(), leaf.keys[0].get_public_key()


class TaprootZeroFpShimBase(TestCase):
    NETWORK = "regtest"

    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore()
        self.wallets_app = get_wallets_app(self.keystore, self.NETWORK)
        self.manager = self.wallets_app.manager
        self.net = NETWORKS[self.NETWORK]

    def tearDown(self):
        clear_testdir()
        gc.collect()

    # ------------------------------------------------------------------ helpers
    def _import(self, desc_str, name="BW"):
        w = self.manager.WalletClass.parse(name + "&" + desc_str)
        self.manager.add_wallet(w)
        return w

    def _taproot_psbt(
        self,
        input_specs,
        outputs,
        change_spec=None,
    ):
        """input_specs: list of (script_pubkey, xonly_pub, fingerprint, path_list, value)
        outputs: list of (script_pubkey, value)
        change_spec: (script_pubkey, xonly_pub, fingerprint, path_list, value) or None
        """
        vin = [
            TransactionInput(bytes([0x11]) * 31 + bytes([i]), 0)
            for i in range(len(input_specs))
        ]
        vout = [TransactionOutput(v, spk) for spk, v in outputs]
        if change_spec is not None:
            vout.append(TransactionOutput(change_spec[4], change_spec[0]))
        tx = Transaction(version=2, locktime=0, vin=vin, vout=vout)
        psbt = PSBT(tx)
        for i, (spk, pub, fp, path, value) in enumerate(input_specs):
            inp = psbt.inputs[i]
            inp.witness_utxo = TransactionOutput(value, spk)
            inp.taproot_bip32_derivations[pub] = ([], DerivationPath(fp, path))
            inp.taproot_internal_key = pub
        if change_spec is not None:
            spk, pub, fp, path, value = change_spec
            out = psbt.outputs[len(outputs)]
            out.taproot_bip32_derivations[pub] = ([], DerivationPath(fp, path))
            out.taproot_internal_key = pub
        return psbt

    def _run(self, psbt):
        raw = psbt.serialize()
        return self.manager.preprocess_psbt(BytesIO(raw), BytesIO())


class TaprootZeroFpShimRecognitionTest(TaprootZeroFpShimBase):
    """An authenticated zero fingerprint in a tap derivation is repaired so the
    imported wallet (and its change output) is recognised - the same behaviour
    the legacy ``bip32_derivations`` shim already provides."""

    def _single_input_psbt(self, fp, account=0):
        desc = _bip86_descriptor(self.keystore, self.net, account=account)
        self._import(desc)
        recv_spk, recv_pub = _leaf(desc, 0, 0)
        chg_spk, chg_pub = _leaf(desc, 0, 1)
        return self._taproot_psbt(
            input_specs=[
                (recv_spk, recv_pub, fp,
                 bip32.parse_path("m/86h/1h/%dh/0/0" % account), 100000)
            ],
            outputs=[(script.address_to_scriptpubkey(EXTERNAL_ADDR), 60000)],
            change_spec=(chg_spk, chg_pub, fp,
                         bip32.parse_path("m/86h/1h/%dh/1/0" % account), 35000),
        )

    def test_authenticated_zero_fingerprint_input_is_recognized(self):
        wallets, meta = self._run(self._single_input_psbt(ZERO_FP))
        # confirm_wallets() shows "Unknown wallet in inputs" iff None in wallets
        self.assertNotIn(None, wallets)
        self.assertEqual(meta["inputs"][0]["label"], "BW")

    def test_authenticated_zero_fingerprint_change_is_verified(self):
        wallets, meta = self._run(self._single_input_psbt(ZERO_FP))
        self.assertFalse(meta["outputs"][0]["change"])   # external recipient
        self.assertTrue(meta["outputs"][1]["change"])    # BIP86 change

    def test_standard_fingerprint_still_recognized(self):
        wallets, meta = self._run(self._single_input_psbt(self.keystore.fingerprint))
        self.assertNotIn(None, wallets)
        self.assertEqual(meta["inputs"][0]["label"], "BW")
        self.assertTrue(meta["outputs"][1]["change"])

    def test_non_zero_account_zero_fingerprint(self):
        wallets, meta = self._run(self._single_input_psbt(ZERO_FP, account=5))
        self.assertNotIn(None, wallets)
        self.assertTrue(meta["outputs"][1]["change"])

    def test_multiple_indexes_zero_fingerprint(self):
        desc = _bip86_descriptor(self.keystore, self.net)
        self._import(desc)
        specs = []
        for branch, idx in ((0, 0), (0, 7)):
            spk, pub = _leaf(desc, idx, branch)
            specs.append((spk, pub, ZERO_FP,
                          bip32.parse_path("m/86h/1h/0h/%d/%d" % (branch, idx)),
                          40000))
        chg_spk, chg_pub = _leaf(desc, 3, 1)
        psbt = self._taproot_psbt(
            input_specs=specs,
            outputs=[(script.address_to_scriptpubkey(EXTERNAL_ADDR), 50000)],
            change_spec=(chg_spk, chg_pub, ZERO_FP,
                         bip32.parse_path("m/86h/1h/0h/1/3"), 25000),
        )
        wallets, meta = self._run(psbt)
        self.assertNotIn(None, wallets)
        self.assertEqual(len([w for w in wallets if w is not None]), 1)
        self.assertEqual(sum(v["amount"] for v in wallets.values()), 80000)
        self.assertTrue(meta["outputs"][1]["change"])


class TaprootZeroFpShimSecurityTest(TaprootZeroFpShimBase):
    """A zero fingerprint is untrusted input - it must be authenticated against a
    locally derived key before it is honoured.  None of these must be recognised.
    """

    def setUp(self):
        super().setUp()
        self.desc = _bip86_descriptor(self.keystore, self.net)
        self._import(self.desc)
        self.recv_spk, self.recv_pub = _leaf(self.desc, 0, 0)

    def _one(self, spk, pub, fp, path):
        psbt = self._taproot_psbt(
            input_specs=[(spk, pub, fp, path, 100000)],
            outputs=[(script.address_to_scriptpubkey(EXTERNAL_ADDR), 90000)],
        )
        return self._run(psbt)

    def test_zero_fp_wrong_derivation_not_matched(self):
        wallets, _ = self._one(self.recv_spk, self.recv_pub, ZERO_FP,
                               bip32.parse_path("m/86h/1h/0h/0/9"))
        self.assertIn(None, wallets)

    def test_zero_fp_wrong_account_not_matched(self):
        # claim a path in a different account while presenting our real 0/0 key
        wallets, _ = self._one(self.recv_spk, self.recv_pub, ZERO_FP,
                               bip32.parse_path("m/86h/1h/9h/0/0"))
        self.assertIn(None, wallets)

    def test_zero_fp_attacker_pubkey_not_matched(self):
        att = ec.PrivateKey(b"\x11" * 32).get_public_key()
        att_spk = script.p2tr(att)
        wallets, _ = self._one(att_spk, att, ZERO_FP,
                               bip32.parse_path("m/86h/1h/0h/0/0"))
        self.assertIn(None, wallets)

    def test_zero_fp_attacker_scriptpubkey_not_matched(self):
        # correct key material in the derivation map, but the witness utxo pays
        # an unrelated script
        att_spk = script.p2tr(ec.PrivateKey(b"\x22" * 32).get_public_key())
        wallets, _ = self._one(att_spk, self.recv_pub, ZERO_FP,
                               bip32.parse_path("m/86h/1h/0h/0/0"))
        self.assertIn(None, wallets)

    def test_non_zero_wrong_fingerprint_not_silently_repaired(self):
        wallets, _ = self._one(self.recv_spk, self.recv_pub, b"\xde\xad\xbe\xef",
                               bip32.parse_path("m/86h/1h/0h/0/0"))
        self.assertIn(None, wallets)

    def test_different_seed_not_matched(self):
        other = get_keystore(mnemonic=OTHER_MNEMONIC)
        opath = "m/86h/1h/0h"
        oxpub = other.get_xpub(opath).to_base58(self.net["xpub"])
        odesc = "tr([%s/86h/1h/0h]%s/{0,1}/*)" % (other.fingerprint.hex(), oxpub)
        ospk, opub = _leaf(odesc, 0, 0)
        wallets, _ = self._one(ospk, opub, ZERO_FP, bip32.parse_path("m/86h/1h/0h/0/0"))
        self.assertIn(None, wallets)

    def test_genuine_unknown_wallet_still_warns(self):
        # a valid taproot input for a wallet we never imported
        other = get_keystore(mnemonic=OTHER_MNEMONIC)
        oxpub = other.get_xpub("m/86h/1h/0h").to_base58(self.net["xpub"])
        odesc = "tr([%s/86h/1h/0h]%s/{0,1}/*)" % (other.fingerprint.hex(), oxpub)
        ospk, opub = _leaf(odesc, 0, 0)
        wallets, meta = self._one(ospk, opub, other.fingerprint,
                                  bip32.parse_path("m/86h/1h/0h/0/0"))
        self.assertIn(None, wallets)
        self.assertEqual(meta["inputs"][0]["label"], "Unknown wallet")


class TaprootZeroFpShimMixedInputsTest(TaprootZeroFpShimBase):
    def test_known_and_unknown_inputs_are_distinguished(self):
        desc = _bip86_descriptor(self.keystore, self.net)
        self._import(desc)
        known_spk, known_pub = _leaf(desc, 0, 0)

        other = get_keystore(mnemonic=OTHER_MNEMONIC)
        oxpub = other.get_xpub("m/86h/1h/0h").to_base58(self.net["xpub"])
        odesc = "tr([%s/86h/1h/0h]%s/{0,1}/*)" % (other.fingerprint.hex(), oxpub)
        unk_spk, unk_pub = _leaf(odesc, 0, 0)

        psbt = self._taproot_psbt(
            input_specs=[
                (known_spk, known_pub, ZERO_FP,
                 bip32.parse_path("m/86h/1h/0h/0/0"), 100000),
                (unk_spk, unk_pub, other.fingerprint,
                 bip32.parse_path("m/86h/1h/0h/0/0"), 70000),
            ],
            outputs=[(script.address_to_scriptpubkey(EXTERNAL_ADDR), 150000)],
        )
        wallets, meta = self._run(psbt)
        names = sorted("" if w is None else w.name for w in wallets)
        self.assertEqual(names, ["", "BW"])
        self.assertIn(None, wallets)                       # warning still shown
        self.assertEqual(meta["inputs"][0]["label"], "BW")
        self.assertEqual(meta["inputs"][1]["label"], "Unknown wallet")


class TaprootZeroFpShimNonTaprootRegressionTest(TaprootZeroFpShimBase):
    """The pre-existing legacy-derivation zero-fingerprint shim must keep working."""

    def _wpkh_descriptor(self):
        fp = self.keystore.fingerprint.hex()
        xpub = self.keystore.get_xpub("m/84h/1h/0h").to_base58(self.net["xpub"])
        return "wpkh([%s/84h/1h/0h]%s/{0,1}/*)" % (fp, xpub)

    def test_wpkh_zero_fingerprint_still_repaired(self):
        desc = self._wpkh_descriptor()
        self._import(desc, name="segwit")
        d = Descriptor.from_string(desc.split("#")[0])
        recv = d.derive(0, branch_index=0)
        chg = d.derive(0, branch_index=1)
        recv_pub = recv.keys[0].get_public_key()
        chg_pub = chg.keys[0].get_public_key()

        vin = [TransactionInput(bytes([0x11]) * 32, 0)]
        vout = [
            TransactionOutput(60000, script.address_to_scriptpubkey(EXTERNAL_ADDR)),
            TransactionOutput(35000, chg.script_pubkey()),
        ]
        psbt = PSBT(Transaction(version=2, locktime=0, vin=vin, vout=vout))
        psbt.inputs[0].witness_utxo = TransactionOutput(100000, recv.script_pubkey())
        psbt.inputs[0].bip32_derivations[recv_pub] = DerivationPath(
            ZERO_FP, bip32.parse_path("m/84h/1h/0h/0/0"))
        psbt.outputs[1].bip32_derivations[chg_pub] = DerivationPath(
            ZERO_FP, bip32.parse_path("m/84h/1h/0h/1/0"))

        wallets, meta = self._run(psbt)
        self.assertNotIn(None, wallets)
        self.assertTrue(meta["outputs"][1]["change"])

    def test_wpkh_zero_fingerprint_wrong_key_not_repaired(self):
        desc = self._wpkh_descriptor()
        self._import(desc, name="segwit")
        att = ec.PrivateKey(b"\x33" * 32).get_public_key()
        att_spk = script.p2wpkh(att)
        vin = [TransactionInput(bytes([0x11]) * 32, 0)]
        vout = [TransactionOutput(90000, script.address_to_scriptpubkey(EXTERNAL_ADDR))]
        psbt = PSBT(Transaction(version=2, locktime=0, vin=vin, vout=vout))
        psbt.inputs[0].witness_utxo = TransactionOutput(100000, att_spk)
        psbt.inputs[0].bip32_derivations[att] = DerivationPath(
            ZERO_FP, bip32.parse_path("m/84h/1h/0h/0/0"))
        wallets, _ = self._run(psbt)
        self.assertIn(None, wallets)


class TaprootZeroFpShimSigningE2ETest(TaprootZeroFpShimBase):
    def test_recognized_wallet_signs_and_signature_is_valid(self):
        from embit.psbtview import PSBTView

        desc = _bip86_descriptor(self.keystore, self.net)
        self._import(desc)
        recv_spk, recv_pub = _leaf(desc, 0, 0)
        chg_spk, chg_pub = _leaf(desc, 0, 1)
        psbt = self._taproot_psbt(
            input_specs=[(recv_spk, recv_pub, ZERO_FP,
                          bip32.parse_path("m/86h/1h/0h/0/0"), 100000)],
            outputs=[(script.address_to_scriptpubkey(EXTERNAL_ADDR), 60000)],
            change_spec=(chg_spk, chg_pub, ZERO_FP,
                         bip32.parse_path("m/86h/1h/0h/1/0"), 35000),
        )
        tmp = self.manager.tempdir
        with open(tmp + "/filled_psbt", "wb") as fout:
            wallets, meta = self.manager.preprocess_psbt(
                BytesIO(psbt.serialize()), fout)
        self.assertNotIn(None, wallets)          # recognised, not just signable

        with open(tmp + "/filled_psbt", "rb") as f:
            psbtv = PSBTView.view(f, compress=True)
            with open(tmp + "/signed_raw", "wb") as fo:
                self.manager.sign_psbtview(psbtv, fo, wallets, sighash=None)

        with open(tmp + "/signed_raw", "rb") as f:
            signed = PSBT.parse(f.read())
        wit = signed.inputs[0].final_scriptwitness
        self.assertIsNotNone(wit)
        sig = wit.items[0]
        sighash_flag = 0
        if len(sig) == 65:
            sighash_flag = sig[64]
            sig = sig[:64]
        h = signed.tx.sighash_taproot(
            0, [recv_spk], [100000], sighash=sighash_flag)
        outkey = ec.PublicKey.from_xonly(recv_spk.data[2:])
        self.assertTrue(outkey.schnorr_verify(ec.SchnorrSig.parse(sig), h))
