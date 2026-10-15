import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from pydantic import BaseModel

from app.config import get_settings

settings = get_settings()


class TokenPayload(BaseModel):
    sub: str
    type: Literal["access", "refresh"]
    jti: str
    iat: int
    exp: int


class DecodedTokenError(Exception):
    pass


def _encode(user_id: str, token_type: Literal["access", "refresh"], ttl: timedelta, jti: str | None = None) -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + ttl
    jti = jti or str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def create_access_token(user_id: str) -> tuple[str, datetime]:
    token, _jti, expires_at = _encode(
        user_id, "access", timedelta(minutes=settings.jwt_access_token_ttl_minutes)
    )
    return token, expires_at


def create_refresh_token(user_id: str, jti: str | None = None) -> tuple[str, str, datetime]:
    return _encode(user_id, "refresh", timedelta(days=settings.jwt_refresh_token_ttl_days), jti=jti)


def decode_token(token: str, expected_type: Literal["access", "refresh"]) -> TokenPayload:
    try:
        raw = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        payload = TokenPayload(**raw)
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise DecodedTokenError("invalid token") from exc

    if payload.type != expected_type:
        raise DecodedTokenError("unexpected token type")
    return payload
