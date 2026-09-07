#!/usr/bin/env python3
"""Validate the isolated trnm-server composition source without granting product credit."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "crates/trnm-server"

EXPECTED_RUNTIME_FILES = {
    "app.rs",
    "auth.rs",
    "codec.rs",
    "config.rs",
    "error.rs",
    "grpc.rs",
    "http.rs",
    "json.rs",
    "mod.rs",
    "pool.rs",
    "retry.rs",
    "retry_atomicity.rs",
    "retry_live_tests.rs",
    "schema.rs",
    "server.rs",
    "session_api.rs",
    "websocket.rs",
}
EXPECTED_DEPENDENCIES: dict[str, object] = {
    "postgres": "=0.19.14",
    "prost": "=0.14.3",
    "tokio": {"version": "=1.53.1", "features": ["rt", "time"]},
    "tonic": {"version": "=0.14.5", "features": ["transport"]},
    "tonic-prost": "=0.14.5",
    "trnm-contracts": {"path": "../trnm-contracts"},
    "trnm-persistence-pg": {"path": "../trnm-persistence-pg"},
    "trnm-realtime-wire": {"path": "../trnm-realtime-wire"},
    "trnm-session-core": {"path": "../trnm-session-core"},
    "trnm-token-jwt-adapter": {"path": "../trnm-token-jwt-adapter"},
}
EXPECTED_BUILD_DEPENDENCIES: dict[str, object] = {
    "prost-build": "=0.14.3",
    "prost-types": "=0.14.3",
    "protoc-bin-vendored": "=3.2.0",
    "tonic-build": "=0.14.5",
    "tonic-prost-build": "=0.14.5",
}
EXPECTED_FALSE_CLAIMS = {
    "live_database_bound",
    "wire_compatible",
    "behavior_compatible",
    "sg4_complete",
    "production_ready",
    "public_online",
    "nakama_replaced",
}


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def read(path: Path) -> str:
    require(path.is_file(), f"missing required source: {path.relative_to(ROOT)}")
    value = path.read_text(encoding="utf-8")
    require(value.endswith("\n"), f"file lacks trailing newline: {path.relative_to(ROOT)}")
    require("\r" not in value, f"CRLF is forbidden: {path.relative_to(ROOT)}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(read(path))
    require(isinstance(value, dict), f"object required: {path.relative_to(ROOT)}")
    return value


def main() -> int:
    try:
        manifest_path = CRATE / "Cargo.toml"
        lock_path = CRATE / "Cargo.lock"
        lib_path = CRATE / "src/lib.rs"
        main_path = CRATE / "src/main.rs"
        runtime_path = CRATE / "src/runtime"
        readme_path = CRATE / "README.md"
        contract_path = ROOT / "contracts/server/vertical-slice-v1.json"

        manifest = tomllib.loads(read(manifest_path))
        package = manifest.get("package")
        require(isinstance(package, dict), "server package table missing")
        require(package.get("name") == "trnm-server", "wrong server package name")
        require(package.get("publish") is False, "server package must not publish")
        require(package.get("rust-version") == "1.85.1", "server Rust version drift")
        require(package.get("build") == "build.rs", "server build script binding drift")
        require(manifest.get("workspace") == {}, "server must remain an isolated workspace")
        require(manifest.get("dependencies") == EXPECTED_DEPENDENCIES, "server dependency contract drift")
        require(
            manifest.get("build-dependencies") == EXPECTED_BUILD_DEPENDENCIES,
            "server build-dependency contract drift",
        )
        binaries = manifest.get("bin")
        require(isinstance(binaries, list) and len(binaries) == 1, "exactly one binary required")
        require(
            binaries[0] == {"name": "trnm-server-composition-candidate", "path": "src/main.rs"},
            "canonical package-local binary binding drift",
        )

        lock = read(lock_path)
        for package_name in (
            "trnm-server",
            "trnm-contracts",
            "trnm-persistence-pg",
            "trnm-realtime-wire",
            "trnm-session-core",
            "trnm-token-jwt-adapter",
        ):
            require(f'name = "{package_name}"' in lock, f"lock omits {package_name}")
        require("git+" not in lock, "isolated lock introduced a Git dependency")

        runtime_files = {path.name for path in runtime_path.glob("*.rs") if path.is_file()}
        require(runtime_files == EXPECTED_RUNTIME_FILES, "runtime module file set drift")
        source_paths = [lib_path, main_path, CRATE / "build.rs"] + sorted(runtime_path.glob("*.rs"))
        source = "\n".join(read(path) for path in source_paths)
        markers = {
            "unsafe prohibition": "#![forbid(unsafe_code)]",
            "composition entry": "runtime::run_from_environment()",
            "typed CLI": "ServerConfig::from_environment(arguments)",
            "migration command": "Command::Migrate",
            "PostgreSQL composition": "PgRepository",
            "bounded queue": "sync_channel(queue_capacity)",
            "bounded reads": "set_read_timeout(Some(config.read_timeout))",
            "bounded writes": "set_write_timeout(Some(config.write_timeout))",
            "health route": '"/healthz"',
            "readiness route": '"/readyz"',
            "metrics route": '"/metrics"',
            "drain route": '"/-/drain"',
            "authority bootstrap": '"/v1/authority/bootstrap"',
            "authority commit": '"/v1/authority/commit"',
            "session me": '"/v1/session/me"',
            "session refresh": '"/v1/session/refresh"',
            "session logout": '"/v1/session/logout"',
            "durable replay": "CommitOutcome::Duplicate",
            "acknowledgement fence": "acknowledgement-after-commit fence",
            "retry wrapper": "RetryingRepository",
            "inflight cancellation": "cancel_inflight()",
            "gRPC transport": "tonic::transport::Server",
            "WebSocket route": "websocket::serve_once",
            "access verifier": "AccessTokenVerifier",
            "generated protobuf": 'tonic::include_proto!("nakama.api")',
        }
        for label, marker in markers.items():
            require(marker in source, f"server source missing {label}: {marker}")
        require(
            "unsafe {" not in source.replace("#![forbid(unsafe_code)]", ""),
            "unsafe block entered server source",
        )
        for forbidden in (
            "compatibility_credit=true",
            "production_ready=true",
            "public_online=true",
            "nakama_replaced=true",
        ):
            require(forbidden not in source, f"premature source claim: {forbidden}")

        contract = load_json(contract_path)
        require(contract.get("schema") == "trillionnium.server-vertical-slice.v1", "wrong contract schema")
        require(contract.get("status") == "source-candidate", "contract status must fail closed")
        require(contract.get("crate") == "crates/trnm-server", "contract crate drift")
        require(contract.get("binary") == "trnm-server-composition-candidate", "contract binary drift")
        claims = contract.get("claims")
        require(isinstance(claims, dict), "contract claims object required")
        require(claims.get("rust_binary_exists") is True, "binary source-presence claim missing")
        require(claims.get("source_candidate") is True, "source-candidate claim missing")
        for name in EXPECTED_FALSE_CLAIMS:
            require(claims.get(name) is False, f"premature contract claim: {name}")

        readme = read(readme_path)
        require(readme.startswith("# trnm-server\n"), "canonical README title required")
        require("Status: **module documentation; source candidate;" in readme, "README status marker missing")
        for heading in (
            "## Status and authority",
            "## Responsibilities",
            "## Architecture and dependencies",
            "## Public contracts",
            "## Operations",
            "## Known gaps and exit criteria",
        ):
            require(readme.count(heading) == 1, f"README section drift: {heading}")
        for marker in (
            "crates/trnm-persistence-pg/src/bin/trnm-server.rs",
            "compatibility_credit=false",
            "database_durability_credit=false",
            "sg4_credit=false",
            "production_ready=false",
            "accepted_evidence=false",
        ):
            require(marker in readme, f"README boundary missing: {marker}")

        print(json.dumps({
            "schema": "trillionnium.server-source-check.v3",
            "status": "passed",
            "package": package.get("name"),
            "binary": binaries[0]["name"],
            "runtime_module_count": len(runtime_files),
            "source_marker_count": len(markers),
            "claims": {
                "compiled": False,
                "live_process_executed": False,
                "live_database_bound": False,
                "wire_compatible": False,
                "production_ready": False,
            },
        }, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError, ValidationError) as error:
        print(f"trnm-server source check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
