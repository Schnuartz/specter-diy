#!/usr/bin/env python3
"""The one-number helper dispatches only the exact current PR head."""
from pathlib import Path
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import trigger_pr_build


class TriggerTests(unittest.TestCase):
    def test_dispatches_current_head_on_default_branch(self):
        sha = "a" * 40
        with patch.object(trigger_pr_build, "gh_json", side_effect=[
            {"headRefOid": sha, "state": "OPEN", "baseRefName": "master"},
            {"defaultBranchRef": {"name": "master"}},
        ]), patch.object(trigger_pr_build.subprocess, "run") as run:
            trigger_pr_build.trigger("Schnuartz/specter-diy", 19)
        run.assert_called_once_with([
            "gh", "workflow", "run", "build.yml", "--repo", "Schnuartz/specter-diy",
            "--ref", "master", "-f", "pr_number=19", "-f", f"head_sha={sha}",
        ], check=True)

    def test_rejects_closed_or_malformed_pr_without_dispatch(self):
        with patch.object(trigger_pr_build, "gh_json", return_value={
            "headRefOid": "a" * 40, "state": "CLOSED", "baseRefName": "master",
        }), patch.object(trigger_pr_build.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "open PR"):
                trigger_pr_build.trigger("Schnuartz/specter-diy", 19)
        run.assert_not_called()

    def test_rejects_pr_against_non_default_branch(self):
        with patch.object(trigger_pr_build, "gh_json", side_effect=[
            {"headRefOid": "a" * 40, "state": "OPEN", "baseRefName": "feature"},
            {"defaultBranchRef": {"name": "master"}},
        ]), patch.object(trigger_pr_build.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "default branch"):
                trigger_pr_build.trigger("Schnuartz/specter-diy", 19)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
