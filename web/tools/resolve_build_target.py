#!/usr/bin/env python3
"""Resolve a Build run to one source commit before any build job starts."""
from urllib.request import Request, urlopen
import json
import os
import re


def fetch_pull(repository: str, number: str, token: str) -> dict:
    request = Request(f"https://api.github.com/repos/{repository}/pulls/{number}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
    })
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def resolve(env, fetch=fetch_pull) -> dict:
    event = env["TARGET_EVENT"]
    number = str(env["TARGET_PR"])
    sha = env["TARGET_SHA"]
    branch = env["TARGET_BRANCH"]
    repository = env["TARGET_REPOSITORY"]
    if event == "workflow_dispatch":
        if not re.fullmatch(r"[a-f0-9]{7,40}", sha):
            raise ValueError("Expected a 7- to 40-character hexadecimal PR head SHA")
        if env["GITHUB_REF"] != f"refs/heads/{env['TARGET_DEFAULT_BRANCH']}":
            raise ValueError("Manual PR builds must run from the default branch")
        if not re.fullmatch(r"[1-9][0-9]{0,6}", number):
            raise ValueError("Invalid PR number")
        pr = fetch(env["GITHUB_REPOSITORY"], number, env["GH_TOKEN"])
        current_sha = pr["head"]["sha"]
        if pr["state"] != "open" or not re.fullmatch(r"[a-f0-9]{40}", current_sha) or \
                not current_sha.startswith(sha) or \
                pr["base"]["repo"]["full_name"].lower() != env["GITHUB_REPOSITORY"].lower() or \
                pr["base"]["ref"] != env["TARGET_DEFAULT_BRANCH"]:
            raise ValueError("PR is closed, targets another branch, or its head SHA changed")
        sha = current_sha
        repository = pr["head"]["repo"]["full_name"]
        branch = pr["head"]["ref"]
    elif event == "pull_request":
        number = int(number)
    elif event == "push":
        number = 0
    else:
        raise ValueError("Unsupported Build event")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Expected a full source commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid source repository")
    return {"event": event, "number": int(number), "branch": branch,
            "commit": sha, "repository": repository,
            "platform_commit": env["GITHUB_SHA"] if event == "workflow_dispatch" else None}


def main():
    target = resolve(os.environ)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"sha={target['commit']}\nrepository={target['repository']}\n")
    with open("target.json", "w") as output:
        json.dump(target, output)


if __name__ == "__main__":
    main()
