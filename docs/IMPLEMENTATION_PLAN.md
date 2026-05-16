# Implementation Plan — uk-tax-mcp

*Derived from `docs/MONETIZATION_PLAN.md`. Status as of 2026-05-16.*

Legend: ✅ Done · ⬜ Not started · 🔄 In progress

---

## Phase A — Codebase Readiness

| # | Task | Status | Notes |
|---|---|---|---|
| A1 | Run and fix tests; add integration tests for rule execution and upload | ✅ | 27 integration tests in `tests/integration/test_mcp_tools.py` |
| A2 | Add production configuration (logging, env vars) | ✅ | `src/hmrc_tax_mcp/config.py` — JSON log formatter, `configure_logging()` |
| A3 | Add Dockerfile and docker-compose for local testing | ✅ | Multi-stage Dockerfile (`builder → runtime → dev`); `docker-compose.yml` |
| A4 | Add `"This is a deterministic calculation, not tax advice"` disclaimer to all MCP tool descriptions and HTTP API response envelopes | ✅ | `_DISCLAIMER` constant + `_tool()` helper in `server.py`; disclaimer field in `http_dev.py` execute_rule and explain_rule responses |
| A5 | Create `RULES_CHANGELOG.md` with defined entry format: Budget date · rule IDs · what changed · HMRC source · reviewer | ✅ | `RULES_CHANGELOG.md` at repo root with baseline entry and entry format documented |
| A6 | Document Budget change process in `CONTRIBUTING.md`: detect announcement → draft update → validate → merge within 24h of HMRC publication | ✅ | `CONTRIBUTING.md` at repo root with 7-step Budget change process |

---

## Phase B — Packaging & CI/CD

| # | Task | Status | Notes |
|---|---|---|---|
| B1 | GitHub Actions CI: lint (ruff), type check (mypy), test (pytest), Docker build | ✅ | `.github/workflows/ci.yml` |
| B2 | Publish images to GitHub Container Registry on merge to `main` and semver tags | ✅ | `.github/workflows/docker-publish.yml`; SBOM + provenance attestations |

---

## Phase C — Hosting & Infrastructure

| # | Task | Status | Notes |
|---|---|---|---|
| C1 | Choose hosting provider and provision staging environment | ⬜ | Options: Render / Railway / Fly.io (simple); AWS ECS / Azure Container Apps (scalable); Cloud Run / Azure Functions (serverless) |
| C2 | Replace `http_dev.py` with production `/v1/` API layer: versioned routes, structured error envelopes, CORS | ✅ | `src/hmrc_tax_mcp/api.py`; routes: `GET /v1/rules`, `GET /v1/rules/{id}`, `POST /v1/rules/{id}/execute`, `/explain`, `/validate`, `GET /v1/snapshots/{year}/{jur}`, `POST /v1/dsl/compile` |
| C3 | Expose `/v1/openapi.json` (FastAPI generates automatically) | ✅ | Set via `openapi_url="/v1/openapi.json"`; Swagger UI at `/v1/docs` |
| C4 | Expand Scotland jurisdiction rules to match rUK breadth for 2026-27 and 2027-28 | ✅ | 2026-27: 9→71 rules; 2027-28: 4→72 rules. Script at `scripts/generate_scotland_rules.py`. Hand-crafted: `income_tax_due`, `is_higher_rate_taxpayer`, `income_tax_bands` for both years |

---

## Phase D — Persistence & State

| # | Task | Status | Notes |
|---|---|---|---|
| D1 | Provision managed Postgres for registry/metadata | ⬜ | |
| D2 | Set up object storage (S3/Blob) for uploaded rule artifacts if needed | ⬜ | |
| D3 | Extend Stage 2 (Semantic) validation to enforce citation content quality: require at least one citation whose `label` matches a known legislative reference pattern (`ITEPA 2003`, `TCGA 1992`, `IHTA 1984`, `ITA 2007`, `IHTM\d+`) | ⬜ | Rejects `"HMRC website"` as sole citation |
| D4 | Add citation URL reachability check as a non-blocking warning in Stage 2: flag (not fail) citations whose GOV.UK URLs return non-200 | ⬜ | Surfaces stale links before they reach production |

---

## Phase E — Security, Auth & Compliance

| # | Task | Status | Notes |
|---|---|---|---|
| E1 | Add OAuth2 / API key authentication for service access | ⬜ | Clerk, KeyCloak, or custom API key table |
| E2 | Rate-limiting and input sanitisation | ⬜ | |
| E3 | Sandboxing of rule execution (time/CPU limits) | ⬜ | |
| E4 | GDPR / Privacy Policy — stateless processing documented | ⬜ | |
| E5 | Terms of Service: (a) outputs are calculations not advice (FSMA 2000 / CIOT/ATT); (b) liability cap at 12 months fees paid; (c) mutual indemnification; (d) acceptable use policy prohibiting sole reliance without professional review | ⬜ | Hard legal prerequisite before commercial launch |
| E6 | Apply for HMRC MTD software recognition: pass HMRC technical API compliance tests against `test-api.service.hmrc.gov.uk` | ⬜ | Budget 6–12 weeks lead time; start early |
| E7 | Evaluate and submit FCA Supercharged Sandbox application (Nvidia partnership, cohorts from October 2025) | ⬜ | Regulatory credibility; low additional effort alongside E6 |
| E8 | Add audit-grade metadata to `trace_execution` responses: ISO 8601 timestamp, `rule_id@version`, SHA-256 hash of inputs, rounding mode | ⬜ | Makes trace output suitable for client file storage by regulated firms |

---

## Phase F — Observability & Reliability

| # | Task | Status | Notes |
|---|---|---|---|
| F1 | Prometheus metrics or hosted metrics (Datadog, Grafana Cloud) | ⬜ | |
| F2 | Sentry for error tracking | ⬜ | |
| F3 | Health checks, readiness/liveness endpoints | ⬜ | `/health` exists in `http_dev.py`; needs to move to production API |
| F4 | Automated backups for DB and rule registry | ⬜ | |
| F5 | Instrument per-rule execution metrics: `rule_id`, `jurisdiction`, `tax_year`, latency, error rate | ⬜ | Feeds both SLA monitoring and usage-based billing |

---

## Phase G — Monetization

| # | Task | Status | Notes |
|---|---|---|---|
| G1 | SaaS subscription tiers: Free (100 calls/mo), Starter (£29/mo, 5k calls), Pro (£199/mo, 100k calls + SLA), Enterprise (custom) | ⬜ | |
| G2 | Stripe Billing integration: subscriptions, Checkout, VAT via Stripe Tax, usage metering via `UsageRecord` API | ⬜ | |
| G3 | Paid API keys / metered billing: key generation, quota enforcement, dunning | ⬜ | |
| G4 | Enterprise licensing: per-instance on-prem or VPC-peering deployment model | ⬜ | |
| G5 | Rule marketplace: curated paid rule sets (specialist pension decumulation, business property relief, etc.) | ⬜ | |
| G6 | OEM / white-label licensing tier for practice software vendors (IRIS, TaxCalc, Xero): £50k–£200k+/year | ⬜ | Most credible path to significant revenue and the exit thesis |

---

## Phase H — Go-to-Market

| # | Task | Status | Notes |
|---|---|---|---|
| H1 | Pricing model, landing page, and quickstart docs | ⬜ | |
| H2 | API playground / interactive docs | ⬜ | |
| H3 | Beta programme with early customers; feedback loop | ⬜ | |
| H4 | Publish Python SDK (`pip install uk-tax-mcp-client`) — thin wrapper over `/v1/` REST API | ⬜ | Removes integration friction for fintech developers |
| H5 | Publish TypeScript/Node SDK (`npm install uk-tax-mcp`) | ⬜ | Most fintech front-ends are Node-based |
| H6 | List MCP server in public registries: MCP.so, PulseMCP, Anthropic partner directory, relevant AI tooling directories | ⬜ | |
| H7 | Approach IRIS Elements, TaxCalc, and Xero partner programmes with integration proposal | ⬜ | Each integration is a distribution channel worth thousands of potential customers |
| H8 | Publish worked-example content targeting ICAEW/ACCA audiences: "How a deterministic tax engine reduces PI exposure" | ⬜ | Drives inbound from primary buyer persona |

---

## Phase I — Rule Operations (new)

| # | Task | Status | Notes |
|---|---|---|---|
| I1 | Budget monitoring script: subscribe to GOV.UK publications RSS and HMRC email updates; auto-open GitHub issue listing affected rule IDs on Finance Act / Budget / HMRC policy update detection | ⬜ | |
| I2 | Rule-impact analysis tool: given changed thresholds or rates, identify which YAML rules contain those values and flag for review | ⬜ | Reduces risk of stale rule surviving a Budget undetected |
| I3 | Evaluate MTD ITSA calculation back-end partnership: identify an HMRC-recognised MTD front-end vendor lacking a strong calculation engine; propose white-label API arrangement | ⬜ | Avoids building MTD submission layer; accesses ready-made distribution |
| I4 | Quarterly rule accuracy audit: re-verify numeric values against live HMRC manual and legislation; document in `RULES_CHANGELOG.md` | ⬜ | Supports PI insurance claims and enterprise sales due diligence |

---

## Operational Launch Checklist

### Infrastructure
| Item | Status |
|---|---|
| CI green, Docker images published to GHCR | ✅ |
| DB provisioned and migrations applied | ⬜ |
| TLS + domain + DNS ready | ⬜ |
| Production `/v1/` API deployed | ⬜ |
| Monitoring dashboards and alerting | ⬜ |

### Commercial & Legal
| Item | Status |
|---|---|
| Billing integration (Stripe) + invoicing | ⬜ |
| Terms of Service published (liability cap + "not tax advice") | ⬜ |
| Privacy Policy published (GDPR-compliant; stateless processing documented) | ⬜ |
| API key system live and tied to Stripe subscription | ⬜ |

### Product & Compliance
| Item | Status |
|---|---|
| "Not tax advice" disclaimer in all server responses | ⬜ |
| `RULES_CHANGELOG.md` created and Budget change process documented | ⬜ |
| Scotland rule coverage expanded for 2026-27 and 2027-28 | ⬜ |
| HMRC MTD software recognition application submitted | ⬜ |
| FCA Supercharged Sandbox application evaluated | ⬜ |

---

## Immediate Next Actions (ordered by urgency)

| Priority | Action | Phase | Effort |
|---|---|---|---|
| 1 | Add "not tax advice" disclaimer to all MCP tool descriptions and HTTP responses | A4 | 1–2 hours |
| 2 | Create `RULES_CHANGELOG.md` and Budget change process section in `CONTRIBUTING.md` | A5, A6 | 2–3 hours |
| 3 | Choose hosting and provision staging environment | C1 | 1–2 days |
| 4 | Expand Scotland rules for 2026-27 (MTD ITSA Phase 1 tax year) | C4 | 3–5 days |
| 5 | Replace `http_dev.py` with production `/v1/` API + OpenAPI spec | C2, C3 | 3–4 days |
| 6 | Integrate Stripe (sandbox) and API key model | G2, G3 | 3–5 days |
| 7 | Submit HMRC MTD software recognition application | E6 | 1–2 weeks (external lead time 6–12 weeks) |
| 8 | Draft and publish Terms of Service with liability cap | E5 | 1–2 days |
