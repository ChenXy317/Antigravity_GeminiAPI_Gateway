"""凭证与认证模块测试。"""
from app.antigravity_auth import antigravity_auth, AntigravitySession
from datetime import UTC, datetime, timedelta


def test_mask_email():
    assert antigravity_auth.mask_email("test@example.com") == "t***@example.com"
    assert antigravity_auth.mask_email("a@b.com") == "***@b.com"
    assert antigravity_auth.mask_email("") == ""


def test_session_expired():
    sess = AntigravitySession(
        access_token="abc",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert sess.expired is True

    sess_valid = AntigravitySession(
        access_token="abc",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert sess_valid.expired is False


def test_parse_expiry_timezone_safe():
    """验证时间解析支持 naive 格式与带时区格式。"""
    from app.antigravity_auth import _parse_expiry

    # 测试 naive ISO 字符串补齐 UTC 时区
    dt_naive = _parse_expiry("2026-10-07T22:20:23")
    assert dt_naive is not None
    assert dt_naive.tzinfo is not None

    # 测试带毫秒与时区偏移的 ISO 字符串
    dt_aware = _parse_expiry("2026-10-07T22:20:23.4559005+08:00")
    assert dt_aware is not None
    assert dt_aware.tzinfo is not None

    # 测试时间戳数字
    dt_ts = _parse_expiry(1791350000)
    assert dt_ts is not None
    assert dt_ts.tzinfo == UTC

    # 验证与 UTC 当前时间对比无异常
    assert isinstance(dt_naive > datetime.now(UTC), bool)


def test_oauth_client_credentials():
    """验证 Antigravity OAuth 客户端凭证格式有效。"""
    from app.antigravity_auth import CLIENT_ID, CLIENT_SECRET
    assert CLIENT_ID.endswith(".apps.googleusercontent.com")
    assert CLIENT_SECRET.startswith("GOCSPX-")
    assert "cre" in CLIENT_ID
    assert len(CLIENT_ID) == 73
    assert len(CLIENT_SECRET) == 35

