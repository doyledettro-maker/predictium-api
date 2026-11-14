"""Cognito tokens from more than one app client (the website and the mobile
app's "predictium-mobile"): ID tokens are checked on `aud`, access tokens on
`client_id` and `token_use`. Signature, issuer and expiry checks are as
before. Tokens are signed here with a throwaway RSA key; nothing reaches
Cognito."""

import asyncio
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from app.config import get_settings
from app.services.cognito import CognitoService

WEB = "web-client-id"
MOBILE = "mobile-client-id"
POOL = "us-east-2_testpool"
REGION = "us-east-2"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
).decode()
_public_pem = _key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
_public_jwk = {**jwk.construct(_public_pem, "RS256").to_dict(), "kid": "k1", "use": "sig"}


@pytest.fixture
def service(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cognito_user_pool_id", POOL)
    monkeypatch.setattr(s, "cognito_region", REGION)
    monkeypatch.setattr(s, "cognito_client_id", WEB)
    monkeypatch.setattr(s, "cognito_client_ids", "")
    svc = CognitoService()
    svc.settings = s

    async def jwks():
        return {"keys": [_public_jwk]}

    monkeypatch.setattr(svc, "_fetch_jwks", jwks)
    return svc


def token(**claims):
    base = {"sub": "user-1", "iss": ISSUER, "exp": int(time.time()) + 600, "email": "a@example.com"}
    base.update(claims)
    base = {k: v for k, v in base.items() if v is not None}
    return jwt.encode(base, _private_pem, algorithm="RS256", headers={"kid": "k1"})


def ok(svc, tok):
    return asyncio.run(svc.validate_token(tok))


def refused(svc, tok):
    with pytest.raises(ValueError):
        asyncio.run(svc.validate_token(tok))


def test_website_id_token_still_accepted_with_no_allowlist_set(service):
    assert ok(service, token(token_use="id", aud=WEB))["sub"] == "user-1"


def test_mobile_token_accepted_only_when_listed(service, monkeypatch):
    refused(service, token(token_use="id", aud=MOBILE))
    monkeypatch.setattr(service.settings, "cognito_client_ids", f"{WEB}, {MOBILE}")
    assert ok(service, token(token_use="id", aud=MOBILE))["aud"] == MOBILE
    assert ok(service, token(token_use="id", aud=WEB))["aud"] == WEB
    assert ok(service, token(token_use="access", client_id=MOBILE))["client_id"] == MOBILE


def test_unknown_client_rejected(service, monkeypatch):
    monkeypatch.setattr(service.settings, "cognito_client_ids", f"{WEB},{MOBILE}")
    refused(service, token(token_use="id", aud="someone-else"))


def test_access_token_checked_on_client_id(service):
    assert ok(service, token(token_use="access", client_id=WEB))["client_id"] == WEB
    refused(service, token(token_use="access", client_id="someone-else"))
    # An access token's aud is not what decides it.
    refused(service, token(token_use="access", aud=WEB, client_id="someone-else"))


def test_missing_token_use_or_aud_rejected(service):
    refused(service, token(aud=WEB))
    refused(service, token(token_use="refresh", aud=WEB))
    refused(service, token(token_use="id"))
    refused(service, token(token_use="access"))


def test_signature_issuer_and_expiry_still_checked(service):
    refused(service, token(token_use="id", aud=WEB, iss="https://cognito-idp.us-east-1.amazonaws.com/other"))
    refused(service, token(token_use="id", aud=WEB, exp=int(time.time()) - 10))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    forged = jwt.encode({"sub": "x", "iss": ISSUER, "exp": int(time.time()) + 600, "token_use": "id", "aud": WEB},
                        other, algorithm="RS256", headers={"kid": "k1"})
    refused(service, forged)


def test_allowlist_parsing(service, monkeypatch):
    s = service.settings
    assert s.cognito_client_id_allowlist == frozenset({WEB})
    monkeypatch.setattr(s, "cognito_client_ids", f" {WEB} ,, {MOBILE} ")
    assert s.cognito_client_id_allowlist == frozenset({WEB, MOBILE})
