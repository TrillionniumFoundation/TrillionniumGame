use std::fmt;
use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::os::unix::net::UnixStream;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use crate::{
    KeyHandle, MacAlgorithm, MacRequest, MacResponse, RemoteMacError, RemoteMacTransport,
    MAX_MAC_PAYLOAD_BYTES,
};

const MAX_SOCKET_PATH_BYTES: usize = 4096;
const MAX_FRAME_BYTES: usize = MAX_MAC_PAYLOAD_BYTES + 4096;
const MAX_PENDING_CONNECTS: usize = 8;
static PENDING_CONNECTS: AtomicUsize = AtomicUsize::new(0);

/// Bounded Unix-domain-socket transport for the opaque remote MAC protocol.
///
/// This adapter deliberately carries only domain/handle/algorithm/payload and
/// opaque MAC bytes. The key referenced by `KeyHandle` remains outside this
/// process. The caller must bind the endpoint to the expected service UID/GID;
/// endpoint inspection rejects symlink substitution and an inode/ownership
/// change between the pre-connect and post-connect observations.
#[derive(Clone)]
pub struct UnixSocketRemoteMacTransport {
    socket_path: PathBuf,
    expected_peer_uid: u32,
    expected_peer_gid: u32,
}

impl UnixSocketRemoteMacTransport {
    pub fn new(
        socket_path: impl Into<PathBuf>,
        expected_peer_uid: u32,
        expected_peer_gid: u32,
    ) -> Result<Self, RemoteMacError> {
        let socket_path = socket_path.into();
        validate_socket_path(&socket_path)?;
        Ok(Self {
            socket_path,
            expected_peer_uid,
            expected_peer_gid,
        })
    }

    fn exchange(
        &self,
        operation: u8,
        request: &MacRequest,
        timeout: Duration,
    ) -> Result<Vec<u8>, RemoteMacError> {
        if request.payload().len() > MAX_MAC_PAYLOAD_BYTES || timeout.is_zero() {
            return Err(RemoteMacError::InvalidRequest);
        }
        let frame = encode_request(operation, request)?;
        let length = u32::try_from(frame.len())
            .map_err(|_| RemoteMacError::InvalidRequest)?
            .to_be_bytes();
        let deadline = Instant::now()
            .checked_add(timeout)
            .ok_or(RemoteMacError::InvalidRequest)?;
        let expected_endpoint = inspect_socket_endpoint(
            &self.socket_path,
            self.expected_peer_uid,
            self.expected_peer_gid,
        )?;
        let mut stream = connect_before_deadline(
            &self.socket_path,
            self.expected_peer_uid,
            self.expected_peer_gid,
            expected_endpoint,
            deadline,
        )?;

        write_all_before_deadline(&mut stream, &length, deadline)?;
        write_all_before_deadline(&mut stream, &frame, deadline)?;
        flush_before_deadline(&mut stream, deadline)?;

        let mut response_length = [0u8; 4];
        read_exact_before_deadline(&mut stream, &mut response_length, deadline)?;
        let response_length = u32::from_be_bytes(response_length) as usize;
        if response_length == 0 || response_length > MAX_FRAME_BYTES {
            return Err(RemoteMacError::ProtocolViolation);
        }
        let mut response = vec![0u8; response_length];
        read_exact_before_deadline(&mut stream, &mut response, deadline)?;
        Ok(response)
    }
}

impl fmt::Debug for UnixSocketRemoteMacTransport {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("UnixSocketRemoteMacTransport")
            .field("socket_path", &"<redacted-socket-path>")
            .field("expected_peer_uid", &self.expected_peer_uid)
            .field("expected_peer_gid", &self.expected_peer_gid)
            .finish()
    }
}

impl RemoteMacTransport for UnixSocketRemoteMacTransport {
    fn sign(&self, request: &MacRequest, timeout: Duration) -> Result<MacResponse, RemoteMacError> {
        decode_sign_response(self.exchange(1, request, timeout)?, request)
    }

    fn verify(
        &self,
        request: &MacRequest,
        expected_mac: &[u8],
        timeout: Duration,
    ) -> Result<bool, RemoteMacError> {
        let mut payload = self.exchange(2, request, timeout)?;
        let width = request.algorithm().output_len();
        if expected_mac.len() != width {
            return Err(RemoteMacError::InvalidRequest);
        }
        payload.extend_from_slice(expected_mac);
        decode_verify_response(payload, request)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileIdentity {
    device: u64,
    inode: u64,
    mode: u32,
    uid: u32,
    gid: u32,
}

impl FileIdentity {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            uid: metadata.uid(),
            gid: metadata.gid(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SocketEndpointIdentity {
    socket: FileIdentity,
    parent: FileIdentity,
}

struct PendingConnectSlot;

impl Drop for PendingConnectSlot {
    fn drop(&mut self) {
        PENDING_CONNECTS.fetch_sub(1, Ordering::Release);
    }
}

fn reserve_connect_slot() -> Result<PendingConnectSlot, RemoteMacError> {
    PENDING_CONNECTS
        .fetch_update(Ordering::AcqRel, Ordering::Acquire, |current| {
            (current < MAX_PENDING_CONNECTS).then_some(current + 1)
        })
        .map(|_| PendingConnectSlot)
        .map_err(|_| RemoteMacError::TransportUnavailable)
}

fn connect_before_deadline(
    path: &Path,
    expected_peer_uid: u32,
    expected_peer_gid: u32,
    expected_endpoint: SocketEndpointIdentity,
    deadline: Instant,
) -> Result<UnixStream, RemoteMacError> {
    let slot = reserve_connect_slot()?;
    let path = path.to_owned();
    let (sender, receiver) = mpsc::sync_channel(1);
    let connector = thread::Builder::new()
        .name("trnm-remote-mac-connect".to_owned())
        .spawn(move || {
            let _slot = slot;
            let result = connect_unix(
                &path,
                expected_peer_uid,
                expected_peer_gid,
                expected_endpoint,
            );
            let _ = sender.send(result);
        })
        .map_err(|_| RemoteMacError::TransportUnavailable)?;
    drop(connector);

    match receiver.recv_timeout(remaining_timeout(deadline)?) {
        Ok(result) => result,
        Err(RecvTimeoutError::Timeout) => Err(RemoteMacError::Timeout),
        Err(RecvTimeoutError::Disconnected) => Err(RemoteMacError::TransportUnavailable),
    }
}

fn connect_unix(
    path: &Path,
    expected_peer_uid: u32,
    expected_peer_gid: u32,
    expected_endpoint: SocketEndpointIdentity,
) -> Result<UnixStream, RemoteMacError> {
    let stream = loop {
        match UnixStream::connect(path) {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(map_io_error(error)),
            Ok(stream) => break stream,
        }
    };
    let observed_endpoint = inspect_socket_endpoint(path, expected_peer_uid, expected_peer_gid)?;
    if observed_endpoint != expected_endpoint {
        return Err(RemoteMacError::ProtocolViolation);
    }
    Ok(stream)
}

fn remaining_timeout(deadline: Instant) -> Result<Duration, RemoteMacError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| !remaining.is_zero())
        .ok_or(RemoteMacError::Timeout)
}

fn write_all_before_deadline(
    stream: &mut UnixStream,
    mut input: &[u8],
    deadline: Instant,
) -> Result<(), RemoteMacError> {
    while !input.is_empty() {
        stream
            .set_write_timeout(Some(remaining_timeout(deadline)?))
            .map_err(map_io_error)?;
        match stream.write(input) {
            Ok(0) => return Err(RemoteMacError::TransportUnavailable),
            Ok(written) => input = &input[written..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(map_io_error(error)),
        }
    }
    Ok(())
}

fn flush_before_deadline(stream: &mut UnixStream, deadline: Instant) -> Result<(), RemoteMacError> {
    stream
        .set_write_timeout(Some(remaining_timeout(deadline)?))
        .map_err(map_io_error)?;
    stream.flush().map_err(map_io_error)
}

fn read_exact_before_deadline(
    stream: &mut UnixStream,
    mut output: &mut [u8],
    deadline: Instant,
) -> Result<(), RemoteMacError> {
    while !output.is_empty() {
        stream
            .set_read_timeout(Some(remaining_timeout(deadline)?))
            .map_err(map_io_error)?;
        match stream.read(output) {
            Ok(0) => return Err(RemoteMacError::ProtocolViolation),
            Ok(read) => {
                let (_, remaining) = output.split_at_mut(read);
                output = remaining;
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(map_io_error(error)),
        }
    }
    Ok(())
}

fn validate_socket_path(path: &Path) -> Result<(), RemoteMacError> {
    let value = path.as_os_str().as_encoded_bytes();
    if !path.is_absolute()
        || path.file_name().is_none()
        || value.is_empty()
        || value.len() > MAX_SOCKET_PATH_BYTES
        || value.contains(&0)
        || path.components().any(|component| {
            matches!(
                component,
                Component::CurDir | Component::ParentDir | Component::Prefix(_)
            )
        })
    {
        return Err(RemoteMacError::InvalidConfiguration);
    }
    Ok(())
}

fn inspect_socket_endpoint(
    path: &Path,
    expected_peer_uid: u32,
    expected_peer_gid: u32,
) -> Result<SocketEndpointIdentity, RemoteMacError> {
    validate_no_symlink_directories(path)?;
    let parent = path.parent().ok_or(RemoteMacError::InvalidConfiguration)?;
    let parent_metadata =
        fs::symlink_metadata(parent).map_err(|_| RemoteMacError::ProtocolViolation)?;
    if !parent_metadata.is_dir()
        || parent_metadata.file_type().is_symlink()
        || !secure_parent_directory(&parent_metadata, expected_peer_uid, expected_peer_gid)
    {
        return Err(RemoteMacError::ProtocolViolation);
    }

    let socket_metadata =
        fs::symlink_metadata(path).map_err(|_| RemoteMacError::TransportUnavailable)?;
    if !socket_metadata.file_type().is_socket()
        || socket_metadata.file_type().is_symlink()
        || socket_metadata.uid() != expected_peer_uid
        || socket_metadata.gid() != expected_peer_gid
        || socket_metadata.nlink() != 1
        || socket_metadata.mode() & 0o002 != 0
    {
        return Err(RemoteMacError::ProtocolViolation);
    }

    Ok(SocketEndpointIdentity {
        socket: FileIdentity::from_metadata(&socket_metadata),
        parent: FileIdentity::from_metadata(&parent_metadata),
    })
}

fn validate_no_symlink_directories(path: &Path) -> Result<(), RemoteMacError> {
    let parent = path.parent().ok_or(RemoteMacError::InvalidConfiguration)?;
    let mut current = PathBuf::new();
    for component in parent.components() {
        match component {
            Component::RootDir => current.push(Path::new("/")),
            Component::Normal(value) => current.push(value),
            Component::CurDir | Component::ParentDir | Component::Prefix(_) => {
                return Err(RemoteMacError::InvalidConfiguration)
            }
        }
        let metadata =
            fs::symlink_metadata(&current).map_err(|_| RemoteMacError::ProtocolViolation)?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err(RemoteMacError::ProtocolViolation);
        }
    }
    Ok(())
}

fn secure_parent_directory(metadata: &fs::Metadata, expected_uid: u32, expected_gid: u32) -> bool {
    let owner_ok = metadata.uid() == 0 || metadata.uid() == expected_uid;
    let group_ok = metadata.gid() == expected_gid;
    let mode = metadata.mode();
    owner_ok && group_ok && mode & 0o002 == 0
}

fn encode_request(operation: u8, request: &MacRequest) -> Result<Vec<u8>, RemoteMacError> {
    let domain = request.domain().as_str().as_bytes();
    let handle = request.key_handle().as_str().as_bytes();
    let payload = request.payload();
    let mut frame =
        Vec::with_capacity(1 + 1 + 2 + domain.len() + 2 + handle.len() + 4 + payload.len());
    frame.push(operation);
    frame.push(algorithm_code(request.algorithm()));
    push_bounded_bytes(&mut frame, domain)?;
    push_bounded_bytes(&mut frame, handle)?;
    let payload_len = u32::try_from(payload.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
    frame.extend_from_slice(&payload_len.to_be_bytes());
    frame.extend_from_slice(payload);
    if frame.len() > MAX_FRAME_BYTES {
        return Err(RemoteMacError::InvalidRequest);
    }
    Ok(frame)
}

fn push_bounded_bytes(target: &mut Vec<u8>, value: &[u8]) -> Result<(), RemoteMacError> {
    let length = u16::try_from(value.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
    target.extend_from_slice(&length.to_be_bytes());
    target.extend_from_slice(value);
    Ok(())
}

fn algorithm_code(algorithm: MacAlgorithm) -> u8 {
    match algorithm {
        MacAlgorithm::Hs256 => 1,
    }
}

fn decode_sign_response(
    response: Vec<u8>,
    request: &MacRequest,
) -> Result<MacResponse, RemoteMacError> {
    let width = request.algorithm().output_len();
    if response.len() != width + 1 || response[0] != 1 {
        return Err(RemoteMacError::ProtocolViolation);
    }
    MacResponse::new(request.request_id(), response[1..].to_vec())
}

fn decode_verify_response(response: Vec<u8>, request: &MacRequest) -> Result<bool, RemoteMacError> {
    let width = request.algorithm().output_len();
    if response.len() != width + 2 || response[0] != 2 {
        return Err(RemoteMacError::ProtocolViolation);
    }
    let split = response.len() - width;
    if response[1] > 1 || response[split..].len() != width {
        return Err(RemoteMacError::ProtocolViolation);
    }
    Ok(response[1] == 1)
}

fn map_io_error(error: io::Error) -> RemoteMacError {
    match error.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => RemoteMacError::Timeout,
        _ => RemoteMacError::TransportUnavailable,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::KeyDomain;
    use std::os::unix::net::UnixListener;
    use std::sync::Arc;

    fn request(payload: &[u8]) -> MacRequest {
        MacRequest::new(
            7,
            KeyDomain::AccessToken,
            KeyHandle::new("access-epoch-7").unwrap(),
            MacAlgorithm::Hs256,
            payload.to_vec(),
        )
        .unwrap()
    }

    fn transport(path: &Path) -> UnixSocketRemoteMacTransport {
        UnixSocketRemoteMacTransport::new(path, unsafe { libc::geteuid() }, unsafe {
            libc::getegid()
        })
        .unwrap()
    }

    fn serve_once(listener: UnixListener, response: Vec<u8>) -> thread::JoinHandle<()> {
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request_length = [0u8; 4];
            stream.read_exact(&mut request_length).unwrap();
            let mut request = vec![0u8; u32::from_be_bytes(request_length) as usize];
            stream.read_exact(&mut request).unwrap();
            let response_length = u32::try_from(response.len()).unwrap().to_be_bytes();
            stream.write_all(&response_length).unwrap();
            stream.write_all(&response).unwrap();
        })
    }

    #[test]
    fn sign_round_trip_uses_bounded_opaque_frame() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let response = [vec![1u8], vec![0xA5; MacAlgorithm::Hs256.output_len()]].concat();
        let worker = serve_once(listener, response);
        let signed = transport(&socket)
            .sign(&request(b"exact-payload"), Duration::from_secs(1))
            .unwrap();
        assert_eq!(signed.mac(), &[0xA5; 32]);
        worker.join().unwrap();
    }

    #[test]
    fn verify_false_and_mismatched_response_are_fail_closed() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = serve_once(listener, vec![2, 0]);
        let verified = transport(&socket)
            .verify(&request(b"exact"), &[0x11; 32], Duration::from_secs(1))
            .unwrap();
        assert!(!verified);
        worker.join().unwrap();

        let listener = UnixListener::bind(&socket).unwrap();
        let worker = serve_once(listener, vec![1, 0]);
        let error = transport(&socket)
            .sign(&request(b"exact"), Duration::from_secs(1))
            .unwrap_err();
        assert_eq!(error, RemoteMacError::ProtocolViolation);
        worker.join().unwrap();
    }

    #[test]
    fn oversized_truncated_and_relative_paths_are_rejected() {
        let relative = UnixSocketRemoteMacTransport::new("relative.sock", 1, 1).unwrap_err();
        assert_eq!(relative, RemoteMacError::InvalidConfiguration);
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = serve_once(listener, vec![1]);
        let error = transport(&socket)
            .sign(
                &request(&vec![1; MAX_MAC_PAYLOAD_BYTES + 1]),
                Duration::from_secs(1),
            )
            .unwrap_err();
        assert_eq!(error, RemoteMacError::InvalidRequest);
        worker.join().unwrap();
    }

    #[test]
    fn slow_drip_response_cannot_extend_total_deadline() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request_length = [0u8; 4];
            stream.read_exact(&mut request_length).unwrap();
            let mut request = vec![0u8; u32::from_be_bytes(request_length) as usize];
            stream.read_exact(&mut request).unwrap();
            let response = [vec![1u8], vec![0xA5; MacAlgorithm::Hs256.output_len()]].concat();
            stream
                .write_all(&u32::try_from(response.len()).unwrap().to_be_bytes())
                .unwrap();
            for byte in response {
                thread::sleep(Duration::from_millis(25));
                if stream.write_all(&[byte]).is_err() {
                    break;
                }
            }
        });
        let started = Instant::now();
        let error = transport(&socket)
            .sign(&request(b"slow"), Duration::from_millis(120))
            .unwrap_err();
        assert_eq!(error, RemoteMacError::Timeout);
        assert!(started.elapsed() < Duration::from_secs(1));
        worker.join().unwrap();
    }

    #[test]
    fn wrong_peer_identity_and_insecure_parent_fail_closed() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("mac.sock");
        let _listener = UnixListener::bind(&socket).unwrap();
        let metadata = fs::symlink_metadata(&socket).unwrap();
        let wrong_uid = metadata.uid().wrapping_add(1);
        let wrong = UnixSocketRemoteMacTransport::new(&socket, wrong_uid, metadata.gid()).unwrap();
        assert_eq!(
            wrong
                .sign(&request(b"wrong-peer"), Duration::from_millis(50))
                .unwrap_err(),
            RemoteMacError::ProtocolViolation
        );

        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(directory.path(), fs::Permissions::from_mode(0o777)).unwrap();
        let insecure = transport(&socket)
            .sign(&request(b"insecure-parent"), Duration::from_millis(50))
            .unwrap_err();
        assert_eq!(insecure, RemoteMacError::ProtocolViolation);
    }

    #[test]
    fn transport_debug_redacts_socket_path() {
        let value =
            UnixSocketRemoteMacTransport::new("/private/provider/mac.sock", 501, 20).unwrap();
        let debug = format!("{value:?}");
        assert!(debug.contains("<redacted-socket-path>"));
        assert!(!debug.contains("/private/provider/mac.sock"));
    }
}
