"""AES-GCM 加密 ApiKey(§14.1 / D-C / R10)。

格式:``nonce(12) + ciphertext + tag``。``BID_APP_MASTER_KEY`` 为 64 位 hex
(32 字节,启动时 config.py 已校验)。

⚠️ R10:master_key 一旦丢失,所有 ApiKey 永久不可解密;rotate 流程见 §24.3。
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import settings

_KEY = bytes.fromhex(settings.bid_app_master_key)
_AES = AESGCM(_KEY)


def encrypt_api_key(plaintext: str) -> bytes:
    """加密一段明文 → ``nonce(12) + ct``。"""
    nonce = os.urandom(12)
    return nonce + _AES.encrypt(nonce, plaintext.encode("utf-8"), None)


def decrypt_api_key(blob: bytes) -> str:
    """从 ``nonce(12) + ct`` 解密回明文字符串。"""
    if len(blob) < 13:
        raise ValueError("encrypted blob too short (expect nonce(12)+ct)")
    nonce, ct = blob[:12], blob[12:]
    return _AES.decrypt(nonce, ct, None).decode("utf-8")
