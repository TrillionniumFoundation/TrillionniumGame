#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "crates/trnm-token-crypto-provider/src/key_epoch.rs"
README = ROOT / "crates/trnm-token-crypto-provider/README.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


text = SOURCE.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    for record in &checkpoint.archived_records {
        if !matches!(
            record.state,
            KeyEpochState::Retired { .. } | KeyEpochState::Revoked { .. }
        ) {
            return Err(KeyEpochError::CheckpointInvalid(
                "archive contains nonterminal record",
            ));
        }
        if !epochs.insert(record.epoch) || !key_ids.insert(record.key_id) {
''',
    '''    for record in &checkpoint.archived_records {
        let terminal_time = match record.state {
            KeyEpochState::Retired {
                retired_at_unix_seconds,
            } => retired_at_unix_seconds,
            KeyEpochState::Revoked {
                revoked_at_unix_seconds,
            } => revoked_at_unix_seconds,
            KeyEpochState::Active | KeyEpochState::VerifyOnly { .. } => {
                return Err(KeyEpochError::CheckpointInvalid(
                    "archive contains nonterminal record",
                ));
            }
        };
        if terminal_time < record.activated_at_unix_seconds {
            return Err(KeyEpochError::CheckpointInvalid(
                "terminal lifecycle time precedes activation",
            ));
        }
        if terminal_time > checkpoint.last_lifecycle_time {
            return Err(KeyEpochError::CheckpointInvalid(
                "terminal lifecycle time exceeds the checkpoint high-water",
            ));
        }
        if !epochs.insert(record.epoch) || !key_ids.insert(record.key_id) {
''',
    "archived lifecycle validation",
)
text = replace_once(
    text,
    '''    for record in &checkpoint.retained_records {
        if !matches!(
            record.state,
            KeyEpochState::Active | KeyEpochState::VerifyOnly { .. }
        ) {
            return Err(KeyEpochError::CheckpointInvalid(
                "retained set contains terminal record",
            ));
        }
        if !epochs.insert(record.epoch) || !key_ids.insert(record.key_id) {
''',
    '''    let mut active_record_count = 0_usize;
    for record in &checkpoint.retained_records {
        match record.state {
            KeyEpochState::Active => {
                active_record_count = active_record_count
                    .checked_add(1)
                    .ok_or(KeyEpochError::CheckpointInvalid(
                        "active record count overflow",
                    ))?;
            }
            KeyEpochState::VerifyOnly {
                retire_after_unix_seconds,
            } => {
                if retire_after_unix_seconds <= record.activated_at_unix_seconds {
                    return Err(KeyEpochError::CheckpointInvalid(
                        "verify-only retirement does not follow activation",
                    ));
                }
            }
            KeyEpochState::Retired { .. } | KeyEpochState::Revoked { .. } => {
                return Err(KeyEpochError::CheckpointInvalid(
                    "retained set contains terminal record",
                ));
            }
        }
        if !epochs.insert(record.epoch) || !key_ids.insert(record.key_id) {
''',
    "retained lifecycle validation",
)
text = replace_once(
    text,
    '''    match checkpoint.active {
        Some(active) => {
''',
    '''    match checkpoint.active {
        Some(_) if active_record_count != 1 => {
            return Err(KeyEpochError::CheckpointInvalid(
                "active checkpoint must retain exactly one active record",
            ));
        }
        None if active_record_count != 0 => {
            return Err(KeyEpochError::CheckpointInvalid(
                "authority-loss checkpoint cannot retain an active record",
            ));
        }
        Some(_) | None => {}
    }
    match checkpoint.active {
        Some(active) => {
''',
    "active cardinality validation",
)
text = replace_once(
    text,
    '''    #[derive(Debug)]
    struct ChainedArchiveVerifier {
''',
    '''    #[derive(Debug)]
    struct PanicArchiveVerifier;

    impl KeyEpochArchiveVerifier for PanicArchiveVerifier {
        fn verify_checkpoint(&self, _checkpoint: &KeyEpochArchiveCheckpoint) -> bool {
            panic!("invalid checkpoint reached the external verifier")
        }

        fn key_id_is_absent(
            &self,
            _checkpoint: &KeyEpochArchiveCheckpoint,
            _candidate: KeyId,
        ) -> bool {
            panic!("invalid checkpoint reached the archive absence verifier")
        }
    }

    #[derive(Debug)]
    struct ChainedArchiveVerifier {
''',
    "panic verifier fixture",
)
text = replace_once(
    text,
    '''    fn key(value: u8) -> KeyId {
        KeyId::new([value; 16]).unwrap()
    }

    #[test]
    fn rotation_is_contiguous_time_monotonic_and_bounded() {
''',
    '''    fn key(value: u8) -> KeyId {
        KeyId::new([value; 16]).unwrap()
    }

    fn valid_shape_checkpoint() -> KeyEpochArchiveCheckpoint {
        KeyEpochArchiveCheckpoint {
            sequence: 1,
            previous_archive_digest: None,
            archive_digest: [31; 32],
            highest_epoch: epoch(3),
            last_lifecycle_time: 30,
            authority_lost_epoch: None,
            active: Some(epoch(3)),
            retained_records: vec![
                KeyEpochRecord {
                    epoch: epoch(2),
                    key_id: key(2),
                    state: KeyEpochState::VerifyOnly {
                        retire_after_unix_seconds: 100,
                    },
                    activated_at_unix_seconds: 20,
                },
                KeyEpochRecord {
                    epoch: epoch(3),
                    key_id: key(3),
                    state: KeyEpochState::Active,
                    activated_at_unix_seconds: 30,
                },
            ],
            archived_records: vec![KeyEpochRecord {
                epoch: epoch(1),
                key_id: key(1),
                state: KeyEpochState::Retired {
                    retired_at_unix_seconds: 25,
                },
                activated_at_unix_seconds: 10,
            }],
        }
    }

    #[test]
    fn checkpoint_shape_rejects_active_and_lifecycle_forgery_before_verifier() {
        let mut cases = Vec::new();

        let mut dual_active = valid_shape_checkpoint();
        dual_active.retained_records[0].state = KeyEpochState::Active;
        cases.push(dual_active);

        let mut authority_loss_with_active = valid_shape_checkpoint();
        authority_loss_with_active.active = None;
        authority_loss_with_active.authority_lost_epoch = Some(epoch(3));
        cases.push(authority_loss_with_active);

        let mut active_not_highest = valid_shape_checkpoint();
        active_not_highest.active = Some(epoch(2));
        cases.push(active_not_highest);

        let mut verify_only_before_activation = valid_shape_checkpoint();
        verify_only_before_activation.retained_records[0].state = KeyEpochState::VerifyOnly {
            retire_after_unix_seconds: 19,
        };
        cases.push(verify_only_before_activation);

        let mut terminal_after_high_water = valid_shape_checkpoint();
        terminal_after_high_water.archived_records[0].state = KeyEpochState::Retired {
            retired_at_unix_seconds: 31,
        };
        cases.push(terminal_after_high_water);

        let mut terminal_before_activation = valid_shape_checkpoint();
        terminal_before_activation.archived_records[0].state = KeyEpochState::Revoked {
            revoked_at_unix_seconds: 9,
        };
        cases.push(terminal_before_activation);

        for candidate in cases {
            assert!(matches!(
                KeyEpochRegistry::from_checkpoint(3, candidate, &PanicArchiveVerifier),
                Err(KeyEpochError::CheckpointInvalid(_))
            ));
        }
    }

    #[test]
    fn checkpoint_shape_accepts_one_active_and_future_verify_window() {
        let checkpoint = valid_shape_checkpoint();
        let restored =
            KeyEpochRegistry::from_checkpoint(3, checkpoint, &verifier(31)).unwrap();
        assert_eq!(restored.active_signer_at(30).unwrap().epoch, epoch(3));
        assert_eq!(
            restored.verification_key(epoch(2), 50).unwrap().epoch,
            epoch(2)
        );
    }

    #[test]
    fn rotation_is_contiguous_time_monotonic_and_bounded() {
''',
    "checkpoint hostile tests",
)
SOURCE.write_text(text, encoding="utf-8")

readme = README.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    '''The operational registry remains deterministic and bounded. Terminal operational records leave memory only through a digest-chained `KeyEpochArchiveCheckpoint` accepted by a trusted `KeyEpochArchiveVerifier`. The checkpoint carries the global highest epoch, last lifecycle time, authority-loss state, retained verification window and exact archived records. New key IDs after archival require an external absence proof, and archived verification requests return an explicit durable-archive requirement instead of falling back. A durable adapter must atomically persist and verify this checkpoint before the window is restored on another node.
''',
    '''The operational registry remains deterministic and bounded. Terminal operational records leave memory only through a digest-chained `KeyEpochArchiveCheckpoint` accepted by a trusted `KeyEpochArchiveVerifier`. The checkpoint carries the global highest epoch, last lifecycle time, authority-loss state, retained verification window and exact archived records. Local shape validation requires exactly one retained `Active` record matching the highest epoch when signing authority exists, and no retained `Active` record after authority loss. Verify-only retirement must follow activation; revoked and retired timestamps cannot precede activation or exceed the lifecycle high-water. These structural checks execute before the external verifier. New key IDs after archival require an external absence proof, and archived verification requests return an explicit durable-archive requirement instead of falling back. A durable adapter must atomically persist and verify this checkpoint before the window is restored on another node.
''',
    "README key checkpoint contract",
)
README.write_text(readme, encoding="utf-8")
