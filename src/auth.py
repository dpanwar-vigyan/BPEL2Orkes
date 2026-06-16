"""
Auth, user management, and quota enforcement for BPEL2Orkes.

DynamoDB schema (single table: bpel2orkes-users-{env}):
  PK: userId (str)  — "{provider}:{providerUserId}"  e.g. "google:1234567"
  GSI: apiKey-index on apiKey (str)

User record fields:
  userId, email, name, provider, apiKey, tier, creditsTotal, creditsUsed, createdAt
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

# ── Config ─────────────────────────────────────────────────────────────────────

ENV = os.getenv("BPEL2ORKES_ENV", "local")
TABLE_NAME = f"bpel2orkes-users-{ENV}"
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")

TIERS = {
    "free":      {"credits": 3,   "price": 0},
    "developer": {"credits": 30,  "price": 10},
    "starter":   {"credits": None, "price": 49},  # None = unlimited
}

FREE_CREDITS = 3


# ── DynamoDB client ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _table():
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return ddb.Table(TABLE_NAME)


def _table_exists() -> bool:
    try:
        _table().load()
        return True
    except Exception:
        return False


# ── User operations ────────────────────────────────────────────────────────────

def _new_api_key(tier: str = "free") -> str:
    prefix = {"free": "bpel2_free_", "developer": "bpel2_dev_", "starter": "bpel2_start_"}.get(tier, "bpel2_")
    return prefix + secrets.token_urlsafe(16)


def get_or_create_user(provider: str, provider_user_id: str, email: str, name: str) -> dict:
    """Upsert a user by OAuth identity. Returns the full user record."""
    user_id = f"{provider}:{provider_user_id}"
    table = _table()

    resp = table.get_item(Key={"userId": user_id})
    if "Item" in resp:
        return resp["Item"]

    # New user — issue free API key
    api_key = _new_api_key("free")
    user = {
        "userId": user_id,
        "email": email,
        "name": name,
        "provider": provider,
        "apiKey": api_key,
        "tier": "free",
        "creditsTotal": FREE_CREDITS,
        "creditsUsed": 0,
        "createdAt": int(time.time()),
    }
    table.put_item(Item=user)
    return user


def get_user_by_api_key(api_key: str) -> Optional[dict]:
    """Look up a user by their API key via the GSI."""
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


def increment_usage(user_id: str) -> dict:
    """Atomically increment creditsUsed. Returns updated user record."""
    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="SET creditsUsed = creditsUsed + :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="ALL_NEW",
    )
    return resp["Attributes"]


def upgrade_user(user_id: str, tier: str) -> dict:
    """Upgrade user tier and reset/add credits (called from Stripe webhook)."""
    credits_total = TIERS[tier]["credits"]  # None = unlimited
    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="SET tier = :tier, creditsTotal = :ct, apiKey = :key",
        ExpressionAttributeValues={
            ":tier": tier,
            ":ct": credits_total if credits_total is not None else "unlimited",
            ":key": _new_api_key(tier),
        },
        ReturnValues="ALL_NEW",
    )
    return resp["Attributes"]


# ── Quota middleware helpers ───────────────────────────────────────────────────

def check_quota(user: dict) -> None:
    """Raise 429 if user has exhausted their credits. Unlimited tiers always pass."""
    if user["tier"] == "starter":
        return
    credits_total = user.get("creditsTotal")
    if credits_total == "unlimited":
        return
    used = int(user.get("creditsUsed", 0))
    total = int(credits_total or 0)
    if used >= total:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "message": f"You've used all {total} conversions on the {user['tier']} plan.",
                "creditsUsed": used,
                "creditsTotal": total,
                "upgradeUrl": "https://bpel2orkes.kshetra.studio/dashboard",
            },
        )


# ── FastAPI dependency: resolve caller from X-Api-Key or session ──────────────

async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> dict:
    """
    FastAPI dependency. Resolves caller from X-Api-Key header.
    Raises 401 if missing/invalid, 429 if over quota.
    """
    if not _table_exists():
        # Auth not yet set up (local dev without DynamoDB) — allow through
        return {"userId": "local", "tier": "starter", "creditsUsed": 0, "creditsTotal": "unlimited"}

    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "missing_api_key",
                "message": "Sign in at https://bpel2orkes.kshetra.studio to get your free API key, then pass it as X-Api-Key header.",
            },
        )

    user = get_user_by_api_key(x_api_key)
    if not user:
        raise HTTPException(status_code=401, detail={"error": "invalid_api_key", "message": "API key not found."})

    check_quota(user)
    return user


async def optional_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> Optional[dict]:
    """Like require_api_key but returns None instead of raising if no key provided."""
    if not x_api_key or not _table_exists():
        return None
    user = get_user_by_api_key(x_api_key)
    if user:
        check_quota(user)
    return user
