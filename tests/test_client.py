"""Unit tests for sf_mcp_auth.client (no network: urlopen is mocked)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from sf_mcp_auth import (
    RetryExhausted,
    SalesforceClient,
    SalesforceClientError,
    TokenBucket,
)


class FakeHeaders:
    def __init__(self, headers: dict | None = None, content_type: str = "application/json") -> None:
        self._headers = headers or {}
        self._ct = content_type

    def get(self, key: str, default=None):
        return self._headers.get(key, default)

    def get_content_type(self) -> str:
        return self._ct


class FakeResponse:
    def __init__(self, body: str, *, headers: dict | None = None, content_type: str = "application/json") -> None:
        self.headers = FakeHeaders(headers, content_type)
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: str) -> None:
        self.code = code
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body


def ok_json(payload) -> FakeResponse:
    return FakeResponse(json.dumps(payload))


def make_fake_urlopen(sequence: list):
    """Return (fake_urlopen, requests) consuming ``sequence`` in order.

    Entries that are exceptions are raised; everything else is returned.
    """
    calls: list = []
    seq = list(sequence)

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return fake_urlopen, calls


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_REFRESH_TOKEN", "SALESFORCE_MCP_URL", "SF_MAX_QPS"):
        monkeypatch.delenv(name, raising=False)
    yield


def build_client(**kwargs) -> SalesforceClient:
    base = dict(
        url="https://mcp.example.test/sobject-all",
        client_id="cid",
        client_secret="csecret",
        refresh_token="rt",
        retries=2,
        jitter=0,
        sleeper=lambda _s: None,
    )
    base.update(kwargs)
    return SalesforceClient(**base)


class TestAuthRefresh:
    def test_refresh_on_401_captures_rotated_token(self, clean_env, monkeypatch):
        client = build_client()
        client._session_id = "s1"
        fake, calls = make_fake_urlopen([
            FakeHTTPError(401, '{"message":"expired access token"}'),
            ok_json({"token_endpoint": "https://login.example.test/oauth2/token"}),
            ok_json({"access_token": "new_access", "refresh_token": "rotated_refresh"}),
            ok_json({"result": {"content": [{"type": "text", "text": "ok"}]}}),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        result = client.call_tool("soqlQuery", {"q": "SELECT Id FROM Account"})

        assert result == "ok"
        assert client.token == "new_access"
        assert client.refresh_token == "rotated_refresh"
        assert calls[-1].headers["Authorization"] == "Bearer new_access"
        assert len(calls) == 4

    def test_401_without_refresh_credentials_fails_fast(self, clean_env, monkeypatch):
        client = build_client(client_id="", refresh_token="")
        client._session_id = "s1"
        fake, calls = make_fake_urlopen([FakeHTTPError(401, "nope")])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert "401" in str(excinfo.value)
        assert len(calls) == 1

    def test_refresh_failure_raises(self, clean_env, monkeypatch):
        client = build_client()
        client._session_id = "s1"
        fake, _calls = make_fake_urlopen([
            FakeHTTPError(401, "expired"),
            ok_json({"token_endpoint": "https://login.example.test/oauth2/token"}),
            FakeHTTPError(400, '{"error":"invalid_grant"}'),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert "Token refresh failed: HTTP 400" in str(excinfo.value)

    def test_rotation_only_updates_when_different(self, clean_env, monkeypatch):
        client = build_client()
        client._session_id = "s1"
        fake, _calls = make_fake_urlopen([
            FakeHTTPError(401, "expired"),
            ok_json({"token_endpoint": "https://login.example.test/oauth2/token"}),
            ok_json({"access_token": "new_access"}),
            ok_json({"result": {"content": [{"type": "text", "text": "ok"}]}}),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        client.call_tool("soqlQuery", {"q": "x"})

        assert client.token == "new_access"
        assert client.refresh_token == "rt"  # no new refresh token in response


class TestRetries:
    def test_retries_on_transient_500(self, clean_env, monkeypatch):
        client = build_client(retries=2)
        client._session_id = "s1"
        fake, calls = make_fake_urlopen([
            FakeHTTPError(500, "boom"),
            FakeHTTPError(502, "boom"),
            ok_json({"result": {"content": [{"type": "text", "text": "ok"}]}}),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        result = client.call_tool("soqlQuery", {"q": "x"})
        assert result == "ok"
        assert len(calls) == 3

    def test_retry_exhausted_on_persistent_500(self, clean_env, monkeypatch):
        client = build_client(retries=2)
        client._session_id = "s1"
        fake, _calls = make_fake_urlopen([
            FakeHTTPError(500, "boom"),
            FakeHTTPError(500, "boom"),
            FakeHTTPError(500, "boom"),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(RetryExhausted):
            client.call_tool("soqlQuery", {"q": "x"})

    def test_non_retryable_400_fails_fast(self, clean_env, monkeypatch):
        client = build_client(retries=3)
        client._session_id = "s1"
        fake, calls = make_fake_urlopen([FakeHTTPError(400, "bad request")])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert excinfo.value.code == 400
        assert len(calls) == 1


class TestInitAndParse:
    def test_initialize_stores_session_id(self, clean_env, monkeypatch):
        client = build_client()
        fake, _calls = make_fake_urlopen([
            FakeResponse(
                json.dumps({"result": {"capabilities": {}}}),
                headers={"Mcp-Session-Id": "abc123"},
            ),
            FakeResponse("", content_type="application/json"),
            ok_json({"result": {"content": [{"type": "text", "text": "ok"}]}}),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        client.call_tool("soqlQuery", {"q": "x"})
        assert client._session_id == "abc123"

    def test_initialize_missing_capabilities_fails(self, clean_env, monkeypatch):
        client = build_client()
        fake, _calls = make_fake_urlopen([ok_json({"result": {"no-capabilities": True}})])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert "Unexpected initialize response" in str(excinfo.value)

    def test_discover_token_endpoint_missing(self, clean_env, monkeypatch):
        client = build_client()
        client._session_id = "s1"
        fake, _calls = make_fake_urlopen([
            FakeHTTPError(401, "expired"),
            ok_json({"other": "no endpoint here"}),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert "no token_endpoint" in str(excinfo.value)

    def test_mcp_tool_is_error_raises(self, clean_env, monkeypatch):
        client = build_client()
        client._session_id = "s1"
        fake, _calls = make_fake_urlopen([
            ok_json({"result": {"isError": True, "content": [{"type": "text", "text": "tool exploded"}]}}),
        ])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert "tool exploded" in str(excinfo.value)

    def test_unwrap_rpc_error(self):
        client = build_client()
        with pytest.raises(SalesforceClientError) as excinfo:
            client._unwrap_rpc({"error": {"code": -32601, "message": "method not found"}})
        assert "-32601" in str(excinfo.value)

    def test_parse_sse_response(self):
        body = 'data: {"jsonrpc":"2.0","result":{"ok":1}}\n\n'
        parsed = SalesforceClient._parse_response(body, "text/event-stream")
        assert parsed["result"]["ok"] == 1

    def test_parse_invalid_json_fails(self):
        client = build_client()
        with pytest.raises(SalesforceClientError):
            client._parse_response("not json", "application/json")


class TestRateLimit:
    def test_bucket_acquire_and_block(self):
        t = [0.0]

        def clock() -> float:
            return t[0]

        def advance(seconds: float) -> None:
            t[0] += seconds

        bucket = TokenBucket(rate=1.0, capacity=1.0, clock=clock, sleeper=lambda _s: None)
        assert bucket.acquire() is True  # initial capacity, no refill needed
        advance(0.1)  # only 0.1 token refilled < 1 needed
        assert bucket.acquire(block=True, timeout=0.0) is False

    def test_bucket_validation(self):
        with pytest.raises(ValueError):
            TokenBucket(rate=-1, capacity=1)
        with pytest.raises(ValueError):
            TokenBucket(rate=1, capacity=0)

    def test_rate_limit_error_from_client(self, clean_env, monkeypatch):
        t = [0.0]

        def fake_clock() -> float:
            return t[0]

        client = build_client(max_qps=1.0, bucket_timeout=0.0, sleeper=lambda _s: None)
        client._bucket._clock = fake_clock  # type: ignore[union-attr]
        client._bucket._last = 0.0  # type: ignore[union-attr]
        client._bucket._tokens = 0.0  # type: ignore[union-attr]
        fake, _calls = make_fake_urlopen([])
        monkeypatch.setattr(urllib.request, "urlopen", fake)

        with pytest.raises(SalesforceClientError) as excinfo:
            client.call_tool("soqlQuery", {"q": "x"})
        assert "Rate limit" in str(excinfo.value)