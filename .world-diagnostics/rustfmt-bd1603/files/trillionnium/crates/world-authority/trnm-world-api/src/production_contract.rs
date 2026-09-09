//! Fail-closed contracts for production World runtime adapters.
//!
//! This module defines the data and error boundary only. It deliberately ships
//! no production implementation and grants no deployment or release credit.

use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt;
use trnm_world_command::WorldCommand;
use trnm_world_domain::WorldState;
use trnm_world_projection::WorldRouteRecords;

use super::{
    WorldAccountAuthDecision, WorldActorIdentity, WorldEvidenceReceipt, WorldLedgerReceipt,
    WorldMetricReceipt, WorldRepositoryReceipt, WorldSessionDecision,
};

pub const WORLD_PRODUCTION_ADAPTER_CONTRACT: &str = "trillionnium_world_production_adapter_v1";
pub const WORLD_PRODUCTION_AUTHORIZATION: &str = "not_granted";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorldProductionAdapterErrorCode {
    InvalidInput,
    UnsupportedContract,
    Unauthenticated,
    Unauthorized,
    AudienceMismatch,
    StaleSession,
    StaleWriterEpoch,
    StaleRevision,
    IdentityConflict,
    RepositoryUnavailable,
    RepositoryConflict,
    AmbiguousCommit,
    LedgerUnavailable,
    LedgerAmbiguous,
    LedgerPermanentRejection,
    EvidenceUnavailable,
    MetricsDegraded,
    RoutingRejected,
    CapacityExceeded,
    ShuttingDown,
    InternalIntegrity,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldProductionAdapterError {
    pub adapter_contract: String,
    pub code: WorldProductionAdapterErrorCode,
    pub retryable: bool,
    pub safe_message: String,
}

impl WorldProductionAdapterError {
    pub fn new(
        code: WorldProductionAdapterErrorCode,
        retryable: bool,
        safe_message: impl Into<String>,
    ) -> Self {
        Self {
            adapter_contract: WORLD_PRODUCTION_ADAPTER_CONTRACT.to_string(),
            code,
            retryable,
            safe_message: safe_message.into(),
        }
    }
}

impl fmt::Display for WorldProductionAdapterError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.safe_message)
    }
}

impl Error for WorldProductionAdapterError {}

pub type WorldProductionAdapterResult<T> = Result<T, WorldProductionAdapterError>;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldCredentialReference {
    pub adapter_contract: String,
    pub credential_id: String,
    pub issuer: String,
    pub audience: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldIdentityResolutionRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub credential: WorldCredentialReference,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldSessionAuthorizationRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub actor_id: String,
    pub account_id: String,
    pub session_id: String,
    pub expected_generation: u64,
    pub expected_audience: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAccountOperationRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub actor_id: String,
    pub account_id: String,
    pub session_id: String,
    pub session_generation: u64,
    pub operation: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldMutationContext {
    pub adapter_contract: String,
    pub request_id: String,
    pub actor_id: String,
    pub account_id: String,
    pub session_id: String,
    pub session_generation: u64,
    pub aggregate_id: String,
    pub expected_revision: u64,
    pub writer_epoch: u64,
    pub api_contract: String,
    pub domain_contract: String,
    pub command_contract: String,
    pub command_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAggregateLoadRequest {
    pub adapter_contract: String,
    pub aggregate_id: String,
    pub expected_writer_epoch: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorldAggregateSnapshot {
    pub adapter_contract: String,
    pub aggregate_id: String,
    pub revision: u64,
    pub writer_epoch: u64,
    pub state_sha256: String,
    pub state: WorldState,
    pub route_records: WorldRouteRecords,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorldAggregateCommitRequest {
    pub context: WorldMutationContext,
    pub command: WorldCommand,
    pub next_revision: u64,
    pub next_state_sha256: String,
    pub next_state: WorldState,
    pub next_route_records: WorldRouteRecords,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAggregateRequestLookup {
    pub adapter_contract: String,
    pub aggregate_id: String,
    pub request_id: String,
    pub request_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAggregateCommitReceipt {
    pub adapter_contract: String,
    pub request_id: String,
    pub event_id: String,
    pub aggregate_id: String,
    pub previous_revision: u64,
    pub next_revision: u64,
    pub writer_epoch: u64,
    pub state_sha256: String,
    pub exact_duplicate: bool,
    pub repository_receipt: WorldRepositoryReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldLedgerLookupRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub intent_id: String,
    pub intent_sha256: String,
    pub account_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldLedgerSubmitRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub intent_id: String,
    pub intent_sha256: String,
    pub account_id: String,
    pub amount_units: u64,
    pub currency: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldEvidenceAppendRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub evidence_kind: String,
    pub subject_id: String,
    pub payload_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldMetricRecordRequest {
    pub adapter_contract: String,
    pub metric_name: String,
    pub value: i64,
    pub bounded_labels: Vec<(String, String)>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldRoutingAuthorizationRequest {
    pub adapter_contract: String,
    pub request_id: String,
    pub caller_identity: String,
    pub caller_audience: String,
    pub component_lock_id: String,
    pub route: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldRoutingDecision {
    pub adapter_contract: String,
    pub accepted: bool,
    pub caller_identity: String,
    pub component_lock_id: String,
    pub reason_code: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldDeploymentIdentity {
    pub adapter_contract: String,
    pub deployment_id: String,
    pub environment: String,
    pub service_identity: String,
    pub component_lock_id: String,
    pub source_commit: String,
    pub source_tree: String,
    pub binary_sha256: String,
}

pub trait WorldProductionIdentityAdapter {
    fn resolve_actor(
        &self,
        request: &WorldIdentityResolutionRequest,
    ) -> WorldProductionAdapterResult<WorldActorIdentity>;
}

pub trait WorldProductionSessionGuard {
    fn authorize_session(
        &self,
        request: &WorldSessionAuthorizationRequest,
    ) -> WorldProductionAdapterResult<WorldSessionDecision>;
}

pub trait WorldProductionAccountAdapter {
    fn apply_account_operation(
        &self,
        request: &WorldAccountOperationRequest,
    ) -> WorldProductionAdapterResult<WorldAccountAuthDecision>;
}

pub trait WorldProductionRepository {
    fn load_snapshot(
        &self,
        request: &WorldAggregateLoadRequest,
    ) -> WorldProductionAdapterResult<WorldAggregateSnapshot>;

    fn lookup_request(
        &self,
        request: &WorldAggregateRequestLookup,
    ) -> WorldProductionAdapterResult<Option<WorldAggregateCommitReceipt>>;

    fn commit(
        &self,
        request: &WorldAggregateCommitRequest,
    ) -> WorldProductionAdapterResult<WorldAggregateCommitReceipt>;
}

pub trait WorldProductionLedgerAdapter {
    fn lookup_reward(
        &self,
        request: &WorldLedgerLookupRequest,
    ) -> WorldProductionAdapterResult<Option<WorldLedgerReceipt>>;

    fn submit_reward(
        &self,
        request: &WorldLedgerSubmitRequest,
    ) -> WorldProductionAdapterResult<WorldLedgerReceipt>;
}

pub trait WorldProductionEvidenceSink {
    fn append_evidence(
        &self,
        request: &WorldEvidenceAppendRequest,
    ) -> WorldProductionAdapterResult<WorldEvidenceReceipt>;
}

pub trait WorldProductionMetricsSink {
    fn record_metric(
        &self,
        request: &WorldMetricRecordRequest,
    ) -> WorldProductionAdapterResult<WorldMetricReceipt>;
}

pub trait WorldProductionRoutingAdapter {
    fn authorize_internal_route(
        &self,
        request: &WorldRoutingAuthorizationRequest,
    ) -> WorldProductionAdapterResult<WorldRoutingDecision>;
}

pub trait WorldProductionDeploymentAdapter {
    fn deployment_identity(&self) -> WorldProductionAdapterResult<WorldDeploymentIdentity>;
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldProductionAdapterCapability {
    pub role: String,
    pub contract_defined: bool,
    pub implementation_available: bool,
    pub exact_head_qualified: bool,
    pub external_evidence_required: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldProductionAdapterReadiness {
    pub adapter_contract: String,
    pub production_ready: bool,
    pub production_authorization: String,
    pub fixture_adapter_credit_allowed: bool,
    pub capabilities: Vec<WorldProductionAdapterCapability>,
    pub blocking_reasons: Vec<String>,
}

pub fn world_production_adapter_readiness() -> WorldProductionAdapterReadiness {
    let roles = [
        "identity",
        "session_guard",
        "account",
        "repository",
        "ledger",
        "evidence_sink",
        "metrics_sink",
        "internal_routing",
        "deployment_identity",
    ];
    WorldProductionAdapterReadiness {
        adapter_contract: WORLD_PRODUCTION_ADAPTER_CONTRACT.to_string(),
        production_ready: false,
        production_authorization: WORLD_PRODUCTION_AUTHORIZATION.to_string(),
        fixture_adapter_credit_allowed: false,
        capabilities: roles
            .into_iter()
            .map(|role| WorldProductionAdapterCapability {
                role: role.to_string(),
                contract_defined: true,
                implementation_available: false,
                exact_head_qualified: false,
                external_evidence_required: true,
            })
            .collect(),
        blocking_reasons: vec![
            "production_adapter_implementations_absent".to_string(),
            "exact_head_black_box_qualification_absent".to_string(),
            "deployment_identity_and_component_lock_absent".to_string(),
            "production_authorization_not_granted".to_string(),
        ],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_readiness_is_explicitly_fail_closed() {
        let readiness = world_production_adapter_readiness();
        assert_eq!(
            readiness.adapter_contract,
            WORLD_PRODUCTION_ADAPTER_CONTRACT
        );
        assert!(!readiness.production_ready);
        assert_eq!(
            readiness.production_authorization,
            WORLD_PRODUCTION_AUTHORIZATION
        );
        assert!(!readiness.fixture_adapter_credit_allowed);
        assert_eq!(readiness.capabilities.len(), 9);
        assert!(readiness
            .capabilities
            .iter()
            .all(|capability| capability.contract_defined));
        assert!(readiness
            .capabilities
            .iter()
            .all(|capability| !capability.implementation_available));
        assert!(readiness
            .capabilities
            .iter()
            .all(|capability| !capability.exact_head_qualified));
    }

    #[test]
    fn error_codes_serialize_to_stable_machine_values() {
        assert_eq!(
            serde_json::to_string(&WorldProductionAdapterErrorCode::AmbiguousCommit).unwrap(),
            "\"ambiguous_commit\""
        );
        let error = WorldProductionAdapterError::new(
            WorldProductionAdapterErrorCode::LedgerAmbiguous,
            true,
            "exact receipt lookup required",
        );
        assert_eq!(error.adapter_contract, WORLD_PRODUCTION_ADAPTER_CONTRACT);
        assert!(error.retryable);
    }

    #[test]
    fn credential_reference_contains_no_secret_value_field() {
        let request = WorldIdentityResolutionRequest {
            adapter_contract: WORLD_PRODUCTION_ADAPTER_CONTRACT.to_string(),
            request_id: "request-1".to_string(),
            credential: WorldCredentialReference {
                adapter_contract: WORLD_PRODUCTION_ADAPTER_CONTRACT.to_string(),
                credential_id: "credential-reference-1".to_string(),
                issuer: "nakama".to_string(),
                audience: "trillionnium-world-internal".to_string(),
            },
        };
        let encoded = serde_json::to_string(&request).unwrap();
        assert!(!encoded.contains("password"));
        assert!(!encoded.contains("token_value"));
        assert!(!encoded.contains("cookie_value"));
        assert!(!encoded.contains("private_key"));
    }
}
