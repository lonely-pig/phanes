"""P2.1 Host-only declarations; importing this module never onboards.

The target Host independently installs the frozen P0 discovery source under
the fixed name ``phanes_host._p0_discovery``. It is a deployment artifact,
not package-v2 content. No usable session or Runtime is created here.
"""

from __future__ import annotations

from dataclasses import dataclass

from phanes.contracts import BodyDescriptor, CapabilitySpec

__all__ = ("HostSessionDescriptor", "OnboardingError")

# Existing P1 source kinds only: Body comes from the current Registry;
# environment/frame/time are independently supplied by the current Host.
_P1_CONTEXT_SOURCE_KINDS = frozenset(
    {"body_instance_id", "environment_id", "frame_id", "evaluation_time"}
)


class OnboardingError(Exception):
    """Ordinary Host bootstrap exception, not a P1 stable failure code.

    P2.1 defines the type only; onboarding and failure wrapping are not yet
    implemented.
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
