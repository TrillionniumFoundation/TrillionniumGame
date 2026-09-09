//! Stable API contracts for the standalone Trillionnium World development environment.

pub mod production_contract;
pub use production_contract::*;

use serde::{Deserialize, Serialize};
use trnm_world_command::{WorldCommand, WorldCommandDecision, WorldTacticsCommandOutcome};
use trnm_world_domain::{WorldState, WORLD_DOMAIN_CONTRACT};
use trnm_world_map_provider::MapProviderStatus;
use trnm_world_projection::{
    WorldHomeProjection, WorldRouteArtifacts, WorldRouteCommandTarget, WorldRouteRecords,
};

pub const WORLD_API_CONTRACT: &str = "trillionnium_world_api_v1";
pub const WORLD_RUNTIME_ADAPTER_CONTRACT: &str = "trillionnium_world_runtime_adapter_v1";
pub const WORLD_FULL_SPLIT_RESPONSE_CONTRACT: &str = "trillionnium_world_full_split_response_v1";
pub const WORLD_ACCOUNT_API_CONTRACT: &str = "trillionnium_world_account_api_v1";
pub const WORLD_ACCOUNT_CLIENT_BOUNDARY_CONTRACT: &str =
    "trillionnium_world_account_client_boundary_v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldApiHomeResponse {
    pub api_contract: String,
    pub domain_contract: String,
    pub home: WorldHomeProjection,
    pub map_provider: MapProviderStatus,
}

impl WorldApiHomeResponse {
    pub fn new(home: WorldHomeProjection, map_provider: MapProviderStatus) -> Self {
        Self {
            api_contract: WORLD_API_CONTRACT.to_string(),
            domain_contract: WORLD_DOMAIN_CONTRACT.to_string(),
            home,
            map_provider,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldApiCommandRequest {
    pub api_contract: String,
    pub command: WorldCommand,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldApiCommandResponse {
    pub api_contract: String,
    pub decision: WorldCommandDecision,
    pub state: WorldState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldApiRouteCommandTargetResponse {
    pub api_contract: String,
    pub target: WorldRouteCommandTarget,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldApiRouteArtifactsResponse {
    pub api_contract: String,
    pub artifacts: WorldRouteArtifacts,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldApiMapRuntimeBudgetResponse {
    pub api_contract: String,
    pub budget: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorldApiTacticsCommandResponse {
    pub api_contract: String,
    pub outcome: WorldTacticsCommandOutcome,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldActorIdentity {
    pub adapter_contract: String,
    pub actor_id: String,
    pub matrix_user_id: String,
    pub display_name: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldSessionDecision {
    pub adapter_contract: String,
    pub accepted: bool,
    pub session_id: String,
    pub actor_id: String,
    pub reason: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAccountProfile {
    pub account_contract: String,
    pub account_id: String,
    pub actor_id: String,
    pub display_name: String,
    pub default_room_id: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAccountSession {
    pub account_contract: String,
    pub session_id: String,
    pub account_id: String,
    pub actor_id: String,
    pub session_generation: u64,
    pub csrf_bound: bool,
    pub http_only_cookie_required: bool,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldAccountAuthDecision {
    pub account_contract: String,
    pub boundary_contract: String,
    pub action: String,
    pub accepted: bool,
    pub reason: String,
    pub profile: WorldAccountProfile,
    pub session: Option<WorldAccountSession>,
    pub passwords_tokens_or_cookie_values_logged: bool,
    pub cex_runtime_player_client_allowed: bool,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldLedgerReceipt {
    pub adapter_contract: String,
    pub receipt_id: String,
    pub route_task_id: String,
    pub amount_units: u64,
    pub status: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldRepositoryReceipt {
    pub adapter_contract: String,
    pub receipt_id: String,
    pub state_contract: String,
    pub route_record_count: usize,
    pub status: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldEvidenceReceipt {
    pub adapter_contract: String,
    pub receipt_id: String,
    pub evidence_kind: String,
    pub status: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldMetricReceipt {
    pub adapter_contract: String,
    pub receipt_id: String,
    pub metric_name: String,
    pub value: i64,
    pub status: String,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldRuntimeAdapterStatus {
    pub adapter_contract: String,
    pub adapter_name: String,
    pub role: String,
    pub status: String,
    pub fixture_adapter_available: bool,
    pub production_adapter_trait_ready: bool,
    pub source_of_truth: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorldRuntimeAdapterReadiness {
    pub adapter_contract: String,
    pub cutover_status: String,
    pub cex_dependency_status: String,
    pub statuses: Vec<WorldRuntimeAdapterStatus>,
}

pub trait WorldIdentityAdapter {
    fn resolve_actor(&self, actor_id: &str) -> WorldActorIdentity;
}

pub trait WorldSessionGuard {
    fn authorize_world_session(&self, actor_id: &str) -> WorldSessionDecision;
}

pub trait WorldAccountAdapter {
    fn register_password_account(
        &self,
        actor_id: &str,
        display_name: &str,
    ) -> WorldAccountAuthDecision;
    fn login_password_account(&self, actor_id: &str) -> WorldAccountAuthDecision;
    fn resolve_account_session(&self, session_id: &str, actor_id: &str)
        -> WorldAccountAuthDecision;
    fn revoke_account_session(&self, session_id: &str, actor_id: &str) -> WorldAccountAuthDecision;
}

pub trait WorldLedgerAdapter {
    fn reserve_reward(&self, route_task_id: &str, amount_units: u64) -> WorldLedgerReceipt;
    fn release_reward(&self, receipt_id: &str) -> WorldLedgerReceipt;
}

pub trait WorldRepository {
    fn load_world(&self, actor_id: &str) -> WorldState;
    fn load_route_records(&self, actor_id: &str, world: &WorldState) -> WorldRouteRecords;
    fn save_world(&self, world: &WorldState, records: &WorldRouteRecords)
        -> WorldRepositoryReceipt;
}

pub trait WorldEvidenceSink {
    fn record_evidence(&self, evidence_kind: &str) -> WorldEvidenceReceipt;
}

pub trait WorldMetricsSink {
    fn record_metric(&self, metric_name: &str, value: i64) -> WorldMetricReceipt;
}

pub fn world_runtime_adapter_readiness() -> WorldRuntimeAdapterReadiness {
    let roles = [
        ("identity", "WorldIdentityAdapter"),
        ("session_guard", "WorldSessionGuard"),
        ("ledger", "WorldLedgerAdapter"),
        ("repository", "WorldRepository"),
        ("evidence_sink", "WorldEvidenceSink"),
        ("metrics_sink", "WorldMetricsSink"),
    ];
    WorldRuntimeAdapterReadiness {
        adapter_contract: WORLD_RUNTIME_ADAPTER_CONTRACT.to_string(),
        cutover_status: "fixture_adapters_only_production_implementations_absent".to_string(),
        cex_dependency_status:
            "trnm_world_crates_define_traits_without_importing_cex_service_internals".to_string(),
        statuses: roles
            .into_iter()
            .map(|(role, adapter_name)| WorldRuntimeAdapterStatus {
                adapter_contract: WORLD_RUNTIME_ADAPTER_CONTRACT.to_string(),
                adapter_name: adapter_name.to_string(),
                role: role.to_string(),
                status: "trait_seam_defined_fixture_adapter_green".to_string(),
                fixture_adapter_available: true,
                production_adapter_trait_ready: true,
                source_of_truth: "trnm_world_api_runtime_adapter_contracts".to_string(),
            })
            .collect(),
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorldApiFullSplitResponse {
    pub api_contract: String,
    pub response_contract: String,
    pub domain_contract: String,
    pub runtime_adapters: WorldRuntimeAdapterReadiness,
    pub projection: serde_json::Value,
    pub map_provider: MapProviderStatus,
}

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_world_domain::WorldState;
    use trnm_world_map_provider::fixture_provider_status;
    use trnm_world_projection::project_home;

    #[test]
    fn home_response_carries_contracts() {
        let response = WorldApiHomeResponse::new(
            project_home(&WorldState::fixture(), "local-player"),
            fixture_provider_status(),
        );
        assert_eq!(response.api_contract, WORLD_API_CONTRACT);
        assert_eq!(response.domain_contract, WORLD_DOMAIN_CONTRACT);
    }

    #[test]
    fn route_command_target_response_carries_api_contract() {
        let response = WorldApiRouteCommandTargetResponse {
            api_contract: WORLD_API_CONTRACT.to_string(),
            target: trnm_world_projection::world_route_command_target(
                "/work deliver latest 成果 证据 风险 下一步 自检",
            ),
        };
        assert_eq!(response.api_contract, WORLD_API_CONTRACT);
        assert_eq!(response.target.panel_id, "world-commerce-panel");
    }

    #[test]
    fn route_artifacts_response_carries_api_contract() {
        let response = WorldApiRouteArtifactsResponse {
            api_contract: WORLD_API_CONTRACT.to_string(),
            artifacts: trnm_world_projection::world_route_artifacts(
                serde_json::json!({"items": []}),
                serde_json::json!({"tasks": []}),
                4,
            ),
        };
        assert_eq!(response.api_contract, WORLD_API_CONTRACT);
        assert_eq!(
            response.artifacts.story.next_opportunity_kind,
            "contract_capture"
        );
    }

    #[test]
    fn runtime_adapter_readiness_declares_trait_seams_without_implementation_credit() {
        let readiness = world_runtime_adapter_readiness();
        assert_eq!(readiness.adapter_contract, WORLD_RUNTIME_ADAPTER_CONTRACT);
        assert_eq!(readiness.statuses.len(), 6);
        assert_eq!(
            readiness.cutover_status,
            "fixture_adapters_only_production_implementations_absent"
        );
        assert!(readiness
            .statuses
            .iter()
            .any(|status| status.adapter_name == "WorldLedgerAdapter"));
        assert!(readiness
            .statuses
            .iter()
            .all(|status| status.production_adapter_trait_ready));
        assert!(readiness
            .statuses
            .iter()
            .all(|status| status.status == "trait_seam_defined_fixture_adapter_green"));

        let production = world_production_adapter_readiness();
        assert!(!production.production_ready);
        assert_eq!(production.production_authorization, "not_granted");
        assert!(production
            .capabilities
            .iter()
            .all(|capability| !capability.implementation_available));
    }

    #[test]
    fn account_auth_decision_contract_is_trillionnium_owned() {
        let profile = WorldAccountProfile {
            account_contract: WORLD_ACCOUNT_API_CONTRACT.to_string(),
            account_id: "trnm-account:local-player".to_string(),
            actor_id: "local-player".to_string(),
            display_name: "Local Trillionnium Player".to_string(),
            default_room_id: "mirror-city-square".to_string(),
            source_of_truth: "test".to_string(),
        };
        let decision = WorldAccountAuthDecision {
            account_contract: WORLD_ACCOUNT_API_CONTRACT.to_string(),
            boundary_contract: WORLD_ACCOUNT_CLIENT_BOUNDARY_CONTRACT.to_string(),
            action: "login".to_string(),
            accepted: true,
            reason: "ok".to_string(),
            profile,
            session: None,
            passwords_tokens_or_cookie_values_logged: false,
            cex_runtime_player_client_allowed: false,
            source_of_truth: "trillionnium_owned_account_api".to_string(),
        };
        assert_eq!(decision.account_contract, WORLD_ACCOUNT_API_CONTRACT);
        assert_eq!(
            decision.boundary_contract,
            WORLD_ACCOUNT_CLIENT_BOUNDARY_CONTRACT
        );
        assert!(!decision.passwords_tokens_or_cookie_values_logged);
        assert!(!decision.cex_runtime_player_client_allowed);
    }
}
