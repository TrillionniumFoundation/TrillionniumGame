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

// The pinned pgx JSONB codec prefers text and appends a Go string unchanged.
// Bind the original bytes as native JSONB, including on excluded rows. Never
// use Json<Value>, add a binary version byte, or reconstruct request bytes.
struct RawStorageJsonb<'a>(&'a [u8]);

impl<'a> RawStorageJsonb<'a> {
    fn new(value: &'a [u8]) -> Result<Self, DomainError> {
        if value.len() > MAX_VALUE_BYTES {
            return Err(invalid("invalid_storage_value"));
        }
        Ok(Self(value))
    }
}

impl std::fmt::Debug for RawStorageJsonb<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RawStorageJsonb")
            .field("bytes", &self.0.len())
            .finish()
    }
}

impl ToSql for RawStorageJsonb<'_> {
    fn to_sql(
        &self,
        kind: &postgres::types::Type,
        output: &mut bytes::BytesMut,
    ) -> Result<postgres::types::IsNull, Box<dyn std::error::Error + Sync + Send>> {
        if *kind != postgres::types::Type::JSONB || self.0.len() > MAX_VALUE_BYTES {
            return Err(Box::new(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "raw storage JSONB type or input bound rejected",
            )));
        }
        output.extend_from_slice(self.0);
        Ok(postgres::types::IsNull::No)
    }

    fn accepts(kind: &postgres::types::Type) -> bool {
        *kind == postgres::types::Type::JSONB
    }

    fn encode_format(&self, _kind: &postgres::types::Type) -> postgres::types::Format {
        postgres::types::Format::Text
    }

    postgres::types::to_sql_checked!();
}

// Expected input is arbitrary TEXT, distinct from stored VARCHAR(32). Keep
// pgx's text wire format as well as the exact bytes, without a token-size cap.
struct RawStorageCondition<'a>(&'a str);

impl std::fmt::Debug for RawStorageCondition<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RawStorageCondition")
            .field("bytes", &self.0.len())
            .finish()
    }
}

impl ToSql for RawStorageCondition<'_> {
    fn to_sql(
        &self,
        kind: &postgres::types::Type,
        output: &mut bytes::BytesMut,
    ) -> Result<postgres::types::IsNull, Box<dyn std::error::Error + Sync + Send>> {
        if *kind != postgres::types::Type::TEXT {
            return Err(Box::new(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "raw storage condition type rejected",
            )));
        }
        output.extend_from_slice(self.0.as_bytes());
        Ok(postgres::types::IsNull::No)
    }

    fn accepts(kind: &postgres::types::Type) -> bool {
        *kind == postgres::types::Type::TEXT
    }

    fn encode_format(&self, _kind: &postgres::types::Type) -> postgres::types::Format {
        postgres::types::Format::Text
    }

    postgres::types::to_sql_checked!();
}

fn decode_locked_storage_access(row: Row) -> Result<LockedStorageAccess, DomainError> {
    let version = PublicVersion::new(
        row.try_get::<_, String>(0)
            .map_err(|_| data_loss("invalid_storage_public_version"))?,
    )
    .map_err(|_| data_loss("invalid_storage_public_version"))?;
    let write = WritePermission::from_stored(
        row.try_get::<_, i16>(1)
            .map_err(|_| data_loss("invalid_storage_write_permission"))?,
    )
    .map_err(|_| data_loss("invalid_storage_write_permission"))?;
    Ok(LockedStorageAccess { version, write })
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
        .map(decode_locked_storage_access)
        .transpose()
}

fn acquire_nakama_write_access(
    transaction: &mut Transaction<'_>,
    actor: Actor,
    operation: &WriteOperation,
) -> Result<Option<LockedStorageAccess>, DomainError> {
    let payload = RawStorageJsonb::new(&operation.value)?;
    let VersionCheck::Exact(token) = &operation.expected else {
        // Any/star retain the existing lock approximation, with payload input
        // validated at native Bind before that lock or host ACL/OCC decision.
        return transaction
            .query_opt(
                "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects \
                 WHERE collection=$1 AND object_key=$2 AND user_id=$3 \
                   AND $4::JSONB IS NOT NULL FOR UPDATE",
                &[
                    &operation.key.collection(),
                    &operation.key.key(),
                    &operation.key.user_id().as_bytes().as_slice(),
                    &payload,
                ],
            )
            .map_err(map_postgres_error)?
            .map(decode_locked_storage_access)
            .transpose();
    };
    let condition = RawStorageCondition(token.as_str());
    let authoritative = actor == Actor::Server;
    let eligible = transaction
        .query_opt(
            "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects \
             WHERE collection=$1 AND object_key=$2 AND user_id=$3 \
               AND $4::JSONB IS NOT NULL AND public_version::TEXT=$5::TEXT \
               AND ($6::BOOL OR write_permission=1) FOR UPDATE",
            &[
                &operation.key.collection(),
                &operation.key.key(),
                &operation.key.user_id().as_bytes().as_slice(),
                &payload,
                &condition,
                &authoritative,
            ],
        )
        .map_err(map_postgres_error)?
        .map(decode_locked_storage_access)
        .transpose()?;
    if eligible.is_some() {
        return Ok(eligible);
    }
    // Excluded Exact rows use only a plain skinny fallback. A held stale or
    // write0 row must not be locked to classify ACL before version. Input has
    // already been natively bound; do not decode a rejected payload/witness.
    let fallback = transaction
        .query_opt(
            "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects \
             WHERE collection=$1 AND object_key=$2 AND user_id=$3",
            &[
                &operation.key.collection(),
                &operation.key.key(),
                &operation.key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?
        .map(decode_locked_storage_access)
        .transpose()?;
    if fallback.as_ref().is_some_and(|row| {
        row.version.as_str() == token.as_str() && (authoritative || row.write.allows_client_write())
    }) {
        return Err(data_loss("storage_exact_access_predicate_mismatch"));
    }
    Ok(fallback)
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
            authorize_delete_permission(actor, &delete.key, access.map(|row| row.write))?;
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

#[cfg(test)]
mod raw_storage_binding_tests {
    use super::{RawStorageCondition, RawStorageJsonb, MAX_VALUE_BYTES};
    use bytes::BytesMut;
    use postgres::types::{Format, IsNull, ToSql, Type};

    #[test]
    fn raw_jsonb_text_binding_preserves_bytes_and_rejects_wrong_type_or_budget() {
        let original = br#" {"b":1e0, "a":3, "b":2, "s":"\u0061"} "#;
        let payload = RawStorageJsonb::new(original).unwrap();
        assert_eq!(Type::JSONB.oid(), 3802);
        assert!(matches!(payload.encode_format(&Type::JSONB), Format::Text));
        let mut wire = BytesMut::new();
        assert!(matches!(
            payload.to_sql_checked(&Type::JSONB, &mut wire).unwrap(),
            IsNull::No
        ));
        assert_eq!(wire.as_ref(), original);
        assert!(!format!("{payload:?}").contains("1e0"));
        assert!(payload
            .to_sql_checked(&Type::TEXT, &mut BytesMut::new())
            .is_err());
        assert!(RawStorageJsonb::new(&vec![b'x'; MAX_VALUE_BYTES + 1]).is_err());

        // Native input validity belongs to the engine, including invalid UTF8.
        let native_invalid = RawStorageJsonb::new(&[0xff, 0x00]).unwrap();
        let mut raw = BytesMut::new();
        native_invalid
            .to_sql_checked(&Type::JSONB, &mut raw)
            .unwrap();
        assert_eq!(raw.as_ref(), &[0xff, 0x00]);
    }

    #[test]
    fn raw_condition_text_binding_preserves_long_unicode_star_empty_and_nul() {
        let long = "q".repeat(8192);
        for original in ["", "*", "ABCDEF", "非十六进制", "x\0y", long.as_str()] {
            let condition = RawStorageCondition(original);
            assert_eq!(Type::TEXT.oid(), 25);
            assert!(matches!(condition.encode_format(&Type::TEXT), Format::Text));
            let mut wire = BytesMut::new();
            assert!(matches!(
                condition.to_sql_checked(&Type::TEXT, &mut wire).unwrap(),
                IsNull::No
            ));
            assert_eq!(wire.as_ref(), original.as_bytes());
            assert!(condition
                .to_sql_checked(&Type::VARCHAR, &mut BytesMut::new())
                .is_err());
        }
        assert!(!format!("{:?}", RawStorageCondition("secret-token")).contains("secret-token"));
    }
}
