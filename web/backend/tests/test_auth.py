"""ตรวจ app/auth.py: token แบบไหนผ่าน/ไม่ผ่าน (ใช้กุญแจปลอม ไม่ต่อ Cognito หรือ DynamoDB จริง)

รัน: cd web/backend && python -m pytest tests -q
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth

CLIENT = "test-client-id"
POOL = "ap-southeast-1_TEST"
ISSUER = f"https://cognito-idp.ap-southeast-1.amazonaws.com/{POOL}"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


def token(key=KEY, **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT, "token_use": "id", "iat": now, "exp": now + 3600,
              "sub": "user-1", "custom:doctor_id": "DR-001", **overrides}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "POOL_ID", POOL)
    monkeypatch.setattr(auth, "CLIENT_ID", CLIENT)
    monkeypatch.setattr(auth, "ISSUER", ISSUER)
    monkeypatch.setattr(auth, "jwks_client", lambda: FakeJwks())
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/who")
    def who(doctor_id: str = Depends(auth.current_doctor)):
        return {"doctor_id": doctor_id}

    return TestClient(app)


def call(client, tok=None):
    return client.get("/who", headers={"Authorization": f"Bearer {tok}"} if tok else {})


def test_valid_id_token_gives_doctor(client):
    r = call(client, token())
    assert r.status_code == 200 and r.json() == {"doctor_id": "DR-001"}


@pytest.mark.parametrize("bad", [
    None,                                            # ไม่ได้ส่ง token
    "not-a-jwt",
    token(key=OTHER_KEY),                            # ลายเซ็นไม่ใช่ของ user pool
    token(exp=int(time.time()) - 10),                # หมดอายุ
    token(aud="other-app"),                          # ออกให้ app อื่น
    token(iss="https://evil.example"),               # ไม่ได้ออกโดย user pool ของเรา
    token(token_use="access"),                       # access token ไม่ใช่ ID token
    token(token_use=None),
])
def test_rejected_tokens(client, bad):
    assert call(client, bad).status_code == 401


def test_account_without_doctor_id_is_forbidden(client):
    assert call(client, token(**{"custom:doctor_id": None})).status_code == 403


def test_config_in_cognito_mode(client):
    c = client.get("/api/auth/config").json()
    assert c["mode"] == "cognito" and c["client_id"] == CLIENT


def test_dev_mode_without_cognito_locally(monkeypatch):
    monkeypatch.setattr(auth, "POOL_ID", None)
    monkeypatch.setattr(auth, "ON_LAMBDA", False)
    assert auth.mode() == "dev" and auth.current_doctor(None) == "DR-001"


def test_lambda_without_cognito_refuses(monkeypatch):
    monkeypatch.setattr(auth, "POOL_ID", None)
    monkeypatch.setattr(auth, "ON_LAMBDA", True)
    with pytest.raises(Exception) as e:
        auth.current_doctor("Bearer anything")
    assert e.value.status_code == 503
