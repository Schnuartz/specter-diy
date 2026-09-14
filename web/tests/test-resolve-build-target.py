#!/usr/bin/env python3
"""Manual dispatch resolves an open PR to one immutable source SHA."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from resolve_build_target import resolve

SHA = "a" * 40
PLATFORM = "b" * 40
REPO = "Schnuartz/specter-diy"


class ResolveTests(unittest.TestCase):
    def env(self):
        return {"TARGET_EVENT": "workflow_dispatch", "TARGET_PR": "19",
                "TARGET_SHA": SHA, "TARGET_BRANCH": "master", "TARGET_REPOSITORY": REPO,
                "TARGET_DEFAULT_BRANCH": "master", "GITHUB_REF": "refs/heads/master",
                "GITHUB_REPOSITORY": REPO, "GITHUB_SHA": PLATFORM, "GH_TOKEN": "test-token"}

    def pr(self):
        return {"state": "open", "head": {"sha": SHA, "ref": "feature",
                "repo": {"full_name": "other-user/specter-diy"}},
                "base": {"ref": "master", "repo": {"full_name": REPO}}}

    def test_manual_dispatch_records_source_and_platform(self):
        env = self.env()
        env["TARGET_SHA"] = f"  {SHA[:7]} "
        env["TARGET_PR"] = " 19 "
        target = resolve(env, lambda repo, number, token: self.pr())
        self.assertEqual(target, {"event": "workflow_dispatch", "number": 19,
                                  "branch": "feature", "commit": SHA,
                                  "repository": "other-user/specter-diy",
                                  "platform_commit": PLATFORM})

    def test_rejects_stale_sha_and_non_default_dispatch(self):
        pr = self.pr()
        pr["head"]["sha"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "currently points to"):
            resolve(self.env(), lambda repo, number, token: pr)
        env = self.env()
        env["TARGET_SHA"] = "abcdef"
        with self.assertRaisesRegex(ValueError, "7- to 40-character"):
            resolve(env, lambda repo, number, token: self.pr())
        env = self.env()
        env["GITHUB_REF"] = "refs/heads/feature"
        with self.assertRaisesRegex(ValueError, "default branch"):
            resolve(env, lambda repo, number, token: self.pr())
        pr = self.pr()
        pr["base"]["ref"] = "feature"
        with self.assertRaisesRegex(ValueError, "another repository or branch"):
            resolve(self.env(), lambda repo, number, token: pr)

    def test_regular_pr_and_push_keep_existing_provenance(self):
        env = self.env()
        env.update({"TARGET_EVENT": "pull_request", "TARGET_REPOSITORY": REPO,
                    "TARGET_BRANCH": "feature"})
        target = resolve(env)
        self.assertEqual((target["number"], target["commit"], target["repository"]),
                         (19, SHA, REPO))
        self.assertIsNone(target["platform_commit"])
        env.update({"TARGET_EVENT": "push", "TARGET_PR": "0", "TARGET_BRANCH": "master"})
        target = resolve(env)
        self.assertEqual(target["number"], 0)


if __name__ == "__main__":
    unittest.main()
