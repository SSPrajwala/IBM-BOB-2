#!/usr/bin/env python3
"""Push DevPilot's local auto-fix branch and open a pull request.

This is NEVER called automatically by run_devpilot.py or fix_agent.py.
It requires:
  1. A GITHUB_TOKEN in your environment / .env (a personal access token
     with `repo` scope, for YOUR OWN repository).
  2. The --confirm flag, spelled out, every time -- there is no default-yes.

Usage:
    export GITHUB_TOKEN=ghp_xxx        # or put it in .env and `source .env`
    python3 push_and_pr.py --project ../TaskFlow-API --repo yourname/yourrepo --confirm
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="Local path with the committed devpilot/auto-fixes branch")
    parser.add_argument("--repo", required=True, help="owner/name on GitHub")
    parser.add_argument("--branch", default="devpilot/auto-fixes")
    parser.add_argument("--base", default="main")
    parser.add_argument("--confirm", action="store_true", help="Required -- this pushes to a real remote and opens a real PR")
    args = parser.parse_args()

    if not args.confirm:
        print("Refusing to push without --confirm. This action modifies your real GitHub repository.")
        sys.exit(1)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set. Put your own personal access token in .env and export it first.")
        sys.exit(1)

    print(f"Pushing {args.branch} to {args.repo} ...")
    subprocess.run(["git", "-C", args.project, "push", "-u", "origin", args.branch], check=True)

    payload = json.dumps({
        "title": "DevPilot auto-fix: security & correctness fixes",
        "head": args.branch,
        "base": args.base,
        "body": "Opened automatically by DevPilot's fix subagent, with explicit human confirmation at push time. "
                "Review the diff -- these are mechanical, pattern-matched fixes, not a substitute for human review.",
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{args.repo}/pulls",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    print(f"Opened PR: {data.get('html_url')}")


if __name__ == "__main__":
    main()
