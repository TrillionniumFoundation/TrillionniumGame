#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MEMBERS = {
    "crates/trnm-contracts",
    "crates/trnm-authority-core",
    "crates/trnm-session-core",
    "crates/trnm-storage-core",
    "crates/trnm-canonical-core",
    "crates/trnm-transport-core",
    "crates/trnm-token-core",
    "crates/trnm-presence-core",
    "crates/trnm-query-core",
    "crates/trnm-persistence-core",
    "crates/trnm-persistence-pg",
}
PURE_CORE_MEMBERS = EXPECTED_MEMBERS - {"crates/trnm-persistence-pg"}
EXPECTED_DEPENDENCIES: dict[str, dict[str, Any]] = {
    "crates/trnm-contracts": {},
    "crates/trnm-authority-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-session-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-storage-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-canonical-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-transport-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-token-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-presence-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-query-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-persistence-core": {
        "trnm-contracts": {"path": "../trnm-contracts"},
    },
    "crates/trnm-persistence-pg": {
        "native-tls": "=0.2.18",
        "openssl": "=0.10.81",
        "postgres": "=0.19.14",
        "postgres-native-tls": "=0.5.3",
        "prost": "=0.14.3",
        "r2d2": "=0.8.10",
        "r2d2_postgres": "=0.18.2",
        "tokio": {"version": "=1.53.1", "features": ["rt", "time"]},
        "tonic": {"version": "=0.14.5", "features": ["transport"]},
        "tonic-prost": "=0.14.5",
        "trnm-contracts": {"path": "../trnm-contracts"},
        "trnm-realtime-wire": {"path": "../trnm-realtime-wire"},
        "trnm-session-core": {"path": "../trnm-session-core"},
        "trnm-token-jwt-adapter": {"path": "../trnm-token-jwt-adapter"},
    },
}
FORBIDDEN_PURE_CORE_PATTERNS = (
    r"\bunsafe\s*\{",
    r"\bstd::net\b",
    r"\bstd::time\b",
    r"\bSystemTime\b",
    r"\btokio(?:::|\b)",
    r"\breqwest(?:::|\b)",
    r"\bsqlx(?:::|\b)",
    r"\bpostgres(?:::|\b)",
    r"\brand::",
    r"\bring::",
    r"\bed25519\b",
)
REQUIRED_TESTS = {
    "first_command_commits_and_advances_global_and_participant_sequences",
    "exact_duplicate_replays_without_advancing_state",
    "same_command_id_with_different_fingerprint_is_terminal_conflict",
    "takeover_fences_old_commands_and_pending_commits",
    "replay_of_consumed_refresh_token_revokes_entire_family",
    "unknown_refresh_token_does_not_rotate_or_revoke_family",
    "multi_operation_batch_rolls_back_on_any_failure",
    "stale_version_rejects_without_mutation",
    "duplicate_object_keys_fail_closed",
    "depth_node_collection_and_output_limits_fail_closed",
    "mismatched_context_fails_closed",
    "malformed_envelopes_use_protocol_close",
    "family_claims_and_epoch_must_match",
    "retired_and_out_of_window_keys_are_rejected",
    "rebind_increments_generation_and_fences_old_node",
    "outbound_budget_failure_has_zero_partial_mutation",
    "single_slash_is_rejected_instead_of_panicking",
    "query_token_and_clause_limits_are_enforced",
    "commit_is_atomic_and_records_events_and_outbox",
    "outbox_lease_generation_fences_stale_worker",
    "pgwire_commit_duplicate_conflict_and_fence_contract",
}


def fail(message: str) -> None:
    raise SystemExit(f"rust foundation contract failed: {message}")


def load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        fail(f"{path.relative_to(ROOT)}: invalid TOML: {exc}")


def validate_member(member: str) -> tuple[list[Path], list[Path]]:
    root = ROOT / member
    manifest = load_toml(root / "Cargo.toml")
    package = manifest.get("package", {})
    expected_name = Path(member).name
    if package.get("name") != expected_name:
        fail(f"{member}: package name mismatch")
    inherited = {
        "version": package.get("version", {}).get("workspace"),
        "edition": package.get("edition", {}).get("workspace"),
        "rust-version": package.get("rust-version", {}).get("workspace"),
        "license": package.get("license", {}).get("workspace"),
        "publish": package.get("publish", {}).get("workspace"),
    }
    if inherited != {key: True for key in inherited}:
        fail(f"{member}: package metadata must inherit the workspace contract")
    if manifest.get("lints", {}).get("workspace") is not True:
        fail(f"{member}: workspace lints are not enabled")
    dependencies = manifest.get("dependencies", {})
    if dependencies != EXPECTED_DEPENDENCIES[member]:
        fail(
            f"{member}: dependency allowlist mismatch: "
            f"expected {EXPECTED_DEPENDENCIES[member]!r}, got {dependencies!r}"
        )
    source_files = sorted((root / "src").rglob("*.rs"))
    if not source_files or not (root / "src/lib.rs").is_file():
        fail(f"{member}: missing Rust library source")
    lib_text = (root / "src/lib.rs").read_text(encoding="utf-8")
    if "#![forbid(unsafe_code)]" not in lib_text:
        fail(f"{member}: crate root does not forbid unsafe code")
    test_files = sorted((root / "tests").rglob("*.rs")) if (root / "tests").is_dir() else []
    return source_files, test_files


def main() -> int:
    workspace = load_toml(ROOT / "Cargo.toml")
    members = set(workspace.get("workspace", {}).get("members", []))
    if members != EXPECTED_MEMBERS:
        fail(f"workspace members mismatch: {sorted(members)}")
    package = workspace["workspace"]["package"]
    if package.get("rust-version") != "1.85.1" or package.get("edition") != "2021":
        fail("Rust version/edition lock mismatch")
    if package.get("publish") is not False:
        fail("workspace packages must remain non-publishable")

    toolchain = load_toml(ROOT / "rust-toolchain.toml")
    if toolchain.get("toolchain", {}).get("channel") != "1.85.1":
        fail("rust-toolchain.toml is not exact")

    all_sources: list[Path] = []
    all_tests: list[Path] = []
    for member in sorted(EXPECTED_MEMBERS):
        sources, tests = validate_member(member)
        all_sources.extend(sources)
        all_tests.extend(tests)

    pure_core = "\n".join(
        path.read_text(encoding="utf-8")
        for member in sorted(PURE_CORE_MEMBERS)
        for path in sorted((ROOT / member / "src").rglob("*.rs"))
    )
    for pattern in FORBIDDEN_PURE_CORE_PATTERNS:
        if re.search(pattern, pure_core):
            fail(f"forbidden pure-core dependency/capability pattern: {pattern}")

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in [*all_sources, *all_tests]
    )
    if re.search(r"\b(?:todo|unimplemented)!\s*\(", combined):
        fail("unfinished Rust macro remains in the consolidated workspace")
    test_names = set(re.findall(r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{", combined))
    missing = sorted(REQUIRED_TESTS - test_names)
    if missing:
        fail(f"required Rust tests missing: {missing}")
    rust_unit_tests = combined.count("#[test]")
    if rust_unit_tests < 85:
        fail(f"consolidated Rust unit/integration test count regressed to {rust_unit_tests}")

    lock = load_toml(ROOT / "Cargo.lock")
    locked_names = {row.get("name") for row in lock.get("package", [])}
    expected_names = {Path(member).name for member in EXPECTED_MEMBERS}
    missing_locked = sorted(expected_names - locked_names)
    if missing_locked:
        fail(f"workspace packages missing from Cargo.lock: {missing_locked}")

    for vector_name in ("authority-vectors.json", "session-vectors.json"):
        document = json.loads(
            (ROOT / "contracts/foundation" / vector_name).read_text(encoding="utf-8")
        )
        if any(document.get("claims", {}).values()):
            fail(f"positive claim in {vector_name}")
        if not document.get("cases"):
            fail(f"empty vector corpus {vector_name}")

    status = json.loads(
        (ROOT / "docs/status/RUST_FOUNDATION_STATUS.json").read_text(encoding="utf-8")
    )
    if any(status["claims"].values()):
        fail("Rust foundation status contains a positive gate or compatibility claim")
    if status.get("stage") != "foundation-alpha-candidate":
        fail("unexpected Rust foundation stage")

    print(
        json.dumps(
            {
                "status": "rust-foundation-workspace-contract-passed",
                "workspace_members": len(members),
                "pure_core_members": len(PURE_CORE_MEMBERS),
                "adapter_members": len(EXPECTED_MEMBERS - PURE_CORE_MEMBERS),
                "rust_source_files": len(all_sources),
                "rust_test_files": len(all_tests),
                "rust_unit_tests": rust_unit_tests,
                "vector_cases": sum(
                    len(
                        json.loads(
                            (ROOT / "contracts/foundation" / name).read_text(
                                encoding="utf-8"
                            )
                        )["cases"]
                    )
                    for name in ("authority-vectors.json", "session-vectors.json")
                ),
                "cargo_executed_by_companion_gate": True,
                "sg4_complete": False,
                "compatibility_credit": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
