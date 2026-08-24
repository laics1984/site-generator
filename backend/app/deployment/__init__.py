"""
The local↔production boundary, in one place.

Everything in this package exists **solely** to answer "is this safe to run
somewhere other than a developer's laptop". Nothing here is imported by the
generation pipeline, and nothing here knows about sites, sources, schemas or
pushes — a module that has any other job stays in the module that does that job.
That rule is what makes this a boundary you can read instead of an assumption
spread across 53 services.

- `profile.py`       — WHERE this process runs (not where a push lands; that is
                       a `CmsTarget` in services/cms_targets.py).
- `guards.py`        — startup checks: refuse the unsafe, warn about the merely
                       unwise. Inert under the default `DEPLOYMENT=local`.
- `process_local.py` — an inventory of the state that assumes one process. The
                       state itself stays in its home module; only the list is
                       here, kept honest by a drift test.
"""

from app.deployment.guards import DeploymentUnsafe, check, enforce
from app.deployment.process_local import PROCESS_LOCAL, blocking_at_scale
from app.deployment.profile import DeploymentProfile, current_profile

__all__ = [
    "DeploymentProfile",
    "DeploymentUnsafe",
    "PROCESS_LOCAL",
    "blocking_at_scale",
    "check",
    "current_profile",
    "enforce",
]
