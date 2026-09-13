#!/usr/bin/env python3
"""Trusted Pages publisher. Never executes files from a PR build artifact."""
from pathlib import Path
from urllib.request import Request, urlopen
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser"))
from verify_build import verify  # type: ignore[import-not-found]

MARKER = "<!-- specter-pr-build-comment -->"


def read_json_file(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError(f"Invalid artifact metadata: {path.name}")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Invalid artifact metadata: {path.name}")
    return data


def api(method: str, path: str, body=None):
    base = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}"
    data = None if body is None else json.dumps(body).encode()
    request = Request(base + path, data=data, method=method, headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **({"Content-Type": "application/json"} if data is not None else {}),
    })
    with urlopen(request, timeout=30) as response:
        content = response.read()
    return json.loads(content) if content else None


def find_current_pr(run: dict, target: dict) -> dict | None:
    number = target.get("number")
    if not isinstance(number, int) or not 0 < number < 1_000_000:
        return None
    numbers = {pr["number"] for pr in run.get("pull_requests", [])}
    if numbers and number not in numbers:
        return None
    pr = api("GET", f"/pulls/{number}")
    if pr["state"] != "open" or pr["head"]["sha"] != target["commit"] or \
            pr["head"]["repo"]["full_name"].lower() != target["repository"].lower() or \
            pr["head"]["ref"] != target["branch"]:
        return None
    # GitHub's workflow_run payload can leave pull_requests empty for fork PRs.
    # In that case the run itself must identify the exact source branch/commit;
    # otherwise an artifact-controlled target.json could nominate another PR.
    if not numbers and (run.get("head_repository", {}).get("full_name", "").lower() !=
            pr["head"]["repo"]["full_name"].lower() or
            run.get("head_branch") != pr["head"]["ref"] or
            run.get("head_sha") != pr["head"]["sha"]):
        return None
    # A pull_request workflow can report the synthetic merge commit as its
    # run head; a checkout of the PR head reports the source commit instead.
    if run["head_sha"] not in (pr["head"]["sha"], pr.get("merge_commit_sha")):
        return None
    return pr


def read_source(directory: Path, kind: str, sha: str, repo: str) -> dict:
    source = read_json_file(directory / "source.json")
    if source.get("kind") != kind or source.get("commit") != sha or \
            source.get("repository", "").lower() != repo.lower():
        raise ValueError(f"{kind} artifact belongs to a different source commit")
    return source


def validate_artifact_tree(root: Path):
    if not root.is_dir():
        raise ValueError("Browser artifact directory missing")
    for path in root.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Unsafe artifact path")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not (relative == "index.html" or
            relative.startswith(("assets/", "browser/runtime/", "builds/")) or
            relative in {"browser/site.js", "browser/runtime-worker.js", "browser/current.json"}):
            raise ValueError(f"Unexpected browser artifact: {relative}")


def validate_bundles(browser: Path, firmware: Path, sha: str, repo: str) -> dict:
    read_source(browser, "browser", sha, repo)
    fw = read_source(firmware, "firmware", sha, repo)
    for name in ("bin/specter-diy.bin", "bin/specter-diy.hex"):
        path = firmware / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing firmware artifact: {name}")
        from hashlib import sha256
        if sha256(path.read_bytes()).hexdigest() != fw["sha256"].get(name):
            raise ValueError(f"Firmware hash mismatch: {name}")
    web = browser / "web"
    validate_artifact_tree(web)
    manifest = verify(web, sha, repo)
    if not manifest.get("experimental"):
        raise ValueError("Development build warning missing from manifest")
    if "NEVER ENTER A REAL SEED PHRASE" not in (web / "index.html").read_text():
        raise ValueError("Development build warning missing from page")
    for name in ("browser/site.js", "browser/runtime-worker.js"):
        if not (web / name).is_file():
            raise ValueError(f"Missing browser shell: {name}")
    return manifest


def publish_files(web: Path, pages: Path, number: int | None, sha: str):
    pages = pages.resolve()
    target = pages / "pr" / str(number) if number else pages
    if not target.resolve().is_relative_to(pages) or pages == target and number:
        raise ValueError("Invalid preview destination")
    if number and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "assets", "browser", "builds"):
        source = web / name
        destination = target / name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    index = target / "index.html"
    contents = index.read_text().replace('./browser/site.js"', f'./browser/site.js?v={sha[:12]}"')
    index.write_text(contents)
    (pages / ".nojekyll").touch()


def artifact_id(run_id: int, name: str) -> int:
    artifacts = api("GET", f"/actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
    found = [item["id"] for item in artifacts if item["name"] == name and not item["expired"]]
    if len(found) != 1:
        raise ValueError(f"Expected one unexpired {name} artifact")
    return found[0]


def comment(state: dict):
    number = state.get("number")
    if not number:
        return
    run_url = state["run_url"]
    sha = state["sha"]
    if state["published"]:
        repo = os.environ["GITHUB_REPOSITORY"]
        pages_owner = repo.split("/")[0].lower()
        pages_url = f"https://{pages_owner}.github.io/{repo.split('/')[1]}/pr/{number}/"
        firmware_url = f"{run_url}/artifacts/{artifact_id(state['run_id'], 'firmware-binaries')}"
        body = (f"{MARKER}\n🧪 **Specter PR Build** · `{sha[:12]}` ✅\n\n"
                f"🖥️ [Open browser simulator]({pages_url})\n\n"
                f"⬇️ [Download firmware from the same commit]({firmware_url})\n\n"
                f"🔧 [Build workflow and logs]({run_url})\n\n"
                "⚠️ **Experimental development build.** Never use real funds or enter a real seed phrase. "
                "Use dedicated test hardware for firmware builds.")
    else:
        body = (f"{MARKER}\n🧪 **Specter PR Build** · `{sha[:12]}` ❌\n\n"
                "The current PR commit has no published browser preview or matching firmware build. "
                f"[Inspect build logs]({run_url}).\n\n"
                "⚠️ Previous previews must not be treated as this commit.")
    existing = []
    for page in range(1, 11):
        batch = api("GET", f"/issues/{number}/comments?per_page=100&page={page}")
        existing.extend(batch)
        if len(batch) < 100:
            break
    for entry in existing:
        if MARKER in entry.get("body", "") and entry.get("user", {}).get("login") == "github-actions[bot]":
            api("DELETE", f"/issues/comments/{entry['id']}")
    api("POST", f"/issues/{number}/comments", {"body": body})


def prepare(args):
    def skip(reason: str):
        state = {"skip": True, "reason": reason}
        Path(args.state).write_text(json.dumps(state, indent=2) + "\n")
        print(reason)
        return state

    event = json.loads(Path(args.event).read_text())
    run = event["workflow_run"]
    if run["name"] != "Build" or run["event"] not in ("pull_request", "push"):
        raise ValueError("Unrecognized workflow run")
    repository = os.environ["GITHUB_REPOSITORY"]
    target = read_json_file(Path(args.target))
    if target.get("event") != run["event"] or not re.fullmatch(r"[a-f0-9]{40}", target.get("commit", "")):
        raise ValueError("Invalid workflow target artifact")
    source = None
    browser = Path(args.browser)
    firmware = Path(args.firmware)
    if run["conclusion"] == "success":
        source = read_json_file(browser / "source.json")
    if run["event"] == "pull_request":
        pr = find_current_pr(run, target)
        if not pr:
            return skip("PR head advanced or PR closed; skip stale workflow run")
        sha, repo, number = pr["head"]["sha"], pr["head"]["repo"]["full_name"], pr["number"]
        if source and (source["commit"] != sha or source["repository"].lower() != repo.lower()):
            raise ValueError("Browser artifact is stale for current PR head")
    else:
        if run["head_branch"] not in ("master", "main"):
            return skip("Default-branch run is no longer publishable")
        sha, repo, number = run["head_sha"], repository, None
        if target.get("commit") != sha or target.get("repository", "").lower() != repo.lower() or \
                target.get("number") != 0:
            raise ValueError("Default-branch target does not match workflow run")
    state = {"skip": False, "number": number, "sha": sha, "repo": repo,
             "run_id": run["id"], "run_url": run["html_url"], "published": False}
    pages = Path(args.pages)
    if run["conclusion"] == "success":
        validate_bundles(browser, firmware, sha, repo)
        publish_files(browser / "web", pages, number, sha)
        state["published"] = True
    elif number:
        target = pages.resolve() / "pr" / str(number)
        if target.exists() and target.is_relative_to(pages.resolve()):
            shutil.rmtree(target)
    Path(args.state).write_text(json.dumps(state, indent=2) + "\n")
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "comment"))
    parser.add_argument("--event")
    parser.add_argument("--target")
    parser.add_argument("--browser")
    parser.add_argument("--firmware")
    parser.add_argument("--pages")
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    if args.phase == "prepare":
        print(prepare(args))
    else:
        state = json.loads(Path(args.state).read_text())
        if not state.get("skip"):
            comment(state)


if __name__ == "__main__":
    main()
