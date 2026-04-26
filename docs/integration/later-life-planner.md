# Integration Guide — `uk-tax-mcp` with `later-life-planner`

**Status:** Active  
**Owner:** NxLap Ltd  
**Repo:** [`durbs182/uk-tax-mcp`](https://github.com/durbs182/uk-tax-mcp)  
**Target client:** [`pauldurbin/later-life-planner`](https://github.com/pauldurbin/later-life-planner)

---

## 1. Purpose

`uk-tax-mcp` provides a **deterministic, auditable HMRC tax rule engine** exposed
via the Model Context Protocol (MCP). `later-life-planner` is a UK retirement planning
SaaS that currently embeds simplified tax constants directly in TypeScript.

This guide documents how to:

- Wire `uk-tax-mcp` as an MCP server to the `later-life-planner` AI layer
- Replace hardcoded `financialConstants.ts` values with live MCP tool calls
- Use `execute_rule` to power the withdrawal waterfall
- Use `explain_rule` and `trace_execution` to generate user-facing audit narratives
- Handle Scottish taxpayers correctly

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                later-life-planner                   │
│                                                     │
│  ┌────────────────┐   ┌──────────────────────────┐  │
│  │ projectionEngine│   │  Claude / Copilot agent  │  │
│  │   (TypeScript) │   │  (Anthropic / GH Copilot)│  │
│  └───────┬────────┘   └────────────┬─────────────┘  │
│          │  direct rule calls      │  MCP calls      │
└──────────┼─────────────────────────┼────────────────┘
           │                         │
           └─────────────────────────▼
                    ┌────────────────────────┐
                    │    uk-tax-mcp        │
                    │  (stdio MCP server)    │
                    │                        │
                    │  • execute_rule        │
                    │  • explain_rule        │
                    │  • trace_execution     │
                    │  • validate_rule       │
                    │  • get_rule_snapshot   │
                    └────────────────────────┘
```

There are **two integration surfaces**:

1. **Direct rule calls** — `projectionEngine.ts` calls the MCP server at tax-year
   startup to fetch the validated, hashed rule set for the household's jurisdiction.
   Tax calculations are then performed via `execute_rule` instead of local constants.

2. **Agent-driven calls** — the Claude / Copilot agent calls `explain_rule`,
   `trace_execution`, and `validate_rule` when generating explanations for the user
   or auditing a projection result.

---

## 3. Running the MCP Server

### Prerequisites

```bash
python >=3.10
pip install 'uk-tax-mcp[server]'       # installs mcp[cli] transport
# For NL extraction:
pip install 'uk-tax-mcp[server,extractor]'
export ANTHROPIC_API_KEY=sk-...
```

### Start the server (stdio transport)

```bash
uk-tax-mcp
# or:
python -m hmrc_tax_mcp.server
```

The server uses **stdio transport** — it reads JSON-RPC requests from stdin and writes
responses to stdout. No network port is opened.

### MCP client configuration (Claude Desktop / Copilot)

Add to your MCP client config (e.g. `~/.config/claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hmrc-tax": {
      "command": "uk-tax-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"
      }
    }
  }
}
```

For later-life-planner's `generate-vision` and Anthropic API routes, add the MCP
server as a tool provider in the Anthropic client options:

```typescript
// src/app/api/generate-vision/route.ts
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic();
const response = await client.messages.create({
  model: "claude-3-5-sonnet-20241022",
  max_tokens: 2048,
  tools: [...existingTools],
  // uk-tax-mcp tools are injected here by the MCP host
});
```

---

## 4. Replacing `financialConstants.ts` with MCP Rule Lookups

### Current approach (hardcoded)

```typescript
// src/config/financialConstants.ts
export const INCOME_TAX_BANDS_2024_25 = [
  { lower: 0,      upper: 12570,  rate: 0.0  },
  { lower: 12570,  upper: 50270,  rate: 0.2  },
  { lower: 50270,  upper: 125140, rate: 0.4  },
  { lower: 125140, upper: null,   rate: 0.45 },
];
export const PERSONAL_ALLOWANCE = 12570;
export const PERSONAL_ALLOWANCE_TAPER_THRESHOLD = 100000;
```

This couples the TypeScript application directly to HMRC constants and requires a
code deployment to update tax year values.

### Proposed approach (MCP-driven)

At session start, call `tax.get_rule_snapshot` to fetch all rules for the user's
tax year and jurisdiction. Cache the snapshot in the Zustand store:

```typescript
// src/hooks/useTaxRules.ts
import { usePlannerStore } from "@/store/plannerStore";

export async function fetchTaxRuleSnapshot(
  taxYear: string,
  jurisdiction: "rUK" | "scotland"
): Promise<TaxRuleSnapshot> {
  const response = await fetch("/api/mcp", {
    method: "POST",
    body: JSON.stringify({
      tool: "tax.get_rule_snapshot",
      arguments: { tax_year: taxYear, jurisdiction },
    }),
  });
  const { rules } = await response.json();
  return rules; // Array of RuleEntry objects with AST + checksum
}
```

Then replace direct constant access with rule execution:

```typescript
// src/financialEngine/taxCalculations.ts (updated)
export async function computeIncomeTax(
  taxableIncome: number,
  jurisdiction: "rUK" | "scotland",
  mcpClient: MCPClient
): Promise<number> {
  const result = await mcpClient.callTool("execute_rule", {
    rule_id: "income_tax_bands",
    inputs: { taxable_income: taxableIncome },
    jurisdiction,          // passed as a filter at the snapshot level
  });
  return Number(result.output);
}
```

> **Note:** For performance, pre-fetch the entire rule snapshot at session start and
> pass rule objects directly to a lightweight local evaluator rather than making an
> MCP round-trip per tax year per person. The MCP server is best used for rule
> *resolution* (fetch + verify checksum) rather than per-call invocation in a
> tight projection loop.

---

## 5. Projection Engine Integration

### Key rules consumed by `projectionEngine.ts`

| Financial constant | Rule ID | Jurisdiction |
|--------------------|---------|-------------|
| Income tax bands | `income_tax_bands` | `rUK` or `scotland` |
| Personal allowance taper | `pa_taper` | `rUK` or `scotland` |
| CGT annual exemption | `cgt_exempt` | `rUK` |
| CGT rates | `cgt_rates` | `rUK` |
| UFPLS tax-free fraction | `pension_ufpls_tax_free_fraction` | `rUK` |
| UFPLS taxable fraction | `pension_ufpls_taxable_fraction` | `rUK` |
| Pension lump sum allowance | `pension_lsa` | `rUK` |
| State pension (annual) | `state_pension_annual` | `rUK` |
| Savings allowance (basic) | `savings_allowance_basic` | `rUK` or `scotland` |
| Savings allowance (higher) | `savings_allowance_higher` | `rUK` or `scotland` |
| Dividend allowance | `dividend_allowance` | `rUK` or `scotland` |

### Recommended initialisation pattern

```typescript
// src/financialEngine/ruleLoader.ts

export interface TaxRuleSet {
  incomeTaxBands: RuleEntry;
  paTaper: RuleEntry;
  cgtExempt: RuleEntry;
  cgtRates: RuleEntry;
  pensionLsa: RuleEntry;
  ufplsTaxFree: RuleEntry;
  ufplsTaxable: RuleEntry;
  statePension: RuleEntry;
  savingsAllowanceBasic: RuleEntry;
  savingsAllowanceHigher: RuleEntry;
  dividendAllowance: RuleEntry;
}

export async function loadTaxRules(
  taxYear: string,
  jurisdiction: "rUK" | "scotland"
): Promise<TaxRuleSet> {
  // Single call to get all rules for this tax year + jurisdiction
  const snapshot = await mcpClient.callTool("tax.get_rule_snapshot", {
    tax_year: taxYear,
    jurisdiction,
  });

  const byId = Object.fromEntries(
    snapshot.rules.map((r: RuleEntry) => [r.rule_id, r])
  );

  return {
    incomeTaxBands:        byId["income_tax_bands"],
    paTaper:               byId["pa_taper"],
    cgtExempt:             byId["cgt_exempt"],
    cgtRates:              byId["cgt_rates"],
    pensionLsa:            byId["pension_lsa"],
    ufplsTaxFree:          byId["pension_ufpls_tax_free_fraction"],
    ufplsTaxable:          byId["pension_ufpls_taxable_fraction"],
    statePension:          byId["state_pension_annual"],
    savingsAllowanceBasic: byId["savings_allowance_basic"],
    savingsAllowanceHigher:byId["savings_allowance_higher"],
    dividendAllowance:     byId["dividend_allowance"],
  };
}
```

### Checksum verification

Each `RuleEntry` includes a `checksum` (SHA-256 of the canonical AST). Verify it
matches an expected value before using a rule in a projection:

```typescript
import { createHash } from "crypto";

function verifyRuleChecksum(rule: RuleEntry): void {
  const canonicalAst = JSON.stringify(sortKeys(rule.ast));
  const computed = createHash("sha256").update(canonicalAst).digest("hex");
  if (computed !== rule.checksum) {
    throw new Error(
      `Rule ${rule.rule_id}@${rule.version} checksum mismatch. ` +
      `Expected ${rule.checksum}, got ${computed}. ` +
      "Do not use this rule — it may have been tampered with."
    );
  }
}
```

---

## 6. Scottish Taxpayer Handling

Later-life-planner currently does not capture jurisdiction or Scottish taxpayer status
(see the design doc `docs/withdrawal-optimizer-mcp-design.md`). Adding Scottish support
requires:

### 6.1 Capture jurisdiction in the plan model

```typescript
// src/models/types.ts (add to PersonalDetails or HouseholdDetails)
export type TaxJurisdiction = "rUK" | "scotland";

export interface PersonalDetails {
  // ... existing fields
  taxJurisdiction: TaxJurisdiction;   // new field
}
```

### 6.2 Pass jurisdiction to `loadTaxRules`

```typescript
const rules = await loadTaxRules(
  "2025-26",
  plan.personalDetails.taxJurisdiction   // "rUK" or "scotland"
);
```

### 6.3 Scottish income tax bands (6 bands vs rUK's 4)

The `income_tax_bands` rule for `scotland` has 6 bands:

| Band | Range | Rate |
|------|-------|------|
| Nil | £0–£12,570 | 0% |
| Starter | £12,570–£15,397 | 19% |
| Basic | £15,397–£27,491 | 20% |
| Intermediate | £27,491–£43,662 | 21% |
| Higher | £43,662–£75,000 | 42% |
| Advanced | £75,000–£125,140 | 45% |
| Top | £125,140+ | 48% |

The same `execute_rule` call works for both jurisdictions — the rule selected differs
based on the snapshot loaded.

### 6.4 Savings and dividend income (UK-wide)

Savings interest and dividends use UK-wide rules regardless of Scottish taxpayer
status. The `savings_allowance_*` and `dividend_allowance` rules are present in
both the `rUK` and `scotland` snapshots for this reason.

---

## 7. Agent-Driven Explanation Workflows

### 7.1 Explaining a tax calculation to the user

When the user asks "why is my tax bill £X?", the agent should:

1. Call `trace_execution` on the relevant rule with the user's inputs
2. Use the step-by-step trace to construct a plain-English narrative
3. Cite the HMRC URL from the rule's `citations` array

```
Agent: trace_execution
  rule_id: "income_tax_bands"
  inputs: { taxable_income: 35000 }

→ Returns 7 trace steps showing band application:
  step 1: VAR taxable_income → 35000
  step 2: BAND 0–12570 at 0% → 0
  step 3: BAND 12570–15397 at 19% → 537.13  (Scotland)
  step 4: BAND 15397–27491 at 20% → 2418.80
  step 5: BAND 27491–35000 at 21% → 1576.89
  step 6: SUM → 4532.82
  ...
```

The agent then narrates: "Your £35,000 income means you pay starter rate (19%) on
£2,827, basic rate (20%) on £12,094, and intermediate rate (21%) on £7,509, giving
a total of £4,532.82."

### 7.2 Validating a rule before use in a projection

```
Agent: validate_rule
  rule_id: "income_tax_bands"
  jurisdiction: "scotland"

→ Returns 6 pipeline stages:
  syntax ✅  semantic ✅  canonicalisation ✅
  execution ✅  worked_examples ✅  human_review ❌ (reviewed_by: null)
```

Rules that fail any stage before `human_review` must not be used in projections.
Rules with `human_review: ❌` are pre-publication and should be treated as drafts.

### 7.3 Extracting a new rule from HMRC guidance

If HMRC publishes updated rates mid-year:

```
Agent: extract_rule
  hmrc_text: "The dividend allowance for 2025-26 is £500..."

→ Returns draft DSL + metadata + requires_review: true
```

The draft is tagged `reviewed_by: null` and must be reviewed by an engineer before
being committed to the registry. After review, run `validate_rule` to confirm all
6 stages pass, then commit the YAML with `reviewed_by` set.

---

## 8. API Route — `/api/mcp`

Add a thin Next.js API route to proxy MCP tool calls from the browser:

```typescript
// src/app/api/mcp/route.ts
import { requireUser } from "@/lib/auth/requireUser";
import { NextResponse } from "next/server";
import { spawn } from "child_process";

export async function POST(request: Request) {
  await requireUser();

  const { tool, arguments: args } = await request.json();

  // Allowlist — only permit read-only tools from the browser
  const ALLOWED_TOOLS = new Set([
    "list_rules",
    "get_rule",
    "execute_rule",
    "tax.get_rule_snapshot",
    "explain_rule",
    "trace_execution",
    "validate_rule",
  ]);

  if (!ALLOWED_TOOLS.has(tool)) {
    return NextResponse.json({ error: "Tool not permitted" }, { status: 403 });
  }

  // In production: use a persistent MCP client connection, not a new process per call
  const result = await callMcpTool(tool, args);
  return NextResponse.json(result);
}
```

> **Security note:** `extract_rule` is NOT in the allowlist — it requires an
> `ANTHROPIC_API_KEY` and must only be called from trusted server-side processes,
> not from browser sessions.

---

## 9. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Only for `extract_rule` | Anthropic API key for NL extraction |

No other environment variables are required. The rule registry is bundled with the
package as YAML files and requires no database or network connection.

---

## 10. Available Tools (Summary)

| Tool | Auth required | Description |
|------|--------------|-------------|
| `list_rules` | None | List all available rule IDs, versions, jurisdictions |
| `get_rule` | None | Fetch DSL, AST, checksum for a specific rule |
| `execute_rule` | None | Evaluate a rule with given inputs |
| `tax.get_rule_snapshot` | None | Full rule set for a tax year + jurisdiction |
| `compile_dsl` | None | Compile DSL text → AST + checksum |
| `validate_rule` | None | Run 6-stage validation pipeline |
| `explain_rule` | None | Human-readable rule explanation + citations |
| `trace_execution` | None | Step-by-step evaluation audit trace |
| `extract_rule` | `ANTHROPIC_API_KEY` | LLM-assisted HMRC prose → draft DSL (requires human review) |

---

## 11. Worked Example — Full Projection Call Sequence

For a Scottish taxpayer with £35,000 income and £110,000 adjusted net income:

```
1. tax.get_rule_snapshot("2025-26", "scotland")
   → 6 rules, each with AST + checksum

2. execute_rule("income_tax_bands", { taxable_income: 35000 })
   → output: 4532.82

3. execute_rule("pa_taper", { adjusted_net_income: 110000 })
   → output: 7570  (PA reduced from £12,570 to £7,570)

4. execute_rule("savings_allowance_basic", {})
   → output: 1000

5. execute_rule("dividend_allowance", {})
   → output: 500
```

Total income tax: **£4,532.82** (Scotland intermediate-rate taxpayer at £35k)  
Remaining personal allowance: **£7,570** (tapered from £12,570)

---

## 12. Roadmap / What's Not Yet Implemented

| Feature | Status | Notes |
|---------|--------|-------|
| Multi-year rUK rules | ✅ | Published registry spans `2025-26` through `2030-31` |
| Scotland rules | ✅ | Published Scottish rule set live; composite `income_tax_due` still outstanding |
| Wales devolved rates | ⏳ | Wales uses rUK rates for 2025-26 |
| NI income tax | ⏳ | NI uses rUK rates; no devolved income tax |
| MPAA rules | ✅ | Money Purchase Annual Allowance rules published |
| Protected pension age | ⏳ | Pre-2006 protected pension age 50 |
| Tapered annual allowance | ✅ | High earner pension AA taper rules published |
| Savings starter rate band | ⏳ | 0% on first £5,000 savings for low earners |

---

## 13. References

- [HMRC — Income Tax rates and Personal Allowances](https://www.gov.uk/income-tax-rates)
- [HMRC — Scottish Income Tax](https://www.gov.uk/scottish-income-tax)
- [Scottish Government — Income Tax 2025-26](https://www.gov.scot/publications/scottish-income-tax-2025-to-2026/)
- [HMRC — Tax on dividends](https://www.gov.uk/tax-on-dividends)
- [HMRC — Capital Gains Tax rates](https://www.gov.uk/capital-gains-tax/rates)
- [Model Context Protocol specification](https://modelcontextprotocol.io/docs)
- [`uk-tax-mcp` README](../../README.md)
- [`later-life-planner` design doc](../../../later-life-planner/docs/withdrawal-optimizer-mcp-design.md)
