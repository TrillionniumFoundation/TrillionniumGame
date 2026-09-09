#![forbid(unsafe_code)]

mod canonical;
mod sha256;

pub use canonical::{
    encode_canonical, parse_canonical, parse_canonical_bytes, CanonicalError, CanonicalValue,
    MAX_CANONICAL_DEPTH,
};
pub use sha256::sha256_hex;

use core::fmt;
use std::fmt::Write as _;

pub const CONTRACT_VERSION: &str = "trnm_world_transition_v1";
pub const REQUEST_HASH_DOMAIN: &str = "trnm.world.transition.request.v1";
pub const TRANSITION_HASH_DOMAIN: &str = "trnm.world.transition.accepted.v1";
pub const OUTCOME_HASH_DOMAIN: &str = "trnm.world.outcome.v1";

pub const MAX_STATE_PAYLOAD_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_COMMAND_PAYLOAD_BYTES: usize = 128 * 1024;
pub const MAX_REPLAY_PAYLOAD_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_OUTCOME_PAYLOAD_BYTES: usize = 512 * 1024;
pub const MAX_IDENTIFIER_BYTES: usize = 160;
pub const MAX_ERROR_DETAIL_BYTES: usize = 256;
pub const MAX_TICK: u64 = i64::MAX as u64;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ContractError {
    Canonical(CanonicalError),
    InvalidContractVersion,
    InvalidIdentifier { field: &'static str },
    InvalidSha256 { field: &'static str },
    PayloadHashMismatch { field: &'static str },
    InvalidTick,
    AdapterInvariant { detail: &'static str },
}

impl fmt::Display for ContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Canonical(error) => write!(formatter, "{error}"),
            Self::InvalidContractVersion => formatter.write_str("invalid contract version"),
            Self::InvalidIdentifier { field } => write!(formatter, "invalid identifier: {field}"),
            Self::InvalidSha256 { field } => write!(formatter, "invalid sha256: {field}"),
            Self::PayloadHashMismatch { field } => {
                write!(formatter, "payload hash mismatch: {field}")
            }
            Self::InvalidTick => formatter.write_str("invalid deterministic tick"),
            Self::AdapterInvariant { detail } => {
                write!(formatter, "adapter invariant failed: {detail}")
            }
        }
    }
}

impl std::error::Error for ContractError {}

impl From<CanonicalError> for ContractError {
    fn from(value: CanonicalError) -> Self {
        Self::Canonical(value)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum TransitionErrorCodeV1 {
    InvalidContractVersion,
    InvalidRequest,
    UnknownRulesetRevision,
    UnknownContentRevision,
    PayloadHashMismatch,
    InvalidCanonicalPayload,
    ForbiddenAuthoritySurface,
    ResourceBudgetExceeded,
    InvalidCommand,
    DomainRejected,
    NondeterministicOutput,
    InternalUnavailable,
}

impl TransitionErrorCodeV1 {
    pub const ALL: [Self; 12] = [
        Self::InvalidContractVersion,
        Self::InvalidRequest,
        Self::UnknownRulesetRevision,
        Self::UnknownContentRevision,
        Self::PayloadHashMismatch,
        Self::InvalidCanonicalPayload,
        Self::ForbiddenAuthoritySurface,
        Self::ResourceBudgetExceeded,
        Self::InvalidCommand,
        Self::DomainRejected,
        Self::NondeterministicOutput,
        Self::InternalUnavailable,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidContractVersion => "invalid_contract_version",
            Self::InvalidRequest => "invalid_request",
            Self::UnknownRulesetRevision => "unknown_ruleset_revision",
            Self::UnknownContentRevision => "unknown_content_revision",
            Self::PayloadHashMismatch => "payload_hash_mismatch",
            Self::InvalidCanonicalPayload => "invalid_canonical_payload",
            Self::ForbiddenAuthoritySurface => "forbidden_authority_surface",
            Self::ResourceBudgetExceeded => "resource_budget_exceeded",
            Self::InvalidCommand => "invalid_command",
            Self::DomainRejected => "domain_rejected",
            Self::NondeterministicOutput => "nondeterministic_output",
            Self::InternalUnavailable => "internal_unavailable",
        }
    }

    pub const fn retryable(self) -> bool {
        matches!(self, Self::InternalUnavailable)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanonicalPayloadV1 {
    pub schema_id: String,
    pub canonical_json: String,
    pub sha256: String,
}

impl CanonicalPayloadV1 {
    pub fn new(
        schema_id: impl Into<String>,
        canonical_json: impl Into<String>,
        maximum_bytes: usize,
    ) -> Result<Self, ContractError> {
        let schema_id = schema_id.into();
        let canonical_json = canonical_json.into();
        validate_identifier("payload.schema_id", &schema_id)?;
        parse_canonical(&canonical_json, maximum_bytes)?;
        let sha256 = sha256_hex(canonical_json.as_bytes());
        Ok(Self {
            schema_id,
            canonical_json,
            sha256,
        })
    }

    pub fn from_parts(
        schema_id: impl Into<String>,
        canonical_json: impl Into<String>,
        sha256: impl Into<String>,
        maximum_bytes: usize,
    ) -> Result<Self, ContractError> {
        let payload = Self {
            schema_id: schema_id.into(),
            canonical_json: canonical_json.into(),
            sha256: sha256.into(),
        };
        payload.validate("payload", maximum_bytes)?;
        Ok(payload)
    }

    pub fn validate(&self, field: &'static str, maximum_bytes: usize) -> Result<(), ContractError> {
        validate_identifier("payload.schema_id", &self.schema_id)?;
        parse_canonical(&self.canonical_json, maximum_bytes)?;
        validate_sha256(field, &self.sha256)?;
        if sha256_hex(self.canonical_json.as_bytes()) != self.sha256 {
            return Err(ContractError::PayloadHashMismatch { field });
        }
        Ok(())
    }

    pub fn to_canonical_json(&self) -> String {
        let mut output = String::with_capacity(self.canonical_json.len() + 256);
        output.push_str("{\"canonical_json\":");
        output.push_str(&self.canonical_json);
        output.push_str(",\"schema_id\":");
        push_json_string(&mut output, &self.schema_id);
        output.push_str(",\"sha256\":");
        push_json_string(&mut output, &self.sha256);
        output.push('}');
        output
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorldCommandV1 {
    pub command_id: String,
    pub payload: CanonicalPayloadV1,
}

impl WorldCommandV1 {
    pub fn new(
        command_id: impl Into<String>,
        payload: CanonicalPayloadV1,
    ) -> Result<Self, ContractError> {
        let command = Self {
            command_id: command_id.into(),
            payload,
        };
        command.validate()?;
        Ok(command)
    }

    pub fn validate(&self) -> Result<(), ContractError> {
        validate_identifier("command.command_id", &self.command_id)?;
        self.payload
            .validate("command.payload", MAX_COMMAND_PAYLOAD_BYTES)
    }

    pub fn to_canonical_json(&self) -> String {
        let mut output = String::with_capacity(self.payload.canonical_json.len() + 320);
        output.push_str("{\"command_id\":");
        push_json_string(&mut output, &self.command_id);
        output.push_str(",\"payload\":");
        output.push_str(&self.payload.to_canonical_json());
        output.push('}');
        output
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorldTransitionRequestV1 {
    pub contract_version: String,
    pub transition_id: String,
    pub ruleset_revision: String,
    pub content_revision: String,
    pub expected_tick: u64,
    pub previous_state: CanonicalPayloadV1,
    pub command: WorldCommandV1,
}

impl WorldTransitionRequestV1 {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        transition_id: impl Into<String>,
        ruleset_revision: impl Into<String>,
        content_revision: impl Into<String>,
        expected_tick: u64,
        previous_state: CanonicalPayloadV1,
        command: WorldCommandV1,
    ) -> Result<Self, ContractError> {
        let request = Self {
            contract_version: CONTRACT_VERSION.to_string(),
            transition_id: transition_id.into(),
            ruleset_revision: ruleset_revision.into(),
            content_revision: content_revision.into(),
            expected_tick,
            previous_state,
            command,
        };
        request.validate()?;
        Ok(request)
    }

    pub fn validate(&self) -> Result<(), ContractError> {
        if self.contract_version != CONTRACT_VERSION {
            return Err(ContractError::InvalidContractVersion);
        }
        validate_identifier("transition_id", &self.transition_id)?;
        validate_identifier("ruleset_revision", &self.ruleset_revision)?;
        validate_identifier("content_revision", &self.content_revision)?;
        if self.expected_tick > MAX_TICK {
            return Err(ContractError::InvalidTick);
        }
        self.previous_state
            .validate("previous_state", MAX_STATE_PAYLOAD_BYTES)?;
        self.command.validate()
    }

    pub fn to_canonical_json(&self) -> Result<String, ContractError> {
        self.validate()?;
        let mut output = String::with_capacity(
            self.previous_state.canonical_json.len()
                + self.command.payload.canonical_json.len()
                + 768,
        );
        output.push_str("{\"command\":");
        output.push_str(&self.command.to_canonical_json());
        output.push_str(",\"content_revision\":");
        push_json_string(&mut output, &self.content_revision);
        output.push_str(",\"contract_version\":");
        push_json_string(&mut output, &self.contract_version);
        let _ = write!(output, ",\"expected_tick\":{}", self.expected_tick);
        output.push_str(",\"previous_state\":");
        output.push_str(&self.previous_state.to_canonical_json());
        output.push_str(",\"ruleset_revision\":");
        push_json_string(&mut output, &self.ruleset_revision);
        output.push_str(",\"transition_id\":");
        push_json_string(&mut output, &self.transition_id);
        output.push('}');
        Ok(output)
    }

    pub fn request_hash(&self) -> Result<String, ContractError> {
        Ok(domain_hash(
            REQUEST_HASH_DOMAIN,
            self.to_canonical_json()?.as_bytes(),
        ))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransitionOutputV1 {
    pub next_tick: u64,
    pub next_state: CanonicalPayloadV1,
    pub replay_material: CanonicalPayloadV1,
    pub outcome_material: Option<CanonicalPayloadV1>,
}

impl TransitionOutputV1 {
    pub fn validate_against(
        &self,
        request: &WorldTransitionRequestV1,
    ) -> Result<(), ContractError> {
        if self.next_tick < request.expected_tick || self.next_tick > MAX_TICK {
            return Err(ContractError::InvalidTick);
        }
        self.next_state
            .validate("next_state", MAX_STATE_PAYLOAD_BYTES)?;
        self.replay_material
            .validate("replay_material", MAX_REPLAY_PAYLOAD_BYTES)?;
        if let Some(outcome) = &self.outcome_material {
            outcome.validate("outcome_material", MAX_OUTCOME_PAYLOAD_BYTES)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DomainRejectionV1 {
    pub code: TransitionErrorCodeV1,
    pub detail: String,
}

impl DomainRejectionV1 {
    pub fn new(code: TransitionErrorCodeV1, detail: impl AsRef<str>) -> Self {
        let code = match code {
            TransitionErrorCodeV1::InvalidCommand
            | TransitionErrorCodeV1::DomainRejected
            | TransitionErrorCodeV1::ResourceBudgetExceeded
            | TransitionErrorCodeV1::InternalUnavailable => code,
            _ => TransitionErrorCodeV1::DomainRejected,
        };
        Self {
            code,
            detail: sanitize_detail(detail.as_ref()),
        }
    }
}

pub trait WorldRulesAdapterV1 {
    fn supports_ruleset(&self, ruleset_revision: &str) -> bool;
    fn supports_content(&self, content_revision: &str) -> bool;
    fn transition(
        &self,
        request: &WorldTransitionRequestV1,
    ) -> Result<TransitionOutputV1, DomainRejectionV1>;
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorldTransitionAcceptedV1 {
    pub contract_version: String,
    pub transition_id: String,
    pub ruleset_revision: String,
    pub content_revision: String,
    pub request_hash: String,
    pub previous_state_hash: String,
    pub next_tick: u64,
    pub next_state: CanonicalPayloadV1,
    pub replay_material: CanonicalPayloadV1,
    pub outcome_material: Option<CanonicalPayloadV1>,
    pub world_outcome_hash: Option<String>,
    pub world_transition_hash: String,
}

impl WorldTransitionAcceptedV1 {
    fn from_output(
        request: &WorldTransitionRequestV1,
        output: TransitionOutputV1,
    ) -> Result<Self, ContractError> {
        request.validate()?;
        output.validate_against(request)?;
        let request_hash = request.request_hash()?;
        let world_outcome_hash = output.outcome_material.as_ref().map(|outcome| {
            outcome_hash(
                &request.ruleset_revision,
                &request.content_revision,
                outcome,
            )
        });
        let mut accepted = Self {
            contract_version: CONTRACT_VERSION.to_string(),
            transition_id: request.transition_id.clone(),
            ruleset_revision: request.ruleset_revision.clone(),
            content_revision: request.content_revision.clone(),
            request_hash,
            previous_state_hash: request.previous_state.sha256.clone(),
            next_tick: output.next_tick,
            next_state: output.next_state,
            replay_material: output.replay_material,
            outcome_material: output.outcome_material,
            world_outcome_hash,
            world_transition_hash: String::new(),
        };
        accepted.world_transition_hash = accepted.compute_transition_hash()?;
        accepted.validate()?;
        Ok(accepted)
    }

    pub fn validate(&self) -> Result<(), ContractError> {
        if self.contract_version != CONTRACT_VERSION {
            return Err(ContractError::InvalidContractVersion);
        }
        validate_identifier("transition_id", &self.transition_id)?;
        validate_identifier("ruleset_revision", &self.ruleset_revision)?;
        validate_identifier("content_revision", &self.content_revision)?;
        validate_sha256("request_hash", &self.request_hash)?;
        validate_sha256("previous_state_hash", &self.previous_state_hash)?;
        validate_sha256("world_transition_hash", &self.world_transition_hash)?;
        if self.next_tick > MAX_TICK {
            return Err(ContractError::InvalidTick);
        }
        self.next_state
            .validate("next_state", MAX_STATE_PAYLOAD_BYTES)?;
        self.replay_material
            .validate("replay_material", MAX_REPLAY_PAYLOAD_BYTES)?;
        match (&self.outcome_material, &self.world_outcome_hash) {
            (None, None) => {}
            (Some(outcome), Some(hash)) => {
                outcome.validate("outcome_material", MAX_OUTCOME_PAYLOAD_BYTES)?;
                validate_sha256("world_outcome_hash", hash)?;
                if outcome_hash(&self.ruleset_revision, &self.content_revision, outcome) != *hash {
                    return Err(ContractError::PayloadHashMismatch {
                        field: "world_outcome_hash",
                    });
                }
            }
            _ => {
                return Err(ContractError::AdapterInvariant {
                    detail: "outcome material and outcome hash must appear together",
                });
            }
        }
        if self.compute_transition_hash()? != self.world_transition_hash {
            return Err(ContractError::PayloadHashMismatch {
                field: "world_transition_hash",
            });
        }
        Ok(())
    }

    fn canonical_facts_json(&self) -> Result<String, ContractError> {
        let mut output = String::with_capacity(
            self.next_state.canonical_json.len()
                + self.replay_material.canonical_json.len()
                + self
                    .outcome_material
                    .as_ref()
                    .map_or(0, |value| value.canonical_json.len())
                + 1024,
        );
        output.push_str("{\"content_revision\":");
        push_json_string(&mut output, &self.content_revision);
        output.push_str(",\"contract_version\":");
        push_json_string(&mut output, &self.contract_version);
        output.push_str(",\"next_state\":");
        output.push_str(&self.next_state.to_canonical_json());
        let _ = write!(output, ",\"next_tick\":{}", self.next_tick);
        output.push_str(",\"outcome_material\":");
        if let Some(outcome) = &self.outcome_material {
            output.push_str(&outcome.to_canonical_json());
        } else {
            output.push_str("null");
        }
        output.push_str(",\"previous_state_hash\":");
        push_json_string(&mut output, &self.previous_state_hash);
        output.push_str(",\"replay_material\":");
        output.push_str(&self.replay_material.to_canonical_json());
        output.push_str(",\"request_hash\":");
        push_json_string(&mut output, &self.request_hash);
        output.push_str(",\"ruleset_revision\":");
        push_json_string(&mut output, &self.ruleset_revision);
        output.push_str(",\"transition_id\":");
        push_json_string(&mut output, &self.transition_id);
        output.push_str(",\"world_outcome_hash\":");
        if let Some(hash) = &self.world_outcome_hash {
            push_json_string(&mut output, hash);
        } else {
            output.push_str("null");
        }
        output.push('}');
        Ok(output)
    }

    pub fn compute_transition_hash(&self) -> Result<String, ContractError> {
        Ok(domain_hash(
            TRANSITION_HASH_DOMAIN,
            self.canonical_facts_json()?.as_bytes(),
        ))
    }

    pub fn to_canonical_json(&self) -> Result<String, ContractError> {
        self.validate()?;
        let facts = self.canonical_facts_json()?;
        let mut output = String::with_capacity(facts.len() + 100);
        output.push_str(&facts[..facts.len() - 1]);
        output.push_str(",\"world_transition_hash\":");
        push_json_string(&mut output, &self.world_transition_hash);
        output.push('}');
        Ok(output)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorldTransitionRejectedV1 {
    pub contract_version: String,
    pub transition_id: String,
    pub request_hash: Option<String>,
    pub code: TransitionErrorCodeV1,
    pub retryable: bool,
    pub detail: String,
}

impl WorldTransitionRejectedV1 {
    fn from_request(
        request: &WorldTransitionRequestV1,
        code: TransitionErrorCodeV1,
        detail: impl AsRef<str>,
    ) -> Self {
        let transition_id = if validate_identifier("transition_id", &request.transition_id).is_ok()
        {
            request.transition_id.clone()
        } else {
            "invalid-transition".to_string()
        };
        Self {
            contract_version: CONTRACT_VERSION.to_string(),
            transition_id,
            request_hash: request.request_hash().ok(),
            code,
            retryable: code.retryable(),
            detail: sanitize_detail(detail.as_ref()),
        }
    }

    pub fn to_canonical_json(&self) -> String {
        let mut output = String::with_capacity(512);
        output.push_str("{\"code\":");
        push_json_string(&mut output, self.code.as_str());
        output.push_str(",\"contract_version\":");
        push_json_string(&mut output, &self.contract_version);
        output.push_str(",\"detail\":");
        push_json_string(&mut output, &self.detail);
        output.push_str(",\"request_hash\":");
        if let Some(hash) = &self.request_hash {
            push_json_string(&mut output, hash);
        } else {
            output.push_str("null");
        }
        let _ = write!(
            output,
            ",\"retryable\":{}",
            if self.retryable { "true" } else { "false" }
        );
        output.push_str(",\"transition_id\":");
        push_json_string(&mut output, &self.transition_id);
        output.push('}');
        output
    }
}

// The accepted result intentionally owns its complete authoritative material
// inline. Boxing it would add a heap allocation to every successful transition
// and change the public Rust API while leaving the canonical wire payload
// unchanged, so the size asymmetry is an explicit contract-level tradeoff.
#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WorldTransitionResultV1 {
    Accepted(WorldTransitionAcceptedV1),
    Rejected(WorldTransitionRejectedV1),
}

impl WorldTransitionResultV1 {
    pub fn to_canonical_json(&self) -> Result<String, ContractError> {
        match self {
            Self::Accepted(value) => value.to_canonical_json(),
            Self::Rejected(value) => Ok(value.to_canonical_json()),
        }
    }
}

pub fn execute_transition<A: WorldRulesAdapterV1>(
    adapter: &A,
    request: &WorldTransitionRequestV1,
) -> WorldTransitionResultV1 {
    if let Err(error) = request.validate() {
        return WorldTransitionResultV1::Rejected(WorldTransitionRejectedV1::from_request(
            request,
            contract_error_code(&error),
            error.to_string(),
        ));
    }
    if !adapter.supports_ruleset(&request.ruleset_revision) {
        return WorldTransitionResultV1::Rejected(WorldTransitionRejectedV1::from_request(
            request,
            TransitionErrorCodeV1::UnknownRulesetRevision,
            "ruleset revision is not supported",
        ));
    }
    if !adapter.supports_content(&request.content_revision) {
        return WorldTransitionResultV1::Rejected(WorldTransitionRejectedV1::from_request(
            request,
            TransitionErrorCodeV1::UnknownContentRevision,
            "content revision is not supported",
        ));
    }
    let output = match adapter.transition(request) {
        Ok(value) => value,
        Err(rejection) => {
            return WorldTransitionResultV1::Rejected(WorldTransitionRejectedV1::from_request(
                request,
                rejection.code,
                rejection.detail,
            ));
        }
    };
    match WorldTransitionAcceptedV1::from_output(request, output) {
        Ok(value) => WorldTransitionResultV1::Accepted(value),
        Err(error) => WorldTransitionResultV1::Rejected(WorldTransitionRejectedV1::from_request(
            request,
            contract_error_code(&error),
            error.to_string(),
        )),
    }
}

fn contract_error_code(error: &ContractError) -> TransitionErrorCodeV1 {
    match error {
        ContractError::InvalidContractVersion => TransitionErrorCodeV1::InvalidContractVersion,
        ContractError::PayloadHashMismatch { .. } => TransitionErrorCodeV1::PayloadHashMismatch,
        ContractError::Canonical(CanonicalError::TooLarge { .. }) => {
            TransitionErrorCodeV1::ResourceBudgetExceeded
        }
        ContractError::Canonical(CanonicalError::ForbiddenAuthorityKey { .. }) => {
            TransitionErrorCodeV1::ForbiddenAuthoritySurface
        }
        ContractError::Canonical(_) => TransitionErrorCodeV1::InvalidCanonicalPayload,
        ContractError::AdapterInvariant { .. } => TransitionErrorCodeV1::NondeterministicOutput,
        ContractError::InvalidIdentifier { .. }
        | ContractError::InvalidSha256 { .. }
        | ContractError::InvalidTick => TransitionErrorCodeV1::InvalidRequest,
    }
}

fn outcome_hash(
    ruleset_revision: &str,
    content_revision: &str,
    outcome: &CanonicalPayloadV1,
) -> String {
    let mut material = String::with_capacity(512);
    material.push_str("{\"content_revision\":");
    push_json_string(&mut material, content_revision);
    material.push_str(",\"outcome_payload_hash\":");
    push_json_string(&mut material, &outcome.sha256);
    material.push_str(",\"outcome_schema_id\":");
    push_json_string(&mut material, &outcome.schema_id);
    material.push_str(",\"ruleset_revision\":");
    push_json_string(&mut material, ruleset_revision);
    material.push('}');
    domain_hash(OUTCOME_HASH_DOMAIN, material.as_bytes())
}

fn domain_hash(domain: &str, material: &[u8]) -> String {
    let mut preimage = Vec::with_capacity(domain.len() + material.len() + 1);
    preimage.extend_from_slice(domain.as_bytes());
    preimage.push(b'\n');
    preimage.extend_from_slice(material);
    sha256_hex(&preimage)
}

fn validate_identifier(field: &'static str, value: &str) -> Result<(), ContractError> {
    if value.is_empty()
        || value.len() > MAX_IDENTIFIER_BYTES
        || !value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(byte, b'.' | b'_' | b':' | b'/' | b'-' | b'+' | b'@')
        })
    {
        return Err(ContractError::InvalidIdentifier { field });
    }
    Ok(())
}

fn validate_sha256(field: &'static str, value: &str) -> Result<(), ContractError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(ContractError::InvalidSha256 { field });
    }
    Ok(())
}

fn sanitize_detail(detail: &str) -> String {
    let mut output = String::with_capacity(detail.len().min(MAX_ERROR_DETAIL_BYTES));
    for character in detail.chars() {
        let replacement = if character.is_control() {
            ' '
        } else {
            character
        };
        if output.len() + replacement.len_utf8() > MAX_ERROR_DETAIL_BYTES {
            break;
        }
        output.push(replacement);
    }
    let output = output.trim();
    if output.is_empty() {
        "request rejected".to_string()
    } else {
        output.to_string()
    }
}

fn push_json_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character <= '\u{1f}' => {
                let _ = write!(output, "\\u{:04x}", character as u32);
            }
            character => output.push(character),
        }
    }
    output.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    const EMPTY_HASH: &str = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a";

    fn payload(schema: &str, value: &str, maximum: usize) -> CanonicalPayloadV1 {
        CanonicalPayloadV1::new(schema, value, maximum).unwrap()
    }

    fn request(command: &str) -> WorldTransitionRequestV1 {
        WorldTransitionRequestV1::new(
            "transition-0001",
            "ruleset-v1",
            "content-v1",
            7,
            payload("state-v1", "{}", MAX_STATE_PAYLOAD_BYTES),
            WorldCommandV1::new(
                "command-0001",
                payload("command-v1", command, MAX_COMMAND_PAYLOAD_BYTES),
            )
            .unwrap(),
        )
        .unwrap()
    }

    struct Echo;

    impl WorldRulesAdapterV1 for Echo {
        fn supports_ruleset(&self, value: &str) -> bool {
            value == "ruleset-v1"
        }

        fn supports_content(&self, value: &str) -> bool {
            value == "content-v1"
        }

        fn transition(
            &self,
            request: &WorldTransitionRequestV1,
        ) -> Result<TransitionOutputV1, DomainRejectionV1> {
            Ok(TransitionOutputV1 {
                next_tick: request.expected_tick + 1,
                next_state: payload("state-v1", "{}", MAX_STATE_PAYLOAD_BYTES),
                replay_material: payload("replay-v1", "[]", MAX_REPLAY_PAYLOAD_BYTES),
                outcome_material: None,
            })
        }
    }

    #[test]
    fn payload_and_request_hashes_are_stable() {
        let state = payload("state-v1", "{}", MAX_STATE_PAYLOAD_BYTES);
        assert_eq!(state.sha256, EMPTY_HASH);
        let first = request("{}");
        let second = request("{}");
        assert_eq!(
            first.request_hash().unwrap(),
            second.request_hash().unwrap()
        );
        assert_ne!(
            first.request_hash().unwrap(),
            request("{\"a\":1}").request_hash().unwrap()
        );
    }

    #[test]
    fn strict_canonical_parser_is_the_only_payload_admission_path() {
        for invalid in [
            "{\"a\":}",
            "{\"b\":1,\"a\":2}",
            "{\"a\":1,\"a\":2}",
            "[01]",
            "[-0]",
            "[1.0]",
            "[1e3]",
            "[9223372036854775808]",
            "{\"nakama_\\u0070rivate_key\":\"x\"}",
            "{}x",
        ] {
            assert!(
                CanonicalPayloadV1::new("test-v1", invalid, 4096).is_err(),
                "unexpected canonical pass: {invalid}"
            );
        }
    }

    #[test]
    fn identical_adapter_inputs_produce_identical_results() {
        let first = execute_transition(&Echo, &request("{}"));
        let second = execute_transition(&Echo, &request("{}"));
        assert_eq!(first, second);
        let WorldTransitionResultV1::Accepted(accepted) = first else {
            panic!("expected accepted transition");
        };
        accepted.validate().unwrap();
        parse_canonical(&accepted.to_canonical_json().unwrap(), 8 * 1024 * 1024).unwrap();
    }

    #[test]
    fn unknown_revisions_are_typed_nonretryable_rejections() {
        let mut unknown = request("{}");
        unknown.ruleset_revision = "ruleset-v2".to_string();
        let WorldTransitionResultV1::Rejected(rejected) = execute_transition(&Echo, &unknown)
        else {
            panic!("expected rejection");
        };
        assert_eq!(rejected.code, TransitionErrorCodeV1::UnknownRulesetRevision);
        assert!(!rejected.retryable);
    }

    #[test]
    fn error_codes_are_unique_and_stable() {
        let codes = TransitionErrorCodeV1::ALL
            .into_iter()
            .map(TransitionErrorCodeV1::as_str)
            .collect::<BTreeSet<_>>();
        assert_eq!(codes.len(), TransitionErrorCodeV1::ALL.len());
    }

    #[test]
    fn diagnostics_are_bounded_by_utf8_bytes_and_control_free() {
        let value = sanitize_detail(&format!("secret\n{}", "界".repeat(200)));
        assert!(value.len() <= MAX_ERROR_DETAIL_BYTES);
        assert!(!value.chars().any(char::is_control));
    }
}
