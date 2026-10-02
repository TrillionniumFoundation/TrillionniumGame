//! Bounded storage read/write/delete HTTP source candidate. The protocol and public
//! error strings are based on Apache-2.0 Nakama d4d92f93 and nakama-common
//! 449b77ec; this Rust implementation is independent. This subset does not yet
//! reproduce complete historical timestamps, runtime hooks, or indexing.

use std::collections::BTreeMap;
use std::fmt;
use std::io;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::de::{MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::value::RawValue;
use trnm_contracts::{DomainError, StableCode, UserId};
use trnm_persistence_pg::{
    ContentVersion, ReadPermission, StorageActor as Actor, StorageBatchOperation as BatchOperation,
    StorageDeleteOperation as DeleteOperation, StorageObject, StorageObjectKey, StorageTimes,
    StorageTimestamp, StorageWriteOperation as WriteOperation,
    StoredStorageMutationReceipt as MutationReceipt, StoredStorageObject, VersionCheck,
    WritePermission,
};

use super::app::Repository;
use super::codec::encode_hex;
use super::http::{Request, Response};

const MAX_BATCH: usize = 100;
const MAX_REQUEST_VALUE_BYTES: usize = 1024 * 1024;
pub(super) const MAX_ENCODED_STORAGE_RESPONSE_BYTES: usize = 32 * 1024 * 1024;
const INVALID_KEYS: &str = "Invalid collection or key value supplied. They must be set.";
const INVALID_READ: &str = "Invalid Read permission supplied. It must be either 0, 1 or 2.";
const INVALID_WRITE: &str = "Invalid Write permission supplied. It must be either 0 or 1.";
const INVALID_VALUE: &str = "Value must be a JSON object.";
const INVALID_USER: &str = "Invalid user ID - make sure user ID is a valid UUID.";
const REJECTED_VERSION: &str = "Storage write rejected - version check failed.";
const REJECTED_PERMISSION: &str = "Storage write rejected - permission denied.";
const REJECTED_DELETE: &str =
    "Storage delete rejected - not found, version check failed, or permission denied.";

#[derive(Clone, Copy)]
enum OperationKind {
    Write,
    Delete,
}

impl OperationKind {
    const fn internal_message(self) -> &'static str {
        match self {
            Self::Write => "Error writing storage objects.",
            Self::Delete => "Error deleting storage objects.",
        }
    }
}

#[derive(Debug)]
struct ApiError(&'static str);

impl ApiError {
    fn response(self) -> Response {
        gateway_error(400, 3, self.0)
    }
}

/// Preserve duplicate keys and original numeric lexemes before interpreting
/// known protobuf fields. A serde_json::Value alone would silently accept the
/// last duplicate field and could round a fractional permission to an integer.
#[derive(Debug)]
struct JsonObject(Vec<(String, Box<RawValue>)>);

impl<'de> Deserialize<'de> for JsonObject {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = JsonObject;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Self::Value, M::Error> {
                let mut fields = Vec::new();
                while let Some(entry) = map.next_entry()? {
                    fields.push(entry);
                }
                Ok(JsonObject(fields))
            }
        }
        deserializer.deserialize_map(ObjectVisitor)
    }
}

impl JsonObject {
    fn known_fields(
        &self,
        names: &[(&'static str, &'static str)],
    ) -> Result<BTreeMap<&'static str, &RawValue>, ApiError> {
        let mut fields = BTreeMap::new();
        for (name, value) in &self.0 {
            if let Some(&(canonical, _)) = names
                .iter()
                .find(|(canonical, alias)| name.as_str() == *canonical || name.as_str() == *alias)
            {
                // protojson records presence before skipping null values.
                if fields.insert(canonical, value.as_ref()).is_some() {
                    return Err(ApiError("Duplicate storage request field."));
                }
            }
        }
        Ok(fields)
    }
}

pub(crate) fn handle<R: Repository>(
    repository: &mut R,
    request: &Request,
    user: UserId,
) -> Response {
    let path = request.target.split('?').next().unwrap_or("");
    if request.method == "POST" && path == "/v2/storage" {
        if user.is_zero() {
            return gateway_error(401, 16, "Auth token invalid");
        }
        return read_objects(repository, request, user);
    }
    let kind = match (request.method.as_str(), path) {
        ("PUT", "/v2/storage") => OperationKind::Write,
        ("PUT", "/v2/storage/delete") => OperationKind::Delete,
        _ => return gateway_error(404, 5, "Requested resource was not found."),
    };
    if user.is_zero() {
        return gateway_error(401, 16, "Auth token invalid");
    }
    let operations = match decode_operations(request, user, kind) {
        Ok(operations) => operations,
        Err(error) => return error.response(),
    };
    // Upstream returns its empty message without touching storage. With
    // UseProtoNames=true and EmitUnpopulated=false, empty write acks are {}.
    if operations.is_empty() {
        return Response::json(200, b"{}".to_vec());
    }
    let Some(now_ms) = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
    else {
        return gateway_error(500, 13, kind.internal_message());
    };
    match repository.apply_storage_batch(Actor::User(user), &operations, now_ms) {
        Ok(receipts) => match kind {
            OperationKind::Write => write_response(&operations, &receipts),
            OperationKind::Delete => delete_response(&operations, &receipts),
        },
        Err(error) => storage_error(error, kind),
    }
}

fn decode_operations(
    request: &Request,
    user: UserId,
    kind: OperationKind,
) -> Result<Vec<BatchOperation>, ApiError> {
    let (canonical, alias) = match kind {
        OperationKind::Write => ("objects", "objects"),
        OperationKind::Delete => ("object_ids", "objectIds"),
    };
    let objects = decode_object_batch(request, canonical, alias)?;
    match kind {
        OperationKind::Write => {
            // Match the API's ordering: validate every object before any OCC
            // condition is interpreted or any repository mutation is attempted.
            objects
                .iter()
                .map(|object| decode_write(object, user))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .map(ParsedWrite::operation)
                .collect()
        }
        OperationKind::Delete => objects
            .iter()
            .map(|object| decode_delete(object, user))
            .collect(),
    }
}

fn decode_object_batch(
    request: &Request,
    canonical: &'static str,
    alias: &'static str,
) -> Result<Vec<JsonObject>, ApiError> {
    // grpc-gateway's generated body decoder treats EOF as an empty message.
    let object = if request.body.iter().all(|byte| is_json_space(*byte)) {
        JsonObject(Vec::new())
    } else {
        serde_json::from_slice::<JsonObject>(&request.body)
            .map_err(|_| ApiError("Invalid JSON storage request."))?
    };
    let fields = object.known_fields(&[(canonical, alias)])?;
    let Some(raw) = fields.get(canonical).filter(|value| value.get() != "null") else {
        return Ok(Vec::new());
    };
    let objects: Vec<Box<RawValue>> =
        serde_json::from_str(raw.get()).map_err(|_| ApiError("Invalid storage object batch."))?;
    if objects.len() > MAX_BATCH {
        return Err(ApiError("Storage batch must contain at most 100 objects."));
    }
    objects
        .iter()
        .map(|raw| {
            serde_json::from_str::<JsonObject>(raw.get())
                .map_err(|_| ApiError("Invalid storage object."))
        })
        .collect()
}

fn read_objects<R: Repository>(repository: &mut R, request: &Request, user: UserId) -> Response {
    let keys = match decode_object_batch(request, "object_ids", "objectIds").and_then(|objects| {
        objects
            .iter()
            .map(decode_read_key)
            .collect::<Result<Vec<_>, _>>()
    }) {
        Ok(keys) => keys,
        Err(error) => return error.response(),
    };
    if keys.is_empty() {
        return Response::json(200, b"{}".to_vec());
    }
    match repository.read_storage_objects(Actor::User(user), &keys) {
        Ok(objects) => read_response(&keys, &objects, user),
        Err(error) if error.code() == StableCode::ResourceExhausted => {
            storage_resource_error("Error reading storage objects.")
        }
        Err(_) => gateway_error(500, 13, "Error reading storage objects."),
    }
}

fn decode_read_key(object: &JsonObject) -> Result<StorageObjectKey, ApiError> {
    let fields = object.known_fields(&[
        ("collection", "collection"),
        ("key", "key"),
        ("user_id", "userId"),
    ])?;
    let collection = string_field(&fields, "collection")?;
    let key = string_field(&fields, "key")?;
    let owner = string_field(&fields, "user_id")?;
    if collection.is_empty() || key.is_empty() {
        return Err(ApiError(INVALID_KEYS));
    }
    let owner = if owner.is_empty() {
        // ReadStorageObjectId's omitted owner is the global object, not caller.
        UserId::new([0; 16])
    } else {
        parse_uuid(&owner)
            .filter(|user| !user.is_zero())
            .ok_or(ApiError(INVALID_USER))?
    };
    StorageObjectKey::new(collection, key, owner).map_err(|_| ApiError(INVALID_KEYS))
}

/// Independent implementation of the six text formats accepted by the pinned
/// gofrs/uuid v5.4.0 parser. Hex digits are case insensitive; urn:uuid: is exact.
pub(super) fn parse_uuid(text: &str) -> Option<UserId> {
    let bytes = text.as_bytes();
    let body = match bytes.len() {
        32 | 36 => bytes,
        34 | 38 if bytes.first() == Some(&b'{') && bytes.last() == Some(&b'}') => {
            &bytes[1..bytes.len() - 1]
        }
        41 | 45 if bytes.starts_with(b"urn:uuid:") => &bytes[9..],
        _ => return None,
    };
    if body.len() == 36 && [8, 13, 18, 23].iter().any(|index| body[*index] != b'-') {
        return None;
    }
    let mut output = [0_u8; 16];
    let mut index = 0;
    let mut nibble = 0;
    for (position, byte) in body.iter().copied().enumerate() {
        if body.len() == 36 && matches!(position, 8 | 13 | 18 | 23) {
            continue;
        }
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            _ => return None,
        };
        if nibble % 2 == 0 {
            output[index] = digit << 4;
        } else {
            output[index] |= digit;
            index += 1;
        }
        nibble += 1;
    }
    Some(UserId::new(output))
}

fn read_response(
    keys: &[StorageObjectKey],
    objects: &[StoredStorageObject],
    user: UserId,
) -> Response {
    if objects.len() > keys.len() {
        return gateway_error(500, 13, "Error reading storage objects.");
    }
    let mut remaining = BTreeMap::new();
    for key in keys {
        *remaining.entry(key).or_insert(0_usize) += 1;
    }
    let mut encoded = StorageJsonEncoder::new();
    if !objects.is_empty() && encoded.append("{\"objects\":[").is_err() {
        return storage_resource_error("Error reading storage objects.");
    }
    let mut first = true;
    for stored in objects {
        let object = &stored.object;
        let Some(count) = remaining.get_mut(&object.key).filter(|count| **count > 0) else {
            return gateway_error(500, 13, "Error reading storage objects.");
        };
        *count -= 1;
        let allowed = object.read_permission == ReadPermission::Public
            || (object.read_permission == ReadPermission::Owner && object.key.user_id() == user);
        if !allowed {
            return gateway_error(500, 13, "Error reading storage objects.");
        }
        let object = match encode_storage_object(object, &stored.times) {
            Ok(object) => object,
            Err(StorageEncodingError::ResourceExhausted) => {
                return storage_resource_error("Error reading storage objects.")
            }
            Err(StorageEncodingError::DataLoss) => {
                return gateway_error(500, 13, "Error reading storage objects.")
            }
        };
        if (!first && encoded.append(",").is_err()) || encoded.append(&object).is_err() {
            return storage_resource_error("Error reading storage objects.");
        }
        first = false;
    }
    if objects.is_empty() {
        Response::json(200, b"{}".to_vec())
    } else {
        if encoded.append("]}").is_err() {
            return storage_resource_error("Error reading storage objects.");
        }
        Response::json(200, encoded.finish())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum StorageEncodingError {
    DataLoss,
    ResourceExhausted,
}

/// Bound the actual UTF-8 JSON response bytes while serde_json escapes strings.
/// This is a response envelope encoder, never a native JSONB value renderer.
#[derive(Debug)]
pub(super) struct StorageJsonEncoder {
    bytes: Vec<u8>,
    exhausted: bool,
}

impl StorageJsonEncoder {
    pub(super) fn new() -> Self {
        Self {
            bytes: Vec::new(),
            exhausted: false,
        }
    }

    pub(super) fn append(&mut self, value: &str) -> Result<(), StorageEncodingError> {
        io::Write::write_all(self, value.as_bytes())
            .map_err(|_| StorageEncodingError::ResourceExhausted)
    }

    fn string(&mut self, value: &str) -> Result<(), StorageEncodingError> {
        serde_json::to_writer(&mut *self, value).map_err(|_| {
            if self.exhausted {
                StorageEncodingError::ResourceExhausted
            } else {
                StorageEncodingError::DataLoss
            }
        })
    }

    pub(super) fn finish(self) -> Vec<u8> {
        self.bytes
    }
}

impl io::Write for StorageJsonEncoder {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let size = self.bytes.len().checked_add(bytes.len());
        if self.exhausted || size.is_none_or(|size| size > MAX_ENCODED_STORAGE_RESPONSE_BYTES) {
            self.exhausted = true;
            return Err(io::Error::new(
                io::ErrorKind::OutOfMemory,
                "storage_encoded_response_budget_exceeded",
            ));
        }
        let size = size.expect("validated response size");
        if size > self.bytes.capacity() {
            let capacity = self
                .bytes
                .capacity()
                .saturating_mul(2)
                .max(1024)
                .max(size)
                .min(MAX_ENCODED_STORAGE_RESPONSE_BYTES);
            if self
                .bytes
                .try_reserve_exact(capacity - self.bytes.len())
                .is_err()
            {
                self.exhausted = true;
                return Err(io::Error::new(
                    io::ErrorKind::OutOfMemory,
                    "storage_encoded_response_allocation_failed",
                ));
            }
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

pub(super) fn encode_storage_object(
    object: &StorageObject,
    times: &StorageTimes,
) -> Result<String, StorageEncodingError> {
    object.verify_integrity().map_err(|error| {
        if error.code() == StableCode::ResourceExhausted {
            StorageEncodingError::ResourceExhausted
        } else {
            StorageEncodingError::DataLoss
        }
    })?;
    let value = std::str::from_utf8(&object.value).map_err(|_| StorageEncodingError::DataLoss)?;
    let mut fields = vec![
        format!(
            "\"collection\":{}",
            serde_json::Value::from(object.key.collection())
        ),
        format!("\"key\":{}", serde_json::Value::from(object.key.key())),
        format!(
            "\"user_id\":{}",
            serde_json::Value::from(uuid_string(object.key.user_id()))
        ),
    ];
    let mut output = StorageJsonEncoder::new();
    output.append("{")?;
    output.append(&fields.join(","))?;
    fields.clear();
    if !value.is_empty() {
        output.append(",\"value\":")?;
        output.string(value)?;
    }
    if !object.version.as_str().is_empty() {
        fields.push(format!(
            "\"version\":{}",
            serde_json::Value::from(object.version.as_str())
        ));
    }
    if object.read_permission != ReadPermission::None {
        fields.push(format!(
            "\"permission_read\":{}",
            object.read_permission as u8
        ));
    }
    if object.write_permission != WritePermission::None {
        fields.push(format!(
            "\"permission_write\":{}",
            object.write_permission as u8
        ));
    }
    // Pinned upstream read/list projection deliberately drops subsecond precision.
    append_storage_times(&mut fields, times, false).ok_or(StorageEncodingError::DataLoss)?;
    if !fields.is_empty() {
        output.append(",")?;
        output.append(&fields.join(","))?;
    }
    output.append("}")?;
    String::from_utf8(output.finish()).map_err(|_| StorageEncodingError::DataLoss)
}

struct ParsedWrite {
    key: StorageObjectKey,
    value: Vec<u8>,
    version: String,
    read_permission: ReadPermission,
    write_permission: WritePermission,
}

impl ParsedWrite {
    fn operation(self) -> Result<BatchOperation, ApiError> {
        let expected = match self.version.as_str() {
            "" => VersionCheck::Any,
            "*" => VersionCheck::MustNotExist,
            version => VersionCheck::Exact(version.into()),
        };
        Ok(BatchOperation::Write(WriteOperation {
            key: self.key,
            value: self.value,
            expected,
            read_permission: self.read_permission,
            write_permission: self.write_permission,
        }))
    }
}

fn decode_write(object: &JsonObject, user: UserId) -> Result<ParsedWrite, ApiError> {
    let fields = object.known_fields(&[
        ("collection", "collection"),
        ("key", "key"),
        ("value", "value"),
        ("version", "version"),
        ("permission_read", "permissionRead"),
        ("permission_write", "permissionWrite"),
    ])?;
    let collection = string_field(&fields, "collection")?;
    let key = string_field(&fields, "key")?;
    let value = string_field(&fields, "value")?;
    let version = string_field(&fields, "version")?;
    if collection.is_empty() || key.is_empty() || value.is_empty() {
        return Err(ApiError(INVALID_KEYS));
    }
    let read_permission = match permission_field(&fields, "permission_read")? {
        0 => ReadPermission::None,
        1 => ReadPermission::Owner,
        2 => ReadPermission::Public,
        _ => return Err(ApiError(INVALID_READ)),
    };
    let write_permission = match permission_field(&fields, "permission_write")? {
        0 => WritePermission::None,
        1 => WritePermission::Owner,
        _ => return Err(ApiError(INVALID_WRITE)),
    };
    if value.len() > MAX_REQUEST_VALUE_BYTES {
        return Err(ApiError("Storage value exceeds the 1048576 byte limit."));
    }
    if value.bytes().find(|byte| !is_json_space(*byte)) != Some(b'{')
        || serde_json::from_str::<Box<RawValue>>(&value).is_err()
    {
        return Err(ApiError(INVALID_VALUE));
    }
    // WriteStorageObject has no user_id field. DiscardUnknown means supplied
    // owner fields cannot change the authenticated storage owner.
    let key = StorageObjectKey::new(collection, key, user).map_err(|_| ApiError(INVALID_KEYS))?;
    Ok(ParsedWrite {
        key,
        value: value.into_bytes(),
        version,
        read_permission,
        write_permission,
    })
}

fn decode_delete(object: &JsonObject, user: UserId) -> Result<BatchOperation, ApiError> {
    let fields = object.known_fields(&[
        ("collection", "collection"),
        ("key", "key"),
        ("version", "version"),
    ])?;
    let collection = string_field(&fields, "collection")?;
    let key = string_field(&fields, "key")?;
    let version = string_field(&fields, "version")?;
    let key = StorageObjectKey::new(collection, key, user).map_err(|_| ApiError(INVALID_KEYS))?;
    let expected_version = if version.is_empty() {
        None
    } else {
        Some(version.into())
    };
    Ok(BatchOperation::Delete(DeleteOperation {
        key,
        expected_version,
    }))
}

fn string_field(fields: &BTreeMap<&str, &RawValue>, name: &str) -> Result<String, ApiError> {
    match fields.get(name) {
        None => Ok(String::new()),
        Some(value) => serde_json::from_str::<Option<String>>(value.get())
            .map(Option::unwrap_or_default)
            .map_err(|_| ApiError("Invalid storage string field.")),
    }
}

fn permission_field(fields: &BTreeMap<&str, &RawValue>, name: &str) -> Result<i32, ApiError> {
    match fields.get(name) {
        None => Ok(1),
        Some(value) if value.get() == "null" => Ok(1),
        Some(value) => {
            let quoted;
            let number = if value.get().starts_with('"') {
                quoted = serde_json::from_str::<String>(value.get())
                    .map_err(|_| ApiError("Invalid storage int32 field."))?;
                quoted.as_str()
            } else {
                value.get()
            };
            parse_int32(number).ok_or(ApiError("Invalid storage int32 field."))
        }
    }
}

/// Decode a JSON decimal integer exactly, including integral decimal/exponent
/// forms. Floating-point rounding must never alter an access permission.
fn parse_int32(number: &str) -> Option<i32> {
    let bytes = number.as_bytes();
    let mut index = usize::from(bytes.first() == Some(&b'-'));
    let negative = index != 0;
    let first = *bytes.get(index)?;
    if !first.is_ascii_digit() {
        return None;
    }
    let integer_start = index;
    index += 1;
    while bytes.get(index).is_some_and(u8::is_ascii_digit) {
        index += 1;
    }
    if first == b'0' && index - integer_start > 1 {
        return None;
    }
    let mut digits = bytes[integer_start..index].to_vec();
    let mut fractional = 0_i64;
    if bytes.get(index) == Some(&b'.') {
        index += 1;
        let start = index;
        while bytes.get(index).is_some_and(u8::is_ascii_digit) {
            index += 1;
        }
        if start == index {
            return None;
        }
        digits.extend_from_slice(&bytes[start..index]);
        fractional = i64::try_from(index - start).ok()?;
    }
    let mut exponent = 0_i64;
    if bytes
        .get(index)
        .is_some_and(|byte| matches!(byte, b'e' | b'E'))
    {
        index += 1;
        let exponent_negative = bytes.get(index) == Some(&b'-');
        if bytes
            .get(index)
            .is_some_and(|byte| matches!(byte, b'-' | b'+'))
        {
            index += 1;
        }
        let start = index;
        while let Some(byte) = bytes.get(index).filter(|byte| byte.is_ascii_digit()) {
            // Magnitudes beyond the bounded input cannot cancel its fraction.
            exponent = exponent
                .saturating_mul(10)
                .saturating_add(i64::from(*byte - b'0'));
            index += 1;
        }
        if start == index {
            return None;
        }
        if exponent_negative {
            exponent = -exponent;
        }
    }
    if index != bytes.len() {
        return None;
    }
    let start = digits.iter().position(|byte| *byte != b'0');
    let Some(start) = start else {
        return Some(0);
    };
    let mut end = digits.len();
    let mut scale = exponent.saturating_sub(fractional);
    while digits[end - 1] == b'0' {
        end -= 1;
        scale = scale.saturating_add(1);
    }
    if scale < 0 || i64::try_from(end - start).ok()?.saturating_add(scale) > 10 {
        return None;
    }
    let mut magnitude = 0_i64;
    for digit in &digits[start..end] {
        magnitude = magnitude * 10 + i64::from(*digit - b'0');
    }
    for _ in 0..scale {
        magnitude *= 10;
    }
    i32::try_from(if negative { -magnitude } else { magnitude }).ok()
}

const fn is_json_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\r' | b'\n')
}

fn write_response(operations: &[BatchOperation], receipts: &[MutationReceipt]) -> Response {
    if receipts.len() != operations.len() {
        return gateway_error(500, 13, "Error writing storage objects.");
    }
    let mut acks = Vec::with_capacity(receipts.len());
    for (operation, stored) in operations.iter().zip(receipts) {
        let receipt = &stored.receipt;
        let BatchOperation::Write(write) = operation else {
            return gateway_error(500, 13, "Error writing storage objects.");
        };
        let Some(version) = receipt.current_version else {
            return gateway_error(500, 13, "Error writing storage objects.");
        };
        let expected_previous = match &write.expected {
            VersionCheck::Any => true,
            VersionCheck::MustNotExist => receipt.previous_version.is_none(),
            VersionCheck::Exact(expected) => receipt
                .previous_version
                .as_ref()
                .is_some_and(|previous| previous.as_str() == expected.as_str()),
        };
        if receipt.key != write.key
            || version != ContentVersion::from_value(&write.value)
            || !expected_previous
        {
            return gateway_error(500, 13, "Error writing storage objects.");
        }
        let mut fields = vec![
            format!(
                "\"collection\":{}",
                serde_json::Value::from(receipt.key.collection())
            ),
            format!("\"key\":{}", serde_json::Value::from(receipt.key.key())),
            format!("\"version\":{}", serde_json::Value::from(version.as_str())),
            format!(
                "\"user_id\":{}",
                serde_json::Value::from(uuid_string(receipt.key.user_id()))
            ),
        ];
        if append_storage_times(&mut fields, &stored.times, true).is_none()
            || (receipt.previous_version.is_none()
                && (stored.times.create.is_none()
                    || stored.times.update.is_none()
                    || stored.times.create != stored.times.update))
            || (stored.times.update.is_none()
                && (matches!(&write.expected, VersionCheck::Exact(_))
                    || receipt
                        .previous_version
                        .as_ref()
                        .is_none_or(|previous| previous.as_str() != version.as_str())))
        {
            return gateway_error(500, 13, "Error writing storage objects.");
        }
        acks.push(format!("{{{}}}", fields.join(",")));
    }
    Response::json(200, format!("{{\"acks\":[{}]}}", acks.join(",")))
}

fn delete_response(operations: &[BatchOperation], receipts: &[MutationReceipt]) -> Response {
    if receipts.len() != operations.len()
        || operations.iter().zip(receipts).any(|(operation, stored)| {
            let receipt = &stored.receipt;
            let BatchOperation::Delete(delete) = operation else {
                return true;
            };
            operation.key() != &receipt.key
                || receipt.current_version.is_some()
                || receipt.previous_version.is_none()
                || delete.expected_version.as_ref().is_some_and(|expected| {
                    receipt
                        .previous_version
                        .as_ref()
                        .map(|previous| previous.as_str())
                        != Some(expected.as_str())
                })
        })
    {
        return gateway_error(500, 13, "Error deleting storage objects.");
    }
    Response::json(200, b"{}".to_vec())
}

/// Format only normalized protobuf timestamps. The reviewed prost-types codec
/// owns Gregorian calendar conversion and RFC3339 0/3/6/9 fractional digits.
fn append_storage_times(
    fields: &mut Vec<String>,
    times: &StorageTimes,
    precise: bool,
) -> Option<()> {
    for (name, timestamp) in [("create_time", times.create), ("update_time", times.update)] {
        let Some(StorageTimestamp { seconds, nanos }) = timestamp else {
            continue;
        };
        if !(-62_135_596_800..=253_402_300_799).contains(&seconds) || nanos >= 1_000_000_000 {
            return None;
        }
        let timestamp = prost_types::Timestamp {
            seconds,
            nanos: if precise {
                i32::try_from(nanos).ok()?
            } else {
                0
            },
        };
        fields.push(format!(
            "\"{name}\":{}",
            serde_json::Value::from(timestamp.to_string())
        ));
    }
    Some(())
}

fn uuid_string(user: UserId) -> String {
    let hex = encode_hex(user.as_bytes());
    format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..],
    )
}

fn storage_error(error: DomainError, kind: OperationKind) -> Response {
    match (kind, error.code()) {
        (OperationKind::Write, StableCode::AlreadyExists | StableCode::FailedPrecondition) => {
            gateway_error(400, 3, REJECTED_VERSION)
        }
        (OperationKind::Write, StableCode::PermissionDenied) => {
            gateway_error(400, 3, REJECTED_PERMISSION)
        }
        (
            OperationKind::Delete,
            StableCode::NotFound
            | StableCode::AlreadyExists
            | StableCode::FailedPrecondition
            | StableCode::PermissionDenied,
        ) => gateway_error(400, 3, REJECTED_DELETE),
        (_, StableCode::InvalidArgument) => gateway_error(400, 3, "Invalid storage request."),
        (_, StableCode::ResourceExhausted) => storage_resource_error(kind.internal_message()),
        // Repository availability errors are redacted like all internal errors.
        // Retryable SQL/transport failures must not reveal schema or key data.
        _ => gateway_error(500, 13, kind.internal_message()),
    }
}

pub(crate) fn authentication_error(error: DomainError) -> Response {
    if error.code() == StableCode::Unavailable
        && error.reason() == "database_operation_deadline_exceeded"
    {
        return gateway_error(504, 4, "Session authentication unavailable");
    }
    match error.code() {
        StableCode::Unimplemented => gateway_error(501, 12, "Session authentication unavailable"),
        StableCode::Unavailable | StableCode::Aborted => {
            gateway_error(503, 14, "Session authentication unavailable")
        }
        StableCode::Internal | StableCode::DataLoss => {
            gateway_error(500, 13, "Session authentication unavailable")
        }
        StableCode::ResourceExhausted => {
            gateway_error(429, 8, "Session authentication unavailable")
        }
        _ => gateway_error(401, 16, "Auth token invalid"),
    }
}

pub(crate) fn gateway_error(status: u16, code: u8, message: &str) -> Response {
    Response::json(
        status,
        format!(
            "{{\"code\":{code},\"message\":{}}}",
            serde_json::Value::from(message),
        ),
    )
}

pub(super) fn storage_resource_error(message: &str) -> Response {
    gateway_error(429, 8, message)
}

#[cfg(test)]
mod tests {
    use trnm_contracts::{Digest32, RetryClass};
    use trnm_persistence_pg::{
        CollisionWitness, CommitOutcome, CommitRequest, EntityHead, EntityId, IntegrityDigest,
        PublicVersion, StorageState,
    };

    use super::*;
    use trnm_persistence_pg::StorageMutationReceipt as CoreReceipt;

    fn stored_receipt(receipt: CoreReceipt) -> MutationReceipt {
        let timestamp = StorageTimestamp {
            seconds: 1_700_000_000,
            nanos: 123_456_000,
        };
        MutationReceipt {
            receipt,
            times: StorageTimes {
                create: Some(timestamp),
                update: Some(timestamp),
            },
        }
    }

    fn stored_object(object: StorageObject) -> StoredStorageObject {
        StoredStorageObject {
            object,
            times: StorageTimes {
                create: None,
                update: None,
            },
        }
    }

    #[test]
    fn storage_timestamp_json_matches_protobuf_range_precision_and_pre_epoch() {
        for (seconds, nanos, expected) in [
            (0, 0, "1970-01-01T00:00:00Z"),
            (0, 123_000_000, "1970-01-01T00:00:00.123Z"),
            (0, 123_456_000, "1970-01-01T00:00:00.123456Z"),
            (0, 123_456_789, "1970-01-01T00:00:00.123456789Z"),
            (-1, 999_999_000, "1969-12-31T23:59:59.999999Z"),
            (-62_135_596_800, 0, "0001-01-01T00:00:00Z"),
            (253_402_300_799, 999_999_000, "9999-12-31T23:59:59.999999Z"),
            (1_709_164_800, 0, "2024-02-29T00:00:00Z"),
        ] {
            let times = StorageTimes {
                create: Some(StorageTimestamp { seconds, nanos }),
                update: None,
            };
            let mut fields = Vec::new();
            assert_eq!(append_storage_times(&mut fields, &times, true), Some(()));
            assert_eq!(fields, [format!("\"create_time\":\"{expected}\"")]);
            let mut seconds_only = Vec::new();
            assert_eq!(
                append_storage_times(&mut seconds_only, &times, false),
                Some(())
            );
            let expected_second = expected.split('.').next().unwrap().trim_end_matches('Z');
            assert_eq!(
                seconds_only,
                [format!("\"create_time\":\"{expected_second}Z\"")]
            );
        }
        let mut fields = Vec::new();
        assert_eq!(
            append_storage_times(
                &mut fields,
                &StorageTimes {
                    create: None,
                    update: None
                },
                true
            ),
            Some(())
        );
        assert!(fields.is_empty(), "unknown historical time remains absent");
        for (seconds, nanos) in [
            (-62_135_596_801, 0),
            (253_402_300_800, 0),
            (0, 1_000_000_000),
            (i64::MIN, 0),
            (i64::MAX, 0),
        ] {
            let times = StorageTimes {
                create: Some(StorageTimestamp { seconds, nanos }),
                update: None,
            };
            assert_eq!(append_storage_times(&mut Vec::new(), &times, true), None);
            assert_eq!(append_storage_times(&mut Vec::new(), &times, false), None);
        }
    }

    #[test]
    fn new_storage_ack_requires_both_times_and_historical_unknown_is_not_fabricated() {
        let operations = decode_operations(
            &request("/v2/storage", &write("k", "{}", "")),
            user(),
            OperationKind::Write,
        )
        .unwrap();
        let mut stored = stored_receipt(CoreReceipt {
            key: operations[0].key().clone(),
            previous_version: None,
            current_version: Some(ContentVersion::from_value(b"{}")),
        });
        let response = write_response(&operations, std::slice::from_ref(&stored));
        assert_eq!(response.status, 200);
        assert_eq!(
            body(&response)["acks"][0]["create_time"],
            "2023-11-14T22:13:20.123456Z"
        );
        stored.times.create = None;
        assert_eq!(
            write_response(&operations, std::slice::from_ref(&stored)).status,
            500
        );
        stored.receipt.previous_version = Some(ContentVersion::from_value(b"{}").into());
        let response = write_response(&operations, &[stored]);
        assert_eq!(response.status, 200);
        let ack = &body(&response)["acks"][0];
        assert!(ack.get("create_time").is_none());
        assert_eq!(ack["update_time"], "2023-11-14T22:13:20.123456Z");
    }

    #[test]
    fn storage_ack_rejects_missing_effective_update_time_and_unequal_insert_pair() {
        let operations = decode_operations(
            &request("/v2/storage", &write("k", "{}", "")),
            user(),
            OperationKind::Write,
        )
        .unwrap();
        let version = ContentVersion::from_value(b"{}");
        let mut stored = stored_receipt(CoreReceipt {
            key: operations[0].key().clone(),
            previous_version: None,
            current_version: Some(version),
        });
        stored.times.update.as_mut().unwrap().nanos = 0;
        assert_eq!(
            write_response(&operations, std::slice::from_ref(&stored)).status,
            500
        );
        stored.receipt.previous_version = Some(version.into());
        stored.times = StorageTimes {
            create: None,
            update: None,
        };
        assert_eq!(
            write_response(&operations, std::slice::from_ref(&stored)).status,
            200,
            "a blind legacy no-op may still have unknown history"
        );
        let exact = decode_operations(
            &request("/v2/storage", &write("k", "{}", version.as_str())),
            user(),
            OperationKind::Write,
        )
        .unwrap();
        assert_eq!(
            write_response(&exact, std::slice::from_ref(&stored)).status,
            500,
            "an exact write always changes update time"
        );
        stored.receipt.previous_version =
            Some(ContentVersion::from_value(b"{\"old\":true}").into());
        assert_eq!(
            write_response(&operations, &[stored]).status,
            500,
            "a changed value cannot return an unknown update time"
        );
    }

    #[derive(Debug, Default)]
    struct TestRepository {
        storage: StorageState,
        calls: usize,
        actor: Option<Actor>,
        operations: Vec<BatchOperation>,
        failure: Option<DomainError>,
        read_calls: usize,
        read_keys: Vec<StorageObjectKey>,
        read_override: Option<Vec<StorageObject>>,
    }

    impl Repository for TestRepository {
        fn bootstrap_entity(
            &mut self,
            _: EntityId,
            _: u64,
            _: Digest32,
            _: u64,
        ) -> Result<EntityHead, DomainError> {
            unreachable!("storage must not bootstrap authority")
        }

        fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
            unreachable!("storage must not commit authority commands")
        }

        fn apply_storage_batch(
            &mut self,
            actor: Actor,
            operations: &[BatchOperation],
            updated_at_ms: u64,
        ) -> Result<Vec<MutationReceipt>, DomainError> {
            assert!(updated_at_ms > 0);
            self.calls += 1;
            self.actor = Some(actor);
            self.operations = operations.to_vec();
            if let Some(error) = self.failure {
                return Err(error);
            }
            self.storage
                .apply_batch(actor, operations)
                .map(|receipts| receipts.into_iter().map(stored_receipt).collect())
        }

        fn read_storage_objects(
            &mut self,
            actor: Actor,
            keys: &[StorageObjectKey],
        ) -> Result<Vec<StoredStorageObject>, DomainError> {
            self.read_calls += 1;
            self.actor = Some(actor);
            self.read_keys = keys.to_vec();
            if let Some(error) = self.failure {
                return Err(error);
            }
            if let Some(objects) = &self.read_override {
                return Ok(objects.iter().cloned().map(stored_object).collect());
            }
            let mut objects = Vec::new();
            for key in keys {
                match self.storage.read(actor, key) {
                    Ok(object) => objects.push(object),
                    Err(error)
                        if matches!(
                            error.code(),
                            StableCode::NotFound | StableCode::PermissionDenied
                        ) => {}
                    Err(error) => return Err(error),
                }
            }
            Ok(objects.into_iter().map(stored_object).collect())
        }
    }

    fn user() -> UserId {
        UserId::new([0x12; 16])
    }

    fn request(target: &str, body: &str) -> Request {
        Request::new("PUT", target, BTreeMap::new(), body.as_bytes())
    }

    fn body(response: &Response) -> serde_json::Value {
        serde_json::from_slice(&response.body).unwrap()
    }

    fn write(key: &str, value: &str, version: &str) -> String {
        serde_json::json!({"objects": [{
            "collection": "profile", "key": key, "value": value, "version": version
        }]})
        .to_string()
    }

    #[test]
    fn writes_exact_value_bytes_and_binds_owner_to_principal() {
        let mut repository = TestRepository::default();
        let value = " { \"b\": 2, \"a\": 1 }\n";
        let input = serde_json::json!({"objects": [{
            "collection": "profile", "key": "main", "value": value,
            "user_id": "00000000-0000-0000-0000-000000000000",
            "userId": "anything", "ignored": {"nested": true}
        }]})
        .to_string();
        let response = handle(&mut repository, &request("/v2/storage", &input), user());
        assert_eq!(response.status, 200);
        assert_eq!(repository.actor, Some(Actor::User(user())));
        let BatchOperation::Write(operation) = &repository.operations[0] else {
            panic!()
        };
        assert_eq!(operation.key.user_id(), user());
        assert_eq!(operation.value, value.as_bytes());
        assert_eq!(operation.read_permission, ReadPermission::Owner);
        assert_eq!(operation.write_permission, WritePermission::Owner);
        assert_eq!(
            body(&response)["acks"][0]["version"],
            ContentVersion::from_value(value.as_bytes()).as_str()
        );
        assert_eq!(
            body(&response)["acks"][0]["user_id"],
            "12121212-1212-1212-1212-121212121212"
        );
    }

    #[test]
    fn accepts_protojson_aliases_integer_forms_and_null_defaults() {
        let mut repository = TestRepository::default();
        let response = handle(
            &mut repository,
            &request(
                "/v2/storage",
                r#"{"objects":[{"collection":"c","key":"k","value":"{}","permissionRead":"2e0","permission_write":1.0,"version":null}]}"#,
            ),
            user(),
        );
        assert_eq!(response.status, 200);
        let BatchOperation::Write(operation) = &repository.operations[0] else {
            panic!()
        };
        assert_eq!(operation.read_permission, ReadPermission::Public);
        assert_eq!(operation.write_permission, WritePermission::Owner);
        assert_eq!(operation.expected, VersionCheck::Any);
        assert_eq!(
            permission_field(&JsonObject(Vec::new()).known_fields(&[]).unwrap(), "x").unwrap(),
            1
        );
        assert_eq!(parse_int32("1.0000000000000001"), None);
    }

    #[test]
    fn exact_int32_parser_handles_range_zero_and_integral_exponents() {
        for (input, expected) in [
            ("0", 0),
            ("-0e999999999999999", 0),
            ("1.000e0", 1),
            ("10e-1", 1),
            ("2147483647", i32::MAX),
            ("-2147483648", i32::MIN),
        ] {
            assert_eq!(parse_int32(input), Some(expected), "{input}");
        }
        for input in [
            "",
            "+1",
            "01",
            " 1",
            "1 ",
            "true",
            "2147483648",
            "-2147483649",
            "1e1000",
            "1e-1000",
            "1.1",
            "1.0000000000000001",
            "1e",
            "1.",
        ] {
            assert_eq!(parse_int32(input), None, "{input}");
        }
    }

    #[test]
    fn empty_and_null_batches_skip_repository_and_omit_empty_acks() {
        for target in ["/v2/storage", "/v2/storage/delete"] {
            for input in [
                "",
                " \t\r\n",
                "{}",
                r#"{"objects":[],"object_ids":[]}"#,
                r#"{"objects":null,"objectIds":null}"#,
            ] {
                let mut repository = TestRepository::default();
                let response = handle(&mut repository, &request(target, input), user());
                assert_eq!(response.status, 200, "{target} {input}");
                assert_eq!(response.body, b"{}");
                assert_eq!(repository.calls, 0);
            }
        }
    }

    #[test]
    fn duplicate_known_fields_and_aliases_reject_before_storage() {
        for input in [
            r#"{"objects":null,"objects":[]}"#,
            r#"{"objects":[{"collection":"c","collection":"d","key":"k","value":"{}"}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"{}","permission_read":null,"permissionRead":1}]}"#,
        ] {
            let mut repository = TestRepository::default();
            let response = handle(&mut repository, &request("/v2/storage", input), user());
            assert_eq!(response.status, 400);
            assert_eq!(body(&response)["code"], 3);
            assert_eq!(repository.calls, 0);
        }
        let mut repository = TestRepository::default();
        let response = handle(
            &mut repository,
            &request("/v2/storage/delete", r#"{"object_ids":[],"objectIds":[]}"#),
            user(),
        );
        assert_eq!(response.status, 400);
        assert_eq!(repository.calls, 0);
    }

    #[test]
    fn duplicate_unknown_fields_and_embedded_json_keys_are_allowed() {
        let mut repository = TestRepository::default();
        let input = r#"{"unknown":1,"unknown":2,"objects":[{"collection":"c","key":"k","value":"{\"x\":1,\"x\":2,\"huge\":1e9999}","extra":1,"extra":2}]}"#;
        assert_eq!(
            handle(&mut repository, &request("/v2/storage", input), user()).status,
            200
        );
    }

    #[test]
    fn missing_keys_invalid_permissions_and_nonobject_values_fail_before_storage() {
        for input in [
            r#"{"objects":[{"key":"k","value":"{}"}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":""}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"[]"}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"null"}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"{broken}"}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"{}","permissionRead":3}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"{}","permissionWrite":2}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":"{}","permissionRead":1.0000000000000001}]}"#,
            r#"{"objects":[{"collection":"c","key":"k","value":{}}]}"#,
        ] {
            let mut repository = TestRepository::default();
            let response = handle(&mut repository, &request("/v2/storage", input), user());
            assert_eq!(response.status, 400, "{input}");
            assert_eq!(body(&response)["code"], 3);
            assert_eq!(repository.calls, 0);
        }
    }

    #[test]
    fn malformed_json_nonobject_messages_and_null_elements_fail() {
        for input in [
            "null",
            "[]",
            "{}{}",
            "{",
            "{\"objects\":[null]}",
            "{\"objects\":{}}",
            "{\"objects\":[1]}",
        ] {
            let mut repository = TestRepository::default();
            assert_eq!(
                handle(&mut repository, &request("/v2/storage", input), user()).status,
                400,
                "{input}"
            );
            assert_eq!(repository.calls, 0);
        }
    }

    #[test]
    fn batch_and_value_boundaries_are_enforced_before_repository() {
        let mut repository = TestRepository::default();
        let object = serde_json::json!({"collection":"c","key":"k","value":"{}"});
        let input = serde_json::json!({"objects": vec![object; MAX_BATCH + 1]}).to_string();
        assert_eq!(
            handle(&mut repository, &request("/v2/storage", &input), user()).status,
            400
        );
        let oversized = format!("{{\"x\":\"{}\"}}", "x".repeat(MAX_REQUEST_VALUE_BYTES));
        assert_eq!(
            handle(
                &mut repository,
                &request("/v2/storage", &write("k", &oversized, "")),
                user()
            )
            .status,
            400
        );
        assert_eq!(repository.calls, 0);
        let objects = (0..MAX_BATCH)
            .map(|index| {
                serde_json::json!({"collection":"c","key":format!("k{index}"),"value":"{}"})
            })
            .collect::<Vec<_>>();
        let input = serde_json::json!({"objects":objects}).to_string();
        assert_eq!(
            handle(&mut repository, &request("/v2/storage", &input), user()).status,
            200
        );
        assert_eq!(repository.operations.len(), MAX_BATCH);
        let boundary = format!("{{\"x\":\"{}\"}}", "x".repeat(MAX_REQUEST_VALUE_BYTES - 8));
        assert_eq!(boundary.len(), MAX_REQUEST_VALUE_BYTES);
        assert_eq!(
            handle(
                &mut repository,
                &request("/v2/storage", &write("k", &boundary, "")),
                user()
            )
            .status,
            200
        );
    }

    #[test]
    fn wildcard_exact_version_and_acl_failures_use_upstream_gateway_errors() {
        let mut repository = TestRepository::default();
        assert_eq!(
            handle(
                &mut repository,
                &request("/v2/storage", &write("k", "{}", "*")),
                user()
            )
            .status,
            200
        );
        let response = handle(
            &mut repository,
            &request("/v2/storage", &write("k", "{}", "*")),
            user(),
        );
        assert_eq!(response.status, 400);
        assert_eq!(
            body(&response),
            serde_json::json!({"code":3,"message":REJECTED_VERSION})
        );
        let response = handle(
            &mut repository,
            &request("/v2/storage", &write("k", "{}", "arbitrary nonhex")),
            user(),
        );
        assert_eq!(body(&response)["message"], REJECTED_VERSION);
        let response = handle(
            &mut repository,
            &request(
                "/v2/storage",
                &write(
                    "k",
                    "{\"new\":1}",
                    ContentVersion::from_value(b"{}").as_str(),
                ),
            ),
            user(),
        );
        assert_eq!(response.status, 200);
        repository.failure = Some(DomainError::new(
            StableCode::PermissionDenied,
            "private",
            RetryClass::Never,
        ));
        let response = handle(
            &mut repository,
            &request("/v2/storage", &write("k", "{}", "")),
            user(),
        );
        assert_eq!(body(&response)["message"], REJECTED_PERMISSION);
        assert_eq!(body(&response)["code"], 3);
    }

    #[test]
    fn delete_uses_official_put_route_aliases_and_binds_owner() {
        let mut repository = TestRepository::default();
        handle(
            &mut repository,
            &request("/v2/storage", &write("k", "{}", "")),
            user(),
        );
        let input = serde_json::json!({"objectIds":[{
            "collection":"profile", "key":"k", "version":ContentVersion::from_value(b"{}").as_str(),
            "user_id":"00000000-0000-0000-0000-000000000000"
        }]})
        .to_string();
        let response = handle(
            &mut repository,
            &request("/v2/storage/delete", &input),
            user(),
        );
        assert_eq!(response.body, b"{}");
        assert_eq!(response.status, 200);
        assert_eq!(repository.storage.object_count(), 0);
        assert_eq!(repository.operations[0].key().user_id(), user());
        let response = handle(
            &mut repository,
            &request("/v2/storage/delete", &input),
            user(),
        );
        assert_eq!(response.status, 400);
        assert_eq!(body(&response)["message"], REJECTED_DELETE);
    }

    #[test]
    fn storage_batch_failure_does_not_acknowledge_or_partially_mutate() {
        let mut repository = TestRepository::default();
        handle(
            &mut repository,
            &request("/v2/storage", &write("old", "{}", "")),
            user(),
        );
        let before = repository.storage.clone();
        let input = serde_json::json!({"objects":[
            {"collection":"profile","key":"new","value":"{}"},
            {"collection":"profile","key":"old","value":"{}","version":"*"}
        ]})
        .to_string();
        let response = handle(&mut repository, &request("/v2/storage", &input), user());
        assert_eq!(response.status, 400);
        assert_eq!(repository.storage, before);
        assert!(body(&response).get("acks").is_none());
    }

    #[test]
    fn internal_and_authentication_errors_never_expose_private_reasons() {
        let private = "private schema credentials and SQL";
        let mut repository = TestRepository {
            failure: Some(DomainError::new(
                StableCode::DataLoss,
                private,
                RetryClass::Never,
            )),
            ..TestRepository::default()
        };
        let response = handle(
            &mut repository,
            &request("/v2/storage", &write("k", "{}", "")),
            user(),
        );
        assert_eq!(response.status, 500);
        assert_eq!(body(&response)["code"], 13);
        assert_eq!(body(&response)["message"], "Error writing storage objects.");
        assert!(!String::from_utf8(response.body).unwrap().contains(private));
        let response = authentication_error(DomainError::new(
            StableCode::NotFound,
            private,
            RetryClass::Never,
        ));
        assert_eq!(response.status, 401);
        assert_eq!(body(&response)["message"], "Auth token invalid");
        let response = authentication_error(DomainError::new(
            StableCode::Unavailable,
            "database_operation_deadline_exceeded",
            RetryClass::SafeBackoff,
        ));
        assert_eq!(response.status, 504);
        assert_eq!(body(&response)["code"], 4);
        assert!(!String::from_utf8(response.body)
            .unwrap()
            .contains("database_operation"));
    }

    #[test]
    fn inconsistent_delete_receipts_never_acknowledge_success() {
        let key = StorageObjectKey::new("profile", "k", user()).unwrap();
        let operation = BatchOperation::Delete(DeleteOperation {
            key: key.clone(),
            expected_version: None,
        });
        let receipt = stored_receipt(CoreReceipt {
            key,
            previous_version: Some(ContentVersion::from_value(b"{}").into()),
            current_version: None,
        });
        assert_eq!(
            delete_response(std::slice::from_ref(&operation), &[]).status,
            500
        );
        let mut changed = receipt.clone();
        changed.receipt.current_version = Some(ContentVersion::from_value(b"{}"));
        assert_eq!(
            delete_response(std::slice::from_ref(&operation), &[changed]).status,
            500
        );
        let mut changed = receipt.clone();
        changed.receipt.key = StorageObjectKey::new("profile", "other", user()).unwrap();
        assert_eq!(
            delete_response(std::slice::from_ref(&operation), &[changed]).status,
            500
        );
        let mut changed = receipt.clone();
        changed.receipt.previous_version = None;
        assert_eq!(
            delete_response(std::slice::from_ref(&operation), &[changed]).status,
            500
        );
        let mut conditional = operation.clone();
        let BatchOperation::Delete(delete) = &mut conditional else {
            panic!()
        };
        delete.expected_version = Some(
            ContentVersion::from_value(b"{}")
                .as_str()
                .to_ascii_uppercase()
                .into(),
        );
        assert_eq!(
            delete_response(
                std::slice::from_ref(&conditional),
                std::slice::from_ref(&receipt)
            )
            .status,
            500,
            "an opaque uppercase condition cannot validate a lowercase stored receipt"
        );
        let BatchOperation::Delete(delete) = &mut conditional else {
            panic!()
        };
        delete.expected_version = Some(ContentVersion::from_value(b"stale").into());
        assert_eq!(
            delete_response(&[conditional], std::slice::from_ref(&receipt)).status,
            500
        );
        let response = delete_response(&[operation], &[receipt]);
        assert_eq!(response.status, 200);
        assert_eq!(response.body, b"{}");
    }

    #[test]
    fn inconsistent_write_receipts_never_acknowledge_occ_success() {
        let request = request("/v2/storage", &write("k", "{}", "*"));
        let operations = decode_operations(&request, user(), OperationKind::Write).unwrap();
        let receipt = stored_receipt(CoreReceipt {
            key: operations[0].key().clone(),
            previous_version: Some(ContentVersion::from_value(b"{}").into()),
            current_version: Some(ContentVersion::from_value(b"{}")),
        });
        assert_eq!(
            write_response(&operations, std::slice::from_ref(&receipt)).status,
            500
        );
        let mut operations = operations;
        let BatchOperation::Write(write) = &mut operations[0] else {
            panic!()
        };
        write.expected = VersionCheck::Exact(
            ContentVersion::from_value(b"{}")
                .as_str()
                .to_ascii_uppercase()
                .into(),
        );
        assert_eq!(
            write_response(&operations, std::slice::from_ref(&receipt)).status,
            500,
            "an opaque uppercase condition cannot validate a lowercase stored receipt"
        );
        let BatchOperation::Write(write) = &mut operations[0] else {
            panic!()
        };
        write.expected = VersionCheck::Exact(ContentVersion::from_value(b"stale").into());
        assert_eq!(
            write_response(&operations, std::slice::from_ref(&receipt)).status,
            500
        );
        let mut changed = receipt;
        changed.receipt.previous_version = Some(ContentVersion::from_value(b"stale").into());
        assert_eq!(write_response(&operations, &[changed]).status, 200);
    }

    fn read_request(body: &str) -> Request {
        Request::new("POST", "/v2/storage", BTreeMap::new(), body.as_bytes())
    }

    fn seed_read_object(
        repository: &mut TestRepository,
        owner: UserId,
        key: &str,
        value: &[u8],
        read_permission: ReadPermission,
        write_permission: WritePermission,
    ) -> StorageObject {
        let key = StorageObjectKey::new("profile", key, owner).unwrap();
        repository
            .storage
            .apply_batch(
                Actor::Server,
                &[BatchOperation::Write(WriteOperation {
                    key: key.clone(),
                    value: value.to_vec(),
                    expected: VersionCheck::Any,
                    read_permission,
                    write_permission,
                })],
            )
            .unwrap();
        repository.storage.read(Actor::Server, &key).unwrap()
    }

    include!("storage_api_projection_tests.rs");

    #[test]
    fn read_preserves_value_bytes_uses_uuid_owner_and_calls_repository_once() {
        let mut repository = TestRepository::default();
        let value = b" { \"z\": 2, \"a\": 1 }\n";
        seed_read_object(
            &mut repository,
            user(),
            "k",
            value,
            ReadPermission::Owner,
            WritePermission::Owner,
        );
        let input = serde_json::json!({"objectIds":[{
            "collection":"profile","key":"k","userId":uuid_string(user()),
            "unknown":"discarded"
        }]})
        .to_string();
        let response = handle(&mut repository, &read_request(&input), user());
        assert_eq!(response.status, 200);
        assert_eq!(repository.read_calls, 1);
        assert_eq!(repository.calls, 0);
        assert_eq!(repository.actor, Some(Actor::User(user())));
        assert_eq!(repository.read_keys[0].user_id(), user());
        let result = body(&response);
        let object = &result["objects"][0];
        assert_eq!(object["value"].as_str().unwrap().as_bytes(), value);
        assert_eq!(object["user_id"], uuid_string(user()));
        assert_eq!(
            object["version"],
            ContentVersion::from_value(value).as_str()
        );
        assert_eq!(object["permission_read"], 1);
        assert_eq!(object["permission_write"], 1);
        assert!(object.get("create_time").is_none());
        assert!(object.get("update_time").is_none());
    }

    #[test]
    fn read_missing_owner_selects_global_object_and_omits_zero_scalar_permissions() {
        let mut repository = TestRepository::default();
        let global = UserId::new([0; 16]);
        seed_read_object(
            &mut repository,
            global,
            "k",
            b"{\"global\":1}",
            ReadPermission::Public,
            WritePermission::None,
        );
        seed_read_object(
            &mut repository,
            user(),
            "k",
            b"{\"private\":1}",
            ReadPermission::Owner,
            WritePermission::Owner,
        );
        for owner in ["", ",\"user_id\":null", ",\"userId\":\"\""] {
            let input =
                format!("{{\"object_ids\":[{{\"collection\":\"profile\",\"key\":\"k\"{owner}}}]}}");
            let response = handle(&mut repository, &read_request(&input), user());
            assert_eq!(response.status, 200);
            assert_eq!(repository.read_keys[0].user_id(), global);
            let result = body(&response);
            assert_eq!(result["objects"][0]["value"], "{\"global\":1}");
            assert_eq!(
                result["objects"][0]["user_id"],
                "00000000-0000-0000-0000-000000000000"
            );
            assert_eq!(result["objects"][0]["permission_read"], 2);
            assert!(result["objects"][0].get("permission_write").is_none());
        }
    }

    #[test]
    fn read_omits_missing_and_acl_hidden_objects() {
        let mut repository = TestRepository::default();
        let other = UserId::new([0x34; 16]);
        seed_read_object(
            &mut repository,
            other,
            "hidden",
            b"{}",
            ReadPermission::Owner,
            WritePermission::Owner,
        );
        seed_read_object(
            &mut repository,
            user(),
            "none",
            b"{}",
            ReadPermission::None,
            WritePermission::Owner,
        );
        seed_read_object(
            &mut repository,
            other,
            "public",
            b"{}",
            ReadPermission::Public,
            WritePermission::Owner,
        );
        let input = serde_json::json!({"object_ids":[
            {"collection":"profile","key":"hidden","user_id":uuid_string(other)},
            {"collection":"profile","key":"none","user_id":uuid_string(user())},
            {"collection":"profile","key":"missing","user_id":uuid_string(user())},
            {"collection":"profile","key":"public","user_id":uuid_string(other)}
        ]})
        .to_string();
        let response = handle(&mut repository, &read_request(&input), user());
        assert_eq!(response.status, 200);
        assert_eq!(body(&response)["objects"].as_array().unwrap().len(), 1);
        assert_eq!(body(&response)["objects"][0]["key"], "public");
        assert_eq!(repository.read_calls, 1);
        let input = serde_json::json!({"object_ids":[{"collection":"profile","key":"missing"}]})
            .to_string();
        assert_eq!(
            handle(&mut repository, &read_request(&input), user()).body,
            b"{}"
        );
    }

    #[test]
    fn read_accepts_six_pinned_uuid_forms_and_rejects_invalid_or_explicit_nil() {
        let canonical = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
        let plain = "6BA7B8109DAD11D180B400C04FD430C8";
        let expected = parse_uuid(canonical).unwrap();
        for uuid in [
            canonical.to_owned(),
            plain.to_owned(),
            format!("{{{canonical}}}"),
            format!("{{{plain}}}"),
            format!("urn:uuid:{canonical}"),
            format!("urn:uuid:{plain}"),
        ] {
            assert_eq!(parse_uuid(&uuid), Some(expected), "{uuid}");
            let mut repository = TestRepository::default();
            let input = serde_json::json!({"object_ids":[{"collection":"profile","key":"k","user_id":uuid}]}).to_string();
            assert_eq!(
                handle(&mut repository, &read_request(&input), user()).status,
                200
            );
            assert_eq!(repository.read_keys[0].user_id(), expected);
        }
        for uuid in [
            "00000000-0000-0000-0000-000000000000",
            "{00000000000000000000000000000000}",
            "urn:uuid:00000000000000000000000000000000",
            "URN:UUID:6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "6ba7b810-9dad-11d1-80b4-00c04fd430cZ",
            " 6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "6ba7b8109dad-11d1-80b4-00c04fd430c8",
            "nonsense",
        ] {
            let mut repository = TestRepository::default();
            let input = serde_json::json!({"object_ids":[{"collection":"profile","key":"k","user_id":uuid}]}).to_string();
            let response = handle(&mut repository, &read_request(&input), user());
            assert_eq!(response.status, 400, "{uuid}");
            assert_eq!(body(&response)["message"], INVALID_USER);
            assert_eq!(repository.read_calls, 0);
        }
    }

    #[test]
    fn read_empty_batches_do_not_access_repository() {
        for input in [
            "",
            " \t\n",
            "{}",
            r#"{"object_ids":[]}"#,
            r#"{"objectIds":null}"#,
        ] {
            let mut repository = TestRepository::default();
            let response = handle(&mut repository, &read_request(input), user());
            assert_eq!(response.status, 200);
            assert_eq!(response.body, b"{}");
            assert_eq!(repository.read_calls, 0);
            assert_eq!(repository.calls, 0);
        }
    }

    #[test]
    fn read_schema_duplicates_types_and_invalid_keys_fail_before_repository() {
        for input in [
            r#"{"object_ids":null,"objectIds":[]}"#,
            r#"{"object_ids":[{"collection":"c","key":"k","user_id":null,"userId":""}]}"#,
            r#"{"object_ids":[{"collection":"c","key":"k","user_id":1}]}"#,
            r#"{"object_ids":[{"collection":"c"}]}"#,
            r#"{"object_ids":[null]}"#,
            r#"{"object_ids":{}}"#,
            r#"{"object_ids":[{"collection":"","key":"k"}]}"#,
        ] {
            let mut repository = TestRepository::default();
            let response = handle(&mut repository, &read_request(input), user());
            assert_eq!(response.status, 400, "{input}");
            assert_eq!(body(&response)["code"], 3);
            assert_eq!(repository.read_calls, 0);
        }
    }

    #[test]
    fn read_candidate_batch_limit_and_duplicate_keys_are_bounded() {
        let mut repository = TestRepository::default();
        seed_read_object(
            &mut repository,
            UserId::new([0; 16]),
            "k",
            b"{}",
            ReadPermission::Public,
            WritePermission::None,
        );
        let object = serde_json::json!({"collection":"profile","key":"k"});
        let input = serde_json::json!({"object_ids":vec![object.clone(); MAX_BATCH]}).to_string();
        let response = handle(&mut repository, &read_request(&input), user());
        assert_eq!(response.status, 200);
        assert_eq!(
            body(&response)["objects"].as_array().unwrap().len(),
            MAX_BATCH
        );
        assert_eq!(repository.read_calls, 1);
        let input = serde_json::json!({"object_ids":vec![object; MAX_BATCH+1]}).to_string();
        let response = handle(&mut repository, &read_request(&input), user());
        assert_eq!(response.status, 400);
        assert_eq!(repository.read_calls, 1);
        // Repeated output follows the candidate repository contract. Upstream
        // deduplication/order depends on its SQL shape and remains unqualified.
    }

    #[test]
    fn read_rejects_unrequested_hidden_and_corrupt_repository_objects() {
        let mut repository = TestRepository::default();
        let object = seed_read_object(
            &mut repository,
            user(),
            "k",
            b"{}",
            ReadPermission::Owner,
            WritePermission::Owner,
        );
        let input = serde_json::json!({"object_ids":[{"collection":"profile","key":"k","user_id":uuid_string(user())}]}).to_string();
        let mut corrupt = Vec::new();
        let mut changed = object.clone();
        changed.key = StorageObjectKey::new("profile", "other", user()).unwrap();
        corrupt.push(changed);
        let mut changed = object.clone();
        changed.read_permission = ReadPermission::None;
        corrupt.push(changed);
        let mut changed = object.clone();
        changed.value = b"changed".to_vec();
        corrupt.push(changed);
        let mut changed = object.clone();
        changed.version = ContentVersion::from_value(b"other").into();
        corrupt.push(changed);
        let mut changed = object.clone();
        changed.integrity_digest = trnm_persistence_pg::IntegrityDigest::from_value(b"other");
        corrupt.push(changed);
        let mut changed = object.clone();
        changed.value = vec![0xff];
        changed.version = ContentVersion::from_value(&changed.value).into();
        changed.integrity_digest = trnm_persistence_pg::IntegrityDigest::from_value(&changed.value);
        corrupt.push(changed);
        for changed in corrupt {
            repository.read_override = Some(vec![changed]);
            let response = handle(&mut repository, &read_request(&input), user());
            assert_eq!(response.status, 500);
            assert_eq!(body(&response)["message"], "Error reading storage objects.");
            assert!(body(&response).get("objects").is_none());
        }
        repository.read_override = Some(vec![object.clone(), object]);
        assert_eq!(
            handle(&mut repository, &read_request(&input), user()).status,
            500
        );
    }

    #[test]
    fn read_rejects_other_owner_private_objects_even_if_requested() {
        let mut repository = TestRepository::default();
        let other = UserId::new([0x34; 16]);
        let object = seed_read_object(
            &mut repository,
            other,
            "k",
            b"{}",
            ReadPermission::Owner,
            WritePermission::Owner,
        );
        repository.read_override = Some(vec![object]);
        let input = serde_json::json!({"object_ids":[{"collection":"profile","key":"k","user_id":uuid_string(other)}]}).to_string();
        assert_eq!(
            handle(&mut repository, &read_request(&input), user()).status,
            500
        );
    }

    #[test]
    fn read_repository_failures_are_redacted_and_query_route_works() {
        let mut repository = TestRepository {
            failure: Some(DomainError::new(
                StableCode::Unavailable,
                "private SQL credentials",
                RetryClass::SafeBackoff,
            )),
            ..TestRepository::default()
        };
        let mut input = read_request(r#"{"object_ids":[{"collection":"c","key":"k"}]}"#);
        input.target = "/v2/storage?ignored=yes".to_owned();
        let response = handle(&mut repository, &input, user());
        assert_eq!(response.status, 500);
        assert_eq!(body(&response)["code"], 13);
        assert_eq!(body(&response)["message"], "Error reading storage objects.");
        assert!(!String::from_utf8(response.body)
            .unwrap()
            .contains("private"));
        assert_eq!(repository.read_calls, 1);
    }
}
