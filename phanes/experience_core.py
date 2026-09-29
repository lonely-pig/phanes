"""P1 operational Core: one exact Experience request before any capability use.

The class is private to the P1 composition boundary. Callers can request an
Experience ID, but cannot submit coordinates or a previously resolved target.
"""

from __future__ import annotations

from phanes.applicability import ApplicabilityResult, EvaluatorFailure, IntendedUse
from phanes.capabilities import CapabilityRegistry
from phanes.contracts import CAPABILITY_UNAVAILABLE, MOVE_TO, NO_BODY, InvocationResult
from phanes.experience_gateway import ExperienceUseGateway, ResolvedNavigationTarget


OperationOutcome = ApplicabilityResult | EvaluatorFailure | InvocationResult


class _ExperienceOperationalCore:
    """Consumes only a Gateway-produced value from this same request."""

    def __init__(self, gateway: ExperienceUseGateway, registry: CapabilityRegistry) -> None:
        self._gateway = gateway
        self._registry = registry

    def navigate_experience(self, experience_id: str) -> OperationOutcome:
        """Re-evaluate one exact ID, then check current Host authority."""
        if type(experience_id) is not str:
            raise TypeError("experience_id must be an exact ID string")
        outcome = self._gateway.request(experience_id, IntendedUse.NAVIGATION_TARGET)
        if isinstance(outcome, (ApplicabilityResult, EvaluatorFailure)):
            return outcome
        if not isinstance(outcome, ResolvedNavigationTarget):
            raise TypeError("navigation Gateway returned an unsupported outcome")
        return self._invoke_current_target(outcome)

    def _invoke_current_target(self, target: ResolvedNavigationTarget) -> InvocationResult:
        if self._registry.current_body is None:
            return InvocationResult.failure(NO_BODY, "no body is bound")
        if MOVE_TO not in {spec.name for spec in self._registry.list()}:
            return InvocationResult.failure(CAPABILITY_UNAVAILABLE, "move_to is not registered")
        return self._registry.invoke(MOVE_TO, {"x": target.x, "y": target.y})
