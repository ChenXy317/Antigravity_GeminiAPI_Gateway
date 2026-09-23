"""Antigravity 周配额与账号用量解析。"""
from __future__ import annotations

from typing import Any
import httpx


def parse_quota(data: Any) -> dict[str, Any]:
    """解析 retrieveUserQuotaSummary 响应。"""
    if not isinstance(data, dict):
        return {"ok": False, "error": "quota 响应格式异常"}

    groups = data.get("groups") or []
    weekly_info: dict[str, Any] | None = None
    five_hour_info: dict[str, Any] | None = None

    for grp in groups:
        for bucket in grp.get("buckets", []):
            bid = bucket.get("bucketId") or ""
            if bid == "gemini-weekly" or bucket.get("window") == "weekly":
                frac = bucket.get("remainingFraction")
                weekly_info = {
                    "bucket_id": bid,
                    "display_name": bucket.get("displayName") or "Weekly Limit Remaining",
                    "remaining_fraction": float(frac) if frac is not None else 1.0,
                    "remaining_percent": round(float(frac) * 100, 2) if frac is not None else 100.0,
                    "reset_time": bucket.get("resetTime") or "",
                    "description": bucket.get("description") or "",
                }
            elif bid == "gemini-5h" or bucket.get("window") == "5h":
                frac = bucket.get("remainingFraction")
                five_hour_info = {
                    "bucket_id": bid,
                    "display_name": bucket.get("displayName") or "Five Hour Limit Remaining",
                    "remaining_fraction": float(frac) if frac is not None else 1.0,
                    "remaining_percent": round(float(frac) * 100, 2) if frac is not None else 100.0,
                    "reset_time": bucket.get("resetTime") or "",
                    "description": bucket.get("description") or "",
                }

    return {
        "ok": True,
        "weekly": weekly_info,
        "five_hour": five_hour_info,
        "description": data.get("description") or "",
    }


def parse_tier(data: Any) -> dict[str, Any]:
    """解析 loadCodeAssist 响应中的套餐信息。"""
    if not isinstance(data, dict):
        return {}
    current = data.get("currentTier") or {}
    paid = data.get("paidTier") or {}
    return {
        "tier_id": current.get("id") or "",
        "tier_name": current.get("name") or "Antigravity",
        "tier_description": current.get("description") or "",
        "paid_tier_name": paid.get("name") or "",
        "project": data.get("cloudaicompanionProject") or "aicode-consumers",
    }


async def fetch_quota_and_tier(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    user_agent: str = "antigravity/2.16.0",
) -> dict[str, Any]:
    """并发请求配额与层级信息。"""
    root = base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }

    quota_url = root + "/v1internal:retrieveUserQuotaSummary"
    tier_url = root + "/v1internal:loadCodeAssist"

    quota_res: dict[str, Any] = {"ok": False}
    tier_res: dict[str, Any] = {}

    try:
        q_resp = await client.post(quota_url, json={}, headers=headers, timeout=10.0)
        if q_resp.status_code == 200:
            quota_res = parse_quota(q_resp.json())
        else:
            quota_res = {"ok": False, "status": q_resp.status_code, "error": q_resp.text[:200]}
    except Exception as e:
        quota_res = {"ok": False, "error": str(e)}

    try:
        t_resp = await client.post(tier_url, json={}, headers=headers, timeout=10.0)
        if t_resp.status_code == 200:
            tier_res = parse_tier(t_resp.json())
    except Exception:
        pass

    return {
        "quota": quota_res,
        "tier": tier_res,
    }
