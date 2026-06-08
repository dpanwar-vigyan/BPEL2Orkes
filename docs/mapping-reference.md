# BPEL → Conductor Mapping Reference

This is the canonical mapping table used by the pattern mapper. Every row is a covered case; rows marked ⚠️ require post-migration review.

---

## Control flow

| BPEL construct | Conductor type | Notes |
|----------------|---------------|-------|
| `<sequence>` | *(implicit)* | Conductor tasks in an array are sequential by default. No explicit task is emitted. |
| `<flow>` | `FORK_JOIN` + `JOIN` | Each child activity of the `<flow>` becomes a branch. The `JOIN` task waits for all branches. |
| `<if>` / `<elseif>` / `<else>` | `SWITCH` | Conditions are preserved in `_bpelSource`. The XPath expressions need translation to JS — flagged as `TODO` in the output. |
| `<switch>` / `<case>` / `<otherwise>` | `SWITCH` | BPEL 1.1 legacy form. Same handling as `<if>`. |
| `<while>` | `DO_WHILE` | Loop body maps to `loopOver`. Condition preserved in comment for manual translation. |
| `<repeatUntil>` | `DO_WHILE` | Semantics differ: runs once then checks. Condition is **inverted** — noted in `_bpelSource`. |
| `<forEach parallel="no">` | `DO_WHILE` | Sequential iteration using counter. |
| `<forEach parallel="yes">` | `FORK_JOIN_DYNAMIC` | Dynamic parallel fan-out. Input array must be provided at runtime. |
| `<pick onMessage>` | `WAIT` + optional `SWITCH` | Waits for an external event. `onAlarm` branch sets the `duration` timeout. Multiple `onMessage` branches generate a `SWITCH` after the `WAIT`. |
| `<scope>` | `SUB_WORKFLOW` | Scope boundary becomes a sub-workflow. Compensation handler becomes `failureWorkflow` on that sub-workflow. |

---

## Service interactions

| BPEL construct | Conductor type | Notes |
|----------------|---------------|-------|
| `<invoke>` | `SIMPLE` | Worker task. Task name derived from `partnerLink` + `operation`. The worker implementation connects to the real endpoint. |
| `<invoke>` (known HTTP pattern) | `HTTP` | When the partner link maps to a REST service, use `HTTP` task directly with endpoint in `inputParameters.http_request.uri`. |
| `<receive createInstance="yes">` | `WAIT` | First receive that starts the process. In Conductor, the workflow is triggered externally — this maps to an initial `WAIT` that unblocks on the trigger event. |
| `<receive>` (mid-flow) | `WAIT` | Async callback receive. Waits for an external signal. |
| `<reply>` | `SIMPLE` | Response worker task. Sends the output variable back to the calling channel. |

---

## Data manipulation

| BPEL construct | Conductor type | Notes |
|----------------|---------------|-------|
| `<assign>` | `SET_VARIABLE` | Each `<copy>` becomes an input parameter entry. XPath `from` expressions need translation to JSONPath / JS — flagged as `TODO`. |
| `<assign>` (simple literal) | `SET_VARIABLE` or `INLINE` | If all copies are literal assignments, `INLINE` with a JS expression is cleaner. |

---

## Error and compensation handling

| BPEL construct | Conductor equivalent | Notes |
|----------------|---------------------|-------|
| `<faultHandlers>` (process level) | `failureWorkflow` | A separate fault-router workflow is generated. It contains a `SWITCH` that routes by fault name. |
| `<faultHandlers>` (scope level) | `failureWorkflow` on sub-workflow | Each scope's fault handler becomes the `failureWorkflow` of its sub-workflow. |
| `<catch faultName="...">` | `SWITCH` case in fault router | Fault name becomes the switch case key. |
| `<catchAll>` | `defaultCase` in fault router SWITCH | |
| `<compensationHandler>` | Compensation sub-workflow | Named `compensate_<scopeName>`. Referenced as `failureWorkflow`. Must be triggered explicitly by the fault handler workflow if rollback is needed. |
| `<compensate name="...">` | `SUB_WORKFLOW` invoke of compensation workflow | ⚠️ Selective compensation (by name) requires manual wiring — Conductor has no built-in equivalent. |
| `<throw>` | `TERMINATE` (`terminationStatus: FAILED`) | Fault name and variable passed as `workflowOutput`. |
| `<rethrow>` | `TERMINATE` (`terminationStatus: FAILED`) | ⚠️ Original fault context is not automatically preserved — requires worker-level propagation. |
| `<exit>` | `TERMINATE` (`terminationStatus: COMPLETED`) | Immediate process exit. |

---

## Timing

| BPEL construct | Conductor type | Notes |
|----------------|---------------|-------|
| `<wait><for>PT24H</for></wait>` | `WAIT` with `duration` | ISO 8601 duration string passed directly. Conductor `WAIT` supports duration natively. |
| `<wait><until>...</until></wait>` | `WAIT` with `until` | ⚠️ XPath date expression needs translation to an absolute ISO timestamp or a JS expression. |
| `<pick><onAlarm><for>` | `WAIT` timeout | Alarm duration becomes the `WAIT` task's timeout. On expiry, Conductor fails the `WAIT` task and the failure workflow handles the timeout branch. |

---

## IBM WPS / BPELX extensions

| IBM construct | Conductor type | Notes |
|---------------|---------------|-------|
| `<bpelx:task taskType="HUMAN_TASK">` | `HUMAN` | Staff query, potential owners, priority, and escalation target mapped to `inputParameters`. |
| `<bpelx:callBusinessRule>` | `HTTP` | Generates an HTTP task calling the IBM ODM REST decision service. Endpoint built from `ruleAppName` + `ruleSetPath`. Base URL injected via `${workflow.input.odmBaseUrl}`. |
| `<bpelx:task taskType="...">` (other types) | `SIMPLE` ⚠️ | Non-HUMAN task types (SERVICE_TASK, etc.) emit a placeholder `SIMPLE` with a warning. |
| Unknown `<bpelx:*>` | `SIMPLE` ⚠️ | Unknown extensions are captured verbatim in `rawXml` and flagged with `_warning`. |

---

## What is not automatically mapped (manual review required)

These patterns are surfaced as `_warning` fields in the output JSON:

1. **XPath → JSONPath/JS translation** — All `<condition>` expressions, `<assign>` from-expressions, and `<wait><until>` expressions are preserved as strings with `TODO` markers.

2. **Selective compensation (`<compensate name="...">`)** — BPEL allows compensating a named scope. Conductor has no direct equivalent. Pattern: generate a compensation sub-workflow per scope and invoke them explicitly in the fault handler workflow.

3. **Correlation sets** — BPEL uses correlation properties to route async messages. In Conductor, correlate via workflow ID or a dedicated correlation task. Correlation set definitions are preserved in `_metadata`.

4. **Partner link types and WSDL** — Port types and operation contracts are not validated during mapping. Worker implementations must match the WSDL contracts.

5. **`<eventHandlers>`** — Process-level event handlers (onEvent, onAlarm) are extracted and noted but not fully mapped. Map to Conductor's event task or a parallel sub-workflow that listens for signals.
