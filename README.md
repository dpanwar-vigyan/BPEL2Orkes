# BPEL → Orkes Conductor Migration Accelerator

**Kshetra Studio** · [askmybank.ai](https://askmybank.ai)

A code-first migration toolkit for banks running **IBM WPS / BAW / IIB** who want to move BPEL process orchestration to **Orkes Conductor**.

---

## Why this exists

Banks in APAC carry thousands of BPEL processes — income verification, communications orchestration, card provisioning, payment workflows. Re-writing them by hand is a 2–3 year programme. This accelerator makes it a weeks-level task.

BPEL and Conductor share the same conceptual DNA (both are explicit workflow orchestrators). The mapping is ~85% automatable. This toolkit handles that 85% and flags the remaining 15% for human review.

---

## Pipeline

```
  .bpel file
      │
      ▼
  ┌─────────────┐
  │ BPEL Parser │  XML → structured JSON AST
  └─────────────┘
      │
      ▼
  ┌────────────────┐
  │ Pattern Mapper │  AST → Conductor workflow JSON
  └────────────────┘
      │
      ▼
  ┌────────────────────┐
  │ Code Generator     │  (coming) clean up + emit deployable JSON
  └────────────────────┘
      │
      ▼
  ┌───────────┐
  │ Validator │  (coming) POST to Orkes /api/metadata/workflow
  └───────────┘
```

---

## Quick start

```bash
# Parse a BPEL file to JSON
python src/bpel_parser.py samples/income_verification.bpel

# Map BPEL → Conductor workflow bundle
python src/pattern_mapper.py samples/income_verification.bpel output.json

# Run all tests
python -m pytest tests/ -v
```

---

## Documentation

| Doc | Contents |
|-----|----------|
| [Mapping Reference](docs/mapping-reference.md) | Every BPEL construct → Conductor task type |
| [IBM Extensions](docs/ibm-extensions.md) | `bpelx:task`, `bpelx:callBusinessRule` and other WPS-specific mappings |
| [Fault & Compensation](docs/fault-compensation.md) | How BPEL fault handlers and compensation handlers map to Conductor failure workflows |
| [Banking Examples](docs/banking-examples.md) | Walk-through of the three included bank process samples |
| [Architecture](docs/architecture.md) | Design decisions and pipeline internals |

---

## Sample processes

Three synthetic bank processes modelled on real APAC bank BPEL implementations:

| Sample | Patterns covered |
|--------|-----------------|
| [`income_verification.bpel`](samples/income_verification.bpel) | Parallel bureau invokes, human review gate, ODM business rule, scope compensation, SLA event handler |
| [`communications_orchestration.bpel`](samples/communications_orchestration.bpel) | Suppression check, channel fallback chain, async delivery callbacks (pick), compensation per channel |
| [`credit_card_provisioning.bpel`](samples/credit_card_provisioning.bpel) | BAW→BPEL handoff, fraud ops human task, parallel scheme + core account setup, async embossing callback, digital wallet provisioning |

---

## Project structure

```
BPEL2Orkes/
├── src/
│   ├── bpel_parser.py      # Stage 1 — XML → JSON AST
│   └── pattern_mapper.py   # Stage 2 — AST → Conductor JSON
├── tests/
│   ├── test_bpel_parser.py
│   └── test_pattern_mapper.py
├── samples/
│   ├── loan_approval.bpel
│   ├── income_verification.bpel
│   ├── communications_orchestration.bpel
│   └── credit_card_provisioning.bpel
└── docs/
```

---

## Status

| Component | Status |
|-----------|--------|
| BPEL Parser | ✅ Complete — WS-BPEL 2.0 + IBM BPELX |
| Pattern Mapper | ✅ Complete — all activity types |
| Code Generator | 🔲 Planned |
| Validator | 🔲 Planned |
