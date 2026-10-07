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
    assert resolve_model_name("o1", cfg) == "gemini-3.1-pro-high"


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




