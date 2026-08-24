"""
Where this generator is RUNNING.

Not where a push lands — that is a `CmsTarget` (`services/cms_targets.py`) and
the two are deliberately separate. A local generator pushing into a live CMS is
the normal case; it does not make the generator itself hosted. Merging these
would mean either a local tool refusing to publish or a hosted one trusting its
own machine, and both are wrong.

Everything in `app/deployment/` exists **solely** for the local↔production
boundary. Nothing here is imported by the generation pipeline, and nothing here
knows anything about sites, sources, schemas or pushes. That is the rule that
keeps the boundary a place you can read rather than an assumption spread across
53 services: if a thing has a job other than answering "is this safe to run in
production", it belongs in the module that does that job, not in here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    """The posture this process is running under.

    `local` is the tool exactly as SECURITY.md describes it: single user, single
    machine, no authentication, bound to localhost. `hosted` asserts none of
    those hold, which is what `guards.py` then checks the configuration against.
    """

    name: str
    auth: str

    @property
    def is_local(self) -> bool:
        return self.name == "local"

    @property
    def is_hosted(self) -> bool:
        return self.name == "hosted"

    @property
    def single_process(self) -> bool:
        """Whether this process may assume it is the only one.

        True under `local`, and that assumption is load-bearing: see
        `process_local.py` for the state that depends on it.
        """
        return self.is_local


def current_profile() -> DeploymentProfile:
    """Read the posture from settings.

    Deliberately not cached — the suite mutates `settings` in place, the same
    reason `cms_targets.resolve_target` isn't cached.
    """
    return DeploymentProfile(name=settings.deployment, auth=settings.deployment_auth)
