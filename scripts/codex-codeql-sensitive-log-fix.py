#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()
RUNTIME_MOD = ROOT / "crates/trnm-server/src/runtime/mod.rs"
SERVER = ROOT / "crates/trnm-server/src/runtime/server.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


mod_text = RUNTIME_MOD.read_text(encoding="utf-8")
mod_text = replace_once(
    mod_text,
    "use config::{Command, ServerConfig};\npub use error::ServerError;\n",
    "use config::{Command, ServerConfig};\npub use error::ServerError;\n\n"
    "const CONFIGURATION_VALID_MESSAGE: &str = \"trnm-server configuration valid\";\n"
    "const MIGRATION_COMPLETE_MESSAGE: &str = \"trnm-server migration completed\";\n",
    "operator message constants",
)
mod_text = replace_once(
    mod_text,
    "        Command::CheckConfig => {\n"
    "            println!(\"trnm-server configuration: {config:?}\");\n"
    "            Ok(())\n"
    "        }\n",
    "        Command::CheckConfig => {\n"
    "            println!(\"{CONFIGURATION_VALID_MESSAGE}\");\n"
    "            Ok(())\n"
    "        }\n",
    "configuration debug sink",
)
mod_text = replace_once(
    mod_text,
    "        Command::Migrate => {\n"
    "            let report = schema::migrate(&config)?;\n"
    "            println!(\n"
    "                \"migration profile={} applied={} table_count={}\",\n"
    "                report.profile.metadata_value(),\n"
    "                report.migration_applied,\n"
    "                report.table_count,\n"
    "            );\n"
    "            Ok(())\n"
    "        }\n",
    "        Command::Migrate => {\n"
    "            schema::migrate(&config)?;\n"
    "            println!(\"{MIGRATION_COMPLETE_MESSAGE}\");\n"
    "            Ok(())\n"
    "        }\n",
    "migration derived sink",
)
mod_text += (
    "\n#[cfg(test)]\n"
    "mod operator_message_tests {\n"
    "    use super::{CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE};\n\n"
    "    #[test]\n"
    "    fn operator_messages_are_static_and_secret_free() {\n"
    "        for message in [CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE] {\n"
    "            assert!(!message.contains(\"key\"));\n"
    "            assert!(!message.contains(\"token\"));\n"
    "            assert!(!message.contains(\"database\"));\n"
    "            assert!(!message.contains(\"profile\"));\n"
    "        }\n"
    "    }\n"
    "}\n"
)
RUNTIME_MOD.write_text(mod_text, encoding="utf-8")

server_text = SERVER.read_text(encoding="utf-8")
server_text = replace_once(
    server_text,
    "const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);\n",
    "const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);\n"
    "const STARTUP_MESSAGE: &str = \"trnm-server source candidate started\";\n",
    "startup message constant",
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
    "startup configuration sink",
)
server_text = replace_once(
    server_text,
    "    #[test]\n"
    "    fn connection_parse_failure_has_no_internal_reason() {\n",
    "    #[test]\n"
    "    fn startup_message_is_static_and_configuration_free() {\n"
    "        assert_eq!(STARTUP_MESSAGE, \"trnm-server source candidate started\");\n"
    "        for forbidden in [\"bind\", \"profile\", \"worker\", \"queue\", \"key\", \"token\"] {\n"
    "            assert!(!STARTUP_MESSAGE.contains(forbidden));\n"
    "        }\n"
    "    }\n\n"
    "    #[test]\n"
    "    fn connection_parse_failure_has_no_internal_reason() {\n",
    "startup message regression",
)
SERVER.write_text(server_text, encoding="utf-8")
