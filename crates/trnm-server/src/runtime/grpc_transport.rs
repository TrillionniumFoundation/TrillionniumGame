//! Own every accepted socket until drop, including tonic's detached connection
//! tasks. Finite process drain closes real sockets before aborting server task;
//! runtime destruction then cancels async tasks and joins native blocking jobs.
use std::collections::BTreeMap;
use std::io;
use std::net::{Shutdown, SocketAddr, TcpStream as StdStream};
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Waker};
use std::time::{Duration, Instant};

use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio::net::{TcpListener, TcpStream};
use tonic::codegen::tokio_stream::Stream;
use tonic::transport::server::Connected;

use crate::runtime::app::SharedDrain;
use crate::runtime::error::ServerError;

pub(super) const MAX_CONNECTIONS: usize = 32;
pub(super) const GRACEFUL_DRAIN: Duration = Duration::from_secs(1);
// Deliberately local policy, not a Nakama lifetime-equivalence claim. This starts
// at TCP acceptance and therefore covers a missing preface/headers as well as
// active sessions. Expiry closes I/O but cannot undo native effects.
pub(super) const MAX_CONNECTION_LIFETIME: Duration = Duration::from_secs(30);

struct RegisteredSocket {
    socket: StdStream,
    accepted_at: Instant,
    expiry_shutdown_sent: bool,
}

#[derive(Default)]
struct Connections {
    next_id: u64,
    sockets: BTreeMap<u64, RegisteredSocket>,
    capacity_waker: Option<Waker>,
    sealed: bool,
}
#[derive(Clone)]
pub(super) struct SocketRegistry(Arc<Mutex<Connections>>, Duration);
impl Default for SocketRegistry {
    fn default() -> Self {
        Self::with_lifetime(MAX_CONNECTION_LIFETIME)
    }
}
impl SocketRegistry {
    fn with_lifetime(lifetime: Duration) -> Self {
        Self(Arc::new(Mutex::new(Connections::default())), lifetime)
    }
    fn close_expired(&self, now: Instant) -> usize {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut expired = 0;
        for registered in state.sockets.values_mut() {
            if !registered.expiry_shutdown_sent
                && now.saturating_duration_since(registered.accepted_at) >= self.1
            {
                let _ = registered.socket.shutdown(Shutdown::Both);
                registered.expiry_shutdown_sent = true;
                expired += 1;
            }
        }
        // Do NOT remove entries or wake capacity here. The actual connection's
        // OwnedSocket destructor alone releases its slot after task cleanup.
        expired
    }
    fn poll_capacity(&self, cx: &mut Context<'_>) -> Poll<bool> {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.sealed {
            return Poll::Ready(false);
        }
        if state.sockets.len() < MAX_CONNECTIONS {
            return Poll::Ready(true);
        }
        state.capacity_waker = Some(cx.waker().clone());
        Poll::Pending
    }
    fn register(&self, socket: &StdStream) -> io::Result<Option<u64>> {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.sealed || state.sockets.len() >= MAX_CONNECTIONS {
            return Ok(None);
        }
        let id = state
            .next_id
            .checked_add(1)
            .ok_or_else(|| io::Error::other("gRPC connection generation exhausted"))?;
        let owned = socket.try_clone()?;
        state.next_id = id;
        state.sockets.insert(
            id,
            RegisteredSocket {
                socket: owned,
                accepted_at: Instant::now(),
                expiry_shutdown_sent: false,
            },
        );
        Ok(Some(id))
    }
    fn remove(&self, id: u64) {
        let wake = {
            let mut state = self
                .0
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.sockets.remove(&id);
            state.capacity_waker.take()
        };
        if let Some(waker) = wake {
            waker.wake();
        }
    }
    pub(super) fn close_all(&self) {
        let wake = {
            let mut state = self
                .0
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.sealed = true;
            for socket in state.sockets.values() {
                let _ = socket.socket.shutdown(Shutdown::Both);
            }
            state.capacity_waker.take()
        };
        if let Some(waker) = wake {
            waker.wake();
        }
    }
}

pub(super) struct OwnedIncoming {
    listener: TcpListener,
    registry: SocketRegistry,
    draining: SharedDrain,
}
impl OwnedIncoming {
    pub(super) async fn bind(bind: SocketAddr, draining: SharedDrain) -> io::Result<Self> {
        Ok(Self {
            listener: TcpListener::bind(bind).await?,
            registry: SocketRegistry::default(),
            draining,
        })
    }
    pub(super) fn registry(&self) -> SocketRegistry {
        self.registry.clone()
    }
}
impl Stream for OwnedIncoming {
    type Item = io::Result<OwnedSocket>;
    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        if self.draining.is_draining() {
            return Poll::Ready(None);
        }
        match self.registry.poll_capacity(cx) {
            Poll::Ready(false) => return Poll::Ready(None),
            Poll::Pending => return Poll::Pending,
            Poll::Ready(true) => {}
        }
        let (socket, peer) = match self.listener.poll_accept(cx) {
            Poll::Pending => return Poll::Pending,
            Poll::Ready(Err(error)) => return Poll::Ready(Some(Err(error))),
            Poll::Ready(Ok(pair)) => pair,
        };
        let standard = match socket.into_std() {
            Ok(socket) => socket,
            Err(error) => return Poll::Ready(Some(Err(error))),
        };
        let id = match self.registry.register(&standard) {
            Ok(Some(id)) => id,
            Ok(None) => {
                let _ = standard.shutdown(Shutdown::Both);
                return Poll::Ready(None);
            }
            Err(error) => return Poll::Ready(Some(Err(error))),
        };
        match TcpStream::from_std(standard) {
            Ok(socket) => Poll::Ready(Some(Ok(OwnedSocket {
                socket,
                peer,
                id,
                registry: self.registry.clone(),
            }))),
            Err(error) => {
                self.registry.remove(id);
                Poll::Ready(Some(Err(error)))
            }
        }
    }
}

pub(super) struct OwnedSocket {
    socket: TcpStream,
    peer: SocketAddr,
    id: u64,
    registry: SocketRegistry,
}
impl Drop for OwnedSocket {
    fn drop(&mut self) {
        self.registry.remove(self.id);
    }
}
impl Connected for OwnedSocket {
    type ConnectInfo = SocketAddr;
    fn connect_info(&self) -> Self::ConnectInfo {
        self.peer
    }
}
impl AsyncRead for OwnedSocket {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.socket).poll_read(cx, buf)
    }
}
impl AsyncWrite for OwnedSocket {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.socket).poll_write(cx, bytes)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.socket).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.socket).poll_shutdown(cx)
    }
}

pub(super) async fn supervise(
    mut server: tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
    registry: SocketRegistry,
    draining: SharedDrain,
) -> Result<(), ServerError> {
    while !server.is_finished() && !draining.is_draining() {
        registry.close_expired(Instant::now());
        tokio::time::sleep(super::SHUTDOWN_POLL_INTERVAL).await;
    }
    let result = if server.is_finished() {
        server.await
    } else {
        match tokio::time::timeout(GRACEFUL_DRAIN, &mut server).await {
            Ok(result) => result,
            Err(_) => {
                // Seal before closing, so a racing accept cannot escape custody.
                registry.close_all();
                server.abort();
                let _ = server.await;
                // This function returns only to the dedicated runtime owner,
                // which drops the runtime (not shutdown_background/timeout).
                // Async connections are cancelled before native workers join.
                return Ok(());
            }
        }
    };
    registry.close_all();
    match result {
        Ok(Ok(())) => Ok(()),
        Ok(Err(_)) => Err(ServerError::Configuration("grpc_server_failed")),
        Err(_) => Err(ServerError::Configuration("grpc_server_panicked")),
    }
}

#[cfg(test)]
#[path = "grpc_transport_tests.rs"]
mod tests;
