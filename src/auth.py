"""
Auth, user management, and quota enforcement for BPEL2Orkes.

DynamoDB schema (single table: bpel2orkes-users-{env}):
  PK: userId (str)  — "{provider}:{providerUserId}"  e.g. "github:1234567"
  GSI: apiKey-index on apiKey (str)

User record fields:
  userId, email, name, provider, apiKey, tier, creditBalanceCents, createdAt

Credit model:
  - All users have a creditBalanceCents balance (integer, atomic in DynamoDB)
  - Each conversion deducts CENTS_PER_CONVERSION from the balance
  - Stripe payments add amount_total cents to the balance
  - tier="starter" bypasses quota entirely (manual enterprise accounts)
  - Configurable rate: change CENTS_PER_CONVERSION to reprice without a data migration
"""

from __future__ import annotations

import os
import secrets
import time
from functools import lru_cache
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from fastapi import Header, HTTPException, Request

def _session_user(request: Request) -> Optional[dict]:
    from oauth import get_session
    session = get_session(request)
    if not session:
        return None
    return get_user_by_id(session["userId"])

# ── Config ─────────────────────────────────────────────────────────────────────

ENV = os.getenv("BPEL2ORKES_ENV", "local")
TABLE_NAME = f"bpel2orkes-users-{ENV}"
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")

# Pricing rate — single constant to change conversion cost across all surfaces
CENTS_PER_CONVERSION = 10          # $0.10/conversion → $1 = 10 conversions
FREE_CREDIT_CENTS    = 500         # 50 free conversions ($5 equivalent) on sign-up
MIN_TOPUP_CENTS      = 500         # $5 minimum top-up
MAX_TOPUP_CENTS      = 100_000     # $1,000 maximum single top-up


# ── DynamoDB client ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _table():
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return ddb.Table(TABLE_NAME)


def _auth_bypassed() -> bool:
    return ENV == "local"


# ── User operations ────────────────────────────────────────────────────────────

def _new_api_key(tier: str = "free") -> str:
    prefix = {"free": "bpel2_free_", "paid": "bpel2_", "starter": "bpel2_start_"}.get(tier, "bpel2_")
    return prefix + secrets.token_urlsafe(16)


def get_or_create_user(provider: str, provider_user_id: str, email: str, name: str) -> dict:
    user_id = f"{provider}:{provider_user_id}"
    table = _table()

    resp = table.get_item(Key={"userId": user_id})
    if "Item" in resp:
        item = resp["Item"]
        # Migrate legacy creditsUsed/creditsTotal records to creditBalanceCents
        if "creditBalanceCents" not in item:
            total = int(item.get("creditsTotal", 50))
            used  = int(item.get("creditsUsed", 0))
            balance = max(0, (total - used)) * CENTS_PER_CONVERSION
            table.update_item(
                Key={"userId": user_id},
                UpdateExpression="SET creditBalanceCents = :b REMOVE creditsTotal, creditsUsed",
                ExpressionAttributeValues={":b": balance},
            )
            item["creditBalanceCents"] = balance
        return item

    api_key = _new_api_key("free")
    user = {
        "userId": user_id,
        "email": email,
        "name": name,
        "provider": provider,
        "apiKey": api_key,
        "tier": "free",
        "creditBalanceCents": FREE_CREDIT_CENTS,
        "createdAt": int(time.time()),
    }
    table.put_item(Item=user)
    return user


def get_user_by_api_key(api_key: str) -> Optional[dict]:
    resp = _table().query(
        IndexName="apiKey-index",
        KeyConditionExpression=Key("apiKey").eq(api_key),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    resp = _table().get_item(Key={"userId": user_id})
    return resp.get("Item")


def deduct_credit(user_id: str) -> Optional[dict]:
    """Atomically deduct one conversion's cost. No-op for local dev."""
    if user_id == "local":
        return None
    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="SET creditBalanceCents = creditBalanceCents - :cost",
        ExpressionAttributeValues={":cost": CENTS_PER_CONVERSION},
        ReturnValues="ALL_NEW",
    )
    return resp["Attributes"]


def add_credits(user_id: str, amount_cents: int) -> dict:
    """Add purchased credits (from Stripe webhook). Upgrades tier to 'paid'."""
    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="SET creditBalanceCents = creditBalanceCents + :amt, tier = :tier",
        ExpressionAttributeValues={":amt": amount_cents, ":tier": "paid"},
        ReturnValues="ALL_NEW",
    )
    return resp["Attributes"]


# ── Quota helpers ──────────────────────────────────────────────────────────────

def conversions_remaining(user: dict) -> int | str:
    """Returns int conversions remaining, or 'unlimited' for starter accounts."""
    if user.get("tier") == "starter":
        return "unlimited"
    balance = int(user.get("creditBalanceCents", 0))
    return max(0, balance // CENTS_PER_CONVERSION)


def quota_status(user: dict) -> Optional[dict]:
    """Returns None if quota OK, else an error dict (shared by REST 429 and MCP error)."""
    if user.get("tier") == "starter":
        return None
    balance = int(user.get("creditBalanceCents", 0))
    if balance < CENTS_PER_CONVERSION:
        remaining = conversions_remaining(user)
        return {
            "error": "quota_exceeded",
            "message": "You've run out of conversion credits. Top up to continue.",
            "creditBalanceCents": balance,
            "conversionsRemaining": remaining,
            "topUpUrl": "https://bpel2orkes.kshetra.studio/dashboard",
        }
    return None


def check_quota(user: dict) -> None:
    status = quota_status(user)
    if status:
        raise HTTPException(status_code=429, detail=status)


# ── MCP tool auth ──────────────────────────────────────────────────────────────

def resolve_mcp_caller(api_key: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    if _auth_bypassed():
        return {"userId": "local", "tier": "starter", "creditBalanceCents": 99999}, None

    if not api_key:
        return None, (
            "Missing X-Api-Key header. Sign in at https://bpel2orkes.kshetra.studio "
            "to get your free API key, then add it to your MCP config, e.g.: "
            'claude mcp add --transport http bpel2orkes '
            'https://bpel2orkes.kshetra.studio/mcp/ --header "X-Api-Key: your_key"'
        )

    user = get_user_by_api_key(api_key)
    if not user:
        return None, "Invalid API key."

    status = quota_status(user)
    if status:
        return None, status["message"] + f" Top up at {status['topUpUrl']}"

    return user, None


# ── FastAPI dependencies ───────────────────────────────────────────────────────

async def require_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> dict:
    if _auth_bypassed():
        return {"userId": "local", "tier": "starter", "creditBalanceCents": 99999}

    user = None
    if x_api_key:
        user = get_user_by_api_key(x_api_key)
        if not user:
            raise HTTPException(status_code=401, detail={"error": "invalid_api_key", "message": "API key not found."})
    else:
        user = _session_user(request)
        if not user:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "not_authenticated",
                    "message": "Sign in at https://bpel2orkes.kshetra.studio to get your free API key.",
                },
            )

    check_quota(user)
    return user


async def optional_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> Optional[dict]:
    if _auth_bypassed():
        return None
    user = get_user_by_api_key(x_api_key) if x_api_key else _session_user(request)
    if user:
        check_quota(user)
    return user
