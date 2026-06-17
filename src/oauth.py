"""
GitHub OAuth2 sign-in routes for BPEL2Orkes.

Env vars required:
  GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
  SESSION_SECRET   — random string for signing session cookies

Routes:
  GET /auth/github           → redirect to GitHub
  GET /auth/github/callback  → exchange code → create user → set cookie → /dashboard
  GET /auth/logout           → clear cookie → /
  GET /api/v1/me             → current user info (JSON)
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from auth import get_or_create_user

router = APIRouter()

SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-in-prod")
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_signer = URLSafeTimedSerializer(SESSION_SECRET)

ENV = os.getenv("BPEL2ORKES_ENV", "local")
BASE_URL = {
    "production": "https://bpel2orkes.kshetra.studio",
    "staging":    "https://staging.bpel2orkes.kshetra.studio",
}.get(ENV, "http://localhost:8000")


# ── Session helpers ────────────────────────────────────────────────────────────

def _set_session(response, user: dict) -> None:
    payload = {"userId": user["userId"], "email": user["email"], "name": user["name"]}
    token = _signer.dumps(payload)
    response.set_cookie(
        "session", token,
        max_age=SESSION_MAX_AGE, httponly=True, secure=(ENV != "local"), samesite="lax",
    )


def get_session(request: Request) -> dict | None:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return _signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ── GitHub OAuth ───────────────────────────────────────────────────────────────

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI  = f"{BASE_URL}/auth/github/callback"


@router.get("/auth/github", include_in_schema=False)
async def github_login(request: Request):
    if not GITHUB_CLIENT_ID:
        return JSONResponse({"error": "GitHub OAuth not configured"}, status_code=503)
    client = AsyncOAuth2Client(
        client_id=GITHUB_CLIENT_ID, client_secret=GITHUB_CLIENT_SECRET,
        redirect_uri=GITHUB_REDIRECT_URI,
    )
    uri, state = client.create_authorization_url(
        "https://github.com/login/oauth/authorize", scope="user:email",
    )
    response = RedirectResponse(uri)
    response.set_cookie("oauth_state", state, httponly=True, secure=(ENV != "local"), max_age=300)
    return response


@router.get("/auth/github/callback", include_in_schema=False)
async def github_callback(request: Request, code: str, state: str):
    stored_state = request.cookies.get("oauth_state", "")
    async with httpx.AsyncClient() as http:
        token_resp = await http.post(
            "https://github.com/login/oauth/access_token",
            data={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET,
                  "code": code, "redirect_uri": GITHUB_REDIRECT_URI},
            headers={"Accept": "application/json"},
        )
        token = token_resp.json().get("access_token", "")
        user_resp = await http.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        email_resp = await http.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    gh = user_resp.json()
    emails = email_resp.json()
    primary_email = next((e["email"] for e in emails if e.get("primary")), gh.get("email", ""))
    user = get_or_create_user("github", str(gh["id"]), primary_email, gh.get("name") or gh.get("login", ""))
    response = RedirectResponse("/dashboard")
    response.delete_cookie("oauth_state")
    _set_session(response, user)
    return response


# ── Logout ─────────────────────────────────────────────────────────────────────

@router.get("/auth/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response


# ── /api/v1/me ────────────────────────────────────────────────────────────────

@router.get("/api/v1/me")
async def me(request: Request):
    """Return current user info. 401 if not signed in."""
    from auth import get_user_by_id, conversions_remaining, CENTS_PER_CONVERSION
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    user = get_user_by_id(session["userId"])
    if not user:
        return JSONResponse({"error": "user_not_found"}, status_code=401)
    remaining = conversions_remaining(user)
    balance_cents = int(user.get("creditBalanceCents", 0))
    return {
        "userId": user["userId"],
        "email": user["email"],
        "name": user["name"],
        "tier": user["tier"],
        "apiKey": user["apiKey"][:12] + "…",
        "creditBalanceCents": balance_cents,
        "conversionsRemaining": remaining,
        "centsPerConversion": CENTS_PER_CONVERSION,
    }
