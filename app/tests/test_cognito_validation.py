"""Cognito token validation after the python-jose -> PyJWT swap (2026-10-24).

python-jose is unmaintained and has an open HS256-forgery CVE with no fix
(CVE-2026-85394). These tests pin the behaviour the API relies on: RS256 only,
issuer and expiry enforced, and the app client checked for both ID tokens
(`aud`) and access tokens (`client_id`, which python-jose never checked).
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import cognito as C

ISSUER = "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_test"
CLIENT = "test-client"


class Settings:
    cognito_client_id = CLIENT
    cognito_issuer = ISSUER
    cognito_jwks_url = ISSUER + "/.well-known/jwks.json"


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": "k1", "alg": "RS256", "use": "sig"})
    return private, jwk


@pytest.fixture()
def service(keypair):
    svc = C.CognitoService.__new__(C.CognitoService)
    svc.settings = Settings()
    svc._jwks_cache = {"jwks": {"keys": [keypair[1]]}}
    svc._http_client = None
    return svc


def token(private, **overrides):
    claims = {"sub": "user-1", "iss": ISSUER, "exp": int(time.time()) + 600,
              "token_use": "id", "aud": CLIENT, "email": "a@example.com"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "k1"})


def validate(service, tok):
    return asyncio.run(service.validate_token(tok))


def test_valid_id_token(service, keypair):
    assert validate(service, token(keypair[0]))["sub"] == "user-1"


def test_valid_access_token_checks_client_id(service, keypair):
    tok = token(keypair[0], token_use="access", aud=None, client_id=CLIENT)
    assert validate(service, tok)["sub"] == "user-1"


@pytest.mark.parametrize("overrides", [
    {"aud": "other-client"},
    {"token_use": "access", "aud": None, "client_id": "other-client"},
    {"token_use": "access", "aud": None},
    {"token_use": "refresh"},
    {"iss": "https://evil.example.com"},
    {"exp": int(time.time()) - 60},
    {"sub": None},
])
def test_rejected_claims(service, keypair, overrides):
    with pytest.raises(ValueError):
        validate(service, token(keypair[0], **overrides))


def test_rejects_a_different_signing_key(service):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError):
        validate(service, token(other))


def test_rejects_hs256_signed_with_the_public_key(service, keypair):
    # The algorithm-confusion forgery python-jose was vulnerable to.
    public_pem = keypair[0].public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    claims = {"sub": "attacker", "iss": ISSUER, "exp": int(time.time()) + 600, "token_use": "id", "aud": CLIENT}
    header = {"kid": "k1", "alg": "HS256", "typ": "JWT"}

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    signing_input = b64(json.dumps(header).encode()) + b"." + b64(json.dumps(claims).encode())
    forged = signing_input + b"." + b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    with pytest.raises(ValueError):
        validate(service, forged.decode())
