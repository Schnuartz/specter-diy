import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import gc

from embit import bip32, ec, script
from embit.psbt import DerivationPath, PSBT
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from tests.util import clear_testdir, get_keystore, get_wallets_app


class PSBTNetworkHintsTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore()
        self.wallets_app = get_wallets_app(self.keystore, "main")
        self.manager = self.wallets_app.manager

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def make_origin(
        self, purpose=84, coin_type=0, branch=0, index=0, fingerprint=None
    ):
        path = bip32.parse_path(
            "m/%dh/%dh/0h/%d/%d" % (purpose, coin_type, branch, index)
        )
        pubkey = self.keystore.get_xpub(path).key
        if fingerprint is None:
            fingerprint = self.keystore.fingerprint
        return pubkey, DerivationPath(fingerprint, path)

    def make_scope(
        self,
        script_pubkey,
        bip32_derivations=None,
        taproot_bip32_derivations=None,
        witness_script=None,
        redeem_script=None,
        taproot_internal_key=None,
        taproot_merkle_root=None,
    ):
        return SimpleNamespace(
            script_pubkey=script_pubkey,
            bip32_derivations=bip32_derivations or {},
            taproot_bip32_derivations=taproot_bip32_derivations or {},
            witness_script=witness_script,
            redeem_script=redeem_script,
            taproot_internal_key=taproot_internal_key,
            taproot_merkle_root=taproot_merkle_root,
        )

    def verified_hints(self, scope, cache=None):
        return self.manager.get_verified_bitcoin_network_hints(
            scope, {} if cache is None else cache
        )

    def test_verified_mainnet_and_testnet_origins_match_utxo_scripts(self):
        for coin_type in (0, 1):
            with self.subTest(coin_type=coin_type):
                pubkey, origin = self.make_origin(coin_type=coin_type)
                scope = self.make_scope(
                    script.p2wpkh(pubkey), {pubkey: origin}
                )

                self.assertEqual(self.verified_hints(scope), ({coin_type}, False))

    def test_origins_for_another_key_or_fingerprint_are_rejected(self):
        pubkey, origin = self.make_origin(coin_type=1)
        unrelated_key = ec.PrivateKey(bytes([91]) * 32).get_public_key()
        unrelated_key_scope = self.make_scope(
            script.p2wpkh(pubkey), {unrelated_key: origin}
        )
        wrong_fingerprint_scope = self.make_scope(
            script.p2wpkh(pubkey),
            {pubkey: DerivationPath(b"\x01\x02\x03\x04", origin.derivation)},
        )
        mismatched_script_scope = self.make_scope(
            script.p2wpkh(unrelated_key), {pubkey: origin}
        )

        self.assertEqual(self.verified_hints(unrelated_key_scope), (set(), False))
        self.assertEqual(self.verified_hints(wrong_fingerprint_scope), (set(), False))
        self.assertEqual(self.verified_hints(mismatched_script_scope), (set(), False))

    def test_zero_fingerprint_is_accepted_only_for_matching_key_and_script(self):
        pubkey, origin = self.make_origin(coin_type=1, fingerprint=b"\x00" * 4)
        scope = self.make_scope(script.p2wpkh(pubkey), {pubkey: origin})

        self.assertEqual(self.verified_hints(scope), ({1}, False))

    def test_zero_fingerprint_derivation_is_reused_for_network_detection(self):
        pubkey, origin = self.make_origin(coin_type=1, fingerprint=b"\x00" * 4)
        scope = self.make_scope(script.p2wpkh(pubkey), {pubkey: origin})
        cache = {}

        with patch.object(
            self.keystore, "get_xpub", wraps=self.keystore.get_xpub
        ) as get_xpub:
            self.assertFalse(self.manager.fill_zero_fingerprint(scope, cache))
            self.assertEqual(origin.fingerprint, self.keystore.fingerprint)
            self.assertEqual(self.verified_hints(scope, cache), ({1}, False))
            self.assertEqual(get_xpub.call_count, 1)
        self.assertEqual(len(cache), 1)

    def test_supported_legacy_wrapped_and_multisig_scripts_are_verified(self):
        for purpose, build_scope in (
            (44, lambda pubkey: self.make_scope(script.p2pkh(pubkey))),
            (
                49,
                lambda pubkey: self.make_scope(
                    script.p2sh(script.p2wpkh(pubkey))
                ),
            ),
            (84, lambda pubkey: self.make_scope(script.p2wpkh(pubkey))),
        ):
            with self.subTest(purpose=purpose):
                pubkey, origin = self.make_origin(purpose=purpose, coin_type=1)
                scope = build_scope(pubkey)
                scope.bip32_derivations[pubkey] = origin
                self.assertEqual(self.verified_hints(scope), ({1}, False))

        pubkey, origin = self.make_origin(purpose=48, coin_type=1)
        cosigner = ec.PrivateKey(bytes([92]) * 32).get_public_key()
        witness_script = script.multisig(1, [pubkey, cosigner])
        scope = self.make_scope(
            script.p2wsh(witness_script),
            {pubkey: origin},
            witness_script=witness_script,
        )
        self.assertEqual(self.verified_hints(scope), ({1}, False))

    def test_taproot_key_path_origin_is_verified_against_tweaked_utxo(self):
        internal_key, origin = self.make_origin(purpose=86, coin_type=1)
        utxo_script = script.p2tr(internal_key)
        scope = self.make_scope(
            utxo_script,
            taproot_bip32_derivations={internal_key: ([], origin)},
            taproot_internal_key=internal_key,
        )

        self.assertEqual(self.verified_hints(scope), ({1}, False))

        output_key = ec.PublicKey.from_xonly(utxo_script.data[2:])
        scope.taproot_bip32_derivations = {output_key: ([], origin)}
        self.assertEqual(self.verified_hints(scope), ({1}, False))

    def test_taproot_leaf_key_does_not_claim_the_internal_key_network(self):
        leaf_key, origin = self.make_origin(purpose=86, coin_type=1)
        internal_key = ec.PrivateKey(bytes([93]) * 32).get_public_key()
        merkle_root = b"\x42" * 32
        output_key = internal_key.taproot_tweak(merkle_root)
        scope = self.make_scope(
            script.Script(b"\x51\x20" + output_key.xonly()),
            taproot_bip32_derivations={leaf_key: ([b"\x24" * 32], origin)},
            taproot_internal_key=internal_key,
            taproot_merkle_root=merkle_root,
        )

        self.assertEqual(self.verified_hints(scope), (set(), False))

    def test_long_paths_and_too_many_unique_derivations_hit_limits(self):
        long_path = [
            84 + 0x80000000,
            1 + 0x80000000,
            0 + 0x80000000,
        ] + list(range(8))
        long_pubkey = ec.PrivateKey(bytes([94]) * 32).get_public_key()
        long_scope = self.make_scope(
            script.p2wpkh(long_pubkey),
            {
                long_pubkey: DerivationPath(
                    self.keystore.fingerprint, long_path
                )
            },
        )
        self.assertEqual(self.verified_hints(long_scope), (set(), True))
        zero_fingerprint_scope = self.make_scope(
            script.p2wpkh(long_pubkey),
            {
                long_pubkey: DerivationPath(
                    b"\x00" * 4, long_path
                )
            },
        )
        zero_fingerprint_cache = {}
        self.assertTrue(
            self.manager.fill_zero_fingerprint(
                zero_fingerprint_scope, zero_fingerprint_cache
            )
        )
        self.assertFalse(zero_fingerprint_cache)

        cache = {}
        derivations = {}
        for index in range(self.manager.MAX_NETWORK_HINT_DERIVATIONS + 1):
            pubkey = ec.PrivateKey(bytes([index + 1]) * 32).get_public_key()
            path = bip32.parse_path("m/84h/1h/0h/0/%d" % index)
            origin = DerivationPath(self.keystore.fingerprint, path)
            derivations[pubkey] = origin
        scope = self.make_scope(
            script.p2wpkh(ec.PrivateKey(bytes([95]) * 32).get_public_key()),
            derivations,
        )

        _, limited = self.verified_hints(scope, cache)
        self.assertTrue(limited)
        self.assertEqual(
            len(cache), self.manager.MAX_NETWORK_HINT_DERIVATIONS
        )
        info, warning = self.manager.get_psbt_network_info(set(), analysis_limited=True)
        self.assertIn("Unknown (network analysis limit reached)", info)
        self.assertIn("safe analysis limits", warning)

    def test_mainnet_mismatch_and_test_network_family_are_reported(self):
        _, warning = self.manager.get_psbt_network_info({0})
        self.assertIsNone(warning)

        _, warning = self.manager.get_psbt_network_info({1})
        self.assertIn("Bitcoin Mainnet", warning)

        self.manager.network = "signet"
        info, warning = self.manager.get_psbt_network_info({1})
        self.assertIn("Bitcoin Signet", info)
        self.assertIsNone(warning)

        _, warning = self.manager.get_psbt_network_info({0})
        self.assertIn("Bitcoin Mainnet", warning)

    def test_mixed_coin_types_produce_a_conflict_warning(self):
        info, warning = self.manager.get_psbt_network_info({0, 1})

        self.assertIn("Conflicting Bitcoin", info)
        self.assertIn("conflicting Bitcoin network types", warning)

    def test_network_hints_are_not_taken_from_output_metadata(self):
        pubkey, origin = self.make_origin(coin_type=0)
        external_input_key = ec.PrivateKey(bytes([96]) * 32).get_public_key()
        external_output_key = ec.PrivateKey(bytes([97]) * 32).get_public_key()
        psbt = PSBT(
            Transaction(
                vin=[TransactionInput(b"\x11" * 32, 0)],
                vout=[TransactionOutput(90_000, script.p2wpkh(external_output_key))],
            )
        )
        psbt.inputs[0].witness_utxo = TransactionOutput(
            100_000, script.p2wpkh(external_input_key)
        )
        psbt.outputs[0].bip32_derivations[pubkey] = origin

        _, meta = self.manager.preprocess_psbt(
            BytesIO(psbt.serialize()), BytesIO()
        )

        self.assertIn(
            "Unknown (no verified standard key origins)", meta["network_info"]
        )
        self.assertNotIn("warnings", meta)

    def test_wallet_fill_scope_origins_are_included_in_network_detection(self):
        self.wallets_app = get_wallets_app(self.keystore, "test")
        self.manager = self.wallets_app.manager
        account_path = bip32.parse_path("m/48h/1h/0h/2h")
        device_xpub = self.keystore.get_xpub(account_path).to_base58(
            self.manager.Networks["test"]["xpub"]
        )
        cosigner_root = bip32.HDKey.from_seed(bytes([98]) * 32)
        cosigner_xpub = cosigner_root.derive(account_path).to_public().to_base58(
            self.manager.Networks["test"]["xpub"]
        )
        device_fingerprint = self.keystore.fingerprint.hex().upper()
        cosigner_fingerprint = cosigner_root.my_fingerprint.hex().upper()
        descriptor = (
            "wsh(sortedmulti(2,"
            "[%s/48h/1h/0h/2h]%s/{0,1}/*,"
            "[%s/48h/1h/0h/2h]%s/{0,1}/*))"
            % (
                device_fingerprint,
                device_xpub,
                cosigner_fingerprint,
                cosigner_xpub,
            )
        )
        wallet = self.manager.parse_wallet(descriptor)
        self.manager.add_wallet(wallet)
        derived = wallet.descriptor.derive(0, branch_index=0)
        cosigner_path = bip32.parse_path("m/48h/1h/0h/2h/0/0")
        cosigner_key = cosigner_root.derive(cosigner_path).key.get_public_key()
        external_key = ec.PrivateKey(bytes([99]) * 32).get_public_key()
        psbt = PSBT(
            Transaction(
                vin=[TransactionInput(b"\x22" * 32, 0)],
                vout=[TransactionOutput(90_000, script.p2wpkh(external_key))],
            )
        )
        psbt.inputs[0].witness_utxo = TransactionOutput(
            100_000, derived.script_pubkey()
        )
        psbt.inputs[0].witness_script = derived.witness_script()
        psbt.inputs[0].bip32_derivations[cosigner_key] = DerivationPath(
            cosigner_root.my_fingerprint, cosigner_path
        )

        filled = BytesIO()
        _, meta = self.manager.preprocess_psbt(BytesIO(psbt.serialize()), filled)

        self.assertIn("Bitcoin test network family", meta["network_info"])
        self.assertNotIn("warnings", meta)
        filled.seek(0)
        filled_psbt = PSBT.read_from(filled)
        device_path = bip32.parse_path("m/48h/1h/0h/2h/0/0")
        device_key = self.keystore.get_xpub(device_path).key
        self.assertIn(device_key, filled_psbt.inputs[0].bip32_derivations)
