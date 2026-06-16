# BPEL2Orkes — Product Backlog

**Studio:** Kshetra Studio · [ktools.kshetra.studio](https://ktools.kshetra.studio)  
**Updated:** 2026-06-16

Items are grouped by pipeline stage and ordered by priority within each group.
Status: 🟢 Done · 🔵 In Progress · 🔲 Planned · ⚠️ Blocked

---

## 🌐 Domain & Vertical Strategy

**Decided:** `ktools.kshetra.studio/bpel2orkes` is the canonical product URL.
`askmybank.ai` is a banking vertical landing page that points there.

**Why:** BPEL is not bank-specific. Any organisation that ran IBM WPS/BAW/IIB is a
target customer. Anchoring the product under a bank-only domain caps the market.

| Domain | Purpose | Audience |
|---|---|---|
| `bpel2orkes.kshetra.studio` | Product — API, MCP, Web UI | All sectors |
| `staging.bpel2orkes.kshetra.studio` | Staging — isolated, not public | Internal / Orkes team demos |
| `askmybank.ai/bpel2orkes` | Banking vertical landing page | Banks (APAC focus) |
| *(future)* `ktools.kshetra.studio` | Tools directory listing bpel2orkes + future tools | Broad |
| *(future)* askmyinsurer.ai | Insurance vertical | Insurers running IBM BPM |

**Decided against** `ktools.kshetra.studio/bpel2orkes` — ktools doesn't exist as a platform
yet and building bpel2orkes under that path creates a blocker. Each tool gets its own subdomain;
ktools becomes a directory when there are 3+ tools ready.

**Target sectors beyond banking:**

| Sector | IBM product typically used | Example BPEL processes |
|---|---|---|
| Insurance | WPS, BAW | Claims processing, policy underwriting, reinsurance |
| Telco | WPS, IIB/ACE | Order management, number porting, service provisioning |
| Government | WPS, BPM | Benefits processing, permit workflows, citizen services |
| Healthcare | WPS, BAW | Patient pathway, prior authorisation, claims adjudication |
| Utilities | WPS | Smart meter events, billing, outage management |
| Retail / Supply Chain | WPS, IIB | Order fulfilment, supplier onboarding |

---

## 🏦 Business Model

Three delivery tiers — customers choose based on their risk appetite and budget:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  TIER 1 — Public SaaS (ktools.kshetra.studio/bpel2orkes)               │
│  • Hosted by Kshetra Studio                                             │
│  • Customer accepts T&C (BPEL data consent + usage rights)             │
│  • Freemium: first 5 conversions free, then subscription / pay-per-use │
│  • Strategic: consenting customers' BPEL builds our pattern library     │
├─────────────────────────────────────────────────────────────────────────┤
│  TIER 2 — Self-Hosted (customer VPC / on-premises)                     │
│  • Docker image or Helm chart deployed by customer                     │
│  • Flat licence fee (annual)                                           │
│  • BPEL never leaves customer network — zero data sharing              │
│  • Includes support SLA                                                │
├─────────────────────────────────────────────────────────────────────────┤
│  TIER 3 — Managed Migration Engagement (Kshetra Studio professional    │
│           services)                                                     │
│  • We run the migration end-to-end on customer premises                │
│  • Code generator + validator + worker stubs + go-live support         │
│  • Per-process or programme fee                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

> **Strategic moat:** Consenting Tier 1 customers build a proprietary BPEL pattern
> corpus. Over time this trains better auto-mapping, catches IBM-specific edge cases,
> and becomes a dataset nobody else has. Opt-in only, explicitly stated in T&C.

---

## Stage 1 — Parser

| # | Item | Status | Notes |
|---|------|--------|-------|
| P-1 | Parse WS-BPEL 2.0 core constructs | 🟢 Done | All activity types covered |
| P-2 | Parse IBM BPELX extensions (`bpelx:task`, `bpelx:callBusinessRule`) | 🟢 Done | Captured verbatim with rawXml |
| P-3 | Parse fault handlers, compensation handlers, event handlers | 🟢 Done | |
| P-4 | Parse partner links, variables, correlation sets | 🟢 Done | |
| P-5 | Handle BPEL 1.1 legacy syntax (`<switch>`, `<otherwise>`) | 🟢 Done | |
| P-6 | Support multi-process BPEL files (multiple `<process>` in one file) | 🔲 Planned | Rare but exists in some IBM exports |
| P-7 | Parse WSDL partner link type definitions | 🔲 Planned | Needed for Stage 4 validator |
| P-8 | Parse XSD type imports | 🔲 Planned | Needed for variable type resolution |
| P-9 | BPEL 1.1 full support (`<onMessage>` vs `<onEvent>` differences) | 🔲 Planned | |

---

## Stage 2 — Pattern Mapper

| # | Item | Status | Notes |
|---|------|--------|-------|
| M-1 | Map all core activity types → Conductor task types | 🟢 Done | 14 types covered |
| M-2 | Map IBM `bpelx:task HUMAN_TASK` → `HUMAN` | 🟢 Done | |
| M-3 | Map `bpelx:callBusinessRule` → `HTTP` (ODM REST) | 🟢 Done | |
| M-4 | Map `<scope>` → `SUB_WORKFLOW` with compensation extraction | 🟢 Done | |
| M-5 | Map `<faultHandlers>` → fault-router workflow with `SWITCH` | 🟢 Done | |
| M-6 | Map `<compensationHandler>` → compensation sub-workflow | 🟢 Done | |
| M-7 | Map `<flow>` → `FORK_JOIN` + `JOIN` | 🟢 Done | |
| M-8 | Map `<pick>` → `WAIT` + timeout | 🟢 Done | |
| M-9 | XPath → JavaScript condition translator | 🔲 Planned | High priority — currently emitted as TODO comments |
| M-10 | XPath → JSONPath `<assign>` from-expression translator | 🔲 Planned | |
| M-11 | Selective compensation (`<compensate name="...">`) wiring | 🔲 Planned | Manual today — need automatic ordering |
| M-12 | Correlation set → Conductor correlation task mapping | 🔲 V1.1 | Pass workflow ID as correlation key through all async calls |
| M-13 | `<eventHandlers>` → parallel signal-listener sub-workflow | 🔲 Planned | |
| M-14 | Partner link → worker endpoint registry lookup | 🔲 Planned | Match partner links to known service URLs |
| M-15 | Warning: `<receive>`/`<pick>` push→pull inversion | 🔲 V1.1 | BPEL waits for ESB push; Conductor WAIT needs external POST to `/tasks/{taskId}/ack` — emit explicit warning with instruction |
| M-16 | Warning: SOAP `<invoke>` needs worker wrapping | 🔲 V1.1 | Detect SOAP partnerLink bindings and warn that HTTP task assumes REST; SOAP envelope + WS-Security requires a dedicated worker |
| M-17 | Warning: correlation set not mapped | 🔲 V1.1 | Emit warning per correlation set listing the variables involved and pointing to Conductor correlation docs |

---

## Stage 3 — Code Generator

| # | Item | Status | Notes |
|---|------|--------|-------|
| G-1 | Emit clean Conductor workflow JSON (strip `_bpelSource`, `_warning`, `_metadata`) | 🟢 Done | `src/code_generator.py` |
| G-2 | Clean bundle exposed via `POST /api/v1/convert` and `POST /api/v1/convert/clean` | 🟢 Done | `src/api.py` |
| G-3 | Simple XPath literal translation in SWITCH conditions | 🟢 Done | `_translate_condition()` in code_generator |
| G-4 | Generate Conductor task definition stubs | 🔲 V1.1 | One task def per unique `<invoke>` operation |
| G-5 | Generate Python worker skeleton (one file per partner link) | 🔲 V1.1 | |
| G-6 | Generate Java worker skeleton (Spring Boot) | 🔲 V2 | Target audience is Java shops |
| G-7 | Generate `docker-compose.yml` for local Orkes + worker testing | 🔲 V2 | |
| G-8 | Variable reference translation (`$var.part` → `${workflow.variables.var}`) | 🔲 V1.1 | |

---

## Stage 4 — Validator

| # | Item | Status | Notes |
|---|------|--------|-------|
| V-1 | POST generated workflow JSON to Orkes `/api/metadata/workflow` | 🟢 Done | `POST /api/v1/validate` — uses Orkes Developer (developer.orkescloud.com) |
| V-2 | Map Orkes validation errors back to BPEL source line numbers | 🔲 V1.1 | |
| V-3 | Report missing task definitions | 🔲 V1.1 | |
| V-4 | Check sub-workflow references are resolvable | 🔲 V1.1 | |
| V-5 | Validate compensation workflow invocation order | 🔲 V2 | |

---

## CLI / UX

| # | Item | Status | Notes |
|---|------|--------|-------|
| U-1 | `bpel2orkes convert <file.bpel>` end-to-end CLI command | 🔲 Planned | |
| U-2 | HTML migration report (warnings, manual items, coverage %) | 🔲 Planned | |
| U-3 | Batch conversion (`bpel2orkes convert-all <directory>`) | 🔲 Planned | Banks have hundreds of processes |
| U-4 | `--dry-run` flag (parse + map but don't write files) | 🔲 Planned | |

---

## Test coverage

| # | Item | Status | Notes |
|---|------|--------|-------|
| T-1 | Parser tests (35 tests) | 🟢 Done | Against loan_approval sample |
| T-2 | Mapper tests (23 tests) | 🟢 Done | Against all three bank samples |
| T-3 | Round-trip test (BPEL → JSON → Orkes import) | 🔲 Planned | Needs local Orkes Docker instance |
| T-4 | IBM WPS 6.x BPELX namespace tests | 🔲 Planned | |
| T-5 | IBM WPS 7.x BPELX namespace tests | 🔲 Planned | |
| T-6 | Stress test with real exported IBM BAW process (anonymised) | 🔲 Planned | Source when available from bank partner |

---

## Tier 1 — Public SaaS (askmybank.ai/bpel2orkes)

Hosted service. Customers upload BPEL, get Conductor JSON back.
Freemium hook: first 5 conversions free, no sign-up required.
Consenting customers opt in to BPEL corpus contribution (pattern library moat).

### SaaS — REST API

| # | Item | Status | Notes |
|---|------|--------|-------|
| SA-1 | `POST /api/v1/convert` — accepts BPEL XML body, returns Conductor bundle JSON | 🔲 Planned | FastAPI, stateless, in-memory only |
| SA-2 | `POST /api/v1/parse` — parse only, returns AST JSON | 🔲 Planned | Diagnostic / agent use |
| SA-3 | `POST /api/v1/validate` — convert + validate against a customer's Orkes instance | 🔲 Planned | Customer passes `orkesBaseUrl` + API key in header |
| SA-4 | `GET /api/v1/health`, `GET /api/v1/version` | 🔲 Planned | |
| SA-5 | Freemium quota: 5 free conversions per IP / session, then API key required | 🔲 Planned | Low friction for first-time users |
| SA-6 | API key management (sign-up, rotate, revoke) | 🔲 Planned | Simple self-service; no OAuth complexity. Key rotation detail → AU-16 |
| SA-7 | Usage dashboard — conversions used, warnings count, plan tier | 🔲 Planned | |
| SA-8 | Pricing tiers: Free → Starter (50/mo) → Pro (unlimited) → Enterprise (SLA) | 🔲 Planned | |
| SA-9 | Stripe payment integration | 🔲 Planned | |

### SaaS — MCP Server (public endpoint)

| # | Item | Status | Notes |
|---|------|--------|-------|
| SM-1 | Public MCP server at `mcp.askmybank.ai/bpel2orkes` (SSE transport) | 🔲 Planned | For customers who accept T&C data sharing |
| SM-2 | `convert_bpel` tool — BPEL XML → Conductor bundle | 🔲 Planned | Core tool |
| SM-3 | `parse_bpel` tool — BPEL XML → AST JSON | 🔲 Planned | Agent inspection / pre-flight |
| SM-4 | `list_warnings` tool — returns `_warning` items from last conversion | 🔲 Planned | Surfaces manual review items to agent |
| SM-5 | `get_mapping_reference` tool — returns mapping table for a construct type | 🔲 Planned | In-context reference during agent-led migration |
| SM-6 | `list_samples` + `convert_sample` tools — demo without upload | 🔲 Planned | Safe entry point for reluctant customers |
| SM-7 | MCP API key auth — same key as REST API | 🔲 Planned | One credential for both surfaces |
| SM-8 | Publish to MCP Registry (Anthropic + community) | 🔲 Planned | Discovery by Claude users doing migration work |
| SM-9 | Rate limiting per API key on MCP tools | 🔲 Planned | Prevent abuse on public endpoint |

### SaaS — Auth, Billing & Quotas (V1.1 — build on staging first)

**Identity model:** All surfaces (Web UI, REST API, MCP) share one identity — a Google/GitHub OAuth account.
API keys are issued from the dashboard after sign-in; no separate sign-up flow.

**Key issuance flow (API & MCP users):**
1. Visit `bpel2orkes.kshetra.studio` → "Sign in with Google/GitHub"
2. Free API key auto-issued (`bpel2_free_xxxx`) with 3 lifetime conversions
3. Copy key from dashboard → use as `X-Api-Key` header or MCP config header
4. "Upgrade" button → Stripe Checkout → key upgraded to paid tier

**MCP with API key:**
```bash
claude mcp add --transport http --header "X-Api-Key: bpel2_free_xxxx" bpel2orkes https://bpel2orkes.kshetra.studio/mcp/
```

| # | Item | Status | Notes |
|---|------|--------|-------|
| AU-1 | Google OAuth sign-in (`/auth/google`, `/auth/google/callback`) | 🔲 V1.1 | Authlib + FastAPI |
| AU-2 | GitHub OAuth sign-in (`/auth/github`, `/auth/github/callback`) | 🔲 V1.1 | Same flow |
| AU-3 | User record in DynamoDB (`userId`, `email`, `provider`, `apiKey`, `tier`, `usageCount`) | 🔲 V1.1 | No RDS needed — single-table design |
| AU-4 | Auto-issue free API key on first sign-in (`bpel2_free_` + random 16 chars) | 🔲 V1.1 | Stored hashed in DynamoDB |
| AU-5 | `X-Api-Key` middleware — validate key, load user, check quota before convert endpoints | 🔲 V1.1 | 401 if missing/invalid, 429 if over quota |
| AU-6 | Free tier: 3 lifetime conversions, no card required | 🔲 V1.1 | Counter increments on every `/convert` call |
| AU-7 | Developer tier ($10 one-off): 30 credits, issued via Stripe Checkout | 🔲 V1.1 | Stripe webhook → update tier + credits in DynamoDB |
| AU-8 | Starter tier ($49/mo): unlimited, contact form for now | 🔲 V1.1 | Manual fulfilment until volume justifies automation |
| AU-9 | Dashboard page (`/dashboard`) — shows tier, credits used, API key, copy button | 🔲 V1.1 | Simple HTML page, no React needed |
| AU-10 | "Upgrade" button in dashboard → Stripe Checkout → webhook → tier upgrade | 🔲 V1.1 | |
| AU-11 | Web UI conversion gate — show "Sign in" prompt when no session, show usage count when signed in | 🔲 V1.1 | |
| AU-12 | Session cookie (httponly, secure, 7-day expiry) | 🔲 V1.1 | JWT signed with server secret |
| AU-13 | MCP `X-Api-Key` header threading — quota applied same as REST API | 🔲 V1.1 | Same middleware |
| AU-14 | Stripe webhook endpoint (`POST /webhooks/stripe`) | 🔲 V1.1 | Verify signature, update DynamoDB on `checkout.session.completed` |
| AU-15 | `/api/v1/me` endpoint — returns current user tier, usage, API key (masked) | 🔲 V1.1 | Used by dashboard and MCP tool |
| AU-16 | **API key rotation** — "Rotate Key" button in dashboard issues a new `bpel2_*` key and immediately invalidates the old one; one-click update shown for MCP config snippet | 🔲 Planned | Needed for: accidental key exposure, key sharing revocation, periodic security hygiene. New key inherits same tier/credits. Old key rejected with 401 + clear "key was rotated" message so users know to update their config rather than thinking auth is broken. |

### SaaS — Web UI (Kickstarter / Try It Now)

| # | Item | Status | Notes |
|---|------|--------|-------|
| SW-1 | One-page converter UI served from the API container at `/` | 🟢 Done | `src/static/index.html` — same-container, no extra infra |
| SW-2 | Sample selector — convert included bank samples without upload | 🟢 Done | `/samples/*.bpel` served as static files |
| SW-3 | Side-by-side view: BPEL source ↔ Conductor JSON output | 🟢 Done | Tab switcher: Main Workflow / Sub-Workflows / Full Bundle |
| SW-4 | Warnings panel — list of manual review items | 🟢 Done | Amber panel appears when warnings > 0 |
| SW-5 | "Register on Orkes" button — one-click POST to Orkes Developer | 🟢 Done | Uses `/api/v1/validate` with customer API key |
| SW-6 | T&C gate on file upload — explicit consent checkbox before BPEL is submitted | 🔲 V1.1 | Legal requirement for public SaaS launch |
| SW-7 | Move Web UI to S3 + CloudFront (separate from API) | 🔲 V2 | Better caching, CDN, custom error pages. Not needed until traffic justifies it |

### SaaS — Orkes Demo Platform Integration

| # | Item | Status | Notes |
|---|------|--------|-------|
| OD-1 | Deploy converted sample workflows on Orkes public demo Conductor instance | 🔲 Planned | Show the workflow *running*, not just JSON — money shot for Orkes sales |
| OD-2 | Coordinate with Orkes team — joint landing page or demo environment | 🔲 Planned | Mutual benefit: Orkes gets a migration story, we get distribution |
| OD-3 | "Convert → Run on Orkes Demo" flow in web UI | 🔲 Planned | End-to-end demo in under 5 minutes |

---

## Tier 2 — Self-Hosted (Customer VPC / On-Premises)

For customers who will not share BPEL externally.
BPEL never leaves their network. Flat annual licence fee.

| # | Item | Status | Notes |
|---|------|--------|-------|
| SH-1 | Docker image — `ghcr.io/kshetra-studio/bpel2orkes:latest` | 🔲 Planned | Same API as SaaS; licence key activates it |
| SH-2 | Helm chart for Kubernetes / OpenShift deployment | 🔲 Planned | Banks run OpenShift; must support it |
| SH-3 | Local MCP server (stdio transport) — BPEL stays on developer's machine | 🔲 Planned | Default mode; no network calls |
| SH-4 | Licence key validation (offline-capable — annual key, no phone-home) | 🔲 Planned | Air-gapped environments must work |
| SH-5 | Air-gap deployment guide (no outbound internet required) | 🔲 Planned | Many bank prod environments are air-gapped |
| SH-6 | Configuration guide for self-hosted Orkes Conductor integration | 🔲 Planned | |
| SH-7 | SBOM (Software Bill of Materials) published per release | 🔲 Planned | Required for bank procurement; deps are lxml + pytest only |

---

## T&C, Legal, and Data Governance

Two distinct T&C positions — one per tier. Getting this right gates enterprise sales.

| # | Item | Status | Notes |
|---|------|--------|-------|
| LG-1 | **SaaS T&C** — explicit: BPEL is process configuration, not customer data | 🔲 Planned | This framing is the unlock for bank legal teams |
| LG-2 | SaaS T&C — data retention policy: BPEL input deleted within 60 seconds of processing | 🔲 Planned | Short retention enables corpus opt-in at lower risk |
| LG-3 | SaaS T&C — **opt-in corpus clause**: customer explicitly consents to anonymised BPEL being used to improve the tool | 🔲 Planned | Opt-in only; clear benefit statement ("helps us handle your specific IBM extensions better") |
| LG-4 | SaaS T&C — no PII clause: customer warrants BPEL contains no customer PII | 🔲 Planned | BPEL process config should not contain PII by design |
| LG-5 | **Self-hosted EULA** — licence terms, no data sharing clause, support SLA | 🔲 Planned | |
| LG-6 | Pricing page with clear tier comparison | 🔲 Planned | |
| LG-7 | Security whitepaper (1-pager) — in-memory processing, no persistence, no logging of content | 🔲 Planned | CISOs need this before approving SaaS use |
| LG-8 | Penetration test checklist for self-hosted API | 🔲 Planned | |

---

## Security — Both Tiers

| # | Item | Status | Notes |
|---|------|--------|-------|
| SEC-1 | BPEL input **never written to disk or logs** — in-memory processing only | 🔲 Planned | Auditable; must be verifiable from open source code |
| SEC-2 | Request size limit (e.g. 5MB) + XML content-type validation | 🔲 Planned | Reject non-BPEL payloads early |
| SEC-3 | XXE (XML External Entity) protection in parser | 🔲 Planned | lxml safe by default; must be documented |
| SEC-4 | Rate limiting per API key and per IP | 🔲 Planned | |
| SEC-5 | No telemetry on BPEL content; anonymous usage metrics only (opt-in) | 🔲 Planned | |
| SEC-6 | MCP local-only stdio mode as the default and recommended mode | 🔲 Planned | Prevents accidental cloud routing |

---

## Infrastructure & Deployment

`kshetra.studio` and `askmybank.ai` are both live. All deployments to these domains
must flow through staging first. See [architecture.md](architecture.md) for the full
environment model.

### Environments

| # | Item | Status | Notes |
|---|------|--------|-------|
| INF-1 | `staging.bpel2orkes.kshetra.studio` DNS CNAME configured | 🟢 Done | Now points to API Gateway custom domain (was ALB) |
| INF-2 | `bpel2orkes.kshetra.studio` DNS CNAME configured (production) | 🟢 Done | Now points to API Gateway custom domain (was ALB) |
| INF-3 | robots.txt + X-Robots-Tag on all staging URLs (no public indexing) | 🔲 Planned | |
| INF-4 | Environment variable config per environment (local / staging / prod) | 🟢 Done | `.env` / `.env.staging` / `.env.production` + `scripts/push-secrets.sh` |
| INF-5 | Separate Orkes Conductor instances for staging vs production | 🔲 Planned | Staging workflows must never touch prod |
| INF-6 | **Serverless migration: ECS Fargate + ALB → Lambda + API Gateway (REST v1) + WAFv2** | 🟢 Done | Idle cost dropped from ~$50-70/mo (always-on Fargate + ALB) to near-$0 (request-billed). `infra/app.py` `Bpel2OrkesServerless` stack. WAFv2 rate-based rule (300 req/5min/IP) is the cost circuit breaker on abuse/DDoS — confirmed empirically that WAF only supports REST API v1, not HTTP API v2, association. Mangum adapts FastAPI to Lambda; `stateless_http=True` on the MCP mount required since Lambda has no session affinity across invocations. Old ECS/Fargate/ALB stacks destroyed; existing ACM certs + DNS CNAMEs reused so OAuth redirect URIs never changed. |
| INF-7 | AWS WAF managed rule `CrossSiteScripting_BODY` overridden to Count (not Block) on `/api/v1/convert*` | 🟢 Done | False-positives on legitimate BPEL/XML request bodies — caught during staging validation, would have silently broken the core conversion feature for all users behind the WAF |
| INF-8 | AWS Budget alert ($20/mo, email) | 🟢 Done | Already existed account-wide — 85%/100% actual + 100% forecasted thresholds to dinesh.s.panwar@gmail.com |

### CI/CD Pipeline

| # | Item | Status | Notes |
|---|------|--------|-------|
| CI-1 | GitHub Actions: run tests on every PR (`pytest tests/ -v`) | 🔲 Planned | Block merge if tests fail |
| CI-2 | GitHub Actions: auto-deploy to staging on merge to `main` | 🔲 Planned | |
| CI-3 | Smoke test suite for staging post-deploy (convert each sample file, assert 200 + non-empty bundle) | 🔲 Planned | Catches regressions before production gate |
| CI-4 | GitHub Actions: production deploy behind manual approval gate | 🔲 Planned | Repo owner approval required — prevents accidental prod push |
| CI-5 | Docker image tagged with git SHA + semver (`bpel2orkes:1.0.0`, `bpel2orkes:sha-abc123`) | 🔲 Planned | Enables precise rollback |
| CI-6 | Rollback runbook — re-run last successful production deploy job | 🔲 Planned | Target: < 2 min to rollback |
| CI-7 | Dependabot for `lxml` and other dependencies | 🔲 Planned | |

### Monitoring

| # | Item | Status | Notes |
|---|------|--------|-------|
| MON-1 | Sentry error tracking (separate DSN per environment) | 🔲 Planned | Errors in staging don't pollute prod dashboard |
| MON-2 | Uptime monitor on `/api/v1/health` (staging + prod separately) | 🔲 Planned | |
| MON-3 | Anonymous usage metrics: request count, conversion duration, warning count | 🔲 Planned | No BPEL content in metrics — only aggregate counts |

---

## Samples / test fixtures

| # | Item | Status | Notes |
|---|------|--------|-------|
| S-1 | `loan_approval.bpel` | 🟢 Done | Standard WS-BPEL 2.0 reference |
| S-2 | `income_verification.bpel` | 🟢 Done | Parallel bureaus, ODM, human task |
| S-3 | `communications_orchestration.bpel` | 🟢 Done | Channel fallback, async pick, suppression |
| S-4 | `credit_card_provisioning.bpel` | 🟢 Done | Multi-scope compensation, fraud ops, wallets |
| S-5 | `payment_initiation.bpel` | 🔲 Planned | NPP/PayTo fast payment flow |
| S-6 | `collections_workflow.bpel` | 🔲 Planned | Hardship + collections + legal escalation |
| S-7 | `kyc_onboarding.bpel` | 🔲 Planned | KYC/AML identity verification |
