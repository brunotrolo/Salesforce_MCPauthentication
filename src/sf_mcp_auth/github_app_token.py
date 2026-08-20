"""Mint GitHub App installation tokens - kill the long-lived PAT.

A Personal Access Token (PAT) is the weak link of any secret-rotation pipeline:
it never expires (unless we set a date), can leak once and be reused forever,
and GitHub refuses to rotate it via the API. The only way to eliminate it is
to authenticate as a **GitHub App**: the App holds a private key, the workflow
signs a short-lived JWT (10 min, RS256) every run, and exchanges it for an
installation token of 1 hour. Tokens are scoped (Actions: write) and born
single-use; nothing sensitive is ever at rest in a third-party store.

Requires the optional dependency `pyjwt` (RSA support included via
`pyjwt[crypto]`). Install as `pip install sf-mcp-auth[github-app]` and import
emptily otherwise (the rest of the package stays stdlib-only).

Setup you do ONCE on GitHub (see README/VALIDACAO for screenshots-free steps):
    1. Create a GitHub App (Settings -> Developer settings -> GitHub Apps
       -> New GitHub App)
    2. Repository permissions -> Actions: **Read and write** (only)
       (Metadata: Read-only is auto-added)
    3. Install the App on the repo
    4. Save 2 secrets: APP_ID, APP_PRIVATE_KEY (the .pem).
       (GitHub reserves the GITHUB_ prefix for its own secrets - do NOT use
       GITHUB_APP_ID etc., the Actions API refuses them with HTTP 422. The
       installation is resolved by the workflow action via `owner`, so no
       installation-id is needed as a secret.)

Then never touch it again - the App lifetime is unlimited (you only rotate the
key if it ever leaks).

Typical use in a workflow or local script:

    import os
    from sf_mcp_auth.github_app_token import mint_installation_token

    token = mint_installation_token(
        app_id=int(os.environ["APP_ID"]),
        private_key_pem=os.environ["APP_PRIVATE_KEY"],
        installation_id=12345678,  # from the App install URL
    )
    # `token` is a `ghs_...` string valid ~1h; use it as GH_TOKEN.

The companion workflow uses `actions/create-github-app-token@v1` to do this
exact mint in pure CI; this module exists so non-CI pipelines can do the same.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict


_GITHUB_API_VERSION = "2022-11-28"
_INSTALLATION_TOKEN_TTL_MIN = 10  # JWT validity window GitHub allows  <=10min


def _make_jwt(app_id: int, private_key_pem: str, *, ttl_min: int = _INSTALLATION_TOKEN_TTL_MIN, now: int | None = None) -> str:  # noqa: E501
    """Sign a GitHub App authentication JWT (RS256).

    Lazy import of `jwt` keeps the rest of the import chain stdlib-only - the
    third-party dep is only required when this helper is actually used.
    """
    try:
        import jwt  # type: ignore  # provided by pyjwt[crypto]
    except ImportError as exc:  # pragma: no cover - exercised via message
        raise ImportError(
            "sf_mcp_auth.github_app_token requires pyjwt[crypto]. "
            "Install with: pip install sf-mcp-auth[github-app]"
        ) from exc

    now_ts = int(time.time()) if now is None else int(now)
    payload: Dict[str, Any] = {
        "iat": now_ts - 60,  # 60s skew tolerance (clock drift between GH & us)
        "exp": now_ts + (ttl_min * 60),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def mint_installation_token(
    app_id: int,
    private_key_pem: str,
    installation_id: int,
    *,
    now: int | None = None,
    timeout: float = 10.0,
    _urlopen=urllib.request.urlopen,
) -> str:
    """Exchange a GitHub App JWT for a 1-hour installation token.

    Returns the opaque `ghs_...` token string suitable for `GH_TOKEN` usage
    against `gh secret set` or the REST API directly.
    """
    bearer = _make_jwt(app_id, private_key_pem, now=now)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        },
    )
    with _urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if "token" not in body:
        raise RuntimeError(f"GitHub App token endpoint returned no token: {body!r}")
    return body["token"]


__all__ = ["mint_installation_token"]
