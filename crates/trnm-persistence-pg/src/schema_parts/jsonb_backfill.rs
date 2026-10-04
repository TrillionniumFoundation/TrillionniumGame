// Native casting and bounded, keyset-scanned legacy witness conversion. This
// runner never reconstructs an original Nakama request from a JSONB rendering.
const LEGACY_STORAGE_SCAN_BATCH: i64 = 1;
const NATIVE_STORAGE_VALUE_LIMIT: usize = 16 * 1024 * 1024;

#[derive(Clone, Debug)]
struct LegacyStorageRow {
    collection: String,
    key: String,
    user: Vec<u8>,
    raw: Vec<u8>,
    raw_digest: Vec<u8>,
    read: i16,
    write: i16,
    audit_ms: i64,
    create_time: Option<String>,
    update_time: Option<String>,
    native: Option<String>,
    public_version: Option<String>,
    projection_digest: Option<Vec<u8>>,
    origin: Option<String>,
    manifest: Option<Vec<u8>>,
}

type LegacyStorageCursor = (String, String, Vec<u8>);

fn read_next_legacy_storage(
    client: &mut impl GenericClient,
    catalog: &Catalog,
    cursor: Option<&LegacyStorageCursor>,
    lock: bool,
) -> Result<Option<LegacyStorageRow>, DomainError> {
    let present =
        |name: &str| catalog.contains_key(&("trnm_storage_objects".to_owned(), name.to_owned()));
    let optional = |name: &str, kind: &str| {
        if present(name) {
            name.to_owned()
        } else {
            format!("NULL::{kind}")
        }
    };
    let native_text = if present("value_jsonb") {
        "CASE WHEN octet_length(value_jsonb::TEXT)<=16777216 THEN value_jsonb::TEXT ELSE NULL END"
    } else {
        "NULL::TEXT"
    };
    let native_length = if present("value_jsonb") {
        "octet_length(value_jsonb::TEXT)::BIGINT"
    } else {
        "NULL::BIGINT"
    };
    let native_fields = format!(
        "{native_text},{},{},{},{},{native_length}",
        optional("public_version", "TEXT"),
        optional("value_projection_digest", "BYTEA"),
        optional("value_origin", "TEXT"),
        optional("source_manifest_digest", "BYTEA")
    );
    let time_text = |name: &str| {
        if present(name) {
            format!("{name}::TEXT")
        } else {
            "NULL::TEXT".to_owned()
        }
    };
    let times = format!("{},{}", time_text("create_time"), time_text("update_time"));
    let native_times = format!(
        "{},{}",
        optional("create_time", "TIMESTAMPTZ"),
        optional("update_time", "TIMESTAMPTZ")
    );
    let collection = cursor.map(|item| item.0.as_str());
    let key = cursor.map(|item| item.1.as_str());
    let user = cursor.map(|item| item.2.as_slice());
    let sql = format!("SELECT collection,object_key,user_id, \
        CASE WHEN octet_length(value_bytes) <= 1048576 THEN value_bytes ELSE NULL END,version_digest, \
        read_permission,write_permission,updated_at_ms,{times},{native_fields},octet_length(value_bytes)::BIGINT,{native_times} \
        FROM public.trnm_storage_objects WHERE $1::TEXT IS NULL OR \
        (collection,object_key,user_id)>($1::TEXT,$2::TEXT,$3::BYTEA) \
        ORDER BY collection,object_key,user_id LIMIT $4 {}", if lock {"FOR UPDATE"} else {""});
    let rows = client
        .query(
            &sql,
            &[&collection, &key, &user, &LEGACY_STORAGE_SCAN_BATCH],
        )
        .map_err(|error| {
            if error.code().is_some_and(|state| state.code().starts_with("22")) {
                data_loss("schema_legacy_storage_native_invalid")
            } else {
                map_postgres_error(error)
            }
        })?;
    let Some(row) = rows.first() else {
        return Ok(None);
    };
    // Decode actual timestamp binary values through the shared checked DTO;
    // retain their original SQL text below solely for the unchanged-row CAS.
    let times = crate::StorageTimes {
        create: row
            .try_get(17)
            .map_err(|_| data_loss("schema_legacy_storage_timestamp_invalid"))?,
        update: row
            .try_get(18)
            .map_err(|_| data_loss("schema_legacy_storage_timestamp_invalid"))?,
    };
    times
        .validate()
        .map_err(|_| data_loss("schema_legacy_storage_timestamp_invalid"))?;
    let raw_len: Option<i64> = row.try_get(16).map_err(map_postgres_error)?;
    if raw_len.is_none_or(|length| !(0..=1048576).contains(&length)) {
        return Err(data_loss("schema_legacy_storage_witness_invalid"));
    }
    let native_len: Option<i64> = row.try_get(15).map_err(map_postgres_error)?;
    if native_len.is_some_and(|length| length < 0 || length > NATIVE_STORAGE_VALUE_LIMIT as i64) {
        return Err(DomainError::new(
            trnm_contracts::StableCode::ResourceExhausted,
            "schema_storage_projection_budget_exceeded",
            trnm_contracts::RetryClass::Never,
        ));
    }
    Ok(Some(LegacyStorageRow {
        collection: row.try_get(0).map_err(map_postgres_error)?,
        key: row.try_get(1).map_err(map_postgres_error)?,
        user: row.try_get(2).map_err(map_postgres_error)?,
        raw: row
            .try_get::<_, Option<Vec<u8>>>(3)
            .map_err(map_postgres_error)?
            .ok_or_else(|| data_loss("schema_legacy_storage_witness_invalid"))?,
        raw_digest: row
            .try_get::<_, Option<Vec<u8>>>(4)
            .map_err(map_postgres_error)?
            .ok_or_else(|| data_loss("schema_legacy_storage_witness_invalid"))?,
        read: row.try_get(5).map_err(map_postgres_error)?,
        write: row.try_get(6).map_err(map_postgres_error)?,
        audit_ms: row.try_get(7).map_err(map_postgres_error)?,
        create_time: row.try_get(8).map_err(map_postgres_error)?,
        update_time: row.try_get(9).map_err(map_postgres_error)?,
        native: row.try_get(10).map_err(map_postgres_error)?,
        public_version: row.try_get(11).map_err(map_postgres_error)?,
        projection_digest: row.try_get(12).map_err(map_postgres_error)?,
        origin: row.try_get(13).map_err(map_postgres_error)?,
        manifest: row.try_get(14).map_err(map_postgres_error)?,
    }))
}

fn native_legacy_projection(
    client: &mut impl GenericClient,
    row: &LegacyStorageRow,
) -> Result<String, DomainError> {
    if row.user.len() != 16
        || row.audit_ms < 0
        || !(0..=2).contains(&row.read)
        || !(0..=1).contains(&row.write)
        || IntegrityDigest::from_value(&row.raw)
            .get()
            .as_bytes()
            .as_slice()
            != row.raw_digest
    {
        return Err(data_loss("schema_legacy_storage_witness_invalid"));
    }
    let raw = std::str::from_utf8(&row.raw)
        .map_err(|_| data_loss("schema_legacy_storage_utf8_invalid"))?;
    let native = client.query_one("SELECT octet_length($1::TEXT::JSONB::TEXT)::BIGINT, \
        CASE WHEN octet_length($1::TEXT::JSONB::TEXT)<=16777216 THEN $1::TEXT::JSONB::TEXT ELSE NULL END",&[&raw])
        .map_err(|error| if error.code().is_some_and(|state| state.code().starts_with("22")) {data_loss("schema_legacy_storage_native_json_invalid")} else {map_postgres_error(error)})?;
    let length: i64 = native.try_get(0).map_err(map_postgres_error)?;
    if length < 0 || length > NATIVE_STORAGE_VALUE_LIMIT as i64 {
        return Err(DomainError::new(
            trnm_contracts::StableCode::ResourceExhausted,
            "schema_storage_projection_budget_exceeded",
            trnm_contracts::RetryClass::Never,
        ));
    }
    native
        .try_get::<_, Option<String>>(1)
        .map_err(map_postgres_error)?
        .ok_or_else(|| data_loss("schema_legacy_storage_projection_missing"))
}

fn validate_partial_native_row(
    row: &LegacyStorageRow,
    native: &str,
    complete: bool,
) -> Result<bool, DomainError> {
    let empty = row.native.is_none()
        && row.public_version.is_none()
        && row.projection_digest.is_none()
        && row.origin.is_none()
        && row.manifest.is_none();
    if empty && !complete {
        return Ok(false);
    }
    let expected_version = crate::ContentVersion::from_value(&row.raw);
    let digest = IntegrityDigest::from_value(native.as_bytes());
    if row.native.as_deref() != Some(native)
        || row.public_version.as_deref() != Some(expected_version.as_str())
        || row.projection_digest.as_deref() != Some(digest.get().as_bytes().as_slice())
        || row.origin.as_deref() != Some("legacy-rust-v2-bytes")
        || row.manifest.is_some()
    {
        return Err(data_loss("schema_storage_partial_backfill_drift"));
    }
    Ok(true)
}

fn preflight_native_storage(
    client: &mut impl GenericClient,
    profile: DatabaseProfile,
    complete: bool,
) -> Result<(), DomainError> {
    let catalog = read_catalog(client)?;
    let mut cursor = None;
    while let Some(row) = read_next_legacy_storage(client, &catalog, cursor.as_ref(), false)? {
        let native = native_legacy_projection(client, &row)?;
        validate_partial_native_row(&row, &native, complete)?;
        cursor = Some((row.collection, row.key, row.user));
    }
    // No data mutation or metadata publication occurred in this scan.
    let _ = profile;
    Ok(())
}

fn fill_native_storage_row(
    client: &mut impl GenericClient,
    row: &LegacyStorageRow,
) -> Result<(), DomainError> {
    let native = native_legacy_projection(client, row)?;
    if validate_partial_native_row(row, &native, false)? {
        return Ok(());
    }
    let version = crate::ContentVersion::from_value(&row.raw);
    let digest = IntegrityDigest::from_value(native.as_bytes());
    let count=client.execute("UPDATE public.trnm_storage_objects SET value_jsonb=$1::TEXT::JSONB,public_version=$2, \
        value_projection_digest=$3,value_origin='legacy-rust-v2-bytes' WHERE collection=$4 AND object_key=$5 AND user_id=$6 \
        AND value_bytes IS NOT DISTINCT FROM $7 AND version_digest IS NOT DISTINCT FROM $8 \
        AND read_permission=$9 AND write_permission=$10 AND updated_at_ms=$11 \
        AND create_time::TEXT IS NOT DISTINCT FROM $12 AND update_time::TEXT IS NOT DISTINCT FROM $13 \
        AND value_jsonb IS NULL AND public_version IS NULL AND value_projection_digest IS NULL AND value_origin IS NULL AND source_manifest_digest IS NULL",
        &[&native,&version.as_str(),&digest.get().as_bytes().as_slice(),&row.collection,&row.key,&row.user,&row.raw,&row.raw_digest,&row.read,&row.write,&row.audit_ms,&row.create_time,&row.update_time]).map_err(map_postgres_error)?;
    if count != 1 {
        return Err(failed_precondition("schema_storage_backfill_conflict"));
    }
    Ok(())
}

fn backfill_native_storage(client: &mut impl GenericClient) -> Result<(), DomainError> {
    let catalog = read_catalog(client)?;
    let mut cursor = None;
    while let Some(row) = read_next_legacy_storage(client, &catalog, cursor.as_ref(), true)? {
        fill_native_storage_row(client, &row)?;
        cursor = Some((row.collection, row.key, row.user));
    }
    Ok(())
}
