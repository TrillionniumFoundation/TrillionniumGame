from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

SHA256_PREFIX = "sha256:"


class ConformanceError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return SHA256_PREFIX + hashlib.sha256(value).hexdigest()


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConformanceError(f"{field} must be a non-empty string")
    return value


def _observation(raw: Mapping[str, Any], policy: Mapping[str, Any], corpus: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("schema") != "trillionnium.runtime-engine-observation.v1":
        raise ConformanceError("unsupported observation schema")
    engine = _string(raw.get("engine"), "observation.engine")
    lane = _string(raw.get("lane"), "observation.lane")
    if engine not in set(policy.get("required_engines", [])):
        raise ConformanceError(f"unknown engine {engine}")
    if lane not in set(policy.get("lanes", [])):
        raise ConformanceError(f"unknown lane {lane}")
    case_id = _string(raw.get("case_id"), "observation.case_id")
    cases = {item["id"]: item for item in corpus.get("cases", []) if isinstance(item, dict)}
    case = cases.get(case_id)
    if case is None or case.get("engine") != engine or case.get("category") != raw.get("category"):
        raise ConformanceError(f"observation does not match corpus case {case_id}")
    if raw.get("input_sha256") != case.get("input_sha256"):
        raise ConformanceError(f"observation input digest mismatch for {case_id}")
    attempt = raw.get("attempt")
    if not isinstance(attempt, int) or attempt < 1 or attempt > int(policy.get("attempts_per_lane", 10)):
        raise ConformanceError(f"invalid attempt for {case_id}")
    host_calls = raw.get("host_calls")
    resources = raw.get("resources")
    if not isinstance(host_calls, list) or not isinstance(resources, dict):
        raise ConformanceError(f"invalid host_calls/resources for {case_id}")
    forbidden = set(policy.get("forbidden_host_capabilities", []))
    for call in host_calls:
        if not isinstance(call, dict):
            raise ConformanceError(f"host call for {case_id} must be an object")
        capability = call.get("capability")
        if capability in forbidden:
            raise ConformanceError(f"forbidden host capability observed: {capability}")
    return {
        "schema": raw["schema"],
        "engine": engine,
        "lane": lane,
        "case_id": case_id,
        "category": raw["category"],
        "attempt": attempt,
        "input_sha256": raw["input_sha256"],
        "return_value": raw.get("return_value"),
        "error": raw.get("error"),
        "stdout": raw.get("stdout"),
        "host_calls": host_calls,
        "resources": resources,
    }


def _semantic(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "engine": value["engine"],
        "case_id": value["case_id"],
        "category": value["category"],
        "input_sha256": value["input_sha256"],
        "return_value": value["return_value"],
        "error": value["error"],
        "stdout": value["stdout"],
        "host_calls": value["host_calls"],
    }


def _diff(path: str, left: Any, right: Any, output: list[dict[str, Any]], severity: str) -> None:
    if type(left) is not type(right):
        output.append({"severity": severity, "path": path, "kind": "type", "oracle": left, "candidate": right})
        return
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                output.append({"severity": severity, "path": child, "kind": "missing", "oracle": left.get(key), "candidate": right.get(key)})
            else:
                _diff(child, left[key], right[key], output, severity)
    elif isinstance(left, list):
        if len(left) != len(right):
            output.append({"severity": severity, "path": path, "kind": "length", "oracle": len(left), "candidate": len(right)})
        for index, (a, b) in enumerate(zip(left, right)):
            _diff(f"{path}[{index}]", a, b, output, severity)
    elif left != right:
        output.append({"severity": severity, "path": path, "kind": "value", "oracle": left, "candidate": right})


def compare_observations(
    observations: Iterable[Mapping[str, Any]],
    policy: Mapping[str, Any],
    corpus: Mapping[str, Any],
) -> dict[str, Any]:
    if any(corpus.get("claims", {}).values()) or any(policy.get("claims", {}).values()):
        raise ConformanceError("policy/corpus contains positive compatibility claim")
    attempts = int(policy.get("attempts_per_lane", 10))
    required_lanes = list(policy.get("lanes", []))
    cases = {item["id"]: item for item in corpus.get("cases", []) if isinstance(item, dict)}
    required_categories = set(policy.get("required_categories", []))
    for engine in policy.get("required_engines", []):
        categories = {item["category"] for item in cases.values() if item.get("engine") == engine}
        missing = sorted(required_categories - categories)
        if missing:
            raise ConformanceError(f"engine {engine} corpus misses categories {missing}")

    grouped: dict[tuple[str, str, str], dict[int, dict[str, Any]]] = {}
    for raw in observations:
        value = _observation(raw, policy, corpus)
        key = (value["engine"], value["case_id"], value["lane"])
        lane_attempts = grouped.setdefault(key, {})
        if value["attempt"] in lane_attempts:
            raise ConformanceError(f"duplicate observation attempt for {key}")
        lane_attempts[value["attempt"]] = value

    divergences: list[dict[str, Any]] = []
    stability: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    budgets = policy.get("resource_budgets", {})
    for case_id, case in sorted(cases.items()):
        engine = case["engine"]
        lane_semantics: dict[str, dict[str, Any]] = {}
        for lane in required_lanes:
            key = (engine, case_id, lane)
            values = grouped.get(key)
            expected_attempts = set(range(1, attempts + 1))
            if values is None or set(values) != expected_attempts:
                raise ConformanceError(f"missing/extra attempts for {key}")
            hashes = {sha256(canonical_bytes(_semantic(value))) for value in values.values()}
            stable = len(hashes) == 1
            stability.append({"engine": engine, "case_id": case_id, "lane": lane, "stable": stable, "hashes": sorted(hashes)})
            if not stable:
                divergences.append({"severity": "P1", "engine": engine, "case_id": case_id, "path": "$", "kind": "lane_nondeterminism", "lane": lane})
            lane_semantics[lane] = _semantic(values[1])
            maxima: dict[str, int] = {}
            for value in values.values():
                for name, raw_amount in value["resources"].items():
                    if not isinstance(raw_amount, int) or raw_amount < 0:
                        raise ConformanceError(f"invalid resource value {name} for {key}")
                    maxima[name] = max(maxima.get(name, 0), raw_amount)
            for name, maximum in sorted(maxima.items()):
                budget = budgets.get(name)
                exceeded = isinstance(budget, int) and maximum > budget
                resources.append({"engine": engine, "case_id": case_id, "lane": lane, "resource": name, "maximum": maximum, "budget": budget, "exceeded": exceeded})
                if exceeded:
                    divergences.append({"severity": "P2", "engine": engine, "case_id": case_id, "path": f"$.resources.{name}", "kind": "resource_budget_exceeded", "lane": lane, "maximum": maximum, "budget": budget})
        before = len(divergences)
        _diff("$.return_value", lane_semantics[required_lanes[0]]["return_value"], lane_semantics[required_lanes[1]]["return_value"], divergences, "P1")
        _diff("$.error", lane_semantics[required_lanes[0]]["error"], lane_semantics[required_lanes[1]]["error"], divergences, "P1")
        _diff("$.stdout", lane_semantics[required_lanes[0]]["stdout"], lane_semantics[required_lanes[1]]["stdout"], divergences, "P1")
        _diff("$.host_calls", lane_semantics[required_lanes[0]]["host_calls"], lane_semantics[required_lanes[1]]["host_calls"], divergences, "P0")
        for item in divergences[before:]:
            item.setdefault("engine", engine)
            item.setdefault("case_id", case_id)

    observed_keys = set(grouped)
    expected_keys = {(case["engine"], case_id, lane) for case_id, case in cases.items() for lane in required_lanes}
    extra = sorted(observed_keys - expected_keys)
    if extra:
        raise ConformanceError(f"unknown observation groups: {extra}")

    counts = {severity: sum(1 for item in divergences if item["severity"] == severity) for severity in ("P0", "P1", "P2", "P3")}
    result = {
        "schema": "trillionnium.runtime-engine-conformance-evidence.v1",
        "project_id": "trillionnium-game",
        "status": "semantic-candidate" if counts["P0"] == 0 and counts["P1"] == 0 else "blocked",
        "case_count": len(cases),
        "attempts_per_lane": attempts,
        "observation_count": len(observed_keys) * attempts,
        "divergence_counts": counts,
        "divergences": sorted(divergences, key=lambda item: canonical_bytes(item)),
        "stability": stability,
        "resources": resources,
        "claims": {
            "semantic_candidate": counts["P0"] == 0 and counts["P1"] == 0,
            "resource_profile_candidate": counts["P2"] == 0,
            "javascript_engine_selected": False,
            "lua_engine_selected": False,
            "runtime_semantic_equivalence": False,
            "sg3_complete": False,
            "production_ready": False,
        },
    }
    result["content_sha256"] = sha256(canonical_bytes(result))
    return result


def evaluate_engine_selection(
    evidence: Mapping[str, Any],
    review: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    if evidence.get("status") != "semantic-candidate" or evidence.get("divergence_counts", {}).get("P0") or evidence.get("divergence_counts", {}).get("P1"):
        raise ConformanceError("engine selection requires zero P0/P1 and semantic-candidate evidence")
    if review.get("self_approval") is not False:
        raise ConformanceError("engine selection self_approval must be false")
    author = _string(review.get("author_identity"), "review.author_identity")
    reviewers = review.get("reviewers")
    minimum = int(policy.get("minimum_independent_reviewers", 2))
    if not isinstance(reviewers, list) or len({item.get("identity") for item in reviewers if isinstance(item, dict)}) < minimum:
        raise ConformanceError(f"engine selection requires {minimum} independent reviewers")
    if any(item.get("identity") == author for item in reviewers if isinstance(item, dict)):
        raise ConformanceError("author cannot approve engine selection")
    adr_ref = _string(review.get("adr_ref"), "review.adr_ref")
    path = PurePosixPath(adr_ref)
    if path.is_absolute() or ".." in path.parts or not adr_ref.startswith("docs/adr/"):
        raise ConformanceError("engine selection ADR path is invalid")
    result = {
        "schema": "trillionnium.runtime-engine-selection-candidate.v1",
        "status": "independent-architecture-review-required",
        "evidence_sha256": _string(evidence.get("content_sha256"), "evidence.content_sha256"),
        "adr_ref": adr_ref,
        "reviewers": sorted(reviewers, key=lambda item: item["identity"]),
        "claims": {
            "engine_selection_candidate": True,
            "javascript_engine_selected": False,
            "lua_engine_selected": False,
            "runtime_semantic_equivalence": False,
            "sg3_complete": False,
            "production_ready": False,
        },
    }
    result["content_sha256"] = sha256(canonical_bytes(result))
    return result
