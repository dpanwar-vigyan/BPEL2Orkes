"""
BPEL2Orkes REST API
Wraps the parser + pattern mapper pipeline as a FastAPI service.

Endpoints:
  POST /api/v1/convert   — BPEL XML → Conductor workflow bundle JSON
  POST /api/v1/parse     — BPEL XML → AST JSON (diagnostic)
  GET  /api/v1/health    — liveness check
  GET  /api/v1/version   — version info
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Resolve src/ on path regardless of working directory
import sys
sys.path.insert(0, str(Path(__file__).parent))

from bpel_parser import parse_bpel, BPELParseError
from pattern_mapper import map_bpel_to_conductor

# ── App ────────────────────────────────────────────────────────────────────────

VERSION = "0.1.0"
ENV = os.getenv("BPEL2ORKES_ENV", "local")

app = FastAPI(
    title="BPEL2Orkes",
    description="Convert IBM BPEL processes to Orkes Conductor workflow JSON",
    version=VERSION,
    docs_url="/docs" if ENV != "production" else None,   # hide Swagger in prod
    redoc_url="/redoc" if ENV != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://bpel2orkes.kshetra.studio",
                   "https://staging.bpel2orkes.kshetra.studio",
                   "https://askmybank.ai",
                   "http://localhost:3000"],   # local dev UI
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_BPEL_BYTES = int(os.getenv("BPEL_MAX_SIZE_MB", "5")) * 1024 * 1024


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
    """Validate content looks like XML before passing to parser."""
    stripped = body.lstrip()
    if not stripped.startswith(b"<"):
        raise HTTPException(
            status_code=422,
            detail="Body does not appear to be XML. Send raw BPEL XML as the request body.",
        )
    return body


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
    """
    Parse BPEL XML and return the structured AST JSON.
    Useful for inspection and debugging before mapping.

    Send raw BPEL XML as the request body (Content-Type: application/xml).
    """
    body = await request.body()
    _read_body(body)
    t0 = time.perf_counter()
    try:
        ast = parse_bpel(body)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected parse error: {exc}")

    return {
        "durationMs": round((time.perf_counter() - t0) * 1000, 1),
        "ast": ast,
    }


@app.post("/api/v1/convert")
async def convert(request: Request):
    """
    Convert BPEL XML to an Orkes Conductor workflow bundle.

    Send raw BPEL XML as the request body (Content-Type: application/xml).

    Returns:
      {
        "durationMs": 12.3,
        "warningCount": 2,
        "bundle": {
          "mainWorkflow":      {...},
          "subWorkflows":      [...],
          "compensationFlows": [...],
          "faultHandlerFlows": [...],
          "warnings":          [...]
        }
      }
    """
    body = await request.body()
    _read_body(body)
    t0 = time.perf_counter()
    try:
        ast = parse_bpel(body)
        bundle = map_bpel_to_conductor(ast)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    return {
        "durationMs": round((time.perf_counter() - t0) * 1000, 1),
        "warningCount": len(bundle.get("warnings", [])),
        "bundle": bundle,
    }


@app.post("/api/v1/convert/file")
async def convert_file(file: UploadFile = File(...)):
    """
    Convert a BPEL file upload to a Conductor workflow bundle.
    Accepts multipart/form-data with a 'file' field containing the .bpel file.
    """
    if file.size and file.size > MAX_BPEL_BYTES:
        raise HTTPException(status_code=413, detail="File too large.")

    body = await file.read()
    _read_body(body)
    t0 = time.perf_counter()
    try:
        ast = parse_bpel(body)
        bundle = map_bpel_to_conductor(ast)
    except BPELParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

    return {
        "filename": file.filename,
        "durationMs": round((time.perf_counter() - t0) * 1000, 1),
        "warningCount": len(bundle.get("warnings", [])),
        "bundle": bundle,
    }


# ── Local dev entrypoint ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
