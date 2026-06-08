# Banking Process Examples

Walk-through of the three included sample processes. Each is modelled on real BPEL
implementations at APAC banks running IBM WPS/BAW.

---

## 1. Income Verification

**File:** [`samples/income_verification.bpel`](../samples/income_verification.bpel)  
**Typical system:** IBM WPS 7.x / BAW 18+  
**Triggered by:** Mortgage or personal loan application (from digital banking or branch system)

### What it does

Verifies a customer's income against two independent data sources before a credit decision
is made. If bureau confidence is low (self-employed, seasonal income), routes to a human
analyst before the ODM rule fires.

### Process flow

```
Receive application (from channel)
        │
        ▼
Audit: log receipt
        │
        ▼
   ┌────┴────┐  PARALLEL
   │         │
   ▼         ▼
Payroll   ATO Tax
Bureau    Portal
   │         │
   └────┬────┘
        │
        ▼
 Low confidence?
   ┌────┴────┐
 YES        NO
   │         │
   ▼         ▼
IBM Human  Auto-merge
Task:      bureau data
Analyst
Review
   │
   └────┬────┘
        │
        ▼
IBM ODM callBusinessRule
(PASS / REFER / DECLINE)
        │
        ▼
 Scope: Persist to core banking
 (with compensation rollback)
        │
        ▼
Audit outcome
        │
        ▼
Reply to channel
```

### Key patterns and their Conductor mappings

| BPEL pattern | Conductor output |
|---|---|
| `<flow>` parallel bureau invokes | `FORK_JOIN` → two `SIMPLE` tasks → `JOIN` |
| `<if>` on confidence threshold | `SWITCH` (conditions need XPath→JS translation) |
| `<bpelx:task HUMAN_TASK>` analyst review | `HUMAN` task |
| `<bpelx:callBusinessRule>` ODM | `HTTP` task → ODM REST endpoint |
| `<scope>` persist + compensation | `SUB_WORKFLOW` with `failureWorkflow` for rollback |
| `<eventHandlers><onAlarm>` 4h SLA | Separate parallel sub-workflow with `WAIT` + `HUMAN` escalation |

### After migration checklist

- [ ] Translate XPath conditions in `SWITCH` to JavaScript (3 conditions)
- [ ] Wire `HUMAN` task to identity provider (`role:LendingAnalyst`)
- [ ] Set `odmBaseUrl` workflow input parameter
- [ ] Register worker implementations for payroll bureau and ATO gateway invokes
- [ ] Implement SLA breach event handler as a parallel sub-workflow

---

## 2. Communications Orchestration

**File:** [`samples/communications_orchestration.bpel`](../samples/communications_orchestration.bpel)  
**Typical system:** IBM WPS 8.x  
**Triggered by:** CRM, collections platform, product system (rate change letters, statements, hardship ack)

### What it does

Sends an outbound communication to a customer across SMS, email, and post (print-and-mail),
in priority order. Falls back to the next channel if delivery fails. Respects suppression
flags (DNC, hardship). Certain mandatory comms (legal notices) require compliance officer
approval even for suppressed customers.

### Process flow

```
Receive comms request
        │
        ▼
  ┌─────┴──────┐  PARALLEL
  │            │
  ▼            ▼
Contact     Suppression
Details     Registry
  │            │
  └─────┬──────┘
        │
        ▼
 Suppressed? + Mandatory?
  ┌─────┴──────┐
SUPPRESSED   NOT
+MANDATORY   SUPPRESSED
  │
  ▼
IBM Human Task:
Compliance Officer
Authorisation
  │
  ▼
  ┌──────────────┐
  │ SMS Channel  │ try first
  │  - send SMS  │
  │  - pick:     │
  │    onMessage │ delivery receipt
  │    onAlarm   │ 10 min timeout
  └──────────────┘
        │ failed?
        ▼
  ┌──────────────┐
  │ Email Channel│ fallback
  │  - send email│
  │  - pick:     │
  │    onMessage │ delivery event
  │    onAlarm   │ 30 min timeout
  └──────────────┘
        │ failed?
        ▼
  ┌──────────────┐
  │ Post Channel │ final fallback
  │  - print job │
  │  - pick:     │
  │    onMessage │ lodgement confirm
  │    onAlarm   │ 4 hour timeout
  └──────────────┘
        │
        ▼
Update CRM
        │
        ▼
ASIC/APRA compliance audit
        │
        ▼
Reply to requester
```

### Key patterns and their Conductor mappings

| BPEL pattern | Conductor output |
|---|---|
| `<flow>` contact + suppression parallel | `FORK_JOIN` + `JOIN` |
| `<if>` suppression + mandatory | `SWITCH` with nested `SWITCH` |
| `<bpelx:task HUMAN_TASK>` compliance approval | `HUMAN` task |
| `<scope>` per channel with compensation | `SUB_WORKFLOW` per channel |
| `<pick>` async delivery callback + timeout | `WAIT` task with `duration` timeout |
| Channel fallback chain | Sequential `SUB_WORKFLOW` tasks with `SWITCH` on delivery status |
| `<compensate>` cancel in-flight job | Compensation sub-workflow invoked from fault handler |

### The async callback pattern (pick)

This is the most important pattern in this process. BPEL's `<pick>` waits for one of
several possible incoming messages or an alarm. In Conductor:

```
BPEL:
  <pick name="WaitForSMSDelivery">
    <onMessage operation="smsDeliveryCallback" variable="smsDeliveryCallback">
      ... handle delivery ...
    </onMessage>
    <onAlarm><for>'PT10M'</for>
      <throw faultName="tns:SMSDeliveryFailed"/>
    </onAlarm>
  </pick>

Conductor:
  {
    "type": "WAIT",
    "inputParameters": {
      "duration": "'PT10M'",
      "waitForEvent": true
    }
  }
  // External system calls PUT /api/tasks/{taskId} to complete the WAIT
  // If duration expires, WAIT fails → failureWorkflow handles the timeout fault
```

The SMS gateway (Twilio / MessageMedia) must be configured to call back to the
Conductor task update endpoint when delivery is confirmed.

### After migration checklist

- [ ] Configure SMS gateway webhook → Conductor `PUT /api/tasks/{taskId}`
- [ ] Configure email platform delivery event → Conductor task update
- [ ] Configure print vendor callback → Conductor task update
- [ ] Translate suppression + mandatory conditions in `SWITCH`
- [ ] Wire `HUMAN` task to compliance team in identity provider
- [ ] Map ASIC/APRA audit `SIMPLE` task to your compliance event bus

---

## 3. Credit Card Provisioning

**File:** [`samples/credit_card_provisioning.bpel`](../samples/credit_card_provisioning.bpel)  
**Typical system:** IBM BAW 20+ (BPEL component, called from BAW case)  
**Triggered by:** BAW case process after credit assessment + human valuations complete

### What it does

The final provisioning step after a credit card application is approved. Reserves a card
number, opens the core banking account, registers with Visa/Mastercard, routes to fraud
ops review for high-risk cards, sends the card for physical embossing, dispatches via
Australia Post, provisions Apple/Google Pay tokens, and sends welcome communications.

### Process flow

```
Receive from BAW orchestrator
        │
        ▼
IBM ODM: Product routing
(Visa/MC, BIN range, interest rate, design)
        │
        ▼
Scope: Reserve card number (compensatable)
        │
        ▼
Fraud screen
        │
        ▼
 High risk or high limit?
        │
        ▼
IBM Human Task: Fraud Ops Review
(4 hour SLA, escalates to manager)
        │
        ▼
  ┌─────┴──────┐  PARALLEL
  │            │
  ▼            ▼
Core Banking  Card Scheme
Account       Registration
(scope with   (Visa/MC async
compensation) callback via pick)
  │            │
  └─────┬──────┘
        │
        ▼
Scope: Embossing
(submit job → wait for callback, 48h timeout)
        │
        ▼
Dispatch via Australia Post
        │
        ▼
  ┌─────┴──────┐  PARALLEL
  │            │
  ▼            ▼
Apple Pay   Google Pay
Token       Token
(non-critical, faults ignored)
        │
        ▼
Invoke: Communications Orchestration sub-process
(welcome SMS + email, card tracking reference)
        │
        ▼
Compliance audit
        │
        ▼
Reply to BAW (PROVISIONED + accountId + tracking)
```

### Key patterns and their Conductor mappings

| BPEL pattern | Conductor output |
|---|---|
| `<bpelx:callBusinessRule>` product routing | `HTTP` → ODM REST |
| Three nested compensatable scopes | Three `SUB_WORKFLOW` tasks with compensation workflows |
| `<bpelx:task HUMAN_TASK>` fraud ops | `HUMAN` task with 4h timeout + escalation |
| `<flow>` core account + scheme parallel | `FORK_JOIN` + `JOIN` |
| Scheme registration async `<pick>` | `WAIT` (scheme callback) |
| Embossing `<pick>` with 48h timeout | `WAIT` with 48h duration |
| Wallet `<scope>` with `<catchAll>` ignore | `SUB_WORKFLOW` with empty `failureWorkflow` |
| Invoke comms orchestration process | `SUB_WORKFLOW` (the comms process itself) |

### Compensation chain order

If scheme registration fails after the core account is open and the card number is
reserved, compensation must run in reverse order:

```
1. compensate_SchemeRegistrationScope  → deregister from Visa/MC
2. compensate_CoreAccountScope         → close the core banking account
3. compensate_CardNumberScope          → release the BIN number back to bureau
```

This order must be set explicitly in the fault handler workflow. See
[fault-compensation.md](fault-compensation.md) for the full pattern.

### After migration checklist

- [ ] Configure Visa/MC scheme callback → Conductor `PUT /api/tasks/{taskId}`
- [ ] Configure Datacard/Entrust emboss job callback → Conductor task update
- [ ] Wire `HUMAN` task to fraud ops team (4h timeout, manager escalation)
- [ ] Set `odmBaseUrl` for product routing rule
- [ ] Verify compensation workflow invocation order in fault handler
- [ ] Register `comms_orchestration` as a sub-workflow (deployed separately)
- [ ] Apple/Google Pay failures must NOT block the workflow — verify `catchAll` → empty fault handler
