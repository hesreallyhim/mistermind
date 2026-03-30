"""Shared pytest fixtures for the MisterMind engine test suite."""

import pytest

from mistermind import engine as mm


@pytest.fixture()
def initial_state() -> dict:
    """A fresh game state for owner/repo issue #1."""
    return mm.build_initial_state("owner/repo", 1, "owner")


@pytest.fixture()
def signing_secret() -> str:
    return "signing-secret"


@pytest.fixture()
def default_solution() -> list[str]:
    return ["red", "blue", "green", "yellow"]


@pytest.fixture()
def default_policy() -> dict:
    return mm.default_moderation_policy()


@pytest.fixture()
def initial_conduct_state() -> dict:
    return mm.build_initial_conduct_state("owner/repo", 40, "owner")
