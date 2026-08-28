#![forbid(unsafe_code)]

use trnm_contracts::{DomainError, RetryClass, StableCode};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransportPhase {
    RequestResponse,
    WebSocketHandshake,
    WebSocketEstablished,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RealtimeContext {
    Generic,
    UnrecognizedPayload,
    MissingPayload,
    MatchNotFound,
    MatchJoinRejected,
    RuntimeFunctionNotFound,
    RuntimeFunctionException,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(i32)]
pub enum RealtimeErrorCode {
    RuntimeException = 0,
    UnrecognizedPayload = 1,
    MissingPayload = 2,
    BadInput = 3,
    MatchNotFound = 4,
    MatchJoinRejected = 5,
    RuntimeFunctionNotFound = 6,
    RuntimeFunctionException = 7,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetryAdvice {
    None,
    Immediate,
    Backoff,
    Resync,
}

impl From<RetryClass> for RetryAdvice {
    fn from(value: RetryClass) -> Self {
        match value {
            RetryClass::Never => Self::None,
            RetryClass::SafeImmediate => Self::Immediate,
            RetryClass::SafeBackoff => Self::Backoff,
            RetryClass::ResyncRequired => Self::Resync,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WebSocketAction {
    None,
    RejectUpgrade { http_status: u16 },
    Close { code: u16 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TransportMapping {
    pub stable_code: StableCode,
    pub http_status: u16,
    pub grpc_code: u16,
    pub realtime_code: RealtimeErrorCode,
    pub websocket_action: WebSocketAction,
    pub retry: RetryAdvice,
    pub public_message: &'static str,
    pub expose_internal_reason: bool,
}

pub fn map_domain_error(
    error: DomainError,
    context: RealtimeContext,
    phase: TransportPhase,
) -> Result<TransportMapping, DomainError> {
    validate_context(error.code(), context)?;
    let http_status = http_status(error.code());
    Ok(TransportMapping {
        stable_code: error.code(),
        http_status,
        grpc_code: error.code() as u16,
        realtime_code: realtime_code(error.code(), context),
        websocket_action: websocket_action(error.code(), context, phase, http_status),
        retry: error.retry().into(),
        public_message: public_message(error.code(), context),
        expose_internal_reason: false,
    })
}

#[must_use]
pub const fn http_status(code: StableCode) -> u16 {
    match code {
        StableCode::InvalidArgument | StableCode::OutOfRange => 400,
        StableCode::Unauthenticated => 401,
        StableCode::PermissionDenied => 403,
        StableCode::NotFound => 404,
        StableCode::AlreadyExists | StableCode::Aborted => 409,
        StableCode::FailedPrecondition => 412,
        StableCode::ResourceExhausted => 429,
        StableCode::Unimplemented => 501,
        StableCode::Internal | StableCode::DataLoss => 500,
        StableCode::Unavailable => 503,
    }
}

#[must_use]
pub const fn grpc_code(code: StableCode) -> u16 {
    code as u16
}

const fn realtime_code(code: StableCode, context: RealtimeContext) -> RealtimeErrorCode {
    match context {
        RealtimeContext::UnrecognizedPayload => RealtimeErrorCode::UnrecognizedPayload,
        RealtimeContext::MissingPayload => RealtimeErrorCode::MissingPayload,
        RealtimeContext::MatchNotFound => RealtimeErrorCode::MatchNotFound,
        RealtimeContext::MatchJoinRejected => RealtimeErrorCode::MatchJoinRejected,
        RealtimeContext::RuntimeFunctionNotFound => {
            RealtimeErrorCode::RuntimeFunctionNotFound
        }
        RealtimeContext::RuntimeFunctionException => {
            RealtimeErrorCode::RuntimeFunctionException
        }
        RealtimeContext::Generic => match code {
            StableCode::Internal | StableCode::Unavailable | StableCode::DataLoss => {
                RealtimeErrorCode::RuntimeException
            }
            _ => RealtimeErrorCode::BadInput,
        },
    }
}

const fn websocket_action(
    code: StableCode,
    context: RealtimeContext,
    phase: TransportPhase,
    status: u16,
) -> WebSocketAction {
    match phase {
        TransportPhase::RequestResponse => WebSocketAction::None,
        TransportPhase::WebSocketHandshake => {
            WebSocketAction::RejectUpgrade { http_status: status }
        }
        TransportPhase::WebSocketEstablished => match context {
            RealtimeContext::UnrecognizedPayload | RealtimeContext::MissingPayload => {
                WebSocketAction::Close { code: 1002 }
            }
            _ => match code {
                StableCode::Unauthenticated | StableCode::PermissionDenied => {
                    WebSocketAction::Close { code: 1008 }
                }
                StableCode::ResourceExhausted | StableCode::Unavailable => {
                    WebSocketAction::Close { code: 1013 }
                }
                StableCode::Internal | StableCode::DataLoss => {
                    WebSocketAction::Close { code: 1011 }
                }
                _ => WebSocketAction::None,
            },
        },
    }
}

const fn public_message(code: StableCode, context: RealtimeContext) -> &'static str {
    match context {
        RealtimeContext::UnrecognizedPayload => "Unrecognized realtime payload.",
        RealtimeContext::MissingPayload => "Realtime payload is required.",
        RealtimeContext::MatchNotFound => "Match was not found.",
        RealtimeContext::MatchJoinRejected => "Match join was rejected.",
        RealtimeContext::RuntimeFunctionNotFound => "Runtime function was not found.",
        RealtimeContext::RuntimeFunctionException => "Runtime function failed.",
        RealtimeContext::Generic => match code {
            StableCode::InvalidArgument => "Request is invalid.",
            StableCode::NotFound => "Requested resource was not found.",
            StableCode::AlreadyExists => "Requested resource already exists.",
            StableCode::PermissionDenied => "Permission denied.",
            StableCode::ResourceExhausted => "Resource limit exceeded.",
            StableCode::FailedPrecondition => "Request precondition failed.",
            StableCode::Aborted => "Request was aborted.",
            StableCode::OutOfRange => "Request value is out of range.",
            StableCode::Unimplemented => "Operation is not implemented.",
            StableCode::Internal => "Internal server error.",
            StableCode::Unavailable => "Service is unavailable.",
            StableCode::DataLoss => "Internal data integrity error.",
            StableCode::Unauthenticated => "Authentication required.",
        },
    }
}

fn validate_context(code: StableCode, context: RealtimeContext) -> Result<(), DomainError> {
    let valid = match context {
        RealtimeContext::Generic => true,
        RealtimeContext::UnrecognizedPayload | RealtimeContext::MissingPayload => {
            code == StableCode::InvalidArgument
        }
        RealtimeContext::MatchNotFound => code == StableCode::NotFound,
        RealtimeContext::MatchJoinRejected => {
            matches!(code, StableCode::PermissionDenied | StableCode::FailedPrecondition)
        }
        RealtimeContext::RuntimeFunctionNotFound => {
            matches!(code, StableCode::NotFound | StableCode::Unimplemented)
        }
        RealtimeContext::RuntimeFunctionException => code == StableCode::Internal,
    };
    if valid {
        Ok(())
    } else {
        Err(DomainError::new(
            StableCode::InvalidArgument,
            "transport_context_mismatch",
            RetryClass::Never,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn error(code: StableCode, retry: RetryClass) -> DomainError {
        DomainError::new(code, "private_reason_must_not_escape", retry)
    }

    #[test]
    fn grpc_codes_preserve_canonical_stable_numbers() {
        assert_eq!(grpc_code(StableCode::InvalidArgument), 3);
        assert_eq!(grpc_code(StableCode::Unauthenticated), 16);
        assert_eq!(grpc_code(StableCode::DataLoss), 15);
    }

    #[test]
    fn http_statuses_are_exhaustive_and_stable() {
        let cases = [
            (StableCode::InvalidArgument, 400),
            (StableCode::NotFound, 404),
            (StableCode::AlreadyExists, 409),
            (StableCode::PermissionDenied, 403),
            (StableCode::ResourceExhausted, 429),
            (StableCode::FailedPrecondition, 412),
            (StableCode::Aborted, 409),
            (StableCode::OutOfRange, 400),
            (StableCode::Unimplemented, 501),
            (StableCode::Internal, 500),
            (StableCode::Unavailable, 503),
            (StableCode::DataLoss, 500),
            (StableCode::Unauthenticated, 401),
        ];
        for (code, expected) in cases {
            assert_eq!(http_status(code), expected);
        }
    }

    #[test]
    fn rtapi_context_codes_match_pinned_enum_values() {
        let cases = [
            (RealtimeContext::UnrecognizedPayload, RealtimeErrorCode::UnrecognizedPayload),
            (RealtimeContext::MissingPayload, RealtimeErrorCode::MissingPayload),
            (RealtimeContext::MatchNotFound, RealtimeErrorCode::MatchNotFound),
            (RealtimeContext::MatchJoinRejected, RealtimeErrorCode::MatchJoinRejected),
            (RealtimeContext::RuntimeFunctionNotFound, RealtimeErrorCode::RuntimeFunctionNotFound),
            (RealtimeContext::RuntimeFunctionException, RealtimeErrorCode::RuntimeFunctionException),
        ];
        for (context, expected) in cases {
            let code = match context {
                RealtimeContext::UnrecognizedPayload | RealtimeContext::MissingPayload => StableCode::InvalidArgument,
                RealtimeContext::MatchNotFound | RealtimeContext::RuntimeFunctionNotFound => StableCode::NotFound,
                RealtimeContext::MatchJoinRejected => StableCode::PermissionDenied,
                RealtimeContext::RuntimeFunctionException => StableCode::Internal,
                RealtimeContext::Generic => unreachable!(),
            };
            let mapping = map_domain_error(
                error(code, RetryClass::Never),
                context,
                TransportPhase::WebSocketEstablished,
            )
            .unwrap();
            assert_eq!(mapping.realtime_code, expected);
            assert_eq!(mapping.realtime_code as i32, expected as i32);
        }
    }

    #[test]
    fn internal_reason_never_reaches_public_mapping() {
        let mapping = map_domain_error(
            error(StableCode::Internal, RetryClass::Never),
            RealtimeContext::Generic,
            TransportPhase::RequestResponse,
        )
        .unwrap();
        assert_eq!(mapping.public_message, "Internal server error.");
        assert!(!mapping.expose_internal_reason);
        assert!(!mapping.public_message.contains("private_reason"));
    }

    #[test]
    fn handshake_rejects_with_http_status_before_upgrade() {
        let mapping = map_domain_error(
            error(StableCode::Unauthenticated, RetryClass::Never),
            RealtimeContext::Generic,
            TransportPhase::WebSocketHandshake,
        )
        .unwrap();
        assert_eq!(mapping.websocket_action, WebSocketAction::RejectUpgrade { http_status: 401 });
    }

    #[test]
    fn established_socket_close_policy_is_typed() {
        let auth = map_domain_error(
            error(StableCode::PermissionDenied, RetryClass::Never),
            RealtimeContext::Generic,
            TransportPhase::WebSocketEstablished,
        )
        .unwrap();
        assert_eq!(auth.websocket_action, WebSocketAction::Close { code: 1008 });

        let pressure = map_domain_error(
            error(StableCode::ResourceExhausted, RetryClass::SafeBackoff),
            RealtimeContext::Generic,
            TransportPhase::WebSocketEstablished,
        )
        .unwrap();
        assert_eq!(pressure.websocket_action, WebSocketAction::Close { code: 1013 });
        assert_eq!(pressure.retry, RetryAdvice::Backoff);
    }

    #[test]
    fn malformed_envelopes_use_protocol_close() {
        let mapping = map_domain_error(
            error(StableCode::InvalidArgument, RetryClass::Never),
            RealtimeContext::MissingPayload,
            TransportPhase::WebSocketEstablished,
        )
        .unwrap();
        assert_eq!(mapping.realtime_code as i32, 2);
        assert_eq!(mapping.websocket_action, WebSocketAction::Close { code: 1002 });
    }

    #[test]
    fn mismatched_context_fails_closed() {
        assert_eq!(
            map_domain_error(
                error(StableCode::Internal, RetryClass::Never),
                RealtimeContext::MatchNotFound,
                TransportPhase::RequestResponse,
            )
            .unwrap_err()
            .reason(),
            "transport_context_mismatch"
        );
    }

    #[test]
    fn retry_classes_map_without_transport_guessing() {
        assert_eq!(RetryAdvice::from(RetryClass::Never), RetryAdvice::None);
        assert_eq!(RetryAdvice::from(RetryClass::SafeImmediate), RetryAdvice::Immediate);
        assert_eq!(RetryAdvice::from(RetryClass::SafeBackoff), RetryAdvice::Backoff);
        assert_eq!(RetryAdvice::from(RetryClass::ResyncRequired), RetryAdvice::Resync);
    }
}
