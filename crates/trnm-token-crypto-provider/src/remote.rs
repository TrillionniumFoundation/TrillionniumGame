use std::fmt;
use std::time::Duration;

pub const REMOTE_HS256_TAG_BYTES: usize = 32;
pub const MAX_REMOTE_MAC_MESSAGE_BYTES: usize = 1024 * 1024;
pub const MAX_REMOTE_MAC_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RemoteMacPurpose {
    AccessToken,
    RefreshCredential,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RemoteMacRequestKind {
    Sign,
    Verify { tag: [u8; REMOTE_HS256_TAG_BYTES] },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteMacRequest {
    pub request_id: [u8; 16],
    pub key_reference: String,
    pub purpose: RemoteMacPurpose,
    pub message: Vec<u8>,
    pub kind: RemoteMacRequestKind,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RemoteMacResponse {
    Signed {
        request_id: [u8; 16],
        tag: [u8; REMOTE_HS256_TAG_BYTES],
    },
    Verified {
        request_id: [u8; 16],
        valid: bool,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RemoteMacError {
    InvalidConfiguration,
    InvalidRequest,
    Timeout,
    TransportUnavailable,
    ProtocolViolation,
    VerificationRejected,
}

pub trait RemoteMacTransport: Send + Sync {
    fn exchange(
        &self,
        request: &RemoteMacRequest,
        timeout: Duration,
    ) -> Result<RemoteMacResponse, RemoteMacError>;
}

pub struct RemoteHs256Provider<T> {
    transport: T,
    key_reference: String,
    timeout: Duration,
}

impl<T> fmt::Debug for RemoteHs256Provider<T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RemoteHs256Provider")
            .field("transport", &"<opaque-transport>")
            .field("key_reference", &"<opaque-key-reference>")
            .field("timeout", &self.timeout)
            .finish()
    }
}

impl<T: RemoteMacTransport> RemoteHs256Provider<T> {
    pub fn new(
        transport: T,
        key_reference: impl Into<String>,
        timeout: Duration,
    ) -> Result<Self, RemoteMacError> {
        let key_reference = key_reference.into();
        if !valid_key_reference(&key_reference)
            || timeout.is_zero()
            || timeout > MAX_REMOTE_MAC_TIMEOUT
        {
            return Err(RemoteMacError::InvalidConfiguration);
        }
        Ok(Self {
            transport,
            key_reference,
            timeout,
        })
    }

    pub fn sign(
        &self,
        request_id: [u8; 16],
        purpose: RemoteMacPurpose,
        message: &[u8],
    ) -> Result<[u8; REMOTE_HS256_TAG_BYTES], RemoteMacError> {
        validate_request(request_id, message)?;
        let request = RemoteMacRequest {
            request_id,
            key_reference: self.key_reference.clone(),
            purpose,
            message: message.to_vec(),
            kind: RemoteMacRequestKind::Sign,
        };
        match self.transport.exchange(&request, self.timeout)? {
            RemoteMacResponse::Signed {
                request_id: response_id,
                tag,
            } if response_id == request_id => Ok(tag),
            _ => Err(RemoteMacError::ProtocolViolation),
        }
    }

    pub fn verify(
        &self,
        request_id: [u8; 16],
        purpose: RemoteMacPurpose,
        message: &[u8],
        tag: &[u8],
    ) -> Result<(), RemoteMacError> {
        validate_request(request_id, message)?;
        let tag: [u8; REMOTE_HS256_TAG_BYTES] =
            tag.try_into().map_err(|_| RemoteMacError::InvalidRequest)?;
        let request = RemoteMacRequest {
            request_id,
            key_reference: self.key_reference.clone(),
            purpose,
            message: message.to_vec(),
            kind: RemoteMacRequestKind::Verify { tag },
        };
        match self.transport.exchange(&request, self.timeout)? {
            RemoteMacResponse::Verified {
                request_id: response_id,
                valid: true,
            } if response_id == request_id => Ok(()),
            RemoteMacResponse::Verified {
                request_id: response_id,
                valid: false,
            } if response_id == request_id => Err(RemoteMacError::VerificationRejected),
            _ => Err(RemoteMacError::ProtocolViolation),
        }
    }

    #[must_use]
    pub const fn timeout(&self) -> Duration {
        self.timeout
    }
}

fn valid_key_reference(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value.bytes().all(|byte| {
            matches!(
                byte,
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
}

fn validate_request(request_id: [u8; 16], message: &[u8]) -> Result<(), RemoteMacError> {
    if request_id.iter().all(|byte| *byte == 0)
        || message.is_empty()
        || message.len() > MAX_REMOTE_MAC_MESSAGE_BYTES
    {
        return Err(RemoteMacError::InvalidRequest);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Debug)]
    struct MockTransport {
        response: Result<RemoteMacResponse, RemoteMacError>,
        requests: Mutex<Vec<RemoteMacRequest>>,
        timeouts: Mutex<Vec<Duration>>,
    }

    impl MockTransport {
        fn new(response: Result<RemoteMacResponse, RemoteMacError>) -> Self {
            Self {
                response,
                requests: Mutex::new(Vec::new()),
                timeouts: Mutex::new(Vec::new()),
            }
        }
    }

    impl RemoteMacTransport for MockTransport {
        fn exchange(
            &self,
            request: &RemoteMacRequest,
            timeout: Duration,
        ) -> Result<RemoteMacResponse, RemoteMacError> {
            self.requests.lock().unwrap().push(request.clone());
            self.timeouts.lock().unwrap().push(timeout);
            self.response.clone()
        }
    }

    fn request_id(value: u8) -> [u8; 16] {
        [value; 16]
    }

    #[test]
    fn opaque_reference_signing_never_accepts_key_bytes() {
        let id = request_id(1);
        let transport = MockTransport::new(Ok(RemoteMacResponse::Signed {
            request_id: id,
            tag: [7; REMOTE_HS256_TAG_BYTES],
        }));
        let provider = RemoteHs256Provider::new(
            transport,
            "kms://tenant/access/epoch-7",
            Duration::from_millis(250),
        )
        .unwrap();
        assert_eq!(
            provider
                .sign(id, RemoteMacPurpose::AccessToken, b"header.payload")
                .unwrap(),
            [7; REMOTE_HS256_TAG_BYTES]
        );
        let requests = provider.transport.requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].key_reference, "kms://tenant/access/epoch-7");
        assert_eq!(requests[0].message, b"header.payload");
        assert_eq!(
            provider.transport.timeouts.lock().unwrap().as_slice(),
            &[Duration::from_millis(250)]
        );
    }

    #[test]
    fn verification_is_exact_width_correlated_and_fail_closed() {
        let id = request_id(2);
        let provider = RemoteHs256Provider::new(
            MockTransport::new(Ok(RemoteMacResponse::Verified {
                request_id: id,
                valid: false,
            })),
            "hsm:partition/token-key",
            Duration::from_millis(100),
        )
        .unwrap();
        assert_eq!(
            provider
                .verify(id, RemoteMacPurpose::AccessToken, b"message", &[0; 31])
                .unwrap_err(),
            RemoteMacError::InvalidRequest
        );
        assert_eq!(
            provider
                .verify(id, RemoteMacPurpose::AccessToken, b"message", &[0; 32])
                .unwrap_err(),
            RemoteMacError::VerificationRejected
        );
    }

    #[test]
    fn mismatched_response_and_transport_failure_never_fall_back() {
        let id = request_id(3);
        let mismatch = RemoteHs256Provider::new(
            MockTransport::new(Ok(RemoteMacResponse::Signed {
                request_id: request_id(4),
                tag: [1; 32],
            })),
            "kms:key",
            Duration::from_millis(100),
        )
        .unwrap();
        assert_eq!(
            mismatch
                .sign(id, RemoteMacPurpose::RefreshCredential, b"refresh")
                .unwrap_err(),
            RemoteMacError::ProtocolViolation
        );
        let unavailable = RemoteHs256Provider::new(
            MockTransport::new(Err(RemoteMacError::TransportUnavailable)),
            "kms:key",
            Duration::from_millis(100),
        )
        .unwrap();
        assert_eq!(
            unavailable
                .sign(id, RemoteMacPurpose::AccessToken, b"message")
                .unwrap_err(),
            RemoteMacError::TransportUnavailable
        );
    }

    #[test]
    fn configuration_request_and_debug_boundaries_are_redacted() {
        assert_eq!(
            RemoteHs256Provider::new(
                MockTransport::new(Err(RemoteMacError::Timeout)),
                "bad key",
                Duration::from_millis(100),
            )
            .unwrap_err(),
            RemoteMacError::InvalidConfiguration
        );
        assert_eq!(
            RemoteHs256Provider::new(
                MockTransport::new(Err(RemoteMacError::Timeout)),
                "kms:key",
                Duration::ZERO,
            )
            .unwrap_err(),
            RemoteMacError::InvalidConfiguration
        );
        let provider = RemoteHs256Provider::new(
            MockTransport::new(Err(RemoteMacError::Timeout)),
            "kms:secret-reference",
            Duration::from_millis(100),
        )
        .unwrap();
        let debug = format!("{provider:?}");
        assert!(!debug.contains("secret-reference"));
        assert!(debug.contains("<opaque-key-reference>"));
        assert_eq!(
            provider
                .sign([0; 16], RemoteMacPurpose::AccessToken, b"message")
                .unwrap_err(),
            RemoteMacError::InvalidRequest
        );
        assert_eq!(
            provider
                .sign(request_id(1), RemoteMacPurpose::AccessToken, &[])
                .unwrap_err(),
            RemoteMacError::InvalidRequest
        );
    }
}
