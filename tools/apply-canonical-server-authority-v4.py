#!/usr/bin/env python3
"""Atomically promote crates/trnm-server and retire the persistence-owned process root.

This transformer is intentionally fail closed: it operates only on the reviewed
Plan v3.1 tree shape, removes the old duplicate binary in the same change, keeps
all compatibility and production claims false, and rewrites the source checkers
to validate the resulting single composition authority.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
OLD_BINARY = "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
OLD_MODULE = "crates/trnm-persistence-pg/src/bin/trnm_server"
OLD_AUTHORITY = "crates/trnm-persistence-pg/Cargo.toml::trnm-server"
NEW_BINARY = "crates/trnm-server/src/main.rs"
NEW_MODULE = "crates/trnm-server/src/runtime"
NEW_MANIFEST = "crates/trnm-server/Cargo.toml"
NEW_AUTHORITY = "crates/trnm-server/Cargo.toml::trnm-server"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def rewrite(value: Any) -> Any:
    if isinstance(value, str):
        return (
            value.replace(OLD_MODULE, NEW_MODULE)
            .replace(OLD_BINARY, NEW_BINARY)
            .replace(OLD_AUTHORITY, NEW_AUTHORITY)
            .replace("trnm-server-foundation", "trnm-server")
        )
    if isinstance(value, list):
        return [rewrite(item) for item in value]
    if isinstance(value, dict):
        return {key: rewrite(item) for key, item in value.items()}
    return value


def load_json(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{relative}: root must be an object")
    return rewrite(value)


def save_json(relative: str, value: dict[str, Any]) -> None:
    write(ROOT / relative, json.dumps(value, ensure_ascii=False, indent=2))


def remove_old_server() -> None:
    old_binary = ROOT / OLD_BINARY
    old_module = ROOT / OLD_MODULE
    require(old_binary.is_file(), f"reviewed old server binary missing: {OLD_BINARY}")
    require(old_module.is_dir(), f"reviewed old server module missing: {OLD_MODULE}")
    old_binary.unlink()
    shutil.rmtree(old_module)

    manifest_path = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
    text = manifest_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    removed = 0
    while index < len(lines):
        if lines[index].strip() != "[[bin]]":
            output.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            end += 1
        block = "".join(lines[index:end])
        if re.search(r'^name\s*=\s*"trnm-server"\s*$', block, re.M):
            removed += 1
        else:
            output.extend(lines[index:end])
        index = end
    require(removed == 1, f"expected one explicit old trnm-server target, removed={removed}")
    write(manifest_path, "".join(output))


def rewrite_text_references() -> None:
    replacements = (
        (OLD_MODULE, NEW_MODULE),
        (OLD_BINARY, NEW_BINARY),
        (OLD_AUTHORITY, NEW_AUTHORITY),
        ("trnm-server-foundation", "trnm-server"),
    )
    suffixes = {".md", ".json", ".py", ".sh", ".yml", ".yaml", ".toml", ".txt"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in {".git", "target"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != text:
            write(path, updated)


def update_machine_authority() -> None:
    authority = load_json("docs/development/RUST_PACKAGE_AUTHORITY.json")
    server = authority.setdefault("server_binary_authority", {})
    server.update(
        {
            "name": "trnm-server",
            "manifest": NEW_MANIFEST,
            "source": NEW_BINARY,
            "status": "canonical-composition-root-source-candidate",
            "reason": (
                "The database-backed process composition now lives in the dedicated "
                "trnm-server package. Persistence remains a dependency and no longer "
                "owns a process binary. Source presence grants no compatibility or "
                "production credit."
            ),
        }
    )
    server["replacement_contract"] = [
        "the replacement package contains the complete database-backed process composition",
        "the old persistence-owned binary target and module tree are absent in the same change",
        "exactly one first-party target is named trnm-server",
        "package authority, status, checkers, tests and documentation point to the same source",
        "exact-head format, tests, strict Clippy and control-plane checks pass before admission",
    ]
    for row in authority.get("isolated_workspaces", []):
        if isinstance(row, dict) and row.get("manifest") == NEW_MANIFEST:
            row["classification"] = "canonical-server-composition-root-source-candidate"
            row["aggregate_gate_required"] = True
    historical = authority.get("foundation_prototype")
    if isinstance(historical, dict):
        historical.update(
            {
                "name": "trnm-server",
                "manifest": NEW_MANIFEST,
                "source": NEW_BINARY,
                "status": "promoted-to-canonical-composition-root-source-candidate",
                "relationship": (
                    "Historical schema key retained for compatibility; the former prototype "
                    "has been replaced by the database-backed canonical composition root."
                ),
                "compatibility_credit": False,
                "production_credit": False,
            }
        )
    save_json("docs/development/RUST_PACKAGE_AUTHORITY.json", authority)

    current = load_json("docs/status/CURRENT_STATE.json")
    topology = current.setdefault("runtime_topology", {})
    topology["rust_server_binary_present"] = True
    topology["rust_server_binary_authority"] = NEW_AUTHORITY
    topology["rust_server_stage"] = "canonical-http-websocket-session-database-source-candidate"
    topology["rust_server_remote_verified"] = False
    save_json("docs/status/CURRENT_STATE.json", current)

    rust_status = load_json("docs/status/RUST_SERVER_STATUS.json")
    rust_status["component"] = "crates/trnm-server"
    rust_status["workspace_mode"] = "standalone-mandatory-merge-gate"
    rust_status["binary_target"] = "trnm-server"
    rust_status["authority_role"] = "canonical-composition-root-source-candidate"
    rust_status["canonical_server_binary"] = {
        "target": "trnm-server",
        "manifest": NEW_MANIFEST,
        "source": NEW_BINARY,
        "status": "canonical-composition-root-source-candidate",
    }
    implemented = rust_status.setdefault("implemented", [])
    for item in (
        "canonical dedicated trnm-server package authority",
        "database-backed HTTP, WebSocket, session and health composition root",
        "PostgreSQL and CockroachDB repository binding through trnm-persistence-pg",
    ):
        if item not in implemented:
            implemented.append(item)
    rust_status["not_implemented"] = [
        item
        for item in rust_status.get("not_implemented", [])
        if item
        not in {
            "canonical trnm-server package authority",
            "database pool and TLS provider",
            "session verification adapter",
            "HTTP JSON Nakama leaf",
            "WebSocket JSON and protobuf leaf",
            "transactional database receipt event and outbox commit",
            "restart recovery",
            "authenticated operator drain API",
        }
    ]
    claims = rust_status.setdefault("claims", {})
    claims["source_exists"] = True
    claims["canonical_binary_authority"] = True
    for field in (
        "exact_head_compiled",
        "remote_verified",
        "http_compatible",
        "grpc_compatible",
        "realtime_compatible",
        "database_durable",
        "sg4_complete",
        "c1_earned",
        "c2_earned",
        "production_ready",
        "public_online",
        "nakama_replaced",
    ):
        claims[field] = False
    save_json("docs/status/RUST_SERVER_STATUS.json", rust_status)

    server_status = load_json("docs/status/TRNM_SERVER_STATUS.json")
    server_status["stage"] = "http-websocket-session-database-vertical-source-candidate"
    server_status["binary_target"] = "trnm-server"
    server_status["temporary_package"] = "crates/trnm-server"
    paths = server_status.setdefault("source_paths", [])
    required_paths = [
        NEW_BINARY,
        NEW_MODULE,
        "crates/trnm-persistence-pg/src/pool.rs",
        "crates/trnm-persistence-pg/src/pool_parts",
        "crates/trnm-persistence-pg/src/session.rs",
        "crates/trnm-persistence-pg/src/auth.rs",
        "crates/trnm-realtime-wire",
        "contracts/realtime/trnm-authority-envelope-v1.proto",
    ]
    server_status["source_paths"] = list(dict.fromkeys(required_paths + paths))
    server_status["source_paths"] = [
        path for path in server_status["source_paths"] if (ROOT / path).exists()
    ]
    status_claims = server_status.setdefault("claims", {})
    status_claims["source_candidate"] = True
    for field in (
        "remote_verified",
        "live_database_verified",
        "http_wire_compatible",
        "websocket_wire_compatible",
        "grpc_implemented",
        "websocket_protobuf_implemented",
        "session_integrated",
        "request_cancellation_implemented",
        "certificate_rotation_verified",
        "outbox_delivery_verified",
        "sg4_complete",
        "production_ready",
        "public_online",
        "nakama_replaced",
    ):
        status_claims[field] = False
    save_json("docs/status/TRNM_SERVER_STATUS.json", server_status)

    inventory = load_json("docs/status/IMPLEMENTATION_INVENTORY.json")
    for component in inventory.get("components", []):
        if isinstance(component, dict) and component.get("id") == "COMP-TRNM-SERVER":
            component["path"] = NEW_BINARY
            component["kind"] = "canonical-first-party-rust-server-source-candidate"
            component["status"] = "http-websocket-source-candidate"
            component["claim_credit"] = False
            implemented = component.setdefault("implemented", [])
            if "dedicated canonical composition-root package" not in implemented:
                implemented.append("dedicated canonical composition-root package")
    save_json("docs/status/IMPLEMENTATION_INVENTORY.json", inventory)

    gap_register = load_json("docs/status/GAP_REGISTER.json")
    save_json("docs/status/GAP_REGISTER.json", gap_register)

    vertical = load_json("docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json")
    vertical["implementation"] = "crates/trnm-server"
    vertical["status"] = "source-candidate"
    vertical.setdefault("claims", {})["source_vertical_slice_exists"] = True
    for field in (
        "locally_verified",
        "remote_verified",
        "nakama_wire_compatible",
        "database_durable",
        "sg4_complete",
        "compatibility_credit",
        "production_ready",
        "public_online",
        "nakama_replaced",
    ):
        vertical["claims"][field] = False
    save_json("docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json", vertical)


def install_authority_checker() -> None:
    helper = r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "crates/trnm-server/Cargo.toml"
MAIN = ROOT / "crates/trnm-server/src/main.rs"
LIB = ROOT / "crates/trnm-server/src/lib.rs"
RUNTIME = ROOT / "crates/trnm-server/src/runtime"
OLD_MAIN = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
OLD_RUNTIME = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server"
AUTHORITY = ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json"
STATUS = ROOT / "docs/status/TRNM_SERVER_STATUS.json"


class AuthorityError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorityError(message)


def validate_authority() -> dict[str, Any]:
    for path in (MANIFEST, MAIN, LIB, RUNTIME, AUTHORITY, STATUS):
        require(path.exists(), f"missing canonical server surface: {path.relative_to(ROOT)}")
    require(not OLD_MAIN.exists(), "persistence-owned trnm-server binary was not retired")
    require(not OLD_RUNTIME.exists(), "persistence-owned server module tree was not retired")

    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    package = manifest.get("package", {})
    require(package.get("name") == "trnm-server", "canonical package name drift")
    require(package.get("publish") is False, "canonical source candidate must not publish")
    require(manifest.get("workspace") == {}, "canonical package must remain an isolated workspace")
    bins = manifest.get("bin", [])
    require(isinstance(bins, list) and len(bins) == 1, "canonical package must expose one binary")
    require(
        bins[0].get("name") == "trnm-server" and bins[0].get("path") == "src/main.rs",
        "canonical trnm-server target drift",
    )
    dependencies = manifest.get("dependencies", {})
    required_dependencies = {
        "postgres",
        "prost",
        "tokio",
        "tonic",
        "tonic-prost",
        "trnm-contracts",
        "trnm-persistence-pg",
        "trnm-realtime-wire",
        "trnm-session-core",
        "trnm-token-jwt-adapter",
    }
    require(required_dependencies <= set(dependencies), "canonical dependency boundary is incomplete")
    for name, value in dependencies.items():
        if isinstance(value, dict):
            require("git" not in value, f"movable git dependency entered canonical server: {name}")

    sources = [MAIN, LIB, *sorted(RUNTIME.rglob("*.rs"))]
    require(len(sources) >= 15, "canonical runtime source set is unexpectedly small")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    cleaned = combined.replace("#![forbid(unsafe_code)]", "")
    for forbidden in ("unsafe {", "todo!", "unimplemented!"):
        require(forbidden not in cleaned, f"forbidden server source marker: {forbidden}")
    markers = (
        "Command::CheckConfig",
        "Command::Migrate",
        "Command::Serve",
        "open_verified_repository",
        "127.0.0.1:7350",
        "TRNM_SERVER_ALLOW_NON_LOOPBACK",
        "TRNM_SERVER_DATABASE_URL",
        "TRNM_SERVER_ADMIN_TOKEN",
        "PgPool::connect_plain",
        "PgPool::connect_tls",
        "RetryingRepository::new",
        "CommitOutcome::Duplicate",
        '"/healthz"',
        '"/readyz"',
        '"/-/drain"',
        '"/v1/authority/bootstrap"',
        '"/v1/authority/commit"',
        '"/v1/session/me"',
        '"/v1/session/refresh"',
        '"/v1/session/logout"',
        '"/v1/realtime"',
        "trnm.json.v1",
        "trnm.protobuf.v1",
        "NakamaServer::new",
        "serve_with_shutdown",
        "read_client_frame_exact",
        "cancel_inflight",
        "RefreshRotationOutcome::ReplayRevoked",
    )
    missing = [marker for marker in markers if marker not in combined]
    require(not missing, "canonical server markers missing: " + ", ".join(missing))
    test_count = combined.count("#[test]")
    require(test_count >= 60, f"canonical server test source count too small: {test_count}")

    authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    server = authority.get("server_binary_authority", {})
    require(server.get("name") == "trnm-server", "authority binary name drift")
    require(server.get("manifest") == "crates/trnm-server/Cargo.toml", "authority manifest drift")
    require(server.get("source") == "crates/trnm-server/src/main.rs", "authority source drift")

    status = json.loads(STATUS.read_text(encoding="utf-8"))
    claims = status.get("claims", {})
    require(claims.get("source_candidate") is True, "server source-candidate claim missing")
    forbidden_positive = {
        "remote_verified",
        "live_database_verified",
        "http_wire_compatible",
        "websocket_wire_compatible",
        "grpc_implemented",
        "websocket_protobuf_implemented",
        "session_integrated",
        "outbox_delivery_verified",
        "sg4_complete",
        "production_ready",
        "public_online",
        "nakama_replaced",
    }
    require(not any(claims.get(field) for field in forbidden_positive), "server status overclaims")
    return {
        "schema": "trillionnium.canonical-server-authority-check.v1",
        "status": "passed",
        "manifest": "crates/trnm-server/Cargo.toml",
        "binary": "trnm-server",
        "source": "crates/trnm-server/src/main.rs",
        "source_file_count": len(sources),
        "source_marker_count": len(markers),
        "source_test_count": test_count,
        "claims_all_false": True,
        "compatibility_credit": False,
        "production_ready": False,
    }
'''
    write(ROOT / "scripts/trnm_server_authority.py", helper)

    checker = r'''#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
import tomllib
from pathlib import Path
from trnm_server_authority import AuthorityError, validate_authority

ROOT = Path(__file__).resolve().parents[1]


def expected_persistence_dependencies() -> dict[str, object]:
    manifest = tomllib.loads((ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8"))
    value = manifest.get("dependencies", {})
    if not isinstance(value, dict) or not value:
        raise AuthorityError("persistence dependency table is empty")
    return value


def validate_dependency_boundary(manifest: dict[str, object]) -> None:
    dependencies = manifest.get("dependencies", {})
    required = {"postgres", "r2d2", "r2d2_postgres", "trnm-contracts"}
    if not isinstance(dependencies, dict) or not required <= set(dependencies):
        raise AuthorityError("persistence dependency boundary is incomplete")


def main() -> int:
    try:
        result = validate_authority()
        result["status"] = "trnm-server-source-contract-passed"
    except (AuthorityError, OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"trnm-server contract failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    write(ROOT / "scripts/check-trnm-server.py", checker)

    slice_checker = r'''#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from trnm_server_authority import AuthorityError, validate_authority


def validate() -> dict[str, object]:
    result = validate_authority()
    return {
        "schema": "trillionnium.rust-server-slice-contract.v3",
        "source": result["source"],
        "canonical_server": result["source"],
        "source_tokens": 16,
        "claims_all_false": True,
        "status": "passed",
        "compatibility_credit": False,
    }


def main() -> int:
    try:
        result = validate()
    except (AuthorityError, OSError, ValueError) as error:
        print(f"Rust server slice contract failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    write(ROOT / "scripts/check-rust-server-slice.py", slice_checker)

    source_checker = r'''#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from trnm_server_authority import AuthorityError, validate_authority


def main() -> int:
    try:
        result = validate_authority()
    except (AuthorityError, OSError, ValueError) as error:
        print(f"trnm-server source check failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "schema": "trillionnium.server-source-check.v2",
        "status": "passed",
        "package": "trnm-server",
        "binary": "trnm-server",
        "source_marker_count": result["source_marker_count"],
        "documentation": "docs/DEVELOPMENT.md",
        "claims": {
            "source_contract_passed": True,
            "compiled": False,
            "live_process_executed": False,
            "live_database_bound": False,
            "wire_compatible": False,
            "production_ready": False,
        },
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    write(ROOT / "scripts/check-rust-server-source-candidate.py", source_checker)


def update_tests_and_process_probe() -> None:
    dependency_test = ROOT / "tests/control_plane/test_trnm_server_dependency_contract.py"
    if dependency_test.exists():
        write(
            dependency_test,
            r'''from __future__ import annotations
import importlib.util
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-trnm-server.py"


class CanonicalServerDependencyContractTests(unittest.TestCase):
    def test_checker_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_persistence_dependency_boundary_remains_complete(self) -> None:
        spec = importlib.util.spec_from_file_location("server_checker", CHECKER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = tomllib.loads(
            (ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8")
        )
        module.validate_dependency_boundary(manifest)


if __name__ == "__main__":
    unittest.main()
''',
        )

    process = ROOT / "scripts/check-rust-server-process.sh"
    if process.exists():
        write(
            process,
            r'''#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
export TRNM_SERVER_DATABASE_URL='postgresql://candidate:secret@127.0.0.1/trnm'
export TRNM_SERVER_DATABASE_PROFILE=postgresql
export TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE=1
export TRNM_SERVER_SCHEMA_SOURCE_COMMIT=7deee4377cff401854647151842c582f038feef1
export TRNM_SERVER_ADMIN_TOKEN='candidate-admin-token-with-sufficient-length'
log=$(mktemp)
trap 'rm -f "$log"' EXIT
cargo run --quiet --manifest-path crates/trnm-server/Cargo.toml --locked \
  --bin trnm-server -- check-config >"$log"
grep -q 'trnm-server configuration:' "$log"
grep -q '<redacted>' "$log"
! grep -Eq '(candidate-admin-token|candidate:secret|BEGIN PRIVATE KEY)' "$log"
echo graceful_shutdown_verified=false
echo database_durability_verified=false
echo compatibility_credit=false
''',
        )
        process.chmod(0o755)


def update_human_docs() -> None:
    additions = {
        "docs/development/RUST_PACKAGE_AUTHORITY.md": (
            "## Canonical server composition authority\n\n"
            "`crates/trnm-server/Cargo.toml::trnm-server` is the sole first-party process "
            "target. `trnm-persistence-pg` remains a library dependency and contains no "
            "server binary or server module tree. This is source-candidate authority only; "
            "compatibility and production credit remain false.\n"
        ),
        "docs/ARCHITECTURE.md": (
            "## Canonical server composition root\n\n"
            "The dedicated `crates/trnm-server` package owns process configuration, HTTP, "
            "WebSocket, gRPC health, session composition, database binding and shutdown. "
            "Persistence is consumed as a library. Exactly one binary target is named "
            "`trnm-server`.\n"
        ),
        "docs/DEVELOPMENT.md": (
            "## Canonical trnm-server validation\n\n"
            "Run `cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets "
            "--locked` and strict Clippy, then `python3 scripts/check-trnm-server.py`. "
            "The source candidate does not by itself grant wire compatibility, durability, "
            "production readiness, public-online or retirement credit.\n"
        ),
    }
    for relative, section in additions.items():
        path = ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        heading = section.splitlines()[0]
        if heading not in text:
            write(path, text.rstrip() + "\n\n" + section)

    readme = ROOT / "crates/trnm-server/README.md"
    text = readme.read_text(encoding="utf-8")
    markers = (
        "compatibility_credit=false",
        "database_durability_credit=false",
        "sg4_credit=false",
        "production_ready=false",
    )
    missing = [marker for marker in markers if marker not in text]
    if missing:
        text += "\n\n## Claim boundary\n\n```text\n" + "\n".join(missing) + "\n```\n"
        write(readme, text)


def verify_shape() -> None:
    require((ROOT / NEW_MANIFEST).is_file(), "new server manifest missing")
    require((ROOT / NEW_BINARY).is_file(), "new server binary missing")
    require((ROOT / NEW_MODULE).is_dir(), "new server runtime missing")
    require(not (ROOT / OLD_BINARY).exists(), "old server binary survived")
    require(not (ROOT / OLD_MODULE).exists(), "old server module survived")
    text = (ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8")
    require('name = "trnm-server"' not in text, "old manifest still declares trnm-server")


def main() -> int:
    remove_old_server()
    rewrite_text_references()
    update_machine_authority()
    install_authority_checker()
    update_tests_and_process_probe()
    update_human_docs()
    verify_shape()
    print("canonical server authority migration: applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
