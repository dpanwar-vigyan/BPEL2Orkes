# IBM WPS / BPELX Extension Mappings

IBM WebSphere Process Server (WPS) and Business Automation Workflow (BAW) add proprietary
extensions to standard WS-BPEL 2.0 under the namespace:

```
http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0
```

These are the most common ones found in APAC bank implementations and how they map.

---

## `<bpelx:task taskType="HUMAN_TASK">` → Conductor `HUMAN`

The most common IBM extension. Used for analyst review gates, fraud operations checks,
compliance approvals, and manager sign-offs.

### BPEL (IBM WPS)

```xml
<bpelx:task name="AnalystIncomeReview"
            taskType="HUMAN_TASK"
            staffQuery="role:LendingAnalyst,ou=IncomeVerification"
            potentialOwners="cn=income-analysts,ou=groups,dc=bank,dc=com"
            priority="2"
            durationExpression="'PT2H'"
            escalationTarget="role:SeniorAnalyst">
  <bpelx:input  variable="payrollResponse"/>
  <bpelx:input  variable="taxResponse"/>
  <bpelx:output variable="humanTaskOutput"/>
</bpelx:task>
```

### Conductor output

```json
{
  "name": "human_AnalystIncomeReview",
  "taskReferenceName": "AnalystIncomeReview",
  "type": "HUMAN",
  "inputParameters": {
    "staffQuery": "role:LendingAnalyst,ou=IncomeVerification",
    "potentialOwners": "cn=income-analysts,ou=groups,dc=bank,dc=com",
    "priority": "2",
    "escalationTarget": "role:SeniorAnalyst",
    "durationExpression": "'PT2H'"
  }
}
```

### What to do after migration

1. Register a **human task worker** in your Conductor deployment that integrates with your
   task management platform (Jira Service Desk, ServiceNow, Appian, or Orkes' built-in human task UI).
2. Map `staffQuery` to your identity provider (LDAP, Azure AD, Okta).
3. `durationExpression` (ISO 8601) maps to Conductor's task timeout — set `timeoutSeconds` on the task definition.
4. `escalationTarget` — implement via a Conductor event listener that fires when the human task times out.

---

## `<bpelx:callBusinessRule>` → Conductor `HTTP`

Calls IBM Operational Decision Manager (ODM) / ILOG JRules from within a BPEL process.
Common for credit decisioning, fraud scoring, product eligibility, and rate calculation.

### BPEL (IBM WPS)

```xml
<bpelx:callBusinessRule name="EvaluateIncomeRule"
                        ruleAppName="LendingDecisionApp"
                        ruleSetPath="/income-verification/v2/IncomeVerificationRuleset"
                        inputVariable="ruleRequest"
                        outputVariable="ruleResponse"/>
```

### Conductor output

```json
{
  "name": "rule_income_verification_v2_IncomeVerificationRuleset",
  "taskReferenceName": "EvaluateIncomeRule",
  "type": "HTTP",
  "inputParameters": {
    "http_request": {
      "uri": "${workflow.input.odmBaseUrl}/decision-service/rest/v1/LendingDecisionApp/1.0/IncomeVerificationRuleset/1.0",
      "method": "POST",
      "accept": "application/json",
      "contentType": "application/json",
      "body": "${ruleRequest}"
    },
    "_outputVariable": "ruleResponse"
  }
}
```

### What to do after migration

1. Set `workflow.input.odmBaseUrl` to your ODM server URL (or the containerised replacement).
2. If migrating away from ODM, replace with an `HTTP` task calling your new rule service,
   or an Orkes `INLINE` task with embedded JS for simple rules.
3. Verify the ODM REST API version path — ODM 8.x uses `/1.0/`, ODM 9.x may differ.

---

## `<bpelx:task taskType="SERVICE_TASK">` → `SIMPLE` ⚠️

Service tasks in IBM WPS can invoke SCA (Service Component Architecture) services,
mediation flows, or IIB (Integration Bus) flows.

### Conductor output

```json
{
  "name": "ibm_ext_task",
  "taskReferenceName": "MyServiceTask",
  "type": "SIMPLE",
  "_warning": "IBM extension <bpelx:task> taskType=SERVICE_TASK has no automatic mapping — review manually",
  "_bpelSource": { "rawXml": "..." }
}
```

### What to do after migration

Replace the `SIMPLE` placeholder with either:
- An `HTTP` task if the SCA service is accessible as a REST endpoint
- A `SIMPLE` task with a worker that calls the IIB/ACE flow
- A `SUB_WORKFLOW` if the service task wraps a complex flow

---

## `<bpelx:task taskType="DECISION_TASK">` → `SWITCH` or `HTTP` ⚠️

Decision tasks that route based on output of a rule. Map to `SWITCH` if the routing
logic is simple, or chain an `HTTP` (ODM call) + `SWITCH` for rule-driven routing.

---

## IBM SLA / Monitoring extensions

IBM WPS includes proprietary monitoring points (`<mon:instance>`, `<ibm:log>`) that
inject process metrics into WebSphere Business Monitor. These have no direct Conductor
equivalent.

**Recommended approach:** Replace with Conductor's built-in workflow event listeners.
Publish workflow start/complete/fail events to your observability platform
(Datadog, Splunk, Elastic) using a Conductor event task or a workflow lifecycle listener.

---

## Namespace reference

| Namespace URI | Used for |
|---------------|---------|
| `http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0` | WPS 6.x / BAW primary extension namespace |
| `http://www.ibm.com/xmlns/prod/websphere/business-process/7.0.0` | WPS 7.x extensions |
| `http://www.ibm.com/bpe/extension` | Older WPS 5.x extensions (rare) |
| `http://www.ibm.com/xmlns/prod/iib/10.0/monitoring` | IIB 10 monitoring hooks |

The parser captures all `bpelx:*` elements with their full namespace URI, `rawXml`,
and attributes, so no IBM-specific content is lost during parsing.
