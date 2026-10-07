"""Antigravity 上游通信客户端（HTTP / SSE 流）。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx


class UpstreamHTTPError(Exception):
    def __init__(self, status_code: int, body: bytes):
        msg = f"上游 HTTP 错误: {status_code}"
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict) and err.get("message"):
                msg += f" - {err['message']}"
            elif isinstance(data, dict) and data.get("message"):
                msg += f" - {data['message']}"
        except Exception:
            pass
        super().__init__(msg)
        self.status_code = status_code
        self.body = body


class StreamTimeoutError(Exception):
    pass


def build_headers(token: str, user_agent: str = "antigravity/2.16.0") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }


async def post_non_stream(
    client: httpx.AsyncClient,
    base_url: str,
    payload: dict[str, Any],
    token: str,
    timeout: float = 120.0,
    user_agent: str = "antigravity/2.16.0",
) -> tuple[int, dict[str, Any]]:
    """向 Antigravity 发送非流式推理请求。"""
    url = base_url.rstrip("/") + "/v1internal:generateContent"
    headers = build_headers(token, user_agent)

    resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise UpstreamHTTPError(resp.status_code, resp.content)

    return resp.status_code, resp.json()


async def stream_upstream(
    client: httpx.AsyncClient,
    base_url: str,
    payload: dict[str, Any],
    token: str,
    user_agent: str = "antigravity/2.16.0",
    first_token_timeout: float = 90.0,
    read_idle_timeout: float = 120.0,
) -> AsyncIterator[bytes]:
    """向 Antigravity 发送流式推理请求并产生 SSE 字节流。"""
    url = base_url.rstrip("/") + "/v1internal:streamGenerateContent?alt=sse"
    headers = build_headers(token, user_agent)
    headers["Accept"] = "text/event-stream"

    req = client.build_request("POST", url, json=payload, headers=headers)
    resp = await client.send(req, stream=True)

    if resp.status_code != 200:
        body = await resp.aread()
        await resp.aclose()
        raise UpstreamHTTPError(resp.status_code, body)

    first = True
    aiter = resp.aiter_raw()
    try:
        while True:
            limit = first_token_timeout if first else read_idle_timeout
            with anyio.move_on_after(limit) as scope:
                try:
                    chunk = await anext(aiter)
                except StopAsyncIteration:
                    break
            if scope.cancelled_caught:
                raise StreamTimeoutError("首 token 超时" if first else "空闲读取超时")
            first = False
            if chunk:
                yield chunk
    finally:
        await resp.aclose()


async def fetch_upstream_models(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    user_agent: str = "antigravity/2.16.0",
) -> list[dict[str, str]]:
    """从 Antigravity 实时同步可用模型池。"""
    url = base_url.rstrip("/") + "/v1internal:fetchAvailableModels"
    headers = build_headers(token, user_agent)

    resp = await client.post(url, json={}, headers=headers, timeout=15.0)
    if resp.status_code != 200:
        raise UpstreamHTTPError(resp.status_code, resp.content)

    data = resp.json()
    models_dict = data.get("models") or {}
    out: list[dict[str, str]] = []
    for mid, info in models_dict.items():
        disp = info.get("displayName") or mid
        out.append({"id": mid, "display_name": disp})

    return out
