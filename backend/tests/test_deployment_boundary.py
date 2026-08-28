"""
The local↔production boundary.

Two things are pinned here, and the second is the one that matters long-term:

1. **The guards.** `DEPLOYMENT=local` is a true no-op; `hosted` refuses the
   settings that expose you and warns about the ones that merely make a deploy
   worse.
2. **The inventory cannot go stale.** A hand-written list of "state that assumes
   one process" is worthless six months later, so this walks the AST of every
   module under `app/` and fails on anything it finds that nobody has classified.
   Same drift-test idiom as the section catalog and the renderer-pinned name sets.

The discriminator: a cache starts empty, a lookup table starts full. An empty
dict/list/set literal at module scope is runtime state; a populated one is data.
`@lru_cache`/`@cache` is always process-local.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from app.config import settings
from app.deployment import check, current_profile
from app.deployment.guards import DeploymentUnsafe, enforce
from app.deployment.process_local import (
    CORRECTNESS,
    NOT_PROCESS_LOCAL,
    PROCESS_LOCAL,
    blocking_at_scale,
)

_APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


def _discover_module_state() -> set[tuple[str, str]]:
    """Every module-level runtime container and memo under app/, as (module, attr)."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_APP_ROOT.rglob("*.py")):
        dotted = ".".join(path.relative_to(_APP_ROOT.parent).with_suffix("").parts)
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if not isinstance(value, (ast.Dict, ast.List, ast.Set)):
                    continue
                empty = not value.keys if isinstance(value, ast.Dict) else not value.elts
                if not empty:
                    continue
                target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
                if isinstance(target, ast.Name):
                    found.add((dotted, target.id))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    ref = deco.func if isinstance(deco, ast.Call) else deco
                    if getattr(ref, "attr", getattr(ref, "id", "")) in ("lru_cache", "cache"):
                        found.add((dotted, node.name))
    return found


class InventoryDriftTest(unittest.TestCase):
    def test_every_module_level_cache_is_classified(self) -> None:
        classified = {(i.module, i.attribute) for i in PROCESS_LOCAL}
        classified |= {(m, a) for m, a, _ in NOT_PROCESS_LOCAL}
        unclassified = _discover_module_state() - classified
        self.assertEqual(
            unclassified,
            set(),
            "New module-level state that nobody has classified. Add it to "
            "PROCESS_LOCAL (it is a per-process cache) or to NOT_PROCESS_LOCAL "
            "with a reason (it is a lookup table or a pure memo), both in "
            "app/deployment/process_local.py.",
        )

    def test_the_inventory_names_nothing_that_no_longer_exists(self) -> None:
        discovered = _discover_module_state()
        listed = {(i.module, i.attribute) for i in PROCESS_LOCAL}
        listed |= {(m, a) for m, a, _ in NOT_PROCESS_LOCAL}
        self.assertEqual(listed - discovered, set(), "Inventory names state that is gone.")

    def test_nothing_is_classified_twice(self) -> None:
        state = {(i.module, i.attribute) for i in PROCESS_LOCAL}
        not_state = {(m, a) for m, a, _ in NOT_PROCESS_LOCAL}
        self.assertEqual(state & not_state, set())

    def test_every_exclusion_carries_a_reason(self) -> None:
        for module, attribute, reason in NOT_PROCESS_LOCAL:
            with self.subTest(attribute=attribute):
                self.assertTrue(len(reason.strip()) > 30, f"{module}.{attribute} needs a real reason")


class DeploymentIsolationTest(unittest.TestCase):
    """Nothing under app/deployment/ may be imported by the pipeline, and nothing
    there may import the pipeline. That mutual isolation IS the boundary — the
    moment a service imports it, deployment posture starts leaking into
    generation logic and the separation stops being readable."""

    def test_the_pipeline_does_not_import_the_deployment_package(self) -> None:
        offenders = []
        for path in sorted(_APP_ROOT.rglob("*.py")):
            if path.parts[-2] == "deployment" or path.name == "main.py":
                continue
            if "app.deployment" in path.read_text():
                offenders.append(str(path.relative_to(_APP_ROOT)))
        self.assertEqual(
            offenders, [], "Only app/main.py may import app.deployment."
        )

    def test_the_deployment_package_does_not_import_the_pipeline(self) -> None:
        offenders = []
        for path in sorted((_APP_ROOT / "deployment").rglob("*.py")):
            for line in path.read_text().splitlines():
                if line.startswith(("import app.", "from app.")) and not line.startswith(
                    ("from app.config", "from app.deployment", "import app.deployment")
                ):
                    offenders.append(f"{path.name}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "app/deployment/ may only import app.config — process_local reaches "
            "its modules lazily, by name, at reset time.",
        )


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (
            settings.deployment,
            settings.deployment_auth,
            settings.scrape_allow_private_hosts,
            list(settings.cors_origins),
            settings.facebook_session_enabled,
        )

    def tearDown(self) -> None:
        (
            settings.deployment,
            settings.deployment_auth,
            settings.scrape_allow_private_hosts,
            settings.cors_origins,
            settings.facebook_session_enabled,
        ) = self._saved

    def _hosted(self) -> None:
        settings.deployment = "hosted"
        settings.deployment_auth = "proxy"
        settings.scrape_allow_private_hosts = False
        settings.cors_origins = ["https://gen.example.com"]
        settings.facebook_session_enabled = False

    def test_local_is_a_true_no_op(self) -> None:
        settings.deployment = "local"
        # Everything that would be fatal under hosted, set at once.
        settings.deployment_auth = "none"
        settings.scrape_allow_private_hosts = True
        settings.cors_origins = ["http://localhost:5173"]
        settings.facebook_session_enabled = True
        self.assertEqual(check(), ([], []))
        enforce()  # must not raise

    def test_hosted_without_auth_is_refused(self) -> None:
        self._hosted()
        settings.deployment_auth = "none"
        errors, _ = check()
        self.assertTrue(any("DEPLOYMENT_AUTH=none" in e for e in errors))
        with self.assertRaises(DeploymentUnsafe):
            enforce()

    def test_hosted_with_the_ssrf_guard_off_is_refused(self) -> None:
        self._hosted()
        settings.scrape_allow_private_hosts = True
        errors, _ = check()
        self.assertTrue(any("SCRAPE_ALLOW_PRIVATE_HOSTS" in e for e in errors))

    def test_hosted_with_leftover_localhost_cors_is_refused(self) -> None:
        self._hosted()
        settings.cors_origins = ["https://gen.example.com", "http://localhost:5174"]
        errors, _ = check()
        self.assertTrue(any("localhost" in e for e in errors))

    def test_hosted_with_wildcard_cors_is_refused(self) -> None:
        self._hosted()
        settings.cors_origins = ["*"]
        errors, _ = check()
        self.assertTrue(any("'*'" in e for e in errors))

    def test_a_correctly_configured_hosted_deploy_starts(self) -> None:
        self._hosted()
        errors, warnings = check()
        self.assertEqual(errors, [])
        enforce()  # must not raise
        # …but it still says what is imperfect rather than going quiet.
        self.assertTrue(warnings)

    def test_hosted_with_a_shared_facebook_login_is_refused(self) -> None:
        """One person's cookies replayed for every user is not a deploy
        posture, it is an account handover — and the endpoint that accepts
        them authenticates nobody."""
        self._hosted()
        settings.facebook_session_enabled = True
        errors, _ = check()
        self.assertTrue(any("FACEBOOK_SESSION_ENABLED" in e for e in errors))
        with self.assertRaises(DeploymentUnsafe):
            enforce()

    def test_scale_warning_names_the_correctness_cases_only(self) -> None:
        self._hosted()
        _, warnings = check()
        scale = next(w for w in warnings if "Run ONE worker" in w)
        self.assertIn("_TOKENS", scale)
        self.assertIn("_registry", scale)
        # A cold cache is not a correctness problem and must not be listed here.
        self.assertNotIn("_RESPONSE_CACHE", scale)

    def test_severity_split_is_real(self) -> None:
        self.assertTrue(blocking_at_scale())
        self.assertLess(len(blocking_at_scale()), len(PROCESS_LOCAL))
        self.assertTrue(all(i.severity == CORRECTNESS for i in blocking_at_scale()))


class ProfileTest(unittest.TestCase):
    def test_profile_reflects_settings_without_caching(self) -> None:
        saved = settings.deployment
        try:
            settings.deployment = "local"
            self.assertTrue(current_profile().is_local)
            self.assertTrue(current_profile().single_process)
            settings.deployment = "hosted"
            self.assertTrue(current_profile().is_hosted)
            self.assertFalse(current_profile().single_process)
        finally:
            settings.deployment = saved


if __name__ == "__main__":
    unittest.main()
