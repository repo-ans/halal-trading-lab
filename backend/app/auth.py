"""Verifies Supabase-issued JWTs. Supabase Auth owns users, passwords and sessions;
this module only validates the access token the frontend sends."""
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    id: UUID
    email: str | None


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json")


def _decode(token: str) -> dict:
    s = get_settings()
    if s.supabase_jwt_secret:  # legacy HS256 projects
        return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"],
                          audience="authenticated")
    if not s.supabase_url:
        raise jwt.InvalidTokenError("auth is not configured (SUPABASE_URL)")
    key = _jwks_client(s.supabase_url).get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> AuthUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        claims = _decode(creds.credentials)
        return AuthUser(id=UUID(claims["sub"]), email=claims.get("email"))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
