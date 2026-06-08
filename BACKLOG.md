# BPEL2Orkes — Product Backlog

**Studio:** Kshetra Studio · [askmybank.ai](https://askmybank.ai)  
**Updated:** 2026-06-09

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
