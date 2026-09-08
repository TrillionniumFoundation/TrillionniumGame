#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTTP = ROOT / "crates/trnm-server/src/runtime/http.rs"
WS = ROOT / "crates/trnm-server/src/runtime/websocket.rs"
SERVER = ROOT / "crates/trnm-server/src/runtime/server.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


http = HTTP.read_text(encoding="utf-8")
http = replace_once(
    http,
    "use std::str;\n",
    "use std::str;\nuse std::time::{Duration, Instant};\n",
    "http time imports",
)
http = replace_once(
    http,
    "pub fn read_request(stream: &mut TcpStream, maximum: usize) -> Result<Request, ServerError> {\n"
    "    let mut input = Vec::with_capacity(4096);\n"
    "    let mut buffer = [0_u8; 4096];\n"
    "    loop {\n"
    "        let read = stream.read(&mut buffer)?;\n",
    "pub fn read_request(stream: &mut TcpStream, maximum: usize) -> Result<Request, ServerError> {\n"
    "    let timeout = stream\n"
    "        .read_timeout()?\n"
    "        .ok_or(ServerError::Configuration(\"connection_read_timeout_required\"))?;\n"
    "    let deadline = Instant::now()\n"
    "        .checked_add(timeout)\n"
    "        .ok_or(ServerError::Configuration(\"http_request_deadline_overflow\"))?;\n"
    "    let mut input = Vec::with_capacity(4096);\n"
    "    let mut buffer = [0_u8; 4096];\n"
    "    loop {\n"
    "        set_remaining_read_timeout(stream, deadline)?;\n"
    "        let read = stream.read(&mut buffer)?;\n",
    "http absolute request deadline",
)
http = replace_once(
    http,
    "pub fn parse_request_bytes(input: &[u8], maximum: usize) -> Result<Request, InputError> {\n",
    "fn set_remaining_read_timeout(\n"
    "    stream: &TcpStream,\n"
    "    deadline: Instant,\n"
    ") -> Result<(), ServerError> {\n"
    "    let remaining = deadline\n"
    "        .checked_duration_since(Instant::now())\n"
    "        .filter(|value| !value.is_zero())\n"
    "        .ok_or_else(|| InputError::new(\"http_request_deadline_exceeded\"))?;\n"
    "    stream.set_read_timeout(Some(remaining))?;\n"
    "    Ok(())\n"
    "}\n\n"
    "pub fn parse_request_bytes(input: &[u8], maximum: usize) -> Result<Request, InputError> {\n",
    "http deadline helper",
)
http = replace_once(
    http,
    "mod tests {\n    use super::*;\n",
    "mod tests {\n"
    "    use std::net::{TcpListener, TcpStream as TestTcpStream};\n"
    "    use std::thread;\n\n"
    "    use super::*;\n",
    "http test imports",
)
http = replace_once(
    http,
    "    #[test]\n"
    "    fn response_framing_is_close_delimited_and_length_exact() {\n",
    "    #[test]\n"
    "    fn slow_drip_cannot_extend_total_request_deadline() {\n"
    "        let listener = TcpListener::bind(\"127.0.0.1:0\").unwrap();\n"
    "        let mut client = TestTcpStream::connect(listener.local_addr().unwrap()).unwrap();\n"
    "        let (mut server, _) = listener.accept().unwrap();\n"
    "        server\n"
    "            .set_read_timeout(Some(Duration::from_millis(120)))\n"
    "            .unwrap();\n"
    "        let writer = thread::spawn(move || {\n"
    "            for byte in b\"GET / HTTP/1.1\\r\\n\\r\\n\" {\n"
    "                if client.write_all(&[*byte]).is_err() {\n"
    "                    break;\n"
    "                }\n"
    "                thread::sleep(Duration::from_millis(40));\n"
    "            }\n"
    "        });\n"
    "        let started = Instant::now();\n"
    "        let result = read_request(&mut server, 4096);\n"
    "        assert!(matches!(\n"
    "            result,\n"
    "            Err(ServerError::Input(_))\n"
    "                | Err(ServerError::Io(ref error))\n"
    "                    if matches!(error.kind(), std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock)\n"
    "        ));\n"
    "        assert!(started.elapsed() < Duration::from_millis(500));\n"
    "        drop(server);\n"
    "        writer.join().unwrap();\n"
    "    }\n\n"
    "    #[test]\n"
    "    fn response_framing_is_close_delimited_and_length_exact() {\n",
    "http slow drip test",
)
HTTP.write_text(http, encoding="utf-8")

ws = WS.read_text(encoding="utf-8")
ws = replace_once(
    ws,
    "use std::str;\n",
    "use std::str;\nuse std::time::{Duration, Instant};\n",
    "websocket time imports",
)
ws = replace_once(
    ws,
    "        let frame = match read_client_frame_exact(stream, maximum_payload) {\n",
    "        let frame = match read_client_frame_from_stream(stream, maximum_payload) {\n",
    "websocket total frame deadline call",
)
ws = replace_once(
    ws,
    "fn read_client_frame_exact(\n"
    "    input: &mut impl Read,\n"
    "    maximum_payload: usize,\n"
    ") -> Result<ClientFrame, FrameReadError> {\n",
    "struct DeadlineReader<'a> {\n"
    "    stream: &'a mut TcpStream,\n"
    "    deadline: Instant,\n"
    "}\n\n"
    "impl Read for DeadlineReader<'_> {\n"
    "    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {\n"
    "        let remaining = self\n"
    "            .deadline\n"
    "            .checked_duration_since(Instant::now())\n"
    "            .filter(|value| !value.is_zero())\n"
    "            .ok_or_else(|| io::Error::new(ErrorKind::TimedOut, \"websocket frame deadline exceeded\"))?;\n"
    "        self.stream.set_read_timeout(Some(remaining))?;\n"
    "        self.stream.read(buffer)\n"
    "    }\n"
    "}\n\n"
    "fn read_client_frame_from_stream(\n"
    "    stream: &mut TcpStream,\n"
    "    maximum_payload: usize,\n"
    ") -> Result<ClientFrame, FrameReadError> {\n"
    "    let timeout = stream\n"
    "        .read_timeout()\n"
    "        .map_err(FrameReadError::Io)?\n"
    "        .ok_or_else(|| {\n"
    "            FrameReadError::Io(io::Error::new(\n"
    "                ErrorKind::InvalidInput,\n"
    "                \"websocket read timeout is required\",\n"
    "            ))\n"
    "        })?;\n"
    "    let deadline = Instant::now().checked_add(timeout).ok_or_else(|| {\n"
    "        FrameReadError::Io(io::Error::new(\n"
    "            ErrorKind::InvalidInput,\n"
    "            \"websocket frame deadline overflow\",\n"
    "        ))\n"
    "    })?;\n"
    "    let mut reader = DeadlineReader { stream, deadline };\n"
    "    read_client_frame_exact(&mut reader, maximum_payload)\n"
    "}\n\n"
    "fn read_client_frame_exact(\n"
    "    input: &mut impl Read,\n"
    "    maximum_payload: usize,\n"
    ") -> Result<ClientFrame, FrameReadError> {\n",
    "websocket deadline reader",
)
ws = replace_once(
    ws,
    "    #[test]\n"
    "    fn server_text_and_close_frames_are_unmasked_and_canonical() {\n",
    "    #[test]\n"
    "    fn slow_drip_cannot_extend_total_frame_deadline() {\n"
    "        let (mut client, mut server) = socket_pair(Duration::from_millis(120));\n"
    "        let frame = masked_text(b\"slow-frame\");\n"
    "        let writer = thread::spawn(move || {\n"
    "            for byte in frame {\n"
    "                if std::io::Write::write_all(&mut client, &[byte]).is_err() {\n"
    "                    break;\n"
    "                }\n"
    "                thread::sleep(Duration::from_millis(40));\n"
    "            }\n"
    "        });\n"
    "        let started = Instant::now();\n"
    "        assert!(matches!(\n"
    "            read_client_frame_from_stream(&mut server, 4096),\n"
    "            Err(FrameReadError::Io(ref error))\n"
    "                if matches!(error.kind(), ErrorKind::TimedOut | ErrorKind::WouldBlock)\n"
    "        ));\n"
    "        assert!(started.elapsed() < Duration::from_millis(500));\n"
    "        drop(server);\n"
    "        writer.join().unwrap();\n"
    "    }\n\n"
    "    #[test]\n"
    "    fn server_text_and_close_frames_are_unmasked_and_canonical() {\n",
    "websocket slow drip test",
)
WS.write_text(ws, encoding="utf-8")

server = SERVER.read_text(encoding="utf-8")
server = replace_once(
    server,
    "use std::io::ErrorKind;\n",
    "use std::collections::BTreeMap;\nuse std::io::ErrorKind;\n",
    "server collection import",
)
server = replace_once(
    server,
    "use std::sync::atomic::{AtomicBool, Ordering};\n",
    "use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};\n",
    "server atomic import",
)
server = replace_once(
    server,
    "const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);\n\n"
    "pub fn serve<R>(config: &ServerConfig, repository: R) -> Result<(), ServerError>\n",
    "const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);\n\n"
    "#[derive(Debug)]\n"
    "struct QueuedConnection {\n"
    "    id: u64,\n"
    "    stream: TcpStream,\n"
    "}\n\n"
    "#[derive(Clone, Debug, Default)]\n"
    "struct ConnectionRegistry {\n"
    "    next_id: Arc<AtomicU64>,\n"
    "    streams: Arc<Mutex<BTreeMap<u64, TcpStream>>>,\n"
    "}\n\n"
    "impl ConnectionRegistry {\n"
    "    fn register(&self, stream: &TcpStream) -> Result<u64, ServerError> {\n"
    "        let previous = self\n"
    "            .next_id\n"
    "            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| value.checked_add(1))\n"
    "            .map_err(|_| ServerError::Configuration(\"connection_id_exhausted\"))?;\n"
    "        let id = previous + 1;\n"
    "        let retained = stream.try_clone()?;\n"
    "        self.streams\n"
    "            .lock()\n"
    "            .unwrap_or_else(std::sync::PoisonError::into_inner)\n"
    "            .insert(id, retained);\n"
    "        Ok(id)\n"
    "    }\n\n"
    "    fn remove(&self, id: u64) {\n"
    "        self.streams\n"
    "            .lock()\n"
    "            .unwrap_or_else(std::sync::PoisonError::into_inner)\n"
    "            .remove(&id);\n"
    "    }\n\n"
    "    fn shutdown_all(&self) -> usize {\n"
    "        let streams = self\n"
    "            .streams\n"
    "            .lock()\n"
    "            .unwrap_or_else(std::sync::PoisonError::into_inner);\n"
    "        for stream in streams.values() {\n"
    "            let _ = stream.shutdown(Shutdown::Both);\n"
    "        }\n"
    "        streams.len()\n"
    "    }\n"
    "}\n\n"
    "pub fn serve<R>(config: &ServerConfig, repository: R) -> Result<(), ServerError>\n",
    "connection registry types",
)
server = replace_once(
    server,
    "    let (sender, receiver) = sync_channel(queue_capacity);\n"
    "    let receiver = Arc::new(Mutex::new(receiver));\n"
    "    let draining = SharedDrain::default();\n",
    "    let (sender, receiver) = sync_channel::<QueuedConnection>(queue_capacity);\n"
    "    let receiver = Arc::new(Mutex::new(receiver));\n"
    "    let connections = ConnectionRegistry::default();\n"
    "    let draining = SharedDrain::default();\n",
    "connection registry creation",
)
server = replace_once(
    server,
    "        let worker_metrics = metrics.clone();\n"
    "        workers.push(\n",
    "        let worker_metrics = metrics.clone();\n"
    "        let worker_connections = connections.clone();\n"
    "        workers.push(\n",
    "worker registry clone",
)
server = replace_once(
    server,
    "                            worker_metrics,\n"
    "                        );\n",
    "                            worker_metrics,\n"
    "                            worker_connections,\n"
    "                        );\n",
    "worker registry argument",
)
server = replace_once(
    server,
    "    let accept_result = accept_loop(&listener, &sender, config, &draining, &worker_failed);\n"
    "    draining.begin();\n"
    "    let cancelled_operations = repository.cancel_inflight();\n",
    "    let accept_result = accept_loop(\n"
    "        &listener,\n"
    "        &sender,\n"
    "        config,\n"
    "        &draining,\n"
    "        &worker_failed,\n"
    "        &connections,\n"
    "    );\n"
    "    draining.begin();\n"
    "    let closed_connections = connections.shutdown_all();\n"
    "    if closed_connections > 0 {\n"
    "        eprintln!(\"trnm-server closed {closed_connections} queued or active connections during drain\");\n"
    "    }\n"
    "    let cancelled_operations = repository.cancel_inflight();\n",
    "serve drain closes connections",
)
server = replace_once(
    server,
    "    sender: &SyncSender<TcpStream>,\n"
    "    config: &ServerConfig,\n"
    "    draining: &SharedDrain,\n"
    "    worker_failed: &AtomicBool,\n"
    ") -> Result<(), ServerError> {\n",
    "    sender: &SyncSender<QueuedConnection>,\n"
    "    config: &ServerConfig,\n"
    "    draining: &SharedDrain,\n"
    "    worker_failed: &AtomicBool,\n"
    "    connections: &ConnectionRegistry,\n"
    ") -> Result<(), ServerError> {\n",
    "accept loop signature",
)
server = replace_once(
    server,
    "            Ok((stream, _peer)) => match sender.try_send(stream) {\n"
    "                Ok(()) => {}\n"
    "                Err(TrySendError::Full(mut stream)) => {\n"
    "                    configure_connection(&stream, config)?;\n"
    "                    write_response(&mut stream, &overloaded());\n"
    "                    let _ = stream.shutdown(Shutdown::Both);\n"
    "                }\n"
    "                Err(TrySendError::Disconnected(mut stream)) => {\n"
    "                    write_response(&mut stream, &unavailable());\n"
    "                    let _ = stream.shutdown(Shutdown::Both);\n"
    "                    draining.begin();\n"
    "                    return Err(ServerError::Configuration(\n"
    "                        \"connection_worker_queue_disconnected\",\n"
    "                    ));\n"
    "                }\n"
    "            },\n",
    "            Ok((stream, _peer)) => {\n"
    "                let id = connections.register(&stream)?;\n"
    "                match sender.try_send(QueuedConnection { id, stream }) {\n"
    "                    Ok(()) => {}\n"
    "                    Err(TrySendError::Full(mut connection)) => {\n"
    "                        connections.remove(connection.id);\n"
    "                        configure_connection(&connection.stream, config)?;\n"
    "                        write_response(&mut connection.stream, &overloaded());\n"
    "                        let _ = connection.stream.shutdown(Shutdown::Both);\n"
    "                    }\n"
    "                    Err(TrySendError::Disconnected(mut connection)) => {\n"
    "                        connections.remove(connection.id);\n"
    "                        write_response(&mut connection.stream, &unavailable());\n"
    "                        let _ = connection.stream.shutdown(Shutdown::Both);\n"
    "                        draining.begin();\n"
    "                        return Err(ServerError::Configuration(\n"
    "                            \"connection_worker_queue_disconnected\",\n"
    "                        ));\n"
    "                    }\n"
    "                }\n"
    "            }\n",
    "accept loop registered connection",
)
server = replace_once(
    server,
    "    receiver: Arc<Mutex<Receiver<TcpStream>>>,\n"
    "    draining: SharedDrain,\n"
    "    metrics: SharedAppMetrics,\n"
    ") where\n",
    "    receiver: Arc<Mutex<Receiver<QueuedConnection>>>,\n"
    "    draining: SharedDrain,\n"
    "    metrics: SharedAppMetrics,\n"
    "    connections: ConnectionRegistry,\n"
    ") where\n",
    "worker loop signature",
)
server = replace_once(
    server,
    "    while let Some(mut stream) = receive_connection(&receiver) {\n"
    "        if let Err(error) = handle_connection(&mut stream, &mut app, &config, &draining) {\n"
    "            eprintln!(\"trnm-server connection failed: {error}\");\n"
    "        }\n"
    "        let _ = stream.shutdown(Shutdown::Both);\n"
    "    }\n"
    "}\n\n"
    "fn receive_connection(receiver: &Mutex<Receiver<TcpStream>>) -> Option<TcpStream> {\n",
    "    while let Some(mut connection) = receive_connection(&receiver) {\n"
    "        if let Err(error) = handle_connection(\n"
    "            &mut connection.stream,\n"
    "            &mut app,\n"
    "            &config,\n"
    "            &draining,\n"
    "        ) {\n"
    "            eprintln!(\"trnm-server connection failed: {error}\");\n"
    "        }\n"
    "        let _ = connection.stream.shutdown(Shutdown::Both);\n"
    "        connections.remove(connection.id);\n"
    "    }\n"
    "}\n\n"
    "fn receive_connection(\n"
    "    receiver: &Mutex<Receiver<QueuedConnection>>,\n"
    ") -> Option<QueuedConnection> {\n",
    "worker removes registered connection",
)
server = replace_once(
    server,
    "    use std::collections::BTreeMap;\n\n    use super::*;\n",
    "    use std::collections::BTreeMap;\n"
    "    use std::io::Read;\n"
    "    use std::net::{TcpListener as TestTcpListener, TcpStream as TestTcpStream};\n\n"
    "    use super::*;\n",
    "server test imports",
)
server = replace_once(
    server,
    "    #[test]\n"
    "    fn overload_and_drain_responses_are_stable_and_retryable() {\n",
    "    #[test]\n"
    "    fn drain_registry_actively_closes_registered_idle_connection() {\n"
    "        let listener = TestTcpListener::bind(\"127.0.0.1:0\").unwrap();\n"
    "        let mut client = TestTcpStream::connect(listener.local_addr().unwrap()).unwrap();\n"
    "        let (server, _) = listener.accept().unwrap();\n"
    "        client\n"
    "            .set_read_timeout(Some(Duration::from_secs(1)))\n"
    "            .unwrap();\n"
    "        let registry = ConnectionRegistry::default();\n"
    "        let id = registry.register(&server).unwrap();\n"
    "        assert_eq!(registry.shutdown_all(), 1);\n"
    "        let mut byte = [0_u8; 1];\n"
    "        assert!(matches!(\n"
    "            client.read(&mut byte),\n"
    "            Ok(0) | Err(_)\n"
    "        ));\n"
    "        registry.remove(id);\n"
    "        assert_eq!(registry.shutdown_all(), 0);\n"
    "    }\n\n"
    "    #[test]\n"
    "    fn overload_and_drain_responses_are_stable_and_retryable() {\n",
    "server active close test",
)
SERVER.write_text(server, encoding="utf-8")
