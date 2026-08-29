#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MEMBERS = {
    "crates/trnm-contracts",
    "crates/trnm-authority-core",
    "crates/trnm-session-core",
    "crates/trnm-storage-core",
}
FORBIDDEN_PURE_CORE = (
    "unsafe {",
    "std::net",
    "std::time",
    "SystemTime",
    "tokio",
    "reqwest",
    "sqlx",
    "postgres",
    "rand::",
    "ring::",
    "ed25519",
)
REQUIRED_TESTS = {
    "first_command_commits_and_advances_global_and_participant_sequences",
    "exact_duplicate_replays_without_advancing_state",
    "same_command_id_with_different_fingerprint_is_terminal_conflict",
    "takeover_fences_old_commands_and_pending_commits",
    "replay_of_consumed_refresh_token_revokes_entire_family",
    "unknown_refresh_token_does_not_rotate_or_revoke_family",
}


def fail(message: str) -> None:
    raise SystemExit(f"rust foundation contract failed: {message}")


def main() -> int:
    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    members = set(workspace.get("workspace", {}).get("members", []))
    if members != EXPECTED_MEMBERS:
        fail(f"workspace members mismatch: {sorted(members)}")
    package = workspace["workspace"]["package"]
    if package.get("rust-version") != "1.85.1" or package.get("edition") != "2021":
        fail("Rust version/edition lock mismatch")

    toolchain = tomllib.loads((ROOT / "rust-toolchain.toml").read_text(encoding="utf-8"))
    if toolchain.get("toolchain", {}).get("channel") != "1.85.1":
        fail("rust-toolchain.toml is not exact")

    rust_sources = sorted((ROOT / "crates").glob("*/src/*.rs"))
    if len(rust_sources) != 4:
        fail(f"expected 4 Rust source files, found {len(rust_sources)}")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in rust_sources)
    if combined.count("#![forbid(unsafe_code)]") != 4:
        fail("every crate must forbid unsafe code")
    for marker in FORBIDDEN_PURE_CORE:
        if marker in combined:
            fail(f"forbidden pure-core dependency/capability: {marker}")
    test_names = set(re.findall(r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{", combined))
    missing = sorted(REQUIRED_TESTS - test_names)
    if missing:
        fail(f"required Rust tests missing: {missing}")
    if combined.count("#[test]") < 24:
        fail("expected at least 24 Rust unit tests")

    for vector_name in ("authority-vectors.json", "session-vectors.json"):
        document = json.loads((ROOT / "contracts/foundation" / vector_name).read_text())
        if any(document.get("claims", {}).values()):
            fail(f"positive claim in {vector_name}")
        if not document.get("cases"):
            fail(f"empty vector corpus {vector_name}")

    status = json.loads((ROOT / "docs/status/RUST_FOUNDATION_STATUS.json").read_text())
    if any(status["claims"].values()):
        fail("Rust foundation status contains a positive gate or compatibility claim")
    if status.get("stage") != "foundation-alpha-candidate":
        fail("unexpected Rust foundation stage")

    print(
        json.dumps(
            {
                "status": "rust-foundation-static-contract-passed",
                "workspace_members": len(members),
                "rust_unit_tests": combined.count("#[test]"),
                "vector_cases": sum(
                    len(json.loads((ROOT / "contracts/foundation" / name).read_text())["cases"])
                    for name in ("authority-vectors.json", "session-vectors.json")
                ),
                "cargo_executed_locally": False,
                "sg4_complete": False,
                "compatibility_credit": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
