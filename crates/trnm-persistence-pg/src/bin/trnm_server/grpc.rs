use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::error::ServerError;

pub const HEALTHCHECK_METHOD_PATH: &str = "/nakama.api.Nakama/Healthcheck";
const SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(10);

pub mod generated {
    pub mod google {
        pub mod protobuf {
            tonic::include_proto!("google.protobuf");
        }
    }

    pub mod nakama {
        pub mod api {
            tonic::include_proto!("nakama.api");
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
    worker: JoinHandle<Result<(), ServerError>>,
}

pub fn spawn(
    bind: Option<SocketAddr>,
    draining: Arc<AtomicBool>,
    worker_failed: Arc<AtomicBool>,
) -> Result<Option<GrpcWorker>, ServerError> {
    let Some(bind) = bind else {
        return Ok(None);
    };
    let worker = thread::Builder::new()
        .name("trnm-grpc-healthcheck".to_owned())
        .spawn(move || {
            eprintln!(
                "trnm-server gRPC source candidate listening on {bind} method={HEALTHCHECK_METHOD_PATH}"
            );
            let result = serve(bind, Arc::clone(&draining));
            if result.is_err() {
                worker_failed.store(true, Ordering::Release);
                draining.store(true, Ordering::Release);
            }
            result
        })?;
    Ok(Some(GrpcWorker { worker }))
}

pub fn join(worker: Option<GrpcWorker>) -> Result<(), ServerError> {
    let Some(worker) = worker else {
        return Ok(());
    };
    worker
        .worker
        .join()
        .map_err(|_| ServerError::Configuration("grpc_worker_panicked"))?
}

fn serve(bind: SocketAddr, draining: Arc<AtomicBool>) -> Result<(), ServerError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async move {
        tonic::transport::Server::builder()
            .add_service(NakamaServer::new(HealthcheckService))
            .serve_with_shutdown(bind, async move {
                while !draining.load(Ordering::Acquire) {
                    tokio::time::sleep(SHUTDOWN_POLL_INTERVAL).await;
                }
            })
            .await
            .map_err(|_| ServerError::Configuration("grpc_server_failed"))
    })
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

        let draining = Arc::new(AtomicBool::new(false));
        let worker_failed = Arc::new(AtomicBool::new(false));
        let worker = spawn(
            Some(bind),
            Arc::clone(&draining),
            Arc::clone(&worker_failed),
        )
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
        draining.store(true, Ordering::Release);
        join(Some(worker)).unwrap();
        assert!(!worker_failed.load(Ordering::Acquire));
    }
}
