use std::fmt;
use std::fs::{self, Metadata};
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use crate::remote::{
    RemoteMacError, RemoteMacPurpose, RemoteMacRequest, RemoteMacRequestKind, RemoteMacResponse,
    RemoteMacTransport, MAX_REMOTE_MAC_TIMEOUT, REMOTE_HS256_TAG_BYTES,
};

const REQUEST_MAGIC: &[u8; 8] = b"TRNMHMC1";
const RESPONSE_MAGIC: &[u8; 8] = b"TRNMRMC1";
const MAX_SOCKET_PATH_BYTES: usize = 100;
const MAX_REQUEST_FRAME_BYTES: usize = 1024 * 1024 + 512;
const MAX_RESPONSE_FRAME_BYTES: usize = 64;
const MAX_PENDING_CONNECTS: usize = 8;
const REDACTED_SOCKET_PATH: &str = "<redacted-socket-path>";

static PENDING_CONNECTS: AtomicUsize = AtomicUsize::new(0);

#[derive(Clone)]
pub struct UnixSocketRemoteMacTransport {
    socket_path: PathBuf,
    expected_peer_uid: u32,
    expected_peer_gid: u32,
}

impl fmt::Debug for UnixSocketRemoteMacTransport {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("UnixSocketRemoteMacTransport")
            .field("socket_path", &REDACTED_SOCKET_PATH)
            .field("expected_peer_uid", &self.expected_peer_uid)
            .field("expected_peer_gid", &self.expected_peer_gid)
            .finish()
    }
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

    #[must_use]
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    #[must_use]
    pub const fn expected_peer_uid(&self) -> u32 {
        self.expected_peer_uid
    }

    #[must_use]
    pub const fn expected_peer_gid(&self) -> u32 {
        self.expected_peer_gid
    }
}

impl RemoteMacTransport for UnixSocketRemoteMacTransport {
    fn exchange(
        &self,
        request: &RemoteMacRequest,
        timeout: Duration,
    ) -> Result<RemoteMacResponse, RemoteMacError> {
        if timeout.is_zero() || timeout > MAX_REMOTE_MAC_TIMEOUT {
            return Err(RemoteMacError::InvalidRequest);
        }
        let deadline = Instant::now()
            .checked_add(timeout)
            .ok_or(RemoteMacError::InvalidRequest)?;
        let frame = encode_request(request)?;
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
        let length = u32::try_from(frame.len())
            .map_err(|_| RemoteMacError::InvalidRequest)?
            .to_be_bytes();
        write_all_before_deadline(&mut stream, &length, deadline)?;
        write_all_before_deadline(&mut stream, &frame, deadline)?;
        flush_before_deadline(&mut stream, deadline)?;

        let mut response_length = [0_u8; 4];
        read_exact_before_deadline(&mut stream, &mut response_length, deadline)?;
        let response_length = u32::from_be_bytes(response_length) as usize;
        if response_length == 0 || response_length > MAX_RESPONSE_FRAME_BYTES {
            return Err(RemoteMacError::ProtocolViolation);
        }
        let mut response = vec![0_u8; response_length];
        read_exact_before_deadline(&mut stream, &mut response, deadline)?;
        decode_response(&response)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileIdentity {
    device: u64,
    inode: u64,
    mode: u32,
    links: u64,
    uid: u32,
    gid: u32,
}

impl FileIdentity {
    fn from_metadata(metadata: &Metadata) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            links: metadata.nlink(),
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
        || (socket_metadata.mode() & 0o020 != 0 && socket_metadata.gid() != expected_peer_gid)
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

fn secure_parent_directory(metadata: &Metadata, expected_uid: u32, expected_gid: u32) -> bool {
    let mode = metadata.permissions().mode();
    (metadata.uid() == 0 || metadata.uid() == expected_uid)
        && mode & 0o002 == 0
        && (mode & 0o020 == 0 || metadata.gid() == expected_gid)
}

fn encode_request(request: &RemoteMacRequest) -> Result<Vec<u8>, RemoteMacError> {
    let key = request.key_reference.as_bytes();
    let key_length = u16::try_from(key.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
    let message_length =
        u32::try_from(request.message.len()).map_err(|_| RemoteMacError::InvalidRequest)?;
    let (operation, tag) = match request.kind {
        RemoteMacRequestKind::Sign => (1_u8, None),
        RemoteMacRequestKind::Verify { tag } => (2_u8, Some(tag)),
    };
    let purpose = match request.purpose {
        RemoteMacPurpose::AccessToken => 1_u8,
        RemoteMacPurpose::RefreshCredential => 2_u8,
    };
    let capacity = 8
        + 1
        + 1
        + 16
        + 2
        + 4
        + key.len()
        + request.message.len()
        + tag.map_or(0, |_| REMOTE_HS256_TAG_BYTES);
    if capacity > MAX_REQUEST_FRAME_BYTES {
        return Err(RemoteMacError::InvalidRequest);
    }
    let mut output = Vec::with_capacity(capacity);
    output.extend_from_slice(REQUEST_MAGIC);
    output.push(operation);
    output.push(purpose);
    output.extend_from_slice(&request.request_id);
    output.extend_from_slice(&key_length.to_be_bytes());
    output.extend_from_slice(&message_length.to_be_bytes());
    output.extend_from_slice(key);
    output.extend_from_slice(&request.message);
    if let Some(tag) = tag {
        output.extend_from_slice(&tag);
    }
    Ok(output)
}

fn decode_response(frame: &[u8]) -> Result<RemoteMacResponse, RemoteMacError> {
    if frame.len() < 26 || &frame[..8] != RESPONSE_MAGIC {
        return Err(RemoteMacError::ProtocolViolation);
    }
    let operation = frame[8];
    let status = frame[9];
    let mut request_id = [0_u8; 16];
    request_id.copy_from_slice(&frame[10..26]);
    match (operation, status, frame.len()) {
        (1, 0, 58) => {
            let mut tag = [0_u8; REMOTE_HS256_TAG_BYTES];
            tag.copy_from_slice(&frame[26..58]);
            Ok(RemoteMacResponse::Signed { request_id, tag })
        }
        (2, 0, 26) => Ok(RemoteMacResponse::Verified {
            request_id,
            valid: true,
        }),
        (2, 1, 26) => Ok(RemoteMacResponse::Verified {
            request_id,
            valid: false,
        }),
        _ => Err(RemoteMacError::ProtocolViolation),
    }
}

fn map_io_error(error: io::Error) -> RemoteMacError {
    match error.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => RemoteMacError::Timeout,
        io::ErrorKind::UnexpectedEof | io::ErrorKind::InvalidData => {
            RemoteMacError::ProtocolViolation
        }
        _ => RemoteMacError::TransportUnavailable,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::remote::RemoteHs256Provider;
    use std::os::unix::net::UnixListener;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::thread;

    static NEXT_SOCKET: AtomicU64 = AtomicU64::new(1);

    struct SocketGuard {
        socket: PathBuf,
        directory: PathBuf,
    }

    impl Drop for SocketGuard {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.socket);
            let _ = fs::remove_dir(&self.directory);
        }
    }

    fn socket_path() -> (PathBuf, SocketGuard) {
        let directory = std::env::temp_dir().join(format!(
            "trnm-mac-{}-{}",
            std::process::id(),
            NEXT_SOCKET.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir(&directory).unwrap();
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
        let socket = directory.join("provider.sock");
        let guard = SocketGuard {
            socket: socket.clone(),
            directory,
        };
        (socket, guard)
    }

    fn bind_secure_listener(path: &Path) -> UnixListener {
        let listener = UnixListener::bind(path).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
        listener
    }

    fn transport(path: PathBuf) -> UnixSocketRemoteMacTransport {
        let metadata = fs::symlink_metadata(&path).unwrap();
        UnixSocketRemoteMacTransport::new(path, metadata.uid(), metadata.gid()).unwrap()
    }

    fn read_request(mut stream: &UnixStream) -> Vec<u8> {
        let mut length = [0_u8; 4];
        stream.read_exact(&mut length).unwrap();
        let mut frame = vec![0_u8; u32::from_be_bytes(length) as usize];
        stream.read_exact(&mut frame).unwrap();
        frame
    }

    fn write_response(mut stream: &UnixStream, payload: &[u8]) {
        stream
            .write_all(&u32::try_from(payload.len()).unwrap().to_be_bytes())
            .unwrap();
        stream.write_all(payload).unwrap();
        stream.flush().unwrap();
    }

    #[test]
    fn sign_round_trip_uses_bounded_opaque_frame() {
        let (path, _guard) = socket_path();
        let listener = bind_secure_listener(&path);
        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let request = read_request(&stream);
            assert_eq!(&request[..8], REQUEST_MAGIC);
            assert_eq!(request[8], 1);
            assert_eq!(request[9], 1);
            assert!(request
                .windows(b"kms://tenant/key-7".len())
                .any(|row| row == b"kms://tenant/key-7"));
            assert!(!request.windows(10).any(|row| row == b"raw-secret"));
            let mut response = Vec::new();
            response.extend_from_slice(RESPONSE_MAGIC);
            response.push(1);
            response.push(0);
            response.extend_from_slice(&[7; 16]);
            response.extend_from_slice(&[9; 32]);
            write_response(&stream, &response);
        });
        let provider = RemoteHs256Provider::new(
            transport(path),
            "kms://tenant/key-7",
            Duration::from_secs(1),
        )
        .unwrap();
        assert_eq!(
            provider
                .sign([7; 16], RemoteMacPurpose::AccessToken, b"header.payload")
                .unwrap(),
            [9; 32]
        );
        server.join().unwrap();
    }

    #[test]
    fn verify_false_and_mismatched_response_are_fail_closed() {
        let (path, _guard) = socket_path();
        let listener = bind_secure_listener(&path);
        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let request = read_request(&stream);
            assert_eq!(request[8], 2);
            let mut response = Vec::new();
            response.extend_from_slice(RESPONSE_MAGIC);
            response.push(2);
            response.push(1);
            response.extend_from_slice(&[8; 16]);
            write_response(&stream, &response);
        });
        let provider =
            RemoteHs256Provider::new(transport(path), "hsm:partition/key", Duration::from_secs(1))
                .unwrap();
        assert_eq!(
            provider
                .verify([8; 16], RemoteMacPurpose::AccessToken, b"message", &[3; 32],)
                .unwrap_err(),
            RemoteMacError::VerificationRejected
        );
        server.join().unwrap();
    }

    #[test]
    fn slow_drip_response_cannot_extend_total_deadline() {
        let (path, _guard) = socket_path();
        let listener = bind_secure_listener(&path);
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let _ = read_request(&stream);
            let mut response = Vec::new();
            response.extend_from_slice(RESPONSE_MAGIC);
            response.push(1);
            response.push(0);
            response.extend_from_slice(&[6; 16]);
            response.extend_from_slice(&[4; 32]);
            if stream
                .write_all(&u32::try_from(response.len()).unwrap().to_be_bytes())
                .is_err()
            {
                return;
            }
            for byte in response {
                if stream.write_all(&[byte]).is_err() {
                    break;
                }
                thread::sleep(Duration::from_millis(15));
            }
        });
        let provider = RemoteHs256Provider::new(
            transport(path),
            "kms://tenant/deadline-key",
            Duration::from_millis(80),
        )
        .unwrap();
        let started = Instant::now();
        assert_eq!(
            provider
                .sign([6; 16], RemoteMacPurpose::AccessToken, b"deadline-message")
                .unwrap_err(),
            RemoteMacError::Timeout
        );
        assert!(started.elapsed() < Duration::from_secs(1));
        server.join().unwrap();
    }

    #[test]
    fn oversized_truncated_and_relative_paths_are_rejected() {
        assert_eq!(
            UnixSocketRemoteMacTransport::new("relative.sock", 1, 1).unwrap_err(),
            RemoteMacError::InvalidConfiguration
        );
        let (path, _guard) = socket_path();
        let listener = bind_secure_listener(&path);
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let _ = read_request(&stream);
            stream.write_all(&32_u32.to_be_bytes()).unwrap();
            stream.write_all(b"short").unwrap();
        });
        let provider =
            RemoteHs256Provider::new(transport(path), "kms:key", Duration::from_secs(1)).unwrap();
        assert_eq!(
            provider
                .sign([1; 16], RemoteMacPurpose::AccessToken, b"message")
                .unwrap_err(),
            RemoteMacError::ProtocolViolation
        );
        server.join().unwrap();
    }

    #[test]
    fn wrong_peer_identity_and_insecure_parent_fail_closed() {
        let (path, guard) = socket_path();
        let _listener = bind_secure_listener(&path);
        let metadata = fs::symlink_metadata(&path).unwrap();
        let wrong_uid = if metadata.uid() == 0 {
            1
        } else {
            metadata.uid() - 1
        };
        let provider = RemoteHs256Provider::new(
            UnixSocketRemoteMacTransport::new(path.clone(), wrong_uid, metadata.gid()).unwrap(),
            "kms:key",
            Duration::from_millis(100),
        )
        .unwrap();
        assert_eq!(
            provider
                .sign([1; 16], RemoteMacPurpose::AccessToken, b"message")
                .unwrap_err(),
            RemoteMacError::ProtocolViolation
        );

        fs::set_permissions(&guard.directory, fs::Permissions::from_mode(0o707)).unwrap();
        let provider = RemoteHs256Provider::new(
            UnixSocketRemoteMacTransport::new(path, metadata.uid(), metadata.gid()).unwrap(),
            "kms:key",
            Duration::from_millis(100),
        )
        .unwrap();
        assert_eq!(
            provider
                .sign([2; 16], RemoteMacPurpose::AccessToken, b"message")
                .unwrap_err(),
            RemoteMacError::ProtocolViolation
        );
    }

    #[test]
    fn transport_debug_redacts_socket_path() {
        let (path, _guard) = socket_path();
        let listener = bind_secure_listener(&path);
        let transport = transport(path.clone());
        let debug = format!("{transport:?}");
        assert!(!debug.contains(path.to_string_lossy().as_ref()));
        assert!(debug.contains(REDACTED_SOCKET_PATH));
        drop(listener);
    }
}
