// The database is the sole JSONB renderer. Raw bytes are provenance witnesses,
// never a read fallback, and public versions are independent opaque tokens.
fn storage_row_columns(_profile: DatabaseProfile, visible: &str) -> String {
    // Both pinned engines support explicit UTF8 decoding. A BYTES/BYTEA text
    // cast produces the binary hex display, which is not the request JSON.
    let raw_native = "pg_catalog.convert_from(value_bytes, 'UTF8')::JSONB::TEXT";
    let native_length = "pg_catalog.octet_length(value_jsonb::TEXT)::INT8";
    let raw_length = "pg_catalog.octet_length(value_bytes)::INT8";
    let known = "value_origin IN ('legacy-rust-v2-bytes', 'write-request-bytes')";
    let fields = [
        format!("CASE WHEN {native_length} <= {MAX_NATIVE_VALUE_BYTES} THEN value_jsonb::TEXT END"),
        native_length.to_owned(),
        "public_version::TEXT".to_owned(),
        "value_projection_digest".to_owned(),
        "read_permission".to_owned(),
        "write_permission".to_owned(),
        "value_origin".to_owned(),
        format!("CASE WHEN {raw_length} <= {MAX_VALUE_BYTES} THEN value_bytes END"),
        raw_length.to_owned(),
        "version_digest".to_owned(),
        "source_manifest_digest".to_owned(),
        format!(
            "CASE WHEN {known} AND {raw_length} <= {MAX_VALUE_BYTES} THEN \
                 CASE WHEN pg_catalog.octet_length({raw_native}) <= {MAX_NATIVE_VALUE_BYTES} \
                 THEN {raw_native} END END"
        ),
        "create_time".to_owned(),
        "update_time".to_owned(),
    ];
    fields
        .into_iter()
        .map(|field| format!("CASE WHEN {visible} THEN {field} END"))
        .collect::<Vec<_>>()
        .join(", ")
}

fn storage_paged_query(
    profile: DatabaseProfile,
    predicate: &str,
    ordering: &str,
    fetch_parameter: usize,
) -> String {
    // Only returned page rows evaluate payload/witness casts. The sentinel
    // remains evidence of existence even if its value or provenance is corrupt.
    let columns = storage_row_columns(profile, &format!("storage_ordinal < ${fetch_parameter}"));
    format!(
        "SELECT object_key, user_id, {columns} \
         FROM (SELECT stored.*, ROW_NUMBER() OVER (ORDER BY {ordering}) AS storage_ordinal \
               FROM public.trnm_storage_objects AS stored \
               WHERE {predicate} ORDER BY {ordering} LIMIT ${fetch_parameter}) AS bounded_storage \
         ORDER BY storage_ordinal"
    )
}

fn project_storage_request(
    transaction: &mut Transaction<'_>,
    request: &[u8],
) -> Result<Vec<u8>, DomainError> {
    if request.len() > MAX_VALUE_BYTES {
        return Err(invalid("invalid_storage_value"));
    }
    let value = std::str::from_utf8(request).map_err(|_| invalid("invalid_storage_value_utf8"))?;
    let row = transaction
        .query_one(
            "SELECT CASE WHEN pg_catalog.octet_length(value) <= $2::INT8 THEN value END, \
                    pg_catalog.octet_length(value)::INT8 \
             FROM (SELECT $1::TEXT::JSONB::TEXT AS value) AS native_projection",
            &[
                &value,
                &i64::try_from(MAX_NATIVE_VALUE_BYTES).expect("constant fits INT8"),
            ],
        )
        .map_err(map_postgres_error)?;
    decode_native_value(&row, 0)
}

fn validate_storage_request_native(
    transaction: &mut Transaction<'_>,
    request: &[u8],
) -> Result<(), DomainError> {
    let text = std::str::from_utf8(request).map_err(|_| invalid("invalid_storage_value_utf8"))?;
    transaction
        .query_one("SELECT $1::TEXT::JSONB IS NOT NULL", &[&text])
        .map_err(map_postgres_error)?;
    Ok(())
}

fn validate_native_condition(
    transaction: &mut Transaction<'_>,
    condition: Option<&str>,
) -> Result<(), DomainError> {
    if let Some(condition) = condition {
        // Do not cast to VARCHAR(32), normalize, or trim an input condition.
        // PostgreSQL/CockroachDB own text validity (including native NUL errors).
        transaction
            .query_one("SELECT $1::TEXT", &[&condition])
            .map_err(map_postgres_error)?;
    }
    Ok(())
}

fn decode_native_value(row: &Row, offset: usize) -> Result<Vec<u8>, DomainError> {
    let length: i64 = row
        .try_get(offset + 1)
        .map_err(|_| data_loss("invalid_storage_native_length"))?;
    let length = usize::try_from(length).map_err(|_| data_loss("invalid_storage_native_length"))?;
    if length > MAX_NATIVE_VALUE_BYTES {
        return Err(storage_resource_exhausted(
            "storage_native_value_budget_exceeded",
        ));
    }
    let value: String = row
        .try_get::<_, Option<String>>(offset)
        .map_err(|_| data_loss("invalid_storage_native_value"))?
        .ok_or_else(|| data_loss("invalid_storage_native_value"))?;
    if value.len() != length {
        return Err(data_loss("storage_native_length_mismatch"));
    }
    Ok(value.into_bytes())
}

fn consume_storage_result_budget(total: &mut usize, value_bytes: usize) -> Result<(), DomainError> {
    *total = total
        .checked_add(value_bytes)
        .filter(|value| *value <= MAX_RESULT_VALUE_BYTES)
        .ok_or_else(|| storage_resource_exhausted("storage_result_value_budget_exceeded"))?;
    Ok(())
}

fn verify_storage_staged_budget(
    staged: &BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
) -> Result<(), DomainError> {
    let mut total = 0;
    for object in staged.values().flatten() {
        consume_storage_result_budget(&mut total, object.object.value.len())?;
    }
    Ok(())
}

fn map_stored_storage_error(source: postgres::Error) -> DomainError {
    if source
        .code()
        .is_some_and(|code| code.code().starts_with("22"))
    {
        return data_loss("invalid_storage_persisted_native_witness");
    }
    let error = map_postgres_error(source);
    // Malformed persisted UTF8/JSONB witnesses are server data loss; native
    // invalid request values remain InvalidArgument on the separate write path.
    if error.code() == StableCode::InvalidArgument {
        data_loss("invalid_storage_persisted_native_witness")
    } else {
        error
    }
}

const fn storage_resource_exhausted(reason: &'static str) -> DomainError {
    error(StableCode::ResourceExhausted, reason, RetryClass::Never)
}

struct LockedStorageAccess {
    version: PublicVersion,
    write: WritePermission,
}

fn lock_storage_access(
    transaction: &mut Transaction<'_>,
    key: &StorageObjectKey,
) -> Result<Option<LockedStorageAccess>, DomainError> {
    transaction
        .query_opt(
            "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects \
         WHERE collection = $1 AND object_key = $2 AND user_id = $3 FOR UPDATE",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?
        .map(|row| {
            let version = PublicVersion::new(
                row.try_get::<_, String>(0)
                    .map_err(|_| data_loss("invalid_storage_public_version"))?,
            )
            .map_err(|_| data_loss("invalid_storage_public_version"))?;
            let write = match row
                .try_get::<_, i16>(1)
                .map_err(|_| data_loss("invalid_storage_write_permission"))?
            {
                0 => WritePermission::None,
                1 => WritePermission::Owner,
                _ => return Err(data_loss("invalid_storage_write_permission")),
            };
            Ok(LockedStorageAccess { version, write })
        })
        .transpose()
}

fn validate_locked_operation(
    transaction: &mut Transaction<'_>,
    actor: Actor,
    operation: &BatchOperation,
    access: Option<&LockedStorageAccess>,
) -> Result<(), DomainError> {
    match operation {
        BatchOperation::Write(write) => {
            let acl = if write.expected == VersionCheck::MustNotExist {
                None
            } else {
                access.map(|row| row.write)
            };
            authorize_write_permission(actor, &write.key, acl)?;
            validate_native_condition(
                transaction,
                match &write.expected {
                    VersionCheck::Exact(token) => Some(token.as_str()),
                    _ => None,
                },
            )?;
            match &write.expected {
                VersionCheck::Any => Ok(()),
                VersionCheck::MustNotExist if access.is_none() => Ok(()),
                VersionCheck::MustNotExist => Err(error(
                    StableCode::AlreadyExists,
                    "storage_object_already_exists",
                    RetryClass::Never,
                )),
                VersionCheck::Exact(token)
                    if access.is_some_and(|row| row.version.as_str() == token.as_str()) =>
                {
                    Ok(())
                }
                VersionCheck::Exact(_) => Err(version_error()),
            }
        }
        BatchOperation::Delete(delete) => {
            authorize_write_permission(actor, &delete.key, access.map(|row| row.write))?;
            validate_native_condition(
                transaction,
                delete.expected_version.as_ref().map(|token| token.as_str()),
            )?;
            let access = access.ok_or_else(storage_not_found)?;
            if delete
                .expected_version
                .as_ref()
                .is_some_and(|token| token.as_str() != access.version.as_str())
            {
                Err(version_error())
            } else {
                Ok(())
            }
        }
    }
}
