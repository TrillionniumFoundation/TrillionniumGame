"""Nakama adapter for the unsigned deterministic World transition v1 contract."""

from .adapter import (
    prepare_world_transition,
    prepared_from_canonical_request,
    verify_world_result,
)
from .contracts import (
    CONTRACT_VERSION,
    NakamaAuthorityContext,
    PreparedWorldTransition,
    TransitionContractError,
    VerifiedWorldTransition,
)
from .shadow import (
    ShadowObservation,
    compare_jsonl,
    compare_observations,
)

__all__ = [
    "CONTRACT_VERSION",
    "NakamaAuthorityContext",
    "PreparedWorldTransition",
    "ShadowObservation",
    "TransitionContractError",
    "VerifiedWorldTransition",
    "compare_jsonl",
    "compare_observations",
    "prepare_world_transition",
    "prepared_from_canonical_request",
    "verify_world_result",
]
