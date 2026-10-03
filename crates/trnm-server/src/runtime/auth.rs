pub(crate) use trnm_persistence_pg::{
    parse_refresh_credential, AccessTokenVerifier, SessionPrincipal,
};

use super::config::AuthAuthorityConfig;
use super::error::ServerError;

/// Pure closed AccountsV5 admission before repository setup, bind or workers.
/// Once admitted, serve installs exactly one selected runtime; no fallback.
pub(crate) fn require_installed_http_authority(
    authority: &AuthAuthorityConfig,
) -> Result<(), ServerError> {
    match authority {
        AuthAuthorityConfig::NakamaLegacy(_) => {
            trnm_persistence_pg::AuthoritativeSchemaTarget::NakamaAccountsV5
                .require_capture_ready()?;
            Ok(())
        }
        AuthAuthorityConfig::Disabled | AuthAuthorityConfig::DurableFamily(_) => Ok(()),
    }
}
