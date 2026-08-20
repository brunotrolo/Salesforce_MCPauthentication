"""Unit tests for sf_mcp_auth.bootstrap (PKCE flow, urlopen mocked)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from sf_mcp_auth import bootstrap as bs


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: str) -> None:
        super().__init__("http://example", code, "error", None, io.BytesIO(body.encode()))


def fake_opener_ok(payload: dict):
    return lambda request: FakeResponse(payload)


def fake_opener_error(code: int, body: str):
    def _opener(request):
        raise FakeHTTPError(code, body)

    return _opener


class TestBuildUrl:
    def test_authorization_url_includes_pkce_and_state(self):
        url, ctx = bs.build_authorization_url(
            "cid", state="fixedstate", verifier="fixedverifier", challenge="fixedchallenge"
        )
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        assert parsed.scheme == "https"
        assert parsed.path == "/services/oauth2/authorize"
        assert query["response_type"] == "code"
        assert query["client_id"] == "cid"
        assert query["redirect_uri"] == bs.REDIRECT_URI
        assert query["scope"] == bs.SCOPES
        assert query["state"] == "fixedstate"
        assert query["code_challenge"] == "fixedchallenge"
        assert query["code_challenge_method"] == "S256"
        assert query["prompt"] == "consent"
        assert ctx["verifier"] == "fixedverifier"

    def test_challenge_derived_when_not_given(self):
        url, ctx = bs.build_authorization_url("cid", state="s", verifier="abc")
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert query["code_challenge"] == ctx["challenge"]
        assert ctx["challenge"]


class TestExtractCode:
    def test_from_full_url(self):
        pasted = f"{bs.REDIRECT_URI}?code=THE_CODE&state=STATE123"
        assert bs.extract_code(pasted, expected_state="STATE123") == "THE_CODE"

    def test_from_bare_code(self):
        assert bs.extract_code("BARE_CODE") == "BARE_CODE"

    def test_state_mismatch_raises(self):
        pasted = f"{bs.REDIRECT_URI}?code=X&state=EVIL"
        with pytest.raises(SystemExit) as excinfo:
            bs.extract_code(pasted, expected_state="GOOD")
        assert "CSRF" in str(excinfo.value)

    def test_missing_code_raises(self):
        with pytest.raises(SystemExit) as excinfo:
            bs.extract_code("no code here")
        assert "code" in str(excinfo.value)


class TestExchange:
    def test_exchange_returns_tokens(self):
        tokens = {"access_token": "at", "refresh_token": "rt", "instance_url": "https://x"}
        result = bs.exchange_code("cid", "csecret", "CODE", "VERIFIER", opener=fake_opener_ok(tokens))
        assert result == tokens

    def test_exchange_http_error_raises_systemexit(self):
        with pytest.raises(SystemExit) as excinfo:
            bs.exchange_code("cid", "csecret", "CODE", "V", opener=fake_opener_error(400, "invalid_grant"))
        assert "HTTP 400" in str(excinfo.value)


class TestBootstrap:
    def test_full_flow_returns_tokens(self):
        tokens = {"access_token": "at", "refresh_token": "rt"}
        pasted = f"{bs.REDIRECT_URI}?code=CODE"
        result = bs.bootstrap(
            "cid", "csecret", pasted=pasted, open_browser=False, opener=fake_opener_ok(tokens)
        )
        assert result["access_token"] == "at"
        assert result["refresh_token"] == "rt"

    def test_no_refresh_token_fails(self):
        pasted = f"{bs.REDIRECT_URI}?code=CODE"
        with pytest.raises(SystemExit) as excinfo:
            bs.bootstrap(
                "cid", "csecret", pasted=pasted, open_browser=False,
                opener=fake_opener_ok({"access_token": "at"}),
            )
        assert "refresh_token" in str(excinfo.value)

    def test_state_is_checked(self):
        tokens = {"access_token": "at", "refresh_token": "rt"}
        _url, ctx = bs.build_authorization_url("cid", state="GOOD")
        pasted = f"{bs.REDIRECT_URI}?code=CODE&state=EVIL"
        with pytest.raises(SystemExit) as excinfo:
            bs.bootstrap(
                "cid", "csecret", pasted=pasted, open_browser=False,
                opener=fake_opener_ok(tokens),
            )
        assert "CSRF" in str(excinfo.value)