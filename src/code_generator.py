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


# ── XPath → JS condition translator (literals only) ───────────────────────────
# Handles the subset that appears in generated SWITCH tasks.

_XPATH_REPLACEMENTS = [
    # string equality:  $var/element = 'value'  →  $.var.element === 'value'
    (re.compile(r"\$(\w+)/(\w+)\s*=\s*'([^']*)'"),
     r"$.\\1.\\2 === '\\3'"),
    # numeric equality: $var = 42  →  $.var === 42
    (re.compile(r"\$(\w+)\s*=\s*(\d+)"),
     r"$.\\1 === \\2"),
    # boolean true/false()
    (re.compile(r"\btrue\(\)"), "true"),
    (re.compile(r"\bfalse\(\)"), "false"),
    # not()
    (re.compile(r"not\(([^)]+)\)"), r"!(\1)"),
    # and / or (XPath uses lowercase keywords, same as JS — no-op but normalise spacing)
    (re.compile(r"\s+and\s+"), " && "),
    (re.compile(r"\s+or\s+"), " || "),
]


def _translate_condition(expr: str) -> str:
    """
    Best-effort translation of a BPEL/XPath condition string to a JS expression
    suitable for Orkes SWITCH evaluatorType=javascript.
    """
    result = expr
    for pattern, repl in _XPATH_REPLACEMENTS:
        result = pattern.sub(repl, result)
    return result


# ── Recursive field stripper ───────────────────────────────────────────────────

def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k in _INTERNAL_FIELDS:
                continue
            # Translate condition strings on SWITCH decision cases
            if k == "expression" and isinstance(v, str):
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
