"""Antigravity 周配额与账号用量解析。"""
from __future__ import annotations

from typing import Any
import httpx


def parse_quota(data: Any) -> dict[str, Any]:
    """解析 retrieveUserQuotaSummary 响应，提取模型组配额。"""
    if not isinstance(data, dict):
        return {"ok": False, "error": "quota 响应格式异常"}

    raw_groups = data.get("groups") or []
    parsed_groups: list[dict[str, Any]] = []
    gemini_group: dict[str, Any] | None = None

    for grp in raw_groups:
        grp_name = grp.get("displayName") or "Models"
        grp_desc = grp.get("description") or ""
        weekly_bucket: dict[str, Any] | None = None
        five_hour_bucket: dict[str, Any] | None = None

        for bucket in grp.get("buckets", []):
            bid = bucket.get("bucketId") or ""
            win = bucket.get("window") or ""
            frac = bucket.get("remainingFraction")
            b_info = {
                "bucket_id": bid,
                "display_name": bucket.get("displayName") or ("Weekly Limit Remaining" if win == "weekly" else "Five Hour Limit Remaining"),
                "window": win,
                "remaining_fraction": float(frac) if frac is not None else 1.0,
                "remaining_percent": round(float(frac) * 100, 2) if frac is not None else 100.0,
                "reset_time": bucket.get("resetTime") or "",
                "description": bucket.get("description") or "",
            }
            if win == "weekly" or "weekly" in bid:
                weekly_bucket = b_info
            elif win == "5h" or "5h" in bid:
                five_hour_bucket = b_info

        g_data = {
            "display_name": grp_name,
            "description": grp_desc,
            "weekly": weekly_bucket,
            "five_hour": five_hour_bucket,
        }
        parsed_groups.append(g_data)
        if "gemini" in grp_name.lower():
            gemini_group = g_data

    # 基准配额优先采用 Gemini 模型组，若无则使用首个模型组
    primary = gemini_group or (parsed_groups[0] if parsed_groups else None)

    return {
        "ok": True,
        "groups": parsed_groups,
        "weekly": primary.get("weekly") if primary else None,
        "five_hour": primary.get("five_hour") if primary else None,
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
