"""
BPEL2Orkes MCP Server

Exposes BPEL conversion as MCP tools so Claude (and other LLM clients)
can convert BPEL processes during a conversation.

Tools:
  convert_bpel        — Convert BPEL XML → clean Conductor workflow bundle
  validate_on_orkes   — Convert + register on an Orkes instance
  get_migration_tips  — Explain a specific warning in plain English

Run locally (for Claude Desktop):
  python -m src.mcp_server

Claude Desktop config (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "bpel2orkes": {
        "command": "python",
        "args": ["-m", "src.mcp_server"],
        "cwd": "/path/to/BPEL2Orkes"
      }
    }
  }

Hosted (no local setup needed) — add to claude_desktop_config.json:
  {
    "mcpServers": {
      "bpel2orkes": {
        "type": "http",
        "url": "https://bpel2orkes.kshetra.studio/mcp/"
      }
    }
  }

Or via Claude Code CLI:
  claude mcp add --transport http bpel2orkes https://bpel2orkes.kshetra.studio/mcp/
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on the path when run as __main__
sys.path.insert(0, str(Path(__file__).parent))

from fastmcp import FastMCP
from bpel_parser import parse_bpel, BPELParseError
from pattern_mapper import map_bpel_to_conductor
from code_generator import generate
from diagram_generator import generate_migration_summary

import httpx

ORKES_DEFAULT_URL = "https://developer.orkescloud.com"

mcp = FastMCP(
    name="BPEL2Orkes",
    instructions=(
        "Convert IBM WS-BPEL 2.0 processes (including IBM BPELX extensions from "
        "WebSphere Process Server, BAW, and IIB) into Orkes Conductor workflow JSON. "
        "Use convert_bpel to get the workflow bundle and understand what needs manual work. "
        "Use validate_on_orkes to register the converted workflow on an Orkes instance. "
        "Always explain warnings to the user in plain English after conversion."
    ),
)


@mcp.tool()
def convert_bpel(bpel_xml: str) -> dict:
    """
    Convert IBM BPEL XML to an Orkes Conductor workflow bundle.

    Args:
        bpel_xml: Raw BPEL XML string (WS-BPEL 2.0, including IBM BPELX extensions)

    Returns:
        A dict containing:
        - mainWorkflow: Clean Conductor workflow definition ready to register
        - subWorkflows: List of sub-workflow definitions (from BPEL scopes)
        - compensationFlows: Compensation handler workflows
        - faultHandlerFlows: Fault handler workflows
        - warnings: List of items needing manual developer attention
        - summary: Counts of auto-converted vs needs-work tasks
    """
    try:
        ast = parse_bpel(bpel_xml.encode())
    except BPELParseError as e:
        return {"error": f"BPEL parse error: {e}"}
    except Exception as e:
        return {"error": f"Unexpected parse error: {e}"}

    try:
        raw_bundle = map_bpel_to_conductor(ast)
        bundle = generate(raw_bundle)
    except Exception as e:
        return {"error": f"Conversion error: {e}"}

    summary = generate_migration_summary(bundle)

    return {
        "mainWorkflow": bundle["mainWorkflow"],
        "subWorkflows": bundle.get("subWorkflows", []),
        "compensationFlows": bundle.get("compensationFlows", []),
        "faultHandlerFlows": bundle.get("faultHandlerFlows", []),
        "warnings": bundle.get("warnings", []),
        "summary": summary,
    }


@mcp.tool()
async def validate_on_orkes(
    bpel_xml: str,
    key_id: str,
    key_secret: str,
    orkes_base_url: str = ORKES_DEFAULT_URL,
) -> dict:
    """
    Convert BPEL XML and register the mainWorkflow on an Orkes Conductor instance.

    Args:
        bpel_xml: Raw BPEL XML string
        key_id: Orkes Application Key ID (from Orkes Console → Applications)
        key_secret: Orkes Application Key Secret
        orkes_base_url: Base URL of your Orkes cluster (default: https://developer.orkescloud.com)

    Returns:
        Registration result including orkesOk (bool), orkesStatus (HTTP code),
        workflowName, and the converted bundle.
    """
    bundle_result = convert_bpel(bpel_xml)
    if "error" in bundle_result:
        return bundle_result

    base_url = orkes_base_url.rstrip("/")

    # Exchange Key ID + Secret for JWT
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                f"{base_url}/api/token",
                json={"keyId": key_id, "keySecret": key_secret},
                headers={"Content-Type": "application/json"},
            )
    except httpx.RequestError as e:
        return {"error": f"Could not reach Orkes at {base_url}: {e}"}

    if token_resp.status_code != 200:
        return {
            "error": f"Orkes token exchange failed (HTTP {token_resp.status_code}): {token_resp.text[:300]}"
        }

    token = token_resp.json().get("token")
    if not token:
        return {"error": "Orkes token response missing 'token' field"}

    # Register workflow
    main_wf = bundle_result["mainWorkflow"]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            reg_resp = await client.put(
                f"{base_url}/api/metadata/workflow",
                json=[main_wf],
                headers={"X-Authorization": token, "Content-Type": "application/json"},
            )
    except httpx.RequestError as e:
        return {"error": f"Could not register workflow on Orkes: {e}"}

    orkes_ok = reg_resp.status_code in (200, 201, 204)

    return {
        "orkesOk": orkes_ok,
        "orkesStatus": reg_resp.status_code,
        "orkesResponse": reg_resp.text[:500] if not orkes_ok else None,
        "workflowName": main_wf.get("name"),
        "orkesConsoleUrl": f"{base_url}/workflowDef/{main_wf.get('name')}" if orkes_ok else None,
        "warnings": bundle_result.get("warnings", []),
        "summary": bundle_result.get("summary", {}),
    }


@mcp.tool()
def get_migration_tips(workflow_name: str, warnings: list[str]) -> str:
    """
    Given the warnings from a conversion, return a structured migration guide
    explaining what each warning means and what the developer needs to do.

    Args:
        workflow_name: Name of the converted workflow
        warnings: List of warning strings from convert_bpel output

    Returns:
        A plain-English migration guide with numbered action items.
    """
    if not warnings:
        return (
            f"✅ {workflow_name} converted with no warnings. "
            "The workflow is ready to register and run on Orkes once task workers are deployed."
        )

    lines = [
        f"## Migration Guide for {workflow_name}",
        f"{len(warnings)} item(s) need attention before this workflow can run:\n",
    ]

    for i, w in enumerate(warnings, 1):
        # Extract ref name and message
        if w.startswith("[") and "]" in w:
            ref = w[1:w.index("]")]
            msg = w[w.index("]") + 2:]
        else:
            ref = f"Item {i}"
            msg = w

        lines.append(f"**{i}. {ref}**")
        lines.append(msg)
        lines.append("")

    lines += [
        "---",
        "**Next steps:**",
        "1. For each SIMPLE task flagged above — implement a Conductor worker using the Orkes Java or Python SDK",
        "2. For each WAIT task — wire your ESB/MQ/API gateway to POST to the Conductor task callback endpoint",
        "3. For correlation sets — pass `workflow.workflowId` as `X-Correlation-Id` in all outbound calls",
        "4. Once workers are deployed and running, start the workflow via POST /api/workflow/{workflowName}",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
