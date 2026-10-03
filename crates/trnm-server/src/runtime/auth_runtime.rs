//! One selected authority. Clones share owned verifier keys or the exact legacy
//! service/cache; no token sniffing, durable-family conversion or key fallback.
use std::sync::Arc;

use super::app::Repository;
use super::auth::{AccessTokenVerifier, SessionPrincipal};
use super::config::AuthAuthorityConfig;
use super::error::ServerError;
use super::http::{Request, Response};
use super::legacy_auth::{
    LegacyAccessPrincipal, LegacyAuthError, LegacyAuthService, LegacyDeviceAccount,
    LegacyDeviceRepository, LegacyDeviceRepositoryInput, LegacyRepositoryError, LegacyStoredUser,
    LegacyUserRepository,
};
use super::legacy_http_api::{
    self as wire, LegacyAuthHttpRoute, LegacyGatewayError, LegacyHttpLimits, LegacyHttpServerKey,
};
use super::session_api::{SessionApi, SessionApiMetrics, SessionError};
use trnm_contracts::{DomainError, RetryClass, StableCode, UserId};

#[derive(Clone, Default)]
pub(crate) enum AuthAuthorityRuntime {
    #[default]
    Disabled,
    DurableFamily(SessionApi),
    NakamaLegacy {
        server_key: Arc<LegacyHttpServerKey>,
        service: LegacyAuthService,
    },
}
impl std::fmt::Debug for AuthAuthorityRuntime {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Disabled => "Disabled",
            Self::DurableFamily(_) => "DurableFamily(<redacted>)",
            Self::NakamaLegacy { .. } => "NakamaLegacy(<redacted>)",
        })
    }
}

// Private provenance is retained until the existing storage ACL consumes user().
// There is deliberately no constructor from legacy claims to SessionPrincipal.
pub(super) enum AccessContext {
    Durable(SessionPrincipal),
    Legacy(LegacyAccessPrincipal),
}
impl AccessContext {
    pub(super) fn user(&self) -> UserId {
        match self {
            Self::Durable(p) => p.user,
            Self::Legacy(p) => p.user(),
        }
    }
}
pub(super) enum AccessError {
    Durable(SessionError),
    Legacy(LegacyGatewayError),
}
fn unavailable() -> SessionError {
    SessionError::Domain(DomainError::new(
        StableCode::Unimplemented,
        "session_authentication_not_configured",
        RetryClass::Never,
    ))
}
impl AuthAuthorityRuntime {
    /// Called once by serve, after selected catalog and import verification.
    /// Config parsing/check-config never calls this service-starting constructor.
    pub(super) fn install(config: &AuthAuthorityConfig) -> Result<Self, ServerError> {
        match config {
            AuthAuthorityConfig::Disabled => Ok(Self::Disabled),
            AuthAuthorityConfig::DurableFamily(config) => Ok(Self::durable(config.verifier()?)),
            AuthAuthorityConfig::NakamaLegacy(config) => {
                let server_key = Arc::new(config.server_key()?);
                let service = LegacyAuthService::from_config(config.service_config()?)
                    .map_err(|_| ServerError::Configuration("legacy_auth_runtime_start_failed"))?;
                Ok(Self::NakamaLegacy {
                    server_key,
                    service,
                })
            }
        }
    }
    pub(super) fn durable(verifier: AccessTokenVerifier) -> Self {
        let mut sessions = SessionApi::default();
        sessions.configure(verifier);
        Self::DurableFamily(sessions)
    }
    pub(super) fn metrics(&self) -> SessionApiMetrics {
        match self {
            Self::DurableFamily(api) => api.metrics(),
            Self::Disabled | Self::NakamaLegacy { .. } => SessionApiMetrics::default(),
        }
    }
    pub(super) fn session_request<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<Response, SessionError> {
        match self {
            Self::DurableFamily(api) => api.handle(repository, request),
            Self::Disabled | Self::NakamaLegacy { .. } => Err(unavailable()),
        }
    }
    pub(super) fn access<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<AccessContext, AccessError> {
        match self {
            Self::DurableFamily(api) => api
                .authenticate(repository, request)
                .map(AccessContext::Durable)
                .map_err(AccessError::Durable),
            Self::NakamaLegacy { service, .. } => wire::require_legacy_access_bearer(
                service,
                request.header("authorization"),
                LegacyHttpLimits::default(),
            )
            .map(AccessContext::Legacy)
            .map_err(AccessError::Legacy),
            Self::Disabled => Err(AccessError::Durable(unavailable())),
        }
    }
    pub(super) fn legacy_request<R: Repository>(
        &self,
        repository: &mut R,
        request: &Request,
        route: LegacyAuthHttpRoute,
    ) -> Response {
        let Self::NakamaLegacy {
            server_key,
            service,
        } = self
        else {
            return gateway_response(wire::legacy_gateway_error(&LegacyAuthError::Repository(
                LegacyRepositoryError::Unimplemented,
            )));
        };
        let limits = LegacyHttpLimits::default();
        let mut repository = AccountBridge(repository);
        let result: Result<Vec<u8>, LegacyGatewayError> = (|| match route {
            LegacyAuthHttpRoute::AuthenticateDevice => {
                let query = request
                    .target
                    .split_once('?')
                    .map_or("", |(_, query)| query);
                let input = wire::decode_device_http_request(
                    server_key,
                    request.header("authorization"),
                    &request.body,
                    query,
                    limits,
                )?;
                let session = service
                    .authenticate_device(&mut repository, input.auth_input())
                    .map_err(|e| wire::legacy_device_gateway_error(&e))?;
                let encoded = wire::encode_legacy_session(&session, limits);
                if encoded.is_err() && session.created {
                    // No retry/compensation. The completed account remains committed.
                    eprintln!("trnm-server legacy response encoding failed after committed creation cleanup_present={}",session.committed_cleanup_failure.is_some());
                }
                encoded
            }
            LegacyAuthHttpRoute::Refresh => {
                let input = wire::decode_refresh_http_request(
                    server_key,
                    request.header("authorization"),
                    &request.body,
                    limits,
                )?;
                let session = service
                    .refresh(&mut repository, input.token(), input.variables())
                    .map_err(|e| wire::legacy_gateway_error(&e))?;
                wire::encode_legacy_session(&session, limits)
            }
            LegacyAuthHttpRoute::Logout => {
                let principal = wire::require_legacy_access_bearer(
                    service,
                    request.header("authorization"),
                    limits,
                )?;
                let input = wire::decode_logout_http_request(&principal, &request.body, limits)?;
                service
                    .logout_authenticated(&principal, input.token(), input.refresh_token())
                    .map_err(|e| wire::legacy_gateway_error(&e))?;
                Ok(wire::encode_legacy_logout())
            }
        })();
        match result {
            Ok(body) => Response::json(200, body),
            Err(error) => gateway_response(error),
        }
    }
}
pub(super) fn gateway_response(error: LegacyGatewayError) -> Response {
    Response::json(error.status(), error.json_body())
        .with_www_authenticate(error.www_authenticate())
}
// Each forwarded call reaches the existing concrete typed one-lease path. This
// bridge adds no lookup, connection, transaction retry or diagnostic flattening.
struct AccountBridge<'a, R>(&'a mut R);
impl<R: Repository> LegacyUserRepository for AccountBridge<'_, R> {
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError> {
        self.0.read_legacy_user(user)
    }
}
impl<R: Repository> LegacyDeviceRepository for AccountBridge<'_, R> {
    fn authenticate_legacy_device(
        &mut self,
        input: LegacyDeviceRepositoryInput<'_>,
    ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
        self.0.authenticate_legacy_device(input)
    }
}
