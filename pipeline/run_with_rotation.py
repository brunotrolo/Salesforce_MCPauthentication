#!/usr/bin/env python3
"""Pattern: run ANY script with Salesforce token rotation persistence.

This is the exact pattern used in the production pipeline (orchestrate.py):
the MCP client is built BEFORE the try block, and a ``finally`` block
persists the (possibly rotated) refresh token to a JSON file so the GitHub
Actions step "Rotate auth secret" can write it back to the secret.

Why this works even when the pipeline fails:
  * a refresh happened -> the old secret token is already dead (Rotation);
  * the finally block still writes the NEW token that the client captured;
  * the workflow rotate step runs with ``if: always()``.

The auth state file also records ``refresh_token_initial`` - the token the run
STARTED with. The rotate step compares both and only writes the secret back
when they differ: a run that never refreshed must NOT clobber a valid rotated
token with an already-dead one.

Usage:
    python run_with_rotation.py --mode real --auth-state-out out/auth_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from sf_mcp_auth.auth_state import write_auth_state
from sf_mcp_auth.client import SalesforceClient


def _build_client() -> Any:
    """Build the real MCP client from SF_* env vars (set by the workflow)."""
    return SalesforceClient()


def run_pipeline(client: Any, mode: str) -> dict:
    """Replace this with your own logic (SOQL queries, MCP tools, etc.)."""
    if mode == "real":
        result = client.soql_query("SELECT Id, Name FROM Account LIMIT 1")
        return {"ok": True, "sample": result}
    return {"ok": True, "mode": "mock"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default="mock",
        choices=["mock", "real"],
        help="Run mode (real: Salesforce MCP with env vars)",
    )
    parser.add_argument(
        "--auth-state-out",
        default=None,
        help=(
            "Write the auth state after the run, so the caller can persist "
            "the rotated refresh token (Salesforce Refresh Token Rotation); "
            "optional"
        ),
    )
    args = parser.parse_args()

    # Build the client BEFORE the try: the finally block must always have a
    # reference to it, even when the pipeline fails mid-run.
    client = _build_client() if args.mode == "real" else None
    try:
        result = run_pipeline(client=client, mode=args.mode)
        print(json.dumps(result, indent=2))
    except Exception:
        # Re-raise so the run fails loudly; the auth state is still persisted.
        raise
    finally:
        if args.auth_state_out and client is not None and client.refresh_token:
            write_auth_state(
                args.auth_state_out,
                client.refresh_token,
                initial=os.environ.get("SF_REFRESH_TOKEN"),
            )


if __name__ == "__main__":
    sys.exit(main())
