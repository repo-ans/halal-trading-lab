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


def _expected_issuer(s) -> str | None:
    # Supabase JWTs always carry "iss" = "<project url>/auth/v1", on both the
    # legacy HS256 path and the current JWKS path -- validated the same way on
    # either branch below. None (never validated) only when SUPABASE_URL itself
    # isn't configured, which README requires regardless of JWT verification mode.
    return f"{s.supabase_url.rstrip('/')}/auth/v1" if s.supabase_url else None


def _decode(token: str) -> dict:
    s = get_settings()
    issuer = _expected_issuer(s)
    # Stage H review finding: require these claims to be PRESENT, not just valid
    # when present -- a token missing "exp" entirely would otherwise never expire,
    # one missing "sub" would blow up later with a confusing KeyError instead of a
    # clean 401, and one missing "iss"/"aud" would skip that check rather than fail
    # it. Applied on both branches below, not just the one that happens to set
    # `issuer`, so neither verification path is weaker than the other.
    decode_kwargs = {"audience": "authenticated",
                     "options": {"require": ["exp", "sub", "aud", "iss"]}}
    if issuer is not None:
        decode_kwargs["issuer"] = issuer
    if s.supabase_jwt_secret:  # legacy HS256 projects
        return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], **decode_kwargs)
    if not s.supabase_url:
        raise jwt.InvalidTokenError("auth is not configured (SUPABASE_URL)")
    key = _jwks_client(s.supabase_url).get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["ES256", "RS256"], **decode_kwargs)


def is_admin(user: AuthUser) -> bool:
    """ADMIN_USER_IDS unset or empty means NO ONE is admin -- fail closed, never
    "everyone" or "no restriction" (admin_user_id_set is already empty in that case,
    so a plain containment check is correct without a separate empty-check here)."""
    return str(user.id).lower() in get_settings().admin_user_id_set


def is_allowed(user: AuthUser) -> bool:
    """Stage H allowlist. ALLOWED_USER_IDS unset/empty means NO ONE is allowed --
    fail closed, same shape as is_admin. An admin counts as allowed automatically:
    there's no scenario where someone should be able to write compliance data but
    be refused the rest of the app."""
    uid = str(user.id).lower()
    s = get_settings()
    return uid in s.allowed_user_id_set or uid in s.admin_user_id_set


def is_owner(user: AuthUser) -> bool:
    """Stage H licence guard (CLAUDE.md section 3): OWNER_USER_IDS unset/empty
    means NO ONE is the owner -- fail closed, same shape as is_admin/is_allowed.
    Deliberately NOT the same set as ADMIN_USER_IDS or ALLOWED_USER_IDS: being
    allowed to use the app, or to write compliance data, implies nothing about
    being licensed to see yfinance-sourced prices."""
    return str(user.id).lower() in get_settings().owner_user_id_set


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> AuthUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        claims = _decode(creds.credentials)
        user = AuthUser(id=UUID(claims["sub"]), email=claims.get("email"))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
    if not is_allowed(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user not allowed")
    return user


def require_admin(user: AuthUser = Depends(current_user)) -> AuthUser:
    if not is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin access required")
    return user
