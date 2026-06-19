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
import threading
import httpx
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).parent))

from bpel_parser import parse_bpel, BPELParseError
from pattern_mapper import map_bpel_to_conductor
from code_generator import generate
from diagram_generator import generate_mermaid, generate_migration_summary
from mcp_server import mcp
from auth import require_api_key, deduct_credit, optional_api_key
from oauth import router as oauth_router, get_session

# ── MCP ASGI app (must be created before FastAPI so lifespan can be wired) ─────
_mcp_asgi = mcp.http_app(transport="streamable-http", path="/", stateless_http=True)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    async with _mcp_asgi.lifespan(app):
        yield

# ── App ────────────────────────────────────────────────────────────────────────

VERSION = "0.2.0"
ENV = os.getenv("BPEL2ORKES_ENV", "local")
BASE_URL = {
    "production": "https://bpel2orkes.kshetra.studio",
    "staging":    "https://staging.bpel2orkes.kshetra.studio",
}.get(ENV, "http://localhost:8000")

app = FastAPI(
    title="BPEL2Orkes",
    description="Convert IBM BPEL processes to Orkes Conductor workflow JSON",
    version=VERSION,
    docs_url=None,   # served at /api/docs via custom Swagger UI
    redoc_url=None,
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
app.include_router(oauth_router)


@app.get("/.well-known/mcp/server-card.json", include_in_schema=False)
def mcp_server_card():
    """Publicly accessible MCP server card for Smithery and other MCP registries."""
    card_path = _STATIC_DIR / ".well-known" / "mcp" / "server-card.json"
    return Response(
        content=card_path.read_bytes(),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui():
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>BPEL2Orkes API</h1><p>UI not found.</p>", status_code=200)
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    session = get_session(request)
    if not session:
        return RedirectResponse("/auth/github")
    page = _STATIC_DIR / "dashboard.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/convert", response_class=HTMLResponse, include_in_schema=False)
def converter(request: Request):
    page = _STATIC_DIR / "convert.html"
    if not page.exists():
        return RedirectResponse("/")
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/openapi.json", include_in_schema=False)
def openapi_spec():
    """Public OpenAPI 3.0 spec — no authentication required."""
    spec_path = _STATIC_DIR / "openapi.json"
    return Response(
        content=spec_path.read_bytes(),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"},
    )


_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BPEL2Orkes — API Reference</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
<style>
  body { margin: 0; }
  .topbar { display: none !important; }
  .swagger-ui .info .title { font-size: 24px; }
</style>
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({
  url: '/openapi.json',
  dom_id: '#swagger-ui',
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
  layout: 'BaseLayout',
  deepLinking: true,
  tryItOutEnabled: true,
  persistAuthorization: true,
  requestInterceptor: (req) => { req.headers['X-Requested-With'] = 'SwaggerUI'; return req; },
});
</script>
</body>
</html>"""

@app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
def api_docs():
    """Interactive Swagger UI for the BPEL2Orkes API."""
    return HTMLResponse(_SWAGGER_HTML)


# ── IP rate limiting for public / optional-auth endpoints ─────────────────────
_rl_lock = threading.Lock()
_rl_hits: dict[str, list[float]] = defaultdict(list)

_RL_LIMITS: dict[str, int] = {
    "/api/v1/parse": 20,           # 20 req/min per IP — no auth, diagnostic only
    "/api/v1/convert/diagram": 30, # 30 req/min per IP — optional auth, no credit cost
}
_RL_WINDOW = 60.0


def _rl_check(ip: str, path: str) -> bool:
    """Return True if allowed, False if rate-limited. Mutates _rl_hits in-place."""
    limit = _RL_LIMITS.get(path)
    if not limit:
        return True
    now = time.time()
    key = f"{ip}:{path}"
    with _rl_lock:
        hits = _rl_hits[key]
        hits[:] = [t for t in hits if now - t < _RL_WINDOW]
        if len(hits) >= limit:
            return False
        hits.append(now)
    return True


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


@app.middleware("http")
async def rate_limit_unauthenticated(request: Request, call_next):
    """Per-IP sliding-window rate limit on public / optional-auth endpoints.
    Authenticated requests (X-Api-Key present) are not rate-limited here —
    the credit quota system handles per-user abuse on those paths."""
    path = request.url.path
    if path in _RL_LIMITS and not request.headers.get("x-api-key"):
        ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
        if not _rl_check(ip, path):
            limit = _RL_LIMITS[path]
            return JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "error": "rate_limited",
                        "message": f"Too many requests. Limit is {limit} per minute per IP. Sign in with GitHub to get an API key and higher limits.",
                        "signInUrl": "https://bpel2orkes.kshetra.studio/auth/github",
                    }
                },
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
async def convert(request: Request, _user: dict = Depends(require_api_key)):
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

    deduct_credit(_user["userId"])
    return {
        "durationMs": ms,
        "warningCount": len(bundle.get("warnings", [])),
        "workflowCount": bundle.get("workflowCount", 1),
        "bundle": bundle,
    }


@app.post("/api/v1/convert/file")
async def convert_file(file: UploadFile = File(...), _user: dict = Depends(require_api_key)):
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

    deduct_credit(_user["userId"])
    return {
        "filename": file.filename,
        "durationMs": ms,
        "warningCount": len(bundle.get("warnings", [])),
        "workflowCount": bundle.get("workflowCount", 1),
        "bundle": bundle,
    }


@app.post("/api/v1/convert/diagram")
async def convert_diagram(request: Request, _user: dict = Depends(optional_api_key)):
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
async def convert_clean(request: Request, _user: dict = Depends(optional_api_key)):
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
    _user: dict = Depends(require_api_key),
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


# ── Dashboard helpers ──────────────────────────────────────────────────────────

@app.get("/api/v1/my-key")
async def my_key(request: Request):
    """Return the full (unmasked) API key for the signed-in user."""
    from oauth import get_session as _gs
    from auth import get_user_by_id
    session = _gs(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_user_by_id(session["userId"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {"apiKey": user["apiKey"]}


@app.post("/api/v1/rotate-key")
async def rotate_key(request: Request):
    """Rotate the signed-in user's API key. Old key is immediately invalidated."""
    from oauth import get_session as _gs
    from auth import get_user_by_id, rotate_api_key as _rotate

    session = _gs(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_user_by_id(session["userId"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    updated = _rotate(session["userId"])
    return {"apiKey": updated["apiKey"]}


@app.post("/api/v1/checkout")
async def checkout(request: Request):
    """Create a Stripe Checkout session to top up conversion credits."""
    import stripe as _stripe
    from oauth import get_session as _gs
    from auth import get_user_by_id, MIN_TOPUP_CENTS, MAX_TOPUP_CENTS, CENTS_PER_CONVERSION

    session = _gs(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    amount_cents = int(body.get("amount_cents", 0))
    if amount_cents < MIN_TOPUP_CENTS or amount_cents > MAX_TOPUP_CENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Amount must be between ${MIN_TOPUP_CENTS//100} and ${MAX_TOPUP_CENTS//100}"
        )

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    conversions = amount_cents // CENTS_PER_CONVERSION
    _stripe.api_key = stripe_key
    checkout_session = _stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": "BPEL2Orkes Conversion Credits",
                    "description": f"{conversions} conversions at ${CENTS_PER_CONVERSION/100:.2f} each",
                },
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=f"{BASE_URL}/dashboard?topped_up=1",
        cancel_url=f"{BASE_URL}/dashboard",
        client_reference_id=session["userId"],
        customer_email=session["email"],
        metadata={"amount_cents": str(amount_cents)},
    )
    return {"url": checkout_session.url}


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook — adds purchased credits to user's balance."""
    import stripe as _stripe
    from auth import add_credits

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    _stripe.api_key = stripe_key
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = _stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        user_id = sess.get("client_reference_id")
        amount_cents = int(sess.get("amount_total", 0))
        if user_id and amount_cents > 0:
            add_credits(user_id, amount_cents)

    return {"received": True}


# ── Lambda entrypoint (API Gateway HTTP API proxy integration) ─────────────────

from mangum import Mangum
handler = Mangum(app)


# ── Local dev entrypoint ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
