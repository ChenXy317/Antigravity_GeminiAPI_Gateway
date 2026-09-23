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



