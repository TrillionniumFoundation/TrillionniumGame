use std::net::SocketAddr;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::app::{Repository, SharedDrain};
use super::auth_runtime::AuthAuthorityRuntime;

#[path = "grpc_auth.rs"]
mod auth;
#[path = "grpc_transport.rs"]
mod transport;
use super::error::ServerError;

pub const HEALTHCHECK_METHOD_PATH: &str = "/nakama.api.Nakama/Healthcheck";
const SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(10);

pub mod generated {
    pub mod google {
        pub mod protobuf {
            include!(concat!(
                env!("OUT_DIR"),
                "/canonical-grpc/google.protobuf.rs"
            ));
        }
    }

    pub mod nakama {
        pub mod api {
            include!(concat!(env!("OUT_DIR"), "/canonical-grpc/nakama.api.rs"));
        }
    }
}

use generated::google::protobuf::Empty;
use generated::nakama::api::nakama_server::{Nakama, NakamaServer};

#[derive(Clone, Copy, Debug, Default)]
pub struct HealthcheckService;

#[tonic::async_trait]
impl Nakama for HealthcheckService {
    async fn healthcheck(
        &self,
        _request: tonic::Request<Empty>,
    ) -> Result<tonic::Response<Empty>, tonic::Status> {
        Ok(tonic::Response::new(Empty {}))
    }
}

#[derive(Debug)]
pub struct GrpcWorker {
    worker: Option<JoinHandle<Result<(), ServerError>>>,
    draining: SharedDrain,
    worker_failed: Arc<AtomicBool>,
}

#[cfg(test)]
pub fn spawn(
    bind: Option<SocketAddr>,
    draining: SharedDrain,
    worker_failed: Arc<AtomicBool>,
) -> Result<Option<GrpcWorker>, ServerError> {
    spawn_service(
        bind,
        draining,
        worker_failed,
        HealthcheckService,
        AuthAuthorityRuntime::Disabled,
    )
}

pub(super) fn spawn_authenticated<R: Repository + Clone + Send + 'static>(
    bind: Option<SocketAddr>,
    draining: SharedDrain,
    worker_failed: Arc<AtomicBool>,
    repository: &R,
    authority: &AuthAuthorityRuntime,
) -> Result<Option<GrpcWorker>, ServerError> {
    if bind.is_none() {
        return Ok(None);
    }
    let (service, authority) = match catch_unwind(AssertUnwindSafe(|| {
        let service = auth::NativeGrpcService::new(
            repository.clone(),
            authority.clone(),
            draining.clone(),
            Arc::clone(&worker_failed),
        );
        (service, authority.clone())
    })) {
        Ok(prepared) => prepared,
        Err(_) => {
            signal_failure(&draining, worker_failed.as_ref());
            return Err(ServerError::Configuration("grpc_worker_panicked"));
        }
    };
    spawn_service(bind, draining, worker_failed, service, authority)
}

fn spawn_service<S: Nakama>(
    bind: Option<SocketAddr>,
    draining: SharedDrain,
    worker_failed: Arc<AtomicBool>,
    service: S,
    authority: AuthAuthorityRuntime,
) -> Result<Option<GrpcWorker>, ServerError> {
    let Some(bind) = bind else {
        return Ok(None);
    };
    let worker_draining = draining.clone();
    let worker_failed_for_thread = Arc::clone(&worker_failed);
    let worker = thread::Builder::new()
        .name("trnm-grpc".to_owned())
        .spawn(move || {
            eprintln!(
                "trnm-server gRPC source candidate listening on {bind} method={HEALTHCHECK_METHOD_PATH}"
            );
            run_guarded(
                &worker_draining,
                worker_failed_for_thread.as_ref(),
                || serve(bind, worker_draining.clone(), service, authority),
            )
        })?;
    Ok(Some(GrpcWorker {
        worker: Some(worker),
        draining,
        worker_failed,
    }))
}

pub fn join(worker: Option<GrpcWorker>) -> Result<(), ServerError> {
    let Some(mut worker) = worker else {
        return Ok(());
    };
    let Some(handle) = worker.worker.take() else {
        return Ok(());
    };
    match handle.join() {
        Ok(result) => result,
        Err(_) => {
            signal_failure(&worker.draining, worker.worker_failed.as_ref());
            Err(ServerError::Configuration("grpc_worker_panicked"))
        }
    }
}

impl Drop for GrpcWorker {
    fn drop(&mut self) {
        if let Some(handle) = self.worker.take() {
            self.draining.begin();
            let _ = handle.join();
        }
    }
}

fn run_guarded(
    draining: &SharedDrain,
    worker_failed: &AtomicBool,
    operation: impl FnOnce() -> Result<(), ServerError>,
) -> Result<(), ServerError> {
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(())) => Ok(()),
        Ok(Err(error)) => {
            signal_failure(draining, worker_failed);
            Err(error)
        }
        Err(_) => {
            signal_failure(draining, worker_failed);
            Err(ServerError::Configuration("grpc_worker_panicked"))
        }
    }
}

fn signal_failure(draining: &SharedDrain, worker_failed: &AtomicBool) {
    worker_failed.store(true, Ordering::Release);
    draining.begin();
}

fn serve<S: Nakama>(
    bind: SocketAddr,
    draining: SharedDrain,
    service: S,
    authority: AuthAuthorityRuntime,
) -> Result<(), ServerError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .max_blocking_threads(auth::MAX_AUTH_JOBS)
        .enable_all()
        .build()?;
    let result = runtime.block_on(async move {
        let incoming = transport::OwnedIncoming::bind(bind, draining.clone()).await?;
        let registry = incoming.registry();
        let shutdown = draining.clone();
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .concurrency_limit_per_connection(32)
                .max_concurrent_streams(32)
                .http2_max_header_list_size(64 * 1024)
                .timeout(Duration::from_secs(5))
                .add_service(auth::AuthBoundary::new(
                    NakamaServer::new(service)
                        .max_decoding_message_size(auth::MAX_REQUEST_BYTES)
                        .max_encoding_message_size(auth::MAX_RESPONSE_BYTES),
                    authority,
                ))
                .serve_with_incoming_shutdown(incoming, async move {
                    while !shutdown.is_draining() {
                        tokio::time::sleep(SHUTDOWN_POLL_INTERVAL).await;
                    }
                })
                .await
        });
        transport::supervise(server, registry, draining).await
    });
    // Tokio runtime drop cancels owned async tasks, then waits for blocking
    // operations. Never use shutdown_timeout/background, which can detach them.
    drop(runtime);
    result
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use generated::nakama::api::nakama_client::NakamaClient;

    use super::*;

    #[test]
    fn official_healthcheck_method_path_is_exact() {
        assert_eq!(HEALTHCHECK_METHOD_PATH, "/nakama.api.Nakama/Healthcheck");
    }

    #[test]
    fn generated_service_returns_an_empty_response() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let response = runtime
            .block_on(HealthcheckService.healthcheck(tonic::Request::new(Empty {})))
            .unwrap();
        assert_eq!(response.into_inner(), Empty {});
    }

    #[test]
    fn generated_client_reaches_the_http2_healthcheck_path() {
        let reservation = TcpListener::bind("127.0.0.1:0").unwrap();
        let bind = reservation.local_addr().unwrap();
        drop(reservation);

        let draining = SharedDrain::default();
        let worker_failed = Arc::new(AtomicBool::new(false));
        let worker = spawn(Some(bind), draining.clone(), Arc::clone(&worker_failed))
            .unwrap()
            .unwrap();

        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let endpoint = format!("http://{bind}");
            let mut client = None;
            for _ in 0..100 {
                match NakamaClient::connect(endpoint.clone()).await {
                    Ok(value) => {
                        client = Some(value);
                        break;
                    }
                    Err(_) => tokio::time::sleep(Duration::from_millis(10)).await,
                }
            }
            let mut client = client.expect("generated gRPC server did not become reachable");
            let response = client
                .healthcheck(tonic::Request::new(Empty {}))
                .await
                .unwrap();
            assert_eq!(response.into_inner(), Empty {});
        });

        // The tonic client connection is driven by tasks owned by this runtime.
        // Drop it before requesting graceful server shutdown; otherwise the test
        // waits for the server to close a connection whose driver is still alive.
        drop(runtime);
        draining.begin();
        join(Some(worker)).unwrap();
        assert!(!worker_failed.load(Ordering::Acquire));
    }

    #[test]
    fn grpc_worker_returned_error_signals_shared_failure_fence() {
        let draining = SharedDrain::default();
        let worker_failed = AtomicBool::new(false);
        let result = run_guarded(&draining, &worker_failed, || {
            Err(ServerError::Configuration("injected_grpc_error"))
        });
        assert!(matches!(
            result,
            Err(ServerError::Configuration("injected_grpc_error"))
        ));
        assert!(worker_failed.load(Ordering::Acquire));
        assert!(draining.is_draining());
    }

    #[test]
    fn grpc_worker_panic_signals_shared_failure_fence() {
        let draining = SharedDrain::default();
        let worker_failed = AtomicBool::new(false);
        let result = run_guarded(&draining, &worker_failed, || {
            panic!("deterministic injected gRPC worker panic")
        });
        assert!(matches!(
            result,
            Err(ServerError::Configuration("grpc_worker_panicked"))
        ));
        assert!(worker_failed.load(Ordering::Acquire));
        assert!(draining.is_draining());
    }
}
