#!/usr/bin/env python3
"""Post or update the rule validation PR comment via the GitHub API (via gh CLI).

Usage:
    python scripts/post_pr_comment.py <pr_number> <run_id> < report.md

Reads the Markdown validation report from stdin, appends a run-ID footer, wraps
it with the sentinel HTML comment, and finds-or-creates/updates the PR comment.

Requires 'gh' CLI authenticated with pull-requests: write permission.
"""
from __future__ import annotations

import json
import subprocess
import sys

_SENTINEL = "<!-- uk-tax-mcp-rule-validation -->"


def _gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _find_existing_comment(repo: str, pr: int) -> int | None:
    """Return the comment ID that contains the sentinel, or None."""
    raw = _gh(
        "api",
        f"repos/{repo}/issues/{pr}/comments",
        "--jq",
        f'[.[] | select(.body | contains("{_SENTINEL}")) | .id] | first',
    )
    try:
        value = json.loads(raw)
        return int(value) if value else None
    except (ValueError, TypeError):
        return None


def _build_body(report: str, run_id: str, repo: str) -> str:
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    return (
        f"{_SENTINEL}\n"
        f"{report.rstrip()}\n\n"
        f"**Validation run**: [{run_id}]({run_url})\n"
    )


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <pr_number> <run_id>", file=sys.stderr)
        sys.exit(1)

    pr_number = int(sys.argv[1])
    run_id = sys.argv[2]
    report = sys.stdin.read()

    repo = _gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    body = _build_body(report, run_id, repo)

    existing_id = _find_existing_comment(repo, pr_number)
    if existing_id:
        _gh(
            "api",
            f"repos/{repo}/issues/comments/{existing_id}",
            "--method", "PATCH",
            "--field", f"body={body}",
        )
        print(f"Updated PR comment {existing_id}.")
    else:
        _gh(
            "api",
            f"repos/{repo}/issues/{pr_number}/comments",
            "--method", "POST",
            "--field", f"body={body}",
        )
        print(f"Created PR comment on PR #{pr_number}.")


if __name__ == "__main__":
    main()
