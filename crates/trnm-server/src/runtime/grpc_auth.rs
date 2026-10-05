//! Bounded native gRPC proposal. No HTTP/JSON round trip or second authority.
//! Source: Nakama d4d92f93 server/api.go and the pinned common v1.47 API.
//! Dropping an RPC abandons its response, not an already-started native operation.
//! In-flight DB work retains the existing pool's finite deadline; no global cancel,
//! replay, compensation, or assertion of no committed effect is permitted here.
use std::collections::BTreeMap;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use tonic::{metadata::MetadataMap, Request, Response, Status};
use trnm_contracts::{StableCode, UserId};

use crate::runtime::app::{Repository, SharedDrain};
use crate::runtime::auth_runtime::AuthAuthorityRuntime;
use crate::runtime::legacy_auth::{
    LegacyAuthService, LegacyCustomAccount, LegacyCustomAuthInput, LegacyCustomRepository,
    LegacyCustomRepositoryInput, LegacyDeviceAccount, LegacyDeviceAuthInput,
    LegacyDeviceRepository, LegacyDeviceRepositoryInput, LegacyRepositoryError, LegacySession,
    LegacyStoredUser, LegacyUserRepository,
};
use crate::runtime::legacy_http_api::{self as wire, LegacyGatewayError, LegacyHttpLimits};

use super::generated::google::protobuf::Empty;
use super::generated::nakama::api::{
    nakama_server::Nakama, AuthenticateCustomRequest, AuthenticateDeviceRequest,
    DeleteStorageObjectsRequest, ReadStorageObjectsRequest, Session, SessionLogoutRequest,
    SessionRefreshRequest, StorageObjectAcks, StorageObjects, WriteStorageObjectsRequest,
};

#[path = "grpc_storage.rs"]
mod storage;
#[path = "grpc_storage_mutation.rs"]
mod storage_mutation;

pub(super) const MAX_AUTH_JOBS: usize = 16;
pub(super) const MAX_REQUEST_BYTES: usize = 512 * 1024;
pub(super) const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;

// URI-aware boundary runs before prost receives a body. No request data is
// translated into the HTTP/JSON adapter, and Healthcheck stays unauthenticated.
#[derive(Clone)]
pub(super) struct AuthBoundary<S> {
    inner: S,
    authority: AuthAuthorityRuntime,
}
impl<S> AuthBoundary<S> {
    pub(super) fn new(inner: S, authority: AuthAuthorityRuntime) -> Self {
        Self { inner, authority }
    }
}
impl<S: tonic::server::NamedService> tonic::server::NamedService for AuthBoundary<S> {
    const NAME: &'static str = S::NAME;
}
impl<S, B> tonic::codegen::Service<tonic::codegen::http::Request<B>> for AuthBoundary<S>
where
    S: tonic::codegen::Service<
        tonic::codegen::http::Request<B>,
        Response = tonic::codegen::http::Response<tonic::body::Body>,
    >,
    S::Future: Send + 'static,
{
    type Response = tonic::codegen::http::Response<tonic::body::Body>;
    type Error = S::Error;
    type Future = tonic::codegen::BoxFuture<Self::Response, Self::Error>;
    fn poll_ready(
        &mut self,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }
    fn call(&mut self, request: tonic::codegen::http::Request<B>) -> Self::Future {
        let metadata = MetadataMap::from_headers(request.headers().clone());
        if let Err(error) = authorize_method(&self.authority, request.uri().path(), &metadata) {
            return Box::pin(async move { Ok(error.into_http()) });
        }
        Box::pin(self.inner.call(request))
    }
}
fn authorize_method(
    authority: &AuthAuthorityRuntime,
    path: &str,
    metadata: &MetadataMap,
) -> Result<(), Status> {
    let bearer = match path {
        "/nakama.api.Nakama/AuthenticateDevice"
        | "/nakama.api.Nakama/AuthenticateCustom"
        | "/nakama.api.Nakama/SessionRefresh" => false,
        "/nakama.api.Nakama/SessionLogout"
        | "/nakama.api.Nakama/ReadStorageObjects"
        | "/nakama.api.Nakama/WriteStorageObjects"
        | "/nakama.api.Nakama/DeleteStorageObjects" => true,
        _ => return Ok(()), // Generated router owns unknown-method Unimplemented.
    };
    let AuthAuthorityRuntime::NakamaLegacy {
        server_key,
        service,
    } = authority
    else {
        return Err(Status::unimplemented("Legacy authentication unavailable."));
    };
    let value = authorization(metadata, bearer)?;
    if bearer {
        wire::require_legacy_access_bearer(service, value, LegacyHttpLimits::default())
            .map(|_| ())
            .map_err(status)
    } else {
        wire::require_basic_server_key(server_key, value, LegacyHttpLimits::default())
            .map_err(status)
    }
}

pub(super) struct NativeGrpcService<R> {
    repository: Mutex<R>,
    authority: AuthAuthorityRuntime,
    fence: FailureFence,
    jobs: Arc<AtomicUsize>,
}
impl<R> NativeGrpcService<R> {
    pub(super) fn new(
        repository: R,
        authority: AuthAuthorityRuntime,
        draining: SharedDrain,
        worker_failed: Arc<AtomicBool>,
    ) -> Self {
        Self {
            repository: Mutex::new(repository),
            authority,
            fence: FailureFence {
                draining,
                worker_failed,
            },
            jobs: Arc::new(AtomicUsize::new(0)),
        }
    }
    fn legacy(&self) -> Result<(&wire::LegacyHttpServerKey, &LegacyAuthService), Status> {
        match &self.authority {
            AuthAuthorityRuntime::NakamaLegacy {
                server_key,
                service,
            } => Ok((server_key, service)),
            _ => Err(Status::unimplemented("Legacy authentication unavailable.")),
        }
    }
    fn server_key(&self, metadata: &MetadataMap) -> Result<(), Status> {
        let (key, _) = self.legacy()?;
        let value = authorization(metadata, false)?;
        wire::require_basic_server_key(key, value, LegacyHttpLimits::default()).map_err(status)
    }
}

struct JobPermit(Arc<AtomicUsize>);
impl JobPermit {
    fn acquire(jobs: &Arc<AtomicUsize>) -> Result<Self, Status> {
        jobs.fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| {
            (n < MAX_AUTH_JOBS).then_some(n + 1)
        })
        .map_err(|_| Status::resource_exhausted("Legacy request resource limit exceeded."))?;
        Ok(Self(Arc::clone(jobs)))
    }
}
impl Drop for JobPermit {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}
struct AbandonOnDrop(Arc<AtomicBool>);
impl Drop for AbandonOnDrop {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
impl<R: Repository + Clone + Send + 'static> NativeGrpcService<R> {
    async fn run<T: Send + 'static>(
        &self,
        operation: impl FnOnce(&LegacyAuthService, &mut AccountBridge<'_, R>) -> Result<T, Status>
            + Send
            + 'static,
    ) -> Result<Response<T>, Status> {
        if self.fence.draining.is_draining() {
            return Err(Status::unavailable("Server draining."));
        }
        let (service, mut repository) = self.fence.protect(|| {
            let service = self.legacy()?.1.clone();
            let repository = self
                .repository
                .lock()
                .map_err(|_| Status::internal("Legacy authentication unavailable."))?
                .clone();
            Ok((service, repository))
        })?;
        run_owned(Arc::clone(&self.jobs), self.fence.clone(), move || {
            operation(&service, &mut AccountBridge(&mut repository))
        })
        .await
        .map(Response::new)
    }
}

#[derive(Clone)]
struct FailureFence {
    draining: SharedDrain,
    worker_failed: Arc<AtomicBool>,
}
impl FailureFence {
    fn protect<T>(&self, operation: impl FnOnce() -> Result<T, Status>) -> Result<T, Status> {
        match catch_unwind(AssertUnwindSafe(operation)) {
            Ok(result) => result,
            Err(_) => {
                super::signal_failure(&self.draining, self.worker_failed.as_ref());
                Err(Status::internal("Legacy authentication unavailable."))
            }
        }
    }
}

async fn run_owned<T: Send + 'static>(
    jobs: Arc<AtomicUsize>,
    fence: FailureFence,
    operation: impl FnOnce() -> Result<T, Status> + Send + 'static,
) -> Result<T, Status> {
    let permit = JobPermit::acquire(&jobs)?;
    let abandoned = Arc::new(AtomicBool::new(false));
    let guard = AbandonOnDrop(Arc::clone(&abandoned));
    let job_fence = fence.clone();
    // Catch spawning/preparation panics too. The job itself owns a clone of the
    // same process fence, so it cannot depend on an RPC still awaiting its result.
    let handle = fence.protect(|| Ok(tokio::task::spawn_blocking(move || {
        let _permit = permit;
        job_fence.protect(|| {
            if abandoned.load(Ordering::Acquire) { return Err(Status::cancelled("Request cancelled.")); }
            if job_fence.draining.is_draining() { return Err(Status::unavailable("Server draining.")); }
            let result = operation();
            if abandoned.load(Ordering::Acquire) {
                eprintln!("trnm-server gRPC auth response abandoned; native outcome must not be replayed");
            }
            result
        })
        // Permit is released only after operation cleanup or unwind finishes.
    })))?;
    let result = match handle.await {
        Ok(result) => result,
        Err(_) => {
            super::signal_failure(&fence.draining, fence.worker_failed.as_ref());
            Err(Status::internal("Legacy authentication unavailable."))
        }
    };
    drop(guard);
    result
}

#[tonic::async_trait]
impl<R: Repository + Clone + Send + 'static> Nakama for NativeGrpcService<R> {
    async fn healthcheck(&self, _: Request<Empty>) -> Result<Response<Empty>, Status> {
        Ok(Response::new(Empty {}))
    }
    async fn authenticate_device(
        &self,
        request: Request<AuthenticateDeviceRequest>,
    ) -> Result<Response<Session>, Status> {
        self.server_key(request.metadata())?;
        let input = request.into_inner();
        if let Some(account) = &input.account {
            check_vars(&account.vars)?;
        }
        self.run(move |service, repository| {
            let session = service
                .authenticate_device(
                    repository,
                    LegacyDeviceAuthInput {
                        account_id: input.account.as_ref().map(|a| a.id.as_str()),
                        username: &input.username,
                        create: input.create.map(|v| v.value),
                        variables: input.account.as_ref().and_then(|a| present_vars(&a.vars)),
                    },
                )
                .map_err(|e| status(wire::legacy_device_gateway_error(&e)))?;
            Ok(encode_session(session))
        })
        .await
    }
    async fn authenticate_custom(
        &self,
        request: Request<AuthenticateCustomRequest>,
    ) -> Result<Response<Session>, Status> {
        self.server_key(request.metadata())?;
        let input = request.into_inner();
        if let Some(account) = &input.account {
            check_vars(&account.vars)?;
        }
        self.run(move |service, repository| {
            let session = service
                .authenticate_custom(
                    repository,
                    LegacyCustomAuthInput {
                        account_id: input.account.as_ref().map(|a| a.id.as_str()),
                        username: &input.username,
                        create: input.create.map(|v| v.value),
                        variables: input.account.as_ref().and_then(|a| present_vars(&a.vars)),
                    },
                )
                .map_err(|e| status(wire::legacy_custom_gateway_error(&e)))?;
            Ok(encode_session(session))
        })
        .await
    }
    async fn session_refresh(
        &self,
        request: Request<SessionRefreshRequest>,
    ) -> Result<Response<Session>, Status> {
        self.server_key(request.metadata())?;
        let input = request.into_inner();
        check_vars(&input.vars)?;
        self.run(move |service, repository| {
            service
                .refresh(
                    repository,
                    input.token.as_bytes(),
                    present_vars(&input.vars),
                )
                .map(encode_session)
                .map_err(|e| status(wire::legacy_gateway_error(&e)))
        })
        .await
    }
    async fn read_storage_objects(
        &self,
        request: Request<ReadStorageObjectsRequest>,
    ) -> Result<Response<StorageObjects>, Status> {
        // Same installed access authority and blacklist as HTTP/Logout. Basic
        // server credentials and refresh tokens never become a player principal.
        let principal = wire::require_legacy_access_bearer(
            self.legacy()?.1,
            authorization(request.metadata(), true)?,
            LegacyHttpLimits::default(),
        )
        .map_err(status)?;
        let input = request.into_inner();
        self.run(move |_, repository| storage::read_objects(repository.0, input, principal.user()))
            .await
    }
    async fn write_storage_objects(
        &self,
        request: Request<WriteStorageObjectsRequest>,
    ) -> Result<Response<StorageObjectAcks>, Status> {
        let principal = wire::require_legacy_access_bearer(
            self.legacy()?.1,
            authorization(request.metadata(), true)?,
            LegacyHttpLimits::default(),
        )
        .map_err(status)?;
        let input = request.into_inner();
        self.run(move |_, repository| {
            storage_mutation::write_objects(repository.0, input, principal.user())
        })
        .await
    }
    async fn delete_storage_objects(
        &self,
        request: Request<DeleteStorageObjectsRequest>,
    ) -> Result<Response<Empty>, Status> {
        let principal = wire::require_legacy_access_bearer(
            self.legacy()?.1,
            authorization(request.metadata(), true)?,
            LegacyHttpLimits::default(),
        )
        .map_err(status)?;
        let input = request.into_inner();
        self.run(move |_, repository| {
            storage_mutation::delete_objects(repository.0, input, principal.user())
        })
        .await
    }
    async fn session_logout(
        &self,
        request: Request<SessionLogoutRequest>,
    ) -> Result<Response<Empty>, Status> {
        let principal = wire::require_legacy_access_bearer(
            self.legacy()?.1,
            authorization(request.metadata(), true)?,
            LegacyHttpLimits::default(),
        )
        .map_err(status)?;
        let input = request.into_inner();
        self.run(move |service, _| {
            service
                .logout_authenticated(
                    &principal,
                    input.token.as_bytes(),
                    input.refresh_token.as_bytes(),
                )
                .map_err(|e| status(wire::legacy_gateway_error(&e)))?;
            Ok(Empty {})
        })
        .await
    }
}

// gRPC metadata is multi-valued. The fallback is used only when the primary
// key is absent, never when it is empty, repeated, or invalid.
fn authorization(metadata: &MetadataMap, bearer: bool) -> Result<Option<&str>, Status> {
    let key = if metadata.contains_key("authorization") {
        "authorization"
    } else {
        "grpcgateway-authorization"
    };
    let all = metadata.get_all(key);
    let mut values = all.iter();
    let Some(value) = values.next() else {
        return Ok(None);
    };
    if values.next().is_some() {
        return Err(Status::unauthenticated(if bearer {
            "Auth token invalid"
        } else {
            "Server key required"
        }));
    }
    value.to_str().map(Some).map_err(|_| {
        Status::unauthenticated(if bearer {
            "Auth token invalid"
        } else {
            "Server key invalid"
        })
    })
}
fn status(error: LegacyGatewayError) -> Status {
    let code = match error.code() {
        StableCode::InvalidArgument => tonic::Code::InvalidArgument,
        StableCode::NotFound => tonic::Code::NotFound,
        StableCode::AlreadyExists => tonic::Code::AlreadyExists,
        StableCode::PermissionDenied => tonic::Code::PermissionDenied,
        StableCode::ResourceExhausted => tonic::Code::ResourceExhausted,
        StableCode::FailedPrecondition => tonic::Code::FailedPrecondition,
        StableCode::Aborted => tonic::Code::Aborted,
        StableCode::OutOfRange => tonic::Code::OutOfRange,
        StableCode::Unimplemented => tonic::Code::Unimplemented,
        StableCode::Internal => tonic::Code::Internal,
        StableCode::Unavailable => tonic::Code::Unavailable,
        StableCode::DataLoss => tonic::Code::DataLoss,
        StableCode::Unauthenticated => tonic::Code::Unauthenticated,
    };
    Status::new(code, error.message())
}
fn check_vars(vars: &BTreeMap<String, String>) -> Result<(), Status> {
    if vars.len() > 256
        || vars
            .iter()
            .any(|(k, v)| k.len() > 128 * 1024 || v.len() > 128 * 1024)
    {
        return Err(Status::resource_exhausted(
            "Legacy request resource limit exceeded.",
        ));
    }
    Ok(())
}
fn present_vars(vars: &BTreeMap<String, String>) -> Option<&BTreeMap<String, String>> {
    // A protobuf map with zero entries is nil after Go unmarshal, including
    // refresh: absence retains the refresh token's original variables.
    (!vars.is_empty()).then_some(vars)
}
fn encode_session(session: LegacySession) -> Session {
    let created = session.created;
    let tokens = session.into_tokens();
    Session {
        created,
        token: tokens.access.into_string(),
        refresh_token: tokens.refresh.into_string(),
    }
}

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
impl<R: Repository> LegacyCustomRepository for AccountBridge<'_, R> {
    fn authenticate_legacy_custom(
        &mut self,
        input: LegacyCustomRepositoryInput<'_>,
    ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
        self.0.authenticate_legacy_custom(input)
    }
}

#[cfg(test)]
#[path = "grpc_auth_tests.rs"]
mod tests;
