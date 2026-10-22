"""凭证工具:Fernet 对称加密存储,界面只显示掩码。"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet | None:
    key = get_settings().credential_key
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def mask_credential(credential: str) -> str:
    if not credential:
        return ""
    if len(credential) <= 4:
        return "****"
    return f"{credential[:2]}{'*' * 6}{credential[-2:]}"


def encrypt_credential(credential: str) -> str:
    if not credential:
        return ""
    f = _fernet()
    if f is None:  # 开发环境未配置密钥时明文存储并标记(仅 dev)
        return f"plain::{credential}"
    return f"fernet::{f.encrypt(credential.encode()).decode()}"


def decrypt_credential(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("plain::"):
        return stored[len("plain::"):]
    if stored.startswith("fernet::"):
        f = _fernet()
        if f is None:
            return ""
        try:
            return f.decrypt(stored[len("fernet::"):].encode()).decode()
        except InvalidToken:
            return ""
    return stored
