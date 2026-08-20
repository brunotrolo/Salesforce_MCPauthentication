"""Auth-state persistence for pipelines with mandatory refresh token rotation.

Contract with the CI "Rotate auth secret" step: a run writes the refresh
token it ENDED with plus the one it STARTED with; the workflow only updates
the GitHub secret when the two differ. A run that never refreshed therefore
cannot clobber a valid secret with an already-used (dead) token.

Why this matters (learned in production):
  Salesforce Refresh Token Rotation is mandatory in some orgs: every
  refresh invalidates the previous refresh token. If a run fails BEFORE
  refreshing, its "current" token is the one from the secret — which may
  already be dead because another run refreshed it. Persisting only the
  end token would write the dead token back over the live rotated one.
  Persisting both and comparing is the fix.
"""

from __future__ import annotations

import json
from typing import Any

KEY_REFRESH = "refresh_token"
KEY_INITIAL = "refresh_token_initial"


def write_auth_state(
    path: str, refresh_token: str, initial: str | None = None
) -> None:
    """Persist the (possibly rotated) refresh token plus the initial one.

    ``initial`` defaults to ``refresh_token`` when unknown, which makes a
    caller that never captured it conservatively produce an auth state
    where ``should_rotate`` is False (no secret update on that run).
    """
    data = {
        KEY_REFRESH: refresh_token,
        KEY_INITIAL: initial or refresh_token,
    }
    with open(path, "w") as f:
        json.dump(data, f)


def read_auth_state(path: str) -> dict[str, Any]:
    """Load an auth state file written by :func:`write_auth_state`."""
    with open(path) as f:
        return json.load(f)


def should_rotate(auth_state: dict[str, Any]) -> bool:
    """True when the run ended with a different token than it started with.

    This is the single decision point used by CI to know whether the
    secret needs updating. Missing/invalid entries return False (never
    rotate on incomplete data).
    """
    ended = auth_state.get(KEY_REFRESH)
    started = auth_state.get(KEY_INITIAL)
    if not ended or not started:
        return False
    return ended != started


def load_and_decide(path: str) -> tuple[dict[str, Any], bool]:
    """Read an auth state file and report whether the secret must rotate."""
    state = read_auth_state(path)
    return state, should_rotate(state)
