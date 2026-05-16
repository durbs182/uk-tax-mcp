# Rules Changelog

This file records every change to rules in the registry. Each entry is created when a rule is added, updated, or deprecated in response to a Budget, Finance Act, or HMRC policy update.

## Entry format

```
## YYYY-MM-DD — <Event name>

| Field | Value |
|---|---|
| **Budget / Finance Act date** | YYYY-MM-DD |
| **HMRC publication** | e.g. Autumn Budget 2025, Finance Act 2026 s.12 |
| **Affected rule IDs** | comma-separated list, e.g. `income_tax_bands.2026-27`, `pa.taper.2026-27` |
| **Jurisdictions** | rUK / Scotland / both |
| **What changed** | One-paragraph plain-English description of the change |
| **HMRC source** | Direct link to the relevant GOV.UK page, HMRC manual section, or legislation.gov.uk reference |
| **Legislation reference** | e.g. ITEPA 2003 s.35, FA 2026 s.12 |
| **Updated by** | name / email / GitHub handle |
| **PR** | Link to the GitHub pull request |
```

---

## 2026-05-16 — Initial registry baseline

| Field | Value |
|---|---|
| **Budget / Finance Act date** | 2024-10-30 (Autumn Budget 2024) |
| **HMRC publication** | Autumn Budget 2024 |
| **Affected rule IDs** | All rules in `rules/rUK/` and `rules/scotland/` (406 rules across 6 tax years: 2025-26 through 2030-31) |
| **Jurisdictions** | rUK and Scotland |
| **What changed** | Initial rule registry created. Covers income tax bands, personal allowance, PA taper, CGT rates and annual exempt amount, IHT nil-rate band, pension annual allowance, ISA limits, property and Scotland-specific bands across 2025-26 through 2030-31. All rules carry HMRC citations. All rules have `reviewed_by: null` pending human review gate. |
| **HMRC source** | https://www.gov.uk/government/collections/budget-2024 |
| **Legislation reference** | ITEPA 2003, TCGA 1992, IHTA 1984, ITA 2007 |
| **Updated by** | durbs182 |
| **PR** | Initial commit |
