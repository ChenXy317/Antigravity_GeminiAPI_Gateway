"""网关服务端点集成测试。"""
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "models" in data


def test_models_auth():
    # 无 Key 应返回 401
    r_unauth = client.get("/v1/models")
    assert r_unauth.status_code == 401

    # 携带当前配置的 local_api_key
    from app.config import config_manager
    current_key = config_manager.config.server.local_api_key or "sk-local"
    r_auth = client.get("/v1/models", headers={"Authorization": f"Bearer {current_key}"})
    assert r_auth.status_code == 200
    data = r_auth.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0

    # 验证单模型查询端点
    r_single = client.get("/v1/models/gemini-3.8-flash-high", headers={"Authorization": f"Bearer {current_key}"})
    assert r_single.status_code == 200
    assert r_single.json()["id"] == "gemini-3.8-flash-high"


def test_model_alias_resolution():
    """验证常用 OpenAI 别名正确映射。"""
    from app.config import config_manager, resolve_model_name
    cfg = config_manager.config.server
    assert resolve_model_name("gpt-4o", cfg) == "gemini-3.8-flash-high"
    assert resolve_model_name("claude-3-5-sonnet", cfg) == "claude-sonnet-4-6"
    assert resolve_model_name("o1", cfg) == "gemini-pro-agent"
    assert resolve_model_name("gemini-3.1-pro-high", cfg) == "gemini-pro-agent"


def test_legacy_functions_compatibility():
    """验证旧版 functions 字段自动升级兼容。"""
    from app.transform import to_ir
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "查天气"}],
        "functions": [
            {
                "name": "get_weather",
                "description": "获取天气",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
    }
    ir = to_ir("chat", body)
    assert ir.tools is not None
    assert len(ir.tools) == 1
    assert ir.tools[0].name == "get_weather"


def test_manage_status_localhost():
    # 本机默认情况下无需 admin key 即可查看状态
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "antigravity" in data
    assert "models" in data
    assert "model_aliases" in data
    assert data["admin_auth"] is False


def test_update_config_empty_upstream_fallback():
    # 模拟前端保存配置时 upstream_base_url 为空或未填，后端应自动保底不报错
    resp = client.put("/api/config", json={"server": {"upstream_base_url": ""}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["server"]["upstream_base_url"].startswith("http")


def test_parse_quota_groups():
    """验证配额多模型组解析与 Gemini 基准配额提取。"""
    from app.quota import parse_quota

    sample_data = {
        "groups": [
            {
                "displayName": "Gemini Models",
                "buckets": [
                    {"bucketId": "gemini-weekly", "window": "weekly", "remainingFraction": 0.92, "resetTime": "2026-03-30T10:00:00Z"},
                    {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0.61},
                ],
            },
            {
                "displayName": "Claude and GPT models",
                "buckets": [
                    {"bucketId": "3p-weekly", "window": "weekly", "remainingFraction": 0.993},
                    {"bucketId": "3p-5h", "window": "5h", "remainingFraction": 0.989},
                ],
            },
        ]
    }
    result = parse_quota(sample_data)
    assert result["ok"] is True
    assert len(result["groups"]) == 2
    assert result["weekly"]["remaining_percent"] == 92.0
    assert result["five_hour"]["remaining_percent"] == 61.0
    assert result["groups"][1]["weekly"]["remaining_percent"] == 99.3


def test_upstream_error_detection():
    """验证上游配额耗尽与算力枯竭错误识别。"""
    from app.upstream import UpstreamHTTPError
    import json

    e429 = UpstreamHTTPError(429, json.dumps({"error": {"message": "You have exhausted your capacity on this model. Your quota will reset after 4h59m57s."}}).encode())
    assert e429.is_quota_exhausted is True
    assert e429.is_capacity_exhausted is False

    e503 = UpstreamHTTPError(503, json.dumps({"error": {"message": "No capacity available for model gemini-2.5-pro on the server"}}).encode())
    assert e503.is_quota_exhausted is False
    assert e503.is_capacity_exhausted is True

    e400 = UpstreamHTTPError(400, json.dumps({"error": {"message": "Invalid request"}}).encode())
    assert e400.is_quota_exhausted is False
    assert e400.is_capacity_exhausted is False


def test_fallback_non_stream(monkeypatch):
    """验证非流式请求遇到上游无算力时自动故障转移至备选模型。"""
    from unittest.mock import AsyncMock
    from app.upstream import UpstreamHTTPError
    import app.server as server_mod
    from app.config import config_manager
    import json

    current_key = config_manager.config.server.local_api_key or "sk-local"
    call_models = []

    async def fake_post_non_stream(client, base_url, payload, token, timeout=120.0, user_agent=""):
        req_model = payload.get("model")
        call_models.append(req_model)
        if req_model == "gemini-2.5-pro":
            raise UpstreamHTTPError(503, json.dumps({"error": {"message": "No capacity available for model gemini-2.5-pro on the server"}}).encode())
        return 200, {
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": "Hello from fallback"}]},
                "finishReason": "STOP",
            }]
        }

    monkeypatch.setattr(server_mod, "post_non_stream", fake_post_non_stream)
    monkeypatch.setattr("app.server.antigravity_auth.get_token", AsyncMock(return_value="mock-token"))

    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {current_key}"},
        json={
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "Hello from fallback" in data["choices"][0]["message"]["content"]
    assert resp.headers.get("x-gateway-fallback-from") == "gemini-2.5-pro"
    assert resp.headers.get("x-gateway-model") == "gemini-3.8-flash-medium"
    assert call_models[0] == "gemini-2.5-pro"
    assert call_models[1] == "gemini-3.8-flash-medium"


def test_client_error_no_fallback(monkeypatch):
    """验证客户端参数错误（400）不触发无意义的备用模型降级。"""
    from unittest.mock import AsyncMock
    from app.upstream import UpstreamHTTPError
    import app.server as server_mod
    from app.config import config_manager
    import json

    current_key = config_manager.config.server.local_api_key or "sk-local"
    calls = 0

    async def fake_post_non_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise UpstreamHTTPError(400, json.dumps({"error": {"message": "Invalid argument format"}}).encode())

    monkeypatch.setattr(server_mod, "post_non_stream", fake_post_non_stream)
    monkeypatch.setattr("app.server.antigravity_auth.get_token", AsyncMock(return_value="mock-token"))

    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {current_key}"},
        json={
            "model": "gemini-3.8-flash-high",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )

    assert resp.status_code == 400
    assert calls == 1


def test_fallback_stream(monkeypatch):
    """验证流式请求遇到 429 配额用尽时自动切换备选模型建立流。"""
    from unittest.mock import AsyncMock
    from app.upstream import UpstreamHTTPError
    import app.server as server_mod
    from app.config import config_manager
    import json
    import httpx

    current_key = config_manager.config.server.local_api_key or "sk-local"
    call_models = []

    async def fake_open_stream(client, base_url, payload, token, user_agent="", connect_timeout=30.0):
        req_model = payload.get("model")
        call_models.append(req_model)
        if req_model == "gemini-3.8-flash-high":
            raise UpstreamHTTPError(429, json.dumps({"error": {"message": "You have exhausted your capacity on this model. Your quota will reset after 4h59m57s."}}).encode())
        
        # 成功响应模拟 SSE 流
        sse_body = (
            b'data: {"response": {"candidates": [{"content": {"role": "model", "parts": [{"text": "stream chunk"}]}}]}}\n\n'
        )

        class MockByteStream(httpx.AsyncByteStream):
            def __init__(self, data: bytes):
                self._data = data
            async def __aiter__(self):
                yield self._data

        return httpx.Response(200, stream=MockByteStream(sse_body))

    monkeypatch.setattr(server_mod, "open_upstream_stream", fake_open_stream)
    monkeypatch.setattr("app.server.antigravity_auth.get_token", AsyncMock(return_value="mock-token"))

    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {current_key}"},
        json={
            "model": "gemini-3.8-flash-high",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert resp.headers.get("x-gateway-fallback-from") == "gemini-3.8-flash-high"
    assert resp.headers.get("x-gateway-model") == "gemini-3.8-flash-medium"
    assert call_models[0] == "gemini-3.8-flash-high"
    assert call_models[1] == "gemini-3.8-flash-medium"
    assert "stream chunk" in resp.text





