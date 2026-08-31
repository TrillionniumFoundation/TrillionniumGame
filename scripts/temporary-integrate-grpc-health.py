#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def patch_config() -> None:
    path = Path("crates/trnm-persistence-pg/src/bin/trnm_server/config.rs")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "pub struct ServerConfig {\n    pub bind: SocketAddr,\n",
        "pub struct ServerConfig {\n    pub bind: SocketAddr,\n    pub grpc_bind: Option<SocketAddr>,\n",
        "config struct",
    )
    text = replace_once(
        text,
        '.field("bind", &self.bind)\n            .field("database_url", &"<redacted>")',
        '.field("bind", &self.bind)\n            .field("grpc_bind", &self.grpc_bind)\n            .field("database_url", &"<redacted>")',
        "config debug",
    )
    text = replace_once(
        text,
        '''        if !bind.ip().is_loopback() && !allow_non_loopback {
            return Err(ServerError::Configuration(
                "non_loopback_bind_requires_explicit_opt_in",
            ));
        }

        let database_url = required(&lookup, "TRNM_SERVER_DATABASE_URL", "database_url_missing")?;
''',
        '''        if !bind.ip().is_loopback() && !allow_non_loopback {
            return Err(ServerError::Configuration(
                "non_loopback_bind_requires_explicit_opt_in",
            ));
        }
        let grpc_bind = lookup("TRNM_SERVER_GRPC_BIND")
            .map(|value| {
                value
                    .parse::<SocketAddr>()
                    .map_err(|_| ServerError::Configuration("grpc_bind_address_invalid"))
            })
            .transpose()?;
        if let Some(grpc_bind) = grpc_bind {
            if !grpc_bind.ip().is_loopback() && !allow_non_loopback {
                return Err(ServerError::Configuration(
                    "grpc_non_loopback_bind_requires_explicit_opt_in",
                ));
            }
            if grpc_bind == bind {
                return Err(ServerError::Configuration(
                    "http_and_grpc_bind_must_differ",
                ));
            }
        }

        let database_url = required(&lookup, "TRNM_SERVER_DATABASE_URL", "database_url_missing")?;
''',
        "config parse",
    )
    text = replace_once(
        text,
        "            Self {\n                bind,\n                database_url,\n",
        "            Self {\n                bind,\n                grpc_bind,\n                database_url,\n",
        "config construction",
    )
    text = replace_once(
        text,
        "        assert!(config.bind.ip().is_loopback());\n        assert_eq!(config.max_request_bytes, 128 * 1024);",
        "        assert!(config.bind.ip().is_loopback());\n        assert!(config.grpc_bind.is_none());\n        assert_eq!(config.max_request_bytes, 128 * 1024);",
        "config default test",
    )
    text = replace_once(
        text,
        '''    #[test]
    fn verify_full_tls_is_secure_by_default_and_material_is_paired() {
''',
        '''    #[test]
    fn grpc_bind_is_optional_distinct_and_public_bind_requires_opt_in() {
        let mut values = base();
        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "127.0.0.1:7351".to_owned(),
        );
        let (_, config) = load(&values).unwrap();
        assert_eq!(
            config.grpc_bind,
            Some("127.0.0.1:7351".parse::<SocketAddr>().unwrap())
        );

        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "127.0.0.1:7350".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("http_and_grpc_bind_must_differ"))
        ));

        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "0.0.0.0:7351".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "grpc_non_loopback_bind_requires_explicit_opt_in"
            ))
        ));
        values.insert(
            "TRNM_SERVER_ALLOW_NON_LOOPBACK".to_owned(),
            "1".to_owned(),
        );
        assert!(load(&values).is_ok());
    }

    #[test]
    fn verify_full_tls_is_secure_by_default_and_material_is_paired() {
''',
        "config grpc test",
    )
    path.write_text(text, encoding="utf-8")


def patch_server() -> None:
    path = Path("crates/trnm-persistence-pg/src/bin/trnm_server/server.rs")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "use super::error::ServerError;\nuse super::http::{read_request, Request, Response};",
        "use super::error::ServerError;\nuse super::grpc;\nuse super::http::{read_request, Request, Response};",
        "server import",
    )
    text = replace_once(
        text,
        "    let worker_failed = Arc::new(AtomicBool::new(false));\n    let metrics = SharedAppMetrics::default();\n    let mut workers = Vec::with_capacity(worker_count);",
        "    let worker_failed = Arc::new(AtomicBool::new(false));\n    let metrics = SharedAppMetrics::default();\n    let grpc_worker = grpc::spawn(\n        config.grpc_bind,\n        Arc::clone(&draining),\n        Arc::clone(&worker_failed),\n    )?;\n    let mut workers = Vec::with_capacity(worker_count);",
        "server spawn",
    )
    text = replace_once(
        text,
        '''        "trnm-server source candidate listening on {} profile={} workers={} queue_capacity={}",
        config.bind,
        config.database_profile.metadata_value(),
        worker_count,
        queue_capacity,
''',
        '''        "trnm-server source candidate listening on {} grpc_bind={:?} profile={} workers={} queue_capacity={}",
        config.bind,
        config.grpc_bind,
        config.database_profile.metadata_value(),
        worker_count,
        queue_capacity,
''',
        "server diagnostic",
    )
    text = replace_once(
        text,
        '''    let accept_result = accept_loop(&listener, &sender, config, &draining, &worker_failed);
    drop(sender);
    let join_result = join_workers(workers);
    accept_result?;
    join_result?;
    eprintln!("trnm-server source candidate drained");
''',
        '''    let accept_result = accept_loop(&listener, &sender, config, &draining, &worker_failed);
    draining.store(true, Ordering::Release);
    drop(sender);
    let join_result = join_workers(workers);
    let grpc_join_result = grpc::join(grpc_worker);
    accept_result?;
    join_result?;
    grpc_join_result?;
    eprintln!("trnm-server source candidate drained");
''',
        "server join",
    )
    path.write_text(text, encoding="utf-8")


def dependency_insertions(text: str, label: str) -> str:
    text = replace_once(
        text,
        '''        "postgres-native-tls": "=0.5.3",
        "r2d2": "=0.8.10",
''',
        '''        "postgres-native-tls": "=0.5.3",
        "prost": "=0.14.3",
        "r2d2": "=0.8.10",
''',
        f"{label} prost",
    )
    return replace_once(
        text,
        '''        "r2d2_postgres": "=0.18.2",
        "trnm-contracts": {"path": "../trnm-contracts"},
''',
        '''        "r2d2_postgres": "=0.18.2",
        "tokio": {"version": "=1.53.1", "features": ["rt", "time"]},
        "tonic": {"version": "=0.14.5", "features": ["transport"]},
        "tonic-prost": "=0.14.5",
        "trnm-contracts": {"path": "../trnm-contracts"},
''',
        f"{label} tonic",
    )


def patch_checkers() -> None:
    path = Path("scripts/check-trnm-server.py")
    text = dependency_insertions(path.read_text(encoding="utf-8"), "server checker")
    text = replace_once(
        text,
        '    MODULE_ROOT / "error.rs",\n    MODULE_ROOT / "http.rs",',
        '    MODULE_ROOT / "error.rs",\n    MODULE_ROOT / "grpc.rs",\n    MODULE_ROOT / "http.rs",',
        "server checker files",
    )
    text = replace_once(
        text,
        '''    if manifest.get("dependencies") != expected_dependencies:
        fail("server candidate changed the reviewed persistence dependency boundary")

    sources = {
''',
        '''    if manifest.get("dependencies") != expected_dependencies:
        fail("server candidate changed the reviewed persistence dependency boundary")
    expected_build_dependencies = {
        "prost-build": "=0.14.3",
        "prost-types": "=0.14.3",
        "protoc-bin-vendored": "=3.2.0",
        "tonic-build": "=0.14.5",
        "tonic-prost-build": "=0.14.5",
    }
    if manifest.get("build-dependencies") != expected_build_dependencies:
        fail("server candidate changed the reviewed protobuf build dependency boundary")

    sources = {
''',
        "server checker build deps",
    )
    text = replace_once(
        text,
        '    "shared_codec_rejects_encoding_mismatch",\n',
        '    "shared_codec_rejects_encoding_mismatch",\n    "grpc_bind_is_optional_distinct_and_public_bind_requires_opt_in",\n    "official_healthcheck_method_path_is_exact",\n    "generated_service_returns_an_empty_response",\n    "generated_client_reaches_the_http2_healthcheck_path",\n',
        "server checker tests",
    )
    text = replace_once(
        text,
        '            "TRNM_SERVER_ALLOW_NON_LOOPBACK",\n',
        '            "TRNM_SERVER_ALLOW_NON_LOOPBACK",\n            "TRNM_SERVER_GRPC_BIND",\n',
        "server checker config marker",
    )
    text = replace_once(
        text,
        '        "crates/trnm-persistence-pg/src/bin/trnm_server/http.rs": [\n',
        '        "crates/trnm-persistence-pg/src/bin/trnm_server/grpc.rs": [\n            "/nakama.api.Nakama/Healthcheck",\n            "NakamaServer::new",\n            "serve_with_shutdown",\n            "worker_failed.store(true",\n        ],\n        "crates/trnm-persistence-pg/src/bin/trnm_server/http.rs": [\n',
        "server checker grpc markers",
    )
    text = replace_once(
        text,
        '            "websocket::serve_once",\n',
        '            "websocket::serve_once",\n            "grpc::spawn",\n            "grpc::join",\n',
        "server checker lifecycle markers",
    )
    text = replace_once(
        text,
        '    if test_count < 53:\n        fail(f"expected at least 53 server/session/pool source tests, got {test_count}")',
        '    if test_count < 57:\n        fail(f"expected at least 57 server/session/pool/grpc source tests, got {test_count}")',
        "server checker threshold",
    )
    text = replace_once(
        text,
        '        "session_http_source_candidate",\n',
        '        "session_http_source_candidate",\n        "grpc_healthcheck_source_candidate",\n',
        "server checker source claim",
    )
    path.write_text(text, encoding="utf-8")

    path = Path("scripts/check-rust-foundation.py")
    path.write_text(
        dependency_insertions(path.read_text(encoding="utf-8"), "foundation checker"),
        encoding="utf-8",
    )


def patch_documents() -> None:
    path = Path("docs/status/TRNM_SERVER_STATUS.json")
    status = json.loads(path.read_text(encoding="utf-8"))
    item = (
        "optional same-process generated Nakama Healthcheck gRPC listener with "
        "shared drain and worker-failure lifecycle"
    )
    if item not in status["implemented_source"]:
        status["implemented_source"].append(item)
    status["claims"]["grpc_healthcheck_source_candidate"] = True
    status["claims"]["grpc_implemented"] = False
    path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    path = Path("contracts/grpc/nakama-healthcheck-v1.json")
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["candidate"].update(
        {
            "runtime_config": "ServerConfig.grpc_bind",
            "runtime_spawn": "server::serve -> grpc::spawn",
            "runtime_join": "server::serve -> grpc::join",
        }
    )
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")

    path = Path(".github/workflows/grpc-health-source.yml")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "          grep -Fq 'tonic_prost_build::configure()' crates/trnm-persistence-pg/build.rs\n",
        "          grep -Fq 'tonic_prost_build::configure()' crates/trnm-persistence-pg/build.rs\n"
        "          grep -Fq 'TRNM_SERVER_GRPC_BIND' crates/trnm-persistence-pg/src/bin/trnm_server/config.rs\n"
        "          grep -Fq 'grpc::spawn' crates/trnm-persistence-pg/src/bin/trnm_server/server.rs\n"
        "          grep -Fq 'grpc::join' crates/trnm-persistence-pg/src/bin/trnm_server/server.rs\n",
        "grpc workflow runtime assertions",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_config()
    patch_server()
    patch_checkers()
    patch_documents()


if __name__ == "__main__":
    main()
