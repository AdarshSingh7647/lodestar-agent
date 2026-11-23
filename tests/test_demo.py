import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo"))

from run_demo import run_demo  # noqa: E402


class DemoEndToEnd(unittest.TestCase):
    """Runs the real hook path: shim subprocess -> HTTP daemon -> verifier -> toy repo tests."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_demo(log=lambda *_: None)

    def test_false_completion_is_blocked_by_real_tests(self):
        out = self.r["first_stop"]
        self.assertEqual(out["decision"], "block")
        self.assertIn("round 1/5", out["reason"])
        self.assertIn("AssertionError", out["reason"])

    def test_fix_passes_verification(self):
        self.assertIn("verification passed", self.r["second_stop"]["systemMessage"])

    def test_correction_repeated_mistake_prevented_next_session(self):
        ctx = self.r["new_session"]["hookSpecificOutput"]["additionalContext"]
        self.assertIn("pathlib", ctx)

    def test_task_ends_done_with_verified_rounds(self):
        fix = [t for t in self.r["tasks"] if t["title"].startswith("Fix the add")][0]
        self.assertEqual(fix["status"], "done")
        self.assertEqual(fix["rounds"], 1)

    def test_standing_rule_does_not_open_a_task(self):
        rules = [t for t in self.r["tasks"] if t["title"].startswith("Always use pathlib")]
        self.assertEqual(rules, [])

    def test_every_hook_went_through_the_daemon(self):
        self.assertTrue(all(s["stderr"] == "" for s in self.r["steps"]))


if __name__ == "__main__":
    unittest.main()
