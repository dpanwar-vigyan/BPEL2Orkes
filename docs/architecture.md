# Architecture

## Design principles

1. **Parse once, map many times.** The parser produces a stable JSON AST. The pattern
   mapper consumes that AST. This separation means the mapper can be improved or forked
   for different Conductor versions without touching the parser.

2. **Preserve everything.** Nothing from the BPEL source is discarded. IBM extensions,
   unknown elements, raw XML, correlation sets, partner link types — all stored in
   `_bpelSource` on each mapped task. The code generator can use this for a second pass.

3. **Warn, don't fail.** Items that can't be automatically mapped get a `_warning` field
   and a safe placeholder task. The migration engineer sees exactly what needs attention.

4. **Bank-grade test fixtures.** The included samples cover every pattern found in real
   APAC bank BPEL portfolios. Tests run against those samples, not toy examples.

---

## Pipeline stages

```
Stage 1: Parse
─────────────
bpel_parser.py
  Input:  .bpel file (WS-BPEL 2.0 + IBM BPELX extensions)
  Output: Python dict / JSON AST

  Key design decisions:
  - Uses lxml for namespace-aware XPath
  - Skips XML comments and PIs (they are not elements)
  - IBM BPELX elements captured verbatim with rawXml
  - Correlations, partner links, variables extracted at process level


Stage 2: Map
────────────
pattern_mapper.py
  Input:  Parsed BPEL AST (output of Stage 1)
  Output: Conductor workflow bundle dict

  Bundle shape:
  {
    "mainWorkflow":      {...},    # primary workflow definition
    "subWorkflows":      [{...}],  # one per <scope>
    "compensationFlows": [{...}],  # one per <compensationHandler>
    "faultHandlerFlows": [{...}],  # one per <faultHandlers> block
    "warnings":          ["..."]   # items needing manual review
  }

  Key design decisions:
  - sequence is a no-op (Conductor tasks are sequential by default)
  - flow emits FORK_JOIN + JOIN pair
  - scope emits SUB_WORKFLOW with inlined sub-workflow extracted post-mapping
  - IBM extensions dispatched by tag name
  - XPath conditions preserved as strings with TODO markers


Stage 3: Generate  [planned]
────────────────
code_generator.py
  Input:  Conductor bundle from Stage 2
  Output: Clean, deployable Conductor JSON files

  Will handle:
  - Stripping _bpelSource, _warning, _metadata from output JSON
  - Writing one file per workflow
  - Generating task definition stubs
  - Translating simple XPath expressions to JS (literals, variable refs)
  - Generating worker skeleton code (Python / Java / Node)


Stage 4: Validate  [planned]
────────────────
validator.py
  Input:  Generated workflow JSON files
  Output: Validation report (pass/fail per workflow)

  Will:
  - POST to Orkes /api/metadata/workflow (dry-run)
  - Map Orkes validation errors back to BPEL source lines
  - Report missing task definitions
```

---

## Key mapping decisions

### Why `sequence` emits nothing

Conductor task arrays are inherently sequential. Adding a wrapper task would increase
nesting with no benefit. The sequence name is preserved in `_bpelSource` on the first
child task if needed for traceability.

### Why `scope` becomes `SUB_WORKFLOW`

BPEL scopes have their own fault handlers, compensation handlers, event handlers, and
variable scope. In Conductor, the only construct that supports an independent
`failureWorkflow` is a workflow (or sub-workflow). The scope boundary is therefore
the natural sub-workflow boundary.

Side effect: complex BPEL processes with many nested scopes produce many sub-workflows.
This is correct — it mirrors the BPEL intent and gives operators visibility into each
scope's execution in the Conductor UI.

### Why `pick` becomes `WAIT`

BPEL `<pick>` suspends execution and waits for one of several incoming messages or
an alarm. Conductor's `WAIT` task suspends a workflow until an external system calls
the task update API. The `onAlarm` branch sets the WAIT's timeout duration. Multiple
`onMessage` branches generate a post-WAIT `SWITCH` that routes based on which event
arrived.

### Why IBM `callBusinessRule` becomes `HTTP`

IBM ODM exposes its decision services as REST APIs (since ODM 8.7). The `HTTP` task
is the cleanest mapping. If customers are migrating off ODM entirely, the `HTTP` task
endpoint is trivially swapped for the new rule service URL without changing the workflow
structure.

### Why faults become separate workflows (not inline catch)

Conductor's failure model is workflow-level, not activity-level. A failing workflow
(or sub-workflow) triggers its `failureWorkflow`. There is no per-task fault scope.
The fault router workflow pattern (SWITCH on fault name) is the closest equivalent to
BPEL's `<catch>` chain. This approach also has a benefit: fault handling logic becomes
visible, testable, and observable as its own workflow in the Conductor UI.

---

## File structure

```
src/
  bpel_parser.py      ~500 lines  — lxml-based XML parser
  pattern_mapper.py   ~550 lines  — activity dispatch + Conductor JSON builder

tests/
  test_bpel_parser.py   35 tests  — covers all BPEL constructs
  test_pattern_mapper.py 23 tests — covers all mapper paths

samples/
  loan_approval.bpel              — standard WS-BPEL 2.0 reference sample
  income_verification.bpel        — parallel bureaus + human task + ODM rule
  communications_orchestration.bpel — channel fallback + async pick callbacks
  credit_card_provisioning.bpel   — multi-scope compensation + fraud ops gate

docs/
  mapping-reference.md  — every construct → Conductor type
  ibm-extensions.md     — bpelx:task, bpelx:callBusinessRule, etc.
  fault-compensation.md — fault handlers, compensation, compensation chains
  banking-examples.md   — walk-through of all three bank samples
  architecture.md       — this file
```

---

## Running the tests

```bash
# Install dependencies
pip install lxml pytest

# Run all tests
python -m pytest tests/ -v

# Run just the parser tests
python -m pytest tests/test_bpel_parser.py -v

# Run just the mapper tests
python -m pytest tests/test_pattern_mapper.py -v
```

Current test coverage: **58 tests, 0 failures**.
