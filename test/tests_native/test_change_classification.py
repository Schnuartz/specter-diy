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

# Regression coverage for the change-output classification fix.
#
# Historically an output was classified as change purely because it
# belonged to the single wallet that also owned the transaction inputs:
#
#   "change": (wallet is not None and len(wallets) == 1 and wallet in wallets)
#
# That does not distinguish a receive-branch (self-payment) output from a
# real change output, and it trusts host-supplied BIP32 derivation metadata
# without ever checking it against the actual output script_pubkey. These
# tests exercise WalletManager.get_verified_change_derivation() - the
# function that replaced that logic - directly, plus one full
# preprocess_psbt() pipeline test that reproduces the exact adversarial
# scenario described in the security report.


def fake_pubkey(seed):
    return ec.PrivateKey(bytes([seed]) * 32).get_public_key()


class ChangeClassificationTest(TestCase):
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

    def make_out(self, bip32_derivations=None, taproot_bip32_derivations=None, script_pubkey=None):
        return SimpleNamespace(
            bip32_derivations=bip32_derivations or {},
            taproot_bip32_derivations=taproot_bip32_derivations or {},
            script_pubkey=script_pubkey,
        )

    # --- Test A: legitimate verified change -------------------------------

    def test_verified_change_branch1_is_change(self):
        out = self.make_out(
            bip32_derivations={fake_pubkey(1): self.derivation(self.wallet, 1, 3)},
            script_pubkey=self.script_for(self.wallet, 1, 3),
        )
        wallets = {self.wallet: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            self.wallet, wallets, out
        )
        self.assertEqual(derivation, (3, 1))
        self.assertTrue(is_change)

    # --- Test B: receive-branch (self-payment) output of the spending wallet
    #     This is the critical regression case: a genuine output of the sole
    #     spending wallet, on branch 0, must NOT be treated as change. This
    #     assertion fails against the previous implementation, which only
    #     checked `wallet is not None and len(wallets) == 1`.

    def test_receive_branch_output_is_not_change(self):
        out = self.make_out(
            bip32_derivations={fake_pubkey(2): self.derivation(self.wallet, 0, 5)},
            script_pubkey=self.script_for(self.wallet, 0, 5),
        )
        wallets = {self.wallet: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            self.wallet, wallets, out
        )
        self.assertEqual(derivation, (5, 0))
        self.assertFalse(is_change)

    # --- Test C: forged change derivation - metadata claims branch 1, but the
    #     actual output script does not match what the device derives for it.

    def test_forged_branch1_metadata_with_mismatched_script_is_not_change(self):
        wrong_script = self.script_for(self.wallet, 0, 3)  # real script, wrong branch
        out = self.make_out(
            bip32_derivations={fake_pubkey(3): self.derivation(self.wallet, 1, 3)},
            script_pubkey=wrong_script,
        )
        wallets = {self.wallet: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            self.wallet, wallets, out
        )
        self.assertFalse(is_change)

    def test_forged_script_pubkey_unrelated_to_wallet_is_not_change(self):
        # metadata claims branch 1 idx 3, but script_pubkey is a completely
        # unrelated (attacker controlled) script
        out = self.make_out(
            bip32_derivations={fake_pubkey(3): self.derivation(self.wallet, 1, 3)},
            script_pubkey=script.p2wpkh(fake_pubkey(99)),
        )
        wallets = {self.wallet: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            self.wallet, wallets, out
        )
        self.assertFalse(is_change)

    # --- Test D: output that doesn't belong to any known wallet ------------

    def test_unknown_wallet_output_is_not_change(self):
        out = self.make_out(script_pubkey=script.p2wpkh(fake_pubkey(42)))
        wallets = {self.wallet: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            None, wallets, out
        )
        self.assertIsNone(derivation)
        self.assertFalse(is_change)

    # --- Test E: multiple spending wallets - conservative behaviour must be
    #     preserved even for a verified branch-1 output.

    def test_verified_change_with_multiple_spending_wallets_is_not_change(self):
        other_wallet = Wallet.from_descriptor(str(self.wallet.descriptor), None)
        out = self.make_out(
            bip32_derivations={fake_pubkey(4): self.derivation(self.wallet, 1, 3)},
            script_pubkey=self.script_for(self.wallet, 1, 3),
        )
        wallets = {self.wallet: {}, other_wallet: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            self.wallet, wallets, out
        )
        self.assertEqual(derivation, (3, 1))
        self.assertFalse(is_change)

    def test_output_wallet_not_among_spending_wallets_is_not_change(self):
        # wallet owns the output (verified), but it never appeared as an
        # input-owning wallet in this transaction
        out = self.make_out(
            bip32_derivations={fake_pubkey(5): self.derivation(self.wallet, 1, 3)},
            script_pubkey=self.script_for(self.wallet, 1, 3),
        )
        wallets = {}  # no wallet owns any input
        derivation, is_change = self.manager.get_verified_change_derivation(
            self.wallet, wallets, out
        )
        self.assertFalse(is_change)

    # --- Test F: additional descriptor branch (branch index > 1) -----------

    def test_branch_beyond_change_is_not_change(self):
        der_path = "m/84h/1h/0h"
        xpub = self.keystore.get_xpub(der_path)
        desc_str = "wpkh([%s%s]%s/<0;1;2>/*)" % (
            self.fingerprint.hex(),
            der_path[1:],
            xpub.to_base58(self.manager.Networks["regtest"]["xpub"]),
        )
        wallet3 = Wallet.from_descriptor(desc_str, None)
        self.assertEqual(wallet3.descriptor.num_branches, 3)

        out = self.make_out(
            bip32_derivations={fake_pubkey(6): self.derivation(wallet3, 2, 7)},
            script_pubkey=self.script_for(wallet3, 2, 7),
        )
        wallets = {wallet3: {}}
        derivation, is_change = self.manager.get_verified_change_derivation(
            wallet3, wallets, out
        )
        self.assertEqual(derivation, (7, 2))
        self.assertFalse(is_change)

    # --- Test H: Taproot ----------------------------------------------------

    def test_taproot_branch1_is_change_and_branch0_is_not(self):
        der_path = "m/86h/1h/0h"
        xpub = self.keystore.get_xpub(der_path)
        desc_str = "tr([%s%s]%s/<0;1>/*)" % (
            self.fingerprint.hex(),
            der_path[1:],
            xpub.to_base58(self.manager.Networks["regtest"]["xpub"]),
        )
        tr_wallet = Wallet.from_descriptor(desc_str, None)
        self.assertTrue(tr_wallet.descriptor.is_taproot)
        wallets = {tr_wallet: {}}

        change_der = self.derivation(tr_wallet, 1, 3, origin=der_path)
        out_change = self.make_out(
            taproot_bip32_derivations={fake_pubkey(7): ([], change_der)},
            script_pubkey=self.script_for(tr_wallet, 1, 3),
        )
        derivation, is_change = self.manager.get_verified_change_derivation(
            tr_wallet, wallets, out_change
        )
        self.assertEqual(derivation, (3, 1))
        self.assertTrue(is_change)

        recv_der = self.derivation(tr_wallet, 0, 3, origin=der_path)
        out_recv = self.make_out(
            taproot_bip32_derivations={fake_pubkey(8): ([], recv_der)},
            script_pubkey=self.script_for(tr_wallet, 0, 3),
        )
        derivation0, is_change0 = self.manager.get_verified_change_derivation(
            tr_wallet, wallets, out_recv
        )
        self.assertEqual(derivation0, (3, 0))
        self.assertFalse(is_change0)

    # --- Test I: Liquid -------------------------------------------------

    def test_liquid_branch0_not_change_branch1_is_change(self):
        clear_testdir()
        lks = get_keystore()
        lwapp = get_wallets_app(lks, "elementsregtest")
        lmanager = lwapp.manager
        lwallet = lmanager.wallets[0]
        lfp = lks.fingerprint

        def lderivation(branch_idx, idx):
            path = bip32.parse_path("m/84h/1h/0h/%d/%d" % (branch_idx, idx))
            return DerivationPath(lfp, path)

        wallets = {lwallet: {}}

        out_recv = self.make_out(
            bip32_derivations={fake_pubkey(11): lderivation(0, 5)},
            script_pubkey=lwallet.descriptor.derive(5, branch_index=0).script_pubkey(),
        )
        derivation, is_change = lmanager.get_verified_change_derivation(
            lwallet, wallets, out_recv
        )
        self.assertEqual(derivation, (5, 0))
        self.assertFalse(is_change)

        out_change = self.make_out(
            bip32_derivations={fake_pubkey(12): lderivation(1, 3)},
            script_pubkey=lwallet.descriptor.derive(3, branch_index=1).script_pubkey(),
        )
        derivation2, is_change2 = lmanager.get_verified_change_derivation(
            lwallet, wallets, out_change
        )
        self.assertEqual(derivation2, (3, 1))
        self.assertTrue(is_change2)
        clear_testdir()

    # --- Adversarial regression transaction (full preprocess_psbt pipeline) -

    def test_adversarial_regression_transaction(self):
        """
        Reproduces the exact scenario from the security report end-to-end
        through WalletManager.preprocess_psbt():

        - Input:  Wallet A
        - Output 0: external recipient                       -> not change, visible
        - Output 1: Wallet A receive branch (/0/5)            -> not change, visible
        - Output 2: Wallet A verified change branch (/1/3)    -> change, visible

        Against the pre-fix implementation, output 1 was misclassified as
        change (since Wallet A is the sole spending wallet), which could
        make it disappear from the primary confirmation screen. This test
        fails on the old implementation and passes after the fix.
        """
        wallet = self.wallet
        prev_script = self.script_for(wallet, 0, 0)
        txin = TransactionInput(b"\x11" * 32, 0)

        external_script = script.p2wpkh(fake_pubkey(50))
        out0 = TransactionOutput(50_000, external_script)
        out1 = TransactionOutput(100_000, self.script_for(wallet, 0, 5))
        out2 = TransactionOutput(40_000, self.script_for(wallet, 1, 3))

        tx = Transaction(vin=[txin], vout=[out0, out1, out2])
        p = PSBT(tx)
        p.inputs[0].witness_utxo = TransactionOutput(190_000, prev_script)
        p.inputs[0].bip32_derivations[fake_pubkey(51)] = self.derivation(wallet, 0, 0)
        p.outputs[1].bip32_derivations[fake_pubkey(52)] = self.derivation(wallet, 0, 5)
        p.outputs[2].bip32_derivations[fake_pubkey(53)] = self.derivation(wallet, 1, 3)

        raw = p.serialize()
        fout = BytesIO()
        wallets, meta = self.manager.preprocess_psbt(BytesIO(raw), fout)

        outputs = meta["outputs"]
        self.assertEqual(len(outputs), 3)

        # Output 0: external, unknown wallet
        self.assertFalse(outputs[0]["change"])
        self.assertNotIn("label", outputs[0])

        # Output 1: receive-branch self-payment - must NOT be change
        self.assertFalse(outputs[1]["change"])
        self.assertIn("label", outputs[1])
        self.assertNotIn("change", outputs[1]["label"])

        # Output 2: verified change on branch 1
        self.assertTrue(outputs[2]["change"])
        self.assertIn("change", outputs[2]["label"])

        # send_amount (as computed in the GUI) must include outputs 0 and 1
        # but exclude the verified change output.
        send_amount = sum(out["value"] for out in outputs if not out["change"])
        self.assertEqual(send_amount, 50_000 + 100_000)
