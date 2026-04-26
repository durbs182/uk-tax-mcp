# uk-tax-mcp

A deterministic, auditable HMRC UK tax rule engine exposed via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

## Purpose

AI agents (Claude, Copilot, Codex) should orchestrate and explain tax strategies — not compute them. This server provides the deterministic calculation layer:

- **LLMs call tools** → this server computes → LLMs explain results
- All rules are versioned, SHA-256 hashed, and cited against HMRC sources
- All arithmetic uses `decimal.Decimal` (no floating-point tax errors)
- Rules are immutable once published; updates create new versions

## Architecture

| Layer | Module | Purpose |
|-------|--------|---------|
| AST | `ast/schema.py`, `ast/canonical.py` | Canonical, sandboxed rule representation + SHA-256 hashing |
| Evaluator | `evaluator.py` | Safe, Decimal-precise AST execution engine |
| DSL | `dsl/tokenizer.py`, `dsl/parser.py`, `dsl/compiler.py` | Human-writable rule language → AST |
| Rule Registry | `registry/model.py`, `registry/store.py`, `registry/rules/` | Versioned, hashed, citable YAML rule store |
| Validation Pipeline | `validation/pipeline.py` | 6-stage pipeline incl. HMRC worked examples |
| MCP Server | `server.py` | Stateless tool layer for AI agents (stdio transport) |
| NL Extractor | `extractor/nl_extractor.py` | LLM-assisted HMRC prose → DSL (mandatory human review gate) |

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                # 405 tests
uk-tax-mcp          # starts MCP server on stdio (requires Python ≥3.10 + pip install -e ".[server]")
```

## MCP Tools

| Tool | Status | Purpose |
|------|--------|---------|
| `list_rules` | ✅ live | List all rule IDs and versions |
| `get_rule` | ✅ live | Get DSL, AST, metadata for a rule |
| `execute_rule` | ✅ live | Run a rule with inputs → output + optional trace |
| `tax.get_rule_snapshot` | ✅ live | Full rule set for a tax year + jurisdiction |
| `compile_dsl` | ✅ live | Compile DSL text → AST + SHA-256 checksum |
| `validate_rule` | ✅ live | Run the 6-stage validation pipeline on a rule |
| `explain_rule` | ✅ live | Human-readable rule explanation |
| `trace_execution` | ✅ live | Structured execution trace for audit |
| `extract_rule` | ✅ live | LLM-assisted HMRC prose → draft DSL (requires ANTHROPIC_API_KEY) |

### NL Extractor & Human Review Gate

`extract_rule` submits HMRC legislative prose to Claude and returns a draft DSL rule.
**The result is always marked `reviewed_by: null`** — publication to the registry is
blocked until a human engineer:

1. Verifies every numeric value against the original HMRC source
2. Runs `validate_rule` on the compiled draft
3. Sets `reviewed_by` to their name/email in the YAML file

## Tax Years Covered

| Coverage | Status |
|----------|--------|
| Published registry | Multiple tax years from `2025-26` through `2030-31` |
| Jurisdictions | `rUK` and `scotland` |
| Worked examples | Full coverage for all published registry rules (`86/86` example files present) |

## DSL Quick Reference

```
# Bands (compiles to BAND_APPLY)
bands taxable_income:
  0      to 12570  at 0%
  12570  to 50270  at 20%
  50270  to 125140 at 40%
  125140+          at 45%

# Taper (compiles to TAPER)
taper adjusted_net_income:
  threshold 100000
  ratio 1 per 2
  base 12570

# Let bindings + conditional
let threshold = 100000
return if income > threshold then 40 else 20
```

## Validation Pipeline

Every rule passes 6 stages before publication:

| Stage | Check |
|-------|-------|
| 1 Syntax | DSL compiles without error |
| 2 Semantic | Required fields, valid provenance, ≥1 HMRC citation |
| 3 Canonicalisation | SHA-256 of recompiled DSL matches stored checksum |
| 4 Execution | Rule evaluates without error on smoke-test inputs |
| 5 Worked examples | Outputs match HMRC-published test cases |
| 6 Human review | `reviewed_by` must be set before publication |

## Build Progress

| Phase | Deliverable | Status |
|-------|------------|--------|
| 1 | Repo scaffold, AST schema, evaluator | ✅ |
| 2 | DSL tokenizer → parser → compiler | ✅ |
| 3 | Rule registry across multiple years / jurisdictions | ✅ |
| 4 | 6-stage validation pipeline with auto-loaded worked examples | ✅ |
| 5 | MCP server tools (`explain_rule`, `trace_execution`, validation, execution) | ✅ |
| 6 | NL extractor with human-review gate | ✅ |
| 7 | Scottish income tax jurisdiction support | ✅ |
| 8 | Integration guide for `later-life-planner` | ✅ |
| 9 | Post-review hardening and full worked-example coverage | ✅ |

## Design Principles

- **No Turing-complete rules** — no loops, no recursion, no `eval()`
- **Stateless per request** — evaluator is pure with no side effects
- **SHA-256 rule hashing** — canonical JSON for legal reproducibility
- **Human review gate** — required before any rule is published
- **HMRC citations** — every rule entry must reference source URLs
- **MCP transport** — stdio (local); HTTP SSE can be added later

## Integration

See **[`docs/integration/later-life-planner.md`](docs/integration/later-life-planner.md)**
for a full guide on wiring this server into `later-life-planner`, including:

- Architecture overview and transport setup
- Replacing `financialConstants.ts` with `tax.get_rule_snapshot` + `execute_rule`
- Checksum verification pattern (TypeScript)
- Scottish taxpayer handling (jurisdiction field, 6-band income tax)
- Agent-driven explanation and audit trace workflows
- `/api/mcp` proxy route with security allowlist

## HMRC Source References

- [Income tax rates and allowances](https://www.gov.uk/income-tax-rates)
- [Capital Gains Tax rates and allowances](https://www.gov.uk/capital-gains-tax/allowances)
- [Scottish income tax](https://www.gov.uk/scottish-income-tax)
- [Tax on pensions (UFPLS)](https://www.gov.uk/tax-on-pension)
- [Pension scheme rates](https://www.gov.uk/government/publications/rates-and-allowances-pension-schemes/pension-schemes-rates)
- [New State Pension](https://www.gov.uk/new-state-pension)
- [Tax on dividends](https://www.gov.uk/tax-on-dividends)
- [Tax on savings interest](https://www.gov.uk/apply-tax-free-interest-on-savings)

## Licence

Private — NxLap Ltd
