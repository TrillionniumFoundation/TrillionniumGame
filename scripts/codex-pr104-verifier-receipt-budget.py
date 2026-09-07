#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "crates/trnm-presence-router-v2/src/disconnect_journal.rs"
LIB = ROOT / "crates/trnm-presence-router-v2/src/lib.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


text = SOURCE.read_text(encoding="utf-8")

text = replace_once(
    text,
    "pub const MAX_DISCONNECT_ACTIVE_RECORDS: usize = 65_536;\n"
    "pub const MAX_DISCONNECT_TOMBSTONES: usize = 262_144;\n"
    "pub const MAX_DISCONNECT_ATTEMPTS: u32 = 1_024;\n",
    "pub const MAX_DISCONNECT_ACTIVE_RECORDS: usize = 65_536;\n"
    "pub const MAX_DISCONNECT_TOMBSTONES: usize = 262_144;\n"
    "pub const MAX_DISCONNECT_ATTEMPTS: u32 = 1_024;\n"
    "pub const DISCONNECT_VERIFIER_RECEIPTS_PER_ATTEMPT: usize = 2;\n"
    "pub const MAX_DISCONNECT_VERIFIER_RECEIPTS: usize = 262_144;\n",
    "receipt budget constants",
)

text = replace_once(
    text,
    "        if self.max_attempts > MAX_DISCONNECT_ATTEMPTS {\n"
    "            return Err(DisconnectJournalError::AttemptLimitTooLarge {\n"
    "                received: self.max_attempts,\n"
    "                maximum: MAX_DISCONNECT_ATTEMPTS,\n"
    "            });\n"
    "        }\n"
    "        Ok(self)\n"
    "    }\n"
    "}\n",
    "        if self.max_attempts > MAX_DISCONNECT_ATTEMPTS {\n"
    "            return Err(DisconnectJournalError::AttemptLimitTooLarge {\n"
    "                received: self.max_attempts,\n"
    "                maximum: MAX_DISCONNECT_ATTEMPTS,\n"
    "            });\n"
    "        }\n"
    "        let receipt_capacity = self.verifier_receipt_capacity()?;\n"
    "        if receipt_capacity > MAX_DISCONNECT_VERIFIER_RECEIPTS {\n"
    "            return Err(DisconnectJournalError::CapacityTooLarge {\n"
    "                field: \"verifier_receipt_capacity\",\n"
    "                received: receipt_capacity,\n"
    "                maximum: MAX_DISCONNECT_VERIFIER_RECEIPTS,\n"
    "            });\n"
    "        }\n"
    "        Ok(self)\n"
    "    }\n\n"
    "    fn verifier_receipts_per_intent(self) -> Result<usize, DisconnectJournalError> {\n"
    "        usize::try_from(self.max_attempts)\n"
    "            .ok()\n"
    "            .and_then(|attempts| {\n"
    "                attempts.checked_mul(DISCONNECT_VERIFIER_RECEIPTS_PER_ATTEMPT)\n"
    "            })\n"
    "            .ok_or(DisconnectJournalError::VerifierReceiptBudgetOverflow)\n"
    "    }\n\n"
    "    fn verifier_receipt_capacity(self) -> Result<usize, DisconnectJournalError> {\n"
    "        self.tombstone_capacity\n"
    "            .checked_mul(self.verifier_receipts_per_intent()?)\n"
    "            .ok_or(DisconnectJournalError::VerifierReceiptBudgetOverflow)\n"
    "    }\n"
    "}\n",
    "config aggregate budget",
)

text = replace_once(
    text,
    "    VerifierReceiptReused {\n"
    "        receipt_owner: DisconnectIntentId,\n"
    "        received_for: DisconnectIntentId,\n"
    "    },\n"
    "    Terminal(DisconnectIntentId),\n",
    "    VerifierReceiptReused {\n"
    "        receipt_owner: DisconnectIntentId,\n"
    "        received_for: DisconnectIntentId,\n"
    "    },\n"
    "    VerifierReceiptBudgetOverflow,\n"
    "    VerifierReceiptCapacityExceeded {\n"
    "        capacity: usize,\n"
    "    },\n"
    "    VerifierReceiptReservationMissing(DisconnectIntentId),\n"
    "    InvariantViolation(&'static str),\n"
    "    Terminal(DisconnectIntentId),\n",
    "receipt budget errors",
)

text = replace_once(
    text,
    "            Self::VerifierReceiptReused {\n"
    "                receipt_owner,\n"
    "                received_for,\n"
    "            } => write!(\n"
    "                formatter,\n"
    "                \"verifier receipt owned by intent {} was supplied for intent {}\",\n"
    "                receipt_owner.get(),\n"
    "                received_for.get()\n"
    "            ),\n"
    "            Self::Terminal(id) => write!(formatter, \"disconnect intent {} is terminal\", id.get()),\n",
    "            Self::VerifierReceiptReused {\n"
    "                receipt_owner,\n"
    "                received_for,\n"
    "            } => write!(\n"
    "                formatter,\n"
    "                \"verifier receipt owned by intent {} was supplied for intent {}\",\n"
    "                receipt_owner.get(),\n"
    "                received_for.get()\n"
    "            ),\n"
    "            Self::VerifierReceiptBudgetOverflow => {\n"
    "                formatter.write_str(\"disconnect verifier receipt budget overflow\")\n"
    "            }\n"
    "            Self::VerifierReceiptCapacityExceeded { capacity } => write!(\n"
    "                formatter,\n"
    "                \"disconnect verifier receipt budget is full at capacity {capacity}\"\n"
    "            ),\n"
    "            Self::VerifierReceiptReservationMissing(id) => write!(\n"
    "                formatter,\n"
    "                \"disconnect intent {} has no verifier receipt reservation\",\n"
    "                id.get()\n"
    "            ),\n"
    "            Self::InvariantViolation(message) => {\n"
    "                write!(formatter, \"disconnect journal invariant violation: {message}\")\n"
    "            }\n"
    "            Self::Terminal(id) => write!(formatter, \"disconnect intent {} is terminal\", id.get()),\n",
    "receipt budget display",
)

text = replace_once(
    text,
    "    outcome_receipt_owners: BTreeMap<[u8; 32], DisconnectIntentId>,\n"
    "    verifier_receipt_owners: BTreeMap<[u8; 32], DisconnectIntentId>,\n"
    "    last_epoch_checkpoint: Option<[u8; 32]>,\n",
    "    outcome_receipt_owners: BTreeMap<[u8; 32], DisconnectIntentId>,\n"
    "    verifier_receipt_owners: BTreeMap<[u8; 32], DisconnectIntentId>,\n"
    "    verifier_receipt_reservations: BTreeMap<DisconnectIntentId, usize>,\n"
    "    verifier_receipt_capacity: usize,\n"
    "    last_epoch_checkpoint: Option<[u8; 32]>,\n",
    "journal receipt budget fields",
)

text = replace_once(
    text,
    "    pub fn new(config: DisconnectJournalConfig) -> Result<Self, DisconnectJournalError> {\n"
    "        Ok(Self {\n"
    "            config: config.validate()?,\n"
    "            records: BTreeMap::new(),\n"
    "            tombstones: BTreeMap::new(),\n"
    "            outcome_receipt_owners: BTreeMap::new(),\n"
    "            verifier_receipt_owners: BTreeMap::new(),\n"
    "            last_epoch_checkpoint: None,\n"
    "        })\n"
    "    }\n",
    "    pub fn new(config: DisconnectJournalConfig) -> Result<Self, DisconnectJournalError> {\n"
    "        let config = config.validate()?;\n"
    "        let verifier_receipt_capacity = config.verifier_receipt_capacity()?;\n"
    "        Ok(Self {\n"
    "            config,\n"
    "            records: BTreeMap::new(),\n"
    "            tombstones: BTreeMap::new(),\n"
    "            outcome_receipt_owners: BTreeMap::new(),\n"
    "            verifier_receipt_owners: BTreeMap::new(),\n"
    "            verifier_receipt_reservations: BTreeMap::new(),\n"
    "            verifier_receipt_capacity,\n"
    "            last_epoch_checkpoint: None,\n"
    "        })\n"
    "    }\n",
    "journal constructor",
)

text = replace_once(
    text,
    "    pub fn verifier_receipt_count(&self) -> usize {\n"
    "        self.verifier_receipt_owners.len()\n"
    "    }\n",
    "    pub fn verifier_receipt_count(&self) -> usize {\n"
    "        self.verifier_receipt_owners.len()\n"
    "    }\n\n"
    "    pub const fn verifier_receipt_capacity(&self) -> usize {\n"
    "        self.verifier_receipt_capacity\n"
    "    }\n\n"
    "    pub fn verifier_receipt_reserved(&self) -> usize {\n"
    "        self.verifier_receipt_reservations.values().copied().sum()\n"
    "    }\n",
    "receipt budget accessors",
)

text = replace_once(
    text,
    "        if self.tombstones.contains_key(&id) {\n"
    "            return Err(DisconnectJournalError::ArchivedIntent(id));\n"
    "        }\n"
    "        if self.records.len().saturating_add(self.tombstones.len())\n",
    "        if self.tombstones.contains_key(&id) {\n"
    "            return Err(DisconnectJournalError::ArchivedIntent(id));\n"
    "        }\n"
    "        let verifier_receipt_reservation = self.require_new_receipt_reservation()?;\n"
    "        if self.records.len().saturating_add(self.tombstones.len())\n",
    "insert receipt reservation precheck",
)

text = replace_once(
    text,
    "        };\n"
    "        self.records.insert(id, record);\n"
    "        Ok(record)\n"
    "    }\n\n"
    "    pub fn lease(\n",
    "        };\n"
    "        self.records.insert(id, record);\n"
    "        self.verifier_receipt_reservations\n"
    "            .insert(id, verifier_receipt_reservation);\n"
    "        Ok(record)\n"
    "    }\n\n"
    "    pub fn lease(\n",
    "insert receipt reservation commit",
)

text = replace_once(
    text,
    "        self.require_unique_verifier_receipt(id, evidence.verifier_receipt_digest)?;\n\n"
    "        let (state, disposition) = match evidence.kind {\n",
    "        self.require_unique_verifier_receipt(id, evidence.verifier_receipt_digest)?;\n\n"
    "        let (state, disposition) = match evidence.kind {\n",
    "reconcile anchor",
)

text = replace_once(
    text,
    "        };\n\n"
    "        self.verifier_receipt_owners\n"
    "            .insert(evidence.verifier_receipt_digest, id);\n",
    "        };\n\n"
    "        self.consume_receipt_reservation(id)?;\n"
    "        self.verifier_receipt_owners\n"
    "            .insert(evidence.verifier_receipt_digest, id);\n",
    "consume receipt reservation",
)

text = replace_once(
    text,
    "        let tombstone = DisconnectArchiveTombstone {\n"
    "            journal_id: self.config.journal_id,\n"
    "            journal_epoch: self.config.epoch,\n"
    "            id,\n"
    "            operation: current.operation,\n"
    "            terminal_digest,\n"
    "            archive_digest,\n"
    "        };\n"
    "        self.records.remove(&id);\n"
    "        self.tombstones.insert(id, tombstone);\n"
    "        Ok(tombstone)\n",
    "        if !self.verifier_receipt_reservations.contains_key(&id) {\n"
    "            return Err(DisconnectJournalError::VerifierReceiptReservationMissing(id));\n"
    "        }\n"
    "        let tombstone = DisconnectArchiveTombstone {\n"
    "            journal_id: self.config.journal_id,\n"
    "            journal_epoch: self.config.epoch,\n"
    "            id,\n"
    "            operation: current.operation,\n"
    "            terminal_digest,\n"
    "            archive_digest,\n"
    "        };\n"
    "        self.records.remove(&id);\n"
    "        self.verifier_receipt_reservations.remove(&id);\n"
    "        self.tombstones.insert(id, tombstone);\n"
    "        Ok(tombstone)\n",
    "archive releases unused receipt reservation",
)

text = replace_once(
    text,
    "        if next_epoch <= self.config.epoch {\n"
    "            return Err(DisconnectJournalError::NonIncreasingJournalEpoch {\n"
    "                current: self.config.epoch,\n"
    "                received: next_epoch,\n"
    "            });\n"
    "        }\n"
    "        self.config.epoch = next_epoch;\n",
    "        if !self.verifier_receipt_reservations.is_empty() {\n"
    "            return Err(DisconnectJournalError::InvariantViolation(\n"
    "                \"receipt reservations remain without active records\",\n"
    "            ));\n"
    "        }\n"
    "        if next_epoch <= self.config.epoch {\n"
    "            return Err(DisconnectJournalError::NonIncreasingJournalEpoch {\n"
    "                current: self.config.epoch,\n"
    "                received: next_epoch,\n"
    "            });\n"
    "        }\n"
    "        self.config.epoch = next_epoch;\n",
    "epoch reservation invariant",
)

text = replace_once(
    text,
    "        self.verifier_receipt_owners.clear();\n"
    "        self.last_epoch_checkpoint = Some(checkpoint_digest);\n",
    "        self.verifier_receipt_owners.clear();\n"
    "        self.verifier_receipt_reservations.clear();\n"
    "        self.last_epoch_checkpoint = Some(checkpoint_digest);\n",
    "epoch clears receipt reservations",
)

text = replace_once(
    text,
    "    fn require_unique_outcome_receipt(\n",
    "    fn require_new_receipt_reservation(&self) -> Result<usize, DisconnectJournalError> {\n"
    "        let reservation = self.config.verifier_receipts_per_intent()?;\n"
    "        let reserved = self\n"
    "            .verifier_receipt_reservations\n"
    "            .values()\n"
    "            .try_fold(0usize, |total, value| total.checked_add(*value))\n"
    "            .ok_or(DisconnectJournalError::VerifierReceiptBudgetOverflow)?;\n"
    "        let projected = self\n"
    "            .verifier_receipt_owners\n"
    "            .len()\n"
    "            .checked_add(reserved)\n"
    "            .and_then(|used| used.checked_add(reservation))\n"
    "            .ok_or(DisconnectJournalError::VerifierReceiptBudgetOverflow)?;\n"
    "        if projected > self.verifier_receipt_capacity {\n"
    "            return Err(DisconnectJournalError::VerifierReceiptCapacityExceeded {\n"
    "                capacity: self.verifier_receipt_capacity,\n"
    "            });\n"
    "        }\n"
    "        Ok(reservation)\n"
    "    }\n\n"
    "    fn consume_receipt_reservation(\n"
    "        &mut self,\n"
    "        id: DisconnectIntentId,\n"
    "    ) -> Result<(), DisconnectJournalError> {\n"
    "        let remaining = self\n"
    "            .verifier_receipt_reservations\n"
    "            .get_mut(&id)\n"
    "            .ok_or(DisconnectJournalError::VerifierReceiptReservationMissing(id))?;\n"
    "        if *remaining == 0 {\n"
    "            return Err(DisconnectJournalError::VerifierReceiptCapacityExceeded {\n"
    "                capacity: self.verifier_receipt_capacity,\n"
    "            });\n"
    "        }\n"
    "        *remaining -= 1;\n"
    "        Ok(())\n"
    "    }\n\n"
    "    fn require_unique_outcome_receipt(\n",
    "receipt reservation helpers",
)

text = replace_once(
    text,
    "        if let Some(owner) = self.verifier_receipt_owners.get(&receipt).copied() {\n"
    "            if owner != id {\n"
    "                return Err(DisconnectJournalError::VerifierReceiptReused {\n"
    "                    receipt_owner: owner,\n"
    "                    received_for: id,\n"
    "                });\n"
    "            }\n"
    "        }\n"
    "        Ok(())\n",
    "        if let Some(owner) = self.verifier_receipt_owners.get(&receipt).copied() {\n"
    "            return Err(DisconnectJournalError::VerifierReceiptReused {\n"
    "                receipt_owner: owner,\n"
    "                received_for: id,\n"
    "            });\n"
    "        }\n"
    "        Ok(())\n",
    "verifier receipt is globally one-use after replay handling",
)

text = replace_once(
    text,
    "    fn attempt_limit_dead_letters_atomically() {\n"
    "        let mut journal = journal(1, 1, 1);\n",
    "    fn aggregate_verifier_receipt_budget_is_checked_and_epoch_scoped() {\n"
    "        let unsafe_config = DisconnectJournalConfig {\n"
    "            journal_id: DisconnectJournalId::new([7; 16]).unwrap(),\n"
    "            epoch: DisconnectJournalEpoch::new(1).unwrap(),\n"
    "            active_capacity: 1,\n"
    "            tombstone_capacity: MAX_DISCONNECT_VERIFIER_RECEIPTS / 2 + 1,\n"
    "            max_attempts: 1,\n"
    "        };\n"
    "        assert!(matches!(\n"
    "            DisconnectJournal::new(unsafe_config),\n"
    "            Err(DisconnectJournalError::CapacityTooLarge {\n"
    "                field: \"verifier_receipt_capacity\",\n"
    "                ..\n"
    "            })\n"
    "        ));\n\n"
    "        let mut journal = journal(1, 2, 1);\n"
    "        assert_eq!(journal.verifier_receipt_capacity(), 4);\n"
    "        for (intent, receipt) in [(id(1), 70), (id(2), 80)] {\n"
    "            let binding = dispatch(&mut journal, intent, operation(intent.get()), worker(1), 8);\n"
    "            journal\n"
    "                .reconcile(\n"
    "                    intent,\n"
    "                    evidence(binding, DisconnectOutcomeKind::Unknown, receipt, receipt),\n"
    "                    &ExactVerifier(receipt),\n"
    "                )\n"
    "                .unwrap();\n"
    "            journal\n"
    "                .reconcile(\n"
    "                    intent,\n"
    "                    evidence(\n"
    "                        binding,\n"
    "                        DisconnectOutcomeKind::Applied,\n"
    "                        receipt + 1,\n"
    "                        receipt + 1,\n"
    "                    ),\n"
    "                    &ExactVerifier(receipt + 1),\n"
    "                )\n"
    "                .unwrap();\n"
    "            journal.archive_terminal(intent, digest(receipt + 2)).unwrap();\n"
    "        }\n"
    "        assert_eq!(journal.verifier_receipt_count(), 4);\n"
    "        assert_eq!(journal.verifier_receipt_reserved(), 0);\n"
    "        let before_tombstones = journal.tombstone_len();\n"
    "        assert_eq!(\n"
    "            journal.insert(id(3), operation(3)),\n"
    "            Err(DisconnectJournalError::VerifierReceiptCapacityExceeded {\n"
    "                capacity: 4\n"
    "            })\n"
    "        );\n"
    "        assert_eq!(journal.len(), 0);\n"
    "        assert_eq!(journal.tombstone_len(), before_tombstones);\n"
    "        assert_eq!(journal.verifier_receipt_count(), 4);\n"
    "        journal\n"
    "            .advance_epoch(DisconnectJournalEpoch::new(2).unwrap(), digest(110))\n"
    "            .unwrap();\n"
    "        assert_eq!(journal.verifier_receipt_count(), 0);\n"
    "        journal.insert(id(3), operation(3)).unwrap();\n"
    "        assert_eq!(journal.verifier_receipt_reserved(), 2);\n"
    "    }\n\n"
    "    #[test]\n"
    "    fn verifier_receipt_reuse_by_same_intent_is_not_a_new_reconciliation() {\n"
    "        let mut journal = journal(1, 1, 2);\n"
    "        let first = dispatch(&mut journal, id(1), operation(9), worker(1), 8);\n"
    "        journal\n"
    "            .reconcile(\n"
    "                id(1),\n"
    "                evidence(\n"
    "                    first,\n"
    "                    DisconnectOutcomeKind::DefinitelyNotApplied,\n"
    "                    71,\n"
    "                    70,\n"
    "                ),\n"
    "                &ExactVerifier(70),\n"
    "            )\n"
    "            .unwrap();\n"
    "        let leased = journal.lease(id(1), worker(2)).unwrap();\n"
    "        let token = match leased.state {\n"
    "            DisconnectState::Leased { token, .. } => token,\n"
    "            _ => unreachable!(),\n"
    "        };\n"
    "        let second = journal\n"
    "            .mark_dispatched(id(1), worker(2), token, digest(8))\n"
    "            .unwrap();\n"
    "        let binding = match second.state {\n"
    "            DisconnectState::Dispatched { binding } => binding,\n"
    "            _ => unreachable!(),\n"
    "        };\n"
    "        let before = journal.get(id(1));\n"
    "        let reserved = journal.verifier_receipt_reserved();\n"
    "        assert!(matches!(\n"
    "            journal.reconcile(\n"
    "                id(1),\n"
    "                evidence(binding, DisconnectOutcomeKind::Applied, 90, 70),\n"
    "                &ExactVerifier(70),\n"
    "            ),\n"
    "            Err(DisconnectJournalError::VerifierReceiptReused { .. })\n"
    "        ));\n"
    "        assert_eq!(journal.get(id(1)), before);\n"
    "        assert_eq!(journal.verifier_receipt_reserved(), reserved);\n"
    "    }\n\n"
    "    #[test]\n"
    "    fn attempt_limit_dead_letters_atomically() {\n"
    "        let mut journal = journal(1, 1, 1);\n",
    "receipt budget regressions",
)

SOURCE.write_text(text, encoding="utf-8")

lib = LIB.read_text(encoding="utf-8")
lib = replace_once(
    lib,
    "    LeaseToken, ReconciliationDisposition, RetryDisposition, WorkerId,\n"
    "    MAX_DISCONNECT_ACTIVE_RECORDS, MAX_DISCONNECT_ATTEMPTS, MAX_DISCONNECT_TOMBSTONES,\n",
    "    LeaseToken, ReconciliationDisposition, RetryDisposition, WorkerId,\n"
    "    DISCONNECT_VERIFIER_RECEIPTS_PER_ATTEMPT, MAX_DISCONNECT_ACTIVE_RECORDS,\n"
    "    MAX_DISCONNECT_ATTEMPTS, MAX_DISCONNECT_TOMBSTONES,\n"
    "    MAX_DISCONNECT_VERIFIER_RECEIPTS,\n",
    "lib receipt budget exports",
)
LIB.write_text(lib, encoding="utf-8")
