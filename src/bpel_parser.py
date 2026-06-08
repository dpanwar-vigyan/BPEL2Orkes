"""
BPEL Parser — reads IBM BPEL XML (WS-BPEL 2.0 + IBM BPELX extensions)
and extracts processes, activities, and handlers into structured JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lxml import etree

# ── Namespace map ──────────────────────────────────────────────────────────────
BPEL_NS = "http://docs.oasis-open.org/wsbpel/2.0/process/executable"
BPELX_NS = "http://www.ibm.com/xmlns/prod/websphere/business-process/6.0.0"

NS = {
    "bpel": BPEL_NS,
    "bpelx": BPELX_NS,
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _localname(tag: str) -> str:
    """Strip namespace URI from a Clark-notation tag."""
    return re.sub(r"^\{[^}]*\}", "", tag)


def _attrs(el: etree._Element) -> dict[str, str]:
    """Return element attributes with namespace URIs stripped from keys."""
    return {_localname(k): v for k, v in el.attrib.items()}


def _qname_to_str(qname: str | None) -> str | None:
    """Normalise a prefixed QName to a plain string (prefix kept for clarity)."""
    return qname.strip() if qname else None


# ── Activity parsers ───────────────────────────────────────────────────────────

def _parse_invoke(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    node: dict[str, Any] = {
        "type": "invoke",
        "name": a.get("name"),
        "partnerLink": a.get("partnerLink"),
        "operation": a.get("operation"),
        "portType": _qname_to_str(a.get("portType")),
        "inputVariable": a.get("inputVariable"),
        "outputVariable": a.get("outputVariable"),
    }
    comp = el.find("bpel:compensationHandler", NS)
    if comp is not None:
        node["compensationHandler"] = _parse_compensation_handler(comp)
    fault = el.find("bpel:catchAll", NS) or el.find("bpel:catch", NS)
    if fault is not None:
        node["faultHandler"] = _parse_fault_handlers(el)
    return node


def _parse_receive(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    return {
        "type": "receive",
        "name": a.get("name"),
        "partnerLink": a.get("partnerLink"),
        "operation": a.get("operation"),
        "portType": _qname_to_str(a.get("portType")),
        "variable": a.get("variable"),
        "createInstance": a.get("createInstance", "no"),
    }


def _parse_reply(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    return {
        "type": "reply",
        "name": a.get("name"),
        "partnerLink": a.get("partnerLink"),
        "operation": a.get("operation"),
        "portType": _qname_to_str(a.get("portType")),
        "variable": a.get("variable"),
        "faultName": _qname_to_str(a.get("faultName")),
    }


def _parse_assign(el: etree._Element) -> dict[str, Any]:
    copies = []
    for copy in el.findall("bpel:copy", NS):
        frm = copy.find("bpel:from", NS)
        to = copy.find("bpel:to", NS)
        copies.append({
            "from": _attrs(frm) if frm is not None else {},
            "to": _attrs(to) if to is not None else {},
        })
    return {
        "type": "assign",
        "name": _attrs(el).get("name"),
        "copies": copies,
    }


def _parse_throw(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    return {
        "type": "throw",
        "name": a.get("name"),
        "faultName": _qname_to_str(a.get("faultName")),
        "faultVariable": a.get("faultVariable"),
    }


def _parse_rethrow(el: etree._Element) -> dict[str, Any]:
    return {"type": "rethrow", "name": _attrs(el).get("name")}


def _parse_wait(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    for_ = el.find("bpel:for", NS)
    until = el.find("bpel:until", NS)
    return {
        "type": "wait",
        "name": a.get("name"),
        "for": for_.text.strip() if for_ is not None and for_.text else None,
        "until": until.text.strip() if until is not None and until.text else None,
    }


def _parse_empty(el: etree._Element) -> dict[str, Any]:
    return {"type": "empty", "name": _attrs(el).get("name")}


def _parse_exit(el: etree._Element) -> dict[str, Any]:
    return {"type": "exit", "name": _attrs(el).get("name")}


def _parse_scope(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    node: dict[str, Any] = {
        "type": "scope",
        "name": a.get("name"),
        "activities": _parse_activity_list(el),
    }
    fh = el.find("bpel:faultHandlers", NS)
    if fh is not None:
        node["faultHandlers"] = _parse_fault_handlers(fh)
    ch = el.find("bpel:compensationHandler", NS)
    if ch is not None:
        node["compensationHandler"] = _parse_compensation_handler(ch)
    eh = el.find("bpel:eventHandlers", NS)
    if eh is not None:
        node["eventHandlers"] = _parse_event_handlers(eh)
    return node


def _parse_flow(el: etree._Element) -> dict[str, Any]:
    links = [_attrs(l) for l in el.findall("bpel:links/bpel:link", NS)]
    return {
        "type": "flow",
        "name": _attrs(el).get("name"),
        "links": links,
        "activities": _parse_activity_list(el),
    }


def _parse_switch_if(el: etree._Element) -> dict[str, Any]:
    """Handles both <switch> (BPEL 1.1) and <if> (BPEL 2.0)."""
    tag = _localname(el.tag)
    branches = []

    if tag == "if":
        cond = el.find("bpel:condition", NS)
        branches.append({
            "condition": cond.text.strip() if cond is not None and cond.text else None,
            "activities": _parse_activity_list(el),
        })
        for elif_el in el.findall("bpel:elseif", NS):
            ec = elif_el.find("bpel:condition", NS)
            branches.append({
                "condition": ec.text.strip() if ec is not None and ec.text else None,
                "activities": _parse_activity_list(elif_el),
            })
        else_el = el.find("bpel:else", NS)
        if else_el is not None:
            branches.append({"condition": None, "activities": _parse_activity_list(else_el)})
    else:  # legacy <switch>
        for case in el.findall("bpel:case", NS):
            branches.append({
                "condition": case.get("condition"),
                "activities": _parse_activity_list(case),
            })
        otherwise = el.find("bpel:otherwise", NS)
        if otherwise is not None:
            branches.append({"condition": None, "activities": _parse_activity_list(otherwise)})

    return {"type": "if", "name": _attrs(el).get("name"), "branches": branches}


def _parse_while(el: etree._Element) -> dict[str, Any]:
    cond = el.find("bpel:condition", NS)
    return {
        "type": "while",
        "name": _attrs(el).get("name"),
        "condition": cond.text.strip() if cond is not None and cond.text else None,
        "activities": _parse_activity_list(el),
    }


def _parse_repeat_until(el: etree._Element) -> dict[str, Any]:
    cond = el.find("bpel:condition", NS)
    return {
        "type": "repeatUntil",
        "name": _attrs(el).get("name"),
        "condition": cond.text.strip() if cond is not None and cond.text else None,
        "activities": _parse_activity_list(el),
    }


def _parse_foreach(el: etree._Element) -> dict[str, Any]:
    a = _attrs(el)
    start = el.find("bpel:startCounterValue", NS)
    final = el.find("bpel:finalCounterValue", NS)
    return {
        "type": "forEach",
        "name": a.get("name"),
        "counterName": a.get("counterName"),
        "parallel": a.get("parallel", "no"),
        "startCounterValue": start.text.strip() if start is not None and start.text else None,
        "finalCounterValue": final.text.strip() if final is not None and final.text else None,
        "activities": _parse_activity_list(el),
    }


def _parse_pick(el: etree._Element) -> dict[str, Any]:
    branches = []
    for ob in el.findall("bpel:onMessage", NS):
        branches.append({
            "trigger": "onMessage",
            **_attrs(ob),
            "activities": _parse_activity_list(ob),
        })
    for oa in el.findall("bpel:onAlarm", NS):
        for_ = oa.find("bpel:for", NS)
        until = oa.find("bpel:until", NS)
        branches.append({
            "trigger": "onAlarm",
            "for": for_.text.strip() if for_ is not None and for_.text else None,
            "until": until.text.strip() if until is not None and until.text else None,
            "activities": _parse_activity_list(oa),
        })
    return {
        "type": "pick",
        "name": _attrs(el).get("name"),
        "createInstance": _attrs(el).get("createInstance", "no"),
        "branches": branches,
    }


def _parse_ibm_extension(el: etree._Element) -> dict[str, Any]:
    """Capture IBM BPELX extension activities verbatim."""
    return {
        "type": "ibmExtension",
        "tag": _localname(el.tag),
        "namespace": BPELX_NS,
        "attributes": _attrs(el),
        "rawXml": etree.tostring(el, encoding="unicode"),
    }


# ── Activity dispatch ──────────────────────────────────────────────────────────

_ACTIVITY_TAGS = {
    "sequence": None,   # handled inline to avoid mutual recursion import issue
    "invoke": _parse_invoke,
    "receive": _parse_receive,
    "reply": _parse_reply,
    "assign": _parse_assign,
    "throw": _parse_throw,
    "rethrow": _parse_rethrow,
    "wait": _parse_wait,
    "empty": _parse_empty,
    "exit": _parse_exit,
    "scope": _parse_scope,
    "flow": _parse_flow,
    "if": _parse_switch_if,
    "switch": _parse_switch_if,
    "while": _parse_while,
    "repeatUntil": _parse_repeat_until,
    "forEach": _parse_foreach,
    "pick": _parse_pick,
}


def _parse_activity(el: etree._Element) -> dict[str, Any] | None:
    tag = _localname(el.tag)
    ns = re.match(r"^\{([^}]*)\}", el.tag)
    ns_uri = ns.group(1) if ns else ""

    if ns_uri == BPELX_NS:
        return _parse_ibm_extension(el)

    if tag == "sequence":
        return _parse_sequence(el)

    handler = _ACTIVITY_TAGS.get(tag)
    if handler:
        return handler(el)

    return None  # structural elements (variables, links, etc.) are skipped


def _parse_activity_list(parent: etree._Element) -> list[dict[str, Any]]:
    activities = []
    for child in parent:
        if not isinstance(child.tag, str):  # skip comments, PIs
            continue
        parsed = _parse_activity(child)
        if parsed is not None:
            activities.append(parsed)
    return activities


def _parse_sequence(el: etree._Element) -> dict[str, Any]:
    return {
        "type": "sequence",
        "name": _attrs(el).get("name"),
        "activities": _parse_activity_list(el),
    }


# ── Handler parsers ────────────────────────────────────────────────────────────

def _parse_fault_handlers(fh_el: etree._Element) -> dict[str, Any]:
    catches = []
    for catch in fh_el.findall("bpel:catch", NS):
        a = _attrs(catch)
        catches.append({
            "faultName": _qname_to_str(a.get("faultName")),
            "faultVariable": a.get("faultVariable"),
            "faultMessageType": _qname_to_str(a.get("faultMessageType")),
            "activities": _parse_activity_list(catch),
        })
    catch_all = fh_el.find("bpel:catchAll", NS)
    return {
        "catches": catches,
        "catchAll": {"activities": _parse_activity_list(catch_all)} if catch_all is not None else None,
    }


def _parse_compensation_handler(ch_el: etree._Element) -> dict[str, Any]:
    return {"activities": _parse_activity_list(ch_el)}


def _parse_event_handlers(eh_el: etree._Element) -> dict[str, Any]:
    on_message = []
    for om in eh_el.findall("bpel:onEvent", NS):
        a = _attrs(om)
        on_message.append({**a, "activities": _parse_activity_list(om)})
    on_alarm = []
    for oa in eh_el.findall("bpel:onAlarm", NS):
        for_ = oa.find("bpel:for", NS)
        until = oa.find("bpel:until", NS)
        on_alarm.append({
            "for": for_.text.strip() if for_ is not None and for_.text else None,
            "until": until.text.strip() if until is not None and until.text else None,
            "activities": _parse_activity_list(oa),
        })
    return {"onEvent": on_message, "onAlarm": on_alarm}


# ── Top-level process ──────────────────────────────────────────────────────────

def _parse_variables(process: etree._Element) -> list[dict[str, Any]]:
    variables = []
    for v in process.findall("bpel:variables/bpel:variable", NS):
        a = _attrs(v)
        variables.append({
            "name": a.get("name"),
            "messageType": _qname_to_str(a.get("messageType")),
            "type": _qname_to_str(a.get("type")),
            "element": _qname_to_str(a.get("element")),
        })
    return variables


def _parse_partner_links(process: etree._Element) -> list[dict[str, Any]]:
    links = []
    for pl in process.findall("bpel:partnerLinks/bpel:partnerLink", NS):
        a = _attrs(pl)
        links.append({
            "name": a.get("name"),
            "partnerLinkType": _qname_to_str(a.get("partnerLinkType")),
            "myRole": a.get("myRole"),
            "partnerRole": a.get("partnerRole"),
        })
    return links


def _parse_correlation_sets(process: etree._Element) -> list[dict[str, Any]]:
    sets = []
    for cs in process.findall("bpel:correlationSets/bpel:correlationSet", NS):
        a = _attrs(cs)
        sets.append({"name": a.get("name"), "properties": a.get("properties")})
    return sets


def _parse_process(process: etree._Element) -> dict[str, Any]:
    a = _attrs(process)
    result: dict[str, Any] = {
        "name": a.get("name"),
        "targetNamespace": a.get("targetNamespace"),
        "queryLanguage": a.get("queryLanguage"),
        "expressionLanguage": a.get("expressionLanguage"),
        "suppressJoinFailure": a.get("suppressJoinFailure", "no"),
        "partnerLinks": _parse_partner_links(process),
        "variables": _parse_variables(process),
        "correlationSets": _parse_correlation_sets(process),
    }

    fh = process.find("bpel:faultHandlers", NS)
    if fh is not None:
        result["faultHandlers"] = _parse_fault_handlers(fh)

    ch = process.find("bpel:compensationHandler", NS)
    if ch is not None:
        result["compensationHandler"] = _parse_compensation_handler(ch)

    eh = process.find("bpel:eventHandlers", NS)
    if eh is not None:
        result["eventHandlers"] = _parse_event_handlers(eh)

    # The process body is the first structural activity child
    body_tags = {"sequence", "flow", "scope", "if", "switch", "while",
                 "repeatUntil", "forEach", "pick", "invoke", "receive",
                 "reply", "assign", "throw", "wait", "empty", "exit"}
    for child in process:
        if _localname(child.tag) in body_tags:
            result["activity"] = _parse_activity(child)
            break

    return result


# ── Public API ─────────────────────────────────────────────────────────────────

class BPELParseError(Exception):
    pass


def parse_bpel(source: str | Path | bytes) -> dict[str, Any]:
    """
    Parse a BPEL document and return a structured dict.

    Args:
        source: file path (str or Path), raw XML bytes, or XML string.

    Returns:
        {"version": "2.0", "process": {...}}

    Raises:
        BPELParseError: on XML parse failure or missing <process> root.
    """
    try:
        if isinstance(source, (str, Path)) and not str(source).strip().startswith("<"):
            tree = etree.parse(str(source))
            root = tree.getroot()
        else:
            xml_bytes = source.encode() if isinstance(source, str) else source
            root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise BPELParseError(f"XML parse error: {exc}") from exc

    local = _localname(root.tag)
    if local != "process":
        raise BPELParseError(f"Expected root element <process>, got <{local}>")

    return {
        "version": "2.0",
        "process": _parse_process(root),
    }


def parse_bpel_to_json(source: str | Path | bytes, indent: int = 2) -> str:
    """Parse BPEL and return pretty-printed JSON string."""
    return json.dumps(parse_bpel(source), indent=indent, ensure_ascii=False)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python bpel_parser.py <file.bpel> [output.json]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    result_json = parse_bpel_to_json(input_path)

    if len(sys.argv) >= 3:
        out = Path(sys.argv[2])
        out.write_text(result_json, encoding="utf-8")
        print(f"Written to {out}")
    else:
        print(result_json)
