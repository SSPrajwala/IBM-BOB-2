#!/usr/bin/env python3
"""Ask DevPilot a question about an existing report.

Usage:
    python3 ask_devpilot.py --report ../TaskFlow-API/devpilot_report.json "what should I fix first?"

Reads WATSONX_API_KEY / WATSONX_PROJECT_ID from the environment if present
(put them in a .env and `source .env` first) -- otherwise answers from the
report's own data with a templated response.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from devpilot import ask, watsonx_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("question", nargs="+")
    args = parser.parse_args()

    with open(args.report, encoding="utf-8") as f:
        report = json.load(f)

    question = " ".join(args.question)
    result = ask.answer(question, report)
    print(f"[{result['source']}] {result['answer']}")
    if not watsonx_client.available():
        print("\n(watsonx.ai not configured -- set WATSONX_API_KEY and WATSONX_PROJECT_ID in .env for AI-generated answers.)")


if __name__ == "__main__":
    main()
