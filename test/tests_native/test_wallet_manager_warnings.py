import sys

if sys.implementation.name != 'micropython':
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase
import gc

from tests.util import get_keystore, get_wallets_app, clear_testdir


class WalletManagerWarningsTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.keystore = get_keystore()
        self.wallets_app = get_wallets_app(self.keystore, "regtest")
        self.manager = self.wallets_app.manager

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def _meta(self):
        return {
            "inputs": [],
            "outputs": [],
            "signed_inputs": 0,
            "tx_version": 2,
            "locktime": 0,
        }

    def test_no_warning_for_empty_wallets(self):
        meta = self._meta()
        self.manager.add_warnings({}, meta)
        self.assertFalse("warnings" in meta)

    def test_no_warning_for_single_wallet(self):
        meta = self._meta()
        wallets = {object(): {"amount": 1000, "gaps": [0, 0]}}
        self.manager.add_warnings(wallets, meta)
        self.assertFalse("warnings" in meta)

    def test_no_warning_for_single_unknown_wallet(self):
        # a single unknown wallet is covered by the separate
        # "Unknown wallet in inputs!" prompt, not by mixed-input warnings
        meta = self._meta()
        wallets = {None: {"amount": 1000, "gaps": None}}
        self.manager.add_warnings(wallets, meta)
        self.assertFalse("warnings" in meta)

    def test_warning_for_mixed_inputs_from_two_wallets(self):
        meta = self._meta()
        wallets = {
            object(): {"amount": 1000, "gaps": [0, 0]},
            object(): {"amount": 2000, "gaps": [0, 0]},
        }
        self.manager.add_warnings(wallets, meta)
        self.assertIn("warnings", meta)
        self.assertEqual(len(meta["warnings"]), 1)
        self.assertIn("Mixed inputs", meta["warnings"][0])

    def test_warning_for_mixed_known_and_unknown_wallets(self):
        meta = self._meta()
        wallets = {
            object(): {"amount": 1000, "gaps": [0, 0]},
            None: {"amount": 2000, "gaps": None},
        }
        self.manager.add_warnings(wallets, meta)
        self.assertIn("warnings", meta)
        self.assertIn("Mixed inputs", meta["warnings"][0])
