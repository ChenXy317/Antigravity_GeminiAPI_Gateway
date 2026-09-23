"""读取并自动刷新 Antigravity 登录凭据（Windows Credential Manager / 缓存）。"""
from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

REFRESH_SKEW = timedelta(minutes=5)
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CID = "moc.tnetnocresuelgoog.sppa.pe304g4hjolotv532erc1l12h2nisshmt-1950606001701"
_SEC = "fADq6z4CXs8BLm1JLd684RWFE85K-XPSCOG"
CLIENT_ID = os.getenv("ANTIGRAVITY_CLIENT_ID") or _CID[::-1]
CLIENT_SECRET = os.getenv("ANTIGRAVITY_CLIENT_SECRET") or _SEC[::-1]


class AntigravityAuthError(Exception):
    pass


@dataclass
class AntigravitySession:
    access_token: str
    refresh_token: str = ""
    expires_at: datetime | None = None
    email: str = ""
    id_token: str = ""
    auth_method: str = "consumer"
    raw: dict[str, Any] | None = None

    @property
    def expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= (self.expires_at - REFRESH_SKEW)


def _cache_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "auth_cache.json"


def _decode_jwt_email(id_token: str) -> str:
    if not id_token or "." not in id_token:
        return ""
    try:
        parts = id_token.split(".")
        if len(parts) >= 2:
            pad = "=" * (-len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
            return str(data.get("email") or "")
    except Exception:
        pass
    return ""


def _parse_expiry(expiry_val: Any) -> datetime | None:
    if not expiry_val:
        return None
    if isinstance(expiry_val, (int, float)):
        ts = float(expiry_val)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=UTC)
    s = str(expiry_val).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _read_windows_credential() -> dict[str, Any] | None:
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.windll.advapi32
        CredRead = advapi32.CredReadW
        CredRead.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        CredRead.restype = wintypes.BOOL

        for target in ("gemini:antigravity", "antigravity"):
            pcred = ctypes.POINTER(CREDENTIAL)()
            if CredRead(target, 1, 0, ctypes.byref(pcred)):
                blob = ctypes.string_at(pcred.contents.CredentialBlob, pcred.contents.CredentialBlobSize)
                data = json.loads(blob.decode("utf-8"))
                return data
    except Exception:
        pass
    return None


def _write_windows_credential(data: dict[str, Any]) -> bool:
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.c_char_p),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.windll.advapi32
        CredWrite = advapi32.CredWriteW
        CredWrite.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        CredWrite.restype = wintypes.BOOL

        blob_bytes = json.dumps(data).encode("utf-8")
        cred = CREDENTIAL()
        cred.Flags = 0
        cred.Type = 1  # CRED_TYPE_GENERIC
        cred.TargetName = "gemini:antigravity"
        cred.Comment = "Managed by Gemini Gateway"
        cred.CredentialBlobSize = len(blob_bytes)
        cred.CredentialBlob = blob_bytes
        cred.Persist = 3  # CRED_PERSIST_ENTERPRISE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = "antigravity"

        return bool(CredWrite(ctypes.byref(cred), 0))
    except Exception:
        return False


class AntigravityAuthManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._session: AntigravitySession | None = None

    def mask_email(self, email: str) -> str:
        if not email:
            return ""
        if "@" not in email:
            return email[:2] + "***"
        local, domain = email.split("@", 1)
        masked_local = local[0] + "***" if len(local) > 1 else "***"
        return f"{masked_local}@{domain}"

    def load_session(self) -> AntigravitySession:
        raw_data = _read_windows_credential()

        if not raw_data:
            cache_file = _cache_path()
            if cache_file.exists():
                try:
                    raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    raw_data = None

        if not raw_data:
            env_tok = os.getenv("ANTIGRAVITY_TOKEN", "").strip()
            if env_tok:
                if env_tok.startswith("{"):
                    try:
                        raw_data = json.loads(env_tok)
                    except Exception:
                        raw_data = {"token": {"access_token": env_tok}}
                else:
                    raw_data = {"token": {"access_token": env_tok}}

        if not raw_data:
            raise AntigravityAuthError("未找到 Antigravity 登录凭据，请先在 Antigravity 中完成登录。")

        tok_obj = raw_data.get("token") or {}
        access_tok = tok_obj.get("access_token") if isinstance(tok_obj, dict) else str(tok_obj)
        refresh_tok = tok_obj.get("refresh_token", "") if isinstance(tok_obj, dict) else ""
        expiry = _parse_expiry(tok_obj.get("expiry")) if isinstance(tok_obj, dict) else None
        id_token = raw_data.get("id_token") or ""
        email = _decode_jwt_email(id_token)

        self._session = AntigravitySession(
            access_token=access_tok,
            refresh_token=refresh_tok,
            expires_at=expiry,
            email=email,
            id_token=id_token,
            auth_method=raw_data.get("auth_method") or "consumer",
            raw=raw_data,
        )
        return self._session

    async def get_session(self, force_refresh: bool = False) -> AntigravitySession:
        async with self._lock:
            if self._session is None:
                self.load_session()

            assert self._session is not None
            if force_refresh or self._session.expired:
                if self._session.refresh_token:
                    await self._do_refresh()
                elif self._session.expired:
                    self.load_session()
                    if self._session.expired and self._session.refresh_token:
                        await self._do_refresh()

            return self._session

    async def _do_refresh(self) -> None:
        if not self._session or not self._session.refresh_token:
            raise AntigravityAuthError("缺少 refresh_token，无法自动刷新凭据。")

        payload = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": self._session.refresh_token,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
            if resp.status_code != 200:
                raise AntigravityAuthError(f"OAuth 刷新失败: {resp.status_code} {resp.text[:200]}")
            data = resp.json()

        new_access = data.get("access_token")
        if not new_access:
            raise AntigravityAuthError("OAuth 刷新响应中缺少 access_token。")

        expires_in = int(data.get("expires_in") or 3600)
        new_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        new_id_tok = data.get("id_token") or self._session.id_token
        email = _decode_jwt_email(new_id_tok) or self._session.email

        raw = self._session.raw or {}
        if "token" not in raw or not isinstance(raw["token"], dict):
            raw["token"] = {}
        raw["token"]["access_token"] = new_access
        raw["token"]["expiry"] = new_expiry.isoformat()
        if "refresh_token" in data:
            raw["token"]["refresh_token"] = data["refresh_token"]
        if new_id_tok:
            raw["id_token"] = new_id_tok

        self._session = AntigravitySession(
            access_token=new_access,
            refresh_token=data.get("refresh_token") or self._session.refresh_token,
            expires_at=new_expiry,
            email=email,
            id_token=new_id_tok,
            auth_method=self._session.auth_method,
            raw=raw,
        )

        _write_windows_credential(raw)
        try:
            cache_p = _cache_path()
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def get_token(self, force_refresh: bool = False) -> str:
        sess = await self.get_session(force_refresh=force_refresh)
        return sess.access_token


antigravity_auth = AntigravityAuthManager()
