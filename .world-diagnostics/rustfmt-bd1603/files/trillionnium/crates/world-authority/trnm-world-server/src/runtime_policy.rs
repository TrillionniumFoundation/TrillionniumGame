use std::error::Error;
use std::fmt;
use std::net::SocketAddr;

pub const FIXTURE_RUNTIME_POLICY_CONTRACT: &str = "trillionnium_world_fixture_runtime_policy_v1";
pub const FIXTURE_RUNTIME_CLASSIFICATION: &str = "development_fixture_only";
pub const FIXTURE_RUNTIME_OPT_IN_FLAG: &str = "--allow-fixture-runtime";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FixtureRuntimePolicyError {
    ExplicitOptInRequired,
    EmptyOrNonCanonicalBind,
    SocketLiteralRequired,
    EphemeralPortForbidden,
    NonLoopbackBind(SocketAddr),
}

impl fmt::Display for FixtureRuntimePolicyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ExplicitOptInRequired => write!(
                formatter,
                "fixture runtime is disabled unless {FIXTURE_RUNTIME_OPT_IN_FLAG} is supplied"
            ),
            Self::EmptyOrNonCanonicalBind => {
                write!(formatter, "fixture bind address must be non-empty and trimmed")
            }
            Self::SocketLiteralRequired => write!(
                formatter,
                "fixture bind must be an explicit IP socket literal such as 127.0.0.1:8787 or [::1]:8787"
            ),
            Self::EphemeralPortForbidden => {
                write!(formatter, "fixture bind port 0 is forbidden; select an explicit port")
            }
            Self::NonLoopbackBind(address) => write!(
                formatter,
                "fixture runtime may bind only to a loopback address; rejected {address}"
            ),
        }
    }
}

impl Error for FixtureRuntimePolicyError {}

pub fn validate_fixture_runtime_bind(
    explicit_opt_in: bool,
    bind: &str,
) -> Result<SocketAddr, FixtureRuntimePolicyError> {
    if !explicit_opt_in {
        return Err(FixtureRuntimePolicyError::ExplicitOptInRequired);
    }
    if bind.is_empty() || bind.trim() != bind {
        return Err(FixtureRuntimePolicyError::EmptyOrNonCanonicalBind);
    }
    let address = bind
        .parse::<SocketAddr>()
        .map_err(|_| FixtureRuntimePolicyError::SocketLiteralRequired)?;
    if address.port() == 0 {
        return Err(FixtureRuntimePolicyError::EphemeralPortForbidden);
    }
    if !address.ip().is_loopback() {
        return Err(FixtureRuntimePolicyError::NonLoopbackBind(address));
    }
    Ok(address)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_opt_in_is_mandatory() {
        assert_eq!(
            validate_fixture_runtime_bind(false, "127.0.0.1:8787"),
            Err(FixtureRuntimePolicyError::ExplicitOptInRequired)
        );
    }

    #[test]
    fn ipv4_and_ipv6_loopback_literals_are_allowed() {
        assert_eq!(
            validate_fixture_runtime_bind(true, "127.0.0.1:8787")
                .unwrap()
                .to_string(),
            "127.0.0.1:8787"
        );
        assert_eq!(
            validate_fixture_runtime_bind(true, "[::1]:8787")
                .unwrap()
                .to_string(),
            "[::1]:8787"
        );
    }

    #[test]
    fn wildcard_and_public_addresses_are_rejected() {
        assert!(matches!(
            validate_fixture_runtime_bind(true, "0.0.0.0:8787"),
            Err(FixtureRuntimePolicyError::NonLoopbackBind(_))
        ));
        assert!(matches!(
            validate_fixture_runtime_bind(true, "198.51.100.10:8787"),
            Err(FixtureRuntimePolicyError::NonLoopbackBind(_))
        ));
        assert!(matches!(
            validate_fixture_runtime_bind(true, "[::]:8787"),
            Err(FixtureRuntimePolicyError::NonLoopbackBind(_))
        ));
    }

    #[test]
    fn hostname_whitespace_and_ephemeral_port_are_rejected() {
        assert_eq!(
            validate_fixture_runtime_bind(true, "localhost:8787"),
            Err(FixtureRuntimePolicyError::SocketLiteralRequired)
        );
        assert_eq!(
            validate_fixture_runtime_bind(true, " 127.0.0.1:8787"),
            Err(FixtureRuntimePolicyError::EmptyOrNonCanonicalBind)
        );
        assert_eq!(
            validate_fixture_runtime_bind(true, "127.0.0.1:0"),
            Err(FixtureRuntimePolicyError::EphemeralPortForbidden)
        );
    }
}
