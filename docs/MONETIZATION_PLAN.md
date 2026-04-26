# Monetization & Hosting Plan — uk-tax-mcp

## Problem

Host the uk-tax-mcp project as a reliable, secure, and scalable online MCP (micro-calculation platform) service and create revenue streams.

## High-level approach

1. Prepare the codebase for production: tests, packaging, containerization, configuration
2. Build CI/CD and reproducible images
3. Deploy to managed hosting with autoscaling and persistent storage for rules/registry
4. Implement auth, billing, monitoring, legal/compliance
5. Launch, market, and iterate on monetization

## Phases & key tasks

### Phase A — Codebase readiness
- Run and fix tests; add integration tests for rule execution and upload
- Add configuration for production (logging, metrics, environment variables)
- Add Dockerfile and a small example compose for local testing

### Phase B — Packaging & CI/CD
- Build GitHub Actions to run tests, build docker image, run linting
- Publish images to a registry (GitHub Container Registry, Docker Hub, or private ECR)

### Phase C — Hosting & infra (options)
- **Simple/fast:** Render, Railway, or Fly.io for app + Postgres
- **Scalable:** AWS ECS/Fargate or Azure Container Apps + managed Postgres
- **Serverless:** Cloud Run (GCP) or Azure Functions for API + Cloud SQL for DB

Choose based on budget and expected load.

### Phase D — Persistence & state
- Use managed Postgres for registry/metadata
- Use object storage (S3/Blob) for uploaded rule artifacts if needed

### Phase E — Security, auth & compliance
- Add OAuth2/Clerk/KeyCloak or API keys for service access
- Rate-limiting, input sanitization, and proper sandboxing of rule execution
- Data protection/privacy (GDPR) and terms of service

### Phase F — Observability & reliability
- Structured logging (JSON), Prometheus metrics or hosted metrics, and Sentry for errors
- Health checks, readiness/liveness endpoints, and automated backups

### Phase G — Monetization strategies
- **SaaS subscription tiers:** Free tier with limits; Pro with higher quotas + SLA
- **Paid API keys / metered billing:** Requests or compute time per rule execution
- **Enterprise licensing:** Per-instance deployments (on-prem or VPC peering)
- **Marketplace:** Curated paid rules or premium rule sets
- **Consulting & priority support**

### Phase H — Go-to-market
- Pricing model, landing page, docs, quickstart, API playground
- Beta program with early customers, gather feedback, iterate

## Operational checklist for launch
- ✓ CI green, Docker images published
- ✓ DB provisioned and migrations applied
- ✓ TLS + domain + DNS ready
- ✓ Billing integration (Stripe) + invoicing
- ✓ Monitoring dashboards and alerting (PagerDuty/opsgenie)

## Immediate next actions (first 2 weeks)

1. **Add Dockerfile and GitHub Actions** (tests→build→publish)
   - Create Dockerfile for production image
   - Add GitHub Actions workflow to run tests, lint, build, and publish to registry

2. **Create production configuration and secrets plan**
   - Define environment variables for logging, metrics, database
   - Document secrets management strategy

3. **Choose hosting option and spin a staging environment**
   - Evaluate Render, Railway, Cloud Run, Azure Container Apps, AWS ECS
   - Create staging deployment for testing

4. **Integrate billing (Stripe sandbox) and API key model**
   - Create Stripe account and configure test mode
   - Design pricing tiers (Free, Starter, Pro, Enterprise)
   - Implement API key generation and subscription management

5. **Add basic usage-based rate limiting and quotas**
   - Implement per-key quotas and metering
   - Add rate-limiting middleware

## Notes & considerations

- **Rule execution safety:** Prioritize safe execution of user-provided rules; consider running rule execution in isolated containers or using time/cpu limits
- **Metered billing design:** Monetization choice affects design; metered billing needs reliable metering and observability
- **Legal/compliance:** UK tax rules may have licensing/accuracy considerations — include disclaimers and terms of service
- **Stripe integration:** Use Stripe Billing for subscriptions + metered usage; support LLP Pro (company) billing with VAT handling

## Monetization details

### Pricing strategy example
- **Free tier:** 100 rule executions/month
- **Starter:** £29/month — 5k executions/month
- **Pro:** £199/month — 100k executions/month + SLA
- **Enterprise:** Custom pricing & on-prem deployment
- **Metered overlay:** £X per 1k extra executions

### Stripe integration notes
- Use Stripe Billing + Checkout for subscriptions
- Use Stripe Tax for VAT auto-calculation (UK/EU)
- Support both individuals and LLPs (company billing)
- Implement usage metering via `UsageRecord` API
- Handle dunning (payment failures) gracefully
- Mirror invoices in local DB for audit trail

## Deliverables

- [ ] This plan (MONETIZATION_PLAN.md)
- [ ] Tracked todos in repo issue tracker or wiki for team progress
- [ ] Dockerfile and GitHub Actions CI/CD pipeline
- [ ] Production configuration templates
- [ ] Stripe billing integration code
- [ ] Hosting infrastructure setup (staging & production)
- [ ] Monitoring and alerting dashboards
- [ ] Landing page and pricing documentation
