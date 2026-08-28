# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence

from tools.oracle.normalize import assert_no_raw_tokens, normalize_json

OBSERVATION_SCHEMA = "trillionnium.oracle-observation.v1"
LANES = {"immutable", "instrumented"}
P0_SURFACES = {"database_effects", "hooks", "provider_intents", "durable_events"}
P1_SURFACES = {"http", "grpc", "realtime", "session", "account"}
P2_SURFACES = {"metrics", "performance"}


@dataclass(frozen=True, slots=True)
class Divergence:
    severity: str
    surface: str
    path: str
    immutable: Any
    instrumented: Any
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "surface": self.surface,
            "path": self.path,
            "immutable": self.immutable,
            "instrumented": self.instrumented,
            "reason": self.reason,
        }


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _required_string(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"observation missing non-empty {field}")
    return item


def validate_observation(value: dict[str, Any]) -> None:
    if value.get("schema") != OBSERVATION_SCHEMA:
        raise ValueError("observation schema mismatch")
    lane = _required_string(value, "lane")
    if lane not in LANES:
        raise ValueError(f"unsupported observation lane: {lane}")
    for field in ("run_id", "case_id", "input_sha256"):
        _required_string(value, field)
    attempt = value.get("attempt")
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError("observation attempt must be a positive integer")
    if not isinstance(value.get("surfaces"), dict):
        raise ValueError("observation surfaces must be an object")
    assert_no_raw_tokens(value)


def normalize_observation(
    value: dict[str, Any], registry: dict[str, Any]
) -> dict[str, Any]:
    validate_observation(value)
    normalized = deepcopy(value)
    # These fields identify one execution but are not protocol or behavior output.
    normalized.pop("run_id", None)
    normalized.pop("lane", None)
    normalized.pop("attempt", None)
    surfaces = normalized["surfaces"]
    for key, payload in list(surfaces.items()):
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("surface"), str)
            and "value" in payload
        ):
            surfaces[key] = {
                "surface": payload["surface"],
                "value": normalize_json(
                    payload["value"], payload["surface"], registry
                ),
            }
    return normalized


def normalized_hash(value: dict[str, Any], registry: dict[str, Any]) -> str:
    return sha256(canonical_json(normalize_observation(value, registry)))


def _severity(surface: str) -> str:
    if surface in P0_SURFACES:
        return "P0"
    if surface in P1_SURFACES:
        return "P1"
    if surface in P2_SURFACES:
        return "P2"
    return "P3"


def _diff(left: Any, right: Any, surface: str, path: str = "$") -> list[Divergence]:
    if type(left) is not type(right):
        return [
            Divergence(
                _severity(surface), surface, path, left, right, "type mismatch"
            )
        ]
    if isinstance(left, dict):
        output: list[Divergence] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left:
                output.append(
                    Divergence(
                        _severity(surface),
                        surface,
                        child,
                        None,
                        right[key],
                        "missing from immutable",
                    )
                )
            elif key not in right:
                output.append(
                    Divergence(
                        _severity(surface),
                        surface,
                        child,
                        left[key],
                        None,
                        "missing from instrumented",
                    )
                )
            else:
                output.extend(_diff(left[key], right[key], surface, child))
        return output
    if isinstance(left, list):
        output = []
        if len(left) != len(right):
            output.append(
                Divergence(
                    _severity(surface),
                    surface,
                    f"{path}.length",
                    len(left),
                    len(right),
                    "length mismatch",
                )
            )
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            output.extend(
                _diff(left_item, right_item, surface, f"{path}[{index}]")
            )
        return output
    if left != right:
        return [
            Divergence(
                _severity(surface), surface, path, left, right, "value mismatch"
            )
        ]
    return []


def compare_pair(
    immutable: dict[str, Any],
    instrumented: dict[str, Any],
    registry: dict[str, Any],
) -> list[Divergence]:
    for value in (immutable, instrumented):
        validate_observation(value)
    for field in ("case_id", "attempt", "input_sha256"):
        if immutable[field] != instrumented[field]:
            raise ValueError(f"observation pair identity mismatch: {field}")
    if immutable["lane"] != "immutable" or instrumented["lane"] != "instrumented":
        raise ValueError("observation lanes are not ordered immutable/instrumented")

    left = normalize_observation(immutable, registry)
    right = normalize_observation(instrumented, registry)
    left_surfaces = left.pop("surfaces")
    right_surfaces = right.pop("surfaces")
    divergences = _diff(left, right, "observation_metadata")
    for surface in sorted(set(left_surfaces) | set(right_surfaces)):
        if surface not in left_surfaces:
            divergences.append(
                Divergence(
                    _severity(surface),
                    surface,
                    "$",
                    None,
                    right_surfaces[surface],
                    "surface missing from immutable",
                )
            )
        elif surface not in right_surfaces:
            divergences.append(
                Divergence(
                    _severity(surface),
                    surface,
                    "$",
                    left_surfaces[surface],
                    None,
                    "surface missing from instrumented",
                )
            )
        else:
            divergences.extend(
                _diff(left_surfaces[surface], right_surfaces[surface], surface)
            )
    return divergences


def compare_corpus(
    observations: Sequence[dict[str, Any]],
    registry: dict[str, Any],
    required_attempts: int = 10,
) -> dict[str, Any]:
    if required_attempts < 2:
        raise ValueError("required_attempts must be at least 2")
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for observation in observations:
        validate_observation(observation)
        key = (observation["case_id"], observation["attempt"])
        lane = observation["lane"]
        if lane in by_key[key]:
            raise ValueError(f"duplicate observation for {key} lane {lane}")
        by_key[key][lane] = observation

    cases = sorted({case_id for case_id, _ in by_key})
    if not cases:
        raise ValueError("oracle differential corpus is empty")

    divergences: list[dict[str, Any]] = []
    stability: list[dict[str, Any]] = []
    for case_id in cases:
        attempts = sorted(
            attempt for candidate, attempt in by_key if candidate == case_id
        )
        expected = list(range(1, required_attempts + 1))
        if attempts != expected:
            raise ValueError(
                f"{case_id}: attempts {attempts} do not equal required {expected}"
            )
        lane_hashes = {lane: set() for lane in LANES}
        for attempt in attempts:
            pair = by_key[(case_id, attempt)]
            if set(pair) != LANES:
                raise ValueError(
                    f"{case_id}/{attempt}: immutable and instrumented lanes are required"
                )
            for lane in LANES:
                lane_hashes[lane].add(normalized_hash(pair[lane], registry))
            divergences.extend(
                divergence.as_dict()
                | {"case_id": case_id, "attempt": attempt}
                for divergence in compare_pair(
                    pair["immutable"], pair["instrumented"], registry
                )
            )
        for lane in sorted(LANES):
            if len(lane_hashes[lane]) != 1:
                divergences.append(
                    {
                        "severity": "P1",
                        "surface": "nondeterminism",
                        "path": "$",
                        "immutable": sorted(lane_hashes["immutable"]),
                        "instrumented": sorted(lane_hashes["instrumented"]),
                        "reason": f"{case_id}/{lane} produced multiple normalized hashes",
                        "case_id": case_id,
                        "attempt": None,
                    }
                )
        stability.append(
            {
                "case_id": case_id,
                "attempts": required_attempts,
                "immutable_hashes": sorted(lane_hashes["immutable"]),
                "instrumented_hashes": sorted(lane_hashes["instrumented"]),
            }
        )

    counts = {
        severity: sum(
            1 for divergence in divergences if divergence["severity"] == severity
        )
        for severity in ("P0", "P1", "P2", "P3")
    }
    evidence: dict[str, Any] = {
        "schema": "trillionnium.oracle-differential-candidate.v1",
        "project_id": "trillionnium-game",
        "status": "candidate-unreviewed",
        "required_attempts": required_attempts,
        "case_count": len(cases),
        "observation_count": len(observations),
        "stability": stability,
        "divergence_counts": counts,
        "divergences": divergences,
        "claims": {
            "instrumented_equivalence": False,
            "sg2_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
            "public_online": False,
        },
    }
    assert_no_raw_tokens(evidence)
    evidence["content_sha256"] = sha256(canonical_json(evidence))
    return evidence
