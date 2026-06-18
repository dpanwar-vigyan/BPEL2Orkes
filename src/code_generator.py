"""
Code Generator — Stage 3 of the BPEL2Orkes pipeline.

Takes the raw bundle produced by pattern_mapper and:
  1. Strips internal diagnostic fields (_bpelSource, _warning, _metadata)
  2. Translates simple XPath literals in SWITCH conditions to JS expressions
  3. Returns a clean, deployable Conductor workflow bundle

The output bundle is ready to POST to the Orkes API.
"""

from __future__ import annotations

import copy
import re
from typing import Any


# ── Internal field names produced by pattern_mapper ───────────────────────────

_INTERNAL_FIELDS = {"_bpelSource", "_warning", "_metadata", "_bpelName"}


# ── XPath → JS condition translator ───────────────────────────────────────────
# Applied to SWITCH expressions, loop conditions, and inline branch conditions.

_XPATH_REPLACEMENTS = [
    # bpel:getVariableData('var','part','/xpath') and getVariableData(...)
    (re.compile(r"(?:bpel:)?getVariableData\('(\w+)'[^)]*\)"), r"$.\1"),

    # ── Slash-notation: $var/part/element ─────────────────────────────────────
    (re.compile(r"\$(\w+)/(\w+)/(\w+)\s*!=\s*'([^']*)'"), r"$.\1.\2.\3 !== '\4'"),
    (re.compile(r"\$(\w+)/(\w+)/(\w+)\s*=\s*'([^']*)'"),  r"$.\1.\2.\3 === '\4'"),
    (re.compile(r"\$(\w+)/(\w+)\s*!=\s*'([^']*)'"),        r"$.\1.\2 !== '\3'"),
    (re.compile(r"\$(\w+)/(\w+)\s*=\s*'([^']*)'"),         r"$.\1.\2 === '\3'"),
    (re.compile(r"\$(\w+)/(\w+)"),                          r"$.\1.\2"),

    # ── Dot-notation: $var.prop ────────────────────────────────────────────────
    (re.compile(r"\$(\w+)\.(\w+)\s*!=\s*'([^']*)'"),       r"$.\1.\2 !== '\3'"),
    (re.compile(r"\$(\w+)\.(\w+)\s*=\s*'([^']*)'"),        r"$.\1.\2 === '\3'"),
    (re.compile(r"\$(\w+)\.(\w+)\s*!=\s*(\d+(?:\.\d+)?)"), r"$.\1.\2 !== \3"),
    (re.compile(r"\$(\w+)\.(\w+)\s*=\s*(\d+(?:\.\d+)?)"),  r"$.\1.\2 === \3"),
    (re.compile(r"\$(\w+)\.(\w+)"),                         r"$.\1.\2"),

    # ── Simple $var patterns ───────────────────────────────────────────────────
    # $var = true() / false() — before generic true()/false() replacement
    (re.compile(r"\$(\w+)\s*=\s*true\(\)"),  r"$.\1 === true"),
    (re.compile(r"\$(\w+)\s*=\s*false\(\)"), r"$.\1 === false"),
    (re.compile(r"\$(\w+)\s*!=\s*'([^']*)'"),       r"$.\1 !== '\2'"),
    (re.compile(r"\$(\w+)\s*=\s*'([^']*)'"),        r"$.\1 === '\2'"),
    (re.compile(r"\$(\w+)\s*!=\s*(\d+(?:\.\d+)?)"), r"$.\1 !== \2"),
    (re.compile(r"\$(\w+)\s*=\s*(\d+(?:\.\d+)?)"),  r"$.\1 === \2"),
    (re.compile(r"\$(\w+)"),                         r"$.\1"),

    # ── XPath functions ────────────────────────────────────────────────────────
    (re.compile(r"string-length\(\$\.?(\w+(?:\.\w+)*)\)"),     r"$.\1.length"),
    (re.compile(r"starts-with\(\$\.?(\w+(?:\.\w+)*),\s*'([^']*)'\)"), r"$.\1.startsWith('\2')"),
    (re.compile(r"contains\(\$\.?(\w+(?:\.\w+)*),\s*'([^']*)'\)"),    r"$.\1.includes('\2')"),
    (re.compile(r"\btrue\(\)"),  "true"),
    (re.compile(r"\bfalse\(\)"), "false"),
    (re.compile(r"\bnot\(([^)]+)\)"), r"!(\1)"),

    # ── Logical operators ──────────────────────────────────────────────────────
    (re.compile(r"\s+and\s+"), " && "),
    (re.compile(r"\s+or\s+"),  " || "),
]


def _translate_condition(expr: str) -> str:
    """Best-effort XPath → JS translation for Orkes SWITCH/DO_WHILE evaluators."""
    result = expr
    for pattern, repl in _XPATH_REPLACEMENTS:
        result = pattern.sub(repl, result)
    return result


def _build_switch_js(task: dict) -> dict:
    """
    Rebuild the SWITCH task `expression` as a proper JS function before
    _bpelSource is stripped. Reads conditions and branchKeys from _bpelSource.
    """
    bpel_src = task.get("_bpelSource", {})
    conditions = bpel_src.get("conditions", [])
    branch_keys = bpel_src.get("branchKeys", [])

    key_iter = iter(branch_keys)
    branches: list[tuple[str, str]] = []
    for cond in conditions:
        if cond is not None:
            try:
                key = next(key_iter)
                branches.append((cond, key))
            except StopIteration:
                break

    if not branches:
        return task

    lines = ["function execute() {"]
    for cond, key in branches:
        js_cond = _translate_condition(cond)
        lines.append(f'  if ({js_cond}) return "{key}";')
    lines.append('  return "default";')
    lines.append("}")

    result = dict(task)
    result["expression"] = "\n".join(lines)
    return result


# ── Recursive field stripper ───────────────────────────────────────────────────

def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        # Rebuild SWITCH expression before stripping _bpelSource
        if obj.get("type") == "SWITCH" and "_bpelSource" in obj:
            obj = _build_switch_js(obj)
        cleaned = {}
        for k, v in obj.items():
            if k in _INTERNAL_FIELDS:
                continue
            if k == "expression" and isinstance(v, str):
                cleaned[k] = _translate_condition(v)
            elif k == "loopCondition" and isinstance(v, str):
                cleaned[k] = _translate_condition(v)
            else:
                cleaned[k] = _clean(v)
        return cleaned
    if isinstance(obj, list):
        return [_clean(item) for item in obj]
    return obj


# ── Public API ─────────────────────────────────────────────────────────────────

def generate(bundle: dict) -> dict:
    """
    Clean a pattern_mapper bundle and return a deployable Conductor bundle.

    Input:  raw bundle from map_bpel_to_conductor()
    Output: {
        "mainWorkflow":      <clean Conductor workflow def>,
        "subWorkflows":      [...],
        "compensationFlows": [...],
        "faultHandlerFlows": [...],
        "warnings":          [...],   # preserved — useful for caller
        "workflowCount":     int,
    }
    """
    raw = copy.deepcopy(bundle)

    warnings = raw.pop("warnings", [])

    main = _clean(raw.get("mainWorkflow", {}))
    subs = [_clean(w) for w in raw.get("subWorkflows", [])]
    comp = [_clean(w) for w in raw.get("compensationFlows", [])]
    faults = [_clean(w) for w in raw.get("faultHandlerFlows", [])]

    total = 1 + len(subs) + len(comp) + len(faults)

    return {
        "mainWorkflow": main,
        "subWorkflows": subs,
        "compensationFlows": comp,
        "faultHandlerFlows": faults,
        "warnings": warnings,
        "workflowCount": total,
    }


def all_workflows(bundle: dict) -> list[dict]:
    """
    Flatten the bundle into an ordered list: main first, then subs/comp/faults.
    Useful for iterating when you want to register each workflow individually.
    """
    return (
        [bundle["mainWorkflow"]]
        + bundle.get("subWorkflows", [])
        + bundle.get("compensationFlows", [])
        + bundle.get("faultHandlerFlows", [])
    )
