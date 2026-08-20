"""OAuth2 bootstrap (PKCE) for Salesforce Hosted MCP servers.

The Salesforce Hosted MCP servers (api.salesforce.com/platform/mcp/v1/<server>)
require an **External Client App** (not a legacy Connected App) configured
with:

  * OAuth scopes: ``api``, ``sfap_api``, ``refresh_token`` (e ``mcp_api``)
  * Require Proof Key for Code Exchange (PKCE): enabled
  * Issue JSON Web Token (JWT)-based access tokens for named users: enabled

External Client Apps do not accept plain-HTTP redirect URIs, so the flow
uses Salesforce's own success callback
(https://login.salesforce.com/services/oauth2/success) and a manual
copy-paste of the ``code``: open the authorization URL in the browser,
approve, land on the success page whose URL contains ``?code=...``, and
paste that URL (or just the code) back into the terminal.

The access token is never printed; the pipeline refreshes it automatically
via ``grant_type=refresh_token`` (and captures the rotated refresh token).

Library usage::

    from sf_mcp_auth.bootstrap import bootstrap
    tokens = bootstrap(client_id, client_secret, pasted="https://login.salesforce.com/services/oauth2/success?code=...&state=...")

CLI usage::

    sf-mcp-auth-bootstrap <client_id> <client_secret>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Callable

AUTHORIZE_URL = "https://login.salesforce.com/services/oauth2/authorize"
TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"
REDIRECT_URI = "https://login.salesforce.com/services/oauth2/success"
SCOPES = "api sfap_api mcp_api refresh_token"


def pkce_pair() -> tuple[str, str]:
    """Generate a (verifier, challenge) pair for the PKCE flow (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorization_url(
    client_id: str,
    *,
    scopes: str = SCOPES,
    state: str | None = None,
    verifier: str | None = None,
    challenge: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the authorization URL; returns (url, context).

    The context dict carries ``state``, ``verifier`` and ``challenge`` —
    keep ``verifier``/``state`` around for the token exchange and the
    state check. Pure function, safe to unit test.
    """
    if state is None:
        state = secrets.token_urlsafe(16)
    if verifier is None:
        verifier, generated = pkce_pair()
        if challenge is None:
            challenge = generated
    if challenge is None:
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "consent",
        }
    )
    return f"{AUTHORIZE_URL}?{params}", {"state": state, "verifier": verifier, "challenge": challenge}


def extract_code(pasted: str, expected_state: str | None = None) -> str:
    """Extract the ``code`` from a pasted URL (or a bare code).

    Validates the ``state`` when present in the pasted URL. Raises
    SystemExit with a human message on failure (CLI-style contract).
    """
    code_match = re.search(r"[?&#]code=([^&#]+)", pasted)
    state_match = re.search(r"[?&#]state=([^&#]+)", pasted)
    if code_match:
        code = urllib.parse.unquote(code_match.group(1))
        if expected_state is not None and state_match:
            actual_state = urllib.parse.unquote(state_match.group(1))
            if actual_state != expected_state:
                raise SystemExit("Falha: state nao confere (possivel CSRF).")
        return code
    if not any(ch in pasted for ch in ("?", "&", "#", "=")) and " " not in pasted.strip():
        return pasted.strip()
    raise SystemExit("Falha: nao encontrei o parametro code na entrada.")


def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    *,
    token_url: str = TOKEN_URL,
    opener: Callable[[urllib.request.Request], object] | None = None,
) -> dict:
    """Exchange the authorization code for tokens (PKCE grant).

    ``opener`` is injectable for tests (defaults to ``urllib.request.urlopen``).
    Raises SystemExit on HTTP failure, like the CLI contract.
    """
    payload = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        }
    ).encode()
    request = urllib.request.Request(
        token_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = opener or urllib.request.urlopen
    try:
        with opener(request) as response:  # type: ignore[attr-defined]
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise SystemExit(f"Token exchange failed: HTTP {exc.code}: {body[:400]}") from exc


def bootstrap(
    client_id: str,
    client_secret: str,
    *,
    pasted: str | None = None,
    open_browser: bool = True,
    token_url: str = TOKEN_URL,
    opener: Callable[[urllib.request.Request], object] | None = None,
) -> dict:
    """Full PKCE bootstrap flow. Returns the token dict (never prints secrets).

    ``pasted`` can be the full redirect URL (or the bare code) — injectable
    for tests; when None, reads from stdin.
    """
    url, ctx = build_authorization_url(client_id)
    if open_browser:
        webbrowser.open(url)
    if pasted is None:
        pasted = input(
            "\nCole aqui a URL final do navegador (ou so o code): "
        ).strip()
    code = extract_code(pasted, expected_state=ctx["state"])
    tokens = exchange_code(
        client_id, client_secret, code, ctx["verifier"], token_url=token_url, opener=opener
    )
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise SystemExit(f"Falha: sem refresh_token na resposta: {tokens}")
    return tokens


def main(argv: list[str] | None = None) -> int:
    """Console entry point (``sf-mcp-auth-bootstrap``)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_id", help="Consumer Key da External Client App")
    parser.add_argument("client_secret", help="Consumer Secret da External Client App")
    args = parser.parse_args(argv)

    tokens = bootstrap(args.client_id, args.client_secret)

    print("\n=== Tokens obtidos ===")
    print(f"instance_url: {tokens.get('instance_url', 'N/A')}")
    print(f"scope: {tokens.get('scope', 'N/A')}")
    print(
        f"access_token (nao compartilhe, expira em ~2h): "
        f"{tokens['access_token'][:12]}..."
    )
    print("\nGrave o refresh token abaixo como secret (copie a linha inteira):")
    print(f"SF_REFRESH_TOKEN = {tokens['refresh_token']}")
    print("\nDepois rode:")
    print("  gh secret set SF_REFRESH_TOKEN <<< '<o refresh token acima>'")
    print("  gh secret set SF_CLIENT_ID <<< '<o consumer key>'")
    print("  gh secret set SF_CLIENT_SECRET <<< '<o consumer secret>'")
    print("\nE dispare o collect:")
    print("  gh workflow run collect.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
