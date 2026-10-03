//! Pure, owned configuration for one explicitly selected Nakama legacy authority.
//! These finite local resource policies are not upstream configuration parity.
//! Construction validates keys and TTL policy without constructing a service,
//! cache, sweeper, database pool, listener or worker.

use core::fmt;

use trnm_session_core::{NakamaLegacyBlacklistLimits, NakamaLegacyBlacklistPolicy};
use trnm_token_crypto_provider::NakamaLegacyKeyLimits;
use trnm_token_jwt_adapter::{
    NakamaLegacyDecodeLimits, NakamaLegacyIssueLimits, NakamaLegacyVerifyLimits,
};

use super::error::ServerError;
use super::legacy_auth::{LegacyAuthConfig, LegacyAuthPolicy, LegacyAuthPolicyConfig};
use super::legacy_http_api::LegacyHttpServerKey;

const KEY_BYTES: usize = 4096;
const PAYLOAD_BYTES: usize = 16 * 1024;
const TOKEN_BYTES: usize = 32 * 1024;

/// Owns three fixed-purpose keys. No defaults, registry, epoch or key fallback.
#[derive(Clone)]
pub struct LegacyServerAuthConfig {
    server_key: Vec<u8>,
    access_key: Vec<u8>,
    refresh_key: Vec<u8>,
    policy: LegacyAuthPolicy,
}

impl LegacyServerAuthConfig {
    pub fn new(
        server_key: Vec<u8>,
        access_key: Vec<u8>,
        refresh_key: Vec<u8>,
        session_ttl_seconds: i64,
        refresh_ttl_seconds: i64,
        single_session: bool,
    ) -> Result<Self, ServerError> {
        // Key values remain exact UTF-8 environment bytes; no trim, hex or
        // durable 32-byte minimum is imposed on this deliberately selected mode.
        if server_key.is_empty() || server_key.len() > KEY_BYTES {
            return Err(ServerError::Configuration("legacy_server_key_invalid"));
        }
        if access_key.is_empty()
            || refresh_key.is_empty()
            || access_key.len() > KEY_BYTES
            || refresh_key.len() > KEY_BYTES
        {
            return Err(ServerError::Configuration(
                "legacy_access_refresh_keys_invalid",
            ));
        }
        fn invalid<E>(_: E) -> ServerError {
            ServerError::Configuration("legacy_auth_policy_invalid")
        }
        let header_decode = NakamaLegacyDecodeLimits::new(4096, 8, 32, 8192).map_err(invalid)?;
        let claims_decode =
            NakamaLegacyDecodeLimits::new(PAYLOAD_BYTES, 32, 1024, 32 * 1024).map_err(invalid)?;
        let policy = LegacyAuthPolicy::new(LegacyAuthPolicyConfig {
            session_ttl_seconds,
            refresh_ttl_seconds,
            single_session,
            issuer_limits: NakamaLegacyIssueLimits::new(PAYLOAD_BYTES, TOKEN_BYTES)
                .map_err(invalid)?,
            verifier_limits: NakamaLegacyVerifyLimits::new(
                TOKEN_BYTES,
                header_decode,
                claims_decode,
            )
            .map_err(invalid)?,
            blacklist_limits: NakamaLegacyBlacklistLimits::new(NakamaLegacyBlacklistPolicy {
                max_users: 10_000,
                max_tokens_per_user: 4096,
                max_total_tokens: 65_536,
                max_token_id_bytes: 4096,
                max_total_token_id_bytes: 16 * 1024 * 1024,
                max_mutation_items: 10_000,
                max_prepared_token_id_bytes: 16 * 1024 * 1024,
            })
            .map_err(invalid)?,
        })
        .map_err(|_| ServerError::Configuration("legacy_auth_ttl_policy_invalid"))?;
        let config = Self {
            server_key,
            access_key,
            refresh_key,
            policy,
        };
        // Both constructors are pure. In particular this never calls
        // LegacyAuthService::from_config, which owns SweepWorker startup.
        config.server_key()?;
        config.service_config()?;
        Ok(config)
    }

    pub fn server_key(&self) -> Result<LegacyHttpServerKey, ServerError> {
        LegacyHttpServerKey::new(self.server_key.clone())
            .map_err(|_| ServerError::Configuration("legacy_server_key_invalid"))
    }

    /// Pure materialization only; the selected runtime installs the service later.
    pub fn service_config(&self) -> Result<LegacyAuthConfig, ServerError> {
        LegacyAuthConfig::new(
            self.access_key.clone(),
            self.refresh_key.clone(),
            NakamaLegacyKeyLimits::new(KEY_BYTES)
                .map_err(|_| ServerError::Configuration("legacy_auth_key_policy_invalid"))?,
            self.policy,
        )
        .map_err(|_| ServerError::Configuration("legacy_access_refresh_keys_invalid"))
    }
}

impl fmt::Debug for LegacyServerAuthConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LegacyServerAuthConfig")
            .field("server_key", &"<redacted>")
            .field("access_key", &"<redacted>")
            .field("refresh_key", &"<redacted>")
            .field("policy", &self.policy)
            .finish()
    }
}
