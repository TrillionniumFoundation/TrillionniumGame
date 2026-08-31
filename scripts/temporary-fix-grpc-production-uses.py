#!/usr/bin/env python3
"""Make generated gRPC identity and bound listener observable in production code."""
from pathlib import Path

path = Path("crates/trnm-persistence-pg/src/bin/trnm_server/server.rs")
text = path.read_text(encoding="utf-8")
old = '''    let grpc_bind = config
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
'''
new = '''    let grpc_bind = grpc_worker.as_ref().map_or_else(
        || "disabled".to_owned(),
        |worker| worker.bind().to_string(),
    );
    eprintln!(
        "trnm-server source candidate listening on {} profile={} workers={} queue_capacity={} grpc_bind={} grpc_method={}",
        config.bind,
        config.database_profile.metadata_value(),
        worker_count,
        queue_capacity,
        grpc_bind,
        grpc::HEALTHCHECK_METHOD_PATH,
    );
'''
if text.count(old) != 1:
    raise SystemExit(f"server production-use anchor count={text.count(old)}")
path.write_text(text.replace(old, new), encoding="utf-8")
