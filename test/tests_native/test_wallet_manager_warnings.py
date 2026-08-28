import gc
import sys

if sys.implementation.name != "micropython":
    from native_support import setup_native_stubs

    setup_native_stubs()

from unittest import TestCase

from tests.util import clear_testdir, get_keystore, get_wallets_app


class WalletManagerWarningsTest(TestCase):
    def setUp(self):
        clear_testdir()
        self.manager = get_wallets_app(get_keystore(), "regtest").manager

    def tearDown(self):
        clear_testdir()
        gc.collect()

    def warnings_for(self, fee, outputs):
        meta = {"fee": fee, "outputs": outputs}
        self.manager.add_warnings(meta)
        return meta.get("warnings", [])

    def test_fee_below_threshold_does_not_warn(self):
        warnings = self.warnings_for(
            99, [{"value": 1_000, "change": False}]
        )
        self.assertEqual(warnings, [])

    def test_fee_at_threshold_does_not_warn(self):
        warnings = self.warnings_for(
            100, [{"value": 1_000, "change": False}]
        )
        self.assertEqual(warnings, [])

    def test_fee_above_threshold_warns_with_percentage(self):
        warnings = self.warnings_for(
            101, [{"value": 1_000, "change": False}]
        )
        self.assertEqual(
            warnings, ["Fee is 10.10% of the amount - unusually high!"]
        )

    def test_change_outputs_are_not_part_of_sent_amount(self):
        warnings = self.warnings_for(
            101,
            [
                {"value": 1_000, "change": False},
                {"value": 10_000, "change": True},
            ],
        )
        self.assertEqual(
            warnings, ["Fee is 10.10% of the amount - unusually high!"]
        )

    def test_missing_fee_or_zero_sent_amount_does_not_warn(self):
        meta = {"outputs": [{"value": 0, "change": False}]}
        self.manager.add_warnings(meta)
        self.assertNotIn("warnings", meta)

        self.assertEqual(
            self.warnings_for(1_000, [{"value": 1_000, "change": True}]), []
        )

    def test_existing_warning_is_preserved_without_duplicates(self):
        meta = {
            "fee": 101,
            "outputs": [{"value": 1_000, "change": False}],
            "warnings": ["Existing warning!"],
        }
        self.manager.add_warnings(meta)
        self.manager.add_warnings(meta)
        self.assertEqual(
            meta["warnings"],
            [
                "Existing warning!",
                "Fee is 10.10% of the amount - unusually high!",
            ],
        )
