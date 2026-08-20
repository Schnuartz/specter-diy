import ast
import sys
from pathlib import Path
from unittest import TestCase

# gui.screens.transaction.TransactionScreen builds real LVGL widgets (pages,
# labels, switches, styles) directly in __init__, and the native test stubs
# in native_support.py replace gui.screens.TransactionScreen with a trivial
# placeholder class (as they do for the other screen classes) so the rest of
# the wallet-manager code can be exercised without a display. That means the
# real screen implementation cannot be instantiated - or its widget tree
# inspected - under the current native/unix test infrastructure.
#
# The security-relevant behaviour this file guards is that *every*
# transaction output is represented on the primary confirmation screen
# built in TransactionScreen.__init__, i.e. no output is skipped merely
# because meta["outputs"][i]["change"] is True. That used to be exactly
# what happened:
#
#   for out in meta["outputs"]:
#       if out["change"] and not out.get("warning", ""):
#           num_change_outputs += 1
#           continue
#       obj = self.show_output(out, obj)
#
# Since we cannot drive the real widget tree here, this test statically
# proves (via the AST, not a text/regex match) that the loop over
# meta["outputs"] in the primary confirmation section calls
# self.show_output() unconditionally, with no conditional "continue"/skip
# keyed on out["change"]. This fails against the pre-fix source and passes
# after it. It was also manually verified in the simulator (see the PR
# description) that a transaction containing an external output, a verified
# change output, and a receive-branch self-payment shows all three outputs
# on the primary confirmation page.

TRANSACTION_SCREEN_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "gui" / "screens" / "transaction.py"
)


class TransactionConfirmationVisibilityTest(TestCase):
    def setUp(self):
        self.source = TRANSACTION_SCREEN_PATH.read_text()
        self.tree = ast.parse(self.source)

    def _find_class(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node
        self.fail("class %s not found in %s" % (name, TRANSACTION_SCREEN_PATH))

    def _find_init(self, class_node):
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                return node
        self.fail("__init__ not found on TransactionScreen")

    def _primary_output_loop(self, init_node):
        # the primary-confirmation loop is `for out in meta["outputs"]: ...`
        for node in ast.walk(init_node):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == "out":
                if (
                    isinstance(node.iter, ast.Subscript)
                    and isinstance(node.iter.value, ast.Name)
                    and node.iter.value.id == "meta"
                ):
                    return node
        self.fail('for out in meta["outputs"]: loop not found in TransactionScreen.__init__')

    def test_num_change_outputs_counter_is_gone(self):
        # this variable existed only to silently count and skip change
        # outputs; it must not come back.
        self.assertNotIn("num_change_outputs", self.source)

    def test_primary_confirmation_loop_never_skips_an_output(self):
        cls = self._find_class("TransactionScreen")
        init = self._find_init(cls)
        loop = self._primary_output_loop(init)

        # no conditional statement (if/continue) may appear directly in the
        # loop body - every iteration must unconditionally reach
        # self.show_output(out, obj).
        for stmt in loop.body:
            self.assertNotIsInstance(
                stmt,
                ast.If,
                "primary confirmation loop must not conditionally skip outputs",
            )
            self.assertNotIsInstance(
                stmt,
                (ast.Continue, ast.Break),
                "primary confirmation loop must not skip/abort early",
            )

        # the loop must call self.show_output(out, obj) for every output
        calls_show_output = any(
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "show_output"
            for stmt in loop.body
        )
        self.assertTrue(
            calls_show_output,
            "primary confirmation loop must call self.show_output() for every output",
        )
