#!/usr/bin/env python3
"""CLI entry point.

Assess a real local project:
    python3 run_devpilot.py --project ../TaskFlow-API --pr-diff ../TaskFlow-API/pr/pr-142-search-by-owner.diff

Assess a real public GitHub repo (shallow-cloned automatically):
    python3 run_devpilot.py --repo-url https://github.com/miguelgrinberg/microblog.git

Also apply DevPilot's safe, mechanical fixes and commit them locally
(never pushes -- see push_and_pr.py for that, which requires --confirm and
your own GITHUB_TOKEN):
    python3 run_devpilot.py --project ../TaskFlow-API --apply-fixes

Cloned repos are temporary by default: when you pass --repo-url, DevPilot
clones into a throwaway temp directory and deletes it again once the report
is written -- UNLESS you pass --apply-fixes (the commit needs somewhere to
live so you can push it later) or --keep-clone explicitly. Either way, the
clone's path is always printed so you know where it is.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from devpilot.orchestrator import run_all, to_html
from devpilot.agents import fix_agent
from devpilot import watsonx_client


def _clone(repo_url):
    workdir = tempfile.mkdtemp(prefix="devpilot_clone_")
    print(f"Cloning {repo_url} into {workdir} ...")
    subprocess.run(["git", "clone", "--depth", "1", repo_url, workdir], check=True, capture_output=True)
    return workdir


def main():
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--project", help="Path to a local target repository")
    src.add_argument("--repo-url", help="A public git URL to shallow-clone and assess")
    parser.add_argument("--pr-diff", default=None, help="Optional path to a PR/diff file to review")
    parser.add_argument("--out", default="devpilot_report", help="Output file basename")
    parser.add_argument("--apply-fixes", action="store_true", help="Apply DevPilot's safe mechanical fixes and commit locally (never pushes)")
    parser.add_argument("--keep-clone", action="store_true", help="Don't delete a --repo-url clone after the run (implied by --apply-fixes)")
    parser.add_argument("--ask", default=None, help="Ask DevPilot a question about the resulting report and exit")
    args = parser.parse_args()

    was_cloned = bool(args.repo_url)
    project_path = args.project or _clone(args.repo_url)
    fix_result = None

    try:
        report = run_all(project_path, pr_diff_path=args.pr_diff, project_identifier=(args.repo_url or project_path))

        with open(f"{args.out}.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        out_dir = os.path.dirname(os.path.abspath(f"{args.out}.html")) or "."
        video_candidate = os.path.join(out_dir, "demo_video.mp4")
        video_src = "demo_video.mp4" if (os.path.exists(video_candidate) and os.path.getsize(video_candidate) > 0) else None

        with open(f"{args.out}.html", "w", encoding="utf-8") as f:
            f.write(to_html(report, video_src=video_src))

        print(f"DevPilot ran {len(report['agents_run_in_parallel'])} subagents in parallel in {report['wall_clock_seconds']}s "
              f"(sequential equivalent: {report['sequential_equivalent_seconds']}s)")
        print(f"Report: {args.out}.json / {args.out}.html")
        print(f"Release recommendation: {report['results']['release_readiness']['recommendation']}")
        print(f"Confirmed (cross-agent) findings: {len(report['confirmed_findings'])}")
        print(f"Estimated hours saved: {report['business_value']['total_hours_saved']}")
        if not watsonx_client.available():
            print("(watsonx.ai not configured -- set WATSONX_API_KEY / WATSONX_PROJECT_ID in .env for AI-generated summaries and Q&A.)")

        if args.apply_fixes:
            print("\nApplying safe, mechanical fixes and committing locally to 'devpilot/auto-fixes' ...")
            fix_result = fix_agent.run(project_path, apply=True)
            print(json.dumps(fix_result, indent=2, default=str))
            if fix_result.get("git_committed"):
                print("Committed locally. Nothing was pushed. To open a PR yourself:")
                print(f"  python3 push_and_pr.py --project {project_path} --repo <owner>/<repo> --confirm")

        if args.ask:
            from devpilot import ask
            result = ask.answer(args.ask, report)
            print(f"\n[{result['source']}] {result['answer']}")

    finally:
        if was_cloned:
            keep = args.keep_clone or (args.apply_fixes and fix_result and fix_result.get("git_committed"))
            if keep:
                print(f"\nClone kept at: {project_path}")
            else:
                shutil.rmtree(project_path, ignore_errors=True)
                print(f"\nTemporary clone deleted ({project_path}). Pass --keep-clone to keep it next time.")


if __name__ == "__main__":
    main()
