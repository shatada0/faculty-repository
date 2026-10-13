"""Окружение тестов: ключи Ed25519 во временной папке, dev-кука, фейковое хранилище."""

import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_keys = Path(tempfile.mkdtemp())
_private = Ed25519PrivateKey.generate()
(_keys / "jwt_private").write_bytes(
    _private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
)
(_keys / "jwt_public").write_bytes(
    _private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
)
# до импорта shared.config: настройки читаются один раз при импорте
os.environ |= {
    "AUTH_KEYS_DIR": str(_keys),
    "AUTH_COOKIE_NAME": "session_token",
    "AUTH_COOKIE_SECURE": "false",
    "AUTH_ALLOWED_ORIGINS": '["https://localhost"]',
    "DB_HOST": "localhost",
}
