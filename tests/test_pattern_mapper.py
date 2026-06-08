"""Tests for BPEL → Conductor pattern mapper."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from bpel_parser import parse_bpel
from pattern_mapper import map_activity, map_bpel_to_conductor

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture(scope="module")
def income_bundle():
    return map_bpel_to_conductor(parse_bpel(SAMPLES / "income_verification.bpel"))


@pytest.fixture(scope="module")
def comms_bundle():
    return map_bpel_to_conductor(parse_bpel(SAMPLES / "communications_orchestration.bpel"))


@pytest.fixture(scope="module")
def card_bundle():
    return map_bpel_to_conductor(parse_bpel(SAMPLES / "credit_card_provisioning.bpel"))


# ── Bundle structure ───────────────────────────────────────────────────────────

def test_bundle_has_required_keys(income_bundle):
    for key in ("mainWorkflow", "subWorkflows", "compensationFlows", "faultHandlerFlows", "warnings"):
        assert key in income_bundle


def test_main_workflow_has_tasks(income_bundle):
    assert len(income_bundle["mainWorkflow"]["tasks"]) > 0


def test_main_workflow_name(income_bundle):
    assert income_bundle["mainWorkflow"]["name"] == "IncomeVerificationProcess"


def test_json_serialisable(income_bundle, comms_bundle, card_bundle):
    for bundle in (income_bundle, comms_bundle, card_bundle):
        dumped = json.dumps(bundle)
        assert json.loads(dumped)["mainWorkflow"]["tasks"]


# ── receive → WAIT ────────────────────────────────────────────────────────────

def test_receive_maps_to_wait():
    tasks = map_activity({"type": "receive", "name": "ReceiveApp",
                          "partnerLink": "client", "operation": "apply",
                          "variable": "req", "createInstance": "yes"})
    assert len(tasks) == 1
    assert tasks[0]["type"] == "WAIT"
    assert tasks[0]["inputParameters"]["operation"] == "apply"


# ── invoke → SIMPLE ───────────────────────────────────────────────────────────

def test_invoke_maps_to_simple():
    tasks = map_activity({"type": "invoke", "name": "CallCredit",
                          "partnerLink": "creditService", "operation": "check",
                          "inputVariable": "req", "outputVariable": "resp"})
    assert tasks[0]["type"] == "SIMPLE"
    assert tasks[0]["inputParameters"]["operation"] == "check"


# ── assign → SET_VARIABLE ─────────────────────────────────────────────────────

def test_assign_maps_to_set_variable():
    tasks = map_activity({
        "type": "assign", "name": "SetStatus",
        "copies": [{"from": {"expression": "'APPROVED'"}, "to": {"variable": "resp", "part": "status"}}],
    })
    assert tasks[0]["type"] == "SET_VARIABLE"
    assert "copy_0_from" in tasks[0]["inputParameters"]


# ── throw → TERMINATE ─────────────────────────────────────────────────────────

def test_throw_maps_to_terminate():
    tasks = map_activity({"type": "throw", "name": "ThrowFault",
                          "faultName": "tns:MyFault"})
    assert tasks[0]["type"] == "TERMINATE"
    assert tasks[0]["inputParameters"]["terminationStatus"] == "FAILED"
    assert "MyFault" in tasks[0]["inputParameters"]["workflowOutput"]["faultName"]


# ── flow → FORK_JOIN + JOIN ───────────────────────────────────────────────────

def test_flow_maps_to_fork_join():
    tasks = map_activity({
        "type": "flow", "name": "ParallelWork",
        "links": [],
        "activities": [
            {"type": "invoke", "name": "TaskA", "partnerLink": "svcA", "operation": "doA"},
            {"type": "invoke", "name": "TaskB", "partnerLink": "svcB", "operation": "doB"},
        ],
    })
    types = [t["type"] for t in tasks]
    assert "FORK_JOIN" in types
    assert "JOIN" in types


def test_fork_has_two_branches():
    tasks = map_activity({
        "type": "flow", "name": "ParallelBureaus",
        "links": [],
        "activities": [
            {"type": "invoke", "name": "A", "partnerLink": "p1", "operation": "op1"},
            {"type": "invoke", "name": "B", "partnerLink": "p2", "operation": "op2"},
        ],
    })
    fork = next(t for t in tasks if t["type"] == "FORK_JOIN")
    assert len(fork["forkTasks"]) == 2


# ── if → SWITCH ───────────────────────────────────────────────────────────────

def test_if_maps_to_switch():
    tasks = map_activity({
        "type": "if", "name": "Decision",
        "branches": [
            {"condition": "$score > 650", "activities": [
                {"type": "assign", "name": "SetApproved", "copies": []}
            ]},
            {"condition": None, "activities": [
                {"type": "assign", "name": "SetDenied", "copies": []}
            ]},
        ],
    })
    assert tasks[0]["type"] == "SWITCH"
    assert len(tasks[0]["decisionCases"]) == 1   # 1 condition branch
    assert tasks[0]["defaultCase"]               # else branch


# ── while → DO_WHILE ─────────────────────────────────────────────────────────

def test_while_maps_to_do_while():
    tasks = map_activity({
        "type": "while", "name": "Loop",
        "condition": "$count < 5",
        "activities": [{"type": "empty", "name": "step"}],
    })
    assert tasks[0]["type"] == "DO_WHILE"
    assert "count" in tasks[0]["loopCondition"]


# ── scope → SUB_WORKFLOW ─────────────────────────────────────────────────────

def test_scope_maps_to_sub_workflow():
    tasks = map_activity({
        "type": "scope", "name": "NotifScope",
        "activities": [{"type": "invoke", "name": "Notify", "partnerLink": "p", "operation": "send"}],
        "compensationHandler": {"activities": [
            {"type": "invoke", "name": "Cancel", "partnerLink": "p", "operation": "cancel"}
        ]},
    })
    assert tasks[0]["type"] == "SUB_WORKFLOW"
    assert tasks[0]["subWorkflowParam"]["name"] == "sub_NotifScope"


def test_scope_compensation_extracted(income_bundle):
    assert len(income_bundle["compensationFlows"]) > 0


# ── IBM bpelx:task → HUMAN ───────────────────────────────────────────────────

def test_ibm_task_maps_to_human():
    tasks = map_activity({
        "type": "ibmExtension", "tag": "task",
        "namespace": "http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0",
        "attributes": {
            "name": "AnalystReview", "taskType": "HUMAN_TASK",
            "staffQuery": "role:LendingAnalyst", "priority": "2",
        },
        "rawXml": "<bpelx:task/>",
    })
    assert tasks[0]["type"] == "HUMAN"
    assert tasks[0]["inputParameters"]["staffQuery"] == "role:LendingAnalyst"


def test_income_has_human_task(income_bundle):
    def find_human(tasks):
        for t in tasks:
            if t.get("type") == "HUMAN":
                return True
            for branch in t.get("forkTasks", []):
                if find_human(branch):
                    return True
            for case in t.get("decisionCases", {}).values():
                if find_human(case):
                    return True
            if find_human(t.get("defaultCase", [])):
                return True
            if find_human(t.get("loopOver", [])):
                return True
        return False
    assert find_human(income_bundle["mainWorkflow"]["tasks"])


# ── IBM bpelx:callBusinessRule → HTTP ────────────────────────────────────────

def test_ibm_rule_maps_to_http():
    tasks = map_activity({
        "type": "ibmExtension", "tag": "callBusinessRule",
        "namespace": "http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0",
        "attributes": {
            "name": "EvalIncome",
            "ruleAppName": "LendingApp",
            "ruleSetPath": "/income/v2/IncomeRuleset",
            "inputVariable": "ruleReq",
            "outputVariable": "ruleResp",
        },
        "rawXml": "<bpelx:callBusinessRule/>",
    })
    assert tasks[0]["type"] == "HTTP"
    assert "odmBaseUrl" in tasks[0]["inputParameters"]["http_request"]["uri"]


# ── pick → WAIT ───────────────────────────────────────────────────────────────

def test_pick_maps_to_wait():
    tasks = map_activity({
        "type": "pick", "name": "WaitDelivery",
        "createInstance": "no",
        "branches": [
            {
                "trigger": "onMessage", "partnerLink": "sms", "operation": "callback",
                "variable": "cb",
                "activities": [{"type": "assign", "name": "Mark", "copies": []}],
            },
            {
                "trigger": "onAlarm", "for": "'PT10M'",
                "activities": [{"type": "throw", "name": "Timeout", "faultName": "tns:Timeout"}],
            },
        ],
    })
    assert tasks[0]["type"] == "WAIT"
    assert tasks[0]["inputParameters"]["duration"] == "'PT10M'"


# ── wait → WAIT ───────────────────────────────────────────────────────────────

def test_wait_maps_to_wait_task():
    tasks = map_activity({"type": "wait", "name": "Wait24h", "for": "'PT24H'"})
    assert tasks[0]["type"] == "WAIT"
    assert tasks[0]["inputParameters"]["duration"] == "'PT24H'"


# ── reply → SIMPLE ───────────────────────────────────────────────────────────

def test_reply_maps_to_simple():
    tasks = map_activity({"type": "reply", "name": "Reply",
                          "partnerLink": "client", "operation": "approve",
                          "variable": "resp"})
    assert tasks[0]["type"] == "SIMPLE"


# ── fault handlers → failure workflow ────────────────────────────────────────

def test_income_has_fault_handler_workflow(income_bundle):
    assert len(income_bundle["faultHandlerFlows"]) > 0
    fh = income_bundle["faultHandlerFlows"][0]
    assert "tasks" in fh
    # Should contain a SWITCH to route by fault name
    assert any(t["type"] == "SWITCH" for t in fh["tasks"])


# ── input parameters derived from partner links ──────────────────────────────

def test_input_params_include_partner_links(income_bundle):
    params = income_bundle["mainWorkflow"]["inputParameters"]
    assert any("Endpoint" in p for p in params)


# ── warnings surface unknown extensions ──────────────────────────────────────

def test_unknown_ibm_extension_generates_warning():
    tasks = map_activity({
        "type": "ibmExtension", "tag": "unknownWidget",
        "namespace": "http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0",
        "attributes": {"name": "Widget"},
        "rawXml": "<bpelx:unknownWidget/>",
    })
    assert "_warning" in tasks[0]
