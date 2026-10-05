//! Transport custody tests, not native account/oracle evidence.
use super::*;
use tonic::codegen::tokio_stream::StreamExt;

#[test]
fn global_connection_saturation_stops_accept_until_owned_socket_drops() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut incoming =
            OwnedIncoming::bind("127.0.0.1:0".parse().unwrap(), SharedDrain::default())
                .await
                .unwrap();
        let address = incoming.listener.local_addr().unwrap();
        let mut clients = Vec::new();
        let mut accepted = Vec::new();
        for _ in 0..MAX_CONNECTIONS {
            clients.push(TcpStream::connect(address).await.unwrap());
            accepted.push(incoming.next().await.unwrap().unwrap());
        }
        clients.push(TcpStream::connect(address).await.unwrap());
        assert!(
            tokio::time::timeout(Duration::from_millis(20), incoming.next())
                .await
                .is_err()
        );
        assert_eq!(
            incoming.registry.0.lock().unwrap().sockets.len(),
            MAX_CONNECTIONS
        );
        drop(accepted.pop());
        accepted.push(
            tokio::time::timeout(Duration::from_secs(1), incoming.next())
                .await
                .unwrap()
                .unwrap()
                .unwrap(),
        );
        assert_eq!(
            incoming.registry.0.lock().unwrap().sockets.len(),
            MAX_CONNECTIONS
        );
        incoming.registry.close_all();
        assert!(incoming.next().await.is_none());
        drop(accepted);
        assert!(incoming.registry.0.lock().unwrap().sockets.is_empty());
        drop(clients);
    });
}

#[test]
fn sealed_registry_cannot_admit_a_socket_after_forced_drain() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let client = StdStream::connect(listener.local_addr().unwrap()).unwrap();
    let (socket, _) = listener.accept().unwrap();
    let registry = SocketRegistry::default();
    let id = registry.register(&socket).unwrap().unwrap();
    registry.close_all();
    assert!(registry.register(&socket).unwrap().is_none());
    let mut byte = [0];
    client
        .set_read_timeout(Some(Duration::from_secs(1)))
        .unwrap();
    let read = std::io::Read::read(&mut &client, &mut byte);
    assert!(matches!(read, Ok(0) | Err(_)));
    registry.remove(id);
}

#[test]
fn stalled_socket_output_is_actually_closed_before_server_abort() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let drain = SharedDrain::default();
        let mut incoming = OwnedIncoming::bind("127.0.0.1:0".parse().unwrap(), drain.clone())
            .await
            .unwrap();
        // Client deliberately never reads. The real owned socket's output must
        // stall once kernel buffers fill; no account implementation is faked.
        let _client = TcpStream::connect(incoming.listener.local_addr().unwrap())
            .await
            .unwrap();
        let mut socket = incoming.next().await.unwrap().unwrap();
        let registry = incoming.registry();
        let closed = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let observed_closed = Arc::clone(&closed);
        let writer = tokio::spawn(async move {
            let bytes = [0u8; 64 * 1024];
            loop {
                let result =
                    std::future::poll_fn(|cx| Pin::new(&mut socket).poll_write(cx, &bytes)).await;
                if result.is_err() || matches!(result, Ok(0)) {
                    break;
                }
                tokio::task::yield_now().await;
            }
            observed_closed.store(true, std::sync::atomic::Ordering::Release);
        });
        let server = tokio::spawn(std::future::pending::<Result<(), tonic::transport::Error>>());
        drain.begin();
        let began = std::time::Instant::now();
        supervise(server, registry.clone(), drain).await.unwrap();
        assert!(began.elapsed() < GRACEFUL_DRAIN + Duration::from_secs(2));
        tokio::time::timeout(Duration::from_secs(1), writer)
            .await
            .unwrap()
            .unwrap();
        assert!(closed.load(std::sync::atomic::Ordering::Acquire));
        assert!(registry.0.lock().unwrap().sealed);
        assert!(registry.0.lock().unwrap().sockets.is_empty());
    });
}

#[test]
fn real_tonic_http2_zero_response_window_cannot_hold_process_drain() {
    use super::super::generated::nakama::api::nakama_server::NakamaServer;
    use super::super::HealthcheckService;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    fn frame(kind: u8, flags: u8, stream: u32, payload: &[u8]) -> Vec<u8> {
        assert!(payload.len() <= 16 * 1024);
        let length = payload.len() as u32;
        let mut bytes = vec![
            (length >> 16) as u8,
            (length >> 8) as u8,
            length as u8,
            kind,
            flags,
        ];
        bytes.extend_from_slice(&stream.to_be_bytes());
        bytes.extend_from_slice(payload);
        bytes
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let drain = SharedDrain::default();
        let incoming = OwnedIncoming::bind("127.0.0.1:0".parse().unwrap(), drain.clone())
            .await
            .unwrap();
        let address = incoming.listener.local_addr().unwrap();
        let registry = incoming.registry();
        let shutdown = drain.clone();
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .add_service(NakamaServer::new(HealthcheckService))
                .serve_with_incoming_shutdown(incoming, async move {
                    while !shutdown.is_draining() {
                        tokio::time::sleep(Duration::from_millis(1)).await;
                    }
                })
                .await
        });
        let mut client = TcpStream::connect(address).await.unwrap();
        client
            .write_all(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
            .await
            .unwrap();
        // SETTINGS_INITIAL_WINDOW_SIZE = 0. Request DATA remains legal because
        // this setting controls the peer's RESPONSE stream window, not ours.
        client
            .write_all(&frame(4, 0, 0, &[0, 4, 0, 0, 0, 0]))
            .await
            .unwrap();
        // Static HPACK indices: POST=3, http=6, :path name=4, :authority=1,
        // content-type name=31. No Huffman/dynamic-table implementation needed.
        let path = b"/nakama.api.Nakama/Healthcheck";
        let mut headers = vec![0x83, 0x86, 0x04, path.len() as u8];
        headers.extend_from_slice(path);
        headers.extend_from_slice(
            b"\x01\x09localhost\x0f\x10\x10application/grpc\x00\x02te\x08trailers",
        );
        client.write_all(&frame(1, 4, 1, &headers)).await.unwrap();
        client
            .write_all(&frame(0, 1, 1, &[0, 0, 0, 0, 0]))
            .await
            .unwrap();
        tokio::time::timeout(Duration::from_secs(2), async {
            for _ in 0..8 {
                let mut header = [0u8; 9];
                client.read_exact(&mut header).await.unwrap();
                let length = (usize::from(header[0]) << 16)
                    | (usize::from(header[1]) << 8)
                    | usize::from(header[2]);
                assert!(length <= 16 * 1024);
                let mut payload = vec![0; length];
                client.read_exact(&mut payload).await.unwrap();
                if header[3] == 4 && header[4] & 1 == 0 {
                    client.write_all(&frame(4, 1, 0, &[])).await.unwrap();
                }
                assert_ne!(
                    header[3], 7,
                    "server rejected the finite HTTP/2 test request"
                );
                if header[3] == 1 && header[8] == 1 {
                    return;
                }
            }
            panic!("actual Healthcheck response headers were not received");
        })
        .await
        .unwrap();
        // Real handler has returned headers, but cannot flush its five-byte
        // gRPC Empty message. Graceful completion must be forcibly bounded.
        drain.begin();
        let began = std::time::Instant::now();
        supervise(server, registry.clone(), drain).await.unwrap();
        assert!(began.elapsed() >= GRACEFUL_DRAIN);
        assert!(began.elapsed() < GRACEFUL_DRAIN + Duration::from_secs(2));
        assert!(registry.0.lock().unwrap().sealed);
        let mut tail = Vec::new();
        tokio::time::timeout(
            Duration::from_secs(1),
            client.take(64 * 1024).read_to_end(&mut tail),
        )
        .await
        .unwrap()
        .unwrap();
    });
}

#[test]
fn expiry_closes_io_but_retains_capacity_until_owned_socket_cleanup() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut incoming =
            OwnedIncoming::bind("127.0.0.1:0".parse().unwrap(), SharedDrain::default())
                .await
                .unwrap();
        let _client = TcpStream::connect(incoming.listener.local_addr().unwrap())
            .await
            .unwrap();
        let owned = incoming.next().await.unwrap().unwrap();
        let registry = incoming.registry();
        assert_eq!(
            registry.close_expired(Instant::now() + MAX_CONNECTION_LIFETIME),
            1
        );
        assert_eq!(registry.0.lock().unwrap().sockets.len(), 1);
        assert!(!registry.0.lock().unwrap().sealed);
        // Repeated maintenance cannot pretend a still-owned connection ended.
        assert_eq!(
            registry.close_expired(Instant::now() + MAX_CONNECTION_LIFETIME),
            0
        );
        assert_eq!(registry.0.lock().unwrap().sockets.len(), 1);
        drop(owned);
        assert!(registry.0.lock().unwrap().sockets.is_empty());
    });
}

#[test]
fn silent_tonic_saturation_expires_and_readmits_real_client_without_process_drain() {
    use super::super::generated::google::protobuf::Empty;
    use super::super::generated::nakama::api::{
        nakama_client::NakamaClient, nakama_server::NakamaServer,
    };
    use super::super::HealthcheckService;

    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let drain = SharedDrain::default();
        let mut incoming = OwnedIncoming::bind("127.0.0.1:0".parse().unwrap(), drain.clone())
            .await
            .unwrap();
        // Same deadline-enforcement implementation, shorter local test interval.
        // Production always constructs the registry with its fixed 30s lifetime.
        incoming.registry = SocketRegistry::with_lifetime(Duration::from_secs(2));
        let address = incoming.listener.local_addr().unwrap();
        let registry = incoming.registry();
        let shutdown = drain.clone();
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .add_service(NakamaServer::new(HealthcheckService))
                .serve_with_incoming_shutdown(incoming, async move {
                    while !shutdown.is_draining() {
                        tokio::time::sleep(Duration::from_millis(1)).await;
                    }
                })
                .await
        });
        let supervisor = tokio::spawn(supervise(server, registry.clone(), drain.clone()));
        let mut silent_clients = Vec::new();
        for _ in 0..MAX_CONNECTIONS {
            silent_clients.push(TcpStream::connect(address).await.unwrap());
        }
        tokio::time::timeout(Duration::from_secs(1), async {
            while registry.0.lock().unwrap().sockets.len() != MAX_CONNECTIONS {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .unwrap();
        assert!(!drain.is_draining());
        // The next connection can enter TCP backlog, but cannot be accepted
        // until an expired connection's actual OwnedSocket destructor runs.
        let mut client = tokio::time::timeout(
            Duration::from_secs(5),
            NakamaClient::connect(format!("http://{address}")),
        )
        .await
        .unwrap()
        .unwrap();
        tokio::time::timeout(
            Duration::from_secs(3),
            client.healthcheck(tonic::Request::new(Empty {})),
        )
        .await
        .unwrap()
        .unwrap();
        tokio::time::timeout(Duration::from_secs(3), async {
            while registry
                .0
                .lock()
                .unwrap()
                .sockets
                .keys()
                .any(|id| *id <= MAX_CONNECTIONS as u64)
            {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .unwrap();
        assert!(
            !drain.is_draining(),
            "lifetime expiry must not shut down the process"
        );
        assert!(!registry.0.lock().unwrap().sealed);
        assert!(!supervisor.is_finished());
        drop(client);
        drop(silent_clients);
        drain.begin();
        tokio::time::timeout(Duration::from_secs(3), supervisor)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
    });
}

#[test]
fn absolute_lifetime_also_closes_healthy_clients_without_claiming_idle_parity() {
    use super::super::generated::google::protobuf::Empty;
    use super::super::generated::nakama::api::{
        nakama_client::NakamaClient, nakama_server::NakamaServer,
    };
    use super::super::HealthcheckService;

    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let drain = SharedDrain::default();
        let incoming = OwnedIncoming::bind("127.0.0.1:0".parse().unwrap(), drain.clone())
            .await
            .unwrap();
        let address = incoming.listener.local_addr().unwrap();
        let registry = incoming.registry();
        let shutdown = drain.clone();
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .timeout(Duration::from_secs(5))
                .add_service(NakamaServer::new(HealthcheckService))
                .serve_with_incoming_shutdown(incoming, async move {
                    while !shutdown.is_draining() {
                        tokio::time::sleep(Duration::from_millis(1)).await;
                    }
                })
                .await
        });
        let supervisor = tokio::spawn(supervise(server, registry.clone(), drain.clone()));
        let mut client = NakamaClient::connect(format!("http://{address}"))
            .await
            .unwrap();
        client.healthcheck(Empty {}).await.unwrap();
        let accepted = registry.0.lock().unwrap().sockets[&1].accepted_at;
        let mut successes = 1;
        tokio::time::timeout(MAX_CONNECTION_LIFETIME + Duration::from_secs(4), async {
            loop {
                tokio::time::sleep(Duration::from_millis(250)).await;
                let response =
                    tokio::time::timeout(Duration::from_secs(2), client.healthcheck(Empty {}))
                        .await
                        .unwrap();
                if response.is_ok() {
                    successes += 1;
                } else {
                    assert!(accepted.elapsed() >= MAX_CONNECTION_LIFETIME);
                }
                let state = registry.0.lock().unwrap();
                if state
                    .sockets
                    .get(&1)
                    .is_none_or(|socket| socket.expiry_shutdown_sent)
                {
                    break;
                }
            }
        })
        .await
        .unwrap();
        assert!(accepted.elapsed() >= MAX_CONNECTION_LIFETIME);
        assert!(
            successes >= 10,
            "the connection must have carried healthy requests"
        );
        assert!(!drain.is_draining());
        assert!(!registry.0.lock().unwrap().sealed);
        drop(client);
        drain.begin();
        tokio::time::timeout(Duration::from_secs(3), supervisor)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        assert!(registry.0.lock().unwrap().sockets.is_empty());
    });
}
