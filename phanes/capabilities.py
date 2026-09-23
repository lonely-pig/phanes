"""Controlled capability registry (Architecture Freeze section 8).

The registry is the single invocation entry point for Core. It starts
empty, binds at most one body per process, and registers exactly
`adapter.offered ∩ host.allowed`. Default authority is zero: a missing or
empty allowed list registers nothing. No wildcards, no runtime grant API.
"""

from __future__ import annotations

from typing import Any, Iterable

from phanes.contracts import (
    ADAPTER_API_VERSION,
    ADAPTER_ERROR,
    CAPABILITY_CONTRACT_VERSION,
    CAPABILITY_UNAVAILABLE,
    INVALID_ARGUMENT,
    INVALID_RESULT,
    KNOWN_CAPABILITIES,
    NO_BODY,
    BodyAdapter,
    BodyDescriptor,
    CapabilitySpec,
    ContractError,
    InvocationResult,
    validate_capability_args,
    validate_capability_result,
)


class RegistryError(Exception):
    """Binding failure (Freeze section 8 rule 2): startup must terminate."""


class CapabilityRegistry:
    """Binds one BodyAdapter and mediates every capability invocation."""

    def __init__(self) -> None:
        self._adapter: BodyAdapter | None = None
        self._registered: dict[str, CapabilitySpec] = {}

    @property
    def current_body(self) -> BodyDescriptor | None:
        return self._adapter.descriptor if self._adapter is not None else None

    def bind(self, adapter: BodyAdapter, allowed_capabilities: Iterable[str]) -> None:
        """Bind the single body for this process and register the authorized
        subset of its offered capabilities. Any inconsistency aborts binding
        before any state is committed; a failed bind leaves the registry empty.
        """
        if self._adapter is not None:
            raise RegistryError("a body is already bound; rebinding is not supported")

        # Startup-time contract validation of the adapter (trusted local code,
        # minimal defense — not a plugin sandbox).
        try:
            descriptor = adapter.descriptor
        except Exception as exc:
            raise RegistryError(f"adapter descriptor is not readable: {exc}") from exc
        if not isinstance(descriptor, BodyDescriptor):
            raise RegistryError(
                f"adapter descriptor must be a BodyDescriptor, got {type(descriptor).__name__}"
            )
        if not isinstance(descriptor.body_id, str) or not descriptor.body_id.strip():
            raise RegistryError("descriptor body_id must be a non-empty string")
        if not isinstance(descriptor.body_type, str) or not descriptor.body_type.strip():
            raise RegistryError("descriptor body_type must be a non-empty string")
        if descriptor.adapter_api_version != ADAPTER_API_VERSION:
            raise RegistryError(
                f"unsupported adapter_api_version: {descriptor.adapter_api_version!r}"
            )

        try:
            offered = tuple(adapter.capabilities())
        except Exception as exc:
            raise RegistryError(f"adapter capabilities() failed: {exc}") from exc
        offered_names: list[str] = []
        for spec in offered:
            if not isinstance(spec, CapabilitySpec):
                raise RegistryError(
                    f"capability entries must be CapabilitySpec, got {type(spec).__name__}"
                )
            if not isinstance(spec.name, str) or not spec.name:
                raise RegistryError("capability name must be a non-empty string")
            if spec.name not in KNOWN_CAPABILITIES:
                raise RegistryError(
                    f"adapter offers capability outside the fixed contract: {spec.name!r}"
                )
            if spec.contract_version != CAPABILITY_CONTRACT_VERSION:
                raise RegistryError(
                    f"capability {spec.name!r} has unsupported contract_version: "
                    f"{spec.contract_version!r}"
                )
            offered_names.append(spec.name)
        duplicates = sorted({n for n in offered_names if offered_names.count(n) > 1})
        if duplicates:
            raise RegistryError(f"adapter offers duplicate capability names: {duplicates}")

        allowed = list(allowed_capabilities)
        if not all(isinstance(name, str) for name in allowed):
            raise RegistryError("allowed_capabilities entries must be strings")
        allowed_duplicates = sorted({n for n in allowed if allowed.count(n) > 1})
        if allowed_duplicates:
            raise RegistryError(f"duplicate entries in allowed_capabilities: {allowed_duplicates}")
        unknown = sorted(set(allowed) - set(offered_names))
        if unknown:
            raise RegistryError(f"allowed_capabilities not offered by adapter: {unknown}")

        self._adapter = adapter
        allowed_set = set(allowed)
        self._registered = {spec.name: spec for spec in offered if spec.name in allowed_set}

    def list(self) -> tuple[CapabilitySpec, ...]:
        """Only capabilities that are both offered and host-authorized."""
        return tuple(self._registered[name] for name in sorted(self._registered))

    def invoke(self, name: str, args: dict[str, Any]) -> InvocationResult:
        """Check body, then membership, then arguments; call the adapter once;
        validate its result. Never retries (Freeze section 8 rules 4-5).
        """
        if self._adapter is None:
            return InvocationResult.failure(NO_BODY, "no body is bound")
        if name not in self._registered:
            return InvocationResult.failure(
                CAPABILITY_UNAVAILABLE, f"{name} is not registered"
            )
        try:
            validate_capability_args(name, args)
        except ContractError as exc:
            return InvocationResult.failure(INVALID_ARGUMENT, str(exc))
        try:
            data = self._adapter.invoke(name, dict(args))
        except Exception as exc:
            return InvocationResult.failure(ADAPTER_ERROR, f"{type(exc).__name__}: {exc}")
        try:
            validate_capability_result(name, data)
        except ContractError as exc:
            return InvocationResult.failure(INVALID_RESULT, str(exc))
        return InvocationResult.success(data)
