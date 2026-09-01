#!/usr/bin/env python3
"""Validate the bounded standalone trnm-server source-candidate contract."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_IMPLEMENTED = {
    "typed serve/check-config/version/help command parsing",
    "bounded configurable HTTP/1.1 request parser",
    "duplicate content-length rejection",
    "transfer-encoding rejection",
    "health and readiness endpoints",
    "one bootstrap and one authority command reaching prepare/commit",
    "event and transactional-outbox intent creation in the pure core",
    "exact duplicate receipt replay",
    "stale revision rejection",
    "bounded synchronous worker queue and socket I/O timeouts",
}
EXPECTED_NOT_IMPLEMENTED = {
    "production configuration loader",
    "actual migration execution",
    "PostgreSQL or CockroachDB repository binding",
    "HTTP JSON compatibility adapter",
    "gRPC server",
    "WebSocket JSON/protobuf server",
    "production session verification",
    "outbox worker and external delivery",
    "signal-based graceful shutdown",
    "metrics and traces",
    "immutable Nakama differential",
    "load, HA, security and operations evidence",
}


class ValidationError(RuntimeError):
    """Raised when the source candidate and its declared contract diverge."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def require_string_list(value: Any, label: str) -> list[str]:
    require(isinstance(value, list) and value, f"{label} must be a non-empty list")
    result: list[str] = []
    for index, item in enumerate(value):
        require(isinstance(item, str), f"{label}[{index}] must be a string")
        require(item and item.strip() == item, f"{label}[{index}] must be canonical text")
        result.append(item)
    require(len(result) == len(set(result)), f"{label} contains duplicates")
    return result


def main() -> int:
    try:
        manifest_path = ROOT / "crates/trnm-server/Cargo.toml"
        lock_path = ROOT / "crates/trnm-server/Cargo.lock"
        lib_path = ROOT / "crates/trnm-server/src/lib.rs"
        main_path = ROOT / "crates/trnm-server/src/main.rs"
        contract_path = ROOT / "contracts/server/vertical-slice-v1.json"
        documentation_path = ROOT / "docs/development/RUST_SERVER_VERTICAL_SLICE_V1.md"
        process_smoke_path = ROOT / "scripts/check-rust-server-process.sh"
        for path in (
            manifest_path,
            lock_path,
            lib_path,
            main_path,
            contract_path,
            documentation_path,
            process_smoke_path,
        ):
            require(
                path.is_file(),
                f"missing required server source: {path.relative_to(ROOT)}",
            )

        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        package = manifest.get("package", {})
        require(package.get("name") == "trnm-server", "wrong server package name")
        require(package.get("publish") is False, "server source candidate must not publish")
        require(
            manifest.get("workspace") == {},
            "server must remain an explicit standalone workspace candidate",
        )
        dependencies = manifest.get("dependencies", {})
        require(
            set(dependencies) == {"trnm-contracts", "trnm-persistence-core"},
            "server source candidate dependency boundary changed",
        )
        for name, value in dependencies.items():
            require(
                isinstance(value, dict) and "path" in value,
                f"{name} must be a path dependency",
            )
            require(
                "version" not in value and "git" not in value,
                f"{name} introduced an external source",
            )
        binary_targets = manifest.get("bin")
        require(
            isinstance(binary_targets, list) and len(binary_targets) == 1,
            "server must expose exactly one standalone binary target",
        )
        binary_target = binary_targets[0]
        require(
            isinstance(binary_target, dict)
            and binary_target.get("name") == "trnm-server-foundation"
            and binary_target.get("path") == "src/main.rs",
            "standalone binary target changed",
        )

        lock = lock_path.read_text(encoding="utf-8")
        for package_name in ("trnm-server", "trnm-contracts", "trnm-persistence-core"):
            require(f'name = "{package_name}"' in lock, f"lock omits {package_name}")
        require(
            "registry+" not in lock and "git+" not in lock,
            "source candidate lock gained an external dependency",
        )

        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        require(
            contract.get("schema") == "trillionnium.server-vertical-slice.v1",
            "wrong server contract schema",
        )
        require(contract.get("project_id") == "trillionnium-game", "wrong project ID")
        require(contract.get("status") == "source-candidate", "server status must remain source-candidate")
        require(contract.get("crate") == "crates/trnm-server", "wrong server crate path")
        require(
            contract.get("binary") == "trnm-server-foundation",
            "contract binary does not match Cargo target",
        )
        implemented = set(
            require_string_list(contract.get("implemented"), "contract.implemented")
        )
        missing_implemented = EXPECTED_IMPLEMENTED - implemented
        require(
            not missing_implemented,
            f"contract omits implemented boundaries: {sorted(missing_implemented)}",
        )
        not_implemented = set(
            require_string_list(
                contract.get("not_implemented"),
                "contract.not_implemented",
            )
        )
        missing_limitations = EXPECTED_NOT_IMPLEMENTED - not_implemented
        require(
            not missing_limitations,
            f"contract omits limitations: {sorted(missing_limitations)}",
        )
        claims = contract.get("claims", {})
        require(isinstance(claims, dict), "contract claims must be an object")
        require(claims.get("rust_binary_exists") is True, "Rust binary claim missing")
        require(claims.get("source_candidate") is True, "source-candidate claim missing")
        for forbidden in (
            "live_database_bound",
            "wire_compatible",
            "behavior_compatible",
            "sg4_complete",
            "production_ready",
            "public_online",
            "nakama_replaced",
        ):
            require(claims.get(forbidden) is False, f"premature server claim: {forbidden}")

        source = lib_path.read_text(encoding="utf-8")
        source_markers = {
            "typed configuration": "pub struct ServerConfig",
            "request limit field": "pub max_request_bytes: usize",
            "request limit environment": "TRNM_SERVER_MAX_REQUEST_BYTES",
            "header limit": "MAX_HEADER_BYTES",
            "bootstrap fixed width": "BOOTSTRAP_BODY_BYTES",
            "command fixed width": "COMMAND_BODY_BYTES",
            "bounded worker queue": "mpsc::sync_channel(self.config.queue_capacity)",
            "bounded socket reads": "set_read_timeout(Some(config.read_timeout))",
            "bounded socket writes": "set_write_timeout(Some(config.write_timeout))",
            "limit threaded to parser": "read_request(&mut stream, config.max_request_bytes)",
            "content-length duplicate fence": "if content_length.is_some()",
            "content-length limit": "if content_length > max_request_bytes",
            "aggregate request limit": "MAX_HEADER_BYTES + max_request_bytes",
            "transfer-encoding fence": 'name.eq_ignore_ascii_case("transfer-encoding")',
            "transfer-encoding error": "ProtocolError::UnsupportedTransferEncoding",
            "health route": '("GET", "/healthz")',
            "readiness route": '("GET", "/readyz")',
            "bootstrap route": '("POST", "/v1/bootstrap")',
            "command route": '("POST", "/v1/command")',
            "duplicate replay": "PrepareOutcome::Duplicate",
            "transactional effect intent": "IntentKind::Broadcast",
            "queue saturation response": "request_queue_full",
            "revision fence regression": "entity_revision_mismatch",
        }
        for boundary, marker in source_markers.items():
            require(marker in source, f"server source missing {boundary}: {marker}")
        require(
            "unsafe" not in source.replace("#![forbid(unsafe_code)]", ""),
            "unsafe token entered server source",
        )

        binary = main_path.read_text(encoding="utf-8")
        binary_markers = {
            "serve command": '"serve"',
            "config validation command": '"check-config"',
            "version command": '"version"',
            "request bound option": '"--max-request-bytes"',
            "no-credit runtime banner": "compatibility_credit=false",
        }
        for boundary, marker in binary_markers.items():
            require(marker in binary, f"server binary missing {boundary}: {marker}")

        documentation = documentation_path.read_text(encoding="utf-8")
        documentation_markers = (
            "`serve`, `check-config`, `version` and help command parsing",
            "`POST /v1/bootstrap`",
            "`POST /v1/command`",
            "`TRNM_SERVER_MAX_REQUEST_BYTES`",
            "`graceful_shutdown_verified=false`",
            "no compatibility or production credit",
        )
        for marker in documentation_markers:
            require(marker in documentation, f"server documentation missing marker: {marker}")
        for obsolete in (
            "`POST /v2/rpc/trnm_vertical_slice`",
            "`--max-requests`",
            "`serve`, `migrate` and help command parsing",
        ):
            require(
                obsolete not in documentation,
                f"server documentation retains obsolete contract: {obsolete}",
            )

        process_smoke = process_smoke_path.read_text(encoding="utf-8")
        for marker in (
            "--bin trnm-server-foundation",
            "--max-request-bytes 4096",
            'request("POST", "/v1/bootstrap"',
            'request("POST", "/v1/command"',
            "graceful_shutdown_verified=false",
            "compatibility_credit=false",
        ):
            require(marker in process_smoke, f"process smoke missing marker: {marker}")

        print(
            json.dumps(
                {
                    "schema": "trillionnium.server-source-check.v2",
                    "status": "passed",
                    "package": package.get("name"),
                    "binary": binary_target.get("name"),
                    "source_marker_count": len(source_markers),
                    "claims": {
                        "source_contract_passed": True,
                        "compiled": False,
                        "live_process_executed": False,
                        "live_database_bound": False,
                        "wire_compatible": False,
                        "production_ready": False,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, tomllib.TOMLDecodeError, ValidationError) as error:
        print(f"trnm-server source check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
