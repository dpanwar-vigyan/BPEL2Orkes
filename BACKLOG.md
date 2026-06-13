# BPEL2Orkes — Product Backlog

**Studio:** Kshetra Studio · [askmybank.ai](https://askmybank.ai)  
**Updated:** 2026-06-13

Items are grouped by pipeline stage and ordered by priority within each group.
Status: 🟢 Done · 🔵 In Progress · 🔲 Planned · ⚠️ Blocked

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
| M-12 | Correlation set → Conductor correlation task mapping | 🔲 Planned | |
| M-13 | `<eventHandlers>` → parallel signal-listener sub-workflow | 🔲 Planned | |
| M-14 | Partner link → worker endpoint registry lookup | 🔲 Planned | Match partner links to known service URLs |

---

## Stage 3 — Code Generator

| # | Item | Status | Notes |
|---|------|--------|-------|
| G-1 | Emit clean Conductor workflow JSON (strip `_bpelSource`, `_warning`) | 🔲 Planned | First priority for Stage 3 |
| G-2 | Write one JSON file per workflow (main + sub-workflows + fault/comp handlers) | 🔲 Planned | |
| G-3 | Generate Conductor task definition stubs | 🔲 Planned | One task def per unique `<invoke>` operation |
| G-4 | Generate Python worker skeleton (one file per partner link) | 🔲 Planned | |
| G-5 | Generate Java worker skeleton (Spring Boot) | 🔲 Planned | Target audience is Java shops |
| G-6 | Generate `docker-compose.yml` for local Orkes + worker testing | 🔲 Planned | |
| G-7 | Simple XPath literal translation (`'APPROVED'` → `"APPROVED"`) | 🔲 Planned | 80% of assigns are literals |
| G-8 | Variable reference translation (`$var.part` → `${workflow.variables.var}`) | 🔲 Planned | |

---

## Stage 4 — Validator

| # | Item | Status | Notes |
|---|------|--------|-------|
| V-1 | POST generated workflow JSON to Orkes `/api/metadata/workflow` | 🔲 Planned | Dry-run validation |
| V-2 | Map Orkes validation errors back to BPEL source line numbers | 🔲 Planned | |
| V-3 | Report missing task definitions | 🔲 Planned | |
| V-4 | Check sub-workflow references are resolvable | 🔲 Planned | |
| V-5 | Validate compensation workflow invocation order | 🔲 Planned | |

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

## API Interface (REST)

Expose the parser + mapper pipeline as a self-hosted REST API so bank IT teams
can integrate conversion into their own CI/CD or migration tooling.

| # | Item | Status | Notes |
|---|------|--------|-------|
| A-1 | `POST /api/v1/convert` — accepts BPEL XML body, returns Conductor bundle JSON | 🔲 Planned | FastAPI or Flask; stateless |
| A-2 | `POST /api/v1/parse` — parse only, returns AST JSON (diagnostic endpoint) | 🔲 Planned | |
| A-3 | `POST /api/v1/validate` — parse + map + validate against Orkes instance | 🔲 Planned | Requires `orkesBaseUrl` + API key in request header |
| A-4 | `GET /api/v1/health` and `GET /api/v1/version` | 🔲 Planned | |
| A-5 | API key auth (simple, self-managed) for self-hosted deployments | 🔲 Planned | No cloud auth dependency |
| A-6 | `Docker` image — `ghcr.io/kshetra-studio/bpel2orkes:latest` | 🔲 Planned | Customer runs in their own VPC |
| A-7 | Helm chart for Kubernetes deployment (bank-friendly) | 🔲 Planned | |
| A-8 | Request size limit + BPEL-only content-type validation | 🔲 Planned | Reject non-BPEL payloads early |
| A-9 | No-persistence guarantee — BPEL input never written to disk or logged | 🔲 Planned | Core security requirement — see Security section below |

---

## MCP Server (Claude / AI Agent Integration)

Expose the converter as an MCP server so AI agents (Claude, Copilot, etc.)
can convert BPEL inline during a migration engagement — without the customer
pasting code into a chat window.

| # | Item | Status | Notes |
|---|------|--------|-------|
| MC-1 | `convert_bpel` tool — takes BPEL XML string, returns Conductor bundle | 🔲 Planned | Core MCP tool |
| MC-2 | `parse_bpel` tool — parse only, returns AST (for agent inspection) | 🔲 Planned | Useful for agents doing pre-flight analysis |
| MC-3 | `list_warnings` tool — returns just the `_warning` fields from a conversion | 🔲 Planned | Lets agent surface manual review items |
| MC-4 | `get_mapping_reference` tool — returns mapping table for a given BPEL construct | 🔲 Planned | In-context reference for agent guidance |
| MC-5 | MCP server runs **locally** (stdio transport) by default — BPEL never leaves machine | 🔲 Planned | Key security posture for enterprise use |
| MC-6 | Optional SSE transport for team deployments (self-hosted, behind VPN) | 🔲 Planned | |
| MC-7 | Publish to MCP Registry | 🔲 Planned | Discovery for Orkes/Claude ecosystem |

---

## Public Demo (Orkes Demo Platform)

**Decision: Optional feature — strong marketing value, strict data boundary required.**

Recommended approach:
- **Demo mode** only converts the four included sample BPEL files (no upload) — zero customer data risk, pure showcase
- **Hosted API** for real customer use is self-deployed (VPC/on-prem) — customer controls their data
- Orkes Demo platform deployment is valuable for Orkes sales team to demo the concept

| # | Item | Status | Notes |
|---|------|--------|-------|
| D-1 | Public demo UI at `askmybank.ai/bpel2orkes` — converts sample files only, no upload | 🔲 Planned | Safe for public; showcases output quality |
| D-2 | "Try with your own BPEL" mode — self-hosted Docker option linked from demo page | 🔲 Planned | Guides customer to run locally |
| D-3 | Deploy working demo workflow on Orkes Demo Conductor instance | 🔲 Planned | Show the actual converted workflow running, not just JSON |
| D-4 | Orkes Demo platform integration (coordinate with Orkes team) | 🔲 Planned | Use as joint marketing asset |

---

## Security & Data Governance

Banks will not use this tool without satisfying these requirements.
These are not optional — they gate enterprise adoption.

| # | Item | Status | Notes |
|---|------|--------|-------|
| SEC-1 | BPEL input is **never persisted** — processed in memory, discarded immediately | 🔲 Planned | Must be documented and auditable |
| SEC-2 | No telemetry or usage analytics on BPEL content | 🔲 Planned | Anonymous usage metrics (request count, duration) are acceptable with consent |
| SEC-3 | T&C / disclaimer for hosted API: "Do not submit production data or customer PII" | 🔲 Planned | BPEL process config should not contain PII — but disclaim anyway |
| SEC-4 | T&C must explicitly state: BPEL is process configuration, not customer data | 🔲 Planned | Helps bank legal teams approve usage |
| SEC-5 | Air-gap deployment guide (no outbound internet required) | 🔲 Planned | Many bank environments are air-gapped |
| SEC-6 | SBOM (Software Bill of Materials) — dependencies are `lxml`, `pytest` only | 🔲 Planned | Low risk; banks need this for procurement |
| SEC-7 | Penetration test checklist for self-hosted API | 🔲 Planned | |
| SEC-8 | MCP local-only mode as default — document clearly | 🔲 Planned | Prevents accidental cloud routing of BPEL |

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
