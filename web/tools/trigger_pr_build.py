#!/usr/bin/env python3
"""Start the existing Build workflow for an open PR, without changing its branch."""
import argparse
import json
import re
import subprocess


def gh_json(*args):
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def trigger(repository: str, number: int):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    if not 0 < number < 1_000_000:
        raise ValueError("Invalid PR number")
    pr = gh_json("pr", "view", str(number), "--repo", repository,
                 "--json", "headRefOid,state,baseRefName")
    sha = pr["headRefOid"]
    if pr["state"] != "OPEN" or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Expected an open PR with a full head SHA")
    repo = gh_json("repo", "view", repository, "--json", "defaultBranchRef")
    default_branch = repo["defaultBranchRef"]["name"]
    if pr["baseRefName"] != default_branch:
        raise ValueError("PR must target the repository default branch")
    subprocess.run(["gh", "workflow", "run", "build.yml", "--repo", repository,
                    "--ref", default_branch, "-f", f"pr_number={number}",
                    "-f", f"head_sha={sha}"], check=True)
    print(f"Started Build for {repository} PR #{number} at {sha}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=int, help="open PR number")
    parser.add_argument("--repo", default="Schnuartz/specter-diy")
    args = parser.parse_args()
    trigger(args.repo, args.number)


if __name__ == "__main__":
    main()
