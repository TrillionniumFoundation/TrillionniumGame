#!/usr/bin/env python3
"""Complete PostgreSQL authority-lease and storage-object adapter source surfaces."""
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


def ensure_dependency(text: str, line: str, anchor: str) -> str:
    if line in text:
        return text
    require(anchor in text, f"dependency anchor missing: {anchor}")
    return text.replace(anchor, anchor + line + "\n", 1)


def update_manifest(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/Cargo.toml"
    text = read(path)
    text = ensure_dependency(
        text,
        'trnm-storage-core = { path = "../trnm-storage-core" }',
        'trnm-session-core = { path = "../trnm-session-core" }\n',
    )
    write(path, text)


def update_library(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/src/lib.rs"
    text = read(path)
    if "mod authority;" not in text:
        require("mod auth;\n" in text, "auth module anchor missing")
        text = text.replace("mod auth;\n", "mod auth;\nmod authority;\n", 1)
    if "mod storage;" not in text:
        require("mod session;\n" in text, "session module anchor missing")
        text = text.replace("mod session;\n", "mod session;\nmod storage;\n", 1)
    authority_export = "pub use authority::AuthorityLease;\n"
    if authority_export not in text:
        require("pub use auth::{\n" in text, "auth export anchor missing")
        text = text.replace("pub use auth::{\n", authority_export + "pub use auth::{\n", 1)
    storage_export = textwrap.dedent(
        '''\
        pub use trnm_storage_core::{
            Actor as StorageActor, BatchOperation as StorageBatchOperation, ContentVersion,
            DeleteOperation as StorageDeleteOperation, IntegrityDigest,
            MutationReceipt as StorageMutationReceipt, ReadPermission, StorageObject,
            StorageObjectKey, VersionCheck, WriteOperation as StorageWriteOperation,
            WritePermission,
        };
        '''
    )
    if "Actor as StorageActor" not in text:
        anchor = "pub use session::{\n    CreateSessionFamily, RefreshRotationOutcome, RefreshTokenCredential, RotateRefreshToken,\n    SessionFamilyRecord,\n};\n"
        require(anchor in text, "session export anchor missing")
        text = text.replace(anchor, anchor + storage_export, 1)
    write(path, text)


def authority_source() -> str:
    return textwrap.dedent(
        '''\
        use postgres::{IsolationLevel, Row};
        use trnm_contracts::{DomainError, RetryClass, StableCode};

        use super::{
            data_loss, decode_id16, error, from_i64, invalid, map_postgres_error, to_i64,
            EntityId, NodeId, PgRepository,
        };

        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub struct AuthorityLease {
            pub entity: EntityId,
            pub owner: NodeId,
            pub lease_generation: u64,
            pub authority_generation: u64,
            pub expires_at_ms: u64,
            pub updated_at_ms: u64,
        }

        impl PgRepository {
            pub fn load_authority_lease(
                &mut self,
                entity: EntityId,
            ) -> Result<Option<AuthorityLease>, DomainError> {
                if entity.is_zero() {
                    return Err(invalid("invalid_authority_entity"));
                }
                self.client
                    .query_opt(
                        "SELECT owner_node, lease_generation, authority_generation, \\
                         expires_at_ms, updated_at_ms FROM trnm_authority_leases \\
                         WHERE entity_id = $1",
                        &[&entity.as_bytes().as_slice()],
                    )
                    .map_err(map_postgres_error)?
                    .map(|row| decode_lease(entity, &row))
                    .transpose()
            }

            pub fn acquire_authority_lease(
                &mut self,
                entity: EntityId,
                owner: NodeId,
                expected_authority_generation: u64,
                now_ms: u64,
                expires_at_ms: u64,
            ) -> Result<AuthorityLease, DomainError> {
                validate_lease_request(
                    entity,
                    owner,
                    expected_authority_generation,
                    now_ms,
                    expires_at_ms,
                )?;
                let now_i64 = to_i64(now_ms)?;
                let expires_i64 = to_i64(expires_at_ms)?;
                let expected_authority_i64 = to_i64(expected_authority_generation)?;
                let mut transaction = self
                    .client
                    .build_transaction()
                    .isolation_level(IsolationLevel::Serializable)
                    .start()
                    .map_err(map_postgres_error)?;

                let head = transaction
                    .query_opt(
                        "SELECT authority_generation, updated_at_ms FROM trnm_entity_heads \\
                         WHERE entity_id = $1 FOR UPDATE",
                        &[&entity.as_bytes().as_slice()],
                    )
                    .map_err(map_postgres_error)?
                    .ok_or_else(|| {
                        error(
                            StableCode::NotFound,
                            "authority_entity_not_found",
                            RetryClass::Never,
                        )
                    })?;
                let current_authority =
                    from_i64(head.get(0), "negative_authority_generation")?;
                let head_updated_at = from_i64(head.get(1), "negative_head_updated_at")?;
                if current_authority != expected_authority_generation {
                    return Err(error(
                        StableCode::Aborted,
                        "authority_generation_mismatch",
                        RetryClass::ResyncRequired,
                    ));
                }
                if now_ms < head_updated_at {
                    return Err(invalid("authority_clock_regression"));
                }

                let existing = transaction
                    .query_opt(
                        "SELECT owner_node, lease_generation, authority_generation, \\
                         expires_at_ms, updated_at_ms FROM trnm_authority_leases \\
                         WHERE entity_id = $1 FOR UPDATE",
                        &[&entity.as_bytes().as_slice()],
                    )
                    .map_err(map_postgres_error)?
                    .map(|row| decode_lease(entity, &row))
                    .transpose()?;

                let lease = match existing {
                    None => {
                        transaction
                            .execute(
                                "INSERT INTO trnm_authority_leases \\
                                 (entity_id, owner_node, lease_generation, authority_generation, \\
                                  expires_at_ms, updated_at_ms) \\
                                 VALUES ($1, $2, 1, $3, $4, $5)",
                                &[
                                    &entity.as_bytes().as_slice(),
                                    &owner.as_bytes().as_slice(),
                                    &expected_authority_i64,
                                    &expires_i64,
                                    &now_i64,
                                ],
                            )
                            .map_err(map_postgres_error)?;
                        AuthorityLease {
                            entity,
                            owner,
                            lease_generation: 1,
                            authority_generation: expected_authority_generation,
                            expires_at_ms,
                            updated_at_ms: now_ms,
                        }
                    }
                    Some(previous) => {
                        if previous.authority_generation != current_authority {
                            return Err(data_loss("authority_lease_head_generation_mismatch"));
                        }
                        if now_ms < previous.updated_at_ms {
                            return Err(invalid("authority_clock_regression"));
                        }
                        if previous.expires_at_ms > now_ms {
                            return Err(error(
                                StableCode::Aborted,
                                "authority_lease_held",
                                RetryClass::SafeBackoff,
                            ));
                        }
                        let next_lease_generation = previous
                            .lease_generation
                            .checked_add(1)
                            .ok_or_else(authority_counter_overflow)?;
                        let next_authority_generation = current_authority
                            .checked_add(1)
                            .ok_or_else(authority_counter_overflow)?;
                        let next_lease_i64 = to_i64(next_lease_generation)?;
                        let next_authority_i64 = to_i64(next_authority_generation)?;
                        let updated_head = transaction
                            .execute(
                                "UPDATE trnm_entity_heads \\
                                 SET authority_generation = $2, updated_at_ms = $3 \\
                                 WHERE entity_id = $1 AND authority_generation = $4",
                                &[
                                    &entity.as_bytes().as_slice(),
                                    &next_authority_i64,
                                    &now_i64,
                                    &expected_authority_i64,
                                ],
                            )
                            .map_err(map_postgres_error)?;
                        if updated_head != 1 {
                            return Err(error(
                                StableCode::Aborted,
                                "authority_head_compare_and_swap_failed",
                                RetryClass::ResyncRequired,
                            ));
                        }
                        let previous_lease_i64 = to_i64(previous.lease_generation)?;
                        let updated_lease = transaction
                            .execute(
                                "UPDATE trnm_authority_leases \\
                                 SET owner_node = $2, lease_generation = $3, \\
                                     authority_generation = $4, expires_at_ms = $5, \\
                                     updated_at_ms = $6 \\
                                 WHERE entity_id = $1 AND lease_generation = $7 \\
                                   AND authority_generation = $8",
                                &[
                                    &entity.as_bytes().as_slice(),
                                    &owner.as_bytes().as_slice(),
                                    &next_lease_i64,
                                    &next_authority_i64,
                                    &expires_i64,
                                    &now_i64,
                                    &previous_lease_i64,
                                    &expected_authority_i64,
                                ],
                            )
                            .map_err(map_postgres_error)?;
                        if updated_lease != 1 {
                            return Err(error(
                                StableCode::Aborted,
                                "authority_lease_compare_and_swap_failed",
                                RetryClass::ResyncRequired,
                            ));
                        }
                        AuthorityLease {
                            entity,
                            owner,
                            lease_generation: next_lease_generation,
                            authority_generation: next_authority_generation,
                            expires_at_ms,
                            updated_at_ms: now_ms,
                        }
                    }
                };
                transaction.commit().map_err(map_postgres_error)?;
                Ok(lease)
            }

            pub fn renew_authority_lease(
                &mut self,
                lease: AuthorityLease,
                now_ms: u64,
                expires_at_ms: u64,
            ) -> Result<AuthorityLease, DomainError> {
                validate_lease_request(
                    lease.entity,
                    lease.owner,
                    lease.authority_generation,
                    now_ms,
                    expires_at_ms,
                )?;
                if lease.lease_generation == 0 {
                    return Err(invalid("invalid_authority_lease_generation"));
                }
                let now_i64 = to_i64(now_ms)?;
                let expires_i64 = to_i64(expires_at_ms)?;
                let lease_generation_i64 = to_i64(lease.lease_generation)?;
                let authority_generation_i64 = to_i64(lease.authority_generation)?;
                let mut transaction = self
                    .client
                    .build_transaction()
                    .isolation_level(IsolationLevel::Serializable)
                    .start()
                    .map_err(map_postgres_error)?;
                let current = transaction
                    .query_opt(
                        "SELECT owner_node, lease_generation, authority_generation, \\
                         expires_at_ms, updated_at_ms FROM trnm_authority_leases \\
                         WHERE entity_id = $1 FOR UPDATE",
                        &[&lease.entity.as_bytes().as_slice()],
                    )
                    .map_err(map_postgres_error)?
                    .map(|row| decode_lease(lease.entity, &row))
                    .transpose()?
                    .ok_or_else(|| {
                        error(
                            StableCode::NotFound,
                            "authority_lease_not_found",
                            RetryClass::Never,
                        )
                    })?;
                if current.owner != lease.owner
                    || current.lease_generation != lease.lease_generation
                    || current.authority_generation != lease.authority_generation
                {
                    return Err(error(
                        StableCode::Aborted,
                        "stale_authority_lease",
                        RetryClass::ResyncRequired,
                    ));
                }
                if current.expires_at_ms <= now_ms {
                    return Err(error(
                        StableCode::Aborted,
                        "authority_lease_expired",
                        RetryClass::ResyncRequired,
                    ));
                }
                if now_ms < current.updated_at_ms {
                    return Err(invalid("authority_clock_regression"));
                }
                let head_generation: i64 = transaction
                    .query_one(
                        "SELECT authority_generation FROM trnm_entity_heads \\
                         WHERE entity_id = $1 FOR UPDATE",
                        &[&lease.entity.as_bytes().as_slice()],
                    )
                    .map_err(map_postgres_error)?
                    .get(0);
                if from_i64(head_generation, "negative_authority_generation")?
                    != lease.authority_generation
                {
                    return Err(data_loss("authority_lease_head_generation_mismatch"));
                }
                let updated = transaction
                    .execute(
                        "UPDATE trnm_authority_leases \\
                         SET expires_at_ms = $5, updated_at_ms = $6 \\
                         WHERE entity_id = $1 AND owner_node = $2 \\
                           AND lease_generation = $3 AND authority_generation = $4",
                        &[
                            &lease.entity.as_bytes().as_slice(),
                            &lease.owner.as_bytes().as_slice(),
                            &lease_generation_i64,
                            &authority_generation_i64,
                            &expires_i64,
                            &now_i64,
                        ],
                    )
                    .map_err(map_postgres_error)?;
                if updated != 1 {
                    return Err(error(
                        StableCode::Aborted,
                        "authority_lease_compare_and_swap_failed",
                        RetryClass::ResyncRequired,
                    ));
                }
                transaction.commit().map_err(map_postgres_error)?;
                Ok(AuthorityLease {
                    expires_at_ms,
                    updated_at_ms: now_ms,
                    ..lease
                })
            }

            pub fn release_authority_lease(
                &mut self,
                lease: AuthorityLease,
                released_at_ms: u64,
            ) -> Result<AuthorityLease, DomainError> {
                if released_at_ms < lease.updated_at_ms {
                    return Err(invalid("authority_clock_regression"));
                }
                let released_i64 = to_i64(released_at_ms)?;
                let lease_generation_i64 = to_i64(lease.lease_generation)?;
                let authority_generation_i64 = to_i64(lease.authority_generation)?;
                let updated = self
                    .client
                    .execute(
                        "UPDATE trnm_authority_leases \\
                         SET expires_at_ms = $5, updated_at_ms = $5 \\
                         WHERE entity_id = $1 AND owner_node = $2 \\
                           AND lease_generation = $3 AND authority_generation = $4 \\
                           AND expires_at_ms > $5",
                        &[
                            &lease.entity.as_bytes().as_slice(),
                            &lease.owner.as_bytes().as_slice(),
                            &lease_generation_i64,
                            &authority_generation_i64,
                            &released_i64,
                        ],
                    )
                    .map_err(map_postgres_error)?;
                if updated != 1 {
                    return Err(error(
                        StableCode::Aborted,
                        "stale_or_expired_authority_lease",
                        RetryClass::ResyncRequired,
                    ));
                }
                Ok(AuthorityLease {
                    expires_at_ms: released_at_ms,
                    updated_at_ms: released_at_ms,
                    ..lease
                })
            }
        }

        fn validate_lease_request(
            entity: EntityId,
            owner: NodeId,
            authority_generation: u64,
            now_ms: u64,
            expires_at_ms: u64,
        ) -> Result<(), DomainError> {
            if entity.is_zero()
                || owner.is_zero()
                || authority_generation == 0
                || expires_at_ms <= now_ms
            {
                return Err(invalid("invalid_authority_lease_request"));
            }
            to_i64(authority_generation)?;
            to_i64(now_ms)?;
            to_i64(expires_at_ms)?;
            Ok(())
        }

        fn decode_lease(entity: EntityId, row: &Row) -> Result<AuthorityLease, DomainError> {
            Ok(AuthorityLease {
                entity,
                owner: decode_id16(row.get(0), NodeId::new, "invalid_authority_owner_bytes")?,
                lease_generation: from_i64(row.get(1), "negative_authority_lease_generation")?,
                authority_generation: from_i64(row.get(2), "negative_authority_generation")?,
                expires_at_ms: from_i64(row.get(3), "negative_authority_expiration")?,
                updated_at_ms: from_i64(row.get(4), "negative_authority_updated_at")?,
            })
        }

        const fn authority_counter_overflow() -> DomainError {
            error(
                StableCode::OutOfRange,
                "authority_generation_overflow",
                RetryClass::Never,
            )
        }

        #[cfg(test)]
        mod tests {
            use super::*;

            #[test]
            fn lease_request_validation_is_fail_closed() {
                let entity = EntityId::new([1; 16]);
                let owner = NodeId::new([2; 16]);
                assert_eq!(
                    validate_lease_request(entity, owner, 1, 10, 10)
                        .unwrap_err()
                        .reason(),
                    "invalid_authority_lease_request"
                );
                assert_eq!(
                    validate_lease_request(EntityId::new([0; 16]), owner, 1, 10, 20)
                        .unwrap_err()
                        .reason(),
                    "invalid_authority_lease_request"
                );
                assert_eq!(
                    validate_lease_request(entity, owner, 1, u64::MAX, u64::MAX)
                        .unwrap_err()
                        .reason(),
                    "invalid_authority_lease_request"
                );
            }

            #[test]
            fn authority_generation_overflow_is_terminal() {
                assert_eq!(
                    authority_counter_overflow().code(),
                    StableCode::OutOfRange
                );
                assert_eq!(
                    authority_counter_overflow().retry(),
                    RetryClass::Never
                );
            }
        }
        '''
    )


def storage_source() -> str:
    return textwrap.dedent(
        '''\
        use std::collections::{BTreeMap, BTreeSet};

        use postgres::{IsolationLevel, Row, Transaction};
        use trnm_contracts::{DomainError, RetryClass, StableCode, UserId};
        use trnm_storage_core::{
            Actor, BatchOperation, ContentVersion, DeleteOperation, IntegrityDigest,
            MutationReceipt, ReadPermission, StorageObject, StorageObjectKey, VersionCheck,
            WriteOperation, WritePermission,
        };

        use super::{
            data_loss, decode_digest, decode_id16, error, invalid, map_postgres_error, to_i64,
            PgRepository,
        };

        const MAX_BATCH_OPERATIONS: usize = 100;
        const MAX_VALUE_BYTES: usize = 1024 * 1024;

        impl PgRepository {
            pub fn read_storage_object(
                &mut self,
                actor: Actor,
                key: &StorageObjectKey,
            ) -> Result<StorageObject, DomainError> {
                let row = self
                    .client
                    .query_opt(
                        "SELECT value_bytes, version_digest, read_permission, write_permission \\
                         FROM trnm_storage_objects \\
                         WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                        &[
                            &key.collection(),
                            &key.key(),
                            &key.user_id().as_bytes().as_slice(),
                        ],
                    )
                    .map_err(map_postgres_error)?
                    .ok_or_else(storage_not_found)?;
                let object = decode_storage_object(key.clone(), &row)?;
                authorize_read(actor, &object)?;
                Ok(object)
            }

            pub fn apply_storage_batch(
                &mut self,
                actor: Actor,
                operations: &[BatchOperation],
                updated_at_ms: u64,
            ) -> Result<Vec<MutationReceipt>, DomainError> {
                validate_batch(operations)?;
                let updated_at_i64 = to_i64(updated_at_ms)?;
                let mut transaction = self
                    .client
                    .build_transaction()
                    .isolation_level(IsolationLevel::Serializable)
                    .start()
                    .map_err(map_postgres_error)?;

                let mut staged = BTreeMap::new();
                for key in sorted_keys(operations) {
                    let object = load_for_update(&mut transaction, &key)?;
                    staged.insert(key, object);
                }

                let mut receipts = Vec::with_capacity(operations.len());
                for operation in operations {
                    let receipt = match operation {
                        BatchOperation::Write(write) => apply_write(
                            &mut transaction,
                            &mut staged,
                            actor,
                            write,
                            updated_at_i64,
                        )?,
                        BatchOperation::Delete(delete) => {
                            apply_delete(&mut transaction, &mut staged, actor, delete)?
                        }
                    };
                    receipts.push(receipt);
                }
                transaction.commit().map_err(map_postgres_error)?;
                Ok(receipts)
            }
        }

        fn sorted_keys(operations: &[BatchOperation]) -> BTreeSet<StorageObjectKey> {
            operations
                .iter()
                .map(|operation| operation.key().clone())
                .collect()
        }

        fn load_for_update(
            transaction: &mut Transaction<'_>,
            key: &StorageObjectKey,
        ) -> Result<Option<StorageObject>, DomainError> {
            transaction
                .query_opt(
                    "SELECT value_bytes, version_digest, read_permission, write_permission \\
                     FROM trnm_storage_objects \\
                     WHERE collection = $1 AND object_key = $2 AND user_id = $3 \\
                     FOR UPDATE",
                    &[
                        &key.collection(),
                        &key.key(),
                        &key.user_id().as_bytes().as_slice(),
                    ],
                )
                .map_err(map_postgres_error)?
                .map(|row| decode_storage_object(key.clone(), &row))
                .transpose()
        }

        fn apply_write(
            transaction: &mut Transaction<'_>,
            staged: &mut BTreeMap<StorageObjectKey, Option<StorageObject>>,
            actor: Actor,
            operation: &WriteOperation,
            updated_at_ms: i64,
        ) -> Result<MutationReceipt, DomainError> {
            if operation.value.len() > MAX_VALUE_BYTES {
                return Err(invalid("invalid_storage_value"));
            }
            let previous = staged
                .get(&operation.key)
                .cloned()
                .ok_or_else(|| data_loss("storage_batch_lock_missing"))?;
            authorize_write(actor, &operation.key, previous.as_ref())?;
            validate_version(previous.as_ref(), operation.expected)?;

            let version = ContentVersion::from_value(&operation.value);
            if let Some(existing) = previous.as_ref() {
                if existing.version == version
                    && (existing.value != operation.value
                        || existing.integrity_digest != operation.integrity_digest)
                {
                    return Err(data_loss(
                        "storage_public_version_collision_or_integrity_mismatch",
                    ));
                }
            }
            let integrity = operation.integrity_digest.get();
            let read_permission = operation.read_permission as i16;
            let write_permission = operation.write_permission as i16;
            let affected = if previous.is_some() {
                transaction
                    .execute(
                        "UPDATE trnm_storage_objects \\
                         SET value_bytes = $4, version_digest = $5, read_permission = $6, \\
                             write_permission = $7, updated_at_ms = $8 \\
                         WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                        &[
                            &operation.key.collection(),
                            &operation.key.key(),
                            &operation.key.user_id().as_bytes().as_slice(),
                            &operation.value.as_slice(),
                            &integrity.as_bytes().as_slice(),
                            &read_permission,
                            &write_permission,
                            &updated_at_ms,
                        ],
                    )
                    .map_err(map_postgres_error)?
            } else {
                transaction
                    .execute(
                        "INSERT INTO trnm_storage_objects \\
                         (collection, object_key, user_id, value_bytes, version_digest, \\
                          read_permission, write_permission, updated_at_ms) \\
                         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                        &[
                            &operation.key.collection(),
                            &operation.key.key(),
                            &operation.key.user_id().as_bytes().as_slice(),
                            &operation.value.as_slice(),
                            &integrity.as_bytes().as_slice(),
                            &read_permission,
                            &write_permission,
                            &updated_at_ms,
                        ],
                    )
                    .map_err(map_postgres_error)?
            };
            if affected != 1 {
                return Err(data_loss("storage_write_row_count_mismatch"));
            }
            let next = StorageObject {
                key: operation.key.clone(),
                value: operation.value.clone(),
                version,
                integrity_digest: operation.integrity_digest,
                read_permission: operation.read_permission,
                write_permission: operation.write_permission,
            };
            staged.insert(operation.key.clone(), Some(next));
            Ok(MutationReceipt {
                key: operation.key.clone(),
                previous_version: previous.map(|object| object.version),
                current_version: Some(version),
            })
        }

        fn apply_delete(
            transaction: &mut Transaction<'_>,
            staged: &mut BTreeMap<StorageObjectKey, Option<StorageObject>>,
            actor: Actor,
            operation: &DeleteOperation,
        ) -> Result<MutationReceipt, DomainError> {
            let previous = staged
                .get(&operation.key)
                .cloned()
                .ok_or_else(|| data_loss("storage_batch_lock_missing"))?
                .ok_or_else(storage_not_found)?;
            authorize_write(actor, &operation.key, Some(&previous))?;
            if operation
                .expected_version
                .is_some_and(|expected| expected != previous.version)
            {
                return Err(version_error());
            }
            let deleted = transaction
                .execute(
                    "DELETE FROM trnm_storage_objects \\
                     WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                    &[
                        &operation.key.collection(),
                        &operation.key.key(),
                        &operation.key.user_id().as_bytes().as_slice(),
                    ],
                )
                .map_err(map_postgres_error)?;
            if deleted != 1 {
                return Err(data_loss("storage_delete_row_count_mismatch"));
            }
            staged.insert(operation.key.clone(), None);
            Ok(MutationReceipt {
                key: operation.key.clone(),
                previous_version: Some(previous.version),
                current_version: None,
            })
        }

        fn validate_batch(operations: &[BatchOperation]) -> Result<(), DomainError> {
            if operations.is_empty() || operations.len() > MAX_BATCH_OPERATIONS {
                return Err(invalid("invalid_storage_batch_size"));
            }
            let mut keys = BTreeSet::new();
            for operation in operations {
                if !keys.insert(operation.key()) {
                    return Err(invalid("duplicate_storage_key_in_batch"));
                }
            }
            Ok(())
        }

        fn validate_version(
            existing: Option<&StorageObject>,
            check: VersionCheck,
        ) -> Result<(), DomainError> {
            match check {
                VersionCheck::Any => Ok(()),
                VersionCheck::MustNotExist if existing.is_none() => Ok(()),
                VersionCheck::MustNotExist => Err(error(
                    StableCode::AlreadyExists,
                    "storage_object_already_exists",
                    RetryClass::Never,
                )),
                VersionCheck::Exact(expected) => match existing {
                    Some(object) if object.version == expected => Ok(()),
                    _ => Err(version_error()),
                },
            }
        }

        fn authorize_read(actor: Actor, object: &StorageObject) -> Result<(), DomainError> {
            let allowed = match actor {
                Actor::Server => true,
                Actor::User(user) => {
                    object.read_permission == ReadPermission::Public
                        || (user == object.key.user_id()
                            && object.read_permission == ReadPermission::Owner)
                }
            };
            if allowed {
                Ok(())
            } else {
                Err(error(
                    StableCode::PermissionDenied,
                    "storage_read_permission_denied",
                    RetryClass::Never,
                ))
            }
        }

        fn authorize_write(
            actor: Actor,
            key: &StorageObjectKey,
            existing: Option<&StorageObject>,
        ) -> Result<(), DomainError> {
            match actor {
                Actor::Server => Ok(()),
                Actor::User(user) => {
                    if user.is_zero() || user != key.user_id() {
                        return Err(write_permission_error());
                    }
                    if existing
                        .is_some_and(|object| object.write_permission != WritePermission::Owner)
                    {
                        return Err(write_permission_error());
                    }
                    Ok(())
                }
            }
        }

        fn decode_storage_object(
            key: StorageObjectKey,
            row: &Row,
        ) -> Result<StorageObject, DomainError> {
            let value: Vec<u8> = row.get(0);
            if value.len() > MAX_VALUE_BYTES {
                return Err(data_loss("invalid_storage_value_bytes"));
            }
            let integrity_digest = IntegrityDigest::new(decode_digest(
                row.get(1),
                "invalid_storage_integrity_digest",
            )?)
            .map_err(|_| data_loss("invalid_storage_integrity_digest"))?;
            let read_permission = match row.get::<_, i16>(2) {
                0 => ReadPermission::None,
                1 => ReadPermission::Owner,
                2 => ReadPermission::Public,
                _ => return Err(data_loss("invalid_storage_read_permission")),
            };
            let write_permission = match row.get::<_, i16>(3) {
                0 => WritePermission::None,
                1 => WritePermission::Owner,
                _ => return Err(data_loss("invalid_storage_write_permission")),
            };
            Ok(StorageObject {
                key,
                version: ContentVersion::from_value(&value),
                value,
                integrity_digest,
                read_permission,
                write_permission,
            })
        }

        fn decode_storage_key(
            collection: String,
            object_key: String,
            user_bytes: Vec<u8>,
        ) -> Result<StorageObjectKey, DomainError> {
            let user = decode_id16(user_bytes, UserId::new, "invalid_storage_user_id")?;
            StorageObjectKey::new(collection, object_key, user)
                .map_err(|_| data_loss("invalid_storage_key_material"))
        }

        const fn storage_not_found() -> DomainError {
            error(
                StableCode::NotFound,
                "storage_object_not_found",
                RetryClass::Never,
            )
        }

        const fn version_error() -> DomainError {
            error(
                StableCode::FailedPrecondition,
                "storage_version_mismatch",
                RetryClass::ResyncRequired,
            )
        }

        const fn write_permission_error() -> DomainError {
            error(
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
                RetryClass::Never,
            )
        }

        #[cfg(test)]
        mod tests {
            use super::*;
            use trnm_contracts::Digest32;

            fn key(value: u8) -> StorageObjectKey {
                StorageObjectKey::new("profile", format!("object-{value}"), UserId::new([value; 16]))
                    .unwrap()
            }

            fn write(value: u8) -> BatchOperation {
                BatchOperation::Write(WriteOperation {
                    key: key(value),
                    value: vec![value],
                    integrity_digest: IntegrityDigest::new(Digest32::new([value; 32])).unwrap(),
                    expected: VersionCheck::Any,
                    read_permission: ReadPermission::Owner,
                    write_permission: WritePermission::Owner,
                })
            }

            #[test]
            fn batch_validation_rejects_empty_duplicate_and_excess() {
                assert_eq!(
                    validate_batch(&[]).unwrap_err().reason(),
                    "invalid_storage_batch_size"
                );
                let duplicate = vec![write(1), write(1)];
                assert_eq!(
                    validate_batch(&duplicate).unwrap_err().reason(),
                    "duplicate_storage_key_in_batch"
                );
                let excess = (0..=MAX_BATCH_OPERATIONS)
                    .map(|index| write(u8::try_from(index + 1).unwrap()))
                    .collect::<Vec<_>>();
                assert_eq!(
                    validate_batch(&excess).unwrap_err().reason(),
                    "invalid_storage_batch_size"
                );
            }

            #[test]
            fn permission_and_version_contract_matches_storage_core() {
                let owner = UserId::new([1; 16]);
                let object = StorageObject {
                    key: key(1),
                    value: b"v1".to_vec(),
                    version: ContentVersion::from_value(b"v1"),
                    integrity_digest: IntegrityDigest::new(Digest32::new([1; 32])).unwrap(),
                    read_permission: ReadPermission::Owner,
                    write_permission: WritePermission::Owner,
                };
                authorize_read(Actor::User(owner), &object).unwrap();
                assert_eq!(
                    authorize_read(Actor::User(UserId::new([2; 16])), &object)
                        .unwrap_err()
                        .reason(),
                    "storage_read_permission_denied"
                );
                validate_version(Some(&object), VersionCheck::Exact(object.version)).unwrap();
                assert_eq!(
                    validate_version(
                        Some(&object),
                        VersionCheck::Exact(ContentVersion::from_value(b"other")),
                    )
                    .unwrap_err()
                    .reason(),
                    "storage_version_mismatch"
                );
            }

            #[test]
            fn database_permission_decoders_are_total() {
                assert_eq!(
                    decode_storage_key("a".to_owned(), "b".to_owned(), vec![1; 16])
                        .unwrap()
                        .user_id(),
                    UserId::new([1; 16])
                );
            }
        }
        '''
    )


def integration_test_source() -> str:
    return textwrap.dedent(
        '''\
        use std::env;

        use trnm_contracts::{CommandId, Digest32, StableCode, UserId};
        use trnm_persistence_pg::{
            CommitRequest, DatabaseProfile, EntityId, NodeId, PgRepository, StorageActor,
            StorageBatchOperation, StorageDeleteOperation, StorageObjectKey, StorageWriteOperation,
            IntegrityDigest, ReadPermission, VersionCheck, WritePermission,
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

        #[test]
        fn authority_takeover_fences_stale_generation() {
            let Some((database_url, profile)) =
                live_database_environment("authority lease contract")
            else {
                return;
            };
            let entity = EntityId::new([0x71; 16]);
            let owner_a = NodeId::new([0x72; 16]);
            let owner_b = NodeId::new([0x73; 16]);
            let mut repository = PgRepository::connect(&database_url, profile).unwrap();
            repository
                .bootstrap_entity(entity, 1, digest(0x74), 1)
                .unwrap();
            let lease_a = repository
                .acquire_authority_lease(entity, owner_a, 1, 10, 20)
                .unwrap();
            assert_eq!(lease_a.lease_generation, 1);
            assert_eq!(lease_a.authority_generation, 1);
            let held = repository
                .acquire_authority_lease(entity, owner_b, 1, 15, 30)
                .unwrap_err();
            assert_eq!(held.code(), StableCode::Aborted);
            assert_eq!(held.reason(), "authority_lease_held");
            let renewed = repository
                .renew_authority_lease(lease_a, 15, 25)
                .unwrap();
            assert_eq!(renewed.lease_generation, 1);
            let lease_b = repository
                .acquire_authority_lease(entity, owner_b, 1, 25, 40)
                .unwrap();
            assert_eq!(lease_b.lease_generation, 2);
            assert_eq!(lease_b.authority_generation, 2);
            let head = repository.load_head(entity).unwrap().unwrap();
            assert_eq!(head.authority_generation, 2);

            let stale = CommitRequest {
                entity,
                command: CommandId::new([0x75; 16]),
                fingerprint: digest(0x76),
                expected_revision: 0,
                authority_generation: 1,
                next_state: digest(0x77),
                committed_at_ms: 30,
                events: vec![],
                outbox: vec![],
            };
            assert_eq!(
                repository.commit_command(&stale).unwrap_err().reason(),
                "authority_generation_mismatch"
            );
        }

        #[test]
        fn storage_occ_acl_and_batch_rollback_are_transactional() {
            let Some((database_url, profile)) =
                live_database_environment("storage object contract")
            else {
                return;
            };
            let user = UserId::new([0x81; 16]);
            let other = UserId::new([0x82; 16]);
            let key = StorageObjectKey::new("profile", "ac3-object", user).unwrap();
            let mut repository = PgRepository::connect(&database_url, profile).unwrap();
            let created = repository
                .apply_storage_batch(
                    StorageActor::User(user),
                    &[StorageBatchOperation::Write(StorageWriteOperation {
                        key: key.clone(),
                        value: b"v1".to_vec(),
                        integrity_digest: IntegrityDigest::new(digest(0x83)).unwrap(),
                        expected: VersionCheck::MustNotExist,
                        read_permission: ReadPermission::Owner,
                        write_permission: WritePermission::Owner,
                    })],
                    10,
                )
                .unwrap();
            let version = created[0].current_version.unwrap();
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::User(user), &key)
                    .unwrap()
                    .value,
                b"v1"
            );
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::User(other), &key)
                    .unwrap_err()
                    .reason(),
                "storage_read_permission_denied"
            );

            repository
                .apply_storage_batch(
                    StorageActor::User(user),
                    &[StorageBatchOperation::Write(StorageWriteOperation {
                        key: key.clone(),
                        value: b"v2".to_vec(),
                        integrity_digest: IntegrityDigest::new(digest(0x84)).unwrap(),
                        expected: VersionCheck::Exact(version),
                        read_permission: ReadPermission::Public,
                        write_permission: WritePermission::Owner,
                    })],
                    20,
                )
                .unwrap();
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::User(other), &key)
                    .unwrap()
                    .value,
                b"v2"
            );

            let stale = repository
                .apply_storage_batch(
                    StorageActor::User(user),
                    &[StorageBatchOperation::Write(StorageWriteOperation {
                        key: key.clone(),
                        value: b"v3".to_vec(),
                        integrity_digest: IntegrityDigest::new(digest(0x85)).unwrap(),
                        expected: VersionCheck::Exact(version),
                        read_permission: ReadPermission::Owner,
                        write_permission: WritePermission::Owner,
                    })],
                    30,
                )
                .unwrap_err();
            assert_eq!(stale.reason(), "storage_version_mismatch");
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &key)
                    .unwrap()
                    .value,
                b"v2"
            );

            let current = repository
                .read_storage_object(StorageActor::Server, &key)
                .unwrap()
                .version;
            repository
                .apply_storage_batch(
                    StorageActor::User(user),
                    &[StorageBatchOperation::Delete(StorageDeleteOperation {
                        key: key.clone(),
                        expected_version: Some(current),
                    })],
                    40,
                )
                .unwrap();
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &key)
                    .unwrap_err()
                    .code(),
                StableCode::NotFound
            );
        }
        '''
    )


def install_sources(root: Path) -> None:
    write(root / "crates/trnm-persistence-pg/src/authority.rs", authority_source())
    write(root / "crates/trnm-persistence-pg/src/storage.rs", storage_source())
    write(
        root / "crates/trnm-persistence-pg/tests/authority_storage.rs",
        integration_test_source(),
    )


def update_schema_checker(root: Path) -> None:
    path = root / "scripts/check-schema-authority.py"
    text = read(path)
    old = '''    adapter = (ROOT / "crates/trnm-persistence-pg/src/lib.rs").read_text(encoding="utf-8")
    missing_adapter = sorted(table for table in REQUIRED_TABLES if table not in adapter)
'''
    new = '''    adapter_root = ROOT / "crates/trnm-persistence-pg/src"
    adapter = "\\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(adapter_root.rglob("*.rs"))
    )
    missing_adapter = sorted(table for table in REQUIRED_TABLES if table not in adapter)
'''
    if new not in text:
        require(old in text, "schema checker adapter anchor missing")
        text = text.replace(old, new, 1)
    old_note = '''    if missing_adapter:
        print(
            "schema authority note: adapter does not yet access all authoritative tables: "
            + ", ".join(missing_adapter)
        )
'''
    new_note = '''    require(
        not missing_adapter,
        "adapter missing authoritative table references " + ", ".join(missing_adapter),
    )
'''
    if new_note not in text:
        require(old_note in text, "schema checker missing-table note anchor missing")
        text = text.replace(old_note, new_note, 1)
    write(path, text)


def update_authority_document(root: Path) -> None:
    path = root / "docs/development/SCHEMA_AUTHORITY.json"
    value: dict[str, Any] = json.loads(read(path))
    adapter = value.setdefault("adapter_abi", {})
    adapter["source_modules"] = [
        "crates/trnm-persistence-pg/src/lib.rs",
        "crates/trnm-persistence-pg/src/authority.rs",
        "crates/trnm-persistence-pg/src/session.rs",
        "crates/trnm-persistence-pg/src/storage.rs",
        "crates/trnm-persistence-pg/src/outbox.rs",
    ]
    adapter["source_coverage_candidate"] = {
        "all_required_tables_referenced": True,
        "authority_lease_takeover_fences_entity_generation": True,
        "session_family_and_refresh_rotation_transactional": True,
        "storage_occ_acl_batch_transactional": True,
        "independently_accepted": False,
    }
    boundary = value.setdefault("claim_boundary", {})
    boundary["adapter_table_surface_source_candidate"] = True
    boundary["database_durable"] = False
    boundary["migration_compatible"] = False
    boundary["rollback_proven"] = False
    boundary["pitr_proven"] = False
    boundary["ha_proven"] = False
    boundary["production_ready"] = False
    write(path, json.dumps(value, indent=2, ensure_ascii=False))


def update_readme(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/README.md"
    text = read(path)
    section = textwrap.dedent(
        '''\

        ## Authority lease and storage adapters

        The adapter owns typed SQL access to all ten authoritative tables. Authority lease acquisition locks the entity head and lease in a serializable transaction; an expired-owner takeover advances both lease and authority generations before a new owner is returned. Renewal and release require the exact entity, owner, lease generation and authority generation.

        Storage reads and mutation batches use `trnm-storage-core` public version, integrity, OCC and ACL types. Batch keys are locked in deterministic order and every write/delete commits in one serializable transaction. The public Nakama-compatible content version is recomputed from exact value bytes while the schema stores the separate internal integrity digest.

        These paths are source candidates. PostgreSQL/CockroachDB live execution, exact-head evidence admission, profile-specific failover/restore and independent database/storage review remain required before production credit.
        '''
    )
    if "## Authority lease and storage adapters" not in text:
        text += section
    write(path, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    update_manifest(root)
    update_library(root)
    install_sources(root)
    update_schema_checker(root)
    update_authority_document(root)
    update_readme(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
