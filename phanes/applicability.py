"""P1.3 pure Experience applicability evaluation (Freeze v0.2, section 12).

The public entry validates the complete history, selects one exact ID, and
then evaluates that record against caller-supplied request context. It never
discovers context, grants authority, persists state, or performs an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from phanes.experience_contracts import (
    ALL_REQUIRED_CONSTRAINTS_MATCH,
    BODY_CONTEXT_MISSING,
    BODY_MISMATCH,
    ENVIRONMENT_CONTEXT_MISSING,
    ENVIRONMENT_MISMATCH,
    EVALUATION_TIME_MISSING,
    EVALUATOR_INTERNAL_ERROR,
    EXPERIENCE_NOT_FOUND,
    FRAME_CONTEXT_MISSING,
    FRAME_MISMATCH,
    HISTORICAL_QUERY_PERMITTED,
    INTENDED_USE_HISTORICAL_QUERY,
    INTENDED_USE_NAVIGATION_TARGET,
    INTENDED_USE_NOT_ALLOWED,
    KIND_BODY_PARAMETER_OBSERVATION,
    MODE_EXACT,
    MODE_INTERVAL,
    MODE_UNKNOWN,
    RECORD_SUPERSEDED,
    TEMPORALLY_EXPIRED,
    TEMPORALLY_NOT_YET_VALID,
    TEMPORAL_DEPENDENCY_UNKNOWN,
)
from phanes.experience_semantic import _utc_order_key, validate_experience_document
from phanes.experience_validation import ExperienceValidationError, _is_utc_rfc3339_timestamp


class IntendedUse(str, Enum):
    HISTORICAL_QUERY = INTENDED_USE_HISTORICAL_QUERY
    NAVIGATION_TARGET = INTENDED_USE_NAVIGATION_TARGET


class ApplicabilityStatus(str, Enum):
    APPLICABLE = "APPLICABLE"
    INAPPLICABLE = "INAPPLICABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TargetContext:
    """Request-local current values supplied independently of Experience."""

    body_instance_id: str | None = None
    environment_id: str | None = None
    frame_id: str | None = None
    evaluation_time: str | None = None

    def __post_init__(self) -> None:
        for name in ("body_instance_id", "environment_id", "frame_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or value == ""):
                raise TypeError(f"{name} must be a non-empty opaque string or None")
        if self.evaluation_time is not None and not _is_utc_rfc3339_timestamp(self.evaluation_time):
            raise ValueError("evaluation_time must be a UTC RFC 3339 timestamp or None")


@dataclass(frozen=True)
class ApplicabilityResult:
    status: ApplicabilityStatus
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not ApplicabilityStatus or type(self.reason_codes) is not tuple:
            raise TypeError("ApplicabilityResult requires a status enum and immutable reason tuple")


@dataclass(frozen=True)
class EvaluatorFailure:
    error_code: str
    diagnostic: str


_INAPPLICABLE_REASONS = frozenset(
    {
        RECORD_SUPERSEDED,
        BODY_MISMATCH,
        ENVIRONMENT_MISMATCH,
        FRAME_MISMATCH,
        TEMPORALLY_NOT_YET_VALID,
        TEMPORALLY_EXPIRED,
    }
)


def evaluate_experience_use(
    document: object,
    identity_agent_id: str,
    experience_id: str,
    target_context: TargetContext,
    intended_use: IntendedUse,
) -> ApplicabilityResult | EvaluatorFailure:
    """Validate full history, select exact ID, and evaluate without side effects.

    Raw intended-use values are API errors before validation or error
    wrapping. Expected domain failures and unexpected internal failures use
    an independent failure value, never a fourth applicability status.
    """
    if type(intended_use) is not IntendedUse:
        raise TypeError("intended_use must be an IntendedUse enum member")
    if not isinstance(target_context, TargetContext):
        raise TypeError("target_context must be a TargetContext value")

    try:
        validate_experience_document(document, identity_agent_id)
        selected = _select_exact_id(document, experience_id)
        return _evaluate_selected(document, selected, target_context, intended_use)
    except ExperienceValidationError as exc:
        return EvaluatorFailure(exc.code, exc.diagnostic)
    except Exception as exc:
        return EvaluatorFailure(EVALUATOR_INTERNAL_ERROR, f"internal applicability failure: {exc}")


def _select_exact_id(document: dict, experience_id: str) -> dict:
    for record in document["records"]:
        if record["experience_id"] == experience_id:
            return record
    raise ExperienceValidationError(EXPERIENCE_NOT_FOUND, f"experience_id not found: {experience_id!r}")


def _evaluate_selected(
    document: dict,
    selected: dict,
    context: TargetContext,
    intended_use: IntendedUse,
) -> ApplicabilityResult:
    if intended_use is IntendedUse.HISTORICAL_QUERY:
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE, (HISTORICAL_QUERY_PERMITTED,))
    if selected["kind"] == KIND_BODY_PARAMETER_OBSERVATION:
        return ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, (INTENDED_USE_NOT_ALLOWED,))
    return _evaluate_navigation(document, selected, context)


def _evaluate_navigation(document: dict, selected: dict, context: TargetContext) -> ApplicabilityResult:
    # Append in the frozen order: lifecycle, Body, environment, frame, time.
    reasons: list[str] = []
    selected_id = selected["experience_id"]
    if any(record["supersedes"] == selected_id for record in document["records"]):
        reasons.append(RECORD_SUPERSEDED)

    dependencies = selected["dependencies"]
    body = dependencies["body"]
    if body["mode"] == MODE_EXACT:
        reason = _exact_match_reason(body["id"], context.body_instance_id, BODY_MISMATCH, BODY_CONTEXT_MISSING)
        if reason is not None:
            reasons.append(reason)

    environment = dependencies["environment"]
    reason = _exact_match_reason(
        environment["id"], context.environment_id, ENVIRONMENT_MISMATCH, ENVIRONMENT_CONTEXT_MISSING
    )
    if reason is not None:
        reasons.append(reason)

    frame = dependencies["frame"]
    reason = _exact_match_reason(frame["id"], context.frame_id, FRAME_MISMATCH, FRAME_CONTEXT_MISSING)
    if reason is not None:
        reasons.append(reason)

    temporal_reason = _temporal_reason(dependencies["temporal"], context.evaluation_time)
    if temporal_reason is not None:
        reasons.append(temporal_reason)

    if any(reason in _INAPPLICABLE_REASONS for reason in reasons):
        return ApplicabilityResult(ApplicabilityStatus.INAPPLICABLE, tuple(reasons))
    if reasons:
        return ApplicabilityResult(ApplicabilityStatus.UNKNOWN, tuple(reasons))
    return ApplicabilityResult(ApplicabilityStatus.APPLICABLE, (ALL_REQUIRED_CONSTRAINTS_MATCH,))


def _exact_match_reason(expected: str, current: str | None, mismatch: str, missing: str) -> str | None:
    if current is None:
        return missing
    if current != expected:
        return mismatch
    return None


def _temporal_reason(temporal: dict, evaluation_time: str | None) -> str | None:
    mode = temporal["mode"]
    if mode == MODE_UNKNOWN:
        return TEMPORAL_DEPENDENCY_UNKNOWN
    if mode != MODE_INTERVAL:
        return None
    if evaluation_time is None:
        return EVALUATION_TIME_MISSING
    current = _utc_order_key(evaluation_time)
    if current < _utc_order_key(temporal["valid_from"]):
        return TEMPORALLY_NOT_YET_VALID
    if current >= _utc_order_key(temporal["valid_until"]):
        return TEMPORALLY_EXPIRED
    return None
