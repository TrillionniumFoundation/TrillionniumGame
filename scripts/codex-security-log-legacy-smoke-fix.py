#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()
LEGACY_MAIN = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
LEGACY_SERVER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs"
OUTBOX_WORKER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"
SMOKE = ROOT / "scripts/check-rust-server-process.sh"
SERVER_LIVE = ROOT / "scripts/ci-trnm-server-live.sh"
OUTBOX_LIVE = ROOT / "scripts/ci-outbox-spool-worker.sh"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


main_text = LEGACY_MAIN.read_text(encoding="utf-8")
main_text = replace_once(
    main_text,
    "use trnm_server::error::ServerError;\n",
    "use trnm_server::error::ServerError;\n\n"
    "const CONFIGURATION_VALID_MESSAGE: &str = \"trnm-server configuration valid\";\n"
    "const MIGRATION_COMPLETE_MESSAGE: &str = \"trnm-server migration completed\";\n",
    "legacy operator constants",
)
main_text = replace_once(
    main_text,
    "        Command::CheckConfig => {\n"
    "            println!(\"trnm-server configuration: {config:?}\");\n"
    "            Ok(())\n"
    "        }\n",
    "        Command::CheckConfig => {\n"
    "            println!(\"{CONFIGURATION_VALID_MESSAGE}\");\n"
    "            Ok(())\n"
    "        }\n",
    "legacy configuration sink",
)
main_text = replace_once(
    main_text,
    "        Command::Migrate => {\n"
    "            let report = trnm_server::schema::migrate(&config)?;\n"
    "            println!(\n"
    "                \"migration profile={} applied={} table_count={}\",\n"
    "                report.profile.metadata_value(),\n"
    "                report.migration_applied,\n"
    "                report.table_count,\n"
    "            );\n"
    "            Ok(())\n"
    "        }\n",
    "        Command::Migrate => {\n"
    "            trnm_server::schema::migrate(&config)?;\n"
    "            println!(\"{MIGRATION_COMPLETE_MESSAGE}\");\n"
    "            Ok(())\n"
    "        }\n",
    "legacy migration sink",
)
main_text += (
    "\n#[cfg(test)]\n"
    "mod operator_message_tests {\n"
    "    use super::{CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE};\n\n"
    "    #[test]\n"
    "    fn legacy_operator_messages_are_static_and_secret_free() {\n"
    "        for message in [CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE] {\n"
    "            for forbidden in [\"key\", \"token\", \"database\", \"profile\", \"bind\"] {\n"
    "                assert!(!message.contains(forbidden));\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "}\n"
)
LEGACY_MAIN.write_text(main_text, encoding="utf-8")

server_text = LEGACY_SERVER.read_text(encoding="utf-8")
server_text = replace_once(
    server_text,
    "const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);\n",
    "const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);\n"
    "const STARTUP_MESSAGE: &str = \"trnm-server source candidate started\";\n",
    "legacy startup constant",
)
server_text = replace_once(
    server_text,
    "    eprintln!(\n"
    "        \"trnm-server source candidate listening on {} grpc_bind={:?} profile={} workers={} queue_capacity={}\",\n"
    "        config.bind,\n"
    "        config.grpc_bind,\n"
    "        config.database_profile.metadata_value(),\n"
    "        worker_count,\n"
    "        queue_capacity,\n"
    "    );\n",
    "    eprintln!(\"{STARTUP_MESSAGE}\");\n",
    "legacy startup sink",
)
LEGACY_SERVER.write_text(server_text, encoding="utf-8")

outbox_text = OUTBOX_WORKER.read_text(encoding="utf-8")
outbox_text = replace_once(
    outbox_text,
    "const DATABASE_RETRY_MAX_BACKOFF_MS: u64 = 100;\n",
    "const DATABASE_RETRY_MAX_BACKOFF_MS: u64 = 100;\n"
    "const CONFIGURATION_VALID_MESSAGE: &str = \"trnm-outbox-worker configuration valid\";\n"
    "const STARTUP_MESSAGE: &str = \"trnm-outbox-worker source candidate started\";\n",
    "outbox operator constants",
)
outbox_text = replace_once(
    outbox_text,
    "        Command::CheckConfig => {\n"
    "            println!(\"trnm-outbox-worker configuration: {config:?}\");\n"
    "            Ok(())\n"
    "        }\n",
    "        Command::CheckConfig => {\n"
    "            println!(\"{CONFIGURATION_VALID_MESSAGE}\");\n"
    "            Ok(())\n"
    "        }\n",
    "outbox configuration sink",
)
outbox_text = replace_once(
    outbox_text,
    "    eprintln!(\n"
    "        \"trnm-outbox-worker source candidate started profile={} node={} batch_size={}\",\n"
    "        config.database_profile.metadata_value(),\n"
    "        encode_hex(config.node.as_bytes()),\n"
    "        config.batch_size,\n"
    "    );\n",
    "    eprintln!(\"{STARTUP_MESSAGE}\");\n",
    "outbox startup sink",
)
outbox_text += (
    "\n#[cfg(test)]\n"
    "mod operator_message_tests {\n"
    "    use super::{CONFIGURATION_VALID_MESSAGE, STARTUP_MESSAGE};\n\n"
    "    #[test]\n"
    "    fn outbox_operator_messages_are_static_and_secret_free() {\n"
    "        for message in [CONFIGURATION_VALID_MESSAGE, STARTUP_MESSAGE] {\n"
    "            for forbidden in [\"url\", \"profile\", \"node\", \"batch\", \"key\", \"token\"] {\n"
    "                assert!(!message.contains(forbidden));\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "}\n"
)
OUTBOX_WORKER.write_text(outbox_text, encoding="utf-8")

smoke_text = SMOKE.read_text(encoding="utf-8")
smoke_text = replace_once(
    smoke_text,
    "grep -q 'trnm-server configuration: ServerConfig' \"$run_root/check-config.out\"\n"
    "grep -q 'database_url: \"<redacted>\"' \"$run_root/check-config.out\"\n"
    "grep -q 'admin_token: \"<redacted>\"' \"$run_root/check-config.out\"\n"
    "! grep -q 'smoke-password' \"$run_root/check-config.out\"\n"
    "! grep -q 'smoke-password' \"$run_root/check-config.err\"\n",
    "grep -qx 'trnm-server configuration valid' \"$run_root/check-config.out\"\n"
    "test ! -s \"$run_root/check-config.err\"\n"
    "! grep -Eq 'ServerConfig|database_url|admin_token|postgresql://|smoke-password' \"$run_root/check-config.out\"\n"
    "! grep -Eq 'ServerConfig|database_url|admin_token|postgresql://|smoke-password' \"$run_root/check-config.err\"\n",
    "process smoke output contract",
)
SMOKE.write_text(smoke_text, encoding="utf-8")

server_live_text = SERVER_LIVE.read_text(encoding="utf-8")
server_live_text = replace_once(
    server_live_text,
    "\"$binary\" check-config > \"$evidence/check-config.log\" 2>&1\n",
    "\"$binary\" check-config > \"$evidence/check-config.log\" 2>&1\n"
    "grep -qx 'trnm-server configuration valid' \"$evidence/check-config.log\"\n",
    "server live check-config contract",
)
server_live_text = replace_once(
    server_live_text,
    "grep -F \"migration profile=${profile} applied=true table_count=10\" \"$evidence/migrate.log\"\n",
    "grep -qx 'trnm-server migration completed' \"$evidence/migrate.log\"\n",
    "server live migration contract",
)
SERVER_LIVE.write_text(server_live_text, encoding="utf-8")

outbox_live_text = OUTBOX_LIVE.read_text(encoding="utf-8")
outbox_live_text = replace_once(
    outbox_live_text,
    "\"$worker_bin\" check-config >\"$evidence/worker-config.txt\"\n"
    "grep -q '<redacted>' \"$evidence/worker-config.txt\"\n"
    "if grep -q \"$password\" \"$evidence/worker-config.txt\"; then\n"
    "  echo 'worker check-config leaked database credentials' >&2\n"
    "  exit 1\n"
    "fi\n",
    "\"$worker_bin\" check-config >\"$evidence/worker-config.txt\"\n"
    "grep -qx 'trnm-outbox-worker configuration valid' \"$evidence/worker-config.txt\"\n"
    "if grep -Eq \"$password|WorkerConfig|database_url|database_profile|spool_directory|node\" \"$evidence/worker-config.txt\"; then\n"
    "  echo 'worker check-config exposed configuration-derived data' >&2\n"
    "  exit 1\n"
    "fi\n",
    "outbox live check-config contract",
)
OUTBOX_LIVE.write_text(outbox_live_text, encoding="utf-8")
