# Rule Review Pipeline — Design

*Status: Draft · Author: pauldurbin · Date: 2026-05-16*

---

## 1. Problem Statement

The current Stage 6 (Human Review) of the validation pipeline stores reviewer identity as a
free-text string in the rule YAML:

```yaml
reviewed_by: "John Smith"   # or an email address
```

This has three critical weaknesses:

| Weakness | Impact |
|---|---|
| **Unverifiable identity** | Any string passes Stage 6. "John Smith" could be anyone; there is no check that this person exists, approved anything, or reviewed the right values. |
| **No evidence link** | Nothing records *what* was reviewed — which PR? which CI run? which HMRC source URL was open? The YAML only records that *someone* typed a name. |
| **Labour-intensive process** | 7 manual steps; requires a separate post-merge commit to set `reviewed_by`; `RULES_CHANGELOG.md` must be hand-authored; validation must be run locally before submitting the PR. |

Under audit or due-diligence review, "John Smith wrote his name in a YAML file" is not
defensible evidence that a tax rule was correctly verified against an HMRC legislative source.

---

## 2. Design Goals

1. **Traceability** — every approved rule links to an immutable GitHub PR review record.
2. **Evidence** — the CI run that validated stages 1–5 is recorded alongside the approval.
3. **Automation** — reduce contributor steps to: edit values → open PR → fix CI failures → reviewer approves on GitHub.
4. **Simplicity** — the reviewer never touches a terminal; the contributor never sets `reviewed_by` manually.
5. **Backwards compatibility** — existing rules with `reviewed_by` set continue to pass Stage 6; migration is non-breaking.

---

## 3. Current State vs Target State

| Aspect | Current | Target |
|---|---|---|
| Review identity | Free-text string | GitHub username (OAuth-verified) |
| PR link | Not recorded | PR number → immutable GitHub audit log |
| Approval timestamp | Not recorded | ISO 8601 from GitHub PR approval event |
| Validation evidence | Not linked | GitHub Actions run ID (stages 1–5 passed) |
| HMRC source check | Reviewer's word | Structured checklist in PR description (committed to history) |
| Contributor steps | 7 manual | ~4 (edit → push → fix CI → reviewer clicks Approve) |
| Post-merge work | Separate `reviewed_by` commit | Zero — automation populates `review` block |
| Changelog | Hand-authored from scratch | Auto-drafted from PR metadata |

---

## 4. Schema Changes

### 4a. New `review` block in YAML (replaces `reviewed_by`)

```yaml
review:
  reviewer: "durbs182"                       # GitHub username (preferred) or email
  pr: 42                                     # GitHub PR number
  approved_at: "2026-05-16T15:30:00Z"        # ISO 8601 timestamp of PR approval
  validation_run: "25965821214"              # GitHub Actions run ID where stages 1–5 passed
```

**Why GitHub username?**

- **OAuth-verified identity**: GitHub guarantees the username maps to a real, authenticated person.
- **Immutable audit trail**: `GET /repos/{owner}/{repo}/pulls/{pr}/reviews` returns the exact approval record including timestamp, the reviewer's account, and what commit they approved.
- **PR diff as evidence**: the reviewer approved a specific diff — they saw exactly which YAML values changed. This is the evidence of what was reviewed.
- **No extra infrastructure**: GitHub is already the source of truth for the codebase.

**Why Actions run ID?**

- Links to the exact CI run that validated stages 1–5 for this specific rule content.
- The run log records which Docker image was used, what the DSL source was at that moment, and the full pytest output — tamper-evident and independently verifiable.

**Email as fallback**: GitHub username is strongly preferred. Email is acceptable for external reviewers (e.g. a tax counsel) who do not hold a GitHub account. Stage 6 accepts both without distinction.

### 4b. Updated `RuleEntry` model

New optional nested model added to `src/hmrc_tax_mcp/registry/model.py`:

```python
class ReviewRecord(BaseModel):
    reviewer: str                       # GitHub username or email
    pr: int                             # GitHub PR number
    approved_at: datetime               # ISO 8601 timestamp of PR approval
    validation_run: str | None = None   # GitHub Actions run ID (optional)

class RuleEntry(BaseModel):
    # ... all existing fields unchanged ...
    review: ReviewRecord | None = None  # NEW — structured review evidence
    reviewed_by: str | None = None      # DEPRECATED — kept for migration period
```

`reviewed_by` is retained so that existing YAML files continue to load without error.
Stage 6 checks `review` first; falls back to `reviewed_by` for backwards compatibility.

---

## 5. Stage 6 Enhancement

**File**: `src/hmrc_tax_mcp/validation/pipeline.py` — `_stage_human_review()`

**New logic**:

```
If review block is present:
    Validate reviewer is a non-empty string
    Validate pr is a positive integer
    Validate approved_at parses as ISO 8601
    → PASS: "Reviewed by @{reviewer} via PR #{pr} on {date}"
    → details = full review block

Else if reviewed_by is present (deprecated path):
    → PASS with warning in details:
        message = "reviewed_by is set (deprecated) — migrate to review block for full traceability"
        details = {"reviewed_by": ..., "migration": "add a structured review: block"}

Else:
    → FAIL: "No review record — open a PR and get it approved by a designated reviewer"
```

The pass/fail outcome of Stage 6 is unchanged for existing rules that have `reviewed_by` set —
they continue to pass. Only rules updated going forward are expected to carry the `review` block.

---

## 6. New GitHub Actions Workflow: Rule Validation (`rule-validation.yml`)

**Trigger**: `pull_request` (opened, synchronised, reopened) where any path matches
`src/hmrc_tax_mcp/registry/rules/**/*.yaml`.

**Purpose**: Run stages 1–5 of the validation pipeline against every changed rule and post a
structured report as a PR comment. Block the PR if any rule fails stages 1–5.

**Steps**:

1. Checkout the PR branch.
2. `pip install -e ".[dev,server,http]"`
3. Detect changed rule YAML files:
   ```bash
   git diff --name-only origin/$GITHUB_BASE_REF HEAD -- 'src/hmrc_tax_mcp/registry/rules/**/*.yaml'
   ```
4. For each changed rule, call `validate_rule()` for stages 1–5 (stage 6 is intentionally
   skipped — the `review` block is not populated until after PR approval).
5. Aggregate results into a Markdown report.
6. Find-or-update an existing PR comment tagged with `<!-- uk-tax-mcp-rule-validation -->`.
7. Emit the GitHub Actions run ID and a JSON summary of validated rules as job outputs
   (consumed by the post-merge workflow).
8. Exit with code 1 if any rule fails stages 1–5, failing the required status check.

**PR comment format**:

```
<!-- uk-tax-mcp-rule-validation -->
## Rule Validation Report

| Rule | Tax Year | Jurisdiction | Syntax | Semantic | Canonical | Execution | Worked Examples |
|---|---|---|---|---|---|---|---|
| `income_tax_bands` | 2026-27 | rUK | ✅ | ✅ | ✅ | ✅ | ✅ |
| `pa_taper` | 2026-27 | rUK | ✅ | ✅ | ✅ | ✅ | ⚠️ No examples |

⚠️ **Warnings** (non-blocking):
- `income_tax_bands`: GOV.UK URL may be stale — https://www.gov.uk/income-tax-rates

**Validation run**: [25965821214](https://github.com/durbs182/uk-tax-mcp/actions/runs/25965821214)

> Stage 6 (Human Review) is intentionally pending — it is satisfied by PR approval.
```

---

## 7. Branch Protection and CODEOWNERS

### `.github/CODEOWNERS`

```
# Any change to rule YAML files requires approval from a designated tax reviewer.
src/hmrc_tax_mcp/registry/rules/ @durbs182
```

### Branch protection rules for `main`

Configure via GitHub repository settings → Branches → Branch protection rules:

| Setting | Value |
|---|---|
| Required status checks | `validate-rules` (from `rule-validation.yml`) |
| Required approvals | 1 |
| Dismiss stale reviews on new commits | ✅ |
| Require review from code owners | ✅ |
| Allow bypass for administrators | ❌ |
| Allow direct pushes | ❌ |

Together, CODEOWNERS + branch protection make the GitHub PR approval the canonical,
machine-verifiable review record. No terminal, no YAML editing — just GitHub's approval UI.

---

## 8. New GitHub Actions Workflow: Review Population (`rule-review-populate.yml`)

**Trigger**: `push` to `main` where any path matches `src/hmrc_tax_mcp/registry/rules/**/*.yaml`.

**Purpose**: After a PR is merged, automatically populate the `review` block in each changed
rule YAML using live GitHub API data. This eliminates the manual post-merge `reviewed_by` commit.

**Steps**:

1. Parse the merge commit message or use the GitHub API to identify the originating PR number.
2. Call `GET /repos/{owner}/{repo}/pulls/{pr}/reviews` to retrieve the approving review
   (reviewer login, approval timestamp).
3. Download the artifact from the corresponding `rule-validation.yml` run to get the Actions run ID.
4. Determine which rule YAML files changed in that PR
   (compare the merge commit against its first parent).
5. For each changed rule YAML, inject or overwrite the `review:` block:
   ```yaml
   review:
     reviewer: "<approver GitHub login>"
     pr: <PR number>
     approved_at: "<approval ISO 8601 timestamp>"
     validation_run: "<run ID from rule-validation.yml>"
   ```
6. Remove the deprecated `reviewed_by` field if present.
7. Commit directly to `main`:
   `chore(rules): populate review metadata from PR #N [skip ci]`

**Required permissions**: `contents: write`, `pull-requests: read`, `actions: read`

---

## 9. RULES_CHANGELOG Automation

`rule-review-populate.yml` also auto-drafts a `RULES_CHANGELOG.md` entry and prepends it,
then includes it in the same commit as step 7 above:

```markdown
## YYYY-MM-DD — PR #N: <PR title>

| Field | Value |
|---|---|
| **Merged** | YYYY-MM-DDTHH:MM:SSZ |
| **Approved by** | @reviewer |
| **Affected rules** | `rule_id (tax_year, jurisdiction)`, ... |
| **What changed** | _See PR #N description_ |
| **PR** | [#N](https://github.com/durbs182/uk-tax-mcp/pull/N) |
| **Validation run** | [run_id](https://github.com/durbs182/uk-tax-mcp/actions/runs/run_id) |
```

The structured fields (merger, approver, rules, PR, run) are populated from API data.
"What changed" defaults to a link to the PR description; contributors can edit it afterwards.

---

## 10. PR Template

**New file**: `.github/pull_request_template.md`

The template is shown to every contributor when they open a PR. For rule change PRs it
provides the structured verification checklist that forms part of the permanent audit trail.

```markdown
## Rule update checklist

<!-- Complete this section if you are modifying any registry/rules/**/*.yaml file -->

- [ ] I have verified the updated numeric values against the HMRC source in `citations`
- [ ] The HMRC source URL is reachable and displays the values I have entered
- [ ] `version` has been bumped in each updated rule file
- [ ] `published_at` has been updated to today's date
- [ ] At least one `citations` entry carries a legislative reference (Act, SI, or HMRC manual code)

## What changed

<!-- Describe the rate/threshold change and the Budget or Finance Act that triggered it -->

## HMRC source

<!-- URL of the GOV.UK page or HMRC manual where you verified the new values -->

## Legislation reference

<!-- e.g. "ITA 2007 s.10 as amended by FA 2026 s.5" -->
```

The completed checklist is committed to the PR history and is visible to anyone who later
audits the approval record.

---

## 11. Simplified Contributor Workflow

**Before (current): 7 steps, all manual**

1. Detect Budget announcement → open GitHub issue (manual)
2. Grep `rules/` for affected files (manual)
3. Edit YAML values, citations, version, published_at (manual)
4. Run validation locally (manual terminal command)
5. Write RULES_CHANGELOG.md entry from scratch (manual, specific format)
6. Open PR, assign reviewer (manual)
7. Post-merge: set `reviewed_by` in a separate commit (manual)

**After (target): 4 steps, 2 automated**

| Step | Who | What | Automated? |
|---|---|---|---|
| 1 | Contributor | Edit rule YAML(s): value, citations, version, published_at | No |
| 2 | Contributor | Open PR; fill in template (HMRC source URL, what changed, legislation ref) | No |
| 3 | CI | Run stages 1–5; post validation report as PR comment; block on failure | **Yes** |
| 3b | Contributor | Fix any stage 1–5 failures surfaced in the PR comment | No (when needed) |
| 4 | Tax reviewer | Read validation report; open HMRC source URL; verify values; click Approve | No |
| 4b | CI | Populate `review` block in merged YAMLs; draft RULES_CHANGELOG entry; commit | **Yes** |

**What the reviewer does** — entirely within the GitHub UI, no terminal required:

- Read the automated validation report comment (stages 1–5 pass/fail per rule)
- Review the PR diff (exactly which values changed)
- Open the HMRC source URL from the PR description; confirm values match
- Click "Approve" (and optionally add a note)

---

## 12. Implementation Phases

| Phase | Deliverables | Key files |
|---|---|---|
| P1 | `ReviewRecord` Pydantic model; Stage 6 enhanced to check `review` block with backwards compat; updated tests | `src/hmrc_tax_mcp/registry/model.py`, `src/hmrc_tax_mcp/validation/pipeline.py`, `tests/unit/test_validation.py` |
| P2 | `rule-validation.yml` workflow — detect changed rules, run stages 1–5, post PR comment, block on failure | `.github/workflows/rule-validation.yml`, `scripts/validate_changed_rules.py` |
| P3 | `.github/CODEOWNERS`; update `CONTRIBUTING.md` with new workflow and branch protection setup instructions | `.github/CODEOWNERS`, `CONTRIBUTING.md` |
| P4 | `rule-review-populate.yml` — post-merge: read PR approvals, inject `review` block, commit | `.github/workflows/rule-review-populate.yml` |
| P5 | RULES_CHANGELOG auto-draft appended in P4 workflow; PR template | `.github/pull_request_template.md`, `rule-review-populate.yml` |
| P6 | Migration script: convert existing `reviewed_by` strings to `review` blocks (optional; all current rules have `reviewed_by: null`) | `scripts/migrate_reviewed_by.py` |

---

## 13. Open Questions and Decisions

| Question | Decision |
|---|---|
| Is `validation_run` required in the `review` block? | No — optional. Rules reviewed before the automation existed will not have it. Stage 6 does not require it. |
| What if a merge bypasses a PR (e.g. direct push to main)? | Block direct pushes via branch protection. `rule-review-populate.yml` logs a warning and skips population if no PR is found; rules remain unreviewed (Stage 6 fails). |
| Should Stage 6 call the GitHub API to verify the PR number is genuine? | No — keep Stage 6 self-contained and offline-capable. The audit trail is in GitHub independently; Stage 6 validates structure, not authenticity. |
| Should `rule-review-populate.yml` open its own PR rather than committing directly to main? | No — commit directly to main with `[skip ci]`. The content review already occurred; this is mechanical metadata population, not a code change. |
| GitHub username vs email for `reviewer`? | GitHub username strongly preferred. Email acceptable for external reviewers without GitHub accounts. Stage 6 accepts both. |
| What if there are multiple approvers on a PR? | Record the first approver. Future enhancement: record all approvers as a list. |
