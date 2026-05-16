"""Rule registry data model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Citation(BaseModel):
    label: str
    url: str


class ReviewRecord(BaseModel):
    """Structured review evidence produced by the automated review pipeline.

    Populated automatically by the rule-review-populate CI workflow after a PR
    carrying rule YAML changes is approved and merged. Fields map directly to
    immutable GitHub audit-trail data so the review is independently verifiable.
    """

    reviewer: str                       # GitHub username (preferred) or email address
    pr: int                             # GitHub PR number — links to approval record
    approved_at: datetime               # ISO 8601 timestamp of the PR approval event
    validation_run: str | None = None   # GitHub Actions run ID where stages 1–5 passed


class RuleEntry(BaseModel):
    """A single versioned, immutable tax rule entry in the registry."""

    rule_id: str
    version: str
    tax_year: str
    jurisdiction: str  # "rUK" | "scotland" | ...
    title: str
    description: str
    dsl_source: str
    ast: dict[str, Any]
    checksum: str  # SHA-256 of canonical AST (without metadata)
    citations: list[Citation]
    provenance: str  # "manual" | "nl_extracted" | "migrated"
    published_at: datetime
    review: ReviewRecord | None = None  # structured review evidence (preferred)
    reviewed_by: str | None = None      # DEPRECATED — prefer review block
    monetary_output: bool = False  # True if this rule produces a sterling monetary amount
