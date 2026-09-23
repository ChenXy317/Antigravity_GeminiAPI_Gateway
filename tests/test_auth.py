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
