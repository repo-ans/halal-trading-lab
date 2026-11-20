"""Stage H item 3. Pure -- no DB. Exercises app.auth._decode/current_user directly
with real, locally-signed JWTs (HS256 legacy-Supabase path), not the dependency
override every other route test uses -- overriding current_user would bypass the
exact logic this file is supposed to prove."""
import time
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import current_user
from app.config import get_settings

SECRET = "test-hs256-secret-at-least-32-bytes-long-xx"
SUPABASE_URL = "https://proj.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(get_settings(), "supabase_url", SUPABASE_URL)
    uid = str(uuid4())
    monkeypatch.setattr(get_settings(), "allowed_user_ids", uid)
    return uid


def _claims(sub: str, **overrides) -> dict:
    now = int(time.time())
    base = {"sub": sub, "aud": "authenticated", "iss": ISSUER,
            "iat": now, "exp": now + 3600, "email": "t@example.com"}
    base.update(overrides)
    return base


def _claims_missing(sub: str, *missing: str) -> dict:
    claims = _claims(sub)
    for key in missing:
        del claims[key]
    return claims


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _call(token: str):
    return current_user(_creds(token))


def test_valid_token_is_accepted(_configured):
    token = jwt.encode(_claims(_configured), SECRET, algorithm="HS256")
    user = _call(token)
    assert str(user.id) == _configured


def test_expired_token_is_rejected(_configured):
    token = jwt.encode(_claims(_configured, exp=int(time.time()) - 10), SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_wrong_audience_is_rejected(_configured):
    token = jwt.encode(_claims(_configured, aud="something-else"), SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_wrong_issuer_is_rejected(_configured):
    token = jwt.encode(_claims(_configured, iss="https://evil.example.com/auth/v1"),
                       SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_alg_none_is_rejected(_configured):
    # A classic bypass attempt: an unsigned token whose header claims alg="none".
    # PyJWT's decode() only accepts algorithms explicitly passed in `algorithms=`
    # (["HS256"] here) -- "none" is never in that list, so this must fail before
    # any signature check even happens.
    token = jwt.encode(_claims(_configured), key="", algorithm="none")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_tampered_signature_is_rejected(_configured):
    token = jwt.encode(_claims(_configured), SECRET, algorithm="HS256")
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{header}.{payload}.{tampered_signature}"
    with pytest.raises(HTTPException) as exc:
        _call(tampered)
    assert exc.value.status_code == 401


def test_tampered_payload_is_rejected(_configured):
    """Swapping `sub` to someone else's id, keeping the original signature, must
    fail -- the signature covers header+payload together, not just the header."""
    token = jwt.encode(_claims(_configured), SECRET, algorithm="HS256")
    header, _, signature = token.split(".")
    other_token = jwt.encode(_claims(str(uuid4())), SECRET, algorithm="HS256")
    _, other_payload, _ = other_token.split(".")
    forged_token = f"{header}.{other_payload}.{signature}"
    with pytest.raises(HTTPException) as exc:
        _call(forged_token)
    assert exc.value.status_code == 401


# --- review finding: options={"require": [...]} -- claim PRESENCE, not just
# validity when present. A validly-SIGNED token that simply omits one of these
# claims must still be rejected, on both verification branches. ---

@pytest.mark.parametrize("missing_claim", ["exp", "sub", "aud", "iss"])
def test_token_missing_a_required_claim_is_rejected(_configured, missing_claim):
    token = jwt.encode(_claims_missing(_configured, missing_claim), SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def _rsa_jwks_fixture(monkeypatch):
    """Forces the JWKS/RS256 branch (instead of the legacy-HS256 branch every
    other test in this file uses) and returns the private key to sign tokens
    with. Factored out so more than one test can exercise that branch cheaply."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    import app.auth as auth_module

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", "")  # force the JWKS branch
    monkeypatch.setattr(get_settings(), "supabase_url", SUPABASE_URL)

    class _FakeSigningKey:
        key = private_key.public_key()

    monkeypatch.setattr(auth_module, "_jwks_client",
                        lambda url: type("C", (), {
                            "get_signing_key_from_jwt": lambda self, token: _FakeSigningKey(),
                        })())
    return private_key


def test_jwks_path_also_enforces_required_claims(monkeypatch, _configured):
    private_key = _rsa_jwks_fixture(monkeypatch)
    token = jwt.encode(_claims_missing(_configured, "sub"), private_key, algorithm="RS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_jwks_path_also_rejects_expired_tokens(monkeypatch, _configured):
    private_key = _rsa_jwks_fixture(monkeypatch)
    token = jwt.encode(_claims(_configured, exp=int(time.time()) - 10), private_key, algorithm="RS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_jwks_path_also_rejects_wrong_issuer(monkeypatch, _configured):
    private_key = _rsa_jwks_fixture(monkeypatch)
    token = jwt.encode(_claims(_configured, iss="https://evil.example.com/auth/v1"),
                       private_key, algorithm="RS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401


def test_jwks_path_also_rejects_wrong_audience(monkeypatch, _configured):
    private_key = _rsa_jwks_fixture(monkeypatch)
    token = jwt.encode(_claims(_configured, aud="something-else"), private_key, algorithm="RS256")
    with pytest.raises(HTTPException) as exc:
        _call(token)
    assert exc.value.status_code == 401
