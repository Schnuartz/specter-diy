#!/usr/bin/env python3
"""Exercise the trusted publisher's source and filesystem checks locally."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish_preview
from publish_preview import publish_files, validate_bundles

ROOT = Path(__file__).resolve().parents[1]
POINTER = json.loads((ROOT / "browser/current.json").read_text())
MANIFEST = json.loads((ROOT / POINTER["build"] / "build-info.json").read_text())
SHA = MANIFEST["commit"]
REPO = MANIFEST["repository"]


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.browser = self.root / "browser"
        self.firmware = self.root / "firmware"
        web = self.browser / "web"
        for name in ("index.html", "assets", "browser/runtime", "browser/site.js",
                     "browser/runtime-worker.js", "browser/current.json", POINTER["build"].rstrip("/")):
            source, target = ROOT / name, web / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        (self.browser / "source.json").write_text(json.dumps({
            "kind": "browser", "commit": SHA, "repository": REPO, "sha256": {},
        }))
        hashes = {}
        for name in ("bin/specter-diy.bin", "bin/specter-diy.hex"):
            path = self.firmware / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            hashes[name] = sha256(path.read_bytes()).hexdigest()
        (self.firmware / "source.json").write_text(json.dumps({
            "kind": "firmware", "commit": SHA, "repository": REPO, "sha256": hashes,
        }))

    def test_matching_build_publishes_under_stable_pr_url(self):
        validate_bundles(self.browser, self.firmware, SHA, REPO)
        pages = self.root / "pages"
        publish_files(self.browser / "web", pages, 425, SHA)
        self.assertTrue((pages / "pr/425/index.html").is_file())
        self.assertTrue((pages / "pr/425/.nojekyll").exists() is False)
        self.assertTrue((pages / ".nojekyll").is_file())
        self.assertIn(f"site.js?v={SHA[:12]}", (pages / "pr/425/index.html").read_text())
        # A new PR commit replaces only that preview, preserving the stable site.
        (pages / "index.html").write_text("stable")
        publish_files(self.browser / "web", pages, 425, SHA)
        self.assertEqual((pages / "index.html").read_text(), "stable")

    def test_stale_or_modified_artifacts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "different source commit"):
            validate_bundles(self.browser, self.firmware, "0" * 40, REPO)
        firmware = self.firmware / "bin/specter-diy.bin"
        firmware.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "Firmware hash mismatch"):
            validate_bundles(self.browser, self.firmware, SHA, REPO)
        firmware.write_bytes(b"bin/specter-diy.bin")
        wasm = self.browser / "web" / POINTER["build"] / "micropython.wasm"
        with wasm.open("ab") as file:
            file.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "Missing or invalid|Hash mismatch"):
            validate_bundles(self.browser, self.firmware, SHA, REPO)

    def test_stale_pr_head_cannot_be_published(self):
        run = {"head_sha": SHA, "pull_requests": [{"number": 17}]}
        target = {"number": 17, "commit": SHA, "repository": REPO, "branch": "feature"}
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": "f" * 40, "ref": "feature", "repo": {"full_name": REPO}}}
        with patch.object(publish_preview, "api", return_value=pr):
            self.assertIsNone(publish_preview.find_current_pr(run, target))
        pr["head"]["sha"] = SHA
        run["head_sha"] = "1" * 40
        pr["merge_commit_sha"] = run["head_sha"]
        with patch.object(publish_preview, "api", return_value=pr):
            self.assertEqual(publish_preview.find_current_pr(run, target), pr)

    def test_empty_run_pr_list_binds_to_fork_branch_and_commit(self):
        # Real upstream fork PR workflow_run payloads have pull_requests: [].
        run = {"head_sha": SHA, "head_branch": "feature",
               "head_repository": {"full_name": REPO}, "pull_requests": []}
        target = {"number": 17, "commit": SHA, "repository": REPO, "branch": "feature"}
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        with patch.object(publish_preview, "api", return_value=pr):
            self.assertEqual(publish_preview.find_current_pr(run, target), pr)
            run["head_repository"] = {"full_name": "other-user/specter-diy"}
            self.assertIsNone(publish_preview.find_current_pr(run, target))
            run["head_repository"] = {"full_name": REPO}
            run["head_branch"] = "another-feature"
            self.assertIsNone(publish_preview.find_current_pr(run, target))
            run["head_branch"] = "feature"
            run["head_sha"] = "f" * 40
            self.assertIsNone(publish_preview.find_current_pr(run, target))

    def test_comment_replaces_only_bot_marker_and_posts_once(self):
        comments = [
            {"id": 1, "body": publish_preview.MARKER, "user": {"login": "github-actions[bot]"}},
            {"id": 2, "body": publish_preview.MARKER, "user": {"login": "another-user"}},
        ]
        calls = []
        def fake_api(method, path, body=None):
            calls.append((method, path, body))
            return comments if method == "GET" else None
        state = {"number": 17, "sha": SHA, "run_url": "https://github.com/example/actions/runs/1",
                 "run_id": 1, "published": False}
        with patch.object(publish_preview, "api", side_effect=fake_api):
            publish_preview.comment(state)
        self.assertEqual([path for method, path, _ in calls if method == "DELETE"],
                         ["/issues/comments/1"])
        posted = [body for method, _, body in calls if method == "POST"]
        self.assertEqual(len(posted), 1)
        self.assertIn("no published browser preview", posted[0]["body"])

    def test_superseded_run_writes_a_skipped_state_for_later_steps(self):
        event = self.root / "event.json"
        target = self.root / "target.json"
        state = self.root / "state.json"
        event.write_text(json.dumps({"workflow_run": {
            "name": "Build", "event": "pull_request", "conclusion": "failure",
            "head_sha": SHA, "pull_requests": [{"number": 17}],
        }}))
        target.write_text(json.dumps({"event": "pull_request", "number": 17,
                                      "commit": SHA, "repository": REPO, "branch": "feature"}))
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": "f" * 40, "ref": "feature", "repo": {"full_name": REPO}}}
        args = SimpleNamespace(event=event, target=target, state=state,
                               browser=self.browser, firmware=self.firmware, pages=self.root / "pages")
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertTrue(result["skip"])
        self.assertTrue(json.loads(state.read_text())["skip"])


if __name__ == "__main__":
    unittest.main()
