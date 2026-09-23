import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import jwt

from app.config import get_settings

settings = get_settings()

# bcrypt only looks at the first 72 bytes of a password, and bcrypt>=5 raises on
# longer input. The schema layer rejects longer passwords; truncating here is a
# defensive guard so hashing never crashes on legacy callers.
_BCRYPT_MAX_BYTES = 72


def _pw_bytes(plain_password: str) -> bytes:
    return plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(plain_password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain_password), hashed_password.encode("ascii"))
    except ValueError:
        return False


def _create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    now = datetime.now(timezone.utc)
    payload.update({"exp": now + expires_delta, "iat": now})
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str, role: str, org_id: str | None = None) -> str:
    claims: dict[str, Any] = {"sub": subject, "role": role, "type": "access"}
    if org_id:
        claims["org"] = org_id
    return _create_token(claims, timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(subject: str) -> str:
    return _create_token(
        {"sub": subject, "type": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """
    Raises JWTError on invalid/expired tokens.
    Callers should catch and convert to HTTP 401.
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


# ── Device API keys ───────────────────────────────────────────────────────────
#
# Format: vw_<prefix>_<secret>. The prefix is stored in clear (indexed, unique)
# so lookup is a single query; only a SHA-256 of the full key is stored. A fast
# hash is appropriate here because the key has 256 bits of entropy — unlike a
# human password it cannot be brute-forced offline.

def generate_device_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hex)."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full_key = f"vw_{prefix}_{secret}"
    return full_key, prefix, hash_api_key(full_key)


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def parse_api_key_prefix(full_key: str) -> str | None:
    parts = full_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "vw" or not parts[1]:
        return None
    return parts[1]


def api_key_matches(full_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(full_key), stored_hash)


# ── Artifact signing (ML model bundles) ───────────────────────────────────────
#
# Model bundles are serialized with joblib (pickle under the hood). Unpickling
# untrusted bytes executes arbitrary code, so every artifact is HMAC-signed with
# SECRET_KEY and the signature is verified before deserialization. Anyone who
# can write to Redis or the model table but does not know SECRET_KEY cannot get
# code executed on the server.

_SIG_LEN = 32


def sign_blob(blob: bytes) -> bytes:
    sig = hmac.new(settings.secret_key.encode("utf-8"), blob, hashlib.sha256).digest()
    return sig + blob


def verify_blob(signed: bytes) -> bytes | None:
    if len(signed) <= _SIG_LEN:
        return None
    sig, blob = signed[:_SIG_LEN], signed[_SIG_LEN:]
    expected = hmac.new(settings.secret_key.encode("utf-8"), blob, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    return blob
