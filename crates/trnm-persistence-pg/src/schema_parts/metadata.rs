#[derive(Clone, Debug, Eq, PartialEq)]
struct RecordedMetadata {
    version: i64,
    profile: String,
    source_commit: String,
    applied_at_ms: i64,
    chain_digest: Option<String>,
    digest_algorithm: Option<String>,
    storage_writer_epoch: Option<i64>,
    upgrade_source_commit: Option<String>,
    v2_apply_source_commit: Option<String>,
    v3_apply_source_commit: Option<String>,
    v4_apply_source_commit: Option<String>,
}

fn validate_source_commit(value: &str) -> Result<(), DomainError> {
    if value.len() != 40 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(invalid("invalid_schema_source_commit"));
    }
    Ok(())
}

fn digest_hex(digest: IntegrityDigest) -> String {
    use std::fmt::Write as _;
    let mut result = String::with_capacity(64);
    for byte in digest.get().as_bytes() {
        write!(result, "{byte:02x}").expect("String write");
    }
    result
}

fn read_metadata(
    client: &mut impl GenericClient,
    catalog: &Catalog,
) -> Result<Option<RecordedMetadata>, DomainError> {
    let optional = |name: &str, kind: &str| {
        if catalog.contains_key(&("trnm_schema_metadata".to_owned(), name.to_owned())) {
            name.to_owned()
        } else {
            format!("NULL::{kind}")
        }
    };
    let rows = client
        .query(
            &format!(
                "SELECT singleton, schema_version, profile, source_commit, applied_at_ms, {}, {}, {}, {}, {}, {}, {} \
                 FROM public.trnm_schema_metadata LIMIT 2",
                optional("chain_digest", "text"),
                optional("digest_algorithm", "text"),
                optional("storage_writer_epoch", "bigint"),
                optional("upgrade_source_commit", "text"),
                optional("v2_apply_source_commit", "text"),
                optional("v3_apply_source_commit", "text"),
                optional("v4_apply_source_commit", "text")
            ),
            &[],
        )
        .map_err(map_postgres_error)?;
    if rows.len() > 1 {
        return Err(data_loss("schema_metadata_cardinality_invalid"));
    }
    let Some(row) = rows.first() else {
        return Ok(None);
    };
    if row.try_get::<_, i16>(0).map_err(map_postgres_error)? != 1 {
        return Err(data_loss("schema_metadata_singleton_invalid"));
    }
    Ok(Some(RecordedMetadata {
        version: row.try_get(1).map_err(map_postgres_error)?,
        profile: row.try_get(2).map_err(map_postgres_error)?,
        source_commit: row.try_get(3).map_err(map_postgres_error)?,
        applied_at_ms: row.try_get(4).map_err(map_postgres_error)?,
        chain_digest: row.try_get(5).map_err(map_postgres_error)?,
        digest_algorithm: row.try_get(6).map_err(map_postgres_error)?,
        storage_writer_epoch: row.try_get(7).map_err(map_postgres_error)?,
        upgrade_source_commit: row.try_get(8).map_err(map_postgres_error)?,
        v2_apply_source_commit: row.try_get(9).map_err(map_postgres_error)?,
        v3_apply_source_commit: row.try_get(10).map_err(map_postgres_error)?,
        v4_apply_source_commit: row.try_get(11).map_err(map_postgres_error)?,
    }))
}

/// Validate the identity recorded for this revision, not the newest binary's
/// full-chain digest. Foundation provenance is retained across transitions;
/// upgrade provenance identifies the process that published that revision.
fn validate_recorded_revision(
    recorded: &RecordedMetadata,
    profile: DatabaseProfile,
) -> Result<&'static RevisionDescriptor, DomainError> {
    if recorded.profile != profile.metadata_value()
        || recorded.applied_at_ms < 0
        || validate_source_commit(&recorded.source_commit).is_err()
    {
        return Err(failed_precondition("schema_metadata_provenance_invalid"));
    }
    let descriptor = u64::try_from(recorded.version)
        .ok()
        .filter(|version| *version <= AUTHORITATIVE_SUPPORTED_SCHEMA_VERSION)
        .and_then(|version| revision(profile, version))
        .ok_or_else(|| failed_precondition("schema_version_unsupported"))?;
    if descriptor.version < 5 && recorded.v4_apply_source_commit.is_some() {
        return Err(failed_precondition("schema_unpublished_metadata_drift"));
    }
    if descriptor.version >= 5
        && recorded
            .v4_apply_source_commit
            .as_deref()
            .is_none_or(|value| validate_source_commit(value).is_err())
    {
        return Err(failed_precondition("schema_v4_apply_provenance_invalid"));
    }
    if descriptor.version < 3 && recorded.v2_apply_source_commit.is_some() {
        return Err(failed_precondition("schema_unpublished_metadata_drift"));
    }
    if descriptor.version >= 3
        && recorded
            .v2_apply_source_commit
            .as_deref()
            .is_none_or(|value| validate_source_commit(value).is_err())
    {
        return Err(failed_precondition("schema_v2_apply_provenance_invalid"));
    }
    if descriptor.version < 4 && recorded.v3_apply_source_commit.is_some() {
        return Err(failed_precondition("schema_unpublished_metadata_drift"));
    }
    if descriptor.version >= 4
        && recorded
            .v3_apply_source_commit
            .as_deref()
            .is_none_or(|value| validate_source_commit(value).is_err())
    {
        return Err(failed_precondition("schema_v3_apply_provenance_invalid"));
    }
    let foundation = revisions(profile)
        .first()
        .expect("the locked foundation revision is embedded");
    if descriptor.version == foundation.version {
        if recorded.chain_digest.is_some()
            || recorded.digest_algorithm.is_some()
            || recorded.storage_writer_epoch.is_some()
            || recorded.upgrade_source_commit.is_some()
        {
            return Err(failed_precondition("schema_unpublished_metadata_drift"));
        }
        return Ok(descriptor);
    }
    let digest = chain_digest_at(profile, descriptor.version)
        .ok_or_else(|| failed_precondition("schema_version_unsupported"))?;
    let epoch = descriptor
        .storage_writer_epoch
        .and_then(|value| i64::try_from(value).ok())
        .ok_or_else(|| failed_precondition("authoritative_schema_not_ready"))?;
    if recorded.chain_digest.as_deref() != Some(digest_hex(digest).as_str())
        || recorded.digest_algorithm.as_deref() != Some(AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM)
        || recorded.storage_writer_epoch != Some(epoch)
    {
        return Err(failed_precondition("authoritative_schema_not_ready"));
    }
    let upgrade_source_commit = recorded
        .upgrade_source_commit
        .as_ref()
        .ok_or_else(|| failed_precondition("schema_upgrade_provenance_missing"))?;
    if validate_source_commit(upgrade_source_commit).is_err() {
        return Err(failed_precondition("schema_upgrade_provenance_invalid"));
    }
    Ok(descriptor)
}

fn verify_ready_metadata(
    recorded: &RecordedMetadata,
    profile: DatabaseProfile,
) -> Result<SchemaIdentity, DomainError> {
    verify_ready_metadata_target(recorded, profile, AuthoritativeSchemaTarget::StorageV4)
}

fn verify_ready_metadata_target(
    recorded: &RecordedMetadata,
    profile: DatabaseProfile,
    target: AuthoritativeSchemaTarget,
) -> Result<SchemaIdentity, DomainError> {
    let admitted = AdmittedTarget::production(target)?;
    verify_ready_metadata_admitted(recorded, profile, admitted)
}

fn verify_ready_metadata_admitted(
    recorded: &RecordedMetadata,
    profile: DatabaseProfile,
    admitted: AdmittedTarget,
) -> Result<SchemaIdentity, DomainError> {
    let target = admitted.target();
    let descriptor = validate_recorded_revision(recorded, profile)?;
    // A valid historical identity is migration input, not serve readiness.
    if descriptor.version != target.version()
        || descriptor.storage_writer_epoch != Some(AUTHORITATIVE_STORAGE_WRITER_EPOCH)
    {
        return Err(failed_precondition("authoritative_schema_not_ready"));
    }
    let digest = chain_digest_at(profile, descriptor.version)
        .ok_or_else(|| failed_precondition("schema_version_unsupported"))?;
    let upgrade_source_commit = recorded
        .upgrade_source_commit
        .as_ref()
        .ok_or_else(|| failed_precondition("schema_upgrade_provenance_missing"))?;
    Ok(SchemaIdentity {
        profile,
        schema_version: descriptor.version,
        chain_digest: digest,
        digest_algorithm: AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM,
        storage_writer_epoch: AUTHORITATIVE_STORAGE_WRITER_EPOCH,
        source_commit: recorded.source_commit.clone(),
        upgrade_source_commit: upgrade_source_commit.clone(),
        v2_apply_source_commit: recorded
            .v2_apply_source_commit
            .clone()
            .ok_or_else(|| failed_precondition("schema_v2_apply_provenance_invalid"))?,
        v3_apply_source_commit: recorded
            .v3_apply_source_commit
            .clone()
            .ok_or_else(|| failed_precondition("schema_v3_apply_provenance_invalid"))?,
        v4_apply_source_commit: recorded.v4_apply_source_commit.clone(),
    })
}

/// Catalog recognition currently covers the locked additive-column actions.
/// An unpublished catalog may contain only a prefix of the immediate next
/// revision. Data backfills and other catalog grammars require separate design.
fn next_revision_for_prefix(
    recorded: &RecordedMetadata,
    profile: DatabaseProfile,
    prefix: usize,
) -> Result<Option<&'static RevisionDescriptor>, DomainError> {
    next_revision_for_prefix_target(
        recorded,
        profile,
        prefix,
        AuthoritativeSchemaTarget::StorageV4,
    )
}

fn next_revision_for_prefix_target(
    recorded: &RecordedMetadata,
    profile: DatabaseProfile,
    prefix: usize,
    target: AuthoritativeSchemaTarget,
) -> Result<Option<&'static RevisionDescriptor>, DomainError> {
    let completed = validate_recorded_revision(recorded, profile)?;
    if completed.version > target.version() {
        return Err(failed_precondition(
            "authoritative_schema_target_downgrade_rejected",
        ));
    }
    if completed.version == target.version() {
        if prefix != completed.action_range.end {
            return Err(failed_precondition(
                "authoritative_schema_upgrade_incomplete",
            ));
        }
        return Ok(None);
    }
    let next = completed
        .version
        .checked_add(1)
        .and_then(|version| revision(profile, version))
        .ok_or_else(|| failed_precondition("schema_version_unsupported"))?;
    if next.action_range.start != completed.action_range.end
        || prefix < next.action_range.start
        || prefix > next.action_range.end
    {
        return Err(failed_precondition(
            "authoritative_schema_revision_prefix_drift",
        ));
    }
    Ok(Some(next))
}

fn ensure_metadata_unchanged(
    expected: &RecordedMetadata,
    current: &RecordedMetadata,
) -> Result<(), DomainError> {
    if current != expected {
        return Err(failed_precondition("schema_metadata_publish_conflict"));
    }
    Ok(())
}

fn metadata_for_transition(
    profile: DatabaseProfile,
    original: &RecordedMetadata,
    target: &RevisionDescriptor,
    source_commit: &str,
) -> Result<RecordedMetadata, DomainError> {
    validate_source_commit(source_commit)?;
    let completed = validate_recorded_revision(original, profile)?;
    if completed.version.checked_add(1) != Some(target.version) {
        return Err(failed_precondition(
            "schema_metadata_transition_not_adjacent",
        ));
    }
    let digest = chain_digest_at(profile, target.version)
        .ok_or_else(|| failed_precondition("schema_version_unsupported"))?;
    let next = RecordedMetadata {
        version: to_i64(target.version)?,
        profile: original.profile.clone(),
        source_commit: original.source_commit.clone(),
        applied_at_ms: original.applied_at_ms,
        chain_digest: Some(digest_hex(digest)),
        digest_algorithm: Some(AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM.to_owned()),
        storage_writer_epoch: target.storage_writer_epoch.map(to_i64).transpose()?,
        // The caller's apply SHA is provenance, never a requirement that each
        // later read-only service binary have that same Git commit.
        upgrade_source_commit: Some(source_commit.to_owned()),
        v2_apply_source_commit: if target.version == 3 {
            original.upgrade_source_commit.clone()
        } else {
            original.v2_apply_source_commit.clone()
        },
        v3_apply_source_commit: if target.version == 4 {
            original.upgrade_source_commit.clone()
        } else {
            original.v3_apply_source_commit.clone()
        },
        v4_apply_source_commit: if target.version == 5 {
            original.upgrade_source_commit.clone()
        } else {
            original.v4_apply_source_commit.clone()
        },
    };
    validate_recorded_revision(&next, profile)?;
    Ok(next)
}

fn publish_metadata(
    client: &mut impl GenericClient,
    profile: DatabaseProfile,
    original: &RecordedMetadata,
    target: &RevisionDescriptor,
    source_commit: &str,
) -> Result<RecordedMetadata, DomainError> {
    let next = metadata_for_transition(profile, original, target, source_commit)?;
    // Compare every persisted field of the old singleton, including NULLs.
    // Keep the foundation source/time; only the adjacent revision is published.
    let mut sql = String::from("UPDATE public.trnm_schema_metadata SET schema_version=$1,chain_digest=$2,digest_algorithm=$3,storage_writer_epoch=$4,upgrade_source_commit=$5");
    let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> = vec![
        &next.version,
        &next.chain_digest,
        &next.digest_algorithm,
        &next.storage_writer_epoch,
        &next.upgrade_source_commit,
        &original.version,
        &original.profile,
        &original.source_commit,
        &original.applied_at_ms,
        &original.chain_digest,
        &original.digest_algorithm,
        &original.storage_writer_epoch,
        &original.upgrade_source_commit,
    ];
    match target.version {
        2 => {}
        3 => {
            sql.push_str(",v2_apply_source_commit=$14");
            params.push(&next.v2_apply_source_commit);
        }
        4 => {
            sql.push_str(",v2_apply_source_commit=$14,v3_apply_source_commit=$15");
            params.push(&next.v2_apply_source_commit);
            params.push(&next.v3_apply_source_commit);
        }
        5 => {
            sql.push_str(",v4_apply_source_commit=$14");
            params.push(&next.v4_apply_source_commit);
        }
        _ => {
            return Err(failed_precondition(
                "schema_metadata_transition_not_adjacent",
            ))
        }
    }
    sql.push_str(" WHERE singleton=1 AND schema_version=$6 AND profile=$7 AND source_commit=$8 AND applied_at_ms=$9 AND chain_digest IS NOT DISTINCT FROM $10 AND digest_algorithm IS NOT DISTINCT FROM $11 AND storage_writer_epoch IS NOT DISTINCT FROM $12 AND upgrade_source_commit IS NOT DISTINCT FROM $13");
    if target.version >= 3 {
        params.push(&original.v2_apply_source_commit);
        sql.push_str(&format!(
            " AND v2_apply_source_commit IS NOT DISTINCT FROM ${}",
            params.len()
        ));
    }
    if target.version >= 4 {
        params.push(&original.v3_apply_source_commit);
        sql.push_str(&format!(
            " AND v3_apply_source_commit IS NOT DISTINCT FROM ${}",
            params.len()
        ));
    }
    if target.version == 5 {
        params.push(&original.v4_apply_source_commit);
        sql.push_str(&format!(
            " AND v4_apply_source_commit IS NOT DISTINCT FROM ${}",
            params.len()
        ));
    }
    let count = client.execute(&sql, &params).map_err(map_postgres_error)?;
    if count != 1 {
        return Err(failed_precondition("schema_metadata_publish_conflict"));
    }
    let catalog = read_catalog(client)?;
    let observed = read_metadata(client, &catalog)?
        .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
    ensure_metadata_unchanged(&next, &observed)?;
    Ok(next)
}

fn bind_foundation_metadata(
    client: &mut impl GenericClient,
    profile: DatabaseProfile,
    source_commit: &str,
    applied_at_ms: i64,
) -> Result<(), DomainError> {
    let foundation = revisions(profile)
        .first()
        .expect("the locked foundation revision is embedded");
    let version = to_i64(foundation.version)?;
    let count = client
        .execute(
            "INSERT INTO public.trnm_schema_metadata \
             (singleton, schema_version, profile, source_commit, applied_at_ms) VALUES (1,$1,$2,$3,$4)",
            &[&version, &profile.metadata_value(), &source_commit, &applied_at_ms],
        )
        .map_err(map_postgres_error)?;
    if count != 1 {
        return Err(data_loss("schema_metadata_binding_failed"));
    }
    Ok(())
}

#[cfg(test)]
mod metadata_tests {
    use super::*;

    fn foundation(profile: DatabaseProfile) -> RecordedMetadata {
        RecordedMetadata {
            version: 1,
            profile: profile.metadata_value().to_owned(),
            source_commit: "a".repeat(40),
            applied_at_ms: 17,
            chain_digest: None,
            digest_algorithm: None,
            storage_writer_epoch: None,
            upgrade_source_commit: None,
            v2_apply_source_commit: None,
            v3_apply_source_commit: None,
            v4_apply_source_commit: None,
        }
    }

    fn timestamps(profile: DatabaseProfile) -> RecordedMetadata {
        metadata_for_transition(
            profile,
            &foundation(profile),
            revision(profile, 2).unwrap(),
            &"b".repeat(40),
        )
        .unwrap()
    }

    #[test]
    fn recorded_revision_checks_profile_prefix_digest_and_writer_epoch() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let old = foundation(profile);
            assert_eq!(
                validate_recorded_revision(&old, profile).unwrap().version,
                1
            );
            assert!(verify_ready_metadata(&old, profile).is_err());
            let current = timestamps(profile);
            assert_eq!(
                validate_recorded_revision(&current, profile)
                    .unwrap()
                    .version,
                2
            );
            assert!(verify_ready_metadata(&current, profile).is_err());
            let native = metadata_for_transition(
                profile,
                &current,
                revision(profile, 3).unwrap(),
                &"c".repeat(40),
            )
            .unwrap();
            assert!(verify_ready_metadata(&native, profile).is_err());
            let imported = metadata_for_transition(
                profile,
                &native,
                revision(profile, 4).unwrap(),
                &"d".repeat(40),
            )
            .unwrap();
            let ready = verify_ready_metadata(&imported, profile).unwrap();
            assert_eq!(ready.v3_apply_source_commit, "c".repeat(40));
            assert_eq!(ready.source_commit, old.source_commit);
            assert_eq!(
                ready.v2_apply_source_commit,
                current.upgrade_source_commit.clone().unwrap()
            );
            let mut wrong_digest = current.clone();
            wrong_digest.chain_digest = Some(digest_hex(chain_digest_at(profile, 1).unwrap()));
            let mut wrong_epoch = current.clone();
            wrong_epoch.storage_writer_epoch = Some(1);
            let mut wrong_algorithm = current.clone();
            wrong_algorithm.digest_algorithm = Some("unrecognized".to_owned());
            let mut other_profile = current.clone();
            other_profile.profile = match profile {
                DatabaseProfile::PostgreSql => "cockroachdb",
                DatabaseProfile::CockroachDb => "postgresql",
            }
            .to_owned();
            for invalid in [wrong_digest, wrong_epoch, wrong_algorithm, other_profile] {
                assert!(validate_recorded_revision(&invalid, profile).is_err());
            }
        }
    }

    #[test]
    fn recorded_metadata_rejects_unknown_revisions_and_invented_provenance() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let current = timestamps(profile);
            for version in [-1, 0, 5, i64::MAX] {
                let mut invalid = current.clone();
                invalid.version = version;
                assert!(validate_recorded_revision(&invalid, profile).is_err());
            }
            let mut bad_source = current.clone();
            bad_source.source_commit = "g".repeat(40);
            let mut bad_time = current.clone();
            bad_time.applied_at_ms = -1;
            let mut missing_upgrade = current.clone();
            missing_upgrade.upgrade_source_commit = None;
            let mut bad_upgrade = current;
            bad_upgrade.upgrade_source_commit = Some("b".repeat(39));
            for invalid in [bad_source, bad_time, missing_upgrade, bad_upgrade] {
                assert!(validate_recorded_revision(&invalid, profile).is_err());
            }
        }
    }

    #[test]
    fn foundation_rejects_each_partially_published_metadata_field() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let old = foundation(profile);
            let mut digest = old.clone();
            digest.chain_digest = Some(digest_hex(chain_digest_at(profile, 1).unwrap()));
            let mut algorithm = old.clone();
            algorithm.digest_algorithm = Some(AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM.to_owned());
            let mut epoch = old.clone();
            epoch.storage_writer_epoch = Some(2);
            let mut upgrade = old;
            upgrade.upgrade_source_commit = Some("b".repeat(40));
            for invalid in [digest, algorithm, epoch, upgrade] {
                assert_eq!(
                    validate_recorded_revision(&invalid, profile)
                        .unwrap_err()
                        .reason(),
                    "schema_unpublished_metadata_drift"
                );
            }
        }
    }

    #[test]
    fn revision_prefix_accepts_only_the_next_revision_or_complete_current_catalog() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let old = foundation(profile);
            let target = revision(profile, 2).unwrap();
            for prefix in target
                .action_range
                .clone()
                .chain(std::iter::once(target.action_range.end))
            {
                assert_eq!(
                    next_revision_for_prefix(&old, profile, prefix)
                        .unwrap()
                        .unwrap()
                        .version,
                    target.version
                );
            }
            assert!(next_revision_for_prefix(&old, profile, target.action_range.end + 1).is_err());
            let current = timestamps(profile);
            let native = revision(profile, 3).unwrap();
            assert_eq!(
                next_revision_for_prefix(&current, profile, target.action_range.end)
                    .unwrap()
                    .unwrap()
                    .version,
                3
            );
            let published =
                metadata_for_transition(profile, &current, native, &"c".repeat(40)).unwrap();
            assert!(
                next_revision_for_prefix(&published, profile, native.action_range.end)
                    .unwrap()
                    .is_some_and(|next| next.version == 4)
            );
            for prefix in [
                0,
                target.action_range.end - 1,
                native.action_range.end + 1,
                usize::MAX,
            ] {
                assert!(next_revision_for_prefix(&current, profile, prefix).is_err());
            }
        }
    }

    #[test]
    fn metadata_snapshot_guard_detects_changes_to_all_old_fields() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let original = timestamps(profile);
            ensure_metadata_unchanged(&original, &original.clone()).unwrap();
            let mut changes = Vec::new();
            let mut changed = original.clone();
            changed.version += 1;
            changes.push(changed);
            let mut changed = original.clone();
            changed.profile.push('_');
            changes.push(changed);
            let mut changed = original.clone();
            changed.source_commit = "c".repeat(40);
            changes.push(changed);
            let mut changed = original.clone();
            changed.applied_at_ms += 1;
            changes.push(changed);
            let mut changed = original.clone();
            changed.chain_digest = None;
            changes.push(changed);
            let mut changed = original.clone();
            changed.digest_algorithm = None;
            changes.push(changed);
            let mut changed = original.clone();
            changed.storage_writer_epoch = None;
            changes.push(changed);
            let mut changed = original.clone();
            changed.upgrade_source_commit = None;
            changes.push(changed);
            let mut changed = original.clone();
            changed.v2_apply_source_commit = Some("d".repeat(40));
            changes.push(changed);
            let mut changed = original.clone();
            changed.v3_apply_source_commit = Some("e".repeat(40));
            changes.push(changed);
            for changed in changes {
                assert_eq!(
                    ensure_metadata_unchanged(&original, &changed)
                        .unwrap_err()
                        .reason(),
                    "schema_metadata_publish_conflict"
                );
            }
        }
    }

    #[test]
    fn adjacent_transition_preserves_foundation_and_separates_apply_provenance() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let old = foundation(profile);
            let source = "c".repeat(40);
            let next =
                metadata_for_transition(profile, &old, revision(profile, 2).unwrap(), &source)
                    .unwrap();
            assert_eq!(next.source_commit, old.source_commit);
            assert_eq!(next.applied_at_ms, old.applied_at_ms);
            assert_eq!(next.upgrade_source_commit.as_deref(), Some(source.as_str()));
            assert_eq!(old, foundation(profile));
            assert!(
                metadata_for_transition(profile, &old, revision(profile, 1).unwrap(), &source)
                    .is_err()
            );
            assert!(metadata_for_transition(
                profile,
                &next,
                revision(profile, 2).unwrap(),
                &source
            )
            .is_err());
            assert!(metadata_for_transition(
                profile,
                &old,
                revision(profile, 2).unwrap(),
                "not-a-commit"
            )
            .is_err());
        }
    }
    #[test]
    fn v3_retains_actual_v2_apply_provenance_and_never_publishes_partial_history() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let old = timestamps(profile);
            let new = metadata_for_transition(
                profile,
                &old,
                revision(profile, 3).unwrap(),
                &"c".repeat(40),
            )
            .unwrap();
            assert_eq!(new.source_commit, old.source_commit);
            assert_eq!(new.applied_at_ms, old.applied_at_ms);
            assert_eq!(new.v2_apply_source_commit, old.upgrade_source_commit);
            assert_eq!(new.upgrade_source_commit, Some("c".repeat(40)));
            for bad in [None, Some("z".repeat(40)), Some("a".repeat(39))] {
                let mut corrupted = new.clone();
                corrupted.v2_apply_source_commit = bad;
                assert!(validate_recorded_revision(&corrupted, profile).is_err());
            }
            let mut early = old;
            early.v2_apply_source_commit = Some("c".repeat(40));
            assert!(validate_recorded_revision(&early, profile).is_err());
        }
    }
    #[test]
    fn accounts_source_revision_preserves_v4_history_and_rejects_storage_downgrade() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let v3 = metadata_for_transition(
                profile,
                &timestamps(profile),
                revision(profile, 3).unwrap(),
                &"C".repeat(40),
            )
            .unwrap();
            let v4 = metadata_for_transition(
                profile,
                &v3,
                revision(profile, 4).unwrap(),
                &"D".repeat(40),
            )
            .unwrap();
            let v5 = metadata_for_transition(
                profile,
                &v4,
                revision(profile, 5).unwrap(),
                &"e".repeat(40),
            )
            .unwrap();
            assert_eq!(v5.source_commit, v4.source_commit);
            assert_eq!(v5.applied_at_ms, v4.applied_at_ms);
            assert_eq!(v5.v2_apply_source_commit, v4.v2_apply_source_commit);
            assert_eq!(v5.v3_apply_source_commit, v4.v3_apply_source_commit);
            assert_eq!(v5.v4_apply_source_commit, v4.upgrade_source_commit);
            assert_eq!(v5.upgrade_source_commit, Some("e".repeat(40)));
            assert_eq!(v5.storage_writer_epoch, Some(4));
            assert_eq!(validate_recorded_revision(&v5, profile).unwrap().version, 5);
            assert_eq!(
                next_revision_for_prefix(
                    &v5,
                    profile,
                    revision(profile, 5).unwrap().action_range.end
                )
                .unwrap_err()
                .reason(),
                "authoritative_schema_target_downgrade_rejected"
            );
            assert_eq!(
                verify_ready_metadata_target(
                    &v5,
                    profile,
                    AuthoritativeSchemaTarget::NakamaAccountsV5
                )
                .unwrap_err()
                .reason(),
                "schema5_native_catalog_capture_pending"
            );
            let mut early = v4.clone();
            early.v4_apply_source_commit = Some("D".repeat(40));
            assert_eq!(
                validate_recorded_revision(&early, profile)
                    .unwrap_err()
                    .reason(),
                "schema_unpublished_metadata_drift"
            );
            for publisher in [None, Some(String::new()), Some("g".repeat(40))] {
                let mut altered = v5.clone();
                altered.v4_apply_source_commit = publisher;
                assert!(validate_recorded_revision(&altered, profile).is_err());
            }
            let mut wrong_epoch = v5.clone();
            wrong_epoch.storage_writer_epoch = Some(5);
            assert!(validate_recorded_revision(&wrong_epoch, profile).is_err());
            assert!(ensure_metadata_unchanged(&v5, &wrong_epoch).is_err());
            assert!(metadata_for_transition(
                profile,
                &v3,
                revision(profile, 5).unwrap(),
                &"e".repeat(40)
            )
            .is_err());
        }
    }

    #[test]
    fn v4_preserves_all_actual_publishers_and_rejects_prepublished_v3_history() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let v2 = timestamps(profile);
            let v3 = metadata_for_transition(
                profile,
                &v2,
                revision(profile, 3).unwrap(),
                &"C".repeat(40),
            )
            .unwrap();
            let v4 = metadata_for_transition(
                profile,
                &v3,
                revision(profile, 4).unwrap(),
                &"d".repeat(40),
            )
            .unwrap();
            let ready = verify_ready_metadata(&v4, profile).unwrap();
            assert_eq!(ready.source_commit, "a".repeat(40));
            assert_eq!(ready.v2_apply_source_commit, "b".repeat(40));
            assert_eq!(ready.v3_apply_source_commit, "C".repeat(40));
            assert_eq!(ready.upgrade_source_commit, "d".repeat(40));
            assert_eq!(v4.applied_at_ms, 17);
            let mut early = v3.clone();
            early.v3_apply_source_commit = Some("C".repeat(40));
            assert_eq!(
                validate_recorded_revision(&early, profile)
                    .unwrap_err()
                    .reason(),
                "schema_unpublished_metadata_drift"
            );
            for history in [None, Some(String::new()), Some("g".repeat(40))] {
                let mut corrupt = v4.clone();
                corrupt.v3_apply_source_commit = history;
                assert!(verify_ready_metadata(&corrupt, profile).is_err());
            }
            let mut empty = v3;
            empty.v3_apply_source_commit = Some(String::new());
            assert!(ensure_metadata_unchanged(&early, &empty).is_err());
        }
    }
}

/// Mutable importer guard reuses the authoritative metadata/catalog identity.
/// Import completion/admission is a separate journal predicate, never a second
/// schema authority. Physical target custody is appended by the importer.
pub(crate) fn storage_import_schema_guard(
    transaction: &mut postgres::Transaction<'_>,
    profile: DatabaseProfile,
    lock_metadata: bool,
) -> Result<IntegrityDigest, DomainError> {
    let sql = if lock_metadata {
        "SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR UPDATE"
    } else {
        "SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1"
    };
    transaction
        .query_one(sql, &[])
        .map_err(map_postgres_error)?;
    let catalog = read_catalog(transaction)?;
    let prefix = catalog_prefix(&catalog, profile)?;
    let recorded = read_metadata(transaction, &catalog)?
        .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
    if next_revision_for_prefix(&recorded, profile, prefix)?.is_some() {
        return Err(failed_precondition(
            "authoritative_schema_upgrade_incomplete",
        ));
    }
    verify_ready_metadata(&recorded, profile)?;
    Ok(recorded_metadata_guard_digest(&recorded))
}

fn recorded_metadata_guard_digest(recorded: &RecordedMetadata) -> IntegrityDigest {
    let mut framed = b"trillionnium.storage-import.schema-guard.v1\0".to_vec();
    let mut field = |value: Option<&[u8]>| match value {
        None => framed.push(0),
        Some(bytes) => {
            framed.push(1);
            framed.extend_from_slice(&(bytes.len() as u64).to_be_bytes());
            framed.extend_from_slice(bytes);
        }
    };
    field(Some(&1_i16.to_be_bytes()));
    field(Some(&recorded.version.to_be_bytes()));
    field(Some(recorded.profile.as_bytes()));
    field(Some(recorded.source_commit.as_bytes()));
    field(Some(&recorded.applied_at_ms.to_be_bytes()));
    field(recorded.chain_digest.as_deref().map(str::as_bytes));
    field(recorded.digest_algorithm.as_deref().map(str::as_bytes));
    let epoch = recorded.storage_writer_epoch.map(i64::to_be_bytes);
    field(epoch.as_ref().map(|bytes| bytes.as_slice()));
    field(recorded.upgrade_source_commit.as_deref().map(str::as_bytes));
    field(
        recorded
            .v2_apply_source_commit
            .as_deref()
            .map(str::as_bytes),
    );
    field(
        recorded
            .v3_apply_source_commit
            .as_deref()
            .map(str::as_bytes),
    );
    IntegrityDigest::from_value(&framed)
}

#[cfg(test)]
mod import_schema_guard_tests {
    use super::*;

    #[test]
    fn schema_guard_distinguishes_every_history_null_empty_and_literal_case() {
        let original = RecordedMetadata {
            version: 4,
            profile: "postgresql".to_owned(),
            source_commit: "a".repeat(40),
            applied_at_ms: 17,
            chain_digest: Some("b".repeat(64)),
            digest_algorithm: Some(AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM.to_owned()),
            storage_writer_epoch: Some(4),
            upgrade_source_commit: Some("c".repeat(40)),
            v2_apply_source_commit: Some("d".repeat(40)),
            v3_apply_source_commit: Some("E".repeat(40)),
            v4_apply_source_commit: None,
        };
        let digest = recorded_metadata_guard_digest(&original);
        let mut variants = Vec::new();
        let mut changed = original.clone();
        changed.version = 3;
        variants.push(changed);
        let mut changed = original.clone();
        changed.profile = "cockroachdb".to_owned();
        variants.push(changed);
        let mut changed = original.clone();
        changed.source_commit = "b".repeat(40);
        variants.push(changed);
        let mut changed = original.clone();
        changed.applied_at_ms = 18;
        variants.push(changed);
        let mut changed = original.clone();
        changed.chain_digest = None;
        variants.push(changed);
        let mut changed = original.clone();
        changed.digest_algorithm = None;
        variants.push(changed);
        let mut changed = original.clone();
        changed.storage_writer_epoch = None;
        variants.push(changed);
        let mut changed = original.clone();
        changed.upgrade_source_commit = None;
        variants.push(changed);
        let mut changed = original.clone();
        changed.v2_apply_source_commit = None;
        variants.push(changed);
        for history in [None, Some(String::new()), Some("e".repeat(40))] {
            let mut changed = original.clone();
            changed.v3_apply_source_commit = history;
            variants.push(changed);
        }
        for changed in &variants {
            assert_ne!(digest, recorded_metadata_guard_digest(changed));
        }
        assert_ne!(
            recorded_metadata_guard_digest(&variants[9]),
            recorded_metadata_guard_digest(&variants[10])
        );
    }
}
