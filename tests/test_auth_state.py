"""Unit tests for sf_mcp_auth.auth_state (initial-vs-rotated contract)."""

from __future__ import annotations

import json

import pytest

from sf_mcp_auth import read_auth_state, should_rotate, write_auth_state


def test_write_and_read_roundtrip(tmp_path):
    path = str(tmp_path / "auth_state.json")
    write_auth_state(path, "rotated", initial="old")
    state = read_auth_state(path)
    assert state == {"refresh_token": "rotated", "refresh_token_initial": "old"}


def test_write_without_initial_defaults_to_refresh(tmp_path):
    path = str(tmp_path / "auth_state.json")
    write_auth_state(path, "only")
    state = read_auth_state(path)
    assert state == {"refresh_token": "only", "refresh_token_initial": "only"}


def test_should_rotate_true_when_rotated():
    assert should_rotate({"refresh_token": "new", "refresh_token_initial": "old"}) is True


def test_should_rotate_false_when_unchanged():
    assert should_rotate({"refresh_token": "same", "refresh_token_initial": "same"}) is False


def test_should_rotate_false_on_incomplete_data():
    assert should_rotate({}) is False
    assert should_rotate({"refresh_token": "new"}) is False
    assert should_rotate({"refresh_token_initial": "old"}) is False
    assert should_rotate({"refresh_token": "", "refresh_token_initial": "old"}) is False


def test_load_and_decide(tmp_path):
    path = str(tmp_path / "auth_state.json")
    with open(path, "w") as f:
        json.dump({"refresh_token": "new", "refresh_token_initial": "old"}, f)
    from sf_mcp_auth.auth_state import load_and_decide

    state, rotate = load_and_decide(path)
    assert state["refresh_token"] == "new"
    assert rotate is True


def test_missing_file_raises(tmp_path):
    from sf_mcp_auth.auth_state import read_auth_state

    with pytest.raises(FileNotFoundError):
        read_auth_state(str(tmp_path / "nope.json"))