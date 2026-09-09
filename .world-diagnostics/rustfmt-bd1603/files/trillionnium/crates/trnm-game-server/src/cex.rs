use crate::signer_protocol::{
    EntitlementIssuerKeyStatusRequest, EntitlementIssuerKeyStatusResponse, EntitlementSignRequest,
    EntitlementSignResponse, EntitlementSignerAttestationRequest,
    EntitlementSignerAttestationResponse, EntitlementSignerReadiness, ENTITLEMENT_SIGNER_CONTRACT,
    ENTITLEMENT_SIGNER_ISSUER, ENTITLEMENT_SIGNER_RECEIPT_PATH, SIGNER_AUTH_HEADER,
};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use chrono::{DateTime, Datelike, Utc};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use reqwest::{header::HeaderMap, StatusCode};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{fmt, net::IpAddr, sync::Arc, time::Duration};
use trnm_campaign_core::EconomyBackend;
use trnm_economy_protocol::{
    EconomicIntent, EconomicIntentKind, EconomicReceipt, EconomyAccountBinding,
    ServerSignedValueEntitlementV2, ValueEntitlementSource, WalletSnapshot,
    SERVER_SIGNED_VALUE_ENTITLEMENT_METADATA_KEY, SERVER_SIGNED_VALUE_ENTITLEMENT_V2_CONTRACT,
};

const PLAYER_SESSION_HEADER: &str = "x-trnm-player-session";
const GAME_AUTHORITY_HEADER: &str = "x-trnm-game-authority";
const INTENT_HASH_HEADER: &str = "x-trnm-intent-sha256";
const CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT: &str = "trnm_cex_settlement_receipt_lookup_v1";
const CEX_SETTLEMENT_RECEIPT_LOOKUP_PATH: &str = "/v1/trnm/economy/receipts/by-intent";
const SETTLEMENT_OUTBOX_REQUIRED: &str =
    "external economy settlement is owned by trnm-settlement-worker; synchronous EconomyBackend I/O is prohibited";
const MAX_REMOTE_ERROR_BODY_BYTES: usize = 64 * 1024;
// Local response-acceptance policy; never truncate a possibly committed receipt.
const MAX_REMOTE_SUCCESS_BODY_BYTES: usize = 2 * 1024 * 1024;

fn append_remote_chunk(body: &mut Vec<u8>, chunk: &[u8]) -> Result<(), &'static str> {
    let next = body
        .len()
        .checked_add(chunk.len())
        .ok_or("remote_success_body_too_large")?;
    if next > MAX_REMOTE_SUCCESS_BODY_BYTES {
        return Err("remote_success_body_too_large");
    }
    body.try_reserve(chunk.len())
        .map_err(|_| "remote_success_body_allocation_failed")?;
    body.extend_from_slice(chunk);
    Ok(())
}

/// Bound bytes actually read, including chunked/unknown-length responses.
/// Errors are static: malformed peer data must not be reflected in diagnostics.
/// A failed read after a possible remote commit remains ambiguous to callers.
async fn bounded_remote_json<T: DeserializeOwned>(
    mut response: reqwest::Response,
) -> Result<T, &'static str> {
    if response
        .content_length()
        .is_some_and(|length| length > MAX_REMOTE_SUCCESS_BODY_BYTES as u64)
    {
        return Err("remote_success_body_too_large");
    }
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| "remote_success_body_read_failed")?
    {
        append_remote_chunk(&mut body, &chunk)?;
    }
    serde_json::from_slice(&body).map_err(|_| "remote_success_json_invalid")
}

#[derive(Debug, Clone, Serialize)]
struct SessionVerifyRequest<'a> {
    player_id: &'a str,
    account_id: &'a str,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SessionVerifyResponse {
    pub verified: bool,
    pub session_id: String,
    pub player_id: String,
    pub account_id: String,
    pub device_id: String,
    pub recovery_generation: i64,
    pub expires_at_epoch: i64,
}

#[derive(Debug, Clone)]
pub struct AuthorizedSettlementIntent {
    pub intent: EconomicIntent,
    pub authorization_request_id: String,
    pub signer_receipt_hash: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CexSettlementReceiptLookupResponse {
    contract_version: String,
    intent_id: String,
    intent_hash: String,
    receipt: EconomicReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExternalSettlementError {
    Retryable(String),
    Permanent(String),
}

impl ExternalSettlementError {
    pub fn message(&self) -> &str {
        match self {
            Self::Retryable(message) | Self::Permanent(message) => message,
        }
    }

    fn transport(context: &str, error: reqwest::Error) -> Self {
        Self::Retryable(format!("{context}: {error}"))
    }

    fn status(context: &str, status: StatusCode, body: String) -> Self {
        let message = format!("{context} ({status}): {body}");
        if retryable_status(status) {
            Self::Retryable(message)
        } else {
            Self::Permanent(message)
        }
    }
}

impl fmt::Display for ExternalSettlementError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.message())
    }
}

impl std::error::Error for ExternalSettlementError {}

fn retryable_status(status: StatusCode) -> bool {
    status == StatusCode::REQUEST_TIMEOUT
        || status == StatusCode::TOO_MANY_REQUESTS
        || status == StatusCode::CONFLICT
        || status.as_u16() == 425
        || status.is_server_error()
        || status.is_redirection()
}

async fn bounded_error_body(mut response: reqwest::Response) -> String {
    if response
        .content_length()
        .is_some_and(|length| length > MAX_REMOTE_ERROR_BODY_BYTES as u64)
    {
        return format!(
            "remote error body omitted: content-length exceeds {} bytes",
            MAX_REMOTE_ERROR_BODY_BYTES
        );
    }

    let mut body = Vec::with_capacity(
        response
            .content_length()
            .unwrap_or_default()
            .min(MAX_REMOTE_ERROR_BODY_BYTES as u64) as usize,
    );
    loop {
        match response.chunk().await {
            Ok(Some(chunk)) => {
                if chunk.is_empty() {
                    continue;
                }
                let remaining = MAX_REMOTE_ERROR_BODY_BYTES.saturating_sub(body.len());
                if chunk.len() > remaining {
                    body.extend_from_slice(&chunk[..remaining]);
                    return format!(
                        "{} [truncated at {} bytes]",
                        String::from_utf8_lossy(&body),
                        MAX_REMOTE_ERROR_BODY_BYTES
                    );
                }
                body.extend_from_slice(&chunk);
            }
            Ok(None) => return String::from_utf8_lossy(&body).into_owned(),
            Err(error) if body.is_empty() => {
                return format!("remote error body unavailable: {error}")
            }
            Err(error) => {
                return format!(
                    "{} [remote error body interrupted: {error}]",
                    String::from_utf8_lossy(&body)
                )
            }
        }
    }
}

fn normalize_service_base_url(raw: &str, variable: &str) -> Result<String, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err(format!("{variable} is required"));
    }
    let mut url = reqwest::Url::parse(raw)
        .map_err(|error| format!("{variable} must be an absolute HTTP(S) URL: {error}"))?;
    if url.cannot_be_a_base() || url.host_str().is_none() {
        return Err(format!("{variable} must contain a network host"));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(format!("{variable} must not contain URL credentials"));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(format!("{variable} must not contain a query or fragment"));
    }
    if !matches!(url.path(), "" | "/") {
        return Err(format!("{variable} must not contain a path prefix"));
    }

    match url.scheme() {
        "https" => {}
        "http" => {
            let host = url.host_str().unwrap_or_default();
            let loopback = host.eq_ignore_ascii_case("localhost")
                || host
                    .parse::<IpAddr>()
                    .is_ok_and(|address| address.is_loopback());
            if !loopback {
                return Err(format!(
                    "{variable} may use plaintext HTTP only for localhost or a loopback IP"
                ));
            }
        }
        _ => {
            return Err(format!(
                "{variable} must use HTTPS, or HTTP on loopback only"
            ))
        }
    }

    url.set_path("");
    Ok(url.as_str().trim_end_matches('/').to_string())
}

fn canonical_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn serialized_intent_hash(intent: &EconomicIntent) -> Result<String, ExternalSettlementError> {
    let encoded = serde_json::to_vec(intent).map_err(|error| {
        ExternalSettlementError::Permanent(format!("encode settlement intent for hashing: {error}"))
    })?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

fn stable_entitlement_id(request_id: &str) -> String {
    let digest = format!("{:x}", Sha256::digest(request_id.as_bytes()));
    format!("trnm-online-entitlement:{}", &digest[..32])
}

fn validate_signer_response(
    mut entitlement: ServerSignedValueEntitlementV2,
    authorization_request_id: &str,
    signed: EntitlementSignResponse,
) -> Result<(ServerSignedValueEntitlementV2, String), ExternalSettlementError> {
    if signed.contract_version != ENTITLEMENT_SIGNER_CONTRACT
        || signed.request_id != authorization_request_id
        || signed.issuer != ENTITLEMENT_SIGNER_ISSUER
        || signed.key_id.is_empty()
        || signed.signature.is_empty()
        || !canonical_sha256(&signed.request_hash)
        || !canonical_sha256(&signed.signing_receipt_hash)
    {
        return Err(ExternalSettlementError::Permanent(
            "isolated signer response failed durable binding validation".to_string(),
        ));
    }
    entitlement.key_id = signed.key_id.clone();
    entitlement.signature = signed.signature.clone();
    entitlement
        .validate_shape()
        .map_err(ExternalSettlementError::Permanent)?;
    let payload = entitlement
        .signing_payload()
        .map_err(ExternalSettlementError::Permanent)?;
    let request_hash = format!("{:x}", Sha256::digest(&payload));
    if request_hash != signed.request_hash {
        return Err(ExternalSettlementError::Permanent(
            "isolated signer response request hash mismatch".to_string(),
        ));
    }
    let signing_receipt_hash = format!(
        "{:x}",
        Sha256::digest(
            format!(
                "{}:{}:{}",
                signed.request_hash, signed.key_id, signed.signature
            )
            .as_bytes()
        )
    );
    if signing_receipt_hash != signed.signing_receipt_hash {
        return Err(ExternalSettlementError::Permanent(
            "isolated signer response receipt hash mismatch".to_string(),
        ));
    }
    Ok((entitlement, signed.signing_receipt_hash))
}

#[derive(Clone)]
pub struct CexClient {
    base_url: Arc<String>,
    game_authority_token: Arc<String>,
    signer_url: Arc<String>,
    signer_token: Arc<String>,
    async_client: reqwest::Client,
}

impl CexClient {
    pub fn new(
        base_url: String,
        game_authority_token: String,
        signer_url: String,
        signer_token: String,
    ) -> Result<Self, String> {
        let base_url = normalize_service_base_url(&base_url, "TRNM_CEX_LEDGER_URL")?;
        let signer_url = normalize_service_base_url(&signer_url, "TRNM_ENTITLEMENT_SIGNER_URL")?;
        if game_authority_token.len() < 24 {
            return Err("TRNM_GAME_AUTHORITY_TOKEN must be at least 24 characters".to_string());
        }
        if signer_token.len() < 32 {
            return Err("TRNM_ENTITLEMENT_SIGNER_TOKEN must be at least 32 characters".to_string());
        }
        reqwest::header::HeaderValue::from_str(&game_authority_token).map_err(|_| {
            "TRNM_GAME_AUTHORITY_TOKEN must be a valid HTTP header value".to_string()
        })?;
        reqwest::header::HeaderValue::from_str(&signer_token).map_err(|_| {
            "TRNM_ENTITLEMENT_SIGNER_TOKEN must be a valid HTTP header value".to_string()
        })?;
        let async_client = reqwest::Client::builder()
            // Custom credential headers must never be forwarded to a redirect target.
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|error| format!("build asynchronous CEX/signer client: {error}"))?;
        Ok(Self {
            base_url: Arc::new(base_url),
            game_authority_token: Arc::new(game_authority_token),
            signer_url: Arc::new(signer_url),
            signer_token: Arc::new(signer_token),
            async_client,
        })
    }

    pub async fn readiness(&self) -> Result<(), String> {
        let response = self
            .async_client
            .get(format!("{}/v1/trnm/economy/readiness", self.base_url))
            .send()
            .await
            .map_err(|error| format!("CEX readiness transport: {error}"))?;
        if !response.status().is_success() {
            return Err(format!("CEX readiness returned {}", response.status()));
        }
        self.signer_attestation().await.map(|_| ())
    }

    pub async fn signer_readiness(&self) -> Result<EntitlementSignerReadiness, String> {
        let response = self
            .async_client
            .get(format!("{}/v1/signer/readiness", self.signer_url))
            .send()
            .await
            .map_err(|error| format!("isolated signer readiness transport: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "isolated signer readiness returned {}",
                response.status()
            ));
        }
        let readiness = bounded_remote_json::<EntitlementSignerReadiness>(response)
            .await
            .map_err(|error| format!("decode isolated signer readiness: {error}"))?;
        if readiness.status != "ok"
            || readiness.contract_version != ENTITLEMENT_SIGNER_CONTRACT
            || readiness.private_key_exported_to_game_server
            || !readiness.database_pool_saturation_healthy
        {
            return Err("isolated signer readiness failed custody contract".to_string());
        }
        Ok(readiness)
    }

    pub async fn signer_attestation(&self) -> Result<EntitlementSignerAttestationResponse, String> {
        let challenge = format!("trnm-signer-registry-check:{}", uuid::Uuid::new_v4());
        let response = self
            .async_client
            .post(format!("{}/v1/signer/attest", self.signer_url))
            .header(SIGNER_AUTH_HEADER, self.signer_token.as_str())
            .json(&EntitlementSignerAttestationRequest {
                contract_version: ENTITLEMENT_SIGNER_CONTRACT.to_string(),
                challenge: challenge.clone(),
            })
            .send()
            .await
            .map_err(|error| format!("isolated signer attestation transport: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "isolated signer attestation returned {}",
                response.status()
            ));
        }
        let attestation = bounded_remote_json::<EntitlementSignerAttestationResponse>(response)
            .await
            .map_err(|error| format!("decode isolated signer attestation: {error}"))?;
        let now = Utc::now().timestamp();
        if attestation.contract_version != ENTITLEMENT_SIGNER_CONTRACT
            || attestation.challenge != challenge
            || attestation.issuer != ENTITLEMENT_SIGNER_ISSUER
            || attestation.observed_at_epoch > now.saturating_add(5)
            || attestation.observed_at_epoch < now.saturating_sub(15)
            || attestation.expires_at_epoch <= now
            || attestation.expires_at_epoch > attestation.observed_at_epoch.saturating_add(30)
        {
            return Err("isolated signer attestation binding is invalid".to_string());
        }
        let public_key = STANDARD
            .decode(&attestation.public_key_base64)
            .map_err(|error| format!("decode signer attestation public key: {error}"))?;
        let public_key: [u8; 32] = public_key
            .try_into()
            .map_err(|_| "signer attestation public key must contain 32 bytes".to_string())?;
        if format!("{:x}", Sha256::digest(public_key)) != attestation.public_key_sha256 {
            return Err("signer attestation public-key fingerprint mismatch".to_string());
        }
        let signature = STANDARD
            .decode(&attestation.signature)
            .map_err(|error| format!("decode signer attestation signature: {error}"))?;
        let signature = Signature::from_slice(&signature)
            .map_err(|error| format!("decode signer Ed25519 signature: {error}"))?;
        let verifying_key = VerifyingKey::from_bytes(&public_key)
            .map_err(|error| format!("decode signer Ed25519 public key: {error}"))?;
        let payload = attestation.signing_payload()?;
        verifying_key
            .verify(&payload, &signature)
            .map_err(|_| "signer key-possession attestation signature failed".to_string())?;

        let registry = self
            .async_client
            .post(format!(
                "{}/v1/trnm/economy/issuer-keys/status",
                self.base_url
            ))
            .header(GAME_AUTHORITY_HEADER, self.game_authority_token.as_str())
            .json(&EntitlementIssuerKeyStatusRequest {
                key_id: attestation.key_id.clone(),
            })
            .send()
            .await
            .map_err(|error| format!("CEX issuer registry status transport: {error}"))?;
        if !registry.status().is_success() {
            return Err(format!(
                "CEX issuer registry rejected signer key ({})",
                registry.status()
            ));
        }
        let registry = bounded_remote_json::<EntitlementIssuerKeyStatusResponse>(registry)
            .await
            .map_err(|error| format!("decode CEX issuer registry status: {error}"))?;
        if registry.key_id != attestation.key_id
            || registry.issuer != attestation.issuer
            || registry.status != "active"
            || registry.signature_algorithm != "ed25519"
            || registry.public_key_sha256 != attestation.public_key_sha256
        {
            return Err("signer key is not the active CEX registry key".to_string());
        }
        Ok(attestation)
    }

    pub async fn verify_session(
        &self,
        player_session: &str,
        player_id: &str,
        account_id: &str,
    ) -> Result<SessionVerifyResponse, String> {
        let response = self
            .async_client
            .post(format!("{}/v1/trnm/identity/session/verify", self.base_url))
            .header(PLAYER_SESSION_HEADER, player_session)
            .json(&SessionVerifyRequest {
                player_id,
                account_id,
            })
            .send()
            .await
            .map_err(|error| format!("CEX session verification transport: {error}"))?;
        if !response.status().is_success() {
            let status = response.status();
            let body = bounded_error_body(response).await;
            return Err(format!("CEX rejected player session ({status}): {body}"));
        }
        let verified = bounded_remote_json::<SessionVerifyResponse>(response)
            .await
            .map_err(|error| format!("decode CEX session verification: {error}"))?;
        if !verified.verified {
            return Err("CEX player session is not verified".to_string());
        }
        Ok(verified)
    }

    async fn lookup_signer_receipt(
        &self,
        authorization_request_id: &str,
    ) -> Result<Option<EntitlementSignResponse>, ExternalSettlementError> {
        let response = self
            .async_client
            .get(format!(
                "{}{}/{}",
                self.signer_url, ENTITLEMENT_SIGNER_RECEIPT_PATH, authorization_request_id
            ))
            .header(SIGNER_AUTH_HEADER, self.signer_token.as_str())
            .send()
            .await
            .map_err(|error| {
                ExternalSettlementError::transport(
                    "isolated signer receipt lookup transport",
                    error,
                )
            })?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            let status = response.status();
            let body = bounded_error_body(response).await;
            return Err(ExternalSettlementError::status(
                "isolated signer receipt lookup failed",
                status,
                body,
            ));
        }
        bounded_remote_json::<EntitlementSignResponse>(response)
            .await
            .map(Some)
            .map_err(|error| {
                ExternalSettlementError::Retryable(format!(
                    "decode isolated signer receipt lookup after success status: {error}"
                ))
            })
    }

    async fn create_signer_receipt(
        &self,
        authorization_request_id: &str,
        entitlement: &ServerSignedValueEntitlementV2,
    ) -> Result<EntitlementSignResponse, ExternalSettlementError> {
        let response = self
            .async_client
            .post(format!("{}/v1/signer/sign", self.signer_url))
            .header(SIGNER_AUTH_HEADER, self.signer_token.as_str())
            .json(&EntitlementSignRequest {
                contract_version: ENTITLEMENT_SIGNER_CONTRACT.to_string(),
                request_id: authorization_request_id.to_string(),
                entitlement: entitlement.clone(),
            })
            .send()
            .await
            .map_err(|error| {
                ExternalSettlementError::transport("isolated signer transport", error)
            })?;
        if !response.status().is_success() {
            let status = response.status();
            let body = bounded_error_body(response).await;
            return Err(ExternalSettlementError::status(
                "isolated signer rejected entitlement",
                status,
                body,
            ));
        }
        bounded_remote_json::<EntitlementSignResponse>(response)
            .await
            .map_err(|error| {
                ExternalSettlementError::Retryable(format!(
                    "decode isolated signer response after possible commit: {error}"
                ))
            })
    }

    pub async fn authorize_settlement_intent(
        &self,
        intent: &EconomicIntent,
        authorization_request_id: &str,
        issued_at_epoch: i64,
        expires_at_epoch: i64,
        nonce: &str,
    ) -> Result<AuthorizedSettlementIntent, ExternalSettlementError> {
        intent
            .validate()
            .map_err(ExternalSettlementError::Permanent)?;
        if authorization_request_id.trim().is_empty()
            || authorization_request_id.len() > 256
            || nonce != authorization_request_id
            || expires_at_epoch <= issued_at_epoch
            || expires_at_epoch > issued_at_epoch.saturating_add(600)
        {
            return Err(ExternalSettlementError::Permanent(
                "settlement authorization identity/timing is invalid".to_string(),
            ));
        }

        let mut authorized = intent.clone();
        let mut signer_receipt_hash = None;
        if matches!(authorized.kind, EconomicIntentKind::ReleaseReward)
            && authorized.amount_credits.unwrap_or_default() > 0
        {
            let actor = authorized.actors.first().ok_or_else(|| {
                ExternalSettlementError::Permanent("reward intent has no primary actor".to_string())
            })?;
            let account_id = actor.account_id.clone().ok_or_else(|| {
                ExternalSettlementError::Permanent("reward intent has no account".to_string())
            })?;
            let metadata_string = |key: &str| -> Result<String, ExternalSettlementError> {
                authorized
                    .metadata
                    .get(key)
                    .and_then(|value| value.as_str())
                    .filter(|value| !value.is_empty())
                    .map(str::to_string)
                    .ok_or_else(|| {
                        ExternalSettlementError::Permanent(format!(
                            "online reward is missing authoritative {key}"
                        ))
                    })
            };
            let issued_at =
                DateTime::<Utc>::from_timestamp(issued_at_epoch, 0).ok_or_else(|| {
                    ExternalSettlementError::Permanent(
                        "settlement entitlement issued_at is outside chrono range".to_string(),
                    )
                })?;
            let entitlement = ServerSignedValueEntitlementV2 {
                contract_version: SERVER_SIGNED_VALUE_ENTITLEMENT_V2_CONTRACT.to_string(),
                entitlement_id: stable_entitlement_id(authorization_request_id),
                issuer: ENTITLEMENT_SIGNER_ISSUER.to_string(),
                key_id: String::new(),
                signature_algorithm: "ed25519".to_string(),
                actor_id: actor.actor_id.clone(),
                account_id,
                source: ValueEntitlementSource::Battle,
                source_id: authorized
                    .metadata
                    .get("value_event_id")
                    .and_then(|value| value.as_str())
                    .unwrap_or(&authorized.intent_id)
                    .to_string(),
                intent_id: authorized.intent_id.clone(),
                amount_credits: authorized.amount_credits.unwrap_or_default(),
                currency: "wallet_credits".to_string(),
                budget_day: (issued_at.year() as u32) * 10_000
                    + issued_at.month() * 100
                    + issued_at.day(),
                issued_at_epoch,
                expires_at_epoch,
                match_id: metadata_string("online_match_id")?,
                rules_version: metadata_string("online_rules_version")?,
                build_id: metadata_string("online_build_id")?,
                result_hash: metadata_string("online_result_hash")?,
                participants_hash: metadata_string("online_participants_hash")?,
                nonce: nonce.to_string(),
                signature: String::new(),
            };
            let signed = match self.lookup_signer_receipt(authorization_request_id).await? {
                Some(existing) => existing,
                None => {
                    self.create_signer_receipt(authorization_request_id, &entitlement)
                        .await?
                }
            };
            let (entitlement, receipt_hash) =
                validate_signer_response(entitlement, authorization_request_id, signed)?;
            signer_receipt_hash = Some(receipt_hash);
            authorized.metadata[SERVER_SIGNED_VALUE_ENTITLEMENT_METADATA_KEY] =
                serde_json::to_value(entitlement).map_err(|error| {
                    ExternalSettlementError::Permanent(format!(
                        "encode signed settlement entitlement: {error}"
                    ))
                })?;
        }

        Ok(AuthorizedSettlementIntent {
            intent: authorized,
            authorization_request_id: authorization_request_id.to_string(),
            signer_receipt_hash,
        })
    }

    async fn lookup_authorized_settlement_receipt(
        &self,
        authorized: &EconomicIntent,
        intent_hash: &str,
    ) -> Result<Option<EconomicReceipt>, ExternalSettlementError> {
        let response = self
            .async_client
            .get(format!(
                "{}{}",
                self.base_url, CEX_SETTLEMENT_RECEIPT_LOOKUP_PATH
            ))
            .headers(
                self.authority_headers()
                    .map_err(ExternalSettlementError::Permanent)?,
            )
            .header(INTENT_HASH_HEADER, intent_hash)
            .query(&[("intent_id", authorized.intent_id.as_str())])
            .send()
            .await
            .map_err(|error| {
                ExternalSettlementError::transport("CEX receipt lookup transport", error)
            })?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            let status = response.status();
            let body = bounded_error_body(response).await;
            return Err(ExternalSettlementError::status(
                "CEX receipt lookup failed",
                status,
                body,
            ));
        }
        let lookup = bounded_remote_json::<CexSettlementReceiptLookupResponse>(response)
            .await
            .map_err(|error| {
                ExternalSettlementError::Retryable(format!(
                    "decode CEX receipt lookup after success status: {error}"
                ))
            })?;
        if lookup.contract_version != CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT
            || lookup.intent_id != authorized.intent_id
            || lookup.intent_hash != intent_hash
        {
            return Err(ExternalSettlementError::Permanent(
                "CEX receipt lookup failed immutable intent binding".to_string(),
            ));
        }
        lookup
            .receipt
            .validate_for(authorized)
            .map_err(ExternalSettlementError::Permanent)?;
        Ok(Some(lookup.receipt))
    }

    pub async fn submit_authorized_settlement_intent(
        &self,
        authorized: &EconomicIntent,
    ) -> Result<EconomicReceipt, ExternalSettlementError> {
        authorized
            .validate()
            .map_err(ExternalSettlementError::Permanent)?;
        let intent_hash = serialized_intent_hash(authorized)?;
        if let Some(receipt) = self
            .lookup_authorized_settlement_receipt(authorized, &intent_hash)
            .await?
        {
            return Ok(receipt);
        }
        let response = self
            .async_client
            .post(format!("{}/v1/trnm/economy/intents", self.base_url))
            .headers(
                self.authority_headers()
                    .map_err(ExternalSettlementError::Permanent)?,
            )
            .header(INTENT_HASH_HEADER, intent_hash.as_str())
            .json(&json!({"intent": authorized}))
            .send()
            .await
            .map_err(|error| ExternalSettlementError::transport("CEX intent transport", error))?;
        if !response.status().is_success() {
            let status = response.status();
            let body = bounded_error_body(response).await;
            return Err(ExternalSettlementError::status(
                "CEX intent rejected",
                status,
                body,
            ));
        }
        let receipt = bounded_remote_json::<EconomicReceipt>(response)
            .await
            .map_err(|error| {
                ExternalSettlementError::Retryable(format!(
                    "decode CEX receipt after possible commit: {error}"
                ))
            })?;
        receipt
            .validate_for(authorized)
            .map_err(ExternalSettlementError::Permanent)?;
        Ok(receipt)
    }

    fn authority_headers(&self) -> Result<HeaderMap, String> {
        let mut headers = HeaderMap::new();
        headers.insert(
            GAME_AUTHORITY_HEADER,
            self.game_authority_token
                .parse()
                .map_err(|_| "game authority token is not a valid header".to_string())?,
        );
        Ok(headers)
    }
}

impl EconomyBackend for CexClient {
    fn backend_id(&self) -> &str {
        "cex-trnm-settlement-outbox-v1"
    }

    fn execute(&self, _intent: &EconomicIntent) -> Result<EconomicReceipt, String> {
        Err(SETTLEMENT_OUTBOX_REQUIRED.to_string())
    }

    fn wallet_snapshot(
        &self,
        _binding: &EconomyAccountBinding,
        _cursor: u64,
    ) -> Result<Option<WalletSnapshot>, String> {
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::{
        bounded_error_body, normalize_service_base_url, retryable_status, serialized_intent_hash,
        stable_entitlement_id, CexClient, CexSettlementReceiptLookupResponse,
        ExternalSettlementError, CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT, INTENT_HASH_HEADER,
        MAX_REMOTE_ERROR_BODY_BYTES, SETTLEMENT_OUTBOX_REQUIRED,
    };
    use crate::signer_protocol::{
        EntitlementSignRequest, EntitlementSignResponse, ENTITLEMENT_SIGNER_CONTRACT,
        ENTITLEMENT_SIGNER_ISSUER, SIGNER_AUTH_HEADER,
    };
    use axum::{
        body::Body,
        extract::{Path, Query, State},
        http::{HeaderMap, StatusCode},
        response::{IntoResponse, Response},
        routing::{get, post},
        Json, Router,
    };
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use chrono::Utc;
    use futures_util::stream;
    use serde::Deserialize;
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::{
        convert::Infallible,
        sync::{
            atomic::{AtomicUsize, Ordering},
            Arc, Mutex,
        },
    };
    use tokio::task::JoinHandle;
    use trnm_campaign_core::EconomyBackend;
    use trnm_economy_protocol::{
        ActorRef, EconomicIntent, EconomicIntentKind, EconomicReceipt, IdempotencyKey,
        ReceiptStatus, SettlementBackendKind, TERM_EXCHANGE_PROTOCOL_VERSION,
    };

    #[derive(Clone, Default)]
    struct RemoteMockState {
        signer_receipt: Arc<Mutex<Option<EntitlementSignResponse>>>,
        cex_receipt: Arc<Mutex<Option<CexSettlementReceiptLookupResponse>>>,
        signer_posts: Arc<AtomicUsize>,
        cex_posts: Arc<AtomicUsize>,
    }

    #[derive(Deserialize)]
    struct LookupQuery {
        intent_id: String,
    }

    #[derive(Deserialize)]
    struct SubmitIntent {
        intent: EconomicIntent,
    }

    async fn signer_lookup(
        State(state): State<RemoteMockState>,
        headers: HeaderMap,
        Path(request_id): Path<String>,
    ) -> Response {
        if headers.get(SIGNER_AUTH_HEADER).is_none() {
            return StatusCode::UNAUTHORIZED.into_response();
        }
        let receipt = state.signer_receipt.lock().unwrap().clone();
        match receipt.filter(|receipt| receipt.request_id == request_id) {
            Some(receipt) => (StatusCode::OK, Json(receipt)).into_response(),
            None => StatusCode::NOT_FOUND.into_response(),
        }
    }

    async fn signer_submit(
        State(state): State<RemoteMockState>,
        headers: HeaderMap,
        Json(request): Json<EntitlementSignRequest>,
    ) -> Response {
        if headers.get(SIGNER_AUTH_HEADER).is_none() {
            return StatusCode::UNAUTHORIZED.into_response();
        }
        let mut entitlement = request.entitlement;
        entitlement.key_id = "mock-key".to_string();
        entitlement.signature.clear();
        let payload = entitlement.signing_payload().unwrap();
        let request_hash = format!("{:x}", Sha256::digest(payload));
        let signature = STANDARD.encode([7_u8; 64]);
        let signing_receipt_hash = format!(
            "{:x}",
            Sha256::digest(format!("{request_hash}:mock-key:{signature}").as_bytes())
        );
        let receipt = EntitlementSignResponse {
            contract_version: ENTITLEMENT_SIGNER_CONTRACT.to_string(),
            request_id: request.request_id,
            request_hash,
            signing_receipt_hash,
            key_id: "mock-key".to_string(),
            issuer: ENTITLEMENT_SIGNER_ISSUER.to_string(),
            signature,
            duplicate: false,
        };
        *state.signer_receipt.lock().unwrap() = Some(receipt.clone());
        let attempt = state.signer_posts.fetch_add(1, Ordering::SeqCst);
        if attempt == 0 {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "response lost after signer commit"})),
            )
                .into_response();
        }
        (StatusCode::OK, Json(receipt)).into_response()
    }

    async fn cex_lookup(
        State(state): State<RemoteMockState>,
        headers: HeaderMap,
        Query(query): Query<LookupQuery>,
    ) -> Response {
        if headers.get(INTENT_HASH_HEADER).is_none() {
            return StatusCode::BAD_REQUEST.into_response();
        }
        let receipt = state.cex_receipt.lock().unwrap().clone();
        match receipt.filter(|receipt| receipt.intent_id == query.intent_id) {
            Some(receipt) => (StatusCode::OK, Json(receipt)).into_response(),
            None => StatusCode::NOT_FOUND.into_response(),
        }
    }

    async fn cex_submit(
        State(state): State<RemoteMockState>,
        headers: HeaderMap,
        Json(request): Json<SubmitIntent>,
    ) -> Response {
        let Some(intent_hash) = headers
            .get(INTENT_HASH_HEADER)
            .and_then(|value| value.to_str().ok())
            .map(str::to_string)
        else {
            return StatusCode::BAD_REQUEST.into_response();
        };
        let receipt = EconomicReceipt::from_intent(
            format!("receipt:{}", request.intent.intent_id),
            &request.intent,
            "mock-cex",
            SettlementBackendKind::Cex,
            ReceiptStatus::Settled,
            Utc::now().timestamp(),
        );
        let lookup = CexSettlementReceiptLookupResponse {
            contract_version: CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT.to_string(),
            intent_id: request.intent.intent_id,
            intent_hash,
            receipt: receipt.clone(),
        };
        *state.cex_receipt.lock().unwrap() = Some(lookup);
        let attempt = state.cex_posts.fetch_add(1, Ordering::SeqCst);
        if attempt == 0 {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "response lost after CEX commit"})),
            )
                .into_response();
        }
        (StatusCode::OK, Json(receipt)).into_response()
    }

    async fn oversized_chunked_error() -> Response {
        let chunks = vec![
            Ok::<_, Infallible>("a".repeat(MAX_REMOTE_ERROR_BODY_BYTES / 2)),
            Ok::<_, Infallible>("b".repeat(MAX_REMOTE_ERROR_BODY_BYTES / 2)),
            Ok::<_, Infallible>("c".repeat(1024)),
        ];
        Response::builder()
            .status(StatusCode::BAD_GATEWAY)
            .body(Body::from_stream(stream::iter(chunks)))
            .unwrap()
    }

    async fn spawn_remote_mock() -> (String, RemoteMockState, JoinHandle<()>) {
        let state = RemoteMockState::default();
        let app = Router::new()
            .route("/v1/signer/receipts/:request_id", get(signer_lookup))
            .route("/v1/signer/sign", post(signer_submit))
            .route("/v1/trnm/economy/receipts/by-intent", get(cex_lookup))
            .route("/v1/trnm/economy/intents", post(cex_submit))
            .route("/oversized-error", get(oversized_chunked_error))
            .with_state(state.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        (format!("http://{address}"), state, task)
    }

    pub(super) fn base_intent(kind: EconomicIntentKind, amount: i64) -> EconomicIntent {
        EconomicIntent {
            protocol_version: TERM_EXCHANGE_PROTOCOL_VERSION.to_string(),
            intent_id: "intent-a".to_string(),
            term_id: "term-a".to_string(),
            term_version: "1".to_string(),
            domain: "trnm_game".to_string(),
            kind,
            idempotency_key: IdempotencyKey {
                scope: "campaign-a".to_string(),
                key: "intent-a".to_string(),
            },
            actors: vec![ActorRef {
                actor_id: "actor-a".to_string(),
                actor_kind: "player".to_string(),
                account_id: Some("account-a".to_string()),
            }],
            assets: Vec::new(),
            amount_credits: Some(amount),
            currency: Some("wallet_credits".to_string()),
            metadata: json!({
                "value_event_id": "event-a",
                "online_match_id": "match-a",
                "online_rules_version": "rules-a",
                "online_build_id": "build-a",
                "online_result_hash": "a".repeat(64),
                "online_participants_hash": "b".repeat(64)
            }),
            created_at_epoch: Utc::now().timestamp(),
        }
    }

    #[test]
    fn synchronous_backend_never_performs_external_settlement() {
        let client = CexClient::new(
            "http://127.0.0.1:1".to_string(),
            "g".repeat(24),
            "http://127.0.0.1:2".to_string(),
            "s".repeat(32),
        )
        .unwrap();
        let intent = base_intent(EconomicIntentKind::CompleteContract, 0);
        assert_eq!(
            client.execute(&intent),
            Err(SETTLEMENT_OUTBOX_REQUIRED.to_string())
        );
        assert_eq!(
            client.wallet_snapshot(
                &trnm_economy_protocol::EconomyAccountBinding {
                    actor_id: "actor-a".to_string(),
                    account_id: "account-a".to_string(),
                    binding_revision: 1,
                },
                0,
            ),
            Ok(None)
        );
    }

    #[test]
    fn service_endpoints_require_encrypted_transport_off_loopback() {
        assert_eq!(
            normalize_service_base_url("http://127.0.0.1:8080/", "TEST_URL").unwrap(),
            "http://127.0.0.1:8080"
        );
        assert_eq!(
            normalize_service_base_url("http://[::1]:8080", "TEST_URL").unwrap(),
            "http://[::1]:8080"
        );
        assert_eq!(
            normalize_service_base_url("http://localhost:8080", "TEST_URL").unwrap(),
            "http://localhost:8080"
        );
        assert_eq!(
            normalize_service_base_url("https://cex.example.test/", "TEST_URL").unwrap(),
            "https://cex.example.test"
        );
        for invalid in [
            "http://cex.example.test",
            "ftp://127.0.0.1",
            "https://user:secret@cex.example.test",
            "https://cex.example.test/prefix",
            "https://cex.example.test?token=secret",
            "https://cex.example.test/#fragment",
        ] {
            assert!(
                normalize_service_base_url(invalid, "TEST_URL").is_err(),
                "unexpectedly accepted {invalid}"
            );
        }
    }

    #[tokio::test]
    async fn remote_error_body_is_streamed_to_a_hard_retained_limit() {
        let (url, _state, task) = spawn_remote_mock().await;
        let response = reqwest::Client::new()
            .get(format!("{url}/oversized-error"))
            .send()
            .await
            .unwrap();
        assert_eq!(response.content_length(), None);
        let body = bounded_error_body(response).await;
        assert!(body.starts_with(&"a".repeat(1024)));
        assert!(body.contains(&format!(
            "[truncated at {MAX_REMOTE_ERROR_BODY_BYTES} bytes]"
        )));
        assert!(body.len() <= MAX_REMOTE_ERROR_BODY_BYTES + 64);
        task.abort();
    }

    #[test]
    fn authorization_identity_is_stable_across_ambiguous_retries() {
        let first = stable_entitlement_id("trnm-settlement-remote-v1:abc");
        let second = stable_entitlement_id("trnm-settlement-remote-v1:abc");
        assert_eq!(first, second);
        assert_ne!(
            first,
            stable_entitlement_id("trnm-settlement-remote-v1:def")
        );
    }

    #[test]
    fn timeout_backpressure_conflict_and_server_statuses_are_retryable() {
        assert!(retryable_status(StatusCode::REQUEST_TIMEOUT));
        assert!(retryable_status(StatusCode::TOO_MANY_REQUESTS));
        assert!(retryable_status(StatusCode::CONFLICT));
        assert!(retryable_status(StatusCode::TEMPORARY_REDIRECT));
        assert!(retryable_status(StatusCode::BAD_GATEWAY));
        assert!(!retryable_status(StatusCode::BAD_REQUEST));
        assert!(!retryable_status(StatusCode::UNAUTHORIZED));
    }

    #[tokio::test]
    async fn signer_response_loss_recovers_by_lookup_without_a_second_sign() {
        let (url, state, task) = spawn_remote_mock().await;
        let client = CexClient::new(url.clone(), "g".repeat(24), url, "s".repeat(32)).unwrap();
        let intent = base_intent(EconomicIntentKind::ReleaseReward, 25);
        let request_id = format!("trnm-settlement-remote-v1:{}", "1".repeat(64));
        let issued_at = Utc::now().timestamp();

        let first = client
            .authorize_settlement_intent(
                &intent,
                &request_id,
                issued_at,
                issued_at + 600,
                &request_id,
            )
            .await;
        assert!(matches!(first, Err(ExternalSettlementError::Retryable(_))));

        let recovered = client
            .authorize_settlement_intent(
                &intent,
                &request_id,
                issued_at,
                issued_at + 600,
                &request_id,
            )
            .await
            .unwrap();
        assert_eq!(recovered.authorization_request_id, request_id);
        assert!(recovered.signer_receipt_hash.is_some());
        assert_eq!(state.signer_posts.load(Ordering::SeqCst), 1);
        task.abort();
    }

    #[tokio::test]
    async fn cex_response_loss_recovers_by_lookup_without_a_second_submit() {
        let (url, state, task) = spawn_remote_mock().await;
        let client = CexClient::new(url.clone(), "g".repeat(24), url, "s".repeat(32)).unwrap();
        let intent = base_intent(EconomicIntentKind::CompleteContract, 0);

        let first = client.submit_authorized_settlement_intent(&intent).await;
        assert!(matches!(first, Err(ExternalSettlementError::Retryable(_))));

        let recovered = client
            .submit_authorized_settlement_intent(&intent)
            .await
            .unwrap();
        recovered.validate_for(&intent).unwrap();
        assert_eq!(state.cex_posts.load(Ordering::SeqCst), 1);
        task.abort();
    }

    #[tokio::test]
    async fn cex_lookup_with_a_mismatched_hash_fails_closed() {
        let (url, state, task) = spawn_remote_mock().await;
        let client = CexClient::new(url.clone(), "g".repeat(24), url, "s".repeat(32)).unwrap();
        let intent = base_intent(EconomicIntentKind::CompleteContract, 0);
        let receipt = EconomicReceipt::from_intent(
            "receipt-a",
            &intent,
            "mock-cex",
            SettlementBackendKind::Cex,
            ReceiptStatus::Settled,
            Utc::now().timestamp(),
        );
        *state.cex_receipt.lock().unwrap() = Some(CexSettlementReceiptLookupResponse {
            contract_version: CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT.to_string(),
            intent_id: intent.intent_id.clone(),
            intent_hash: "f".repeat(64),
            receipt,
        });
        let expected_hash = serialized_intent_hash(&intent).unwrap();
        assert_ne!(expected_hash, "f".repeat(64));
        let result = client.submit_authorized_settlement_intent(&intent).await;
        assert!(matches!(result, Err(ExternalSettlementError::Permanent(_))));
        task.abort();
    }
}

#[cfg(test)]
#[path = "cex_response_policy_tests.rs"]
mod response_policy_tests;
