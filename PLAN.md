# PLAN.md — hmrc-tax-mcp

Deterministic HMRC UK tax rule engine exposed via the Model Context Protocol.  
Owner: NxLap Ltd | Repo: `durbs182/hmrc-tax-mcp` (private)

---

## Problem Statement

AI agents (Claude, Copilot, Codex) should orchestrate and explain UK tax strategies — not compute them. This server provides the deterministic calculation layer:

- LLMs call MCP tools → this server computes → LLMs explain results
- All rules are versioned, hashed, and cited against HMRC sources
- All arithmetic uses `decimal.Decimal` (no floating-point tax errors)
- Rules are immutable once published; updates create new versions

---

## Architecture

| Layer | Module | Purpose |
|-------|--------|---------|
| AST | `ast/schema.py`, `ast/canonical.py` | Canonical, sandboxed rule representation + SHA-256 hashing |
| Evaluator | `evaluator.py` | Safe, Decimal-precise AST execution engine |
| DSL | `dsl/tokenizer.py`, `dsl/parser.py`, `dsl/compiler.py` | Human-writable rule language → AST |
| Rule Registry | `registry/model.py`, `registry/store.py`, `registry/rules/` | Versioned, hashed, citable rule store |
| Validation Pipeline | `validation/pipeline.py` | 6-stage pipeline incl. HMRC worked examples |
| MCP Server | `server.py` | Stateless tool layer for AI agents (stdio transport) |
| NL Extractor | `extractor/nl_extractor.py` | LLM-assisted HMRC prose → DSL (human-reviewed) |

---

## Implementation Status

### Phase 1 — Repo Scaffold & Core AST/Evaluator ✅
- [x] `repo-scaffold` — GitHub repo, pyproject.toml, src layout, CI workflow
- [x] `ast-schema` — AST node types (Pydantic), JSON schema, forward-ref rebuild
- [x] `evaluator` — Decimal-precise evaluator, depth limit, trace, strict boolean control-flow semantics

### Phase 2 — DSL + Parser + Compiler ✅
- [x] `dsl-tokenizer` — Regex tokenizer: IDENT, NUMBER, OP, KEYWORD, PUNCT, NEWLINE
- [x] `dsl-parser` — Recursive-descent parser: let, return, if/then/else, bands, taper
- [x] `dsl-compiler` — Parse tree → canonical AST with deterministic compile-error normalisation

### Phase 3 — Rule Registry & 2025–26 rUK Rule Set ✅
- [x] `rule-registry` — YAML store, lazy loading, get_rule(), list_rules(), get_rule_snapshot()
- [x] Published registry now spans multiple tax years, rUK and Scotland, with full worked-example coverage

### Phase 4 — Validation Pipeline ✅
- [x] `validation-pipeline` — 6 stages: syntax → semantic → canonicalisation → execution → worked examples → human review gate
- [x] Worked example YAML files for the full published registry (`86/86` rule/example files present)
- [x] `validate_rule` MCP tool wired into server.py
- [x] Validation now auto-loads worked examples by rule identity and enforces them for monetary/publication-ready rules

### Phase 5 — MCP Server (full) ✅
- [x] `mcp-server` — `explain_rule` tool: AST walker → human-readable prose + variable list + citations
- [x] `mcp-server` — `trace_execution` tool: full step-by-step audit trace with node-level inputs/outputs
- [x] `explainer.py` — deterministic AST → prose; comma-formatted numbers; coverage for CONST, BAND_APPLY, TAPER, IF, arithmetic nodes
- [x] Server import path now degrades cleanly when optional MCP runtime dependencies are absent

### Phase 6 — NL Extractor ✅
- [x] `nl-extractor` — `NLExtractor` class: HMRC prose → draft DSL via Anthropic Claude
- [x] `ExtractionResult` dataclass: `reviewed_by`, `requires_review`, `to_registry_dict()`, `warnings`
- [x] `_parse_response()`: JSON delimiter (`<<<JSON / JSON>>>`), markdown fence stripping, fallback handling
- [x] `extract_rule` MCP tool wired into server.py — returns draft, checksum, compile error, review gate
- [x] All output permanently tagged `reviewed_by: null` — mandatory human review before publication
- [x] Extracted draft citations and provenance now normalise to the registry contract

### Phase 7 — Scottish Income Tax ✅
- [x] `scotland-rules` — 6 Scotland YAML rules: income_tax_bands (6-band: starter/basic/intermediate/higher/advanced/top), pa_taper, savings_allowance_starter, savings_allowance_basic, savings_allowance_higher, dividend_allowance
- [x] Registry key updated to `{rule_id}@{version}@{jurisdiction}` — supports same rule_id across jurisdictions
- [x] `get_rule()` gains optional `jurisdiction` parameter for disambiguation
- [x] Worked examples for Scotland income_tax_bands and pa_taper, plus coverage files for all currently published Scottish rules
- [x] Scottish rule coverage now includes worked-example files for all currently published Scottish rules

### Phase 8 — Integration ✅
- [x] `integration-docs` — `docs/integration/later-life-planner.md`: full integration guide
  - Architecture diagram (projectionEngine + AI agent → MCP server)
  - Running the server (stdio transport, MCP client config)
  - Replacing `financialConstants.ts` with `tax.get_rule_snapshot` + `execute_rule`
  - Checksum verification pattern (TypeScript)
  - Scottish taxpayer handling: jurisdiction field in plan model, 6-band income tax table
  - Agent-driven explanation workflows (trace_execution, validate_rule, extract_rule)
  - `/api/mcp` proxy route with security allowlist (extract_rule blocked from browser)
  - Full projection call sequence worked example (Scottish £35k taxpayer)
  - Roadmap for future rules (2026-27, Wales, NI, MPAA, tapered AA)

### Phase 9 — Post-Review Hardening ✅
- [x] `dsl-errors` — syntax and parser failures normalised into deterministic `CompileError` responses
- [x] `bool-semantics` — `IF` / `AND` / `OR` / `NOT` require explicit boolean operands; numeric truthiness rejected
- [x] `let-explainer` — `LET` bindings explained in-order and excluded from free-variable reporting
- [x] `worked-examples` — full registry coverage added, preferring HMRC/GOV.UK worked examples where available and clearly marking arithmetic derivations otherwise
- [x] `test-suite` — public-surface regression tests added; **405 total tests passing**

---

## DSL Syntax Reference

```
# Arithmetic
return income - 12570

# Bands (compiles to BAND_APPLY)
bands taxable_income:
  0 to 37700 at 20%
  37700 to 125140 at 40%
  125140+ at 45%

# Taper (compiles to TAPER)
taper adjusted_net_income:
  threshold 100000
  ratio 1 per 2
  base 12570

# LET bindings
let pa = 12570
let threshold = 100000
return pa - threshold

# Function call
percent(50000, 20)
```

---

## MCP Tools

| Tool | Status | Purpose |
|------|--------|---------|
| `list_rules` | ✅ live | List all rule IDs and versions |
| `get_rule` | ✅ live | DSL, AST, metadata for a rule |
| `execute_rule` | ✅ live | Run rule with inputs → output + trace |
| `tax.get_rule_snapshot` | ✅ live | Full rule set for tax year + jurisdiction |
| `compile_dsl` | ✅ live | DSL text → AST + SHA-256 checksum |
| `validate_rule` | ✅ live | Full 6-stage validation pipeline |
| `explain_rule` | ✅ live | Human-readable rule explanation |
| `trace_execution` | ✅ live | Full execution trace for audit |

---

## Foundational 2025–26 rUK Rule Set ✅

| Rule ID | Description | HMRC Source |
|---------|-------------|-------------|
| `income_tax_bands` | Basic/higher/additional rate bands + nil band | [Income tax rates](https://www.gov.uk/income-tax-rates) |
| `pa_taper` | Personal allowance taper (£100k threshold, £12,570 base) | [Income tax rates](https://www.gov.uk/income-tax-rates) |
| `cgt_exempt` | CGT annual exempt amount (£3,000) | [CGT allowances](https://www.gov.uk/capital-gains-tax/allowances) |
| `cgt_rates` | CGT rates: 24% higher, 18% basic | [CGT rates](https://www.gov.uk/capital-gains-tax/rates) |
| `pension_ufpls_tax_free_fraction` | UFPLS tax-free fraction (0.25) | [Tax on pension](https://www.gov.uk/tax-on-pension) |
| `pension_ufpls_taxable_fraction` | UFPLS taxable fraction (0.75) | [Tax on pension](https://www.gov.uk/tax-on-pension) |
| `pension_lsa` | Lump Sum Allowance £268,275 | [Pension scheme rates](https://www.gov.uk/government/publications/rates-and-allowances-pension-schemes/pension-schemes-rates) |
| `state_pension_annual` | Full new state pension £11,502.40/year | [New State Pension](https://www.gov.uk/new-state-pension) |
| `savings_allowance_basic` | PSA basic rate £1,000 | [Tax on savings](https://www.gov.uk/apply-tax-free-interest-on-savings) |
| `savings_allowance_higher` | PSA higher rate £500 | [Tax on savings](https://www.gov.uk/apply-tax-free-interest-on-savings) |
| `dividend_allowance` | Dividend allowance £500 | [Tax on dividends](https://www.gov.uk/tax-on-dividends) |

---

## Design Principles

- **No Turing-complete rules** — no loops, no recursion, no `eval()`
- **Stateless per request** — evaluator is pure with no side effects
- **SHA-256 rule hashing** — canonical JSON for legal reproducibility
- **Human review gate** — required before any rule is published
- **HMRC citations required** — every rule entry must reference source URLs
- **MCP transport** — stdio (local); HTTP SSE can be added later

---

## Key Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Language | Python | All mcp.md spec code is Python; Decimal precision |
| Transport | stdio | Simplest to start; SSE for cloud later |
| Python version | >=3.9 (dev), 3.11 (CI) | System constraint; mcp package needs 3.10+ |
| Packaging | git-dep initially | PyPI when stable |
| LLM for NL extractor | Anthropic Claude | Already used in later-life-planner |

## Recent changes (2026-04-06)

- Merged `feat/current-tax-year` into `main` (commit d6f4b80).
  - Adds `src/hmrc_tax_mcp/registry/tax_year.py` with `tax_year_for_date()` and
    `current_tax_year()` helpers.
  - `current_tax_year()` is timezone-aware (Europe/London) and returns the
    current UK tax year string (e.g. "2026-27").
  - MCP server tool handlers now default `tax_year` to `current_tax_year()` when
    callers omit it, preventing accidental use of future-year rules.
  - Unit tests added (tests/unit/test_tax_year.py) and the full unit suite passes.

Next steps:
- Monitor Copilot MCP tool connections; restart the client if the stdio proxy
  loses its connection after a local server reinstall.
- Consider adding an endpoint `GET /api/tax-year/current` for clients that want
  to display the currently-applied tax year.

