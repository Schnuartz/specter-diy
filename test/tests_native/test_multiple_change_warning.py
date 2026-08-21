import sys

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs

    setup_native_stubs()

from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
import gc

from tests.util import get_keystore, get_wallets_app, clear_testdir

from embit import bip32, ec, script
from embit.psbt import PSBT, DerivationPath
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from apps.wallets.wallet import Wallet
from apps.wallets.manager import MULTIPLE_CHANGE_OUTPUTS_WARNING

# Follow-up to PR #13 (safe change-output classification): a transaction
# with two or more cryptographically *verified* change outputs
# (meta["outputs"][i]["change"] is True) should raise exactly one
# transaction-level warning. PR #13's classification logic itself -
# get_verified_change_derivation()/get_output_status(), the two-branch
# descriptor rule, and the output labels - is untouched here; these tests
# only exercise the new counting/warning step in add_warnings(), wired
# through the real preprocess_psbt() pipeline wherever possible.


def fake_pubkey(seed):
    return ec.PrivateKey(bytes([seed]) * 32).get_public_key()


class MultipleChangeWarningTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore()
        self.wallets_app = get_wallets_app(self.keystore, "regtest")
        self.manager = self.wallets_app.manager
        self.wallet = self.manager.wallets[0]  # default wpkh([..]/{0,1}/*) wallet
        self.fingerprint = self.keystore.fingerprint

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def derivation(self, wallet, branch_idx, idx, origin="m/84h/1h/0h"):
        path = bip32.parse_path("%s/%d/%d" % (origin, branch_idx, idx))
        return DerivationPath(self.fingerprint, path)

    def script_for(self, wallet, branch_idx, idx):
        return wallet.descriptor.derive(idx, branch_index=branch_idx).script_pubkey()

    def _base_tx(self, outputs, prev_value=1_000_000):
        """Build a single-input tx spending from self.wallet, with the
        given list of (value, script_pubkey, derivation_or_None) outputs."""
        prev_script = self.script_for(self.wallet, 0, 0)
        txin = TransactionInput(b"\x99" * 32, 0)
        vouts = [TransactionOutput(value, spk) for value, spk, _ in outputs]
        tx = Transaction(vin=[txin], vout=vouts)
        p = PSBT(tx)
        p.inputs[0].witness_utxo = TransactionOutput(prev_value, prev_script)
        p.inputs[0].bip32_derivations[fake_pubkey(200)] = self.derivation(self.wallet, 0, 0)
        for i, (_, _, der) in enumerate(outputs):
            if der is not None:
                p.outputs[i].bip32_derivations[fake_pubkey(201 + i)] = der
        return p

    def _preprocess(self, psbt):
        return self.manager.preprocess_psbt(BytesIO(psbt.serialize()), BytesIO())

    # --- 1: exactly one verified change output -> no warning ---------------

    def test_single_verified_change_output_no_warning(self):
        out_change = (
            40_000,
            self.script_for(self.wallet, 1, 3),
            self.derivation(self.wallet, 1, 3),
        )
        p = self._base_tx([out_change])
        wallets, meta = self._preprocess(p)

        self.assertTrue(meta["outputs"][0]["change"])
        self.assertNotIn(MULTIPLE_CHANGE_OUTPUTS_WARNING, meta.get("warnings", []))

    # --- 2: exactly two verified change outputs -> exactly one warning -----

    def test_two_verified_change_outputs_warn_exactly_once(self):
        out1 = (40_000, self.script_for(self.wallet, 1, 3), self.derivation(self.wallet, 1, 3))
        out2 = (50_000, self.script_for(self.wallet, 1, 4), self.derivation(self.wallet, 1, 4))
        p = self._base_tx([out1, out2])
        wallets, meta = self._preprocess(p)

        self.assertTrue(meta["outputs"][0]["change"])
        self.assertTrue(meta["outputs"][1]["change"])
        self.assertEqual(meta["warnings"], [MULTIPLE_CHANGE_OUTPUTS_WARNING])

    # --- 3: three verified change outputs -> still exactly one warning -----

    def test_three_verified_change_outputs_warn_exactly_once(self):
        out1 = (10_000, self.script_for(self.wallet, 1, 3), self.derivation(self.wallet, 1, 3))
        out2 = (20_000, self.script_for(self.wallet, 1, 4), self.derivation(self.wallet, 1, 4))
        out3 = (30_000, self.script_for(self.wallet, 1, 5), self.derivation(self.wallet, 1, 5))
        p = self._base_tx([out1, out2, out3])
        wallets, meta = self._preprocess(p)

        self.assertTrue(all(o["change"] for o in meta["outputs"]))
        self.assertEqual(meta["warnings"], [MULTIPLE_CHANGE_OUTPUTS_WARNING])

    # --- 4: only wallet-owned outputs that are NOT verified change ---------
    #     (receive-branch self-payments) -> never counted, no warning.

    def test_wallet_owned_non_change_outputs_do_not_warn(self):
        out1 = (10_000, self.script_for(self.wallet, 0, 5), self.derivation(self.wallet, 0, 5))
        out2 = (20_000, self.script_for(self.wallet, 0, 6), self.derivation(self.wallet, 0, 6))
        p = self._base_tx([out1, out2])
        wallets, meta = self._preprocess(p)

        self.assertFalse(meta["outputs"][0]["change"])
        self.assertFalse(meta["outputs"][1]["change"])
        self.assertNotIn(MULTIPLE_CHANGE_OUTPUTS_WARNING, meta.get("warnings", []))

    # --- 5: three-branch descriptor, branch position 1 -> change=False -----
    #     per PR #13 - must not trigger the multiple-change warning even
    #     with two such outputs.

    def test_three_branch_descriptor_branch1_outputs_never_warn(self):
        der_path = "m/84h/1h/0h"
        xpub = self.keystore.get_xpub(der_path)
        desc_str = "wpkh([%s%s]%s/<0;1;2>/*)" % (
            self.fingerprint.hex(),
            der_path[1:],
            xpub.to_base58(self.manager.Networks["regtest"]["xpub"]),
        )
        wallet3 = Wallet.from_descriptor(desc_str, None)
        # Replace the default two-branch wallet entirely (rather than
        # appending) - it shares this keystore's xpub at the same origin,
        # so a branch-1 script under the two-branch descriptor is bit-for-
        # bit identical to this wallet's branch-position-1 script. Keeping
        # both around would make wallet detection in preprocess_psbt() pick
        # the two-branch wallet first and defeat the point of this test.
        self.manager.wallets = [wallet3]
        self.assertEqual(wallet3.descriptor.num_branches, 3)

        prev_script = self.script_for(wallet3, 0, 0)
        txin = TransactionInput(b"\x88" * 32, 0)
        out1 = TransactionOutput(10_000, self.script_for(wallet3, 1, 3))
        out2 = TransactionOutput(20_000, self.script_for(wallet3, 1, 4))
        tx = Transaction(vin=[txin], vout=[out1, out2])
        p = PSBT(tx)
        p.inputs[0].witness_utxo = TransactionOutput(1_000_000, prev_script)
        p.inputs[0].bip32_derivations[fake_pubkey(210)] = self.derivation(wallet3, 0, 0)
        p.outputs[0].bip32_derivations[fake_pubkey(211)] = self.derivation(wallet3, 1, 3)
        p.outputs[1].bip32_derivations[fake_pubkey(212)] = self.derivation(wallet3, 1, 4)

        wallets, meta = self._preprocess(p)

        self.assertFalse(meta["outputs"][0]["change"])
        self.assertFalse(meta["outputs"][1]["change"])
        self.assertNotIn(MULTIPLE_CHANGE_OUTPUTS_WARNING, meta.get("warnings", []))

    # --- 6: one real change output + receive-branch + external outputs -----
    #     -> only one verified change output, so no multiple-change warning.

    def test_single_change_with_receive_and_external_outputs_no_warning(self):
        external_script = script.p2wpkh(fake_pubkey(90))
        out_external = (5_000, external_script, None)
        out_receive = (15_000, self.script_for(self.wallet, 0, 7), self.derivation(self.wallet, 0, 7))
        out_change = (25_000, self.script_for(self.wallet, 1, 3), self.derivation(self.wallet, 1, 3))
        p = self._base_tx([out_external, out_receive, out_change])
        wallets, meta = self._preprocess(p)

        self.assertFalse(meta["outputs"][0]["change"])
        self.assertFalse(meta["outputs"][1]["change"])
        self.assertTrue(meta["outputs"][2]["change"])
        self.assertNotIn(MULTIPLE_CHANGE_OUTPUTS_WARNING, meta.get("warnings", []))

    # --- 7: existing warnings are preserved, new warning appended once -----

    def _meta_with_outputs(self, change_flags):
        return {
            "inputs": [],
            "outputs": [{"change": flag} for flag in change_flags],
            "signed_inputs": 0,
            "tx_version": 2,
            "locktime": 0,
        }

    def test_existing_warnings_preserved_and_no_duplicates(self):
        meta = self._meta_with_outputs([True, True])
        meta["warnings"] = ["Some other warning!"]
        wallets = {self.wallet: {}}

        self.manager.add_warnings(wallets, meta)
        self.assertEqual(
            meta["warnings"], ["Some other warning!", MULTIPLE_CHANGE_OUTPUTS_WARNING]
        )

        # calling again (e.g. re-processing) must not duplicate the warning
        self.manager.add_warnings(wallets, meta)
        self.assertEqual(
            meta["warnings"], ["Some other warning!", MULTIPLE_CHANGE_OUTPUTS_WARNING]
        )

    # --- 8: Liquid uses the same warning infrastructure ---------------------
    #
    # A full Liquid PSET (blinded commitments, rangeproofs, surjection
    # proofs) is out of scope for this warning-only follow-up, so this
    # drives the real LWalletManager (inherited, unmodified
    # get_output_status()/add_warnings()) directly on real output objects
    # and a real two-branch wallet/descriptor, the same way
    # test_change_classification.test_liquid_branch0_not_change_branch1_is_change
    # already does for PR #13's classification logic.

    def test_liquid_two_verified_change_outputs_warn_exactly_once(self):
        clear_testdir()
        lks = get_keystore()
        lwapp = get_wallets_app(lks, "elementsregtest")
        lmanager = lwapp.manager
        lwallet = lmanager.wallets[0]
        lfp = lks.fingerprint
        origin = "m/84h/1h/0h"

        def lderivation(branch_idx, idx):
            path = bip32.parse_path("%s/%d/%d" % (origin, branch_idx, idx))
            return DerivationPath(lfp, path)

        def loutput(branch_idx, idx, seed):
            return SimpleNamespace(
                bip32_derivations={fake_pubkey(seed): lderivation(branch_idx, idx)},
                taproot_bip32_derivations={},
                script_pubkey=lwallet.descriptor.derive(
                    idx, branch_index=branch_idx
                ).script_pubkey(),
            )

        wallets = {lwallet: {}}
        out1 = loutput(1, 3, 230)
        out2 = loutput(1, 4, 231)
        _, is_change1, _ = lmanager.get_output_status(lwallet, wallets, out1)
        _, is_change2, _ = lmanager.get_output_status(lwallet, wallets, out2)
        self.assertTrue(is_change1)
        self.assertTrue(is_change2)

        meta = {"outputs": [{"change": is_change1}, {"change": is_change2}]}
        lmanager.add_warnings(wallets, meta)
        self.assertEqual(meta["warnings"], [MULTIPLE_CHANGE_OUTPUTS_WARNING])
        clear_testdir()
