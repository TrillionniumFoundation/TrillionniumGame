"""One fail-closed structural admission contract for retained evidence.

Local validation checks metadata, exact review binding and retained bytes. It is
not a GitHub approval, an independent review, a live run verifier or permission to
promote a release. External provenance and current-target acceptance remain
separate prerequisites. Historical diagnostic entries never enter this path.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = "TrillionniumFoundation/TrillionniumGame"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS = 256
MAX_RETAINED_BYTES = 256 * 1024 * 1024
HEX40 = re.compile(r"[a-f0-9]{40}")
HEX64 = re.compile(r"(?:sha256:)?[a-f0-9]{64}")
EVIDENCE_TYPES = frozenset({
    "manifest", "unit", "property", "fuzz", "wire-differential",
    "database-differential", "runtime-differential", "sdk-blackbox",
    "migration-rehearsal", "fault-injection", "performance", "endurance",
    "security-review", "penetration-test", "backup-restore", "canary",
    "cutover", "retirement",
})


class AdmissionError(ValueError):
    """A structural admission prerequisite is absent or contradictory."""


def need(condition: bool, message: str) -> None:
    if not condition:
        raise AdmissionError(message)


def canonical_text(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 4096
            and value == value.strip() and not any(ord(c) < 32 for c in value))


def exact_alias(row: dict[str, Any], *paths: str) -> Any:
    values = []
    for path in paths:
        value: Any = row
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            values.append(value)
    if not values:
        return None
    # JSON booleans must not compare equal to numbers in alias decisions.
    encoded = [json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)
               for v in values]
    need(len(set(encoded)) == 1, "conflicting aliases: " + ", ".join(paths))
    return values[0]


def parse_time(value: Any) -> datetime:
    need(isinstance(value, str) and len(value) <= 64, "invalid timestamp")
    need(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value) is not None,
         "timestamp must be a timezone-qualified ISO datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    need(parsed.tzinfo is not None, "timestamp timezone missing")
    return parsed.astimezone(timezone.utc)


def clock(now: datetime | None = None) -> datetime:
    value = now if now is not None else datetime.now(timezone.utc)
    need(isinstance(value, datetime) and value.tzinfo is not None, "clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        need(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise AdmissionError("non-finite JSON number: " + value)


def load_object(path: Path) -> dict[str, Any]:
    need(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), "JSON input must be a regular non-symlink file")
    need(0 < path.stat().st_size <= MAX_JSON_BYTES, "JSON input size exceeds budget")
    with path.open("rb") as stream:
        value = stream.read(MAX_JSON_BYTES + 1)
    need(0 < len(value) <= MAX_JSON_BYTES, "JSON input is empty or exceeds byte limit")
    parsed = json.loads(value.decode("utf-8"), object_pairs_hook=_pairs,
                        parse_constant=_constant)
    need(isinstance(parsed, dict), "JSON root must be an object")
    return parsed


def repository_path(root: Path, value: Any) -> Path:
    need(canonical_text(value) and "\\" not in value, "invalid retained path")
    path = PurePosixPath(value)
    need(not path.is_absolute() and str(path) == value
         and all(part not in (".", "..") for part in path.parts), "unsafe retained path")
    resolved_root = root.resolve(strict=True)
    current = resolved_root
    for part in path.parts:
        current = current / part
        need(not current.is_symlink(), "symlink in retained path")
    need(current.is_file() and stat.S_ISREG(current.stat().st_mode), "retained file missing or non-regular")
    need(current.resolve(strict=True).is_relative_to(resolved_root), "retained path escapes root")
    return current


def digest(value: Any) -> str:
    need(isinstance(value, str) and HEX64.fullmatch(value) is not None, "artifact SHA-256 required")
    return value.removeprefix("sha256:")


def artifact_identity(item: Any) -> tuple[str, str, str, int]:
    need(isinstance(item, dict), "artifact must be an object")
    name = item.get("name", item.get("profile"))
    need(canonical_text(name), "artifact name required")
    path = item.get("path")
    need(canonical_text(path), "retained artifact path required")
    sha = digest(exact_alias(item, "sha256", "digest"))
    size = exact_alias(item, "size_bytes", "size")
    need(type(size) is int and 0 < size <= MAX_ARTIFACT_BYTES, "artifact size must be bounded and positive")
    return name, path, sha, size


def verify_artifact(root: Path, item: dict[str, Any]) -> tuple[str, str, str, int]:
    identity = artifact_identity(item)
    _, relative, expected, size = identity
    path = repository_path(root, relative)
    need(path.stat().st_size == size, "retained artifact size mismatch")
    sha = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(min(1024 * 1024, size + 1 - total))
            if not block:
                break
            total += len(block)
            need(total <= size, "retained artifact grew during validation")
            sha.update(block)
    need(total == size and sha.hexdigest() == expected, "retained artifact digest mismatch")
    return identity


def target_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    repository = exact_alias(row, "candidate.repository", "target.repository", "target_repository")
    commit = exact_alias(row, "candidate.commit", "target.commit", "target_commit")
    tree = exact_alias(row, "candidate.tree", "target.tree", "target_tree")
    need(repository == REPOSITORY, "wrong target repository")
    need(isinstance(commit, str) and HEX40.fullmatch(commit) is not None, "exact target commit required")
    need(isinstance(tree, str) and HEX40.fullmatch(tree) is not None, "exact target tree required")
    return repository, commit, tree


def accepted_review(row: dict[str, Any], *, target_commit: str | None = None,
                    target_tree: str | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    try:
        review = exact_alias(row, "independent_review", "review", "validity.review")
        need(isinstance(review, dict), "review required")
        need(review.get("decision") == "accepted", "independent acceptance required")
        need(canonical_text(review.get("reviewer_identity")), "reviewer identity required")
        need(review.get("independent") is True and review.get("self_review") is False,
             "review must be independent and not self-review")
        need(parse_time(review.get("reviewed_at")) <= clock(now), "future review is invalid")
        for key, expected in (("reviewed_commit", target_commit), ("reviewed_tree", target_tree)):
            observed = review.get(key)
            need(isinstance(observed, str) and HEX40.fullmatch(observed) is not None, "exact review identity required")
            need(expected is None or observed == expected, "review target mismatch")
        return review
    except (AdmissionError, ValueError, TypeError, OverflowError, RecursionError):
        return None


def validate_schema(value: Any, schema: dict[str, Any]) -> None:
    """Evaluate the closed keyword subset used by the repository v1 schema.

    Only local references are supported. An unknown validation keyword rejects
    rather than silently bypassing future schema requirements. No remote fetch,
    extension loading, coercion or dynamic code evaluation is performed.
    """
    keywords = {"$schema", "$id", "$defs", "$ref", "title", "description", "type",
                "const", "enum", "properties", "additionalProperties", "required",
                "items", "minItems", "uniqueItems", "minLength", "pattern",
                "minimum", "format"}
    budget = [100000]

    def visit(item: Any, rule: Any, depth: int) -> None:
        budget[0] -= 1
        need(depth <= 64 and budget[0] >= 0, "schema evaluation budget exceeded")
        need(isinstance(rule, dict) and not (set(rule) - keywords), "unsupported evidence schema keyword")
        if "$ref" in rule:
            ref = rule["$ref"]
            need(isinstance(ref, str) and ref.startswith("#/$defs/")
                 and re.fullmatch(r"#/[A-Za-z0-9_$/-]+", ref) is not None, "only local schema references supported")
            target: Any = schema
            for part in ref[2:].split("/"):
                need(isinstance(target, dict) and part in target, "unresolved schema reference")
                target = target[part]
            visit(item, target, depth + 1)
        actual_type = ("null" if item is None else "boolean" if type(item) is bool
                       else "integer" if type(item) is int else "number" if type(item) is float
                       else "string" if isinstance(item, str) else "object" if isinstance(item, dict)
                       else "array" if isinstance(item, list) else "invalid")
        if "type" in rule:
            types = rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
            need(actual_type in types or (actual_type == "integer" and "number" in types), "evidence schema type mismatch")
        canonical = lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if "const" in rule:
            need(canonical(item) == canonical(rule["const"]), "evidence schema const mismatch")
        if "enum" in rule:
            need(any(canonical(item) == canonical(option) for option in rule["enum"]), "evidence schema enum mismatch")
        if isinstance(item, dict):
            need(set(rule.get("required", [])) <= set(item), "evidence schema required field missing")
            properties = rule.get("properties", {})
            if rule.get("additionalProperties") is False:
                need(not (set(item) - set(properties)), "unexpected evidence schema property")
            for name, child in item.items():
                if name in properties:
                    visit(child, properties[name], depth + 1)
        if isinstance(item, list):
            need(len(item) >= rule.get("minItems", 0), "evidence schema array too short")
            if rule.get("uniqueItems") is True:
                need(len({canonical(v) for v in item}) == len(item), "duplicate evidence schema item")
            if "items" in rule:
                for child in item:
                    visit(child, rule["items"], depth + 1)
        if isinstance(item, str):
            need(len(item) >= rule.get("minLength", 0), "evidence schema string too short")
            if "pattern" in rule:
                need(re.search(rule["pattern"], item) is not None, "evidence schema pattern mismatch")
            if "format" in rule:
                need(rule["format"] == "date-time", "unsupported evidence schema format")
                parse_time(item)
        if type(item) in (int, float) and "minimum" in rule:
            need(item >= rule["minimum"], "evidence schema numeric bound")

    visit(value, schema, 0)


def validate_entry(row: dict[str, Any], *, root: Path, now: datetime | None = None) -> None:
    """Validate the retained candidate envelope; never create an approval."""
    need(isinstance(row, dict), "evidence entry must be an object")
    current = clock(now)
    evidence_id = row.get("evidence_id")
    need(isinstance(evidence_id, str) and re.fullmatch(r"TG-EV-[A-Z0-9][A-Z0-9._-]{5,127}", evidence_id) is not None,
         "invalid evidence ID")
    need(row.get("status") == "accepted", "accepted index status required")
    need(exact_alias(row, "compatibility_credit", "claim_credit", "validity.compatibility_credit", "validity.claim_credit") is True,
         "explicit evidence credit required, including manifests")
    need(exact_alias(row, "schema_valid", "validity.schema_valid") is True, "schema validation required")
    need(exact_alias(row, "target_identity_verified_by_current_repo", "exact_target_identity", "validity.exact_target_identity") is True,
         "target identity verification required")
    repository, commit, tree = target_identity(row)
    review = accepted_review(row, target_commit=commit, target_tree=tree, now=current)
    need(review is not None and canonical_text(review.get("reviewer_role")), "exact independent review and role required")
    expiry_raw = exact_alias(row, "expires_at", "validity.expires_at")
    expiry = parse_time(expiry_raw) if expiry_raw is not None else None
    need(expiry is None or current < expiry, "expired evidence")
    evidence_type = row.get("evidence_type")
    need(isinstance(evidence_type, str) and evidence_type in EVIDENCE_TYPES, "unsupported evidence type")
    relative = exact_alias(row, "path", "manifest_path", "source.path")
    manifest_path = repository_path(root, relative)
    manifest = load_object(manifest_path)
    schema = load_object(repository_path(root, "docs/evidence/schemas/trillionnium-evidence-v1.schema.json"))
    validate_schema(manifest, schema)
    need(manifest.get("schema") == "trillionnium.evidence.v1", "retained evidence schema mismatch")
    need(manifest.get("evidence_id") == evidence_id and manifest.get("evidence_type") == evidence_type,
         "retained evidence ID/type mismatch")
    need(manifest.get("status") == "passed" and manifest.get("generated_by_automation") is True,
         "retained evidence must record passed automation")
    need(target_identity(manifest) == (repository, commit, tree), "retained target mismatch")
    need(manifest.get("review") == review, "retained and indexed review differ")
    need(manifest.get("expires_at") == expiry_raw, "retained and indexed expiry differ")
    for key in ("claim_ids", "gate_ids", "task_ids", "parity_ids"):
        values = row.get(key)
        need(isinstance(values, list) and len(values) <= 10000
             and all(canonical_text(v) for v in values)
             and len(values) == len(set(values)), "invalid evidence mapping: " + key)
        need(key == "parity_ids" or bool(values), "empty required evidence mapping: " + key)
        need(manifest.get(key) == values, "retained evidence mapping mismatch: " + key)
    started = parse_time(manifest.get("started_at"))
    completed = parse_time(manifest.get("completed_at"))
    reviewed = parse_time(review["reviewed_at"])
    need(started <= completed <= reviewed <= current, "execution/review time order invalid")
    need(expiry is None or completed < expiry, "expiry predates execution")
    result = manifest.get("result")
    need(isinstance(result, dict), "retained result required")
    count = result.get("assertions_total")
    passed = result.get("assertions_passed")
    need(type(count) is int and type(passed) is int and count > 0 and passed == count,
         "nonempty complete passing assertions required")
    divergences = result.get("divergences")
    need(isinstance(divergences, list) and len(divergences) <= 10000, "divergence list required")
    for divergence in divergences:
        need(isinstance(divergence, dict), "divergence must be an object")
        need(divergence.get("severity") in ("P0", "P1", "P2", "informational"), "invalid divergence severity")
        need(divergence.get("status") in ("open", "explained", "fixed", "waived"), "invalid divergence status")
        need(not (divergence["severity"] in ("P0", "P1") and divergence["status"] != "fixed"),
             "blocking divergence is not verified fixed")
    artifacts = exact_alias(row, "artifacts", "source.artifacts")
    need(isinstance(artifacts, list) and 0 < len(artifacts) <= MAX_ARTIFACTS, "bounded nonempty artifacts required")
    retained = manifest.get("artifacts")
    need(isinstance(retained, list) and len(retained) == len(artifacts), "retained artifact set mismatch")
    identities = [artifact_identity(a) for a in artifacts]
    need(len({i[0] for i in identities}) == len(identities), "duplicate artifact name")
    need(len({i[1] for i in identities}) == len(identities), "duplicate artifact path")
    need(sum(i[3] for i in identities) <= MAX_RETAINED_BYTES, "total retained bytes exceed budget")
    need(sorted(identities) == sorted(artifact_identity(a) for a in retained), "retained artifact identities differ")
    for artifact in artifacts:
        need(artifact["path"] != relative, "evidence cannot cite itself as an artifact")
        verify_artifact(root, artifact)


def entry_eligible(row: dict[str, Any], *, root: Path, now: datetime | None = None) -> bool:
    try:
        validate_entry(row, root=root, now=now)
        return True
    except (OSError, AdmissionError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return False


def validate_gap_evidence(gap: dict[str, Any], evidence: dict[str, dict[str, Any]],
                          *, root: Path, now: datetime | None = None) -> None:
    """Check declared closure, including type coverage and a single target cohort."""
    need(not gap.get("external_dependency"), "closed gap retains external dependency")
    ids = gap.get("evidence_ids")
    need(isinstance(ids, list) and 0 < len(ids) <= MAX_ARTIFACTS
         and all(canonical_text(v) for v in ids) and len(ids) == len(set(ids)),
         "closed gap requires unique indexed evidence IDs")
    required = gap.get("required_evidence_types")
    need(isinstance(required, list) and required
         and all(isinstance(v, str) and v in EVIDENCE_TYPES for v in required)
         and len(required) == len(set(required)), "closed gap requires valid evidence types")
    types = set()
    targets = set()
    for evidence_id in ids:
        need(evidence_id in evidence, "closed gap cites unknown evidence")
        row = evidence[evidence_id]
        validate_entry(row, root=root, now=now)
        types.add(row["evidence_type"])
        targets.add(target_identity(row))
    need(len(targets) == 1, "closed gap mixes candidate identities")
    need(set(required) <= types, "closed gap is missing required evidence types")


def index_rows(index: dict[str, Any]) -> list[dict[str, Any]]:
    keys = [name for name in ("evidence", "items", "entries") if name in index]
    need(len(keys) == 1, "evidence index must have exactly one entry collection")
    values = index[keys[0]]
    need(isinstance(values, list) and len(values) <= 10000
         and all(isinstance(v, dict) for v in values), "invalid evidence index rows")
    ids = [v.get("evidence_id") for v in values]
    need(all(canonical_text(v) for v in ids) and len(ids) == len(set(ids)), "missing or duplicate evidence ID")
    return values
