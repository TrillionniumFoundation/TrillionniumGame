#!/usr/bin/env python3
"""Apply the reviewed generated Healthcheck integration before exact-tree validation."""
from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: patch anchor count={count} for {old[:100]!r}")
    file.write_text(text.replace(old, new), encoding="utf-8")


def patch_config() -> None:
    path = "crates/trnm-persistence-pg/src/bin/trnm_server/config.rs"
    replace_once(
        path,
        "pub struct ServerConfig {\n    pub bind: SocketAddr,\n    pub database_url: String,",
        "pub struct ServerConfig {\n    pub bind: SocketAddr,\n    pub grpc_bind: Option<SocketAddr>,\n    pub database_url: String,",
    )
    replace_once(
        path,
        '.field("bind", &self.bind)\n            .field("database_url", &"<redacted>")',
        '.field("bind", &self.bind)\n            .field("grpc_bind", &self.grpc_bind)\n            .field("database_url", &"<redacted>")',
    )
    bind_anchor = '''        if !bind.ip().is_loopback() && !allow_non_loopback {
            return Err(ServerError::Configuration(
                "non_loopback_bind_requires_explicit_opt_in",
            ));
        }

'''
    grpc_parse = bind_anchor + '''        let grpc_bind = lookup("TRNM_SERVER_GRPC_BIND")
            .map(|value| {
                value
                    .parse::<SocketAddr>()
                    .map_err(|_| ServerError::Configuration("grpc_bind_address_invalid"))
            })
            .transpose()?;
        if let Some(grpc_bind) = grpc_bind {
            if grpc_bind.port() == 0 {
                return Err(ServerError::Configuration("grpc_bind_address_invalid"));
            }
            if !grpc_bind.ip().is_loopback() && !allow_non_loopback {
                return Err(ServerError::Configuration(
                    "grpc_non_loopback_bind_requires_explicit_opt_in",
                ));
            }
            if grpc_bind == bind {
                return Err(ServerError::Configuration(
                    "grpc_bind_must_differ_from_http_bind",
                ));
            }
        }

'''
    replace_once(path, bind_anchor, grpc_parse)
    replace_once(
        path,
        "            Self {\n                bind,\n                database_url,",
        "            Self {\n                bind,\n                grpc_bind,\n                database_url,",
    )
    replace_once(
        path,
        "        assert!(config.bind.ip().is_loopback());\n        assert_eq!(config.max_request_bytes, 128 * 1024);",
        "        assert!(config.bind.ip().is_loopback());\n        assert_eq!(config.grpc_bind, None);\n        assert_eq!(config.max_request_bytes, 128 * 1024);",
    )
    grpc_test = '''    #[test]
    fn grpc_bind_is_explicit_distinct_and_uses_public_opt_in() {
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
            Err(ServerError::Configuration(
                "grpc_bind_must_differ_from_http_bind"
            ))
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
        assert!(!load(&values).unwrap().1.grpc_bind.unwrap().ip().is_loopback());

        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "127.0.0.1:0".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("grpc_bind_address_invalid"))
        ));
    }

'''
    replace_once(
        path,
        "    #[test]\n    fn verify_full_tls_is_secure_by_default_and_material_is_paired() {",
        grpc_test + "    #[test]\n    fn verify_full_tls_is_secure_by_default_and_material_is_paired() {",
    )


def patch_server() -> None:
    path = "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs"
    replace_once(
        path,
        "use super::error::ServerError;\nuse super::http::{read_request, Request, Response};",
        "use super::error::ServerError;\nuse super::grpc;\nuse super::http::{read_request, Request, Response};",
    )
    replace_once(
        path,
        '''    eprintln!(
        "trnm-server source candidate listening on {} profile={} workers={} queue_capacity={}",
        config.bind,
        config.database_profile.metadata_value(),
        worker_count,
        queue_capacity,
    );
''',
        '''    let grpc_worker = grpc::spawn(
        config.grpc_bind,
        Arc::clone(&draining),
        Arc::clone(&worker_failed),
    )?;
    let grpc_bind = config
        .grpc_bind
        .map_or_else(|| "disabled".to_owned(), |value| value.to_string());
    eprintln!(
        "trnm-server source candidate listening on {} profile={} workers={} queue_capacity={} grpc_bind={}",
        config.bind,
        config.database_profile.metadata_value(),
        worker_count,
        queue_capacity,
        grpc_bind,
    );
''',
    )
    replace_once(
        path,
        '''    let accept_result = accept_loop(&listener, &sender, config, &draining, &worker_failed);
    drop(sender);
    let join_result = join_workers(workers);
    accept_result?;
    join_result?;
''',
        '''    let accept_result = accept_loop(&listener, &sender, config, &draining, &worker_failed);
    draining.store(true, Ordering::Release);
    drop(sender);
    let join_result = join_workers(workers);
    let grpc_join_result = grpc::join(grpc_worker);
    accept_result?;
    join_result?;
    grpc_join_result?;
''',
    )


def patch_foundation_checker() -> None:
    path = "scripts/check-rust-foundation.py"
    replace_once(
        path,
        '''        "postgres-native-tls": "=0.5.3",
        "r2d2": "=0.8.10",''',
        '''        "postgres-native-tls": "=0.5.3",
        "prost": "=0.14.3",
        "r2d2": "=0.8.10",''',
    )
    replace_once(
        path,
        '''        "r2d2_postgres": "=0.18.2",
        "trnm-contracts": {"path": "../trnm-contracts"},''',
        '''        "r2d2_postgres": "=0.18.2",
        "tokio": {"version": "=1.53.1", "features": ["rt", "time"]},
        "tonic": {"version": "=0.14.5", "features": ["transport"]},
        "tonic-prost": "=0.14.5",
        "trnm-contracts": {"path": "../trnm-contracts"},''',
    )
    replace_once(
        path,
        '''    dependencies = manifest.get("dependencies", {})
    if dependencies != EXPECTED_DEPENDENCIES[member]:
        fail(
            f"{member}: dependency allowlist mismatch: "
            f"expected {EXPECTED_DEPENDENCIES[member]!r}, got {dependencies!r}"
        )
    source_files = sorted((root / "src").rglob("*.rs"))
''',
        '''    dependencies = manifest.get("dependencies", {})
    if dependencies != EXPECTED_DEPENDENCIES[member]:
        fail(
            f"{member}: dependency allowlist mismatch: "
            f"expected {EXPECTED_DEPENDENCIES[member]!r}, got {dependencies!r}"
        )
    expected_build_dependencies = (
        {
            "prost-build": "=0.14.3",
            "prost-types": "=0.14.3",
            "protoc-bin-vendored": "=3.2.0",
            "tonic-build": "=0.14.5",
            "tonic-prost-build": "=0.14.5",
        }
        if member == "crates/trnm-persistence-pg"
        else {}
    )
    build_dependencies = manifest.get("build-dependencies", {})
    if build_dependencies != expected_build_dependencies:
        fail(
            f"{member}: build dependency allowlist mismatch: "
            f"expected {expected_build_dependencies!r}, got {build_dependencies!r}"
        )
    source_files = sorted((root / "src").rglob("*.rs"))
''',
    )


def patch_server_checker() -> None:
    path = "scripts/check-trnm-server.py"
    replace_once(
        path,
        '''REQUIRED_FILES = {
    PERSISTENCE_ROOT / "pool.rs",''',
        '''REQUIRED_FILES = {
    ROOT / "crates/trnm-persistence-pg/build.rs",
    ROOT / "crates/trnm-persistence-pg/proto/nakama-healthcheck.proto",
    PERSISTENCE_ROOT / "pool.rs",''',
    )
    replace_once(
        path,
        '''    MODULE_ROOT / "error.rs",
    MODULE_ROOT / "http.rs",''',
        '''    MODULE_ROOT / "error.rs",
    MODULE_ROOT / "grpc.rs",
    MODULE_ROOT / "http.rs",''',
    )
    replace_once(
        path,
        '''    "default_candidate_config_is_loopback_bounded_and_redacted",
    "accidental_public_bind_and_implicit_plaintext_database_fail_closed",''',
        '''    "default_candidate_config_is_loopback_bounded_and_redacted",
    "grpc_bind_is_explicit_distinct_and_uses_public_opt_in",
    "accidental_public_bind_and_implicit_plaintext_database_fail_closed",''',
    )
    replace_once(
        path,
        '''    "wrapper_is_cloneable_without_exposing_database_url",
    "rfc6455_handshake_accept_matches_the_published_vector",''',
        '''    "wrapper_is_cloneable_without_exposing_database_url",
    "official_healthcheck_method_path_is_exact",
    "generated_service_returns_an_empty_response",
    "generated_client_reaches_the_http2_healthcheck_path",
    "rfc6455_handshake_accept_matches_the_published_vector",''',
    )
    replace_once(
        path,
        '''        "postgres-native-tls": "=0.5.3",
        "r2d2": "=0.8.10",''',
        '''        "postgres-native-tls": "=0.5.3",
        "prost": "=0.14.3",
        "r2d2": "=0.8.10",''',
    )
    replace_once(
        path,
        '''        "r2d2_postgres": "=0.18.2",
        "trnm-contracts": {"path": "../trnm-contracts"},''',
        '''        "r2d2_postgres": "=0.18.2",
        "tokio": {"version": "=1.53.1", "features": ["rt", "time"]},
        "tonic": {"version": "=0.14.5", "features": ["transport"]},
        "tonic-prost": "=0.14.5",
        "trnm-contracts": {"path": "../trnm-contracts"},''',
    )
    replace_once(
        path,
        '''    if manifest.get("dependencies") != expected_dependencies:
        fail("server candidate changed the reviewed persistence dependency boundary")

    sources = {''',
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

    sources = {''',
    )
    replace_once(
        path,
        '''    required_markers = {
        "crates/trnm-persistence-pg/src/pool.rs": [''',
        '''    required_markers = {
        "crates/trnm-persistence-pg/build.rs": [
            "protoc_bin_vendored::protoc_bin_path",
            "compile_well_known_types(true)",
            "compile_protos",
        ],
        "crates/trnm-persistence-pg/proto/nakama-healthcheck.proto": [
            "package nakama.api;",
            "rpc Healthcheck (google.protobuf.Empty) returns (google.protobuf.Empty);",
        ],
        "crates/trnm-persistence-pg/src/pool.rs": [''',
    )
    replace_once(
        path,
        '''            "pub(crate) mod auth;",
            "pub(crate) mod pool;",''',
        '''            "pub(crate) mod auth;",
            "pub(crate) mod grpc;",
            "pub(crate) mod pool;",''',
    )
    replace_once(
        path,
        '''            "TRNM_SERVER_ALLOW_NON_LOOPBACK",
            "TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE",''',
        '''            "TRNM_SERVER_ALLOW_NON_LOOPBACK",
            "TRNM_SERVER_GRPC_BIND",
            "grpc_bind_must_differ_from_http_bind",
            "TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE",''',
    )
    replace_once(
        path,
        '''        "crates/trnm-persistence-pg/src/bin/trnm_server/http.rs": [''',
        '''        "crates/trnm-persistence-pg/src/bin/trnm_server/grpc.rs": [
            "/nakama.api.Nakama/Healthcheck",
            "NakamaServer::new",
            "serve_with_shutdown",
            "worker_failed.store(true",
            "generated_client_reaches_the_http2_healthcheck_path",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/http.rs": [''',
    )
    replace_once(
        path,
        '''            "websocket::is_route",
            "websocket::serve_once",''',
        '''            "grpc::spawn",
            "grpc::join",
            "websocket::is_route",
            "websocket::serve_once",''',
    )
    replace_once(
        path,
        '''        "websocket_protobuf_envelope_source_candidate",
        "bounded_pool_source_candidate",''',
        '''        "websocket_protobuf_envelope_source_candidate",
        "grpc_healthcheck_source_candidate",
        "bounded_pool_source_candidate",''',
    )
    replace_once(
        path,
        '''                "websocket_protobuf_envelope_source_candidate": True,
                "bounded_pool_source_candidate": True,''',
        '''                "websocket_protobuf_envelope_source_candidate": True,
                "grpc_healthcheck_source_candidate": True,
                "bounded_pool_source_candidate": True,''',
    )


def patch_status() -> None:
    path = Path("docs/status/TRNM_SERVER_STATUS.json")
    status = json.loads(path.read_text(encoding="utf-8"))
    for value in [
        "crates/trnm-persistence-pg/build.rs",
        "crates/trnm-persistence-pg/proto/nakama-healthcheck.proto",
        "crates/trnm-persistence-pg/src/bin/trnm_server/grpc.rs",
        "contracts/grpc/nakama-healthcheck-v1.json",
    ]:
        if value not in status["source_paths"]:
            status["source_paths"].append(value)
    for value in [
        "generated Rust client and server bindings for the pinned official nakama.api.Nakama Healthcheck signature",
        "optional same-process HTTP/2 gRPC listener configured by TRNM_SERVER_GRPC_BIND",
        "shared drain and worker-failure fencing between HTTP and gRPC listeners",
        "generated client-to-server Healthcheck transport test",
    ]:
        if value not in status["implemented_source"]:
            status["implemented_source"].append(value)
    status["not_implemented_or_verified"] = [
        (
            "remaining Nakama gRPC methods and grpc-gateway behavior beyond the generated Healthcheck subset"
            if value == "gRPC server and grpc-gateway behavior"
            else value
        )
        for value in status["not_implemented_or_verified"]
    ]
    status["claims"]["grpc_healthcheck_source_candidate"] = True
    status["claims"]["grpc_implemented"] = False
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    patch_config()
    patch_server()
    patch_foundation_checker()
    patch_server_checker()
    patch_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
