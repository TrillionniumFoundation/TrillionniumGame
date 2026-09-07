#!/usr/bin/env python3
"""Apply the exact PR #103 key-epoch archive-chain repair once."""
from __future__ import annotations

from pathlib import Path

PATH = Path("crates/trnm-token-crypto-provider/src/key_epoch.rs")
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    """pub trait KeyEpochArchiveVerifier: fmt::Debug + Send + Sync {
    fn verify_checkpoint(&self, checkpoint: &KeyEpochArchiveCheckpoint) -> bool;

    fn key_id_is_absent(&self, checkpoint: &KeyEpochArchiveCheckpoint, candidate: KeyId) -> bool;
}
""",
    """pub trait KeyEpochArchiveVerifier: fmt::Debug + Send + Sync {
    /// Authenticate the complete checkpoint. For sequence > 1 this includes
    /// resolving `previous_archive_digest` and proving that the carried
    /// `highest_epoch`, `last_lifecycle_time`, and `authority_lost_epoch`
    /// witnesses do not roll back or detach from the predecessor chain.
    fn verify_checkpoint(&self, checkpoint: &KeyEpochArchiveCheckpoint) -> bool;

    /// Prove absence across the complete authenticated archive chain, not only
    /// the records retained in the current operational window.
    fn key_id_is_absent(&self, checkpoint: &KeyEpochArchiveCheckpoint, candidate: KeyId) -> bool;
}
""",
    "archive verifier contract",
)

replace_once(
    """    let highest = epochs
        .iter()
        .next_back()
        .copied()
        .ok_or(KeyEpochError::CheckpointInvalid("empty checkpoint"))?;
    if highest != checkpoint.highest_epoch {
        return Err(KeyEpochError::CheckpointInvalid(
            "highest epoch does not match checkpoint records",
        ));
    }
    if checkpoint
        .retained_records
        .iter()
        .chain(checkpoint.archived_records.iter())
        .any(|record| record.activated_at_unix_seconds > checkpoint.last_lifecycle_time)
    {
        return Err(KeyEpochError::CheckpointInvalid(
            "lifecycle time precedes an activation",
        ));
    }
    match checkpoint.active {
        Some(active) => {
            if active != checkpoint.highest_epoch
                || checkpoint.authority_lost_epoch.is_some()
                || !checkpoint
                    .retained_records
                    .iter()
                    .any(|record| record.epoch == active && record.state == KeyEpochState::Active)
            {
                return Err(KeyEpochError::CheckpointInvalid(
                    "active epoch binding is inconsistent",
                ));
            }
        }
        None => {
            if checkpoint.authority_lost_epoch != Some(checkpoint.highest_epoch)
                || !checkpoint.archived_records.iter().any(|record| {
                    record.epoch == checkpoint.highest_epoch
                        && matches!(record.state, KeyEpochState::Revoked { .. })
                })
            {
                return Err(KeyEpochError::CheckpointInvalid(
                    "authority-loss checkpoint is inconsistent",
                ));
            }
        }
    }
""",
    """    let highest_in_batch = epochs
        .iter()
        .next_back()
        .copied()
        .ok_or(KeyEpochError::CheckpointInvalid("empty checkpoint"))?;
    if highest_in_batch > checkpoint.highest_epoch {
        return Err(KeyEpochError::CheckpointInvalid(
            "checkpoint record exceeds the monotonic high-water",
        ));
    }

    let predecessor_authenticated = match (
        checkpoint.sequence,
        checkpoint.previous_archive_digest,
    ) {
        (1, None) => false,
        (1, Some(_)) => {
            return Err(KeyEpochError::CheckpointInvalid(
                "first checkpoint cannot name a predecessor",
            ));
        }
        (_, Some(digest)) if digest.iter().any(|byte| *byte != 0) => true,
        (_, Some(_)) => {
            return Err(KeyEpochError::CheckpointInvalid(
                "predecessor archive digest must not be zero",
            ));
        }
        (_, None) => {
            return Err(KeyEpochError::CheckpointInvalid(
                "chained checkpoint requires a predecessor digest",
            ));
        }
    };
    if highest_in_batch < checkpoint.highest_epoch && !predecessor_authenticated {
        return Err(KeyEpochError::CheckpointInvalid(
            "historical high-water requires an authenticated predecessor",
        ));
    }

    if checkpoint
        .retained_records
        .iter()
        .chain(checkpoint.archived_records.iter())
        .any(|record| record.activated_at_unix_seconds > checkpoint.last_lifecycle_time)
    {
        return Err(KeyEpochError::CheckpointInvalid(
            "lifecycle time precedes an activation",
        ));
    }
    match checkpoint.active {
        Some(active) => {
            if active != checkpoint.highest_epoch
                || checkpoint.authority_lost_epoch.is_some()
                || !checkpoint
                    .retained_records
                    .iter()
                    .any(|record| record.epoch == active && record.state == KeyEpochState::Active)
            {
                return Err(KeyEpochError::CheckpointInvalid(
                    "active epoch binding is inconsistent",
                ));
            }
        }
        None => {
            let highest_revoked_in_batch = checkpoint.archived_records.iter().any(|record| {
                record.epoch == checkpoint.highest_epoch
                    && matches!(record.state, KeyEpochState::Revoked { .. })
            });
            if checkpoint.authority_lost_epoch != Some(checkpoint.highest_epoch)
                || (!highest_revoked_in_batch && !predecessor_authenticated)
            {
                return Err(KeyEpochError::CheckpointInvalid(
                    "authority-loss checkpoint is inconsistent",
                ));
            }
        }
    }
""",
    "checkpoint shape high-water logic",
)

replace_once(
    """    fn verifier(digest: u8) -> ExactArchiveVerifier {
        ExactArchiveVerifier {
            digest,
            denied_key: None,
        }
    }

    fn epoch(value: u64) -> KeyEpoch {
""",
    """    fn verifier(digest: u8) -> ExactArchiveVerifier {
        ExactArchiveVerifier {
            digest,
            denied_key: None,
        }
    }

    #[derive(Debug)]
    struct ChainedArchiveVerifier {
        digest: u8,
        predecessor: KeyEpochArchiveCheckpoint,
    }

    impl KeyEpochArchiveVerifier for ChainedArchiveVerifier {
        fn verify_checkpoint(&self, checkpoint: &KeyEpochArchiveCheckpoint) -> bool {
            checkpoint.archive_digest[0] == self.digest
                && checkpoint.sequence == self.predecessor.sequence + 1
                && checkpoint.previous_archive_digest
                    == Some(self.predecessor.archive_digest)
                && checkpoint.highest_epoch >= self.predecessor.highest_epoch
                && checkpoint.last_lifecycle_time >= self.predecessor.last_lifecycle_time
                && match checkpoint.active {
                    Some(active) => {
                        active == checkpoint.highest_epoch
                            && checkpoint.authority_lost_epoch.is_none()
                    }
                    None => {
                        checkpoint.authority_lost_epoch == Some(checkpoint.highest_epoch)
                            && (checkpoint.highest_epoch > self.predecessor.highest_epoch
                                || self.predecessor.authority_lost_epoch
                                    == Some(checkpoint.highest_epoch))
                    }
                }
        }

        fn key_id_is_absent(
            &self,
            checkpoint: &KeyEpochArchiveCheckpoint,
            candidate: KeyId,
        ) -> bool {
            self.verify_checkpoint(checkpoint)
                && self
                    .predecessor
                    .retained_records
                    .iter()
                    .chain(self.predecessor.archived_records.iter())
                    .chain(checkpoint.retained_records.iter())
                    .chain(checkpoint.archived_records.iter())
                    .all(|record| record.key_id != candidate)
        }
    }

    fn epoch(value: u64) -> KeyEpoch {
""",
    "chained verifier fixture",
)

replace_once(
    """    #[test]
    fn capacity_configuration_has_a_hard_upper_bound() {
""",
    """    #[test]
    fn successive_archives_carry_historical_authority_loss() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 100).unwrap();
        registry.revoke(epoch(2), 30).unwrap();

        let first = registry.archive_terminal([21; 32], &verifier(21)).unwrap();
        assert_eq!(first.highest_epoch, epoch(2));
        assert_eq!(first.authority_lost_epoch, Some(epoch(2)));
        assert_eq!(registry.retire_expired(101), 1);

        let chain = ChainedArchiveVerifier {
            digest: 22,
            predecessor: first.clone(),
        };
        let second = registry.archive_terminal([22; 32], &chain).unwrap();
        assert_eq!(second.previous_archive_digest, Some(first.archive_digest));
        assert_eq!(second.highest_epoch, epoch(2));
        assert_eq!(second.authority_lost_epoch, Some(epoch(2)));
        assert_eq!(second.archived_records.len(), 1);
        assert_eq!(second.archived_records[0].epoch, epoch(1));
        assert!(second.retained_records.is_empty());
        assert!(registry.is_empty());
        assert_eq!(
            registry.active_signer_at(101),
            Err(KeyEpochError::NoActiveSigner)
        );

        let mut restored =
            KeyEpochRegistry::from_checkpoint(2, second.clone(), &chain).unwrap();
        assert_eq!(restored.highest_epoch(), Some(epoch(2)));
        assert_eq!(
            restored.active_signer_at(101),
            Err(KeyEpochError::NoActiveSigner)
        );
        assert!(matches!(
            restored.verification_key(epoch(1), 101),
            Err(KeyEpochError::EpochArchived { .. })
        ));
        assert!(matches!(
            restored.verification_key(epoch(2), 101),
            Err(KeyEpochError::EpochArchived { .. })
        ));
        restored
            .install_after_revocation_with_archive_verifier(
                epoch(3),
                key(3),
                102,
                &chain,
            )
            .unwrap();
        assert_eq!(restored.active_signer_at(102).unwrap().epoch, epoch(3));
    }

    #[test]
    fn restored_predecessor_can_archive_older_overlap_without_chain_forgery() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 100).unwrap();
        registry.revoke(epoch(2), 30).unwrap();
        let first = registry.archive_terminal([23; 32], &verifier(23)).unwrap();

        let mut restored =
            KeyEpochRegistry::from_checkpoint(2, first.clone(), &verifier(23)).unwrap();
        assert_eq!(restored.retire_expired(101), 1);
        let chain = ChainedArchiveVerifier {
            digest: 24,
            predecessor: first,
        };
        let second = restored.archive_terminal([24; 32], &chain).unwrap();
        let restored_again =
            KeyEpochRegistry::from_checkpoint(2, second.clone(), &chain).unwrap();
        assert_eq!(restored_again.highest_epoch(), Some(epoch(2)));
        assert_eq!(
            restored_again.active_signer_at(101),
            Err(KeyEpochError::NoActiveSigner)
        );

        let mut forged = second;
        forged.previous_archive_digest = Some([99; 32]);
        assert_eq!(
            KeyEpochRegistry::from_checkpoint(2, forged, &chain).unwrap_err(),
            KeyEpochError::CheckpointVerificationFailed
        );
    }

    #[test]
    fn capacity_configuration_has_a_hard_upper_bound() {
""",
    "successive archive regression tests",
)

PATH.write_text(text, encoding="utf-8")
