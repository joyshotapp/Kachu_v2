"""
欄位級加密工具（A-6）。

使用 Fernet 對稱加密（AES-128-CBC + HMAC-SHA256）保護存入 DB 的敏感欄位，
例如 LINE channel_secret / channel_access_token。

設定方式：
    在 .env 設定 FIELD_ENCRYPTION_KEY，值為 Fernet key（base64 url-safe 32 bytes）。
    生成指令：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

未設定時退化為明文（dev/test 環境可接受）。
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

_PREFIX = "fernet:"


def _get_fernet(key: str):  # type: ignore[return]
    """建立 Fernet 實例；若 key 無效或套件不存在則回 None。"""
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        logger.warning("FIELD_ENCRYPTION_KEY invalid, falling back to plaintext: %s", exc)
        return None


def encrypt_field(value: str, key: str) -> str:
    """
    加密 value。
    - key 有效 → 回傳 "fernet:{base64_token}"
    - key 空或無效 → 回傳原始 value（不加密）
    """
    if not value:
        return value
    fernet = _get_fernet(key)
    if fernet is None:
        return value
    token = fernet.encrypt(value.encode("utf-8"))
    return _PREFIX + base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_field(value: str, key: str) -> str:
    """
    解密 value。
    - 有 "fernet:" prefix → 嘗試解密
    - 無 prefix → 視為明文原樣回傳（相容舊資料）
    """
    if not value or not value.startswith(_PREFIX):
        return value
    fernet = _get_fernet(key)
    if fernet is None:
        # key 無效，回傳去掉 prefix 的 base64 raw（避免崩潰但確實無法解密）
        logger.warning("Cannot decrypt field: FIELD_ENCRYPTION_KEY not set or invalid")
        return value
    try:
        raw = base64.urlsafe_b64decode(value[len(_PREFIX):])
        return fernet.decrypt(raw).decode("utf-8")
    except Exception as exc:
        logger.error("Field decryption failed: %s", exc)
        return value
