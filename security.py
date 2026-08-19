import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from config import settings


def hash_password(password: str) -> str:
    iterations = 390000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        algorithm, iteration_text, salt, digest_text = hashed_password.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iteration_text),
    )
    return secrets.compare_digest(digest.hex(), digest_text)


def create_token(subject: str, expires_minutes: int, extra_claims: dict | None = None) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expire_at = now + timedelta(minutes=expires_minutes)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": expire_at,
        "jti": secrets.token_hex(8),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, expire_at.replace(tzinfo=None)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except ExpiredSignatureError as exc:
        raise ValueError("令牌已过期") from exc
    except InvalidTokenError as exc:
        raise ValueError("令牌无效") from exc
