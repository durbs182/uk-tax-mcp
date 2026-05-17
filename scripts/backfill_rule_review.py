#!/usr/bin/env python3
"""Create a backfill review PR for unreviewed rule YAMLs.

Called via the backfill-rule-review workflow_dispatch workflow (or locally).
Updates published_at to today on each specified rule to produce a minimal,
auditable diff, then opens a PR so the normal pipeline runs:
  validate-rules CI → reviewer approves → rule-review-populate writes review block.

Usage:
    python scripts/backfill_rule_review.py <rule_file> [<rule_file> ...]
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=check)
    return result.stdout.strip()


def _gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0 and check:
        print(f"::error::gh {' '.join(args[:2])} failed: {result.stderr.strip()}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, ["gh", *args], result.stdout, result.stderr)
    return result.stdout.strip()


def _update_published_at(content: str, today: str) -> tuple[str, bool]:
    new_content = re.sub(
        r'^published_at:.*$',
        f'published_at: "{today}T00:00:00Z"',
        content,
        flags=re.MULTILINE,
    )
    return new_content, new_content != content


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: backfill_rule_review.py <rule_file> [...]", file=sys.stderr)
        sys.exit(1)

    paths = [Path(p) for p in sys.argv[1:]]
    today = date.today().isoformat()

    valid_paths: list[Path] = []
    rule_ids: list[str] = []
    citation_lines: list[str] = []

    for path in paths:
        if not path.exists():
            print(f"::warning::File not found: {path}", file=sys.stderr)
            continue
        try:
            rule = yaml.safe_load(path.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Could not parse {path}: {exc}", file=sys.stderr)
            continue
        valid_paths.append(path)
        rule_id = rule.get("rule_id", path.stem)
        rule_ids.append(rule_id)
        for cit in rule.get("citations") or []:
            url = cit.get("url", "")
            if url:
                citation_lines.append(f"- `{rule_id}`: {url}")

    # Build branch name after collecting rule_ids so it includes a slug.
    # Limit to 50 chars to stay well inside Git's ref length limit.
    slug = "-".join(rule_ids)[:50].rstrip("-")
    branch = f"backfill/{today}-{slug}"

    if not valid_paths:
        print("No valid rule files to backfill.", file=sys.stderr)
        sys.exit(1)

    updated: list[Path] = []
    for path in valid_paths:
        content = path.read_text()
        new_content, changed = _update_published_at(content, today)
        if changed:
            path.write_text(new_content)
            updated.append(path)
            print(f"Updated published_at in {path}")
        else:
            print(f"Skipped {path} — published_at already set to {today}")

    if not updated:
        print("All specified rules already have today's published_at date — nothing to commit.")
        sys.exit(0)

    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
    _git("checkout", "-b", branch)

    for path in updated:
        _git("add", str(path))

    rule_list = ", ".join(rule_ids)
    commit_msg = f"chore(rules): backfill review — reaffirm values as of {today} for {rule_list}"
    _git("commit", "-m", commit_msg)
    _git("push", "-u", "origin", branch)

    citation_block = (
        "\n".join(citation_lines)
        if citation_lines
        else "_See `citations` in each rule file._"
    )
    rule_display = ", ".join(f"`{r}`" for r in rule_ids)

    body = f"""\
## Rule update checklist

<!-- Backfill review — no numeric values changed. -->

- [ ] I have verified the current values against the HMRC sources listed in `citations`
- [ ] The HMRC source URLs below are reachable and show the values in each rule
- [x] `version` unchanged — this is a reaffirmation, not a value change
- [x] `published_at` updated to {today} to confirm current values remain correct
- [x] Legislative citations already present in each rule file

## What changed

Backfill review: `published_at` updated to {today} to reaffirm that the current
values are correct as of today. No tax rates, thresholds, or rule logic changed.

Rules: {rule_display}

## HMRC source

Verify that these URLs confirm the values currently in each rule:

{citation_block}

## Legislation reference

See the existing `citations` field in each rule file.

---

> **Reviewer note:** This is a backfill PR — the diff shows only `published_at` changes.
> Open each HMRC source URL above and confirm the current rule values match the
> authoritative source before approving.
"""

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_url = _gh(
        "pr", "create",
        *(["--repo", repo] if repo else []),
        "--title", f"chore(rules): backfill review for {rule_list}",
        "--body", body,
        "--base", "main",
        "--head", branch,
    )
    print(f"Created PR: {pr_url}")


if __name__ == "__main__":
    main()
