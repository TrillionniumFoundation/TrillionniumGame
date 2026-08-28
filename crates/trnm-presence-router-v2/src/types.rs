use core::fmt;

pub const MAX_CONNECTION_ID_BYTES: usize = 128;
pub const MAX_NODE_ID_BYTES: usize = 128;
pub const MAX_USER_ID_BYTES: usize = 128;
pub const MAX_SESSION_ID_BYTES: usize = 128;
pub const MAX_USERNAME_BYTES: usize = 256;
pub const MAX_STATUS_BYTES: usize = 512;
pub const MAX_STREAM_LABEL_BYTES: usize = 256;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ValidationError {
    Empty {
        field: &'static str,
    },
    TooLong {
        field: &'static str,
        limit: usize,
        actual: usize,
    },
    ControlCharacter {
        field: &'static str,
    },
    ZeroGeneration,
    InvalidStreamMode,
}

impl fmt::Display for ValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty { field } => write!(formatter, "{field} must not be empty"),
            Self::TooLong {
                field,
                limit,
                actual,
            } => write!(
                formatter,
                "{field} length {actual} exceeds the {limit}-byte limit"
            ),
            Self::ControlCharacter { field } => {
                write!(formatter, "{field} contains a forbidden control character")
            }
            Self::ZeroGeneration => formatter.write_str("connection generation must be positive"),
            Self::InvalidStreamMode => formatter.write_str("stream mode must be positive"),
        }
    }
}

impl std::error::Error for ValidationError {}

fn validate_text(
    field: &'static str,
    value: &str,
    limit: usize,
    allow_empty: bool,
) -> Result<(), ValidationError> {
    if !allow_empty && value.is_empty() {
        return Err(ValidationError::Empty { field });
    }
    if value.len() > limit {
        return Err(ValidationError::TooLong {
            field,
            limit,
            actual: value.len(),
        });
    }
    if value.chars().any(|character| character.is_control()) {
        return Err(ValidationError::ControlCharacter { field });
    }
    Ok(())
}

macro_rules! bounded_identifier {
    ($name:ident, $field:literal, $limit:expr) => {
        #[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Result<Self, ValidationError> {
                let value = value.into();
                validate_text($field, &value, $limit, false)?;
                Ok(Self(value))
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(&self.0)
            }
        }
    };
}

bounded_identifier!(ConnectionId, "connection_id", MAX_CONNECTION_ID_BYTES);
bounded_identifier!(NodeId, "node_id", MAX_NODE_ID_BYTES);
bounded_identifier!(UserId, "user_id", MAX_USER_ID_BYTES);
bounded_identifier!(SessionId, "session_id", MAX_SESSION_ID_BYTES);
bounded_identifier!(Username, "username", MAX_USERNAME_BYTES);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ConnectionGeneration(u64);

impl ConnectionGeneration {
    pub fn new(value: u64) -> Result<Self, ValidationError> {
        if value == 0 {
            return Err(ValidationError::ZeroGeneration);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

impl fmt::Display for ConnectionGeneration {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct PresenceStatus(String);

impl PresenceStatus {
    pub fn new(value: impl Into<String>) -> Result<Self, ValidationError> {
        let value = value.into();
        validate_text("status", &value, MAX_STATUS_BYTES, true)?;
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for PresenceStatus {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct StreamKey {
    mode: u8,
    subject: [u8; 16],
    subcontext: [u8; 16],
    label: String,
}

impl StreamKey {
    pub fn new(
        mode: u8,
        subject: [u8; 16],
        subcontext: [u8; 16],
        label: impl Into<String>,
    ) -> Result<Self, ValidationError> {
        if mode == 0 {
            return Err(ValidationError::InvalidStreamMode);
        }
        let label = label.into();
        validate_text("stream.label", &label, MAX_STREAM_LABEL_BYTES, true)?;
        Ok(Self {
            mode,
            subject,
            subcontext,
            label,
        })
    }

    pub const fn mode(&self) -> u8 {
        self.mode
    }

    pub const fn subject(&self) -> &[u8; 16] {
        &self.subject
    }

    pub const fn subcontext(&self) -> &[u8; 16] {
        &self.subcontext
    }

    pub fn label(&self) -> &str {
        &self.label
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct PresenceIdentity {
    pub user_id: UserId,
    pub session_id: SessionId,
    pub username: Username,
}

impl PresenceIdentity {
    pub const fn new(user_id: UserId, session_id: SessionId, username: Username) -> Self {
        Self {
            user_id,
            session_id,
            username,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ConnectionRef {
    pub node_id: NodeId,
    pub connection_id: ConnectionId,
}

impl ConnectionRef {
    pub const fn new(node_id: NodeId, connection_id: ConnectionId) -> Self {
        Self {
            node_id,
            connection_id,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JoinPresenceRequest {
    pub connection: ConnectionRef,
    pub generation: ConnectionGeneration,
    pub stream: StreamKey,
    pub identity: PresenceIdentity,
    pub status: PresenceStatus,
    pub hidden: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdatePresenceRequest {
    pub connection: ConnectionRef,
    pub generation: ConnectionGeneration,
    pub stream: StreamKey,
    pub status: PresenceStatus,
    pub hidden: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LeavePresenceRequest {
    pub connection: ConnectionRef,
    pub generation: ConnectionGeneration,
    pub stream: StreamKey,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RemoveConnectionRequest {
    pub connection: ConnectionRef,
    pub generation: ConnectionGeneration,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SnapshotVisibility {
    PublicOnly,
    IncludeHidden,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PresenceRecord {
    pub connection: ConnectionRef,
    pub generation: ConnectionGeneration,
    pub stream: StreamKey,
    pub identity: PresenceIdentity,
    pub status: PresenceStatus,
    pub hidden: bool,
}
