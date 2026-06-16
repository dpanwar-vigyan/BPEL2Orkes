"""
Diagram Generator — produces a Mermaid flowchart from a clean Conductor bundle.

Color coding:
  Green  (#14532d) — auto-converted, no action needed (HTTP, SET_VARIABLE, INLINE)
  Amber  (#92400e) — converted but needs developer intervention (warning present)
  Red    (#7f1d1d) — process termination (TERMINATE)
  Purple (#4c1d95) — human task (HUMAN)
  Blue   (#1e3a5f) — structural (FORK_JOIN, JOIN, SWITCH, DO_WHILE, SUB_WORKFLOW)
  Grey   (#1e2433) — neutral / no action (WAIT without warning shouldn't happen but fallback)
"""

from __future__ import annotations

import re

# ── Style map by task type (default — warning overrides to amber) ─────────────

_TYPE_STYLE = {
    "HTTP":          "fill:#14532d,color:#bbf7d0,stroke:#22c55e",
    "SET_VARIABLE":  "fill:#1e2433,color:#94a3b8,stroke:#334155",
    "INLINE":        "fill:#1e2433,color:#94a3b8,stroke:#334155",
    "SWITCH":        "fill:#1e3a5f,color:#bfdbfe,stroke:#3b82f6",
    "FORK_JOIN":     "fill:#1e3a5f,color:#bfdbfe,stroke:#3b82f6",
    "JOIN":          "fill:#1e3a5f,color:#bfdbfe,stroke:#3b82f6",
    "DO_WHILE":      "fill:#1e3a5f,color:#bfdbfe,stroke:#3b82f6",
    "SUB_WORKFLOW":  "fill:#1e3a5f,color:#bfdbfe,stroke:#3b82f6",
    "TERMINATE":     "fill:#7f1d1d,color:#fca5a5,stroke:#ef4444",
    "HUMAN":         "fill:#4c1d95,color:#ddd6fe,stroke:#7c3aed",
    "WAIT":          "fill:#92400e,color:#fde68a,stroke:#d97706",   # always amber — needs callback
    "SIMPLE":        "fill:#92400e,color:#fde68a,stroke:#d97706",   # always amber — needs worker
}

_AMBER = "fill:#92400e,color:#fde68a,stroke:#d97706"

# Node shape per type
_SHAPE = {
    "SWITCH":       ("{{", "}}"),        # hexagon — decision
    "FORK_JOIN":    ("[/", "/]"),        # parallelogram — fork
    "JOIN":         ("[\\", "\\]"),      # parallelogram reversed — join
    "TERMINATE":    ("([", "])"),        # stadium — terminal
    "SUB_WORKFLOW": ("[[", "]]"),        # subroutine
    "HUMAN":        ("(", ")"),          # rounded — human
    "DO_WHILE":     ("[", "]"),
}
_DEFAULT_SHAPE = ("[", "]")


def _safe_id(ref: str) -> str:
    """Mermaid node IDs must be alphanumeric + underscore."""
    return re.sub(r"[^A-Za-z0-9_]", "_", ref)[:40]


def _label(task: dict) -> str:
    name = task.get("name", task.get("taskReferenceName", "?"))
    ttype = task.get("type", "")
    # Short label: type + name, truncated
    short_name = name[:30] + ("…" if len(name) > 30 else "")
    return f"{ttype}\\n{short_name}"


def _node(task: dict) -> str:
    sid = _safe_id(task.get("taskReferenceName", "node"))
    lbl = _label(task)
    ttype = task.get("type", "")
    open_s, close_s = _SHAPE.get(ttype, _DEFAULT_SHAPE)
    return f'    {sid}{open_s}"{lbl}"{close_s}'


def _style(task: dict, warned_refs: set[str]) -> str:
    sid = _safe_id(task.get("taskReferenceName", "node"))
    ttype = task.get("type", "")
    ref = task.get("taskReferenceName", "")
    if ref in warned_refs:
        style = _AMBER
    else:
        style = _TYPE_STYLE.get(ttype, "fill:#1a1d27,color:#e2e8f0,stroke:#2e3147")
    return f"    style {sid} {style}"


def _edge(from_ref: str, to_ref: str, label: str = "") -> str:
    f = _safe_id(from_ref)
    t = _safe_id(to_ref)
    if label:
        short = label[:20] + ("…" if len(label) > 20 else "")
        return f"    {f} -->|{short}| {t}"
    return f"    {f} --> {t}"


# ── Recursive task walker ──────────────────────────────────────────────────────

def _walk(
    tasks: list[dict],
    warned_refs: set[str],
    nodes: list[str],
    edges: list[str],
    styles: list[str],
    prev_ref: str | None = None,
) -> str | None:
    """Walk task list, emit nodes/edges/styles, return last ref."""
    last_ref = prev_ref
    for task in tasks:
        ttype = task.get("type", "")
        ref = task.get("taskReferenceName", "")

        nodes.append(_node(task))
        styles.append(_style(task, warned_refs))

        if last_ref:
            edges.append(_edge(last_ref, ref))

        if ttype == "FORK_JOIN":
            # Each branch is a list of tasks in forkTasks
            join_ref = None
            for branch in task.get("forkTasks", []):
                branch_last = _walk(branch, warned_refs, nodes, edges, styles, prev_ref=ref)
                # Find the JOIN that follows
                if branch_last:
                    # Will connect to JOIN in next iteration
                    pass

        elif ttype == "SWITCH":
            cases = task.get("decisionCases", {})
            default = task.get("defaultCase", [])
            branch_ends = []
            for case_key, case_tasks in cases.items():
                if case_tasks:
                    # Add label edge to first task of branch
                    first_ref = case_tasks[0].get("taskReferenceName", "")
                    edges_before = len(edges)
                    branch_last = _walk(case_tasks, warned_refs, nodes, edges, styles)
                    # Replace the auto-added edge from switch to first task with a labelled one
                    # (the _walk added it via prev_ref=None, so we add labelled edge manually)
                    if first_ref:
                        edges.append(_edge(ref, first_ref, case_key))
                    if branch_last:
                        branch_ends.append(branch_last)
            if default:
                branch_last = _walk(default, warned_refs, nodes, edges, styles)
                if default[0].get("taskReferenceName"):
                    edges.append(_edge(ref, default[0]["taskReferenceName"], "default"))
                if branch_last:
                    branch_ends.append(branch_last)
            # branch_ends will connect to next task
            last_ref = ref  # simplified: next task connects from switch
            continue

        elif ttype == "DO_WHILE":
            loop_tasks = task.get("loopOver", [])
            if loop_tasks:
                loop_last = _walk(loop_tasks, warned_refs, nodes, edges, styles, prev_ref=ref)
                if loop_last:
                    # Back-edge to show loop
                    edges.append(f"    {_safe_id(loop_last)} -.->|loop| {_safe_id(ref)}")

        last_ref = ref

    return last_ref


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_mermaid(bundle: dict) -> str:
    """
    Generate a Mermaid flowchart string from a clean code_generator bundle.
    Returns a Mermaid diagram string ready to render with mermaid.js.
    """
    # Build set of task reference names that have warnings
    warned_refs: set[str] = set()
    for w in bundle.get("warnings", []):
        # Warnings are formatted as "[refName] message..."
        if w.startswith("["):
            ref = w[1:w.index("]")] if "]" in w else ""
            if ref:
                warned_refs.add(ref)

    main_wf = bundle.get("mainWorkflow", {})
    tasks = main_wf.get("tasks", [])
    wf_name = main_wf.get("name", "workflow")

    nodes: list[str] = []
    edges: list[str] = []
    styles: list[str] = []

    _walk(tasks, warned_refs, nodes, edges, styles)

    lines = [
        f"%%{{init: {{'theme': 'dark', 'themeVariables': {{'darkMode': true}}}}}}%%",
        "flowchart TD",
        f'    START(("▶ {wf_name}"))',
        "    style START fill:#6c63ff,color:#fff,stroke:#8b84ff",
    ]

    # Connect START to first task
    if tasks:
        first_ref = _safe_id(tasks[0].get("taskReferenceName", ""))
        if first_ref:
            lines.append(f"    START --> {first_ref}")

    lines.extend(nodes)
    lines.extend(edges)
    lines.extend(styles)

    # Legend
    lines += [
        "",
        "    subgraph Legend",
        '        L1["⚠ Needs Worker / Callback"]',
        '        L2["✓ Auto-converted"]',
        '        L3[/"⑂ Parallel Fork"/]',
        '        L4(["⊗ Terminate"])',
        '        L5("👤 Human Task")',
        "    end",
        "    style L1 fill:#92400e,color:#fde68a,stroke:#d97706",
        "    style L2 fill:#14532d,color:#bbf7d0,stroke:#22c55e",
        "    style L3 fill:#1e3a5f,color:#bfdbfe,stroke:#3b82f6",
        "    style L4 fill:#7f1d1d,color:#fca5a5,stroke:#ef4444",
        "    style L5 fill:#4c1d95,color:#ddd6fe,stroke:#7c3aed",
    ]

    return "\n".join(lines)


def generate_migration_summary(bundle: dict) -> dict:
    """
    Return counts used for the summary bar above the diagram.
    """
    tasks = bundle.get("mainWorkflow", {}).get("tasks", [])
    warned_refs = {
        w[1:w.index("]")] for w in bundle.get("warnings", [])
        if w.startswith("[") and "]" in w
    }

    auto = sum(1 for t in tasks if t.get("taskReferenceName") not in warned_refs
               and t.get("type") not in ("SIMPLE", "WAIT"))
    needs_work = sum(1 for t in tasks if t.get("taskReferenceName") in warned_refs)
    total = len(tasks)
    sub_count = len(bundle.get("subWorkflows", []))

    return {
        "total": total,
        "autoConverted": auto,
        "needsWork": needs_work,
        "subWorkflows": sub_count,
        "warningCount": len(bundle.get("warnings", [])),
    }
