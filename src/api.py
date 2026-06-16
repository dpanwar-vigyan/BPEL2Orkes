"""
BPEL2Orkes REST API
Wraps the parser + pattern mapper + code generator pipeline as a FastAPI service.

Endpoints:
  POST /api/v1/convert         — BPEL XML → raw Conductor bundle JSON (with internals)
  POST /api/v1/convert/file    — multipart BPEL file upload → raw bundle JSON
  POST /api/v1/convert/clean   — BPEL XML → clean deployable Conductor bundle JSON
  POST /api/v1/validate        — BPEL XML → convert + register mainWorkflow on Orkes
  POST /api/v1/parse           — BPEL XML → AST JSON (diagnostic)
  GET  /api/v1/health          — liveness check
  GET  /api/v1/version         — version info
  GET  /                       — Web UI
  GET  /mcp/sse                — MCP SSE endpoint (Claude Desktop / MCP clients)
"""

from __future__ import annotations

import os
import time
import httpx
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).parent))

from bpel_parser import parse_bpel, BPELParseError
from pattern_mapper import map_bpel_to_conductor
from code_generator import generate
from diagram_generator import generate_mermaid, generate_migration_summary
from mcp_server import mcp

# ── MCP ASGI app (must be created before FastAPI so lifespan can be wired) ─────
_mcp_asgi = mcp.http_app(transport="streamable-http", path="/")

@asynccontextmanager
async def _lifespan(app: FastAPI):
    async with _mcp_asgi.lifespan(app):
        yield

# ── App ────────────────────────────────────────────────────────────────────────

VERSION = "0.2.0"
ENV = os.getenv("BPEL2ORKES_ENV", "local")

app = FastAPI(
    title="BPEL2Orkes",
    description="Convert IBM BPEL processes to Orkes Conductor workflow JSON",
    version=VERSION,
    docs_url="/docs" if ENV != "production" else None,
    redoc_url="/redoc" if ENV != "production" else None,
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bpel2orkes.kshetra.studio",
        "https://staging.bpel2orkes.kshetra.studio",
        "https://askmybank.ai",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_BPEL_BYTES = int(os.getenv("BPEL_MAX_SIZE_MB", "5")) * 1024 * 1024

# Orkes Developer base URL (can be overridden via env for other Orkes clusters)
ORKES_BASE_URL = os.getenv("ORKES_BASE_URL", "https://developer.orkescloud.com")


# ── Static UI ──────────────────────────────────────────────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)

# Mount static assets (CSS, JS, images) at /static — the index.html is served
# directly via the root GET handler so we keep full control of the response.
if (_STATIC_DIR / "assets").exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Serve sample BPEL files for the UI sample loader
_SAMPLES_DIR = Path(__file__).parent.parent / "samples"
if _SAMPLES_DIR.exists():
    app.mount("/samples", StaticFiles(directory=str(_SAMPLES_DIR)), name="samples")

# ── MCP server (SSE transport) ─────────────────────────────────────────────────
# Customers add this to Claude Desktop claude_desktop_config.json:
#   { "mcpServers": { "bpel2orkes": { "type": "http", "url": "https://bpel2orkes.kshetra.studio/mcp/" } } }
# Note: trailing slash required — Claude Code CLI: claude mcp add --transport http bpel2orkes https://bpel2orkes.kshetra.studio/mcp/
app.mount("/mcp", _mcp_asgi)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui():
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>BPEL2Orkes API</h1><p>UI not found.</p>", status_code=200)
    return HTMLResponse(index.read_text(encoding="utf-8"))


# ── Request size guard ─────────────────────────────────────────────────────────

@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BPEL_BYTES:
        return JSONResponse(
            status_code=413,
            content={"error": f"Request too large. Maximum BPEL size is {MAX_BPEL_BYTES // (1024*1024)} MB."},
        )
    return await call_next(request)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _read_body(body: bytes) -> bytes:
    stripped = body.lstrip()
    if not stripped.startswith(b"<"):
        raise HTTPException(
            status_code=422,
            detail="Body does not appear to be XML. Send raw BPEL XML as the request body.",
        )
    return body


def _convert(body: bytes) -> tuple[dict, float]:
    """Parse + map + generate. Returns (clean_bundle, duration_ms)."""
    t0 = time.perf_counter()
    ast = parse_bpel(body)
    raw_bundle = map_bpel_to_conductor(ast)
    bundle = generate(raw_bundle)
    return bundle, round((time.perf_counter() - t0) * 1000, 1)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "env": ENV, "version": VERSION}


@app.get("/api/v1/version")
def version():
    return {
        "version": VERSION,
        "env": ENV,
        "supportedBpelVersions": ["2.0"],
        "supportedExtensions": ["IBM BPELX 6.0.0"],
    }


@app.post("/api/v1/parse")
async def parse(request: Request):
    """Parse BPEL XML → AST JSON. Useful for debugging before mapping."""
    body = await request.body()
    _read_body(body)
    t0 = time.perf_counter()
    try:
        ast = parse_bpel(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected parse error: {exc}")
    return {"durationMs": round((time.perf_counter() - t0) * 1000, 1), "ast": ast}


@app.post("/api/v1/convert")
async def convert(request: Request):
    """
    Convert BPEL XML to a clean Orkes Conductor workflow bundle.

    Send raw BPEL XML as the request body (Content-Type: application/xml).

    Returns:
      {
        "durationMs": 12.3,
        "warningCount": 2,
        "workflowCount": 3,
        "bundle": { "mainWorkflow": {...}, "subWorkflows": [...], ... }
      }
    """
    body = await request.body()
    _read_body(body)
    try:
        bundle, ms = _convert(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    return {
        "durationMs": ms,
        "warningCount": len(bundle.get("warnings", [])),
        "workflowCount": bundle.get("workflowCount", 1),
        "bundle": bundle,
    }


@app.post("/api/v1/convert/file")
async def convert_file(file: UploadFile = File(...)):
    """Convert a BPEL file upload to a Conductor workflow bundle (multipart/form-data)."""
    if file.size and file.size > MAX_BPEL_BYTES:
        raise HTTPException(status_code=413, detail="File too large.")
    body = await file.read()
    _read_body(body)
    try:
        bundle, ms = _convert(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    return {
        "filename": file.filename,
        "durationMs": ms,
        "warningCount": len(bundle.get("warnings", [])),
        "workflowCount": bundle.get("workflowCount", 1),
        "bundle": bundle,
    }


@app.post("/api/v1/convert/diagram")
async def convert_diagram(request: Request):
    """
    Convert BPEL XML and return a Mermaid flowchart diagram string + migration summary.
    Render the diagram with mermaid.js on the client.
    """
    body = await request.body()
    _read_body(body)
    try:
        bundle, ms = _convert(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    return {
        "durationMs": ms,
        "mermaid": generate_mermaid(bundle),
        "summary": generate_migration_summary(bundle),
        "warnings": bundle.get("warnings", []),
    }


@app.post("/api/v1/convert/clean")
async def convert_clean(request: Request):
    """
    Same as /convert but the response contains only the mainWorkflow JSON,
    ready to copy-paste into Orkes or register via the Orkes API.
    """
    body = await request.body()
    _read_body(body)
    try:
        bundle, ms = _convert(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    return {
        "durationMs": ms,
        "warningCount": len(bundle.get("warnings", [])),
        "workflowCount": bundle.get("workflowCount", 1),
        "warnings": bundle.get("warnings", []),
        "mainWorkflow": bundle["mainWorkflow"],
        "subWorkflows": bundle.get("subWorkflows", []),
        "compensationFlows": bundle.get("compensationFlows", []),
        "faultHandlerFlows": bundle.get("faultHandlerFlows", []),
    }


async def _orkes_token(key_id: str, key_secret: str, base_url: str) -> str:
    """Exchange Orkes Key ID + Key Secret for a short-lived JWT token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{base_url}/api/token",
            json={"keyId": key_id, "keySecret": key_secret},
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Orkes token exchange failed (HTTP {resp.status_code}): {resp.text[:500]}",
        )
    token = resp.json().get("token")
    if not token:
        raise HTTPException(status_code=502, detail="Orkes token response missing 'token' field")
    return token


@app.post("/api/v1/validate")
async def validate(
    request: Request,
    x_orkes_key_id: str = Header(..., description="Orkes Application Key ID"),
    x_orkes_key_secret: str = Header(..., description="Orkes Application Key Secret"),
    x_orkes_base_url: str = Header(default=None, description="Orkes cluster URL (default: developer.orkescloud.com)"),
):
    """
    Convert BPEL XML, exchange Key ID + Key Secret for a JWT token, then register
    the mainWorkflow on the specified Orkes instance.

    Headers required:
      X-Orkes-Key-Id     — Key ID from your Orkes Application
      X-Orkes-Key-Secret — Key Secret from your Orkes Application
    Optional:
      X-Orkes-Base-Url   — Base URL of your Orkes cluster (default: https://developer.orkescloud.com)

    The workflow is registered (PUT /api/metadata/workflow) but NOT started.
    """
    base_url = (x_orkes_base_url or ORKES_BASE_URL).rstrip("/")

    body = await request.body()
    _read_body(body)
    try:
        bundle, ms = _convert(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    token = await _orkes_token(x_orkes_key_id, x_orkes_key_secret, base_url)
    main_wf = bundle["mainWorkflow"]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(
                f"{base_url}/api/metadata/workflow",
                json=[main_wf],
                headers={
                    "X-Authorization": token,
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Orkes: {exc}")

    orkes_ok = resp.status_code in (200, 201, 204)

    return {
        "durationMs": ms,
        "warningCount": len(bundle.get("warnings", [])),
        "workflowCount": bundle.get("workflowCount", 1),
        "orkesStatus": resp.status_code,
        "orkesOk": orkes_ok,
        "orkesResponse": resp.text[:2000] if not orkes_ok else None,
        "workflowName": main_wf.get("name"),
        "bundle": bundle,
    }


# ── Local dev entrypoint ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
