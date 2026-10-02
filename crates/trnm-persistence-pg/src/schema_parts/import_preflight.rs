// Schema3->4 changes stored identifier/ACL domains, not payloads or history.
// Validate actual native JSONB and known/unknown witness bindings independently
// of strict HTTP request factories and the legacy-v2 conversion algorithm.
#[derive(Clone, Debug)]
struct NativeStorageImportRow {
    collection: String,
    key: String,
    user: Vec<u8>,
    native: String,
    public_version: String,
    projection_digest: Vec<u8>,
    origin: String,
    raw: Option<Vec<u8>>,
    raw_digest: Option<Vec<u8>>,
    manifest: Option<Vec<u8>>,
    raw_native: Option<String>,
    read: i16,
    write: i16,
    audit_ms: i64,
}

fn validate_native_storage_import_row(row: &NativeStorageImportRow) -> Result<(), DomainError> {
    if row.collection.chars().count() > 128
        || row.key.chars().count() > 128
        || row.user.len() != 16
        || row.read < 0
        || row.write < 0
        || row.audit_ms < 0
        || row.public_version.chars().count() > 32
    {
        return Err(data_loss("schema_storage_stored_domain_invalid"));
    }
    if row.native.len() > NATIVE_STORAGE_VALUE_LIMIT {
        return Err(DomainError::new(
            trnm_contracts::StableCode::ResourceExhausted,
            "schema_storage_projection_budget_exceeded",
            trnm_contracts::RetryClass::Never,
        ));
    }
    if row.projection_digest.as_slice()
        != IntegrityDigest::from_value(row.native.as_bytes())
            .get()
            .as_bytes()
            .as_slice()
    {
        return Err(data_loss("schema_storage_projection_digest_invalid"));
    }
    match row.origin.as_str() {
        "legacy-rust-v2-bytes" | "write-request-bytes" => {
            let raw = row
                .raw
                .as_deref()
                .ok_or_else(|| data_loss("schema_storage_known_witness_invalid"))?;
            if raw.len() > 1048576
                || row.manifest.is_some()
                || row.raw_digest.as_deref()
                    != Some(IntegrityDigest::from_value(raw).get().as_bytes().as_slice())
                || crate::ContentVersion::from_value(raw).as_str() != row.public_version
                || row.raw_native.as_deref() != Some(row.native.as_str())
            {
                return Err(data_loss("schema_storage_known_witness_invalid"));
            }
        }
        "nakama-export-unknown-request" => {
            if row.raw.is_some()
                || row.raw_digest.is_some()
                || row.raw_native.is_some()
                || row
                    .manifest
                    .as_deref()
                    .is_none_or(|digest| digest.len() != 32 || digest.iter().all(|byte| *byte == 0))
            {
                return Err(data_loss("schema_storage_unknown_witness_invalid"));
            }
        }
        _ => return Err(data_loss("schema_storage_origin_invalid")),
    }
    Ok(())
}

fn preflight_v3_storage(client: &mut impl GenericClient) -> Result<(), DomainError> {
    let mut cursor: Option<LegacyStorageCursor> = None;
    loop {
        let collection = cursor.as_ref().map(|item| item.0.as_str());
        let key = cursor.as_ref().map(|item| item.1.as_str());
        let user = cursor.as_ref().map(|item| item.2.as_slice());
        let rows=client.query("SELECT collection,object_key,user_id,octet_length(value_jsonb::TEXT)::BIGINT,CASE WHEN octet_length(value_jsonb::TEXT)<=16777216 THEN value_jsonb::TEXT ELSE NULL END,public_version,value_projection_digest,value_origin,CASE WHEN octet_length(value_bytes)<=1048576 THEN value_bytes ELSE NULL END,octet_length(value_bytes)::BIGINT,version_digest,source_manifest_digest,CASE WHEN value_origin IN ('legacy-rust-v2-bytes','write-request-bytes') AND octet_length(value_bytes)<=1048576 THEN CASE WHEN octet_length(pg_catalog.convert_from(value_bytes,'UTF8')::JSONB::TEXT)<=16777216 THEN pg_catalog.convert_from(value_bytes,'UTF8')::JSONB::TEXT ELSE NULL END ELSE NULL END,read_permission,write_permission,updated_at_ms,create_time,update_time FROM public.trnm_storage_objects WHERE $1::TEXT IS NULL OR (collection,object_key,user_id)>($1::TEXT,$2::TEXT,$3::BYTEA) ORDER BY collection,object_key,user_id LIMIT 1",&[&collection,&key,&user]).map_err(|error|if error.code().is_some_and(|state|state.code().starts_with("22")){data_loss("schema_storage_native_projection_invalid")}else{map_postgres_error(error)})?;
        let Some(row) = rows.first() else {
            break;
        };
        let length: i64 = row.try_get(3).map_err(map_postgres_error)?;
        if length < 0 || length > NATIVE_STORAGE_VALUE_LIMIT as i64 {
            return Err(DomainError::new(
                trnm_contracts::StableCode::ResourceExhausted,
                "schema_storage_projection_budget_exceeded",
                trnm_contracts::RetryClass::Never,
            ));
        }
        let raw_len: Option<i64> = row.try_get(9).map_err(map_postgres_error)?;
        if raw_len.is_some_and(|length| !(0..=1048576).contains(&length)) {
            return Err(data_loss("schema_storage_known_witness_invalid"));
        }
        let times = crate::StorageTimes {
            create: row
                .try_get(16)
                .map_err(|_| data_loss("schema_storage_timestamp_invalid"))?,
            update: row
                .try_get(17)
                .map_err(|_| data_loss("schema_storage_timestamp_invalid"))?,
        };
        times
            .validate()
            .map_err(|_| data_loss("schema_storage_timestamp_invalid"))?;
        let decoded = NativeStorageImportRow {
            collection: row.try_get(0).map_err(map_postgres_error)?,
            key: row.try_get(1).map_err(map_postgres_error)?,
            user: row.try_get(2).map_err(map_postgres_error)?,
            native: row
                .try_get::<_, Option<String>>(4)
                .map_err(map_postgres_error)?
                .ok_or_else(|| data_loss("schema_storage_native_projection_invalid"))?,
            public_version: row.try_get(5).map_err(map_postgres_error)?,
            projection_digest: row.try_get(6).map_err(map_postgres_error)?,
            origin: row.try_get(7).map_err(map_postgres_error)?,
            raw: row.try_get(8).map_err(map_postgres_error)?,
            raw_digest: row.try_get(10).map_err(map_postgres_error)?,
            manifest: row.try_get(11).map_err(map_postgres_error)?,
            raw_native: row.try_get(12).map_err(map_postgres_error)?,
            read: row.try_get(13).map_err(map_postgres_error)?,
            write: row.try_get(14).map_err(map_postgres_error)?,
            audit_ms: row.try_get(15).map_err(map_postgres_error)?,
        };
        validate_native_storage_import_row(&decoded)?;
        // Metadata is still3 while this revision is being completed. Wider
        // rows belong only to the journal importer after epoch4 publication;
        // a removed Cockroach CHECK is not premature data-write authority.
        if decoded.collection.is_empty()
            || decoded.key.is_empty()
            || decoded.read > 2
            || decoded.write > 1
        {
            return Err(failed_precondition(
                "schema_unpublished_stored_domain_drift",
            ));
        }
        cursor = Some((decoded.collection, decoded.key, decoded.user));
    }
    Ok(())
}

fn require_empty_import_journals(
    client: &mut impl GenericClient,
    catalog: &Catalog,
) -> Result<(), DomainError> {
    for table in ["trnm_storage_import_jobs", "trnm_storage_import_pages"] {
        if catalog.contains_key(&(table.to_owned(), "@table".to_owned())) {
            let row = client
                .query_one(
                    &format!("SELECT EXISTS (SELECT 1 FROM public.{table} LIMIT 1)"),
                    &[],
                )
                .map_err(map_postgres_error)?;
            if row.try_get::<_, bool>(0).map_err(map_postgres_error)? {
                return Err(failed_precondition(
                    "schema_unpublished_import_journal_nonempty",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod import_preflight_tests {
    use super::*;

    fn known() -> NativeStorageImportRow {
        let raw = b"{\"b\":2,\"a\":1}".to_vec();
        let native = "{\"a\": 1, \"b\": 2}".to_owned();
        NativeStorageImportRow {
            collection: String::new(),
            key: "\u{0001}历史".to_owned(),
            user: vec![1; 16],
            native: native.clone(),
            public_version: crate::ContentVersion::from_value(&raw).as_str().to_owned(),
            projection_digest: IntegrityDigest::from_value(native.as_bytes())
                .get()
                .as_bytes()
                .to_vec(),
            origin: "write-request-bytes".to_owned(),
            raw_digest: Some(IntegrityDigest::from_value(&raw).get().as_bytes().to_vec()),
            raw: Some(raw),
            manifest: None,
            raw_native: Some(native),
            read: 32767,
            write: 2,
            audit_ms: 0,
        }
    }

    #[test]
    fn v4_stored_preflight_preserves_known_request_md5_independent_native_render() {
        let row = known();
        assert_ne!(
            row.public_version,
            crate::ContentVersion::from_value(row.native.as_bytes()).as_str()
        );
        validate_native_storage_import_row(&row).unwrap();
        let mut corrupt = row.clone();
        corrupt.public_version = crate::ContentVersion::from_value(row.native.as_bytes())
            .as_str()
            .to_owned();
        assert!(validate_native_storage_import_row(&corrupt).is_err());
        let mut corrupt = row;
        corrupt.raw_native = Some("null".to_owned());
        assert!(validate_native_storage_import_row(&corrupt).is_err());
    }

    #[test]
    fn v4_stored_preflight_accepts_unknown_native_null_empty_version_and_raw_acl() {
        let mut row = known();
        row.origin = "nakama-export-unknown-request".to_owned();
        row.native = "null".to_owned();
        row.public_version = String::new();
        row.projection_digest = IntegrityDigest::from_value(b"null")
            .get()
            .as_bytes()
            .to_vec();
        row.raw = None;
        row.raw_digest = None;
        row.raw_native = None;
        row.manifest = Some(vec![2; 32]);
        validate_native_storage_import_row(&row).unwrap();
        row.public_version = "opaque历史*UPPER".to_owned();
        validate_native_storage_import_row(&row).unwrap();
        for manifest in [None, Some(vec![0; 32]), Some(vec![2; 31])] {
            let mut bad = row.clone();
            bad.manifest = manifest;
            assert!(validate_native_storage_import_row(&bad).is_err());
        }
        row.raw = Some(b"null".to_vec());
        assert!(validate_native_storage_import_row(&row).is_err());
    }
    #[test]
    fn v4_stored_preflight_rejects_acl_and_identifier_drift_without_rewriting() {
        let original = known();
        let mut read = original.clone();
        read.read = -1;
        let mut write = original.clone();
        write.write = -1;
        let mut key = original.clone();
        key.key = "字".repeat(129);
        let mut version = original.clone();
        version.public_version = "字".repeat(33);
        let mut witness = original.clone();
        witness.raw_digest = Some(vec![0; 32]);
        for malformed in [read, write, key, version, witness] {
            assert!(validate_native_storage_import_row(&malformed).is_err());
        }
        validate_native_storage_import_row(&original).unwrap();
        assert_eq!(original.read, 32767);
        assert_eq!(original.write, 2);
        assert!(original.collection.is_empty());
    }

    #[test]
    fn v4_stored_preflight_treats_native_expansion_as_resource_exhaustion() {
        let mut row = known();
        row.origin = "nakama-export-unknown-request".to_owned();
        row.raw = None;
        row.raw_digest = None;
        row.raw_native = None;
        row.manifest = Some(vec![3; 32]);
        row.native = format!("\"{}\"", "x".repeat(1048576));
        row.public_version = "opaque".to_owned();
        row.projection_digest = IntegrityDigest::from_value(row.native.as_bytes())
            .get()
            .as_bytes()
            .to_vec();
        validate_native_storage_import_row(&row).unwrap();
        row.native = "x".repeat(NATIVE_STORAGE_VALUE_LIMIT + 1);
        let error = validate_native_storage_import_row(&row).unwrap_err();
        assert_eq!(error.code(), trnm_contracts::StableCode::ResourceExhausted);
    }
}
