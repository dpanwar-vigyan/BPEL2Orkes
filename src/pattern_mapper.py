"""
Pattern Mapper — translates parsed BPEL JSON into Orkes Conductor workflow JSON.

Each BPEL activity type maps to one or more Conductor task definitions.
Scope boundaries become sub-workflows. Compensation handlers become failure workflows.

Conductor task types used:
  SIMPLE          — invoke, reply, persist operations (worker tasks)
  HTTP            — invoke where operation maps to a known HTTP endpoint
  SET_VARIABLE    — assign
  SWITCH          — if/elseif/else, switch/case
  FORK_JOIN       — flow (parallel activities)
  JOIN            — closing join after a fork
  DO_WHILE        — while, repeatUntil, forEach (sequential)
  FORK_JOIN_DYNAMIC — forEach parallel="yes"
  SUB_WORKFLOW    — scope
  WAIT            — receive, pick (async callback), wait (timer)
  TERMINATE       — throw, rethrow, exit
  HUMAN           — bpelx:task taskType=HUMAN_TASK
  EVENT           — publish to audit/event bus
  INLINE          — lightweight assign expressions
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

# ── Helpers ────────────────────────────────────────────────────────────────────

def _slug(name: str | None, fallback: str = "task") -> str:
    """Convert a BPEL activity name to a safe Conductor task reference name."""
    if not name:
        return f"{fallback}_{_short_id()}"
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return s[:64]


def _short_id() -> str:
    return uuid.uuid4().hex[:6]


def _strip_prefix(qname: str | None) -> str | None:
    """Remove namespace prefix from a QName like 'tns:MyType'."""
    if not qname:
        return None
    return qname.split(":")[-1] if ":" in qname else qname


# ── Leaf activity mappers ──────────────────────────────────────────────────────

def _map_invoke(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "invoke")
    partner = act.get("partnerLink", "unknown")
    operation = act.get("operation", "unknown")
    task: dict[str, Any] = {
        "name": f"{_slug(partner)}_{_slug(operation)}",
        "taskReferenceName": ref,
        "type": "SIMPLE",
        "inputParameters": {
            "partnerLink": partner,
            "operation": operation,
            "inputVariable": act.get("inputVariable"),
            "outputVariable": act.get("outputVariable"),
        },
        "_bpelSource": {"type": "invoke", "name": act.get("name")},
        "_warning": (
            f"<invoke> '{act.get('name', operation)}' → SIMPLE task (worker stub). "
            f"If partnerLink '{partner}' was a SOAP/WSDL service or SCA Java component, "
            f"implement a Conductor worker: register task type '{_slug(partner)}_{_slug(operation)}', "
            f"deploy a worker that polls GET /api/tasks/poll/..., executes the call, "
            f"and posts results back. SOAP callers must handle WS envelope and security in the worker."
        ),
    }
    return [task]


def _map_receive(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "receive")
    partner = act.get("partnerLink", "unknown")
    operation = act.get("operation", "unknown")
    return [{
        "name": f"wait_{_slug(operation)}",
        "taskReferenceName": ref,
        "type": "WAIT",
        "inputParameters": {
            "partnerLink": partner,
            "operation": operation,
            "variable": act.get("variable"),
            "createInstance": act.get("createInstance", "no"),
        },
        "_bpelSource": {"type": "receive", "name": act.get("name")},
        "_warning": (
            f"<receive> '{act.get('name', operation)}' uses ESB push pattern — "
            f"BPEL waited for an inbound message on partnerLink '{partner}'. "
            f"In Conductor this WAIT task must be completed by an external callback: "
            f"POST /api/tasks/{{taskId}}/ack with the payload. "
            f"Wire your ESB/MQ consumer or API gateway to call that endpoint."
        ),
    }]


def _map_reply(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "reply")
    return [{
        "name": f"reply_{_slug(act.get('operation', 'response'))}",
        "taskReferenceName": ref,
        "type": "SIMPLE",
        "inputParameters": {
            "partnerLink": act.get("partnerLink"),
            "operation": act.get("operation"),
            "variable": act.get("variable"),
            "faultName": act.get("faultName"),
        },
        "_bpelSource": {"type": "reply", "name": act.get("name")},
    }]


def _map_assign(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "assign")
    # Each copy becomes an inputParameters entry
    params: dict[str, Any] = {}
    for i, copy in enumerate(act.get("copies", [])):
        params[f"copy_{i}_from"] = copy.get("from", {})
        params[f"copy_{i}_to"] = copy.get("to", {})
    return [{
        "name": f"set_{ref}",
        "taskReferenceName": ref,
        "type": "SET_VARIABLE",
        "inputParameters": params,
        "_bpelSource": {"type": "assign", "name": act.get("name"), "copies": act.get("copies", [])},
    }]


def _map_throw(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "throw")
    return [{
        "name": f"terminate_{_slug(act.get('faultName', 'fault'))}",
        "taskReferenceName": ref,
        "type": "TERMINATE",
        "inputParameters": {
            "terminationStatus": "FAILED",
            "workflowOutput": {
                "faultName": act.get("faultName"),
                "faultVariable": act.get("faultVariable"),
            },
        },
        "_bpelSource": {"type": "throw", "name": act.get("name")},
    }]


def _map_rethrow(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "rethrow")
    return [{
        "name": "rethrow_fault",
        "taskReferenceName": ref,
        "type": "TERMINATE",
        "inputParameters": {"terminationStatus": "FAILED"},
        "_bpelSource": {"type": "rethrow"},
    }]


def _map_exit(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "exit")
    return [{
        "name": "process_exit",
        "taskReferenceName": ref,
        "type": "TERMINATE",
        "inputParameters": {"terminationStatus": "COMPLETED"},
        "_bpelSource": {"type": "exit"},
    }]


def _map_empty(act: dict) -> list[dict]:
    # Empty activity — map to a no-op INLINE task
    ref = _slug(act.get("name"), "empty")
    return [{
        "name": "noop",
        "taskReferenceName": ref,
        "type": "INLINE",
        "inputParameters": {
            "evaluatorType": "javascript",
            "expression": "function execute() { return {}; }",
        },
        "_bpelSource": {"type": "empty"},
    }]


def _map_wait(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "wait")
    duration = act.get("for") or act.get("until")
    return [{
        "name": "wait_timer",
        "taskReferenceName": ref,
        "type": "WAIT",
        "inputParameters": {
            "duration": duration,
            "waitType": "for" if act.get("for") else "until",
        },
        "_bpelSource": {"type": "wait", "name": act.get("name")},
    }]


# ── Compound activity mappers ──────────────────────────────────────────────────

def _map_sequence(act: dict) -> list[dict]:
    """Sequence flattens — Conductor tasks are implicitly sequential in a list."""
    tasks = []
    for child in act.get("activities", []):
        tasks.extend(map_activity(child))
    return tasks


def _map_flow(act: dict) -> list[dict]:
    """BPEL <flow> → FORK_JOIN + JOIN pair."""
    fork_ref = _slug(act.get("name"), "fork")
    join_ref = f"join_{fork_ref}"
    branches = []
    all_branch_tasks = []

    for child in act.get("activities", []):
        branch_tasks = map_activity(child)
        if branch_tasks:
            branches.append([t["taskReferenceName"] for t in branch_tasks])
            all_branch_tasks.extend(branch_tasks)

    fork_task: dict[str, Any] = {
        "name": f"fork_{_slug(act.get('name', ''))}",
        "taskReferenceName": fork_ref,
        "type": "FORK_JOIN",
        "forkTasks": [map_activity(child) for child in act.get("activities", [])],
        "_bpelSource": {"type": "flow", "name": act.get("name")},
    }
    join_task: dict[str, Any] = {
        "name": f"join_{_slug(act.get('name', ''))}",
        "taskReferenceName": join_ref,
        "type": "JOIN",
        "joinOn": [branch[-1] for branch in branches if branch],
        "_bpelSource": {"type": "flow_join"},
    }
    return [fork_task, join_task]


def _map_if(act: dict) -> list[dict]:
    """BPEL <if/elseif/else> → SWITCH task."""
    ref = _slug(act.get("name"), "switch")
    branches = act.get("branches", [])

    decision_cases: dict[str, list] = {}
    default_case: list = []

    for i, branch in enumerate(branches):
        condition = branch.get("condition")
        branch_tasks = []
        for child in branch.get("activities", []):
            branch_tasks.extend(map_activity(child))
        if condition is None:
            default_case = branch_tasks
        else:
            # Conductor SWITCH uses string keys; embed the XPath condition as the key
            key = f"branch_{i}_{_slug(condition[:40]) if condition else 'default'}"
            decision_cases[key] = branch_tasks

    switch_task: dict[str, Any] = {
        "name": f"switch_{_slug(act.get('name', ''))}",
        "taskReferenceName": ref,
        "type": "SWITCH",
        "evaluatorType": "javascript",
        # The expression is a placeholder — code generator will replace with real eval
        "expression": _build_switch_expression(branches),
        "decisionCases": decision_cases,
        "defaultCase": default_case,
        "_bpelSource": {
            "type": "if",
            "name": act.get("name"),
            "conditions": [b.get("condition") for b in branches],
        },
    }
    return [switch_task]


def _build_switch_expression(branches: list[dict]) -> str:
    """Generate a JS expression placeholder for SWITCH evaluator."""
    conditions = []
    for i, b in enumerate(branches):
        cond = b.get("condition")
        if cond:
            conditions.append(f'/* branch_{i}: {cond[:80]} */')
    return "/* TODO: translate XPath conditions */ $.branch"


def _map_while(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "while")
    body_tasks = []
    for child in act.get("activities", []):
        body_tasks.extend(map_activity(child))

    return [{
        "name": f"while_{_slug(act.get('name', ''))}",
        "taskReferenceName": ref,
        "type": "DO_WHILE",
        "loopCondition": f"/* BPEL while: {act.get('condition', '')} */",
        "loopOver": body_tasks,
        "_bpelSource": {
            "type": "while",
            "condition": act.get("condition"),
        },
    }]


def _map_repeat_until(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "repeatuntil")
    body_tasks = []
    for child in act.get("activities", []):
        body_tasks.extend(map_activity(child))

    return [{
        "name": f"repeat_{_slug(act.get('name', ''))}",
        "taskReferenceName": ref,
        "type": "DO_WHILE",
        # repeatUntil runs first, then checks — invert condition for DO_WHILE
        "loopCondition": f"/* BPEL repeatUntil (inverted): {act.get('condition', '')} */",
        "loopOver": body_tasks,
        "_bpelSource": {
            "type": "repeatUntil",
            "condition": act.get("condition"),
            "note": "condition is inverted — repeatUntil runs once then checks",
        },
    }]


def _map_foreach(act: dict) -> list[dict]:
    ref = _slug(act.get("name"), "foreach")
    body_tasks = []
    for child in act.get("activities", []):
        body_tasks.extend(map_activity(child))

    if act.get("parallel") == "yes":
        return [{
            "name": f"foreach_parallel_{_slug(act.get('name', ''))}",
            "taskReferenceName": ref,
            "type": "FORK_JOIN_DYNAMIC",
            "dynamicForkTasksParam": "forkedTasks",
            "dynamicForkTasksInputParamName": "forkedTasksInput",
            "inputParameters": {
                "counterName": act.get("counterName"),
                "startCounterValue": act.get("startCounterValue"),
                "finalCounterValue": act.get("finalCounterValue"),
            },
            "_bpelSource": {"type": "forEach", "parallel": "yes"},
        }]
    else:
        return [{
            "name": f"foreach_seq_{_slug(act.get('name', ''))}",
            "taskReferenceName": ref,
            "type": "DO_WHILE",
            "loopCondition": (
                f"$.{ref}['iteration'] < "
                f"parseInt('{act.get('finalCounterValue', '0')}')"
            ),
            "loopOver": body_tasks,
            "_bpelSource": {"type": "forEach", "parallel": "no"},
        }]


def _map_pick(act: dict) -> list[dict]:
    """BPEL <pick> → WAIT task that unblocks on an external event, with timeout."""
    ref = _slug(act.get("name"), "pick")
    branches = act.get("branches", [])

    # Find alarm branch for timeout
    alarm = next((b for b in branches if b.get("trigger") == "onAlarm"), None)
    timeout = None
    if alarm:
        timeout = alarm.get("for") or alarm.get("until")

    # onMessage branches become post-WAIT SWITCH
    msg_branches = [b for b in branches if b.get("trigger") == "onMessage"]

    operations = [b.get("operation", "?") for b in msg_branches]
    wait_task: dict[str, Any] = {
        "name": f"wait_event_{_slug(act.get('name', ''))}",
        "taskReferenceName": ref,
        "type": "WAIT",
        "inputParameters": {
            "duration": timeout,
            "waitForEvent": True,
            "onMessageBranches": [
                {
                    "operation": b.get("operation"),
                    "partnerLink": b.get("partnerLink"),
                    "variable": b.get("variable"),
                }
                for b in msg_branches
            ],
        },
        "_bpelSource": {"type": "pick", "name": act.get("name")},
        "_warning": (
            f"<pick> '{act.get('name', ref)}' waits for one of {len(msg_branches)} "
            f"inbound message(s) ({', '.join(operations)}) — ESB push pattern. "
            f"In Conductor, each event source must POST to /api/tasks/{{taskId}}/ack to unblock "
            f"this WAIT. Identify which ESB channel, queue, or API triggers each onMessage branch "
            f"and add the callback call. "
            + (f"Alarm/timeout of {timeout} maps to WAIT duration." if timeout else "No timeout defined — WAIT will block indefinitely without an external callback.")
        ),
    }
    tasks = [wait_task]

    # If multiple onMessage handlers, add a SWITCH to route after wait
    if len(msg_branches) > 1:
        switch_ref = f"route_{ref}"
        cases: dict[str, list] = {}
        for b in msg_branches:
            key = _slug(b.get("operation", "msg"))
            branch_tasks = []
            for child in b.get("activities", []):
                branch_tasks.extend(map_activity(child))
            cases[key] = branch_tasks
        tasks.append({
            "name": f"route_pick_{_slug(act.get('name', ''))}",
            "taskReferenceName": switch_ref,
            "type": "SWITCH",
            "evaluatorType": "javascript",
            "expression": "$.eventType",
            "decisionCases": cases,
            "defaultCase": [],
        })
    elif msg_branches:
        for child in msg_branches[0].get("activities", []):
            tasks.extend(map_activity(child))

    return tasks


def _map_scope(act: dict) -> list[dict]:
    """BPEL <scope> → SUB_WORKFLOW. Compensation handler → failureWorkflow reference."""
    ref = _slug(act.get("name"), "scope")
    sub_wf_name = f"sub_{ref}"

    # Build the sub-workflow inline (will be extracted by code generator)
    body_tasks = []
    for child in act.get("activities", []):
        body_tasks.extend(map_activity(child))

    compensation = act.get("compensationHandler")
    failure_wf = None
    if compensation:
        failure_wf = f"compensate_{ref}"

    task: dict[str, Any] = {
        "name": sub_wf_name,
        "taskReferenceName": ref,
        "type": "SUB_WORKFLOW",
        "subWorkflowParam": {
            "name": sub_wf_name,
            "version": 1,
        },
        "_inlinedSubWorkflow": {
            "name": sub_wf_name,
            "tasks": body_tasks,
            "failureWorkflow": failure_wf,
        },
        "_bpelSource": {
            "type": "scope",
            "name": act.get("name"),
            "hasFaultHandlers": "faultHandlers" in act,
            "hasCompensationHandler": compensation is not None,
        },
    }

    if compensation:
        comp_tasks = []
        for child in compensation.get("activities", []):
            comp_tasks.extend(map_activity(child))
        task["_compensationWorkflow"] = {
            "name": failure_wf,
            "tasks": comp_tasks,
        }

    return [task]


def _map_ibm_extension(act: dict) -> list[dict]:
    tag = act.get("tag", "")
    attrs = act.get("attributes", {})
    ref = _slug(attrs.get("name", tag), tag)

    if tag == "task" and attrs.get("taskType") == "HUMAN_TASK":
        return [{
            "name": f"human_{ref}",
            "taskReferenceName": ref,
            "type": "HUMAN",
            "inputParameters": {
                "staffQuery": attrs.get("staffQuery"),
                "potentialOwners": attrs.get("potentialOwners"),
                "priority": attrs.get("priority", "3"),
                "escalationTarget": attrs.get("escalationTarget"),
                "durationExpression": attrs.get("durationExpression"),
            },
            "_bpelSource": {
                "type": "ibmExtension",
                "tag": "task",
                "taskType": "HUMAN_TASK",
                "rawXml": act.get("rawXml"),
            },
        }]

    if tag == "callBusinessRule":
        return [{
            "name": f"rule_{_slug(attrs.get('ruleSetPath', ref))}",
            "taskReferenceName": ref,
            "type": "HTTP",
            "inputParameters": {
                "http_request": {
                    "uri": "${workflow.input.odmBaseUrl}" + f"/decision-service/rest/v1/{attrs.get('ruleAppName', 'RuleApp')}/1.0/{_slug(attrs.get('ruleSetPath', 'ruleset'))}/1.0",
                    "method": "POST",
                    "accept": "application/json",
                    "contentType": "application/json",
                    "body": f"${{{attrs.get('inputVariable', 'ruleInput')}}}",
                },
                "_outputVariable": attrs.get("outputVariable"),
            },
            "_bpelSource": {
                "type": "ibmExtension",
                "tag": "callBusinessRule",
                "ruleAppName": attrs.get("ruleAppName"),
                "ruleSetPath": attrs.get("ruleSetPath"),
            },
        }]

    # Unknown IBM extension — emit a placeholder SIMPLE task
    return [{
        "name": f"ibm_ext_{_slug(tag)}",
        "taskReferenceName": ref,
        "type": "SIMPLE",
        "inputParameters": {**attrs},
        "_bpelSource": {"type": "ibmExtension", "tag": tag, "rawXml": act.get("rawXml")},
        "_warning": f"IBM extension <bpelx:{tag}> has no automatic mapping — review manually",
    }]


# ── Activity dispatch ──────────────────────────────────────────────────────────

_MAPPERS = {
    "invoke":        _map_invoke,
    "receive":       _map_receive,
    "reply":         _map_reply,
    "assign":        _map_assign,
    "throw":         _map_throw,
    "rethrow":       _map_rethrow,
    "exit":          _map_exit,
    "empty":         _map_empty,
    "wait":          _map_wait,
    "sequence":      _map_sequence,
    "flow":          _map_flow,
    "if":            _map_if,
    "while":         _map_while,
    "repeatUntil":   _map_repeat_until,
    "forEach":       _map_foreach,
    "pick":          _map_pick,
    "scope":         _map_scope,
    "ibmExtension":  _map_ibm_extension,
}


def map_activity(act: dict) -> list[dict]:
    """Map a single parsed BPEL activity to a list of Conductor task dicts."""
    if not act:
        return []
    mapper = _MAPPERS.get(act.get("type", ""))
    if mapper:
        return mapper(act)
    return [{
        "name": f"unmapped_{act.get('type', 'unknown')}",
        "taskReferenceName": _slug(act.get("name"), "unmapped"),
        "type": "SIMPLE",
        "_warning": f"No mapping for BPEL type '{act.get('type')}' — review manually",
        "_bpelSource": act,
    }]


# ── Fault handler mapping ──────────────────────────────────────────────────────

def _map_fault_handlers(fh: dict | None, workflow_name: str) -> list[dict]:
    """
    Produce a list of failure workflow descriptors from BPEL faultHandlers.
    In Conductor, a single failureWorkflow is referenced per workflow.
    We generate one fault-router workflow that SWITCH-es on faultName.
    """
    if not fh:
        return []

    catch_cases: dict[str, list] = {}
    for catch in fh.get("catches", []):
        fname = _strip_prefix(catch.get("faultName")) or "unknownFault"
        branch_tasks = []
        for child in catch.get("activities", []):
            branch_tasks.extend(map_activity(child))
        catch_cases[fname] = branch_tasks

    default_tasks = []
    if fh.get("catchAll"):
        for child in fh["catchAll"].get("activities", []):
            default_tasks.extend(map_activity(child))

    fault_router = {
        "name": f"fault_handler_{workflow_name}",
        "description": f"Auto-generated fault handler for {workflow_name}",
        "version": 1,
        "tasks": [{
            "name": "route_fault",
            "taskReferenceName": "route_fault",
            "type": "SWITCH",
            "evaluatorType": "javascript",
            "expression": "$.workflow.input.error.workflowType",
            "decisionCases": catch_cases,
            "defaultCase": default_tasks,
        }],
    }
    return [fault_router]


# ── Top-level process mapper ───────────────────────────────────────────────────

def map_process(parsed: dict) -> dict:
    """
    Map a fully parsed BPEL document to a Conductor workflow bundle.

    Returns:
        {
          "mainWorkflow":       {...},      # the primary workflow definition
          "subWorkflows":       [{...}],    # extracted scope sub-workflows
          "compensationFlows":  [{...}],    # compensation handler workflows
          "faultHandlerFlows":  [{...}],    # fault handler workflows
          "warnings":           ["..."],    # items needing manual review
        }
    """
    process = parsed.get("process", {})
    wf_name = _slug(process.get("name", "workflow"))

    # Map main body
    body_activity = process.get("activity", {})
    main_tasks = map_activity(body_activity)

    # Collect fault handler workflows
    fault_wfs = _map_fault_handlers(process.get("faultHandlers"), wf_name)

    # Collect compensation handler
    comp_wfs = []
    comp = process.get("compensationHandler")
    if comp:
        comp_tasks = []
        for child in comp.get("activities", []):
            comp_tasks.extend(map_activity(child))
        comp_wfs.append({
            "name": f"compensate_{wf_name}",
            "description": f"Compensation workflow for {wf_name}",
            "version": 1,
            "tasks": comp_tasks,
        })

    # Build input parameters from partner links + variables
    input_params = _build_input_parameters(process)

    main_workflow = {
        "name": wf_name,
        "description": f"Migrated from BPEL process {process.get('name')}",
        "version": 1,
        "inputParameters": input_params,
        "tasks": main_tasks,
        "failureWorkflow": fault_wfs[0]["name"] if fault_wfs else None,
        "outputParameters": {
            "processName": process.get("name"),
            "targetNamespace": process.get("targetNamespace"),
        },
        "timeoutPolicy": "ALERT_ONLY",
        "timeoutSeconds": 0,
        "_metadata": {
            "migratedFrom": "BPEL",
            "bpelProcessName": process.get("name"),
            "bpelTargetNamespace": process.get("targetNamespace"),
            "suppressJoinFailure": process.get("suppressJoinFailure"),
        },
    }

    # Extract inlined sub-workflows and compensation workflows from scope tasks
    sub_wfs, comp_from_scopes, warnings = _extract_nested_workflows(main_tasks)

    # M-17: warn on correlation sets — not automatically mapped
    for cs in process.get("correlationSets", []):
        cs_name = cs.get("name", "unknown")
        props = cs.get("properties", [])
        warnings.append(
            f"Correlation set '{cs_name}' (properties: {', '.join(props) or 'none'}) is not mapped. "
            f"In BPEL this routed async callbacks back to the correct process instance. "
            f"In Conductor, use the workflow ID as your correlation key: pass it in every "
            f"outbound HTTP call header (e.g. X-Correlation-Id: ${{workflow.workflowId}}) "
            f"and have callers include it when posting task callbacks."
        )

    return {
        "mainWorkflow": main_workflow,
        "subWorkflows": sub_wfs,
        "compensationFlows": comp_wfs + comp_from_scopes,
        "faultHandlerFlows": fault_wfs,
        "warnings": warnings,
    }


def _build_input_parameters(process: dict) -> list[str]:
    params = []
    for pl in process.get("partnerLinks", []):
        params.append(f"{pl['name']}Endpoint")
    for v in process.get("variables", []):
        params.append(v["name"])
    return params


def _extract_nested_workflows(
    tasks: list[dict],
    sub_wfs: list | None = None,
    comp_wfs: list | None = None,
    warnings: list | None = None,
) -> tuple[list, list, list]:
    """Recursively extract inlined sub-workflows and collect warnings."""
    if sub_wfs is None:
        sub_wfs = []
    if comp_wfs is None:
        comp_wfs = []
    if warnings is None:
        warnings = []

    for task in tasks:
        if "_warning" in task:
            warnings.append(f"[{task.get('taskReferenceName')}] {task['_warning']}")

        if "_inlinedSubWorkflow" in task:
            sub_wf = task.pop("_inlinedSubWorkflow")
            sub_wfs.append(sub_wf)
            _extract_nested_workflows(sub_wf.get("tasks", []), sub_wfs, comp_wfs, warnings)

        if "_compensationWorkflow" in task:
            comp_wfs.append(task.pop("_compensationWorkflow"))

        # Recurse into fork branches
        for branch_list in task.get("forkTasks", []):
            _extract_nested_workflows(branch_list, sub_wfs, comp_wfs, warnings)
        for case_tasks in task.get("decisionCases", {}).values():
            _extract_nested_workflows(case_tasks, sub_wfs, comp_wfs, warnings)
        _extract_nested_workflows(task.get("defaultCase", []), sub_wfs, comp_wfs, warnings)
        _extract_nested_workflows(task.get("loopOver", []), sub_wfs, comp_wfs, warnings)

    return sub_wfs, comp_wfs, warnings


# ── Public API ─────────────────────────────────────────────────────────────────

def map_bpel_to_conductor(parsed_bpel: dict) -> dict:
    """
    Entry point: takes output of bpel_parser.parse_bpel() and returns
    a Conductor workflow bundle dict.
    """
    return map_process(parsed_bpel)


def map_bpel_file_to_conductor(bpel_path: str | Path) -> dict:
    """Convenience: parse a BPEL file and map it in one call."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from bpel_parser import parse_bpel
    parsed = parse_bpel(bpel_path)
    return map_bpel_to_conductor(parsed)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pattern_mapper.py <file.bpel> [output.json]")
        sys.exit(1)

    bundle = map_bpel_file_to_conductor(Path(sys.argv[1]))

    if len(sys.argv) >= 3:
        out = Path(sys.argv[2])
        out.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        print(f"Written to {out}")
    else:
        print(json.dumps(bundle, indent=2))
