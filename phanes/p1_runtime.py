"""P1 production composition using current Host-supplied context and Registry.

This is separate from the P0 legacy command loop. It neither discovers a
Body nor reads or extends the P0 host-config schema. The bound Registry and
three explicit current-context providers are supplied by the current Host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from phanes.capabilities import CapabilityRegistry
from phanes.experience_core import _ExperienceOperationalCore
from phanes.experience_gateway import ExperienceUseGateway, TargetContextSources
from phanes.experience_store import ExperienceStore
from phanes.identity import load_identity
from phanes.memory import MemoryStore


def compose_p1_runtime(
    state_dir: Path | str,
    registry: CapabilityRegistry,
    *,
    get_environment_id: Callable[[], str | None],
    get_asserted_frame_id: Callable[[], str | None],
    get_evaluation_time: Callable[[], str | None],
) -> _ExperienceOperationalCore:
    """Load a complete Self and expose only exact-ID P1 navigation.

    The Host must assert that any non-None frame ID matches the currently
    bound Adapter's P0 mock coordinate interpretation. No value is copied
    from Experience into current context. Authority remains in ``registry``.
    """
    identity = load_identity(state_dir)
    MemoryStore.load(state_dir, identity.agent_id)
    store = ExperienceStore.load(state_dir, identity.agent_id)

    def get_body_instance_id() -> str | None:
        body = registry.current_body
        return body.body_id if body is not None else None

    sources = TargetContextSources(
        get_body_instance_id=get_body_instance_id,
        get_environment_id=get_environment_id,
        get_asserted_frame_id=get_asserted_frame_id,
        get_evaluation_time=get_evaluation_time,
    )
    gateway = ExperienceUseGateway(store, identity.agent_id, sources)
    return _ExperienceOperationalCore(gateway, registry)
