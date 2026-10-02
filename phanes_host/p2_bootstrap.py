"""Host-only declarations and explicit one-shot onboarding.

The target Host independently installs the frozen P0 discovery source under
the fixed name ``phanes_host._p0_discovery``. It is a deployment artifact,
not package-v2 content. Import never creates a usable session or Runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Callable, Iterable

from phanes.contracts import BodyAdapter, BodyDescriptor, CapabilitySpec

if TYPE_CHECKING:
    from phanes.experience_core import _ExperienceOperationalCore

__all__ = ("HostSessionDescriptor", "OnboardingError", "onboard_host_session")

# Only publication is permanent. Preparation objects never live in module state.
# Serialize callers (including rejecting reentrant preparation); not a hardware
# lock, Registry lifecycle change, or cross-process consistency mechanism.
_publication_lock = Lock()
_published = False

# Existing P1 source kinds only: Body comes from the current Registry;
# environment/frame/time are independently supplied by the current Host.
_P1_CONTEXT_SOURCE_KINDS = frozenset(
    {"body_instance_id", "environment_id", "frame_id", "evaluation_time"}
)


class OnboardingError(Exception):
    """Ordinary Host bootstrap exception, not a P1 stable failure code.

    Preparation errors preserve their original exception as __cause__.
    """


@dataclass(frozen=True, slots=True)
class HostSessionDescriptor:
    """Read-only, ephemeral Host declaration; not a session or permission.

    Shape checks prevent mutable containers and live values in supported
    construction. They do not establish capability compatibility, validate
    a Registry binding, authenticate a Body, or prove physical truth.
    There is no persistence/serialization API or session ID generation.
    """

    host_session_id: str
    body: BodyDescriptor
    offered_capabilities: tuple[CapabilitySpec, ...]
    context_source_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.host_session_id) is not str:
            raise TypeError("host_session_id must be an opaque string")
        if not self.host_session_id.strip():
            raise ValueError("host_session_id must not be empty")
        if type(self.body) is not BodyDescriptor:
            raise TypeError("body must be the existing BodyDescriptor")
        if (
            type(self.body.body_id) is not str
            or type(self.body.body_type) is not str
            or type(self.body.adapter_api_version) is not int
        ):
            raise TypeError("BodyDescriptor must contain string/string/integer declarations")
        if type(self.offered_capabilities) is not tuple:
            raise TypeError("offered_capabilities must be an immutable tuple")
        for spec in self.offered_capabilities:
            if type(spec) is not CapabilitySpec:
                raise TypeError("offered capabilities must reuse CapabilitySpec")
            if type(spec.name) is not str or type(spec.contract_version) is not int:
                raise TypeError("CapabilitySpec must contain string/integer declarations")
        if type(self.context_source_kinds) is not tuple:
            raise TypeError("context_source_kinds must be an immutable tuple")
        if any(type(kind) is not str for kind in self.context_source_kinds):
            raise TypeError("context source kinds must be strings")
        if not set(self.context_source_kinds) <= _P1_CONTEXT_SOURCE_KINDS:
            raise ValueError("context source kinds must belong to the fixed P1 vocabulary")
        if len(set(self.context_source_kinds)) != len(self.context_source_kinds):
            raise ValueError("context source kinds must not be duplicated")


def _load_host_discovery():
    """Load only the independently installed, fixed Host dependency.

    Private preparation helper, not a discovery/onboarding API. Missing
    installation raises ImportError; there is deliberately no repository
    fallback, path argument, plugin lookup, or invocation of discovery.
    """
    from phanes_host import _p0_discovery

    return _p0_discovery


class _CapturedAdapterView:
    """Private declaration snapshot; invocation delegates exactly once.

    Registry alone validates these declarations. Reading this view never
    re-reads the trusted Adapter's potentially changing declarations.
    """

    def __init__(self, adapter: BodyAdapter) -> None:
        self._adapter = adapter
        self._descriptor = adapter.descriptor
        self._offered = tuple(adapter.capabilities())

    @property
    def descriptor(self) -> BodyDescriptor:
        return self._descriptor

    def capabilities(self) -> tuple[CapabilitySpec, ...]:
        return self._offered

    def invoke(self, name: str, args: dict) -> dict:
        return self._adapter.invoke(name, args)


def onboard_host_session(
    state_dir: Path | str,
    body_config: dict,
    *,
    allowed_capabilities: Iterable[str] = (),
    get_environment_id: Callable[[], str | None] | None = None,
    get_asserted_frame_id: Callable[[], str | None] | None = None,
    get_evaluation_time: Callable[[], str | None] | None = None,
) -> tuple[HostSessionDescriptor, _ExperienceOperationalCore] | None:
    """Privately prepare and publish one complete Host session per process.

    A valid explicit no-body configuration returns None and remains UNBOUND.
    Providers are checked for callability, never called here. Preparation
    failures are retryable; successful publication cannot be reset/rebound.
    The independently installed fixed Host Discovery dependency is mandatory.
    """
    global _published
    if not _publication_lock.acquire(blocking=False):
        raise OnboardingError("Host onboarding is already in progress")
    try:
        if _published:
            raise OnboardingError("a Host session is already published in this process")
        stage = "provider validation"
        try:
            for name, provider in (
                ("get_environment_id", get_environment_id),
                ("get_asserted_frame_id", get_asserted_frame_id),
                ("get_evaluation_time", get_evaluation_time),
            ):
                if not callable(provider):
                    raise TypeError(f"{name} must be callable")

            stage = "authority/config input preparation"
            if isinstance(allowed_capabilities, (str, bytes)):
                raise TypeError("allowed_capabilities must be an iterable of names, not a string")
            allowed_snapshot = tuple(allowed_capabilities)
            # Keep the existing Discovery as the sole config validator. The
            # extra P2 rule forbids ambiguous config-sourced authority.
            from copy import deepcopy

            config_snapshot = deepcopy(body_config)
            if isinstance(config_snapshot, dict) and config_snapshot.get("allowed_capabilities"):
                raise ValueError("body_config authority must be empty; use the separate allowed_capabilities input")

            stage = "fixed Host Discovery"
            discovery = _load_host_discovery()
            adapter = discovery.discover_body(config_snapshot)
            if adapter is None:
                if allowed_snapshot:
                    raise ValueError("no-body configuration cannot publish authority")
                return None

            stage = "declaration capture"
            view = _CapturedAdapterView(adapter)
            stage = "Registry binding"
            from phanes.capabilities import CapabilityRegistry

            registry = CapabilityRegistry()
            registry.bind(view, allowed_snapshot)

            stage = "P1 Runtime composition"
            from phanes.p1_runtime import compose_p1_runtime

            runtime = compose_p1_runtime(
                state_dir, registry,
                get_environment_id=get_environment_id,
                get_asserted_frame_id=get_asserted_frame_id,
                get_evaluation_time=get_evaluation_time,
            )
            stage = "session ID/declaration construction"
            import uuid

            descriptor = HostSessionDescriptor(
                host_session_id=str(uuid.uuid4()),
                body=view.descriptor,
                offered_capabilities=view.capabilities(),
                context_source_kinds=("body_instance_id", "environment_id", "frame_id", "evaluation_time"),
            )
            result = (descriptor, runtime)
        except Exception as exc:
            raise OnboardingError(f"Host onboarding failed during {stage}: {exc}") from exc
        # No fallible preparation follows this sole publication commit point.
        _published = True
        return result
    finally:
        _publication_lock.release()
