use audit_events::AuditEvent;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use sha2::{Digest as Sha256Digest, Sha256};
use sha3::Keccak256;

const ADMIN_UNSET: [u8; 32] = [0u8; 32];
use std::collections::HashSet;

const VALIDATOR_SIGNATURE_LEN: usize = 96;
const VALIDATOR_SIGNATURE_KEY_LEN: usize = 32;
const VALIDATOR_SIGNATURE_BYTES_LEN: usize = 64;
const TX_RECEIPT_SUCCESS: u8 = 1;

pub fn action_settlement_finalize() -> [u8; 32] {
    Keccak256::digest(b"SETTLEMENT_FINALIZE").into()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeSettlementMessage {
    pub source_chain_id: u64,
    pub source_bridge_id: [u8; 32],
    pub source_tx_hash: [u8; 32],
    pub source_log_index: u64,
    pub target_chain_id: u64,
    pub target_bridge: [u8; 20],
    pub receiver: [u8; 20],
    pub asset: [u8; 20],
    pub amount: u128,
    pub nonce: u64,
    pub deadline: u64,
    pub tx_receipt_status: u8,
    pub config_version: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BridgeRelayError {
    ProofExpired {
        now_ts: u64,
        deadline: u64,
    },
    DeadlineWitnessMismatch {
        expected: u64,
        got: u64,
    },
    ProofAlreadyUsed {
        proof_digest: [u8; 32],
    },
    NonceAlreadyUsed {
        nonce_key: [u8; 32],
    },
    SettlementAlreadyFinalized {
        settlement_id: [u8; 32],
    },
    InvalidTargetChain {
        expected: u64,
        got: u64,
    },
    InvalidTargetBridge {
        expected: [u8; 20],
        got: [u8; 20],
    },
    InvalidValidatorSignatureLength {
        got: usize,
    },
    InvalidValidatorConfiguration {
        min_validator_signatures: usize,
        available_validators: usize,
    },
    Unauthorized,
    UnknownValidator {
        validator: [u8; 32],
    },
    DuplicateValidatorSignature {
        validator: [u8; 32],
    },
    InvalidValidatorSignature {
        validator: [u8; 32],
        expected: [u8; 32],
        got: [u8; 32],
    },
    InvalidConfigVersion {
        expected: u64,
        got: u64,
    },
    InvalidTransactionReceipt {
        status: u8,
    },
    NotEnoughValidatorSignatures {
        required: usize,
        got: usize,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BridgeRelayEvent {
    ProofSubmitted {
        proof_digest: [u8; 32],
        validator_count: usize,
    },
    ConfigVersionUpdated {
        actor: [u8; 32],
        old_version: u64,
        new_version: u64,
    },
    ProofSubmittedAndStored {
        proof_digest: [u8; 32],
    },
    SettlementFinalized {
        settlement_id: [u8; 32],
        proof_digest: [u8; 32],
    },
    NonceConsumed {
        nonce_key: [u8; 32],
    },
    AdminUpdated {
        actor: [u8; 32],
        old_admin: [u8; 32],
        new_admin: [u8; 32],
    },
    MinSignaturesUpdated {
        actor: [u8; 32],
        old_min: usize,
        new_min: usize,
    },
    ValidatorsUpdated {
        actor: [u8; 32],
        previous_count: usize,
        new_count: usize,
    },
}

#[derive(Debug, Default)]
pub struct BridgeRelay {
    proof_used: HashSet<[u8; 32]>,
    nonce_used: HashSet<[u8; 32]>,
    settlement_finalized: HashSet<[u8; 32]>,
    validators: HashSet<[u8; 32]>,
    min_validator_signatures: usize,
    admin: [u8; 32],
    config_version: u64,
    audit_log: Vec<BridgeRelayEvent>,
}

impl BridgeRelay {
    pub fn new(
        min_validator_signatures: usize,
        validators: impl IntoIterator<Item = [u8; 32]>,
    ) -> Self {
        Self::with_admin(min_validator_signatures, validators, ADMIN_UNSET)
    }

    pub fn config_version(&self) -> u64 {
        self.config_version
    }

    pub fn with_admin(
        min_validator_signatures: usize,
        validators: impl IntoIterator<Item = [u8; 32]>,
        admin: [u8; 32],
    ) -> Self {
        Self {
            validators: validators.into_iter().collect(),
            min_validator_signatures,
            admin,
            config_version: 1,
            ..Self::default()
        }
    }

    pub fn submit_proof(
        &mut self,
        message: &BridgeSettlementMessage,
        signatures: &[Vec<u8>],
        deadline: u64,
        now_ts: u64,
        current_chain_id: u64,
        self_bridge: [u8; 20],
    ) -> Result<[u8; 32], BridgeRelayError> {
        if now_ts > deadline {
            return Err(BridgeRelayError::ProofExpired { now_ts, deadline });
        }
        if deadline != message.deadline {
            return Err(BridgeRelayError::DeadlineWitnessMismatch {
                expected: message.deadline,
                got: deadline,
            });
        }

        self.validate_validator_signature_config(
            self.min_validator_signatures,
            self.validators.len(),
        )?;
        self.validate_message_domain(message, now_ts, current_chain_id, self_bridge)?;
        self.validate_message_config(message)?;
        self.validate_message_receipt(message)?;

        let proof_digest = hash_message(message);
        if self.proof_used.contains(&proof_digest) {
            return Err(BridgeRelayError::ProofAlreadyUsed { proof_digest });
        }

        let valid_count = self.validate_validator_signatures(message, signatures)?;
        if valid_count < self.min_validator_signatures {
            return Err(BridgeRelayError::NotEnoughValidatorSignatures {
                required: self.min_validator_signatures,
                got: valid_count,
            });
        }

        self.audit_log.push(BridgeRelayEvent::ProofSubmitted {
            proof_digest,
            validator_count: valid_count,
        });
        self.audit_log
            .push(BridgeRelayEvent::ProofSubmittedAndStored { proof_digest });
        self.proof_used.insert(proof_digest);
        Ok(proof_digest)
    }

    pub fn consume_nonce(
        &mut self,
        source_chain_id: u64,
        source_bridge_id: [u8; 32],
        target_chain_id: u64,
        target_bridge: [u8; 20],
        action: [u8; 32],
        nonce: u64,
    ) -> Result<[u8; 32], BridgeRelayError> {
        let nonce_key = nonce_key(
            source_chain_id,
            source_bridge_id,
            target_chain_id,
            target_bridge,
            action,
            nonce,
        );

        if self.nonce_used.contains(&nonce_key) {
            return Err(BridgeRelayError::NonceAlreadyUsed { nonce_key });
        }

        self.nonce_used.insert(nonce_key);
        self.audit_log
            .push(BridgeRelayEvent::NonceConsumed { nonce_key });
        Ok(nonce_key)
    }

    pub fn set_admin(
        &mut self,
        caller: &[u8; 32],
        new_admin: [u8; 32],
    ) -> Result<(), BridgeRelayError> {
        self.require_admin(caller)?;
        let old_admin = self.admin;
        let old_version = self.config_version;
        let new_version = old_version + 1;

        self.admin = new_admin;
        self.config_version = new_version;
        self.audit_log.push(BridgeRelayEvent::AdminUpdated {
            actor: *caller,
            old_admin,
            new_admin,
        });
        self.audit_log.push(BridgeRelayEvent::ConfigVersionUpdated {
            actor: *caller,
            old_version,
            new_version,
        });
        Ok(())
    }

    pub fn set_min_validator_signatures(
        &mut self,
        caller: &[u8; 32],
        min: usize,
    ) -> Result<(), BridgeRelayError> {
        self.require_admin(caller)?;
        self.validate_validator_signature_config(min, self.validators.len())?;

        let old_min = self.min_validator_signatures;
        let old_version = self.config_version;
        let new_version = old_version + 1;

        self.min_validator_signatures = min;
        self.config_version = new_version;
        self.audit_log.push(BridgeRelayEvent::MinSignaturesUpdated {
            actor: *caller,
            old_min,
            new_min: min,
        });
        self.audit_log.push(BridgeRelayEvent::ConfigVersionUpdated {
            actor: *caller,
            old_version,
            new_version,
        });
        Ok(())
    }

    pub fn set_validators(
        &mut self,
        caller: &[u8; 32],
        validators: impl IntoIterator<Item = [u8; 32]>,
    ) -> Result<(), BridgeRelayError> {
        self.require_admin(caller)?;
        let previous_count = self.validators.len();
        let new_validators = validators
            .into_iter()
            .collect::<std::collections::HashSet<_>>();
        let new_count = new_validators.len();

        self.validate_validator_signature_config(self.min_validator_signatures, new_count)?;
        let old_version = self.config_version;
        let new_version = old_version + 1;

        self.validators = new_validators;
        self.config_version = new_version;

        self.audit_log.push(BridgeRelayEvent::ValidatorsUpdated {
            actor: *caller,
            previous_count,
            new_count: self.validators.len(),
        });
        self.audit_log.push(BridgeRelayEvent::ConfigVersionUpdated {
            actor: *caller,
            old_version,
            new_version,
        });
        Ok(())
    }

    pub fn set_admin_with_version(
        &mut self,
        caller: &[u8; 32],
        expected_config_version: u64,
        new_admin: [u8; 32],
    ) -> Result<(), BridgeRelayError> {
        self.require_admin(caller)?;
        self.require_config_version(expected_config_version)?;
        self.set_admin(caller, new_admin)
    }

    pub fn set_min_validator_signatures_with_version(
        &mut self,
        caller: &[u8; 32],
        expected_config_version: u64,
        min: usize,
    ) -> Result<(), BridgeRelayError> {
        self.require_admin(caller)?;
        self.require_config_version(expected_config_version)?;
        self.set_min_validator_signatures(caller, min)
    }

    pub fn set_validators_with_version(
        &mut self,
        caller: &[u8; 32],
        expected_config_version: u64,
        validators: impl IntoIterator<Item = [u8; 32]>,
    ) -> Result<(), BridgeRelayError> {
        self.require_admin(caller)?;
        self.require_config_version(expected_config_version)?;
        self.set_validators(caller, validators)
    }

    fn require_config_version(&self, expected_config_version: u64) -> Result<(), BridgeRelayError> {
        if self.config_version != expected_config_version {
            return Err(BridgeRelayError::InvalidConfigVersion {
                expected: self.config_version,
                got: expected_config_version,
            });
        }

        Ok(())
    }

    fn validate_validator_signature_config(
        &self,
        min_validator_signatures: usize,
        validator_count: usize,
    ) -> Result<(), BridgeRelayError> {
        if validator_count == 0
            || min_validator_signatures == 0
            || min_validator_signatures > validator_count
        {
            return Err(BridgeRelayError::InvalidValidatorConfiguration {
                min_validator_signatures,
                available_validators: validator_count,
            });
        }

        Ok(())
    }

    pub fn finalize_settlement(
        &mut self,
        message: &BridgeSettlementMessage,
        signatures: &[Vec<u8>],
        deadline: u64,
        now_ts: u64,
        current_chain_id: u64,
        self_bridge: [u8; 20],
    ) -> Result<[u8; 32], BridgeRelayError> {
        let settlement_id = settlement_id(message);
        if self.settlement_finalized.contains(&settlement_id) {
            return Err(BridgeRelayError::SettlementAlreadyFinalized { settlement_id });
        }

        let audit_len_before = self.audit_log.len();
        let proof_digest = self.submit_proof(
            message,
            signatures,
            deadline,
            now_ts,
            current_chain_id,
            self_bridge,
        )?;

        if let Err(err) = self.consume_nonce(
            message.source_chain_id,
            message.source_bridge_id,
            message.target_chain_id,
            message.target_bridge,
            action_settlement_finalize(),
            message.nonce,
        ) {
            self.proof_used.remove(&proof_digest);
            self.audit_log.truncate(audit_len_before);
            return Err(err);
        }

        self.settlement_finalized.insert(settlement_id);
        self.audit_log.push(BridgeRelayEvent::SettlementFinalized {
            settlement_id,
            proof_digest,
        });
        Ok(settlement_id)
    }

    pub fn audit_log(&self) -> &[BridgeRelayEvent] {
        &self.audit_log
    }

    pub fn consume_audit_log(&mut self) -> Vec<BridgeRelayEvent> {
        std::mem::take(&mut self.audit_log)
    }

    pub fn normalized_audit_log(&self) -> Vec<AuditEvent> {
        self.audit_log
            .iter()
            .map(Self::normalize_audit_event)
            .collect()
    }

    fn normalize_audit_event(event: &BridgeRelayEvent) -> AuditEvent {
        match event {
            BridgeRelayEvent::ProofSubmitted {
                proof_digest,
                validator_count,
            } => {
                let mut normalized =
                    AuditEvent::new("bridge-relay", "bridge_relay.proof_submitted");
                normalized.object_id = Some(hex32(proof_digest));
                normalized.amount = Some(*validator_count as u128);
                normalized.note = Some("proof submitted".to_string());
                normalized
            }
            BridgeRelayEvent::ProofSubmittedAndStored { proof_digest } => {
                let mut normalized =
                    AuditEvent::new("bridge-relay", "bridge_relay.proof_submitted_and_stored");
                normalized.object_id = Some(hex32(proof_digest));
                normalized.note = Some("proof stored".to_string());
                normalized
            }
            BridgeRelayEvent::SettlementFinalized {
                settlement_id,
                proof_digest,
            } => {
                let mut normalized =
                    AuditEvent::new("bridge-relay", "bridge_relay.settlement_finalized");
                normalized.object_id = Some(hex32(settlement_id));
                normalized.related_id = Some(hex32(proof_digest));
                normalized
            }
            BridgeRelayEvent::NonceConsumed { nonce_key } => {
                let mut normalized = AuditEvent::new("bridge-relay", "bridge_relay.nonce_consumed");
                normalized.object_id = Some(hex32(nonce_key));
                normalized
            }
            BridgeRelayEvent::AdminUpdated {
                actor,
                old_admin,
                new_admin,
            } => {
                let mut normalized = AuditEvent::new("bridge-relay", "bridge_relay.admin_updated");
                normalized.actor = Some(hex32(actor));
                normalized.object_id = Some(hex32(new_admin));
                normalized.related_id = Some(hex32(old_admin));
                normalized.reason = Some("admin_rotation".to_string());
                normalized
            }
            BridgeRelayEvent::ConfigVersionUpdated {
                actor,
                old_version,
                new_version,
            } => {
                let mut normalized =
                    AuditEvent::new("bridge-relay", "bridge_relay.config_version_updated");
                normalized.actor = Some(hex32(actor));
                normalized.object_id = Some("bridge_config".to_string());
                normalized.related_id = Some("config_version".to_string());
                normalized.amount = Some(*new_version as u128);
                normalized.reason = Some("config_version_rotation".to_string());
                normalized.note = Some(format!(
                    "old_version={old_version}, new_version={new_version}"
                ));
                normalized
            }
            BridgeRelayEvent::MinSignaturesUpdated {
                actor,
                old_min,
                new_min,
            } => {
                let mut normalized =
                    AuditEvent::new("bridge-relay", "bridge_relay.min_signatures_updated");
                normalized.actor = Some(hex32(actor));
                normalized.object_id = Some("bridge_config".to_string());
                normalized.related_id = Some("min_signatures".to_string());
                normalized.amount = Some(*new_min as u128);
                normalized.reason = Some("validator_threshold_rotation".to_string());
                normalized.note = Some(format!("old_min={old_min}, new_min={new_min}"));
                normalized
            }
            BridgeRelayEvent::ValidatorsUpdated {
                actor,
                previous_count,
                new_count,
            } => {
                let mut normalized =
                    AuditEvent::new("bridge-relay", "bridge_relay.validators_updated");
                normalized.actor = Some(hex32(actor));
                normalized.object_id = Some("bridge_config".to_string());
                normalized.related_id = Some("validators".to_string());
                normalized.amount = Some(*new_count as u128);
                normalized.reason = Some("validator_set_rotation".to_string());
                normalized.note = Some(format!(
                    "previous_count={previous_count}, new_count={new_count}"
                ));
                normalized
            }
        }
    }

    fn require_admin(&self, caller: &[u8; 32]) -> Result<(), BridgeRelayError> {
        if self.admin == ADMIN_UNSET || self.admin == *caller {
            Ok(())
        } else {
            Err(BridgeRelayError::Unauthorized)
        }
    }

    fn validate_message_config(
        &self,
        message: &BridgeSettlementMessage,
    ) -> Result<(), BridgeRelayError> {
        if message.config_version != self.config_version {
            return Err(BridgeRelayError::InvalidConfigVersion {
                expected: self.config_version,
                got: message.config_version,
            });
        }

        Ok(())
    }

    fn validate_message_receipt(
        &self,
        message: &BridgeSettlementMessage,
    ) -> Result<(), BridgeRelayError> {
        if message.tx_receipt_status != TX_RECEIPT_SUCCESS {
            return Err(BridgeRelayError::InvalidTransactionReceipt {
                status: message.tx_receipt_status,
            });
        }

        Ok(())
    }

    fn validate_message_domain(
        &self,
        message: &BridgeSettlementMessage,
        now_ts: u64,
        current_chain_id: u64,
        self_bridge: [u8; 20],
    ) -> Result<(), BridgeRelayError> {
        if message.target_chain_id != current_chain_id {
            return Err(BridgeRelayError::InvalidTargetChain {
                expected: current_chain_id,
                got: message.target_chain_id,
            });
        }

        if message.target_bridge != self_bridge {
            return Err(BridgeRelayError::InvalidTargetBridge {
                expected: self_bridge,
                got: message.target_bridge,
            });
        }

        if now_ts > message.deadline {
            return Err(BridgeRelayError::ProofExpired {
                now_ts,
                deadline: message.deadline,
            });
        }

        Ok(())
    }

    fn validate_validator_signatures(
        &self,
        message: &BridgeSettlementMessage,
        signatures: &[Vec<u8>],
    ) -> Result<usize, BridgeRelayError> {
        let message_digest = hash_message(message);
        let mut seen = HashSet::new();

        for signature in signatures {
            let (validator, provided_signature) = parse_validator_signature(signature)?;

            if !self.validators.contains(&validator) {
                return Err(BridgeRelayError::UnknownValidator { validator });
            }
            if !seen.insert(validator) {
                return Err(BridgeRelayError::DuplicateValidatorSignature { validator });
            }

            let verifier = VerifyingKey::from_bytes(&validator).map_err(|_| {
                BridgeRelayError::InvalidValidatorSignature {
                    validator,
                    expected: [0u8; 32],
                    got: [0u8; 32],
                }
            })?;

            if verifier
                .verify(message_digest.as_slice(), &provided_signature)
                .is_err()
            {
                return Err(BridgeRelayError::InvalidValidatorSignature {
                    validator,
                    expected: [0u8; 32],
                    got: {
                        let bytes = provided_signature.to_bytes();
                        let mut got = [0u8; 32];
                        got.copy_from_slice(&bytes[..32]);
                        got
                    },
                });
            }
        }

        Ok(seen.len())
    }
}

fn parse_validator_signature(signature: &[u8]) -> Result<([u8; 32], Signature), BridgeRelayError> {
    if signature.len() != VALIDATOR_SIGNATURE_LEN {
        return Err(BridgeRelayError::InvalidValidatorSignatureLength {
            got: signature.len(),
        });
    }

    let mut validator = [0u8; VALIDATOR_SIGNATURE_KEY_LEN];
    validator.copy_from_slice(&signature[..VALIDATOR_SIGNATURE_KEY_LEN]);

    let mut raw_signature = [0u8; VALIDATOR_SIGNATURE_BYTES_LEN];
    raw_signature.copy_from_slice(&signature[VALIDATOR_SIGNATURE_KEY_LEN..]);

    Ok((validator, Signature::from_bytes(&raw_signature)))
}

pub fn nonce_key(
    source_chain_id: u64,
    source_bridge_id: [u8; 32],
    target_chain_id: u64,
    target_bridge: [u8; 20],
    action: [u8; 32],
    nonce: u64,
) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(source_chain_id.to_be_bytes());
    hasher.update(source_bridge_id);
    hasher.update(target_chain_id.to_be_bytes());
    hasher.update(target_bridge);
    hasher.update(action);
    hasher.update(nonce.to_be_bytes());
    hasher.finalize().into()
}

fn hex32(value: &[u8]) -> String {
    let mut out = String::with_capacity(value.len() * 2);
    for byte in value {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

pub fn settlement_id(message: &BridgeSettlementMessage) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(message.source_chain_id.to_be_bytes());
    hasher.update(message.source_bridge_id);
    hasher.update(message.source_tx_hash);
    hasher.update(message.source_log_index.to_be_bytes());
    hasher.update(message.target_chain_id.to_be_bytes());
    hasher.update(message.target_bridge);
    hasher.update(message.receiver);
    hasher.update(message.asset);
    hasher.update(message.amount.to_be_bytes());
    hasher.finalize().into()
}

pub fn hash_message(message: &BridgeSettlementMessage) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(message.source_chain_id.to_be_bytes());
    hasher.update(message.source_bridge_id);
    hasher.update(message.source_tx_hash);
    hasher.update(message.source_log_index.to_be_bytes());
    hasher.update(message.target_chain_id.to_be_bytes());
    hasher.update(message.target_bridge);
    hasher.update(message.receiver);
    hasher.update(message.asset);
    hasher.update(message.amount.to_be_bytes());
    hasher.update(message.nonce.to_be_bytes());
    hasher.update(message.deadline.to_be_bytes());
    hasher.update(message.tx_receipt_status.to_be_bytes());
    hasher.update(message.config_version.to_be_bytes());
    hasher.finalize().into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::Signer;

    fn addr(b: u8) -> [u8; 20] {
        [b; 20]
    }

    fn b32(b: u8) -> [u8; 32] {
        [b; 32]
    }

    fn validator_pub(seed: u8) -> [u8; 32] {
        let seed_bytes = [seed; 32];
        let signing_key = ed25519_dalek::SigningKey::from_bytes(&seed_bytes);
        signing_key.verifying_key().to_bytes()
    }

    fn sig_for(message: &BridgeSettlementMessage, seed: u8) -> Vec<u8> {
        let seed_bytes = [seed; 32];
        let signing_key = ed25519_dalek::SigningKey::from_bytes(&seed_bytes);
        let signature = signing_key.sign(message_digest_bytes(message).as_slice());
        let signature_bytes = signature.to_bytes();

        let mut out = [0u8; VALIDATOR_SIGNATURE_LEN];
        out[..VALIDATOR_SIGNATURE_KEY_LEN].copy_from_slice(&signing_key.verifying_key().to_bytes());
        out[VALIDATOR_SIGNATURE_KEY_LEN..].copy_from_slice(&signature_bytes);
        out.to_vec()
    }

    fn message_digest_bytes(message: &BridgeSettlementMessage) -> [u8; 32] {
        hash_message(message)
    }

    fn relay(min_validator_signatures: usize, validators: &[u8]) -> BridgeRelay {
        BridgeRelay::new(
            min_validator_signatures,
            validators.iter().copied().map(validator_pub),
        )
    }

    fn sample_msg() -> BridgeSettlementMessage {
        BridgeSettlementMessage {
            source_chain_id: 1,
            source_bridge_id: b32(1),
            source_tx_hash: b32(2),
            source_log_index: 7,
            target_chain_id: 31337,
            target_bridge: addr(9),
            receiver: addr(3),
            asset: addr(4),
            amount: 42,
            nonce: 99,
            deadline: 1_000,
            tx_receipt_status: TX_RECEIPT_SUCCESS,
            config_version: 1,
        }
    }

    #[test]
    fn nonce_domain_isolation_and_replay_protection() {
        let mut relay = relay(1, &[7]);

        let n1 = relay
            .consume_nonce(1, b32(1), 31337, addr(9), action_settlement_finalize(), 10)
            .unwrap();

        let n2 = relay
            .consume_nonce(1, b32(1), 31337, addr(9), b32(55), 10)
            .unwrap();

        assert_ne!(n1, n2, "different action should isolate nonce domains");

        let n3 = relay
            .consume_nonce(1, b32(2), 31337, addr(9), action_settlement_finalize(), 10)
            .unwrap();
        let n4 = relay
            .consume_nonce(1, b32(1), 31337, addr(10), action_settlement_finalize(), 10)
            .unwrap();
        let n5 = relay
            .consume_nonce(1, b32(1), 31338, addr(9), action_settlement_finalize(), 10)
            .unwrap();

        assert_ne!(
            n1, n3,
            "different source bridge ids should isolate nonce domains"
        );
        assert_ne!(
            n1, n4,
            "different target bridges should isolate nonce domains"
        );
        assert_ne!(
            n1, n5,
            "different target chain ids should isolate nonce domains"
        );
        assert_ne!(
            n3, n4,
            "source and target bridge domains should stay independently isolated"
        );
        assert_ne!(
            n4, n5,
            "target bridge and target chain must stay independently isolated"
        );

        let err = relay
            .consume_nonce(1, b32(1), 31337, addr(9), action_settlement_finalize(), 10)
            .unwrap_err();
        assert!(matches!(err, BridgeRelayError::NonceAlreadyUsed { .. }));
    }

    #[test]
    fn fail_closed_on_expired_proof() {
        let mut relay = relay(1, &[1]);
        let msg = sample_msg();
        let err = relay
            .submit_proof(&msg, &[sig_for(&msg, 1)], 900, 901, 31337, addr(9))
            .unwrap_err();
        assert!(matches!(err, BridgeRelayError::ProofExpired { .. }));
    }

    #[test]
    fn fail_closed_on_deadline_witness_mismatch() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        let err = relay
            .submit_proof(
                &msg,
                &[sig_for(&msg, 7)],
                msg.deadline - 1,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::DeadlineWitnessMismatch {
                expected,
                got,
            } if expected == msg.deadline && got == msg.deadline - 1
        ));
        assert!(
            relay.audit_log().is_empty(),
            "mismatched deadline must not append audit events"
        );
    }

    #[test]
    fn fail_closed_on_duplicate_finalize() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let err = relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        let expected_settlement_id = settlement_id(&msg);
        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == expected_settlement_id
        ));
    }

    #[test]
    fn finalize_settlement_replay_with_new_nonce_still_rejects_terminal_state() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let mut replay = sample_msg();
        replay.nonce = msg.nonce + 1;

        let err = relay
            .finalize_settlement(&replay, &[sig_for(&replay, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        let expected_settlement_id = settlement_id(&msg);
        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == expected_settlement_id
        ));
    }

    #[test]
    fn submit_proof_replay_after_finalize_stays_proof_replay_bound() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let proof_digest = hash_message(&msg);
        let err = relay
            .submit_proof(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::ProofAlreadyUsed { proof_digest: used } if used == proof_digest
        ));
    }

    #[test]
    fn proof_replay_rejection_is_side_effect_free() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        let proof_digest = relay
            .submit_proof(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();
        let audit_len_before = relay.audit_log().len();

        let err = relay
            .submit_proof(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::ProofAlreadyUsed { proof_digest: used } if used == proof_digest
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "proof replay rejection must not append duplicate audit events"
        );

        let mut fresh_msg = sample_msg();
        fresh_msg.source_log_index += 1;
        fresh_msg.nonce += 1;

        let fresh_proof = relay
            .submit_proof(
                &fresh_msg,
                &[sig_for(&fresh_msg, 7)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();
        assert_ne!(fresh_proof, proof_digest);
    }

    #[test]
    fn finalize_settlement_replay_with_invalid_signature_after_terminal_still_blocked_by_terminal_bound(
    ) {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let err = relay
            .finalize_settlement(&msg, &[vec![0u8; 64]], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        let expected_settlement_id = settlement_id(&msg);
        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == expected_settlement_id
        ));
    }

    #[test]
    fn duplicate_finalize_is_side_effect_free() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let audit_len_before = relay.audit_log().len();
        let err = relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == settlement_id(&msg)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "duplicate finalize must not append audit events"
        );
    }

    #[test]
    fn duplicate_finalize_with_bad_receipt_still_stops_at_terminal_state() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let audit_len_before = relay.audit_log().len();
        let mut replay = sample_msg();
        replay.tx_receipt_status = 0;

        let err = relay
            .finalize_settlement(&replay, &[sig_for(&replay, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == settlement_id(&msg)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "terminal duplicate finalize must stay side-effect free even with a bad receipt"
        );
    }

    #[test]
    fn duplicate_finalize_with_fresh_nonce_and_bad_receipt_still_stops_at_terminal_state() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let audit_len_before = relay.audit_log().len();
        let mut replay = sample_msg();
        replay.nonce = msg.nonce + 1;
        replay.tx_receipt_status = 0;

        let err = relay
            .finalize_settlement(&replay, &[sig_for(&replay, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == settlement_id(&msg)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "terminal duplicate finalize must stay side-effect free even with a fresh nonce and bad receipt"
        );
    }

    #[test]
    fn duplicate_finalize_with_stale_config_version_after_governance_change_still_stops_at_terminal_state(
    ) {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        relay
            .set_validators(&b32(9), vec![validator_pub(7), validator_pub(8)])
            .unwrap();
        relay.set_min_validator_signatures(&b32(9), 2).unwrap();

        let mut msg = sample_msg();
        msg.config_version = relay.config_version();

        relay
            .finalize_settlement(
                &msg,
                &[sig_for(&msg, 7), sig_for(&msg, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();

        relay.set_admin(&b32(9), b32(10)).unwrap();

        let audit_len_before = relay.audit_log().len();
        let err = relay
            .finalize_settlement(
                &msg,
                &[sig_for(&msg, 7), sig_for(&msg, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == settlement_id(&msg)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "terminal duplicate finalize must win over stale config version after governance change"
        );
    }

    #[test]
    fn duplicate_finalize_with_fresh_config_version_after_governance_change_still_stops_at_terminal_state(
    ) {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        relay
            .set_validators(&b32(9), vec![validator_pub(7), validator_pub(8)])
            .unwrap();
        relay.set_min_validator_signatures(&b32(9), 2).unwrap();

        let mut msg = sample_msg();
        msg.config_version = relay.config_version();

        relay
            .finalize_settlement(
                &msg,
                &[sig_for(&msg, 7), sig_for(&msg, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();

        relay.set_admin(&b32(9), b32(10)).unwrap();

        let audit_len_before = relay.audit_log().len();
        let mut replay = msg.clone();
        replay.config_version = relay.config_version();
        replay.nonce += 1;

        let err = relay
            .finalize_settlement(
                &replay,
                &[sig_for(&replay, 7), sig_for(&replay, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == settlement_id(&msg)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "fresh config version must not bypass settlement terminal state after governance drift"
        );
    }

    #[test]
    fn fail_closed_on_chain_domain_mismatch() {
        let mut relay = relay(1, &[7]);
        let mut msg = sample_msg();
        msg.target_chain_id = 10;

        let err = relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(err, BridgeRelayError::InvalidTargetChain { .. }));
    }

    #[test]
    fn finalize_settlement_rejects_target_bridge_mismatch_without_audit_side_effects() {
        let mut relay = relay(1, &[7]);
        let mut msg = sample_msg();
        msg.target_bridge = addr(8);
        let audit_len_before = relay.audit_log().len();

        let err = relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidTargetBridge { expected, got }
                if expected == addr(9) && got == addr(8)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "target bridge mismatch must not append proof/nonce/finalize audit events"
        );
    }

    #[test]
    fn duplicate_validator_signatures_do_not_count_twice() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        let err = relay
            .submit_proof(
                &msg,
                &[sig_for(&msg, 7), sig_for(&msg, 7)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::DuplicateValidatorSignature { validator } if validator == validator_pub(7)
        ));
    }

    #[test]
    fn governance_like_admin_can_rotate_validator_set_and_threshold() {
        let mut relay = BridgeRelay::with_admin(1, vec![validator_pub(7)], b32(9));

        relay
            .set_validators(&b32(9), vec![validator_pub(7), validator_pub(8)])
            .unwrap();
        relay.set_min_validator_signatures(&b32(9), 2).unwrap();

        let mut msg = sample_msg();
        msg.config_version = 3;
        let sigs = vec![sig_for(&msg, 7), sig_for(&msg, 8)];

        relay
            .submit_proof(&msg, &sigs, 1_000, 999, 31337, addr(9))
            .unwrap();

        let mut fallback = sample_msg();
        fallback.config_version = 3;
        fallback.nonce = 100;
        let err = relay
            .submit_proof(
                &fallback,
                &[sig_for(&fallback, 7)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::NotEnoughValidatorSignatures {
                required,
                ..
            } if required == 2
        ));
    }

    #[test]
    fn governance_like_admin_restrains_configuration_changes() {
        let mut relay = BridgeRelay::with_admin(1, vec![validator_pub(7)], b32(9));

        let err = relay.set_min_validator_signatures(&b32(8), 2).unwrap_err();
        assert!(matches!(err, BridgeRelayError::Unauthorized));
    }

    #[test]
    fn governance_like_admin_rejects_zero_min_signatures() {
        let mut relay = relay(1, &[7]);
        let err = relay.set_min_validator_signatures(&b32(7), 0).unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorConfiguration {
                min_validator_signatures: 0,
                available_validators: 1,
            }
        ));
    }

    #[test]
    fn governance_like_admin_rejects_validator_set_below_threshold() {
        let mut relay = relay(2, &[7]);
        let err = relay
            .set_validators(&b32(7), vec![validator_pub(7)])
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorConfiguration {
                min_validator_signatures: 2,
                available_validators: 1,
            }
        ));
    }

    #[test]
    fn governance_like_admin_rejects_empty_validator_set() {
        let mut relay = relay(1, &[7]);
        let err = relay
            .set_validators(&b32(7), Vec::<[u8; 32]>::new())
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorConfiguration {
                min_validator_signatures: 1,
                available_validators: 0,
            }
        ));
    }

    #[test]
    fn governance_like_admin_rejects_configuration_without_validators() {
        let mut relay = BridgeRelay::with_admin(1, vec![], b32(9));
        let err = relay.set_min_validator_signatures(&b32(9), 1).unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorConfiguration {
                min_validator_signatures: 1,
                available_validators: 0,
            }
        ));
    }

    #[test]
    fn submit_proof_rejects_invalid_validator_signature_configuration() {
        let mut relay = BridgeRelay::with_admin(0, vec![validator_pub(7)], b32(9));
        let msg = sample_msg();

        let err = relay
            .submit_proof(&msg, &[], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorConfiguration {
                min_validator_signatures: 0,
                available_validators: 1,
            }
        ));
    }

    #[test]
    fn unknown_validator_signature_fails_closed() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        let err = relay
            .submit_proof(&msg, &[sig_for(&msg, 8)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::UnknownValidator { validator } if validator == validator_pub(8)
        ));
    }

    #[test]
    fn invalid_validator_pubkey_is_rejected_even_if_allowlisted() {
        let mut relay = BridgeRelay::with_admin(1, vec![[0u8; 32]], b32(9));
        let msg = sample_msg();
        let mut bad_sig = sig_for(&msg, 7);
        bad_sig[..VALIDATOR_SIGNATURE_KEY_LEN].fill(0);

        let err = relay
            .submit_proof(&msg, &[bad_sig], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorSignature {
                validator,
                ..
            } if validator == [0u8; 32]
        ));
    }

    #[test]
    fn malformed_signature_length_is_rejected() {
        let mut relay = relay(1, &[9]);
        let msg = sample_msg();
        let short = vec![9u8; 63];

        let err = relay
            .submit_proof(&msg, &[short], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorSignatureLength { got } if got == 63
        ));
    }

    #[test]
    fn invalid_signature_binding_is_rejected() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();
        let mut modified = sample_msg();
        modified.amount = 999;

        let bad_sig = sig_for(&msg, 7);
        let err = relay
            .submit_proof(&modified, &[bad_sig], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidValidatorSignature { validator, .. } if validator == validator_pub(7)
        ));
    }

    #[test]
    fn submit_proof_rejects_non_success_tx_receipt() {
        let mut relay = relay(1, &[7]);
        let mut msg = sample_msg();
        msg.tx_receipt_status = 0;

        let err = relay
            .submit_proof(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidTransactionReceipt { status: 0 }
        ));

        let mut good_msg = sample_msg();
        good_msg.nonce = 100;
        good_msg.config_version = 1;
        let proof = relay
            .submit_proof(
                &good_msg,
                &[sig_for(&good_msg, 7)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();
        assert!(!proof.iter().all(|b| *b == 0));
    }

    #[test]
    fn finalize_settlement_rejects_non_success_tx_receipt() {
        let mut relay = relay(1, &[7]);
        let mut msg = sample_msg();
        msg.tx_receipt_status = 0;
        let audit_len_before = relay.audit_log().len();

        let err = relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidTransactionReceipt { status: 0 }
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "bad receipt must fail closed without proof/nonce/finalize audit side effects"
        );

        let mut settled_msg = sample_msg();
        settled_msg.nonce = 11;
        let settlement_id = relay
            .finalize_settlement(
                &settled_msg,
                &[sig_for(&settled_msg, 7)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();

        let fallback = relay
            .consume_nonce(
                settled_msg.source_chain_id,
                settled_msg.source_bridge_id,
                settled_msg.target_chain_id,
                settled_msg.target_bridge,
                action_settlement_finalize(),
                settled_msg.nonce,
            )
            .unwrap_err();
        assert!(matches!(
            fallback,
            BridgeRelayError::NonceAlreadyUsed { .. }
        ));

        assert_ne!(settlement_id, [0u8; 32]);
    }

    #[test]
    fn finalize_settlement_nonce_collision_rolls_back_proof_side_effects() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();

        relay
            .consume_nonce(
                msg.source_chain_id,
                msg.source_bridge_id,
                msg.target_chain_id,
                msg.target_bridge,
                action_settlement_finalize(),
                msg.nonce,
            )
            .unwrap();
        let audit_len_before = relay.audit_log().len();
        let normalized_before = relay.normalized_audit_log();

        let err = relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();
        assert!(matches!(err, BridgeRelayError::NonceAlreadyUsed { .. }));
        assert_eq!(relay.audit_log().len(), audit_len_before);
        assert_eq!(relay.normalized_audit_log(), normalized_before);

        let proof_digest = relay
            .submit_proof(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();
        assert_eq!(proof_digest, hash_message(&msg));
    }

    #[test]
    fn submit_proof_rejects_wrong_config_version() {
        let mut relay = relay(1, &[7]);
        let mut msg = sample_msg();
        msg.config_version = 2;

        let err = relay
            .submit_proof(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion {
                expected: 1,
                got: 2
            }
        ));
    }

    #[test]
    fn config_version_bumps_on_admin_rotation() {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        relay
            .set_validators(&b32(9), vec![validator_pub(7), validator_pub(8)])
            .unwrap();
        relay.set_min_validator_signatures(&b32(9), 2).unwrap();

        let mut stale = sample_msg();
        relay.set_admin(&b32(9), b32(10)).unwrap();

        stale.nonce = 123;
        let err = relay
            .submit_proof(
                &stale,
                &[sig_for(&stale, 7), sig_for(&stale, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        let expected_version = relay.config_version;
        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion {
                expected,
                got: 1,
            } if expected == expected_version
        ));

        let mut fresh_msg = sample_msg();
        fresh_msg.config_version = expected_version;
        fresh_msg.nonce = 123;

        let proof = relay
            .submit_proof(
                &fresh_msg,
                &[sig_for(&fresh_msg, 7), sig_for(&fresh_msg, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();
        assert!(!proof.iter().all(|b| *b == 0));
    }

    #[test]
    fn finalize_settlement_rejects_stale_config_version_after_governance_change() {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        relay
            .set_validators(&b32(9), vec![validator_pub(7), validator_pub(8)])
            .unwrap();
        relay.set_min_validator_signatures(&b32(9), 2).unwrap();

        let mut stale = sample_msg();
        stale.nonce = 123;

        relay.set_admin(&b32(9), b32(10)).unwrap();

        let audit_len_before = relay.audit_log().len();
        let err = relay
            .finalize_settlement(
                &stale,
                &[sig_for(&stale, 7), sig_for(&stale, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        let expected_version = relay.config_version();
        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion { expected, got: 1 }
                if expected == expected_version
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "stale finalize must not append proof/nonce/finalize audit side effects"
        );

        let mut fresh = sample_msg();
        fresh.config_version = expected_version;
        fresh.nonce = 123;

        let finalized_settlement_id = relay
            .finalize_settlement(
                &fresh,
                &[sig_for(&fresh, 7), sig_for(&fresh, 8)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();
        assert_eq!(finalized_settlement_id, settlement_id(&fresh));
    }

    #[test]
    fn config_version_gating_rejects_stale_expected_version() {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        relay
            .set_validators_with_version(
                &b32(9),
                relay.config_version(),
                vec![validator_pub(7), validator_pub(8)],
            )
            .unwrap();

        let expected = relay.config_version();
        relay.set_min_validator_signatures(&b32(9), 2).unwrap();

        let err = relay
            .set_admin_with_version(&b32(9), expected, b32(10))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion {
                expected: current,
                got,
            } if current > 0 && got == expected
        ));
    }

    #[test]
    fn governance_write_with_stale_config_version_is_fail_closed_and_side_effect_free() {
        let mut relay = BridgeRelay::with_admin(1, vec![validator_pub(7)], b32(9));
        let audit_len_before = relay.audit_log().len();
        let admin_before = b32(9);

        relay
            .set_validators_with_version(
                &admin_before,
                relay.config_version(),
                vec![validator_pub(7), validator_pub(8)],
            )
            .unwrap();
        let stale_version = relay.config_version();

        relay
            .set_min_validator_signatures(&admin_before, 2)
            .unwrap();

        let audit_len_after_rotation = relay.audit_log().len();
        let current_version = relay.config_version();

        let err = relay
            .set_admin_with_version(&admin_before, stale_version, b32(10))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion {
                expected,
                got,
            } if expected == current_version && got == stale_version
        ));
        assert_eq!(
            relay.admin, admin_before,
            "stale write must not rotate admin"
        );
        assert_eq!(
            relay.config_version(),
            current_version,
            "stale write must not mutate config version"
        );
        assert_eq!(
            relay.audit_log().len(),
            audit_len_after_rotation,
            "stale write must not append governance audit events"
        );
        assert!(
            audit_len_after_rotation > audit_len_before,
            "control step should have produced governance audit events"
        );
    }

    #[test]
    fn stale_min_signature_update_is_fail_closed_and_side_effect_free() {
        let mut relay = BridgeRelay::with_admin(1, vec![validator_pub(7)], b32(9));
        let admin = b32(9);

        relay
            .set_validators_with_version(
                &admin,
                relay.config_version(),
                vec![validator_pub(7), validator_pub(8)],
            )
            .unwrap();
        let stale_version = relay.config_version();

        relay.set_admin(&admin, b32(10)).unwrap();

        let current_version = relay.config_version();
        let audit_len_before = relay.audit_log().len();
        let normalized_before = relay.normalized_audit_log();

        let err = relay
            .set_min_validator_signatures_with_version(&b32(10), stale_version, 2)
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion { expected, got }
                if expected == current_version && got == stale_version
        ));
        assert_eq!(
            relay.min_validator_signatures, 1,
            "stale threshold write must leave the previous quorum intact"
        );
        assert_eq!(
            relay.config_version(),
            current_version,
            "stale threshold write must not mutate config version"
        );
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "stale threshold write must not append governance audit events"
        );
        assert_eq!(
            relay.normalized_audit_log(),
            normalized_before,
            "stale threshold write must not append normalized governance audit events"
        );
    }

    #[test]
    fn stale_validator_rotation_is_fail_closed_and_side_effect_free() {
        let mut relay = BridgeRelay::with_admin(1, vec![validator_pub(7)], b32(9));
        let admin = b32(9);

        relay
            .set_validators_with_version(
                &admin,
                relay.config_version(),
                vec![validator_pub(7), validator_pub(8)],
            )
            .unwrap();
        let stale_version = relay.config_version();

        relay.set_min_validator_signatures(&admin, 2).unwrap();

        let current_version = relay.config_version();
        let audit_len_before = relay.audit_log().len();
        let normalized_before = relay.normalized_audit_log();

        let mut stale = sample_msg();
        stale.config_version = stale_version;
        stale.nonce = 124;

        let err = relay
            .submit_proof(&stale, &[sig_for(&stale, 7)], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion { expected, got }
                if expected == current_version && got == stale_version
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "stale proof must not append audit events"
        );
        assert_eq!(
            relay.normalized_audit_log(),
            normalized_before,
            "stale proof must not append normalized audit events"
        );

        let err = relay
            .set_validators_with_version(&admin, stale_version, vec![validator_pub(7)])
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion { expected, got }
                if expected == current_version && got == stale_version
        ));
        assert_eq!(relay.config_version(), current_version);
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "stale validator rotation must not append governance audit events"
        );
        assert_eq!(
            relay.normalized_audit_log(),
            normalized_before,
            "stale validator rotation must not append normalized governance audit events"
        );

        let mut rotated = sample_msg();
        rotated.config_version = current_version;
        rotated.nonce = 125;
        let err = relay
            .submit_proof(
                &rotated,
                &[sig_for(&rotated, 7)],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::NotEnoughValidatorSignatures {
                required: 2,
                got: 1,
            }
        ));

        let mut retained_validator = sample_msg();
        retained_validator.config_version = current_version;
        retained_validator.nonce = 126;
        let proof_digest = relay
            .submit_proof(
                &retained_validator,
                &[
                    sig_for(&retained_validator, 7),
                    sig_for(&retained_validator, 8),
                ],
                1_000,
                999,
                31337,
                addr(9),
            )
            .expect("stale validator rotation must leave the prior validator set intact");
        assert!(
            relay.proof_used.contains(&proof_digest),
            "successful proof should confirm validator 8 remained allowlisted"
        );
    }

    #[test]
    fn stale_validator_rotation_does_not_admit_new_validator() {
        let mut relay =
            BridgeRelay::with_admin(2, vec![validator_pub(7), validator_pub(8)], b32(9));
        let admin = b32(9);
        let stale_version = relay.config_version();

        relay
            .set_min_validator_signatures_with_version(&admin, stale_version, 2)
            .unwrap();

        let current_version = relay.config_version();
        let audit_len_before = relay.audit_log().len();
        let normalized_before = relay.normalized_audit_log();

        let err = relay
            .set_validators_with_version(
                &admin,
                stale_version,
                vec![validator_pub(7), validator_pub(9)],
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::InvalidConfigVersion { expected, got }
                if expected == current_version && got == stale_version
        ));
        assert!(
            relay.validators.contains(&validator_pub(8)),
            "stale rotation must keep the previously allowlisted validator"
        );
        assert!(
            !relay.validators.contains(&validator_pub(9)),
            "stale rotation must not admit a newly proposed validator"
        );
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before,
            "stale rotation must not append governance audit events"
        );
        assert_eq!(
            relay.normalized_audit_log(),
            normalized_before,
            "stale rotation must not append normalized governance audit events"
        );

        let mut swapped_validator = sample_msg();
        swapped_validator.config_version = current_version;
        swapped_validator.nonce = 127;
        let audit_len_before_unknown_validator = relay.audit_log().len();
        let normalized_before_unknown_validator = relay.normalized_audit_log();

        let err = relay
            .submit_proof(
                &swapped_validator,
                &[
                    sig_for(&swapped_validator, 7),
                    sig_for(&swapped_validator, 9),
                ],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::UnknownValidator { validator }
                if validator == validator_pub(9)
        ));
        assert_eq!(
            relay.audit_log().len(),
            audit_len_before_unknown_validator,
            "unknown validator proof must not append audit events"
        );
        assert_eq!(
            relay.normalized_audit_log(),
            normalized_before_unknown_validator,
            "unknown validator proof must not append normalized audit events"
        );

        let mut retained_validator = sample_msg();
        retained_validator.config_version = current_version;
        retained_validator.nonce = 128;
        let proof_digest = relay
            .submit_proof(
                &retained_validator,
                &[
                    sig_for(&retained_validator, 7),
                    sig_for(&retained_validator, 8),
                ],
                1_000,
                999,
                31337,
                addr(9),
            )
            .expect("stale rotation must preserve the prior validator membership");
        assert!(
            relay.proof_used.contains(&proof_digest),
            "successful proof should confirm validator 8 remained allowlisted"
        );
    }

    #[test]
    fn config_version_gating_accepts_matching_version() {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        let expected = relay.config_version();

        relay
            .set_validators_with_version(
                &b32(9),
                expected,
                vec![validator_pub(7), validator_pub(8)],
            )
            .unwrap();

        assert_eq!(relay.config_version(), expected + 1);

        relay
            .set_min_validator_signatures_with_version(&b32(9), expected + 1, 2)
            .unwrap();
        assert_eq!(relay.config_version(), expected + 2);
    }

    #[test]
    fn settlement_id_is_scoped_to_domain_fields() {
        let msg_a = sample_msg();
        let mut msg_b = sample_msg();
        let mut msg_c = sample_msg();
        let mut msg_d = sample_msg();

        msg_b.source_bridge_id = [11u8; 32];
        msg_c.target_bridge = [12u8; 20];
        msg_d.target_chain_id = 4_200;

        let source_settlement_id = settlement_id(&msg_a);
        assert_ne!(source_settlement_id, settlement_id(&msg_b));
        assert_ne!(source_settlement_id, settlement_id(&msg_c));
        assert_ne!(source_settlement_id, settlement_id(&msg_d));
    }

    #[test]
    fn finalize_settlement_is_idempotent_by_settlement_id_even_with_new_nonce() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();
        let sig = vec![sig_for(&msg, 7)];

        let _ = relay
            .finalize_settlement(&msg, &sig, 1_000, 999, 31337, addr(9))
            .unwrap();

        let mut replay_msg = sample_msg();
        replay_msg.nonce = msg.nonce + 1;

        let replay_sig = sig_for(&replay_msg, 7);
        let err = relay
            .finalize_settlement(&replay_msg, &vec![replay_sig], 1_000, 999, 31337, addr(9))
            .unwrap_err();

        assert!(matches!(
            err,
            BridgeRelayError::SettlementAlreadyFinalized { settlement_id: id }
                if id == settlement_id(&msg)
        ));
    }

    #[test]
    fn audit_log_records_admin_and_settlement_flow() {
        let mut relay = BridgeRelay::with_admin(2, vec![validator_pub(7)], b32(9));
        let old_admin = b32(9);
        let new_admin = b32(11);

        relay.set_admin(&old_admin, new_admin).unwrap();
        relay
            .set_validators(&new_admin, vec![validator_pub(7), validator_pub(8)])
            .unwrap();
        relay.set_min_validator_signatures(&new_admin, 2).unwrap();

        let mut msg = sample_msg();
        msg.config_version = 4;
        let sigs = vec![sig_for(&msg, 7), sig_for(&msg, 8)];

        let proof_digest = relay
            .submit_proof(&msg, &sigs, 1_000, 999, 31337, addr(9))
            .unwrap();
        let mut replay_msg = sample_msg();
        replay_msg.nonce += 1;
        let replay_sig = sig_for(&replay_msg, 7);
        let replay_sig2 = sig_for(&replay_msg, 8);
        let mut relay2 =
            BridgeRelay::with_admin(2, vec![validator_pub(7), validator_pub(8)], b32(9));
        relay2
            .finalize_settlement(
                &replay_msg,
                &[replay_sig, replay_sig2],
                1_000,
                999,
                31337,
                addr(9),
            )
            .unwrap();

        let logs = relay.audit_log();
        assert!(logs
            .iter()
            .any(|e| matches!(e, BridgeRelayEvent::ValidatorsUpdated { .. })));
        assert!(logs
            .iter()
            .any(|e| matches!(e, BridgeRelayEvent::MinSignaturesUpdated { .. })));
        assert!(logs
            .iter()
            .any(|e| matches!(e, BridgeRelayEvent::ProofSubmitted { .. })));
        assert!(logs.iter().any(|e| matches!(e, BridgeRelayEvent::ProofSubmittedAndStored { proof_digest: d } if *d == proof_digest)));

        let normalized = relay.normalized_audit_log();
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.proof_submitted"
                && event.object_id.as_deref() == Some(hex32(&proof_digest).as_str())
                && event.amount == Some(2)
                && event.note.as_deref() == Some("proof submitted")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.proof_submitted_and_stored"
                && event.object_id.as_deref() == Some(hex32(&proof_digest).as_str())
                && event.note.as_deref() == Some("proof stored")
        }));
        assert!(normalized
            .iter()
            .any(|event| event.source == "bridge-relay"));
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.admin_updated"
                && event.actor == Some(hex32(&old_admin))
                && event.object_id == Some(hex32(&new_admin))
                && event.related_id == Some(hex32(&old_admin))
                && event.reason.as_deref() == Some("admin_rotation")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.config_version_updated"
                && event.actor == Some(hex32(&new_admin))
                && event.amount == Some(4)
                && event.object_id.as_deref() == Some("bridge_config")
                && event.related_id.as_deref() == Some("config_version")
                && event.reason.as_deref() == Some("config_version_rotation")
                && event.note.as_deref() == Some("old_version=3, new_version=4")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.min_signatures_updated"
                && event.actor == Some(hex32(&new_admin))
                && event.amount == Some(2)
                && event.object_id.as_deref() == Some("bridge_config")
                && event.related_id.as_deref() == Some("min_signatures")
                && event.reason.as_deref() == Some("validator_threshold_rotation")
                && event.note.as_deref() == Some("old_min=2, new_min=2")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.validators_updated"
                && event.actor == Some(hex32(&new_admin))
                && event.amount == Some(2)
                && event.object_id.as_deref() == Some("bridge_config")
                && event.related_id.as_deref() == Some("validators")
                && event.reason.as_deref() == Some("validator_set_rotation")
                && event.note.as_deref() == Some("previous_count=1, new_count=2")
        }));

        relay.consume_audit_log().into_iter().for_each(|event| {
            if let BridgeRelayEvent::ProofSubmittedAndStored {
                proof_digest: stored_digest,
            } = event
            {
                assert_eq!(stored_digest, proof_digest);
            }
        });

        assert!(relay.audit_log().is_empty());
    }

    #[test]
    fn normalized_audit_log_keeps_finalize_and_nonce_binding() {
        let mut relay = relay(1, &[7]);
        let msg = sample_msg();
        let proof_digest = hash_message(&msg);
        let expected_settlement_id = settlement_id(&msg);
        let expected_nonce_key = nonce_key(
            msg.source_chain_id,
            msg.source_bridge_id,
            msg.target_chain_id,
            msg.target_bridge,
            action_settlement_finalize(),
            msg.nonce,
        );

        relay
            .finalize_settlement(&msg, &[sig_for(&msg, 7)], 1_000, 999, 31337, addr(9))
            .unwrap();

        let normalized = relay.normalized_audit_log();
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.nonce_consumed"
                && event.object_id.as_deref() == Some(hex32(&expected_nonce_key).as_str())
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "bridge_relay.settlement_finalized"
                && event.object_id.as_deref() == Some(hex32(&expected_settlement_id).as_str())
                && event.related_id.as_deref() == Some(hex32(&proof_digest).as_str())
        }));
    }

    #[test]
    fn normalized_admin_rotation_keeps_caller_new_admin_and_old_admin_roles() {
        let old_admin = b32(7);
        let new_admin = b32(8);
        let mut relay = BridgeRelay::with_admin(1, [validator_pub(7)], old_admin);

        relay.set_admin(&old_admin, new_admin).unwrap();

        let normalized = relay.normalized_audit_log();
        let event = normalized
            .iter()
            .find(|event| event.event_type == "bridge_relay.admin_updated")
            .expect("admin rotation should normalize");

        assert_eq!(event.actor.as_deref(), Some(hex32(&old_admin).as_str()));
        assert_eq!(event.object_id.as_deref(), Some(hex32(&new_admin).as_str()));
        assert_eq!(
            event.related_id.as_deref(),
            Some(hex32(&old_admin).as_str())
        );
        assert_eq!(event.reason.as_deref(), Some("admin_rotation"));
        assert_eq!(event.amount, None);
    }
}
