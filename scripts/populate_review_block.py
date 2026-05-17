#!/usr/bin/env python3
"""Populate the review: block in changed rule YAMLs after a PR is merged.

Called by the rule-review-populate GitHub Actions workflow after a push to main
that touches registry/rules/**/*.yaml files. Uses the GitHub API (via gh CLI)
to retrieve the first approving reviewer, then injects structured review metadata
into each changed rule file and commits the result.

Also prepends an entry to RULES_CHANGELOG.md.

Environment variables (set automatically by GitHub Actions):
    GITHUB_REPOSITORY   e.g. "durbs182/uk-tax-mcp"
    GITHUB_SHA          merge commit SHA
    GITHUB_RUN_ID       this workflow's run ID (not used; we record the validate-rules run)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml


def _gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=check
    )
    return result.stdout.strip()


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 and check:
        print(
            f"::error::git {' '.join(args[:2])} failed: {result.stderr.strip()}",
            file=sys.stderr,
        )
        raise subprocess.CalledProcessError(
            result.returncode, ["git", *args], result.stdout, result.stderr
        )
    return result.stdout.strip()


def _parse_pr_number(commit_message: str) -> int | None:
    """Extract PR number from a GitHub merge commit message."""
    # "Merge pull request #42 from ..."
    m = re.search(r"Merge pull request #(\d+)", commit_message)
    if m:
        return int(m.group(1))
    # Squash-merge: "Some title (#42)"
    m = re.search(r"\(#(\d+)\)\s*$", commit_message)
    if m:
        return int(m.group(1))
    return None


def _get_pr_data(repo: str, pr: int) -> dict:
    raw = _gh(
        "api", f"repos/{repo}/pulls/{pr}",
        "--jq", "{title: .title, mergedAt: .merged_at, headSha: .head.sha}"
    )
    return json.loads(raw)


def _get_first_approver(repo: str, pr: int) -> dict | None:
    """Return {login, submitted_at} for the first APPROVED review, or None."""
    raw = _gh("api", f"repos/{repo}/pulls/{pr}/reviews")
    reviews = json.loads(raw)
    for review in reviews:
        if review.get("state") == "APPROVED":
            return {
                "login": review["user"]["login"],
                "submitted_at": review["submitted_at"],
            }
    return None


def _get_validation_run_id(repo: str, head_sha: str) -> str | None:
    """Find the run ID of the 'validate-rules' check run on the PR head commit."""
    raw = _gh(
        "api", f"repos/{repo}/commits/{head_sha}/check-runs",
        "--jq", '[.check_runs[] | select(.name == "validate-rules")] | first | .id'
    )
    try:
        value = json.loads(raw)
        return str(int(value)) if value else None
    except (ValueError, TypeError):
        return None


def _changed_rule_files(merge_sha: str) -> list[Path]:
    """Return rule YAML files that changed between the merge commit and its first parent."""
    out = _git(
        "diff", "--name-only", f"{merge_sha}~1", merge_sha,
        "--", "src/hmrc_tax_mcp/registry/rules/**/*.yaml",
    )
    if not out:
        # git doesn't expand globs in diff; use diff-filter with pathspec
        out = _git("diff", "--name-only", f"{merge_sha}~1", merge_sha)
        paths = [
            Path(p) for p in out.splitlines()
            if p.startswith("src/hmrc_tax_mcp/registry/rules/") and p.endswith(".yaml")
        ]
    else:
        paths = [Path(p) for p in out.splitlines() if p]
    return [p for p in paths if p.exists()]


def _inject_review(content: str, reviewer: str, pr: int, approved_at: str,
                   validation_run: str | None) -> str:
    """Replace or add the review: block; remove reviewed_by: if present."""
    # Remove existing review: block (indented continuation lines)
    content = re.sub(r"^review:\n(?:  [^\n]*\n)*", "", content, flags=re.MULTILINE)
    # Remove legacy reviewed_by: line
    content = re.sub(r"^reviewed_by:.*\n", "", content, flags=re.MULTILINE)

    lines = [
        "review:\n",
        f"  reviewer: \"{reviewer}\"\n",
        f"  pr: {pr}\n",
        f"  approved_at: \"{approved_at}\"\n",
    ]
    if validation_run:
        lines.append(f"  validation_run: \"{validation_run}\"\n")

    block = "".join(lines)

    # Insert before dsl_source: so it stays in the metadata section
    if "\ndsl_source:" in content:
        content = content.replace("\ndsl_source:", f"\n{block}dsl_source:")
    else:
        content = content.rstrip("\n") + "\n" + block

    return content


def _prepend_changelog(repo: str, pr: int, pr_title: str, merged_at: str,
                       approver: str, rules: list[dict],
                       validation_run: str | None) -> None:
    changelog = Path("RULES_CHANGELOG.md")
    today = date.today().isoformat()

    rule_list = ", ".join(
        f"`{r.get('rule_id', '?')} ({r.get('tax_year', '?')}, {r.get('jurisdiction', '?')})`"
        for r in rules
    )

    run_cell = (
        f"[{validation_run}](https://github.com/{repo}/actions/runs/{validation_run})"
        if validation_run else "_not recorded_"
    )

    entry = (
        f"## {today} — PR #{pr}: {pr_title}\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| **Merged** | {merged_at} |\n"
        f"| **Approved by** | @{approver} |\n"
        f"| **Affected rules** | {rule_list} |\n"
        f"| **What changed** | _See [PR #{pr}](https://github.com/{repo}/pull/{pr})_ |\n"
        f"| **PR** | [#{pr}](https://github.com/{repo}/pull/{pr}) |\n"
        f"| **Validation run** | {run_cell} |\n\n"
        f"---\n\n"
    )

    if changelog.exists():
        existing = changelog.read_text()
        # Prepend after the first heading (# RULES_CHANGELOG) if present
        if existing.startswith("# "):
            first_nl = existing.index("\n") + 1
            changelog.write_text(existing[:first_nl] + "\n" + entry + existing[first_nl:])
        else:
            changelog.write_text(entry + existing)
    else:
        changelog.write_text(f"# Rules Changelog\n\n{entry}")


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    merge_sha = os.environ.get("GITHUB_SHA", "")

    if not repo or not merge_sha:
        print("GITHUB_REPOSITORY and GITHUB_SHA must be set.", file=sys.stderr)
        sys.exit(1)

    commit_message = _git("log", "-1", "--format=%B", merge_sha)
    pr_number = _parse_pr_number(commit_message)

    if not pr_number:
        print(
            "::warning::Could not identify a PR number from commit message — "
            "review block will not be populated.",
            file=sys.stderr,
        )
        sys.exit(0)

    pr_data = _get_pr_data(repo, pr_number)
    approver = _get_first_approver(repo, pr_number)

    if not approver:
        print(
            f"::warning::PR #{pr_number} has no approving review — "
            f"review block will not be populated.",
            file=sys.stderr,
        )
        sys.exit(0)

    validation_run = _get_validation_run_id(repo, pr_data["headSha"])

    changed = _changed_rule_files(merge_sha)
    if not changed:
        print("No changed rule files — nothing to populate.")
        sys.exit(0)

    rule_dicts: list[dict] = []
    updated: list[Path] = []

    for path in changed:
        content = path.read_text()
        try:
            rule_dict = yaml.safe_load(content)
        except Exception as exc:
            print(f"::warning::Could not parse {path}: {exc}", file=sys.stderr)
            continue

        new_content = _inject_review(
            content,
            reviewer=approver["login"],
            pr=pr_number,
            approved_at=approver["submitted_at"],
            validation_run=validation_run,
        )
        path.write_text(new_content)
        rule_dicts.append(rule_dict)
        updated.append(path)
        print(f"Populated review block in {path}.")

    if not updated:
        print("No rule files were updated.")
        sys.exit(0)

    _prepend_changelog(
        repo=repo,
        pr=pr_number,
        pr_title=pr_data["title"],
        merged_at=pr_data["mergedAt"],
        approver=approver["login"],
        rules=rule_dicts,
        validation_run=validation_run,
    )

    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")

    for path in updated:
        _git("add", str(path))
    _git("add", "RULES_CHANGELOG.md")

    _git(
        "commit", "-m",
        f"chore(rules): populate review metadata from PR #{pr_number} [skip ci]",
    )
    _git("push")
    print(f"Committed and pushed review metadata for PR #{pr_number}.")


if __name__ == "__main__":
    main()
