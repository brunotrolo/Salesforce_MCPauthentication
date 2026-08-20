"""sf_mcp_auth: OAuth2 client + bootstrap for Salesforce Hosted MCP servers.

Agnostic and stdlib-only: no third-party dependencies, works on any
Python >= 3.10. Designed to be dropped into any project that talks to a
Salesforce Platform MCP server (``soqlQuery`` etc.) and to survive the
org setting where **Refresh Token Rotation is mandatory**.

Highlights:
  * ``SalesforceClient`` — MCP client with automatic token refresh on 401,
    capturing the ROTATED refresh token in ``client.refresh_token`` so the
    caller can persist it back to the secret.
  * ``write_auth_state`` / ``read_auth_state`` / ``should_rotate`` — the
    initial-vs-rotated contract with CI: a run that never refreshed cannot
    clobber a valid secret with an already-used token.
  * ``bootstrap`` — PKCE flow (External Client App) to obtain the FIRST
    refresh token, exposed both as a library and as the
    ``sf-mcp-auth-bootstrap`` console script.
"""

from __future__ import annotations

from . import bootstrap
from .auth_state import read_auth_state, should_rotate, write_auth_state
from .client import (
    DEFAULT_MCP_URL,
    OAUTH_DISCOVERY_URL,
    RetryExhausted,
    SalesforceClient,
    SalesforceClientError,
    TokenBucket,
    is_retryable_error,
    retry,
)

__version__ = "1.1.1"

__all__ = [
    "DEFAULT_MCP_URL",
    "OAUTH_DISCOVERY_URL",
    "RetryExhausted",
    "SalesforceClient",
    "SalesforceClientError",
    "TokenBucket",
    "bootstrap",
    "is_retryable_error",
    "read_auth_state",
    "retry",
    "should_rotate",
    "write_auth_state",
]
