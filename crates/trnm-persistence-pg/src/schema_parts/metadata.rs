#[derive(Clone, Debug)]
struct RecordedMetadata {
    version: i64,
    profile: String,
    source_commit: String,
    applied_at_ms: i64,
    chain_digest: Option<String>,
    digest_algorithm: Option<String>,
    storage_writer_epoch: Option<i64>,
    upgrade_source_commit: Option<String>,
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
    for byte in digest.get().as_bytes() { write!(result, "{byte:02x}").expect("String write"); }
    result
}

fn read_metadata(client: &mut impl GenericClient, catalog: &Catalog) -> Result<Option<RecordedMetadata>, DomainError> {
    let optional = |name: &str, kind: &str| {
        if catalog.contains_key(&("trnm_schema_metadata".to_owned(), name.to_owned())) {
            name.to_owned()
        } else { format!("NULL::{kind}") }
    };
    let rows = client.query(&format!(
        "SELECT singleton, schema_version, profile, source_commit, applied_at_ms, {}, {}, {}, {} \
         FROM public.trnm_schema_metadata", optional("chain_digest", "text"), optional("digest_algorithm", "text"),
         optional("storage_writer_epoch", "bigint"), optional("upgrade_source_commit", "text")), &[])
        .map_err(map_postgres_error)?;
    if rows.len() > 1 { return Err(data_loss("schema_metadata_cardinality_invalid")); }
    let Some(row) = rows.first() else { return Ok(None) };
    if row.try_get::<_, i16>(0).map_err(map_postgres_error)? != 1 { return Err(data_loss("schema_metadata_singleton_invalid")); }
    Ok(Some(RecordedMetadata {
        version: row.try_get(1).map_err(map_postgres_error)?, profile:row.try_get(2).map_err(map_postgres_error)?,
        source_commit:row.try_get(3).map_err(map_postgres_error)?, applied_at_ms:row.try_get(4).map_err(map_postgres_error)?,
        chain_digest:row.try_get(5).map_err(map_postgres_error)?, digest_algorithm:row.try_get(6).map_err(map_postgres_error)?,
        storage_writer_epoch:row.try_get(7).map_err(map_postgres_error)?, upgrade_source_commit:row.try_get(8).map_err(map_postgres_error)?,
    }))
}

fn validate_metadata_base(recorded: &RecordedMetadata, profile: DatabaseProfile) -> Result<(), DomainError> {
    if recorded.profile != profile.metadata_value() || recorded.applied_at_ms < 0 ||
        validate_source_commit(&recorded.source_commit).is_err() {
        return Err(failed_precondition("schema_metadata_provenance_invalid"));
    }
    if !(1..=2).contains(&recorded.version) { return Err(failed_precondition("schema_version_unsupported")); }
    Ok(())
}

fn verify_ready_metadata(recorded: &RecordedMetadata, profile: DatabaseProfile) -> Result<SchemaIdentity, DomainError> {
    validate_metadata_base(recorded, profile)?;
    let digest = authoritative_chain_digest(profile);
    if recorded.version != 2 || recorded.chain_digest.as_deref() != Some(digest_hex(digest).as_str()) ||
        recorded.digest_algorithm.as_deref() != Some(AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM) || recorded.storage_writer_epoch != Some(2) {
        return Err(failed_precondition("authoritative_schema_not_ready"));
    }
    let upgrade_source_commit = recorded.upgrade_source_commit.as_ref()
        .ok_or_else(|| failed_precondition("schema_upgrade_provenance_missing"))?;
    if validate_source_commit(upgrade_source_commit).is_err() { return Err(failed_precondition("schema_upgrade_provenance_invalid")); }
    Ok(SchemaIdentity {profile, schema_version:2, chain_digest:digest, digest_algorithm:AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM,
        storage_writer_epoch:2, source_commit:recorded.source_commit.clone(), upgrade_source_commit:upgrade_source_commit.clone()})
}

fn validate_unpublished_metadata(recorded: &RecordedMetadata, profile: DatabaseProfile) -> Result<(), DomainError> {
    validate_metadata_base(recorded, profile)?;
    if recorded.version != 1 || recorded.chain_digest.is_some() || recorded.digest_algorithm.is_some() ||
        recorded.storage_writer_epoch.is_some() || recorded.upgrade_source_commit.is_some() {
        return Err(failed_precondition("schema_unpublished_metadata_drift"));
    }
    Ok(())
}

fn publish_metadata(client: &mut impl GenericClient, profile: DatabaseProfile, original: &RecordedMetadata,
    source_commit: &str) -> Result<(), DomainError> {
    let count = client.execute(
        "UPDATE public.trnm_schema_metadata SET schema_version = 2, chain_digest = $1, \
          digest_algorithm = $2, storage_writer_epoch = 2, upgrade_source_commit = $3 \
         WHERE singleton = 1 AND schema_version = 1 AND profile = $4 AND source_commit = $5 \
           AND applied_at_ms = $6 AND chain_digest IS NULL AND digest_algorithm IS NULL \
           AND storage_writer_epoch IS NULL AND upgrade_source_commit IS NULL",
        &[&digest_hex(authoritative_chain_digest(profile)), &AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM,
          &source_commit, &profile.metadata_value(), &original.source_commit, &original.applied_at_ms])
        .map_err(map_postgres_error)?;
    if count != 1 { return Err(failed_precondition("schema_metadata_publish_conflict")); }
    Ok(())
}

fn bind_foundation_metadata(client: &mut impl GenericClient, profile: DatabaseProfile, source_commit: &str, applied_at_ms: i64) -> Result<(), DomainError> {
    let count = client.execute("INSERT INTO public.trnm_schema_metadata \
        (singleton, schema_version, profile, source_commit, applied_at_ms) VALUES (1,1,$1,$2,$3)",
        &[&profile.metadata_value(), &source_commit, &applied_at_ms]).map_err(map_postgres_error)?;
    if count != 1 { return Err(data_loss("schema_metadata_binding_failed")); }
    Ok(())
}
