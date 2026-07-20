"""Shared pytest fixtures for the backend suite.

The suite is meant to run offline and deterministically (no LLM, no network).
The one ambient dependency that leaks in is Pexels: a real ``PEXELS_API_KEY`` in
``.env`` makes ``get_pexels_client()`` build a *configured* client, so any test
that runs the generation pipeline without injecting a ``FakePexels`` would make
live API calls and resolve real stock photos — non-deterministic, and enough to
flip image/overlay assertions that assume "no genuine photo resolved".

``get_pexels_client`` is ``@lru_cache``d (a process singleton), so clearing the
key alone isn't enough — the cached client keeps the old key. This autouse
fixture nulls the key AND clears that cache around every test, guaranteeing an
unconfigured client regardless of the environment. Tests that want stock-photo
behaviour inject a ``FakePexels`` directly (dependency injection), which bypasses
``get_pexels_client`` entirely and is unaffected.
"""

import pytest

from app.config import settings
from app.services.pexels import get_pexels_client


@pytest.fixture(autouse=True)
def _offline_pexels():
    original = settings.pexels_api_key
    settings.pexels_api_key = None
    get_pexels_client.cache_clear()
    try:
        yield
    finally:
        settings.pexels_api_key = original
        get_pexels_client.cache_clear()


@pytest.fixture(autouse=True)
def _hermetic_diversity():
    """Disable the diversity engine's SQLite history for every test.

    The engine deliberately makes consecutive generations differ (it steers a
    new site away from the chrome the previous site picked). In a test run
    that would make one test's generated header depend on which tests ran
    before it — order-dependent assertions. With the history off, archetype
    selection is the purely seeded, per-brand-idempotent rotation, which is
    what structural assertions should target. diversity-specific tests flip
    the flag back on themselves with an isolated DB path.
    """
    original = settings.diversity_engine_enabled
    settings.diversity_engine_enabled = False
    try:
        yield
    finally:
        settings.diversity_engine_enabled = original
