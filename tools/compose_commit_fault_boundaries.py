#!/usr/bin/env python3
"""Compose fail-closed commit-boundary injection and rollback regressions."""
from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def update_manifest(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/Cargo.toml"
    text = read(path)
    marker = "transaction-test-hooks = []"
    if marker not in text:
        anchor = "session-test-hooks = []\n"
        require(anchor in text, "session-test-hooks feature anchor missing")
        text = text.replace(anchor, anchor + marker + "\n", 1)
    write(path, text)


def update_library(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/src/lib.rs"
    text = read(path)
    if "mod fault;" not in text:
        anchor = "mod authority;\n" if "mod authority;\n" in text else "mod auth;\n"
        require(anchor in text, "module anchor missing")
        text = text.replace(anchor, anchor + "mod fault;\n", 1)
    export = '#[cfg(feature = "transaction-test-hooks")]\npub use fault::CommitMutationPoint;\n'
    if export not in text:
        anchor = "pub use auth::{\n"
        require(anchor in text, "public export anchor missing")
        text = text.replace(anchor, export + anchor, 1)

    old_open = textwrap.dedent(
        '''\
            pub fn commit_command(
                &mut self,
                request: &CommitRequest,
            ) -> Result<CommitOutcome, DomainError> {
                validate_request(request)?;
        '''
    )
    new_open = textwrap.dedent(
        '''\
            pub fn commit_command(
                &mut self,
                request: &CommitRequest,
            ) -> Result<CommitOutcome, DomainError> {
                self.commit_command_with_hook(request, |_| Ok(()))
            }

            #[cfg(feature = "transaction-test-hooks")]
            pub fn commit_command_with_test_hook(
                &mut self,
                request: &CommitRequest,
                hook: impl FnMut(fault::CommitMutationPoint) -> Result<(), DomainError>,
            ) -> Result<CommitOutcome, DomainError> {
                self.commit_command_with_hook(request, hook)
            }

            fn commit_command_with_hook(
                &mut self,
                request: &CommitRequest,
                mut hook: impl FnMut(fault::CommitMutationPoint) -> Result<(), DomainError>,
            ) -> Result<CommitOutcome, DomainError> {
                validate_request(request)?;
        '''
    )
    if "fn commit_command_with_hook(" not in text:
        require(old_open in text, "commit_command opening anchor missing")
        text = text.replace(old_open, new_open, 1)

    head_anchor = textwrap.dedent(
        '''\
                if updated != 1 {
                    return Err(error(
                        StableCode::Aborted,
                        "entity_compare_and_swap_failed",
                        RetryClass::ResyncRequired,
                    ));
                }

                let first_event_sequence_i64 = first_event_sequence.map(to_i64).transpose()?;
        '''
    )
    head_replacement = head_anchor.replace(
        "\n        let first_event_sequence_i64",
        "\n        hook(fault::CommitMutationPoint::HeadComparedAndSwapped)?;\n\n        let first_event_sequence_i64",
        1,
    )
    if "CommitMutationPoint::HeadComparedAndSwapped" not in text:
        require(head_anchor in text, "head-CAS hook anchor missing")
        text = text.replace(head_anchor, head_replacement, 1)

    if "CommitMutationPoint::ReceiptInserted" not in text:
        pattern = re.compile(
            r'(?s)(\n        transaction\n            \.execute\(\n                "INSERT INTO trnm_command_receipts .*?\n            \.map_err\(map_postgres_error\)\?;)(\n\n        let mut sequence = last_event_sequence;)'
        )
        text, count = pattern.subn(
            r"\1\n        hook(fault::CommitMutationPoint::ReceiptInserted)?;\2",
            text,
            count=1,
        )
        require(count == 1, "receipt-insert hook anchor missing")

    if "CommitMutationPoint::EventsAppended" not in text:
        anchor = "        }\n\n        for (position, intent) in request.outbox.iter().enumerate() {\n"
        require(anchor in text, "event-loop hook anchor missing")
        text = text.replace(
            anchor,
            "        }\n        hook(fault::CommitMutationPoint::EventsAppended)?;\n\n"
            "        for (position, intent) in request.outbox.iter().enumerate() {\n",
            1,
        )

    if "CommitMutationPoint::OutboxAppended" not in text:
        pattern = re.compile(
            r'(\n        transaction\.commit\(\)\.map_err\(map_postgres_error\)\?;\n        Ok\(CommitOutcome::Applied)'
        )
        replacement = (
            "\n        hook(fault::CommitMutationPoint::OutboxAppended)?;"
            "\n        hook(fault::CommitMutationPoint::BeforeCommit)?;"
            r"\1"
        )
        text, count = pattern.subn(replacement, text, count=1)
        require(count == 1, "pre-commit hook anchor missing")
    write(path, text)


def fault_source() -> str:
    return textwrap.dedent(
        '''\
        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub enum CommitMutationPoint {
            HeadComparedAndSwapped,
            ReceiptInserted,
            EventsAppended,
            OutboxAppended,
            BeforeCommit,
        }
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        #![cfg(feature = "transaction-test-hooks")]

        use std::env;

        use postgres::{Client, NoTls};
        use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};
        use trnm_persistence_pg::{
            CommitMutationPoint, CommitRequest, DatabaseProfile, EntityId, EventId, EventInput,
            IntentId, IntentKind, OutboxInput, PgRepository,
        };

        fn digest(value: u8) -> Digest32 {
            Digest32::new([value; 32])
        }

        fn profile(value: &str) -> DatabaseProfile {
            match value {
                "postgresql" => DatabaseProfile::PostgreSql,
                "cockroachdb" => DatabaseProfile::CockroachDb,
                other => panic!("unsupported TRNM_DATABASE_PROFILE={other}"),
            }
        }

        fn live_database_environment(label: &str) -> Option<(String, DatabaseProfile)> {
            let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
                Err(env::VarError::NotPresent) => false,
                Err(error) => panic!("cannot read TRNM_REQUIRE_LIVE_DATABASE: {error}"),
                Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
                Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
                Ok(value) => panic!("invalid TRNM_REQUIRE_LIVE_DATABASE={value:?}"),
            };
            let database_url = match env::var("TRNM_DATABASE_URL") {
                Ok(value) if !value.is_empty() => value,
                Ok(_) if required => panic!("{label}: empty TRNM_DATABASE_URL"),
                Ok(_) => return None,
                Err(env::VarError::NotPresent) if required => {
                    panic!("{label}: required TRNM_DATABASE_URL is absent")
                }
                Err(env::VarError::NotPresent) => return None,
                Err(error) => panic!("{label}: cannot read TRNM_DATABASE_URL: {error}"),
            };
            let profile_value = env::var("TRNM_DATABASE_PROFILE")
                .unwrap_or_else(|_| panic!("{label}: TRNM_DATABASE_PROFILE is required"));
            Some((database_url, profile(&profile_value)))
        }

        fn injected_failure() -> DomainError {
            DomainError::new(
                StableCode::Unavailable,
                "injected_commit_boundary_failure",
                RetryClass::SafeBackoff,
            )
        }

        fn assert_zero_partial_commit(
            database_url: &str,
            entity: EntityId,
            command: CommandId,
            initial_state: Digest32,
        ) {
            let mut client = Client::connect(database_url, NoTls).unwrap();
            let row = client
                .query_one(
                    "SELECT revision, last_event_sequence, state_digest \\
                     FROM trnm_entity_heads WHERE entity_id = $1",
                    &[&entity.as_bytes().as_slice()],
                )
                .unwrap();
            assert_eq!(row.get::<_, i64>(0), 0);
            assert_eq!(row.get::<_, i64>(1), 0);
            assert_eq!(row.get::<_, Vec<u8>>(2), initial_state.as_bytes().as_slice());

            for (table, predicate) in [
                ("trnm_command_receipts", "entity_id = $1 AND command_id = $2"),
                ("trnm_events", "entity_id = $1 AND command_id = $2"),
                ("trnm_outbox", "entity_id = $1 AND command_id = $2"),
                ("trnm_command_outbox", "entity_id = $1 AND command_id = $2"),
            ] {
                let sql = format!("SELECT COUNT(*) FROM {table} WHERE {predicate}");
                let count: i64 = client
                    .query_one(
                        &sql,
                        &[
                            &entity.as_bytes().as_slice(),
                            &command.as_bytes().as_slice(),
                        ],
                    )
                    .unwrap()
                    .get(0);
                assert_eq!(count, 0, "partial durable rows remained in {table}");
            }
        }

        #[test]
        fn commit_fault_injection_rolls_back_every_internal_boundary() {
            let Some((database_url, profile)) =
                live_database_environment("commit durable-boundary contract")
            else {
                return;
            };
            let points = [
                CommitMutationPoint::HeadComparedAndSwapped,
                CommitMutationPoint::ReceiptInserted,
                CommitMutationPoint::EventsAppended,
                CommitMutationPoint::OutboxAppended,
                CommitMutationPoint::BeforeCommit,
            ];

            for (index, target) in points.into_iter().enumerate() {
                let seed = 0xa0_u8 + u8::try_from(index).unwrap();
                let entity = EntityId::new([seed; 16]);
                let command = CommandId::new([seed + 0x10; 16]);
                let initial_state = digest(seed + 0x20);
                let mut repository = PgRepository::connect(&database_url, profile).unwrap();
                repository
                    .bootstrap_entity(entity, 1, initial_state, 1)
                    .unwrap();
                let request = CommitRequest {
                    entity,
                    command,
                    fingerprint: digest(seed + 0x21),
                    expected_revision: 0,
                    authority_generation: 1,
                    next_state: digest(seed + 0x22),
                    committed_at_ms: 10,
                    events: vec![EventInput {
                        id: EventId::new([seed + 0x30; 16]),
                        payload: digest(seed + 0x23),
                    }],
                    outbox: vec![OutboxInput {
                        id: IntentId::new([seed + 0x40; 16]),
                        kind: IntentKind::ExternalEffect,
                        payload: digest(seed + 0x24),
                        available_at_ms: 10,
                    }],
                };
                let error = repository
                    .commit_command_with_test_hook(&request, |observed| {
                        if observed == target {
                            Err(injected_failure())
                        } else {
                            Ok(())
                        }
                    })
                    .unwrap_err();
                assert_eq!(error.reason(), "injected_commit_boundary_failure");
                drop(repository);
                assert_zero_partial_commit(&database_url, entity, command, initial_state);
            }
        }
        '''
    )


def update_contracts(root: Path) -> None:
    foundation = root / "scripts/check-rust-foundation.py"
    text = read(foundation)
    test_marker = '    "commit_fault_injection_rolls_back_every_internal_boundary",\n'
    if test_marker not in text:
        anchor = '    "storage_occ_acl_and_batch_rollback_are_transactional",\n'
        require(anchor in text, "foundation required-test anchor missing")
        text = text.replace(anchor, anchor + test_marker, 1)
    write(foundation, text)

    server = root / "scripts/check-trnm-server.py"
    text = read(server)
    file_marker = '    ROOT / "crates/trnm-persistence-pg/tests/commit_fault_boundaries.rs",\n'
    if file_marker not in text:
        anchor = '    ROOT / "crates/trnm-persistence-pg/tests/authority_storage.rs",\n'
        require(anchor in text, "server integration-test anchor missing")
        text = text.replace(anchor, anchor + file_marker, 1)
    if test_marker not in text:
        anchor = '    "storage_occ_acl_and_batch_rollback_are_transactional",\n'
        require(anchor in text, "server required-test anchor missing")
        text = text.replace(anchor, anchor + test_marker, 1)
    write(server, text)


def update_documents(root: Path) -> None:
    path = root / "docs/development/SCHEMA_AUTHORITY.json"
    value: dict[str, Any] = json.loads(read(path))
    adapter = value.setdefault("adapter_abi", {})
    candidate = adapter.setdefault("source_coverage_candidate", {})
    candidate["commit_fault_points"] = [
        "head-compare-and-swap",
        "command-receipt-insert",
        "event-append",
        "outbox-and-command-outbox-insert",
        "before-commit",
    ]
    candidate["fault_injection_rolls_back_every_internal_boundary"] = True
    candidate["postgresql_live_accepted"] = False
    candidate["cockroachdb_live_accepted"] = False
    candidate["independently_accepted"] = False
    boundary = value.setdefault("claim_boundary", {})
    boundary["durable_boundary_fault_source_candidate"] = True
    boundary["database_durable"] = False
    boundary["rollback_proven"] = False
    boundary["pitr_proven"] = False
    boundary["ha_proven"] = False
    boundary["production_ready"] = False
    write(path, json.dumps(value, indent=2, ensure_ascii=False))

    readme = root / "crates/trnm-persistence-pg/README.md"
    text = read(readme)
    section = textwrap.dedent(
        '''\

        ## Durable commit-boundary fault injection

        The `transaction-test-hooks` feature exposes five deterministic test-only mutation points inside the serializable command transaction: after entity-head CAS, after command-receipt insertion, after event append, after outbox/command-outbox insertion, and immediately before commit. A live regression injects a terminal error at each point, reconnects through a fresh PostgreSQL wire client, and requires the entity head plus every receipt/event/outbox table to show zero partial mutation.

        The feature is not enabled by production binaries. Passing source and live-profile tests creates a fault-evidence candidate only; profile-specific independent database review, HA/PITR, approved RPO/RTO, endurance and production promotion remain separate requirements.
        '''
    )
    if "## Durable commit-boundary fault injection" not in text:
        text += section
    write(readme, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    update_manifest(root)
    update_library(root)
    write(root / "crates/trnm-persistence-pg/src/fault.rs", fault_source())
    write(
        root / "crates/trnm-persistence-pg/tests/commit_fault_boundaries.rs",
        test_source(),
    )
    update_contracts(root)
    update_documents(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
