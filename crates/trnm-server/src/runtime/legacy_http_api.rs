//! Inactive, bounded JSON transport for the server-owned legacy auth service.
//! Studied Nakama d4d92f93 apigrpc.proto/apigrpc.pb.gw.go, api.go,
//! api_authenticate.go/api_session.go and the vendored gateway/protojson decoders.
//! Nakama source: Apache-2.0, The Nakama Authors & Contributors. This independent
//! Rust implementation registers no App route and grants no HTTP parity claim.
//! Authentication precedes business parsing (AGENTS rule 11); the upstream
//! gateway instead decodes before its security interceptor. That difference is
//! intentional and must remain visible in a future paired profile.

use core::fmt;
use std::collections::BTreeMap;

use base64::engine::{general_purpose::GeneralPurpose, DecodePaddingMode, GeneralPurposeConfig};
use base64::{alphabet, Engine};
use serde::de::{MapAccess, Visitor};
use serde::ser::SerializeMap;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::value::RawValue;
use trnm_contracts::StableCode;

use super::legacy_auth::{
    LegacyAccessPrincipal, LegacyAuthError, LegacyAuthService, LegacyDeviceAuthError,
    LegacyDeviceAuthInput, LegacyRepositoryError, LegacySession,
};

/// Source functions exist, but no authority, pool or HTTP route is installed.
pub const LEGACY_HTTP_ROUTES_QUALIFIED: bool = false;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LegacyAuthHttpRoute {
    AuthenticateDevice,
    Refresh,
    Logout,
}

/// Recognition alone does not enable a route or authenticate a request.
#[must_use]
pub fn legacy_auth_http_route(method: &str, target: &str) -> Option<LegacyAuthHttpRoute> {
    if method != "POST" {
        return None;
    }
    match target.split_once('?').map_or(target, |(path, _)| path) {
        "/v2/account/authenticate/device" => Some(LegacyAuthHttpRoute::AuthenticateDevice),
        "/v2/account/session/refresh" => Some(LegacyAuthHttpRoute::Refresh),
        "/v2/session/logout" => Some(LegacyAuthHttpRoute::Logout),
        _ => None,
    }
}

/// Local caps are explicit residuals, not claimed Nakama resource limits.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LegacyHttpLimits {
    pub body_bytes: usize,
    pub json_depth: usize,
    pub json_nodes: usize,
    pub json_members: usize,
    pub decoded_string_bytes: usize,
    pub scalar_bytes: usize,
    pub vars_entries: usize,
    pub query_bytes: usize,
    pub query_pairs: usize,
    pub authorization_bytes: usize,
    pub response_bytes: usize,
}
impl Default for LegacyHttpLimits {
    fn default() -> Self {
        Self {
            body_bytes: 512 * 1024,
            json_depth: 64,
            json_nodes: 8192,
            json_members: 2048,
            decoded_string_bytes: 512 * 1024,
            scalar_bytes: 128 * 1024,
            vars_entries: 256,
            query_bytes: 8192,
            query_pairs: 64,
            authorization_bytes: 64 * 1024,
            response_bytes: 2 * 1024 * 1024,
        }
    }
}
impl LegacyHttpLimits {
    fn validate(self) -> Result<(), LegacyGatewayError> {
        let caps = Self::default();
        for (value, cap) in [
            (self.body_bytes, caps.body_bytes),
            (self.json_depth, caps.json_depth),
            (self.json_nodes, caps.json_nodes),
            (self.json_members, caps.json_members),
            (self.decoded_string_bytes, caps.decoded_string_bytes),
            (self.scalar_bytes, caps.scalar_bytes),
            (self.vars_entries, caps.vars_entries),
            (self.query_bytes, caps.query_bytes),
            (self.query_pairs, caps.query_pairs),
            (self.authorization_bytes, caps.authorization_bytes),
            (self.response_bytes, caps.response_bytes),
        ] {
            if value == 0 || value > cap {
                return Err(configuration_error());
            }
        }
        Ok(())
    }
}

/// Owns one deliberately supplied server key; never uses a public default.
pub struct LegacyHttpServerKey(Vec<u8>);
impl LegacyHttpServerKey {
    pub fn new(key: impl Into<Vec<u8>>) -> Result<Self, LegacyGatewayError> {
        let key = key.into();
        if key.is_empty() || key.len() > 4096 {
            return Err(configuration_error());
        }
        Ok(Self(key))
    }
}
impl fmt::Debug for LegacyHttpServerKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyHttpServerKey")
            .field("key", &"<redacted>")
            .finish()
    }
}

/// Uses the existing audited base64 package, not a new hand-written decoder.
/// Go StdEncoding ignores CR/LF and permits nonzero unused trailing bits. The
/// password remains bytes, is ignored, and never has to be valid UTF-8.
pub fn require_basic_server_key(
    key: &LegacyHttpServerKey,
    authorization: Option<&str>,
    limits: LegacyHttpLimits,
) -> Result<(), LegacyGatewayError> {
    let authorization = authorization.ok_or_else(server_key_required)?;
    if authorization.len() > limits.authorization_bytes {
        return Err(resource_error());
    }
    let encoded = authorization
        .strip_prefix("Basic ")
        .ok_or_else(server_key_invalid)?;
    limits.validate()?;
    let filtered = encoded
        .bytes()
        .filter(|byte| !matches!(byte, b'\r' | b'\n'))
        .collect::<Vec<_>>();
    let decoder = GeneralPurpose::new(
        &alphabet::STANDARD,
        GeneralPurposeConfig::new()
            .with_decode_allow_trailing_bits(true)
            .with_decode_padding_mode(DecodePaddingMode::RequireCanonical),
    );
    let decoded = decoder.decode(filtered).map_err(|_| server_key_invalid())?;
    let colon = decoded
        .iter()
        .position(|byte| *byte == b':')
        .ok_or_else(server_key_invalid)?;
    if decoded[..colon] != key.0 {
        return Err(server_key_invalid());
    }
    Ok(())
}

/// Verifies the actual owned Access authority before the Logout body is parsed.
/// Basic credentials cannot produce the private service principal.
pub fn require_legacy_access_bearer(
    service: &LegacyAuthService,
    authorization: Option<&str>,
    limits: LegacyHttpLimits,
) -> Result<LegacyAccessPrincipal, LegacyGatewayError> {
    let authorization = authorization.ok_or_else(auth_token_required)?;
    if authorization.len() > limits.authorization_bytes {
        return Err(resource_error());
    }
    limits.validate()?;
    service
        .verify_access_bearer(authorization)
        .map_err(|_| auth_token_invalid())
}

pub struct LegacyDeviceHttpRequest {
    id: String,
    username: String,
    create: Option<bool>,
    variables: Option<BTreeMap<String, String>>,
}
impl LegacyDeviceHttpRequest {
    #[must_use]
    pub fn auth_input(&self) -> LegacyDeviceAuthInput<'_> {
        LegacyDeviceAuthInput {
            account_id: Some(&self.id),
            username: &self.username,
            create: self.create,
            variables: self.variables.as_ref(),
        }
    }
}
impl fmt::Debug for LegacyDeviceHttpRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyDeviceHttpRequest")
            .field("id_bytes", &self.id.len())
            .field("username_bytes", &self.username.len())
            .field("create", &self.create)
            .field("vars_entries", &self.variables.as_ref().map(BTreeMap::len))
            .finish()
    }
}
pub struct LegacyRefreshHttpRequest {
    token: String,
    variables: Option<BTreeMap<String, String>>,
}
impl LegacyRefreshHttpRequest {
    #[must_use]
    pub fn token(&self) -> &[u8] {
        self.token.as_bytes()
    }
    #[must_use]
    pub fn variables(&self) -> Option<&BTreeMap<String, String>> {
        self.variables.as_ref()
    }
}
impl fmt::Debug for LegacyRefreshHttpRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyRefreshHttpRequest")
            .field("token_bytes", &self.token.len())
            .field("vars_entries", &self.variables.as_ref().map(BTreeMap::len))
            .finish()
    }
}
pub struct LegacyLogoutHttpRequest {
    token: String,
    refresh_token: String,
}
impl LegacyLogoutHttpRequest {
    #[must_use]
    pub fn token(&self) -> &[u8] {
        self.token.as_bytes()
    }
    #[must_use]
    pub fn refresh_token(&self) -> &[u8] {
        self.refresh_token.as_bytes()
    }
}
impl fmt::Debug for LegacyLogoutHttpRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyLogoutHttpRequest")
            .field("token_bytes", &self.token.len())
            .field("refresh_token_bytes", &self.refresh_token.len())
            .finish()
    }
}

/// The body is AccountDevice, not AuthenticateDeviceRequest. Wrapper/create and
/// username fields in the JSON body are unknown and cannot override the query.
pub fn decode_device_http_request(
    key: &LegacyHttpServerKey,
    authorization: Option<&str>,
    body: &[u8],
    query: &str,
    limits: LegacyHttpLimits,
) -> Result<LegacyDeviceHttpRequest, LegacyGatewayError> {
    require_basic_server_key(key, authorization, limits)?;
    let object = parse_message(body, limits)?;
    let id = scalar(object.field(&["id"])?)?;
    let variables = variables(object.field(&["vars"])?, limits)?;
    let (create, username) = device_query(query, limits)?;
    Ok(LegacyDeviceHttpRequest {
        id,
        username,
        create,
        variables,
    })
}

/// The pinned generated gateway does not populate Refresh query parameters.
pub fn decode_refresh_http_request(
    key: &LegacyHttpServerKey,
    authorization: Option<&str>,
    body: &[u8],
    limits: LegacyHttpLimits,
) -> Result<LegacyRefreshHttpRequest, LegacyGatewayError> {
    require_basic_server_key(key, authorization, limits)?;
    let object = parse_message(body, limits)?;
    Ok(LegacyRefreshHttpRequest {
        token: scalar(object.field(&["token"])?)?,
        variables: variables(object.field(&["vars"])?, limits)?,
    })
}

/// The mandatory private verified principal is the authentication witness. The
/// same principal must later be passed to this service's logout_authenticated.
/// This decoder does not mutate a cache or perform Logout by itself.
pub fn decode_logout_http_request(
    _authenticated: &LegacyAccessPrincipal,
    body: &[u8],
    limits: LegacyHttpLimits,
) -> Result<LegacyLogoutHttpRequest, LegacyGatewayError> {
    logout_body(body, limits)
}
fn logout_body(
    body: &[u8],
    limits: LegacyHttpLimits,
) -> Result<LegacyLogoutHttpRequest, LegacyGatewayError> {
    let object = parse_message(body, limits)?;
    Ok(LegacyLogoutHttpRequest {
        token: scalar(object.field(&["token"])?)?,
        refresh_token: scalar(object.field(&["refresh_token", "refreshToken"])?)?,
    })
}

#[derive(Default)]
struct RawObject(Vec<(String, Box<RawValue>)>);
impl RawObject {
    fn field(&self, aliases: &[&str]) -> Result<Option<&RawValue>, LegacyGatewayError> {
        let mut found = None;
        for (key, raw) in &self.0 {
            if aliases.contains(&key.as_str()) {
                if found.is_some() {
                    return Err(body_error());
                }
                found = Some(raw.as_ref());
            }
        }
        Ok(found)
    }
}
impl<'de> Deserialize<'de> for RawObject {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = RawObject;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON object")
            }
            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Self::Value, M::Error> {
                let mut members = Vec::new();
                while let Some(member) = map.next_entry::<String, Box<RawValue>>()? {
                    members.push(member);
                }
                Ok(RawObject(members))
            }
        }
        deserializer.deserialize_map(ObjectVisitor)
    }
}

fn scalar(raw: Option<&RawValue>) -> Result<String, LegacyGatewayError> {
    match raw {
        None => Ok(String::new()),
        Some(raw) if raw.get() == "null" => Ok(String::new()),
        Some(raw) => serde_json::from_str(raw.get()).map_err(|_| body_error()),
    }
}
fn variables(
    raw: Option<&RawValue>,
    limits: LegacyHttpLimits,
) -> Result<Option<BTreeMap<String, String>>, LegacyGatewayError> {
    let Some(raw) = raw.filter(|raw| raw.get() != "null") else {
        return Ok(None);
    };
    // protojson calls Mutable(fd).Map() for a non-null map, including {}.
    let object: RawObject = serde_json::from_str(raw.get()).map_err(|_| body_error())?;
    if object.0.len() > limits.vars_entries {
        return Err(resource_error());
    }
    let mut vars = BTreeMap::new();
    for (key, value) in object.0 {
        let value: String = serde_json::from_str(value.get()).map_err(|_| body_error())?;
        if vars.insert(key, value).is_some() {
            return Err(body_error());
        }
    }
    Ok(Some(vars))
}

fn parse_message(body: &[u8], limits: LegacyHttpLimits) -> Result<RawObject, LegacyGatewayError> {
    limits.validate()?;
    if body.len() > limits.body_bytes {
        return Err(resource_error());
    }
    let Some(start) = body.iter().position(|b| !json_whitespace(*b)) else {
        // NewDecoder's io.EOF is explicitly ignored by all three generated handlers.
        return Ok(RawObject::default());
    };
    if body[start] != b'{' {
        return Err(body_error());
    }
    let end = bounded_first_object(body, start, limits)?;
    // The gateway decodes one RawMessage; later body values are not decoded.
    let object: RawObject = serde_json::from_slice(&body[start..end]).map_err(|_| body_error())?;
    // Pinned protojson resolves [extension] before DiscardUnknown or null.
    // Both blank-imported generated registries bind these seven names to
    // descriptor options, never to AccountDevice/SessionRefresh/SessionLogout.
    // This check belongs to message members; vars map keys remain arbitrary.
    if object
        .0
        .iter()
        .any(|(name, _)| registered_options_extension(name))
    {
        return Err(body_error());
    }
    Ok(object)
}
fn registered_options_extension(name: &str) -> bool {
    let Some(name) = name.strip_prefix('[').and_then(|v| v.strip_suffix(']')) else {
        return false;
    };
    matches!(
        name,
        "google.api.http"
            | "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_swagger"
            | "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_operation"
            | "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_schema"
            | "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_enum"
            | "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_tag"
            | "grpc.gateway.protoc_gen_openapiv2.options.openapiv2_field"
    )
}
const fn json_whitespace(byte: u8) -> bool {
    matches!(byte, b' ' | b'\n' | b'\r' | b'\t')
}

/// Resource preflight only; serde remains the JSON grammar/type authority. Raw
/// unknown values retain duplicate keys and huge JSON numbers without f64
/// conversion. All strings, including discarded values, are UTF-8/UTF-16 checked.
fn bounded_first_object(
    body: &[u8],
    start: usize,
    limits: LegacyHttpLimits,
) -> Result<usize, LegacyGatewayError> {
    let mut stack = Vec::new();
    let mut quote = None;
    let mut escaped = false;
    let mut primitive = false;
    let mut nodes = 0usize;
    let mut members = 0usize;
    let mut strings = 0usize;
    for (offset, &byte) in body.iter().enumerate().skip(start) {
        if let Some(begin) = quote {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                let value: String =
                    serde_json::from_slice(&body[begin..=offset]).map_err(|_| body_error())?;
                if value.len() > limits.scalar_bytes {
                    return Err(resource_error());
                }
                strings = strings.saturating_add(value.len());
                if strings > limits.decoded_string_bytes {
                    return Err(resource_error());
                }
                quote = None;
            }
            continue;
        }
        match byte {
            b'"' => {
                quote = Some(offset);
                nodes = nodes.saturating_add(1);
                primitive = false;
            }
            b'{' | b'[' => {
                stack.push(byte);
                if stack.len() > limits.json_depth {
                    return Err(resource_error());
                }
                nodes = nodes.saturating_add(1);
                primitive = false;
            }
            b'}' | b']' => {
                let expected = if byte == b'}' { b'{' } else { b'[' };
                if stack.pop() != Some(expected) {
                    return Err(body_error());
                }
                if stack.is_empty() {
                    return Ok(offset + 1);
                }
                primitive = false;
            }
            b':' => {
                members = members.saturating_add(1);
                if members > limits.json_members {
                    return Err(resource_error());
                }
                primitive = false;
            }
            b',' | b' ' | b'\t' | b'\r' | b'\n' => primitive = false,
            _ if !primitive => {
                nodes = nodes.saturating_add(1);
                primitive = true;
            }
            _ => {}
        }
        if nodes > limits.json_nodes {
            return Err(resource_error());
        }
    }
    Err(body_error())
}

fn device_query(
    query: &str,
    limits: LegacyHttpLimits,
) -> Result<(Option<bool>, String), LegacyGatewayError> {
    if query.len() > limits.query_bytes {
        return Err(resource_error());
    }
    let mut values: BTreeMap<Vec<u8>, Vec<Vec<u8>>> = BTreeMap::new();
    let mut pairs = 0usize;
    for pair in query.split('&').filter(|pair| !pair.is_empty()) {
        pairs = pairs.saturating_add(1);
        if pairs > limits.query_pairs {
            return Err(resource_error());
        }
        // Go url.ParseQuery rejects raw semicolons, even on unknown fields.
        if pair.contains(';') {
            return Err(query_error());
        }
        let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
        let key = query_unescape(key)?;
        let value = query_unescape(value)?;
        values.entry(key).or_default().push(value);
    }
    let mut create = None;
    let mut username = String::new();
    let mut create_paths = 0usize;
    for (mut key, mut values) in values {
        // Same greedy suffix grouping as query.go valuesKeyRegexp.
        if key.last() == Some(&b']') && !key.contains(&b'\n') {
            if let Some(open) = key.iter().rposition(|byte| *byte == b'[') {
                values.insert(0, key[open + 1..key.len() - 1].to_vec());
                key.truncate(open);
            }
        }
        let path = key.split(|byte| *byte == b'.').collect::<Vec<_>>();
        match path[0] {
            b"account" => continue, // body-bound prefix filter
            b"username" => {
                if path.len() != 1 || values.len() != 1 {
                    return Err(query_error());
                }
                username = String::from_utf8(values.remove(0)).map_err(|_| wire_gap_error())?;
            }
            b"create" => {
                create_paths += 1;
                if create_paths > 1 {
                    // Go map iteration makes distinct overlapping wrapper paths
                    // order-dependent. No invented deterministic equivalence.
                    return Err(wire_gap_error());
                }
                if path.len() == 1 || path.get(1) == Some(&b"value".as_slice()) {
                    if path.len() > 2 || values.len() != 1 {
                        return Err(query_error());
                    }
                    create = Some(parse_query_bool(&values[0])?);
                } else {
                    // Traversal initializes BoolValue before ignoring an unknown
                    // child. The default false wrapper remains present.
                    create = Some(false);
                }
            }
            _ => {} // pinned DefaultQueryParser ignores unknown names
        }
    }
    Ok((create, username))
}
fn query_unescape(raw: &str) -> Result<Vec<u8>, LegacyGatewayError> {
    let bytes = raw.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => decoded.push(b' '),
            b'%' => {
                let a = bytes.get(index + 1).copied().and_then(hex);
                let b = bytes.get(index + 2).copied().and_then(hex);
                let (Some(a), Some(b)) = (a, b) else {
                    return Err(query_error());
                };
                decoded.push((a << 4) | b);
                index += 2;
            }
            byte => decoded.push(byte),
        }
        index += 1;
    }
    Ok(decoded)
}
const fn hex(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}
fn parse_query_bool(raw: &[u8]) -> Result<bool, LegacyGatewayError> {
    match raw {
        b"1" | b"t" | b"T" | b"TRUE" | b"true" | b"True" => Ok(true),
        b"0" | b"f" | b"F" | b"FALSE" | b"false" | b"False" => Ok(false),
        _ => Err(query_error()),
    }
}

/// Called only with an actual service session, after its repository confirms
/// commit. Encoding/delivery failure supplies no retry or compensation action.
pub fn encode_legacy_session(
    session: &LegacySession,
    limits: LegacyHttpLimits,
) -> Result<Vec<u8>, LegacyGatewayError> {
    encode_session_parts(
        session.created,
        session.tokens().access.as_str(),
        session.tokens().refresh.as_str(),
        limits,
    )
}
fn encode_session_parts(
    created: bool,
    token: &str,
    refresh_token: &str,
    limits: LegacyHttpLimits,
) -> Result<Vec<u8>, LegacyGatewayError> {
    limits.validate()?;
    if token.len() > limits.scalar_bytes || refresh_token.len() > limits.scalar_bytes {
        return Err(resource_error());
    }
    let bound = token
        .len()
        .checked_add(refresh_token.len())
        .and_then(|size| size.checked_mul(6))
        .and_then(|size| size.checked_add(128))
        .ok_or_else(resource_error)?;
    if bound > limits.response_bytes {
        return Err(resource_error());
    }
    struct SessionWire<'a> {
        created: bool,
        token: &'a str,
        refresh_token: &'a str,
    }
    impl Serialize for SessionWire<'_> {
        fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            let fields = usize::from(self.created)
                + usize::from(!self.token.is_empty())
                + usize::from(!self.refresh_token.is_empty());
            let mut object = serializer.serialize_map(Some(fields))?;
            if self.created {
                object.serialize_entry("created", &true)?;
            }
            if !self.token.is_empty() {
                object.serialize_entry("token", self.token)?;
            }
            if !self.refresh_token.is_empty() {
                object.serialize_entry("refresh_token", self.refresh_token)?;
            }
            object.end()
        }
    }
    serde_json::to_vec(&SessionWire {
        created,
        token,
        refresh_token,
    })
    .map_err(|_| internal_error())
}
#[must_use]
pub fn encode_legacy_logout() -> Vec<u8> {
    b"{}".to_vec()
}

/// The public error contains only a code and a fixed source/local message. Raw
/// serde/SQLSTATE/transaction/credential diagnostics cannot enter this type.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LegacyGatewayError {
    code: StableCode,
    message: &'static str,
}
impl LegacyGatewayError {
    #[must_use]
    pub const fn code(self) -> StableCode {
        self.code
    }
    #[must_use]
    pub const fn message(self) -> &'static str {
        self.message
    }
    /// DefaultHTTPErrorHandler emits this header for unauthenticated errors.
    /// Installing it in the actual Response transport is still an App task.
    #[must_use]
    pub const fn www_authenticate(self) -> Option<&'static str> {
        if matches!(self.code, StableCode::Unauthenticated) {
            Some(self.message)
        } else {
            None
        }
    }
    #[must_use]
    pub const fn status(self) -> u16 {
        match self.code {
            StableCode::InvalidArgument
            | StableCode::FailedPrecondition
            | StableCode::OutOfRange => 400,
            StableCode::Unauthenticated => 401,
            StableCode::PermissionDenied => 403,
            StableCode::NotFound => 404,
            StableCode::AlreadyExists | StableCode::Aborted => 409,
            StableCode::ResourceExhausted => 429,
            StableCode::Unimplemented => 501,
            StableCode::Unavailable => 503,
            StableCode::Internal | StableCode::DataLoss => 500,
        }
    }
    #[must_use]
    pub fn json_body(self) -> Vec<u8> {
        // Static messages below contain no JSON-escape characters. The source
        // gateway Status omits empty details rather than fabricating diagnostics.
        format!(
            "{{\"code\":{},\"message\":\"{}\"}}",
            self.code as u16, self.message
        )
        .into_bytes()
    }
}
impl fmt::Display for LegacyGatewayError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.message)
    }
}
impl std::error::Error for LegacyGatewayError {}

#[must_use]
pub fn legacy_gateway_error(error: &LegacyAuthError) -> LegacyGatewayError {
    use LegacyAuthError as E;
    let (code, message) = match error {
        E::AuthTokenInvalid => (StableCode::Unauthenticated, "Auth token invalid"),
        E::RefreshTokenRequired => (StableCode::InvalidArgument, "Refresh token is required."),
        E::RefreshTokenInvalidOrExpired => (
            StableCode::Unauthenticated,
            "Refresh token invalid or expired.",
        ),
        E::SessionLogoutTokenInvalid => (StableCode::InvalidArgument, "Session token invalid."),
        E::RefreshLogoutTokenInvalid => (StableCode::InvalidArgument, "Refresh token invalid."),
        E::UserAccountNotFound => (StableCode::NotFound, "User account not found."),
        E::UserAccountBanned => (StableCode::PermissionDenied, "User account banned."),
        E::UsernameAlreadyInUse => (StableCode::AlreadyExists, "Username is already in use."),
        E::DeviceInput(error) => (StableCode::InvalidArgument, error.message()),
        E::Repository(error) | E::DeviceRepository(error) => {
            let message = match error {
                LegacyRepositoryError::UserNotFound => "User account not found.",
                LegacyRepositoryError::UserBanned => "User account banned.",
                LegacyRepositoryError::UsernameAlreadyInUse => "Username is already in use.",
                _ if matches!(error, LegacyRepositoryError::Unimplemented) => {
                    "Legacy authentication unavailable."
                }
                _ => "Error finding user account.",
            };
            (error.code(), message)
        }
        E::PrincipalServiceMismatch => (StableCode::PermissionDenied, "Auth token invalid"),
        E::Cache(trnm_session_core::NakamaLegacyBlacklistError::InvalidLimits)
        | E::CachePoisoned => (StableCode::Internal, "Legacy authentication unavailable."),
        E::Cache(_) => (
            StableCode::ResourceExhausted,
            "Legacy authentication unavailable.",
        ),
        E::InvalidConfiguration
        | E::ClockUnavailable
        | E::ClockRange
        | E::RandomUnavailable
        | E::Issue(_) => (StableCode::Internal, "Error issuing session tokens."),
    };
    LegacyGatewayError { code, message }
}
#[must_use]
pub fn legacy_device_gateway_error(error: &LegacyDeviceAuthError) -> LegacyGatewayError {
    let mut public = legacy_gateway_error(error.cause());
    public.code = error.code();
    if matches!(
        error.cause(),
        LegacyAuthError::DeviceRepository(
            LegacyRepositoryError::Internal
                | LegacyRepositoryError::Unavailable
                | LegacyRepositoryError::DataLoss
                | LegacyRepositoryError::ResourceExhausted
                | LegacyRepositoryError::NativeFailure(_)
                | LegacyRepositoryError::Lease(_)
        )
    ) {
        public.message = "Error finding or creating user account.";
    }
    public
}
const fn error(code: StableCode, message: &'static str) -> LegacyGatewayError {
    LegacyGatewayError { code, message }
}
const fn server_key_required() -> LegacyGatewayError {
    error(StableCode::Unauthenticated, "Server key required")
}
const fn server_key_invalid() -> LegacyGatewayError {
    error(StableCode::Unauthenticated, "Server key invalid")
}
const fn auth_token_required() -> LegacyGatewayError {
    error(StableCode::Unauthenticated, "Auth token required")
}
const fn auth_token_invalid() -> LegacyGatewayError {
    error(StableCode::Unauthenticated, "Auth token invalid")
}
const fn resource_error() -> LegacyGatewayError {
    error(
        StableCode::ResourceExhausted,
        "Legacy request resource limit exceeded.",
    )
}
const fn configuration_error() -> LegacyGatewayError {
    error(
        StableCode::Internal,
        "Legacy authentication configuration invalid.",
    )
}
const fn body_error() -> LegacyGatewayError {
    error(StableCode::InvalidArgument, "Invalid request body.")
}
const fn query_error() -> LegacyGatewayError {
    error(StableCode::InvalidArgument, "Invalid query parameters.")
}
const fn wire_gap_error() -> LegacyGatewayError {
    error(
        StableCode::Unimplemented,
        "Legacy wire form is not implemented.",
    )
}
const fn internal_error() -> LegacyGatewayError {
    error(StableCode::Internal, "Error encoding session response.")
}

#[cfg(test)]
#[path = "legacy_http_api_tests.rs"]
mod tests;
