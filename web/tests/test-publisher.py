#!/usr/bin/env python3
"""Exercise the trusted publisher's source and filesystem checks locally."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import sys
import unittest
from urllib.parse import quote
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
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": "f" * 40, "ref": "feature", "repo": {"full_name": REPO}}}
        with patch.object(publish_preview, "api", return_value=pr):
            self.assertIsNone(publish_preview.find_current_pr(run))
        pr["head"]["sha"] = SHA
        run["head_sha"] = "1" * 40
        pr["merge_commit_sha"] = run["head_sha"]
        with patch.object(publish_preview, "api", return_value=pr):
            self.assertEqual(publish_preview.find_current_pr(run), pr)

    def test_run_pr_head_survives_a_changed_synthetic_merge_sha(self):
        run = {"head_sha": "2" * 40,
               "pull_requests": [{"number": 17, "head": {"sha": SHA}}]}
        pr = {"number": 17, "state": "open", "merge_commit_sha": "3" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        with patch.object(publish_preview, "api", return_value=pr):
            self.assertEqual(publish_preview.find_current_pr(run), pr)
            run["pull_requests"][0]["head"]["sha"] = "f" * 40
            self.assertIsNone(publish_preview.find_current_pr(run))

    def test_empty_run_pr_list_binds_to_fork_branch_and_commit(self):
        # Real upstream fork PR workflow_run payloads have pull_requests: [].
        run = {"head_sha": SHA, "head_branch": "feature",
               "head_repository": {"full_name": REPO}, "pull_requests": []}
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        calls = []
        def list_pulls(method, path, body=None):
            calls.append(path)
            return [pr]
        with patch.object(publish_preview, "api", side_effect=list_pulls):
            self.assertEqual(publish_preview.find_current_pr(run), pr)
            owner = REPO.split("/", 1)[0]
            self.assertIn(f"head={quote(f'{owner}:feature', safe='')}", calls[0])
            run["head_repository"] = {"full_name": "other-user/specter-diy"}
            self.assertIsNone(publish_preview.find_current_pr(run))
            run["head_repository"] = {"full_name": REPO}
            run["head_branch"] = "another-feature"
            self.assertIsNone(publish_preview.find_current_pr(run))
            run["head_branch"] = "feature"
            run["head_sha"] = "f" * 40
            self.assertIsNone(publish_preview.find_current_pr(run))

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
        preview = self.root / "pages/pr/17"
        preview.mkdir(parents=True)
        (preview / "index.html").write_text("newer preview")
        event = self.root / "event.json"
        state = self.root / "state.json"
        event.write_text(json.dumps({"workflow_run": {
            "name": "PR 17", "path": ".github/workflows/build.yml", "event": "pull_request", "conclusion": "failure",
            "head_sha": SHA, "pull_requests": [{"number": 17}],
        }}))
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": "f" * 40, "ref": "feature", "repo": {"full_name": REPO}}}
        args = SimpleNamespace(event=event, target=self.root / "missing-target.json", state=state,
                               browser=self.browser, firmware=self.firmware, pages=self.root / "pages")
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertTrue(result["skip"])
        self.assertTrue(json.loads(state.read_text())["skip"])
        self.assertEqual((preview / "index.html").read_text(), "newer preview")

    def test_matching_success_still_publishes_with_target_cross_check(self):
        event = self.root / "event.json"
        target = self.root / "target.json"
        event.write_text(json.dumps({"workflow_run": {
            "id": 22, "html_url": "https://github.com/example/actions/runs/22",
            "name": "PR 17", "path": ".github/workflows/build.yml", "event": "pull_request", "conclusion": "success",
            "head_sha": SHA, "pull_requests": [{"number": 17}],
        }}))
        target.write_text(json.dumps({"event": "pull_request", "number": 17,
                                      "commit": SHA, "repository": REPO, "branch": "feature"}))
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        pages = self.root / "pages"
        args = SimpleNamespace(event=event, target=target, state=self.root / "state.json",
                               browser=self.browser, firmware=self.firmware, pages=pages)
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertTrue(result["published"])
        self.assertTrue((pages / "pr/17/index.html").is_file())

    def test_failed_current_build_removes_preview_without_any_artifacts(self):
        pages = self.root / "pages"
        preview = pages / "pr/17"
        preview.mkdir(parents=True)
        (preview / "index.html").write_text("old preview")
        (pages / "index.html").write_text("stable site")
        event = self.root / "event.json"
        state = self.root / "state.json"
        event.write_text(json.dumps({"workflow_run": {
            "id": 22, "html_url": "https://github.com/example/actions/runs/22",
            "name": "PR 17", "path": ".github/workflows/build.yml", "event": "pull_request", "conclusion": "failure",
            "head_sha": SHA, "head_branch": "feature",
            "head_repository": {"full_name": REPO}, "pull_requests": [],
        }}))
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        calls = []
        def fake_api(method, path, body=None):
            calls.append((method, path, body))
            if path.startswith("/pulls?"):
                return [pr]
            if path.startswith("/issues/") and method == "GET":
                return [{"id": 9, "body": publish_preview.MARKER,
                         "user": {"login": "github-actions[bot]"}}]
            return None
        args = SimpleNamespace(event=event, target=self.root / "missing-target.json",
                               state=state, browser=self.root / "missing-browser",
                               firmware=self.root / "missing-firmware", pages=pages)
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", side_effect=fake_api):
            result = publish_preview.prepare(args)
            publish_preview.comment(result)
        self.assertFalse(result["skip"])
        self.assertFalse(result["published"])
        self.assertFalse(preview.exists())
        self.assertEqual((pages / "index.html").read_text(), "stable site")
        self.assertIn(("DELETE", "/issues/comments/9", None), calls)
        failure_comments = [body for method, path, body in calls
                            if method == "POST" and path == "/issues/17/comments"]
        self.assertEqual(len(failure_comments), 1)
        self.assertIn("no published browser preview", failure_comments[0]["body"])

    def test_success_requires_target_artifact(self):
        pages = self.root / "pages"
        preview = pages / "pr/17"
        preview.mkdir(parents=True)
        (preview / "index.html").write_text("old preview")
        event = self.root / "event.json"
        event.write_text(json.dumps({"workflow_run": {
            "id": 22, "html_url": "https://github.com/example/actions/runs/22",
            "name": "PR 17", "path": ".github/workflows/build.yml", "event": "pull_request", "conclusion": "success",
            "head_sha": SHA, "pull_requests": [{"number": 17}],
        }}))
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        args = SimpleNamespace(event=event, target=self.root / "missing-target.json",
                               state=self.root / "state.json", browser=self.browser,
                               firmware=self.firmware, pages=pages)
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertFalse(result["published"])
        self.assertIn("missing-target.json", result["reason"])
        self.assertFalse(preview.exists())

    def test_success_rejects_artifact_target_for_another_pr(self):
        pages = self.root / "pages"
        preview = pages / "pr/17"
        preview.mkdir(parents=True)
        (preview / "index.html").write_text("old preview")
        event = self.root / "event.json"
        target = self.root / "target.json"
        event.write_text(json.dumps({"workflow_run": {
            "id": 22, "html_url": "https://github.com/example/actions/runs/22",
            "name": "PR 17", "path": ".github/workflows/build.yml", "event": "pull_request", "conclusion": "success",
            "head_sha": SHA, "pull_requests": [{"number": 17}],
        }}))
        target.write_text(json.dumps({"event": "pull_request", "number": 18,
                                      "commit": SHA, "repository": REPO, "branch": "feature"}))
        pr = {"number": 17, "state": "open", "merge_commit_sha": "1" * 40,
              "head": {"sha": SHA, "ref": "feature", "repo": {"full_name": REPO}}}
        args = SimpleNamespace(event=event, target=target, state=self.root / "state.json",
                               browser=self.browser, firmware=self.firmware, pages=pages)
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertFalse(result["published"])
        self.assertIn("does not match current run", result["reason"])
        self.assertFalse(preview.exists())

    def test_manual_failure_without_artifacts_removes_current_preview(self):
        platform_sha = "b" * 40
        pages = self.root / "pages"
        preview = pages / "pr/17"
        preview.mkdir(parents=True)
        (preview / "index.html").write_text("old preview")
        event = self.root / "event.json"
        event.write_text(json.dumps({"repository": {"default_branch": "master"}, "workflow_run": {
            "id": 22, "html_url": "https://github.com/example/actions/runs/22",
            "name": f"Manual PR 17  {SHA[:7]}",
            "path": ".github/workflows/build.yml", "event": "workflow_dispatch",
            "conclusion": "failure", "display_title": f"Manual PR 17  {SHA[:7]} ",
            "head_sha": platform_sha,
            "head_branch": "master", "head_repository": {"full_name": REPO},
        }}))
        pr = {"number": 17, "state": "open", "head": {
            "sha": SHA, "ref": "feature", "repo": {"full_name": REPO}},
            "base": {"ref": "master", "repo": {"full_name": REPO}}}
        args = SimpleNamespace(event=event, target=self.root / "missing-target.json",
                               state=self.root / "state.json", browser=self.root / "missing-browser",
                               firmware=self.root / "missing-firmware", pages=pages)
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertFalse(result["skip"])
        self.assertFalse(result["published"])
        self.assertFalse(preview.exists())
        event_data = json.loads(event.read_text())
        event_data["workflow_run"]["display_title"] = "Manual PR 17  4c2a3f2"
        event.write_text(json.dumps(event_data))
        preview.mkdir(parents=True)
        (preview / "index.html").write_text("newer preview")
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertTrue(result["skip"])
        self.assertEqual((preview / "index.html").read_text(), "newer preview")
        event_data["workflow_run"]["display_title"] = f"Manual PR 17 {SHA[:7]}"
        event_data["workflow_run"]["path"] = ".github/workflows/other.yml"
        event.write_text(json.dumps(event_data))
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}):
            with self.assertRaisesRegex(ValueError, "Unrecognized workflow run"):
                publish_preview.prepare(args)

    def test_manual_success_checks_platform_commit(self):
        platform_sha = "b" * 40
        event = self.root / "event.json"
        target = self.root / "target.json"
        event.write_text(json.dumps({"repository": {"default_branch": "master"}, "workflow_run": {
            "id": 22, "html_url": "https://github.com/example/actions/runs/22",
            "name": f"Manual PR 17 {SHA}", "path": ".github/workflows/build.yml",
            "event": "workflow_dispatch", "conclusion": "success",
            "display_title": f"Manual PR 17 {SHA}", "head_sha": platform_sha,
            "head_branch": "master", "head_repository": {"full_name": REPO},
        }}))
        target.write_text(json.dumps({"event": "workflow_dispatch", "number": 17,
                                      "commit": SHA, "repository": REPO, "branch": "feature",
                                      "platform_commit": platform_sha}))
        pr = {"number": 17, "state": "open", "head": {
            "sha": SHA, "ref": "feature", "repo": {"full_name": REPO}},
            "base": {"ref": "master", "repo": {"full_name": REPO}}}
        args = SimpleNamespace(event=event, target=target, state=self.root / "state.json",
                               browser=self.browser, firmware=self.firmware, pages=self.root / "pages")
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr), \
                patch.object(publish_preview, "validate_bundles",
                             return_value={"platform_commit": platform_sha}):
            result = publish_preview.prepare(args)
        self.assertTrue(result["published"])
        self.assertTrue((self.root / "pages/pr/17/index.html").is_file())
        target_data = json.loads(target.read_text())
        target_data["platform_commit"] = "f" * 40
        target.write_text(json.dumps(target_data))
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}), \
                patch.object(publish_preview, "api", return_value=pr):
            result = publish_preview.prepare(args)
        self.assertFalse(result["published"])
        self.assertFalse((self.root / "pages/pr/17").exists())

    def test_default_branch_run_uses_workflow_path_not_dynamic_name(self):
        event = self.root / "event.json"
        event.write_text(json.dumps({"workflow_run": {
            "id": 23, "html_url": "https://github.com/example/actions/runs/23",
            "name": SHA, "path": ".github/workflows/build.yml@master",
            "event": "push", "conclusion": "failure", "head_sha": SHA,
            "head_branch": "master",
        }}))
        args = SimpleNamespace(event=event, target=self.root / "missing-target.json",
                               state=self.root / "state.json", browser=self.root / "missing-browser",
                               firmware=self.root / "missing-firmware", pages=self.root / "pages")
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO}):
            result = publish_preview.prepare(args)
        self.assertFalse(result["skip"])
        self.assertFalse(result["published"])
        self.assertIsNone(result["number"])


if __name__ == "__main__":
    unittest.main()
