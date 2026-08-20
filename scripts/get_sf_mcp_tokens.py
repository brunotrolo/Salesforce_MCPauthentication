#!/usr/bin/env python3
"""Bootstrap OAuth2 PKCE for the Salesforce Hosted MCP servers.

Thin wrapper around the ``sf_mcp_auth`` package. The implementation lives in
``sf_mcp_auth.bootstrap`` (and its CLI entry point ``sf-mcp-auth-bootstrap``);
this script exists so the documented one-liner below keeps working without a
prior ``pip install`` of the package:

    python scripts/get_sf_mcp_tokens.py <client_id> <client_secret>
"""

from __future__ import annotations

import sys

from sf_mcp_auth.bootstrap import main


if __name__ == "__main__":
    sys.exit(main())