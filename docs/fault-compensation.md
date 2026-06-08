# Fault Handling and Compensation

This is the trickiest part of the migration. BPEL has a rich, structured model for
both fault handling and compensation. Conductor has `failureWorkflow` and task-level
retries. This document explains how the two models map and where manual work is required.

---

## The BPEL model (quick recap)

```
process
├── faultHandlers          ← catches faults thrown anywhere in the process
│   ├── catch faultName="X"
│   ├── catch faultName="Y"
│   └── catchAll
├── compensationHandler    ← rolls back the process if compensation is triggered
└── sequence
    └── scope              ← scopes have their own fault + compensation handlers
        ├── faultHandlers
        ├── compensationHandler
        └── activities...
```

Key properties:
- Fault handlers **intercept** — the process can continue after a caught fault.
- Compensation handlers **undo** — triggered explicitly by `<compensate>` inside a fault handler.
- Scopes are the unit of both fault handling and compensation.
- `<compensate name="scope">` selectively undoes a named scope.

---

## The Conductor model

```
workflow definition
└── failureWorkflow: "my_fault_handler_wf"   ← runs if the main workflow fails

task definition
└── retryCount, retryLogic, timeoutSeconds   ← task-level retry/timeout
```

Key properties:
- `failureWorkflow` runs when the workflow reaches a FAILED terminal state.
- It receives the failed workflow's ID and can query its output.
- There is no built-in compensation trigger — the failure workflow must explicitly
  invoke compensating workers.
- Task retries are configurable per task definition.

---

## Mapping approach

### Process-level `<faultHandlers>`

The mapper generates a **fault router workflow** containing a `SWITCH` task:

```
BPEL process-level faultHandlers
        │
        ▼
  fault_handler_<processName>  (a separate workflow)
        │
        ▼
  SWITCH task: routes by ${workflow.input.error.workflowType}
        ├── case "CreditCheckFault"  → [tasks from <catch faultName="tns:CreditCheckFault">]
        ├── case "ATOGatewayTimeout" → [tasks from <catch faultName="tns:ATOGatewayTimeout">]
        └── defaultCase             → [tasks from <catchAll>]
```

This workflow is set as `failureWorkflow` on the main workflow definition.

### Scope-level `<faultHandlers>`

Each `<scope>` becomes a `SUB_WORKFLOW`. Its fault handlers become that sub-workflow's
`failureWorkflow`.

```
BPEL <scope name="PersistResultScope">
  └── faultHandlers
        └── catch faultName="tns:CoreBankingWriteFailure"
        
        ↓ maps to ↓

Conductor SUB_WORKFLOW task (sub_PersistResultScope)
  failureWorkflow: "fault_handler_PersistResultScope"
```

### `<compensationHandler>`

Compensation handlers become **compensation workflows** — separate, named workflows
that perform the undo operations.

```
BPEL <scope name="CardNumberReservationScope">
  └── compensationHandler
        └── invoke ReleaseCardNumber

        ↓ maps to ↓

Workflow: compensate_CardNumberReservationScope
  tasks:
    - SIMPLE: release_cardNumberBureau_releaseCardNumber
```

The compensation workflow is referenced from the fault handler workflow. When a fault
handler wants to trigger compensation, it invokes the compensation sub-workflow as a
`SUB_WORKFLOW` task.

### `<compensate name="scope">` — selective compensation

BPEL allows compensating a specific named scope:

```xml
<compensate name="CompensateCoreAccount"/>
<compensate name="CompensateSchemeRegistration"/>
```

**Conductor has no native equivalent.** The mapper generates the individual compensation
workflows, but the wiring (invoking them in the right order) must be done manually.

**Recommended pattern:** In the fault handler workflow, add explicit `SUB_WORKFLOW`
tasks for each compensation that needs to run, in reverse order:

```json
{
  "tasks": [
    { "type": "SUB_WORKFLOW", "subWorkflowParam": { "name": "compensate_SchemeRegistrationScope" } },
    { "type": "SUB_WORKFLOW", "subWorkflowParam": { "name": "compensate_CoreAccountScope" } },
    { "type": "SUB_WORKFLOW", "subWorkflowParam": { "name": "compensate_CardNumberScope" } }
  ]
}
```

---

## Complete example: Credit Card Provisioning

The `credit_card_provisioning.bpel` process has three nested compensation scopes.
Here is the full fault/compensation flow as it maps to Conductor:

```
Conductor workflows generated:
─────────────────────────────
 CreditCardProvisioningProcess          ← main workflow
   failureWorkflow: fault_handler_CreditCardProvisioningProcess

 sub_CardNumberReservationScope         ← sub-workflow
   failureWorkflow: fault_handler_CardNumberReservationScope

 sub_CoreAccountScope                   ← sub-workflow
   failureWorkflow: fault_handler_CoreAccountScope

 sub_SchemeRegistrationScope            ← sub-workflow
   failureWorkflow: fault_handler_SchemeRegistrationScope

 fault_handler_CreditCardProvisioningProcess   ← fault router
   SWITCH on faultName:
     "SchemeRegistrationFailed" →
       SUB_WORKFLOW: compensate_CoreAccountScope
       SUB_WORKFLOW: compensate_CardNumberReservationScope
       SET_VARIABLE: status = SCHEME_REGISTRATION_FAILED
       SIMPLE: reply to BAW

     "EmbossingFailed" →
       SUB_WORKFLOW: compensate_SchemeRegistrationScope
       SUB_WORKFLOW: compensate_CoreAccountScope
       SUB_WORKFLOW: compensate_CardNumberReservationScope
       SET_VARIABLE: status = EMBOSSING_FAILED
       SIMPLE: reply to BAW

 compensate_CardNumberReservationScope  ← compensation workflow
   SIMPLE: release_cardNumberBureau_releaseCardNumber

 compensate_CoreAccountScope            ← compensation workflow
   SIMPLE: coreBanking_closeAccount

 compensate_SchemeRegistrationScope     ← compensation workflow
   SIMPLE: cardScheme_deregisterCard
```

---

## Task-level retries

BPEL has no built-in retry concept — retries are handled at the transport layer
(JAX-WS, MQ). In Conductor, retries are first-class on task definitions.

When migrating each `<invoke>`, set appropriate retry policy on the task definition:

```json
{
  "name": "call_creditService_checkCredit",
  "retryCount": 3,
  "retryLogic": "EXPONENTIAL_BACKOFF",
  "retryDelaySeconds": 5,
  "timeoutSeconds": 30,
  "timeoutPolicy": "RETRY"
}
```

For idempotent operations (reads, queries): retry freely.
For non-idempotent operations (writes, financial transactions): set `retryCount: 0`
and handle retries in the fault handler workflow with idempotency keys.
