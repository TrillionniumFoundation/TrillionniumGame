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
    RemoteMacError, RemoteMacPurpose, RemoteMacRequest, RemoteMacRequestKind, RemoteMacResponse,
    RemoteMacTransport, MAX_REMOTE_MAC_MESSAGE_BYTES, MAX_REMOTE_MAC_TIMEOUT,
    REMOTE_HS256_TAG_BYTES,
};

const MAX_SOCKET_PATH_BYTES: usize = 4096;
const MAX_KEY_REFERENCE_BYTES: usize = 256;
const MAX_FRAME_BYTES: usize = MAX_REMOTE_MAC_MESSAGE_BYTES + 4096;
const MAX_PENDING_CONNECTS: usize = 8;
static PENDING_CONNECTS: AtomicUsize = AtomicUsize::new(0);

/// Bounded Unix-domain-socket transport for the opaque remote MAC protocol.
///
/// This adapter deliberately carries only an opaque key reference, purpose,
/// request correlation identity, message and optional verification tag. Raw
/// key bytes remain outside this process. The caller must bind the endpoint to
/// the expected service UID/GID; endpoint inspection rejects symlink
/// substitution and an inode/ownership change between the pre-connect and
/// post-connect observations.
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

    fn exchange_frame(
        &self,
        request: &RemoteMacRequest,
        timeout: Duration,
    ) -> Result<Vec<u8>, RemoteMacError> {
        validate_remote_request(request, timeout)?;
        let frame = encode_request(request)?;
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
    fn exchange(
        &self,
        request: &RemoteMacRequest,
        timeout: Duration,
    ) -> Result<RemoteMacResponse, RemoteMacError> {
        decode_response(self.exchange_frame(request, timeout)?, request)
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

fn validate_remote_request(
    request: &RemoteMacRequest,
    timeout: Duration,
) -> Result<(), RemoteMacError> {
    let key_reference = request.key_reference.as_bytes();
    if request.request_id.iter().all(|byte| *byte == 0)
        || key_reference.is_empty()
        || key_reference.len() > MAX_KEY_REFERENCE_BYTES
        || !key_reference.iter().all(|byte| {
            matches!(
                *byte,
                b'a'..=b'z'
                    | b'A'..=b'Z'
                    | b'0'..=b'9'
                    | b'/'
                    | b'_'
                    | b'-'
                    | b'.'
                    | b':'
                    | b'@'
            )
        })
        || request.message.is_empty()
        || request.message.len() > MAX_REMOTE_MAC_MESSAGE_BYTES
        || timeout.is_zero()
        || timeout > MAX_REMOTE_MAC_TIMEOUT
    {
        return Err(RemoteMacError::InvalidRequest);
    }
    Ok(())
}

fn encode_request(request: &RemoteMacRequest) -> Result<Vec<u8>, RemoteMacError> {
    let key_reference = request.key_reference.as_bytes();
    let message = request.message.as_slice();
    let tag_bytes = match &request.kind {
        RemoteMacRequestKind::Sign => 0,
        RemoteMacRequestKind::Verify { .. } => REMOTE_HS256_TAG_BYTES,
    };
    let mut frame = Vec::with_capacity(
        1 + 1 + request.request_id.len() + 2 + key_reference.len() + 4 + message.len() + tag_bytes,
    );
    frame.push(match &request.kind {
        RemoteMacRequestKind::Sign => 1,
        RemoteMacRequestKind::Verify { .. } => 2,
    });
    frame.push(match request.purpose {
        RemoteMacPurpose::AccessToken => 1,
        RemoteMacPurpose::RefreshCredential => 2,
    });
    frame.extend_from_slice(&request.request_id);
    push_bounded_bytes(&mut frame, key_reference)?;
    let message_len = u32::try_from(message.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
    frame.extend_from_slice(&message_len.to_be_bytes());
    frame.extend_from_slice(message);
    if let RemoteMacRequestKind::Verify { tag } = &request.kind {
        frame.extend_from_slice(tag);
    }
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

fn decode_response(
    response: Vec<u8>,
    request: &RemoteMacRequest,
) -> Result<RemoteMacResponse, RemoteMacError> {
    match &request.kind {
        RemoteMacRequestKind::Sign => {
            if response.len() != 1 + 16 + REMOTE_HS256_TAG_BYTES || response[0] != 1 {
                return Err(RemoteMacError::ProtocolViolation);
            }
            let response_id: [u8; 16] = response[1..17]
                .try_into()
                .map_err(|_| RemoteMacError::ProtocolViolation)?;
            if response_id != request.request_id {
                return Err(RemoteMacError::ProtocolViolation);
            }
            let tag: [u8; REMOTE_HS256_TAG_BYTES] = response[17..]
                .try_into()
                .map_err(|_| RemoteMacError::ProtocolViolation)?;
            Ok(RemoteMacResponse::Signed {
                request_id: response_id,
                tag,
            })
        }
        RemoteMacRequestKind::Verify { .. } => {
            if response.len() != 1 + 16 + 1 || response[0] != 2 || response[17] > 1 {
                return Err(RemoteMacError::ProtocolViolation);
            }
            let response_id: [u8; 16] = response[1..17]
                .try_into()
                .map_err(|_| RemoteMacError::ProtocolViolation)?;
            if response_id != request.request_id {
                return Err(RemoteMacError::ProtocolViolation);
            }
            Ok(RemoteMacResponse::Verified {
                request_id: response_id,
                valid: response[17] == 1,
            })
        }
    }
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
    use std::fs::Permissions;
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    use std::process;
    use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};

    static NEXT_TEST_DIRECTORY: AtomicU64 = AtomicU64::new(1);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let sequence = NEXT_TEST_DIRECTORY.fetch_add(1, AtomicOrdering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "trnm-remote-mac-{}-{label}-{sequence}",
                process::id()
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir(&path).unwrap();
            Self(path)
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn request(kind: RemoteMacRequestKind) -> RemoteMacRequest {
        RemoteMacRequest {
            request_id: [7; 16],
            key_reference: "kms://tenant/access/epoch-7".to_owned(),
            purpose: RemoteMacPurpose::AccessToken,
            message: b"exact-payload".to_vec(),
            kind,
        }
    }

    fn transport(path: &Path) -> UnixSocketRemoteMacTransport {
        let metadata = fs::symlink_metadata(path).unwrap();
        UnixSocketRemoteMacTransport::new(path, metadata.uid(), metadata.gid()).unwrap()
    }

    fn signed_response(request_id: [u8; 16], fill: u8) -> Vec<u8> {
        let mut response = Vec::with_capacity(1 + 16 + REMOTE_HS256_TAG_BYTES);
        response.push(1);
        response.extend_from_slice(&request_id);
        response.extend_from_slice(&[fill; REMOTE_HS256_TAG_BYTES]);
        response
    }

    fn verified_response(request_id: [u8; 16], valid: bool) -> Vec<u8> {
        let mut response = Vec::with_capacity(1 + 16 + 1);
        response.push(2);
        response.extend_from_slice(&request_id);
        response.push(u8::from(valid));
        response
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
        let directory = TestDirectory::new("sign");
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = serve_once(listener, signed_response([7; 16], 0xA5));
        let signed = transport(&socket)
            .exchange(&request(RemoteMacRequestKind::Sign), Duration::from_secs(1))
            .unwrap();
        assert_eq!(
            signed,
            RemoteMacResponse::Signed {
                request_id: [7; 16],
                tag: [0xA5; REMOTE_HS256_TAG_BYTES],
            }
        );
        worker.join().unwrap();
    }

    #[test]
    fn verify_false_and_mismatched_response_are_fail_closed() {
        let directory = TestDirectory::new("verify");
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = serve_once(listener, verified_response([7; 16], false));
        let verified = transport(&socket)
            .exchange(
                &request(RemoteMacRequestKind::Verify {
                    tag: [0x11; REMOTE_HS256_TAG_BYTES],
                }),
                Duration::from_secs(1),
            )
            .unwrap();
        assert_eq!(
            verified,
            RemoteMacResponse::Verified {
                request_id: [7; 16],
                valid: false,
            }
        );
        worker.join().unwrap();

        let mismatch_socket = directory.path().join("mac-mismatch.sock");
        let listener = UnixListener::bind(&mismatch_socket).unwrap();
        let worker = serve_once(listener, signed_response([8; 16], 0xA5));
        let error = transport(&mismatch_socket)
            .exchange(&request(RemoteMacRequestKind::Sign), Duration::from_secs(1))
            .unwrap_err();
        assert_eq!(error, RemoteMacError::ProtocolViolation);
        worker.join().unwrap();
    }

    #[test]
    fn oversized_truncated_and_relative_paths_are_rejected() {
        let relative = UnixSocketRemoteMacTransport::new("relative.sock", 1, 1).unwrap_err();
        assert_eq!(relative, RemoteMacError::InvalidConfiguration);

        let directory = TestDirectory::new("oversized");
        let socket = directory.path().join("mac.sock");
        let _listener = UnixListener::bind(&socket).unwrap();
        let mut oversized = request(RemoteMacRequestKind::Sign);
        oversized.message = vec![1; MAX_REMOTE_MAC_MESSAGE_BYTES + 1];
        let error = transport(&socket)
            .exchange(&oversized, Duration::from_secs(1))
            .unwrap_err();
        assert_eq!(error, RemoteMacError::InvalidRequest);
    }

    #[test]
    fn slow_drip_response_cannot_extend_total_deadline() {
        let directory = TestDirectory::new("slow");
        let socket = directory.path().join("mac.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request_length = [0u8; 4];
            stream.read_exact(&mut request_length).unwrap();
            let mut request = vec![0u8; u32::from_be_bytes(request_length) as usize];
            stream.read_exact(&mut request).unwrap();
            let response = signed_response([7; 16], 0xA5);
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
            .exchange(
                &request(RemoteMacRequestKind::Sign),
                Duration::from_millis(120),
            )
            .unwrap_err();
        assert_eq!(error, RemoteMacError::Timeout);
        assert!(started.elapsed() < Duration::from_secs(1));
        worker.join().unwrap();
    }

    #[test]
    fn wrong_peer_identity_and_insecure_parent_fail_closed() {
        let directory = TestDirectory::new("peer");
        let socket = directory.path().join("mac.sock");
        let _listener = UnixListener::bind(&socket).unwrap();
        let metadata = fs::symlink_metadata(&socket).unwrap();
        let wrong_uid = metadata.uid().wrapping_add(1);
        let wrong = UnixSocketRemoteMacTransport::new(&socket, wrong_uid, metadata.gid()).unwrap();
        assert_eq!(
            wrong
                .exchange(
                    &request(RemoteMacRequestKind::Sign),
                    Duration::from_millis(50),
                )
                .unwrap_err(),
            RemoteMacError::ProtocolViolation
        );

        fs::set_permissions(directory.path(), Permissions::from_mode(0o777)).unwrap();
        let insecure = transport(&socket)
            .exchange(
                &request(RemoteMacRequestKind::Sign),
                Duration::from_millis(50),
            )
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
