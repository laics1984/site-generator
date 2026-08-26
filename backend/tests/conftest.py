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
def _offline_photo_sampling():
    """Disable pixel sampling of photos for every test.

    ``ImageResolver.resolve`` samples a full-bleed slot's photo to read its
    dominant colour and focal point (services/image_sampling.py). That is the
    one place in the resolver that downloads bytes, so leaving it on makes the
    suite hit the network for every fake ``https://cdn.example.com/...`` URL and
    wait out the timeout. Off, resolution takes the metadata-only path it always
    had. The sampling tests exercise the measurement functions directly, and any
    test wanting the wired-up behaviour flips the flag back on with a stubbed
    fetcher.
    """
    original = settings.photo_sampling_enabled
    settings.photo_sampling_enabled = False
    try:
        yield
    finally:
        settings.photo_sampling_enabled = original


@pytest.fixture(autouse=True)
def _offline_ocr():
    """Disable the OCR text screen for every test.

    ``text_detection.prefetch_text_flags`` downloads images and runs an ONNX
    detector (~630ms each). Today it self-disables because the wheel isn't in
    the test venv, but that is an accident of the environment, not a guarantee —
    once ``rapidocr-onnxruntime`` is installed the suite would start doing
    network I/O and burning seconds per test. Off, ``ocr_has_text`` simply stays
    None, which is the pre-OCR behaviour every existing assertion was written
    against. The text-detection tests flip it back on themselves.
    """
    original = settings.ocr_text_detection_enabled
    settings.ocr_text_detection_enabled = False
    try:
        yield
    finally:
        settings.ocr_text_detection_enabled = original


@pytest.fixture(autouse=True)
def _offline_facebook():
    """Keep the Facebook reader off the network for every test.

    A real ``FACEBOOK_ACCESS_TOKEN`` in ``.env`` makes ``default_fetchers``
    build a Graph client, so any test that reaches ``fetch_facebook_page``
    without injecting fetchers would call graph.facebook.com for real. Nulling
    the token AND disabling the render fallback leaves an empty chain, which
    fails loudly instead of quietly doing I/O — tests that want a read inject
    their own fetcher, which bypasses both.
    """
    original_token = settings.facebook_access_token
    original_fallback = settings.facebook_render_fallback_enabled
    settings.facebook_access_token = None
    settings.facebook_render_fallback_enabled = False
    try:
        yield
    finally:
        settings.facebook_access_token = original_token
        settings.facebook_render_fallback_enabled = original_fallback


@pytest.fixture(autouse=True)
def _offline_paste_structure():
    """Pin the LLM paste-structuring pass off for every test.

    The suite runs offline, and `structure_paste` would otherwise try to reach
    a server on every paste read — falling back after a timeout, so the tests
    would still pass while quietly waiting out the socket. Off, the
    deterministic line-shape reader runs, which is what the paste tests assert.
    tests/test_paste_structure.py turns it on itself and injects a fake client.
    """
    original = settings.paste_llm_structure_enabled
    settings.paste_llm_structure_enabled = False
    try:
        yield
    finally:
        settings.paste_llm_structure_enabled = original


@pytest.fixture(autouse=True)
def _no_design_schemes():
    """Pin design schemes off for every test.

    Same reasoning as the diversity fixture below, one level up: a scheme
    changes radius, density, measure, card frame, layout order and hero policy
    per brand, so a structural assertion written against "the" generated tree
    would really be an assertion about whichever scheme that fixture's brand
    name happened to hash to. Scheme behaviour is tested explicitly in
    tests/test_design_schemes.py, which turns the flag on itself.

    Pinned here rather than relying on the config default, so this suite keeps
    asserting the deferring path after the default is flipped on.
    """
    original = settings.design_schemes_enabled
    settings.design_schemes_enabled = False
    try:
        yield
    finally:
        settings.design_schemes_enabled = original


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
