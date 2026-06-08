"""Tests for BPEL parser."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from bpel_parser import BPELParseError, parse_bpel

SAMPLE = Path(__file__).parent.parent / "samples" / "loan_approval.bpel"


@pytest.fixture(scope="module")
def loan():
    return parse_bpel(SAMPLE)


# ── Top-level structure ────────────────────────────────────────────────────────

def test_version(loan):
    assert loan["version"] == "2.0"


def test_process_name(loan):
    assert loan["process"]["name"] == "LoanApprovalProcess"


def test_target_namespace(loan):
    assert loan["process"]["targetNamespace"] == "http://example.com/loan"


def test_suppress_join_failure(loan):
    assert loan["process"]["suppressJoinFailure"] == "yes"


# ── Partner links ──────────────────────────────────────────────────────────────

def test_partner_links_count(loan):
    assert len(loan["process"]["partnerLinks"]) == 3


def test_partner_link_client(loan):
    client = next(pl for pl in loan["process"]["partnerLinks"] if pl["name"] == "client")
    assert client["myRole"] == "approver"
    assert client["partnerRole"] is None


def test_partner_link_credit_service(loan):
    cs = next(pl for pl in loan["process"]["partnerLinks"] if pl["name"] == "creditService")
    assert cs["partnerRole"] == "creditChecker"


# ── Variables ─────────────────────────────────────────────────────────────────

def test_variables_count(loan):
    assert len(loan["process"]["variables"]) == 5


def test_variable_names(loan):
    names = {v["name"] for v in loan["process"]["variables"]}
    assert names == {"loanRequest", "loanResponse", "creditScore", "riskResult", "faultDetail"}


# ── Correlation sets ───────────────────────────────────────────────────────────

def test_correlation_sets(loan):
    assert loan["process"]["correlationSets"][0]["name"] == "loanCorrelation"


# ── Process-level fault handlers ──────────────────────────────────────────────

def test_process_fault_handlers_present(loan):
    assert "faultHandlers" in loan["process"]


def test_process_catch_fault_name(loan):
    fh = loan["process"]["faultHandlers"]
    assert fh["catches"][0]["faultName"] == "tns:CreditCheckFault"


def test_process_catch_all_present(loan):
    fh = loan["process"]["faultHandlers"]
    assert fh["catchAll"] is not None


def test_process_catch_all_has_throw(loan):
    acts = loan["process"]["faultHandlers"]["catchAll"]["activities"]
    # catchAll wraps a sequence
    inner = acts[0]["activities"]
    assert inner[0]["type"] == "throw"
    assert inner[0]["faultName"] == "tns:UnexpectedFault"


# ── Process-level compensation handler ────────────────────────────────────────

def test_process_compensation_handler(loan):
    ch = loan["process"]["compensationHandler"]
    invoke = ch["activities"][0]["activities"][0]
    assert invoke["type"] == "invoke"
    assert invoke["name"] == "RollbackCredit"


# ── Main sequence activity ─────────────────────────────────────────────────────

def test_main_activity_is_sequence(loan):
    assert loan["process"]["activity"]["type"] == "sequence"
    assert loan["process"]["activity"]["name"] == "MainFlow"


def _main_activities(loan):
    return loan["process"]["activity"]["activities"]


def test_receive_first(loan):
    acts = _main_activities(loan)
    rcv = acts[0]
    assert rcv["type"] == "receive"
    assert rcv["name"] == "ReceiveLoanRequest"
    assert rcv["createInstance"] == "yes"
    assert rcv["operation"] == "approveLoan"


# ── IBM extension ──────────────────────────────────────────────────────────────

def test_ibm_extension_captured(loan):
    acts = _main_activities(loan)
    ext = acts[1]
    assert ext["type"] == "ibmExtension"
    assert ext["tag"] == "task"
    assert ext["namespace"] == "http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0"
    assert ext["attributes"]["taskType"] == "HUMAN_TASK"


# ── Parallel flow ──────────────────────────────────────────────────────────────

def test_flow_type(loan):
    acts = _main_activities(loan)
    flow = acts[2]
    assert flow["type"] == "flow"
    assert flow["name"] == "ParallelChecks"


def test_flow_links(loan):
    flow = _main_activities(loan)[2]
    link_names = {l["name"] for l in flow["links"]}
    assert "creditToDone" in link_names
    assert "riskToDone" in link_names


def test_flow_invokes(loan):
    flow = _main_activities(loan)[2]
    invoke_names = {a["name"] for a in flow["activities"]}
    assert "CheckCredit" in invoke_names
    assert "AssessRisk" in invoke_names


# ── If/elseif/else ─────────────────────────────────────────────────────────────

def test_if_type(loan):
    acts = _main_activities(loan)
    if_act = acts[3]
    assert if_act["type"] == "if"
    assert if_act["name"] == "CreditDecision"


def test_if_branches_count(loan):
    if_act = _main_activities(loan)[3]
    assert len(if_act["branches"]) == 3


def test_if_condition(loan):
    branch = _main_activities(loan)[3]["branches"][0]
    assert "650" in branch["condition"]


def test_else_branch_has_no_condition(loan):
    else_branch = _main_activities(loan)[3]["branches"][2]
    assert else_branch["condition"] is None


# ── Scope with nested handlers ─────────────────────────────────────────────────

def test_scope_type(loan):
    scope = _main_activities(loan)[4]
    assert scope["type"] == "scope"
    assert scope["name"] == "NotificationScope"


def test_scope_fault_handler(loan):
    scope = _main_activities(loan)[4]
    assert scope["faultHandlers"]["catchAll"] is not None


def test_scope_compensation_handler(loan):
    scope = _main_activities(loan)[4]
    ch = scope["compensationHandler"]
    assert ch["activities"][0]["name"] == "CancelNotification"


def test_scope_invoke(loan):
    scope = _main_activities(loan)[4]
    invoke = scope["activities"][0]
    assert invoke["type"] == "invoke"
    assert invoke["name"] == "SendNotification"


# ── Wait ──────────────────────────────────────────────────────────────────────

def test_wait(loan):
    wait = _main_activities(loan)[5]
    assert wait["type"] == "wait"
    assert wait["for"] == "'PT24H'"


# ── Reply ─────────────────────────────────────────────────────────────────────

def test_reply_last(loan):
    acts = _main_activities(loan)
    reply = acts[-1]
    assert reply["type"] == "reply"
    assert reply["name"] == "ReplyToClient"
    assert reply["operation"] == "approveLoan"


# ── Assign copies ─────────────────────────────────────────────────────────────

def test_assign_copies(loan):
    # Inside the if-approved branch
    if_branch = _main_activities(loan)[3]["branches"][0]
    assign = if_branch["activities"][0]["activities"][0]
    assert assign["type"] == "assign"
    assert len(assign["copies"]) == 1
    assert assign["copies"][0]["to"]["variable"] == "loanResponse"


# ── Error handling ─────────────────────────────────────────────────────────────

def test_bad_xml_raises():
    with pytest.raises(BPELParseError, match="XML parse error"):
        parse_bpel(b"<not valid xml")


def test_wrong_root_raises():
    with pytest.raises(BPELParseError, match="Expected root element"):
        parse_bpel(b"<definitions/>")


# ── Round-trip JSON serialisability ───────────────────────────────────────────

def test_json_serialisable(loan):
    dumped = json.dumps(loan)
    reloaded = json.loads(dumped)
    assert reloaded["process"]["name"] == "LoanApprovalProcess"
