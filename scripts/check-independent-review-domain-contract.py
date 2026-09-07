#!/usr/bin/env python3
"""Validate the immutable candidate-specific review-domain and CODEOWNERS contract."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"
CODEOWNERS_PATH = ROOT / ".github/CODEOWNERS"

EXPECTED_DOMAINS: dict[str, dict[str, frozenset[str]]] = {
    "governance": {
        "required_roles": frozenset({
            "repository-administrator",
            "independent-program-governance-reviewer",
        }),
        "protected_paths": frozenset({
            ".github/**",
            "CURRENT_PLAN.md",
            "docs/DOCUMENTATION_AUTHORITY.json",
            "docs/GOVERNANCE.md",
            "docs/TESTING_AND_EVIDENCE.md",
            "docs/governance/**",
            "docs/review/**",
            "docs/status/PRODUCT_GATES.json",
            "docs/status/GAP_REGISTER.json",
            "docs/evidence/index.json",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-CI-001",
            "GAP-P0-GOV-001",
            "GAP-P0-PLAN-001",
        }),
    },
    "database-data-integrity": {
        "required_roles": frozenset({
            "independent-database-reviewer",
            "independent-data-integrity-reviewer",
        }),
        "protected_paths": frozenset({
            "migrations/**",
            "database/**",
            "crates/trnm-persistence-core/**",
            "crates/trnm-persistence-pg/**",
            "docs/development/SCHEMA_AUTHORITY.json",
            "docs/ARCHITECTURE.md",
            "docs/OPERATIONS_AND_RELEASE.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-DATA-001",
            "GAP-P1-OUTBOX-001",
            "GAP-P1-PG-001",
        }),
    },
    "security-cryptography": {
        "required_roles": frozenset({
            "independent-security-reviewer",
            "independent-cryptography-reviewer",
        }),
        "protected_paths": frozenset({
            "SECURITY.md",
            "docs/SECURITY_AND_PRIVACY.md",
            "crates/trnm-token-core/**",
            "crates/trnm-token-jwt-adapter/**",
            "crates/trnm-token-crypto-provider/**",
            "crates/trnm-token-jwt-provider-adapter/**",
            "crates/trnm-session-core/**",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-CRYPTO-001",
            "GAP-P1-CRYPTO-002",
        }),
    },
    "protocol-compatibility": {
        "required_roles": frozenset({
            "independent-protocol-reviewer",
            "independent-compatibility-qa-reviewer",
        }),
        "protected_paths": frozenset({
            "contracts/**",
            "crates/trnm-canonical-core/**",
            "crates/trnm-transport-core/**",
            "crates/trnm-realtime-wire/**",
            "crates/trnm-persistence-pg/src/bin/trnm-server.rs",
            "crates/trnm-persistence-pg/src/bin/trnm_server/**",
            "docs/ARCHITECTURE.md",
            "docs/COMPATIBILITY.md",
            "docs/TESTING_AND_EVIDENCE.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-SERVER-001",
            "GAP-P0-SCOPE-001",
        }),
    },
    "realtime-distributed-systems": {
        "required_roles": frozenset({
            "independent-realtime-reviewer",
            "independent-distributed-systems-reviewer",
        }),
        "protected_paths": frozenset({
            "crates/trnm-presence-core/**",
            "crates/trnm-presence-router-v2/**",
            "crates/trnm-realtime-wire/**",
            "runtime/internal/worldcommand/**",
            "runtime/internal/worldtransition/**",
            "docs/ARCHITECTURE.md",
            "docs/COMPATIBILITY.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-SERVER-001",
        }),
    },
    "operations-sre": {
        "required_roles": frozenset({
            "independent-sre-reviewer",
            "independent-recovery-reviewer",
        }),
        "protected_paths": frozenset({
            "deploy/**",
            "compose.yaml",
            ".github/workflows/database-backup-restore.yml",
            ".github/workflows/prospective-merge-gate.yml",
            "scripts/ci-pgwire-backup-restore.sh",
            "scripts/ci-trnm-server-live.sh",
            "docs/OPERATIONS_AND_RELEASE.md",
            "docs/TESTING_AND_EVIDENCE.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P1-PG-001",
        }),
    },
}

# This is a closed-world mapping from every protected path declaration to the
# exact CODEOWNERS rule expected to be effective under GitHub's last-match rule.
EXPECTED_DOMAIN_CODEOWNER_PATTERNS: dict[str, dict[str, str]] = {
    "governance": {
        ".github/**": "/.github/",
        "CURRENT_PLAN.md": "/CURRENT_PLAN.md",
        "docs/DOCUMENTATION_AUTHORITY.json": "/docs/DOCUMENTATION_AUTHORITY.json",
        "docs/GOVERNANCE.md": "/docs/GOVERNANCE.md",
        "docs/TESTING_AND_EVIDENCE.md": "/docs/TESTING_AND_EVIDENCE.md",
        "docs/governance/**": "/docs/governance/",
        "docs/review/**": "/docs/review/",
        "docs/status/PRODUCT_GATES.json": "/docs/status/",
        "docs/status/GAP_REGISTER.json": "/docs/status/",
        "docs/evidence/index.json": "/docs/evidence/",
    },
    "database-data-integrity": {
        "migrations/**": "/migrations/",
        "database/**": "/database/",
        "crates/trnm-persistence-core/**": "/crates/trnm-persistence-core/",
        "crates/trnm-persistence-pg/**": "/crates/trnm-persistence-pg/",
        "docs/development/SCHEMA_AUTHORITY.json": "/docs/development/SCHEMA_AUTHORITY.json",
        "docs/ARCHITECTURE.md": "*",
        "docs/OPERATIONS_AND_RELEASE.md": "/docs/OPERATIONS_AND_RELEASE.md",
    },
    "security-cryptography": {
        "SECURITY.md": "/SECURITY.md",
        "docs/SECURITY_AND_PRIVACY.md": "/docs/SECURITY_AND_PRIVACY.md",
        "crates/trnm-token-core/**": "/crates/trnm-token-core/",
        "crates/trnm-token-jwt-adapter/**": "/crates/trnm-token-jwt-adapter/",
        "crates/trnm-token-crypto-provider/**": "/crates/trnm-token-crypto-provider/",
        "crates/trnm-token-jwt-provider-adapter/**": "/crates/trnm-token-jwt-provider-adapter/",
        "crates/trnm-session-core/**": "/crates/trnm-session-core/",
    },
    "protocol-compatibility": {
        "contracts/**": "/contracts/",
        "crates/trnm-canonical-core/**": "/crates/trnm-canonical-core/",
        "crates/trnm-transport-core/**": "/crates/trnm-transport-core/",
        "crates/trnm-realtime-wire/**": "/crates/trnm-realtime-wire/",
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs": "/crates/trnm-persistence-pg/",
        "crates/trnm-persistence-pg/src/bin/trnm_server/**": "/crates/trnm-persistence-pg/",
        "docs/ARCHITECTURE.md": "*",
        "docs/COMPATIBILITY.md": "/docs/COMPATIBILITY.md",
        "docs/TESTING_AND_EVIDENCE.md": "/docs/TESTING_AND_EVIDENCE.md",
    },
    "realtime-distributed-systems": {
        "crates/trnm-presence-core/**": "/crates/trnm-presence-core/",
        "crates/trnm-presence-router-v2/**": "/crates/trnm-presence-router-v2/",
        "crates/trnm-realtime-wire/**": "/crates/trnm-realtime-wire/",
        "runtime/internal/worldcommand/**": "/runtime/",
        "runtime/internal/worldtransition/**": "/runtime/",
        "docs/ARCHITECTURE.md": "*",
        "docs/COMPATIBILITY.md": "/docs/COMPATIBILITY.md",
    },
    "operations-sre": {
        "deploy/**": "/deploy/",
        "compose.yaml": "/compose.yaml",
        ".github/workflows/database-backup-restore.yml": "/.github/",
        ".github/workflows/prospective-merge-gate.yml": "/.github/",
        "scripts/ci-pgwire-backup-restore.sh": "*",
        "scripts/ci-trnm-server-live.sh": "*",
        "docs/OPERATIONS_AND_RELEASE.md": "/docs/OPERATIONS_AND_RELEASE.md",
        "docs/TESTING_AND_EVIDENCE.md": "/docs/TESTING_AND_EVIDENCE.md",
    },
}

# Additional independently qualified owners may be added later, but these
# candidate-conflicted routes cannot silently disappear from the effective rule.
REQUIRED_OWNER_LOGINS = frozenset({
    "profhepta",
    "franksudoman",
    "tomasrgbsf",
})


class DomainContractError(RuntimeError):
    """The review matrix or effective CODEOWNERS routing changed its contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DomainContractError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = item
    return value


def reject_constant(value: str) -> Any:
    raise DomainContractError(f"non-finite JSON value: {value}")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except DomainContractError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise DomainContractError(f"{path}: {error}") from error
    require(isinstance(value, dict), "review matrix root must be an object")
    return value


def string_set(value: Any, label: str) -> frozenset[str]:
    require(isinstance(value, list), f"{label}: list required")
    require(
        all(isinstance(item, str) and item and item.strip() == item for item in value),
        f"{label}: non-empty normalized strings required",
    )
    require(len(value) == len(set(value)), f"{label}: duplicates forbidden")
    return frozenset(value)


def parse_codeowners(path: Path) -> list[tuple[str, frozenset[str], int]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise DomainContractError(f"{path}: {error}") from error
    rules: list[tuple[str, frozenset[str], int]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        require(
            len(parts) >= 3,
            f"CODEOWNERS line {number}: at least two owners required",
        )
        pattern = parts[0]
        require(
            not pattern.startswith("!") and "[" not in pattern and "]" not in pattern,
            f"CODEOWNERS line {number}: unsupported pattern syntax",
        )
        owners = [item[1:].casefold() for item in parts[1:] if item.startswith("@")]
        require(
            len(owners) == len(parts) - 1 and all(owners),
            f"CODEOWNERS line {number}: invalid owner",
        )
        require(
            len(owners) == len(set(owners)),
            f"CODEOWNERS line {number}: duplicate owner alias",
        )
        rules.append((pattern, frozenset(owners), number))
    require(rules, "CODEOWNERS has no effective rules")
    return rules


def pattern_regex(pattern: str) -> re.Pattern[str]:
    if pattern == "*":
        return re.compile(r"^.*$")
    rooted = pattern.startswith("/")
    raw = pattern[1:] if rooted else pattern
    if raw.endswith("/"):
        raw += "**"
    pieces: list[str] = []
    offset = 0
    while offset < len(raw):
        token = raw[offset]
        if token == "*":
            if offset + 1 < len(raw) and raw[offset + 1] == "*":
                pieces.append(".*")
                offset += 2
            else:
                pieces.append("[^/]*")
                offset += 1
        elif token == "?":
            pieces.append("[^/]")
            offset += 1
        else:
            pieces.append(re.escape(token))
            offset += 1
    body = "".join(pieces)
    if not rooted and "/" not in raw:
        return re.compile(r"^(?:.*/)?" + body + r"$")
    return re.compile("^" + body + "$")


def matches(pattern: str, repository_path: str) -> bool:
    return pattern_regex(pattern).fullmatch(repository_path) is not None


def witnesses(protected_path: str) -> tuple[str, ...]:
    if protected_path.endswith("/**"):
        prefix = protected_path[:-2]
        return (
            prefix + "__domain_contract__",
            prefix + "nested/__domain_contract__",
        )
    return (protected_path,)


def effective_rule(
    rules: list[tuple[str, frozenset[str], int]],
    repository_path: str,
) -> tuple[str, frozenset[str], int]:
    matched = [rule for rule in rules if matches(rule[0], repository_path)]
    require(matched, f"CODEOWNERS has no effective rule for {repository_path}")
    return matched[-1]


def effective_domain_owners(
    codeowners_path: Path = CODEOWNERS_PATH,
) -> dict[str, dict[str, frozenset[str]]]:
    require(
        set(EXPECTED_DOMAIN_CODEOWNER_PATTERNS) == set(EXPECTED_DOMAINS),
        "domain-to-CODEOWNERS domain set drift",
    )
    rules = parse_codeowners(codeowners_path)
    result: dict[str, dict[str, frozenset[str]]] = {}
    for domain_id, expected in EXPECTED_DOMAINS.items():
        mappings = EXPECTED_DOMAIN_CODEOWNER_PATTERNS[domain_id]
        require(
            set(mappings) == set(expected["protected_paths"]),
            f"{domain_id}: domain-to-CODEOWNERS path set drift",
        )
        bound: dict[str, frozenset[str]] = {}
        for protected_path, expected_pattern in mappings.items():
            observed_owners: frozenset[str] | None = None
            for witness in witnesses(protected_path):
                pattern, owners, line = effective_rule(rules, witness)
                require(
                    pattern == expected_pattern,
                    f"{domain_id}: effective CODEOWNERS pattern drift for "
                    f"{protected_path} at witness {witness}; "
                    f"expected={expected_pattern!r}, observed={pattern!r}, line={line}",
                )
                require(
                    REQUIRED_OWNER_LOGINS <= owners,
                    f"{domain_id}: CODEOWNERS pattern {pattern} lacks "
                    "conflict-surviving review routes; "
                    f"missing={sorted(REQUIRED_OWNER_LOGINS - owners)}",
                )
                if observed_owners is None:
                    observed_owners = owners
                else:
                    require(
                        observed_owners == owners,
                        f"{domain_id}: CODEOWNERS owners vary inside {protected_path}",
                    )
            assert observed_owners is not None
            bound[protected_path] = observed_owners
        result[domain_id] = bound
    return result


def validate(
    path: Path = MATRIX_PATH,
    codeowners_path: Path = CODEOWNERS_PATH,
) -> dict[str, int | bool]:
    matrix = load(path)
    require(
        matrix.get("schema") == "trillionnium.independent-review-matrix.v2",
        "review matrix schema mismatch",
    )
    raw = matrix.get("domains")
    require(isinstance(raw, list) and raw, "review domains required")
    domains: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw):
        require(isinstance(row, dict), f"domains[{index}]: object required")
        domain_id = row.get("id")
        require(
            isinstance(domain_id, str) and domain_id and domain_id not in domains,
            f"domains[{index}]: invalid or duplicate id",
        )
        domains[domain_id] = row

    missing = sorted(set(EXPECTED_DOMAINS) - set(domains))
    extra = sorted(set(domains) - set(EXPECTED_DOMAINS))
    require(
        not missing and not extra,
        f"review domain set mismatch: missing={missing}, extra={extra}",
    )

    role_count = 0
    path_count = 0
    gap_count = 0
    for domain_id, expected in EXPECTED_DOMAINS.items():
        observed = domains[domain_id]
        for field in ("required_roles", "protected_paths", "blocking_gaps"):
            actual = string_set(observed.get(field), f"{domain_id}.{field}")
            require(
                actual == expected[field],
                f"{domain_id}: {field} drift; "
                f"missing={sorted(expected[field] - actual)}, "
                f"extra={sorted(actual - expected[field])}",
            )
        role_count += len(expected["required_roles"])
        path_count += len(expected["protected_paths"])
        gap_count += len(expected["blocking_gaps"])

    summary = matrix.get("summary")
    require(isinstance(summary, dict), "review matrix summary required")
    require(
        summary.get("domain_count") == len(EXPECTED_DOMAINS),
        "summary domain count drift",
    )
    require(
        summary.get("required_role_count") == role_count,
        "summary required-role count drift",
    )
    bindings = effective_domain_owners(codeowners_path)
    return {
        "domain_contract_valid": True,
        "codeowners_contract_valid": True,
        "domain_count": len(EXPECTED_DOMAINS),
        "required_role_count": role_count,
        "protected_path_assignments": path_count,
        "blocking_gap_assignments": gap_count,
        "effective_codeowner_bindings": sum(len(item) for item in bindings.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--codeowners", type=Path, default=CODEOWNERS_PATH)
    args = parser.parse_args(argv)
    try:
        result = validate(args.matrix, args.codeowners)
    except DomainContractError as error:
        print(f"independent review domain contract invalid: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
