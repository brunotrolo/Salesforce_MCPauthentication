"""Unit tests for sf_mcp_auth.github_app_token (no network: jwt & urlopen mocked).

Covers the contract that kills the long-lived PAT:
- JWT payload has iat/exp/iss and a 10-min max TTL
- mint_installation_token POSTs to the right URL, returns body['token']
- invalid installation id surfaces as RuntimeError, not a silent None
- ImportError with a helpful hint when pyjwt is missing
"""

import json
from unittest import mock

import pytest


def test_make_jwt_payload_shape():
    """JWT contains iat/exp window and the App id as iss."""
    mod = pytest.importorskip("sf_mcp_auth.github_app_token")
    fake_jwt = mock.MagicMock()
    fake_jwt.encode.return_value = "SIGNED.JWT"
    with mock.patch.dict("sys.modules", {"jwt": fake_jwt}):
        out = mod._make_jwt(app_id=1234, private_key_pem="PEM", now=1_700_000_000)
    assert out == "SIGNED.JWT"
    payload, key = fake_jwt.encode.call_args.args
    assert key == "PEM"
    assert fake_jwt.encode.call_args.kwargs["algorithm"] == "RS256"
    assert payload["iss"] == 1234
    assert payload["iat"] == 1_700_000_000 - 60  # 60s skew tolerance
    assert payload["exp"] == 1_700_000_000 + 10 * 60  # 10 min window


def test_mint_installation_token_returns_ghs_token():
    """Happy path: exchange JWT for installation token."""
    mod = pytest.importorskip("sf_mcp_auth.github_app_token")
    fake_jwt = mock.MagicMock()
    fake_jwt.encode.return_value = "SIGNED.JWT"
    fake_resp = mock.MagicMock()
    fake_resp.__enter__ = mock.MagicMock(return_value=fake_resp)
    fake_resp.read.return_value = json.dumps({"token": "ghs_abc123"}).encode()

    class _Ctx:
        def __enter__(self):
            return fake_resp

        def __exit__(self, *a):
            return False

    with mock.patch.dict("sys.modules", {"jwt": fake_jwt}):
        fake_urlopen = mock.MagicMock(return_value=_Ctx())
        token = mod.mint_installation_token(
            app_id=42,
            private_key_pem="PEM",
            installation_id=99,
            now=1_700_000_000,
            _urlopen=fake_urlopen,
        )
    assert token == "ghs_abc123"
    assert fake_urlopen.call_count == 1
    req = fake_urlopen.call_args.args[0]
    assert req.full_url.endswith("/app/installations/99/access_tokens")
    assert req.get_method() == "POST"
    assert req.headers.get("Authorization") == "Bearer SIGNED.JWT"


def test_mint_installation_token_missing_token_raises():
    """If GH returns body without 'token', surface a RuntimeError with the body."""
    mod = pytest.importorskip("sf_mcp_auth.github_app_token")
    fake_jwt = mock.MagicMock()
    fake_jwt.encode.return_value = "SIGNED.JWT"
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps({"message": "Not Found"}).encode()

    class _Ctx:
        def __enter__(self):
            return fake_resp

        def __exit__(self, *a):
            return False

    with mock.patch.dict("sys.modules", {"jwt": fake_jwt}):
        fake_urlopen = mock.MagicMock(return_value=_Ctx())
        with pytest.raises(RuntimeError, match="no token"):
            mod.mint_installation_token(
                app_id=42,
                private_key_pem="PEM",
                installation_id=999,  # wrong install id, GH returns 404 body
                now=1_700_000_000,
                _urlopen=fake_urlopen,
            )


def test_missing_pyjwt_raises_import_error_with_hint():
    """If pyjwt is not installed, the helper surfaces a clear install hint."""
    mod = pytest.importorskip("sf_mcp_auth.github_app_token")
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "jwt":
            raise ImportError("simulated missing pyjwt")
        return real_import(name, *args, **kwargs)

    with mock.patch.object(builtins, "__import__", side_effect=_fake_import):
        with pytest.raises(ImportError, match="pyjwt\\[crypto\\]"):
            mod._make_jwt(app_id=1, private_key_pem="PEM")
