"""P1.4 request-scoped Experience use boundary (Freeze v0.2, sections 5, 9, 14).

Current context is injected independently of Self. The gateway selects one
Experience from one validated history snapshot, delegates applicability to
P1.3, and resolves a typed navigation value only on an applicable request.
It neither grants authority nor invokes a capability or Adapter.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

from phanes.applicability import (
    ApplicabilityResult,
    ApplicabilityStatus,
    EvaluatorFailure,
    IntendedUse,
    TargetContext,
    evaluate_experience_use,
)
from phanes.experience_contracts import EXPERIENCE_NOT_FOUND, KIND_PLACE_OBSERVATION
from phanes.experience_semantic import validate_experience_document
from phanes.experience_store import ExperienceStore
from phanes.experience_validation import ExperienceValidationError


@dataclass(frozen=True)
class TargetContextSources:
    """Read-only, independently injected current-value sources.

    The frame source returns an ID only when the current Host/operator has
    asserted its P0 mock coordinate contract for the bound Adapter; otherwise
    it returns None. Sources receive no Experience and are called once each
    per valid request, not cached across requests.
    """

    get_body_instance_id: Callable[[], str | None]
    get_environment_id: Callable[[], str | None]
    get_asserted_frame_id: Callable[[], str | None]
    get_evaluation_time: Callable[[], str | None]

    def snapshot(self) -> TargetContext:
        """Take one request-local context snapshot; provider errors propagate."""
        return TargetContext(
            body_instance_id=self.get_body_instance_id(),
            environment_id=self.get_environment_id(),
            frame_id=self.get_asserted_frame_id(),
            evaluation_time=self.get_evaluation_time(),
        )


@dataclass(frozen=True)
class HistoricalExperienceResult:
    """A defensive copy of one record, for historical reading only."""

    experience_id: str
    record: dict
    applicability: ApplicabilityResult


@dataclass(frozen=True, init=False)
class ResolvedNavigationTarget:
    """Ephemeral typed P0 mock target created only by the Gateway API."""

    agent_id: str
    experience_id: str
    place_id: str
    x: int | float
    y: int | float
    target_context: TargetContext

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("ResolvedNavigationTarget is created by ExperienceUseGateway.request")

    @classmethod
    def _from_applicable_request(
        cls,
        *,
        agent_id: str,
        experience_id: str,
        place_id: str,
        x: int | float,
        y: int | float,
        target_context: TargetContext,
    ) -> ResolvedNavigationTarget:
        target = object.__new__(cls)
        for name, value in (
            ("agent_id", agent_id),
            ("experience_id", experience_id),
            ("place_id", place_id),
            ("x", x),
            ("y", y),
            ("target_context", target_context),
        ):
            object.__setattr__(target, name, value)
        return target


GatewayOutcome = HistoricalExperienceResult | ResolvedNavigationTarget | ApplicabilityResult | EvaluatorFailure


class ExperienceUseGateway:
    """No action authority: resolve only one explicit Experience request."""

    def __init__(self, store: ExperienceStore, identity_agent_id: str, sources: TargetContextSources) -> None:
        self._store = store
        self._identity_agent_id = identity_agent_id
        self._sources = sources

    def request(self, experience_id: str, intended_use: IntendedUse) -> GatewayOutcome:
        if type(intended_use) is not IntendedUse:
            raise TypeError("intended_use must be an IntendedUse enum member")

        # One Store-owned defensive snapshot for selection, evaluation and
        # resolution. No later Store read can swap the selected record.
        document = self._store.document_snapshot()
        try:
            validate_experience_document(document, self._identity_agent_id)
        except ExperienceValidationError as exc:
            return EvaluatorFailure(exc.code, exc.diagnostic)

        selected = next((record for record in document["records"] if record["experience_id"] == experience_id), None)
        if selected is None:
            return EvaluatorFailure(EXPERIENCE_NOT_FOUND, f"experience_id not found: {experience_id!r}")

        # Missing current values are explicit None. Exceptions are not missing
        # values and deliberately propagate before any target is created.
        context = self._sources.snapshot()
        outcome = evaluate_experience_use(document, self._identity_agent_id, experience_id, context, intended_use)
        if isinstance(outcome, EvaluatorFailure):
            return outcome
        if not isinstance(outcome, ApplicabilityResult):
            raise TypeError("evaluator returned an unsupported outcome")
        if outcome.status is not ApplicabilityStatus.APPLICABLE:
            return outcome

        if intended_use is IntendedUse.HISTORICAL_QUERY:
            return HistoricalExperienceResult(experience_id, copy.deepcopy(selected), outcome)

        if selected["kind"] != KIND_PLACE_OBSERVATION:
            raise RuntimeError("non-place Experience cannot resolve a navigation target")
        payload = selected["payload"]
        position = payload["position"]
        return ResolvedNavigationTarget._from_applicable_request(
            agent_id=self._identity_agent_id,
            experience_id=selected["experience_id"],
            place_id=payload["place_id"],
            x=position["x"],
            y=position["y"],
            target_context=context,
        )
