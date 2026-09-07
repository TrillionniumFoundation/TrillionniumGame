#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_tests(path: Path, tests: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = "#[cfg(test)]\nmod tests {"
    if text.count(marker) != 1:
        raise SystemExit(f"{path}: test module anchor drift")
    path.write_text(text.split(marker, 1)[0] + tests, encoding="utf-8")


def patch_connection_actor() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/src/connection_actor.rs"
    replace_once(
        path,
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\npub struct ConnectionActorConfig {",
        "#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]\n"
        "pub struct RequestHandle {\n"
        "    correlation: CorrelationId,\n"
        "    admitted_at_sequence: u64,\n"
        "}\n\n"
        "impl RequestHandle {\n"
        "    pub const fn correlation(self) -> CorrelationId {\n"
        "        self.correlation\n"
        "    }\n\n"
        "    pub const fn admitted_at_sequence(self) -> u64 {\n"
        "        self.admitted_at_sequence\n"
        "    }\n"
        "}\n\n"
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\n"
        "pub struct ConnectionActorConfig {",
        "request handle type",
    )
    replace_once(
        path,
        "pub struct OutboundFrame {\n"
        "    pub sequence: u64,\n"
        "    pub correlation: Option<CorrelationId>,\n"
        "    pub payload: Box<[u8]>,\n"
        "}",
        "pub struct OutboundFrame {\n"
        "    pub sequence: u64,\n"
        "    pub correlation: Option<CorrelationId>,\n"
        "    pub request_handle: Option<RequestHandle>,\n"
        "    pub payload: Box<[u8]>,\n"
        "}",
        "outbound handle",
    )
    replace_once(
        path,
        "pub struct PendingRequest {\n"
        "    /// Unique request-admission sequence. This is intentionally independent of\n"
        "    /// the socket write sequence and therefore never makes a premature write\n"
        "    /// reservation claim.\n"
        "    pub admitted_at_sequence: u64,",
        "pub struct PendingRequest {\n"
        "    /// Actor-issued identity consumed by every asynchronous mutation.\n"
        "    pub handle: RequestHandle,\n"
        "    /// Unique request-admission sequence. This is intentionally independent of\n"
        "    /// the socket write sequence and therefore never makes a premature write\n"
        "    /// reservation claim.\n"
        "    pub admitted_at_sequence: u64,",
        "pending handle",
    )
    replace_once(
        path,
        "    UnknownCorrelation(CorrelationId),\n"
        "    ResponseAlreadyQueued(CorrelationId),",
        "    UnknownCorrelation(CorrelationId),\n"
        "    StaleRequestHandle {\n"
        "        correlation: CorrelationId,\n"
        "        current_admitted_at_sequence: u64,\n"
        "        received_admitted_at_sequence: u64,\n"
        "    },\n"
        "    ResponseAlreadyQueued(CorrelationId),",
        "stale error type",
    )
    replace_once(
        path,
        "            Self::UnknownCorrelation(value) => {\n"
        "                write!(formatter, \"correlation {} is not pending\", value.get())\n"
        "            }\n"
        "            Self::ResponseAlreadyQueued(value) => write!(",
        "            Self::UnknownCorrelation(value) => {\n"
        "                write!(formatter, \"correlation {} is not pending\", value.get())\n"
        "            }\n"
        "            Self::StaleRequestHandle {\n"
        "                correlation,\n"
        "                current_admitted_at_sequence,\n"
        "                received_admitted_at_sequence,\n"
        "            } => write!(\n"
        "                formatter,\n"
        "                \"correlation {} admission handle {} is stale; current is {}\",\n"
        "                correlation.get(),\n"
        "                received_admitted_at_sequence,\n"
        "                current_admitted_at_sequence\n"
        "            ),\n"
        "            Self::ResponseAlreadyQueued(value) => write!(",
        "stale error display",
    )
    replace_once(
        path,
        "        let request = PendingRequest {\n"
        "            admitted_at_sequence,\n"
        "            response_sequence: None,\n"
        "            response_dequeued: false,\n"
        "        };\n"
        "        self.pending.insert(correlation, request);",
        "        let handle = RequestHandle {\n"
        "            correlation,\n"
        "            admitted_at_sequence,\n"
        "        };\n"
        "        let request = PendingRequest {\n"
        "            handle,\n"
        "            admitted_at_sequence,\n"
        "            response_sequence: None,\n"
        "            response_dequeued: false,\n"
        "        };\n"
        "        self.pending.insert(correlation, request);",
        "async admission handle",
    )
    replace_once(
        path,
        "        let request = PendingRequest {\n"
        "            admitted_at_sequence,\n"
        "            response_sequence: Some(response_sequence),\n"
        "            response_dequeued: false,\n"
        "        };\n"
        "        self.pending.insert(correlation, request);\n"
        "        self.outbound.push_back(OutboundFrame {\n"
        "            sequence: response_sequence,\n"
        "            correlation: Some(correlation),\n"
        "            payload: payload.into_boxed_slice(),\n"
        "        });",
        "        let handle = RequestHandle {\n"
        "            correlation,\n"
        "            admitted_at_sequence,\n"
        "        };\n"
        "        let request = PendingRequest {\n"
        "            handle,\n"
        "            admitted_at_sequence,\n"
        "            response_sequence: Some(response_sequence),\n"
        "            response_dequeued: false,\n"
        "        };\n"
        "        self.pending.insert(correlation, request);\n"
        "        self.outbound.push_back(OutboundFrame {\n"
        "            sequence: response_sequence,\n"
        "            correlation: Some(correlation),\n"
        "            request_handle: Some(handle),\n"
        "            payload: payload.into_boxed_slice(),\n"
        "        });",
        "immediate admission handle",
    )
    start = "    pub fn enqueue_response(\n"
    end = "    /// Uncorrelated egress is a new unit of work and is therefore rejected once\n"
    text = path.read_text(encoding="utf-8")
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit("enqueue response anchors drift")
    before, tail = text.split(start, 1)
    _, after = tail.split(end, 1)
    replacement = '''    pub fn enqueue_response(
        &mut self,
        handle: RequestHandle,
        payload: Vec<u8>,
    ) -> Result<u64, ConnectionActorError> {
        self.require_not_closed()?;
        self.require_frame_size(payload.len())?;
        self.require_outbound_capacity()?;
        let correlation = handle.correlation;
        let current = self.require_handle(handle)?;
        if current.response_sequence.is_some() {
            return Err(ConnectionActorError::ResponseAlreadyQueued(correlation));
        }
        let sequence = self.next_write_sequence;
        let next_write_sequence = sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::WriteSequenceExhausted)?;
        let next = PendingRequest {
            response_sequence: Some(sequence),
            ..current
        };
        self.outbound.push_back(OutboundFrame {
            sequence,
            correlation: Some(correlation),
            request_handle: Some(handle),
            payload: payload.into_boxed_slice(),
        });
        self.pending.insert(correlation, next);
        self.next_write_sequence = next_write_sequence;
        Ok(sequence)
    }

'''
    path.write_text(before + replacement + end + after, encoding="utf-8")
    replace_once(
        path,
        "        self.outbound.push_back(OutboundFrame {\n"
        "            sequence,\n"
        "            correlation: None,\n"
        "            payload: payload.into_boxed_slice(),\n"
        "        });",
        "        self.outbound.push_back(OutboundFrame {\n"
        "            sequence,\n"
        "            correlation: None,\n"
        "            request_handle: None,\n"
        "            payload: payload.into_boxed_slice(),\n"
        "        });",
        "control frame handle",
    )
    start = "    /// Compatibility entry point with strict correlation semantics.\n"
    end = "    pub fn begin_drain(&mut self) -> Result<(), ConnectionActorError> {\n"
    text = path.read_text(encoding="utf-8")
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit("async mutation block anchors drift")
    before, tail = text.split(start, 1)
    _, after = tail.split(end, 1)
    replacement = '''    /// Compatibility entry point that preserves the complete actor-issued
    /// admission identity. A bare correlation is intentionally not accepted.
    pub fn enqueue_outbound(
        &mut self,
        request: Option<RequestHandle>,
        payload: Vec<u8>,
    ) -> Result<u64, ConnectionActorError> {
        match request {
            Some(handle) => self.enqueue_response(handle, payload),
            None => self.enqueue_control(payload),
        }
    }

    pub fn pop_outbound(&mut self) -> Option<OutboundFrame> {
        let frame = self.outbound.pop_front()?;
        if let Some(handle) = frame.request_handle {
            if let Ok(current) = self.require_handle(handle) {
                self.pending.insert(
                    handle.correlation,
                    PendingRequest {
                        response_dequeued: true,
                        ..current
                    },
                );
            }
        }
        Some(frame)
    }

    pub fn complete_request(
        &mut self,
        handle: RequestHandle,
    ) -> Result<PendingRequest, ConnectionActorError> {
        let current = self.require_handle(handle)?;
        if !current.response_dequeued {
            return Err(ConnectionActorError::ResponseNotDequeued(
                handle.correlation,
            ));
        }
        self.pending.remove(&handle.correlation);
        Ok(current)
    }

    pub fn cancel_request(
        &mut self,
        handle: RequestHandle,
    ) -> Result<PendingRequest, ConnectionActorError> {
        let current = self.require_handle(handle)?;
        if current.response_sequence.is_some() {
            return Err(ConnectionActorError::CannotCancelQueuedResponse(
                handle.correlation,
            ));
        }
        self.pending.remove(&handle.correlation);
        Ok(current)
    }

'''
    path.write_text(before + replacement + end + after, encoding="utf-8")
    replace_once(
        path,
        "    fn require_outbound_capacity(&self) -> Result<(), ConnectionActorError> {",
        "    fn require_handle(\n"
        "        &self,\n"
        "        handle: RequestHandle,\n"
        "    ) -> Result<PendingRequest, ConnectionActorError> {\n"
        "        let current = self\n"
        "            .pending\n"
        "            .get(&handle.correlation)\n"
        "            .copied()\n"
        "            .ok_or(ConnectionActorError::UnknownCorrelation(handle.correlation))?;\n"
        "        if current.handle != handle {\n"
        "            return Err(ConnectionActorError::StaleRequestHandle {\n"
        "                correlation: handle.correlation,\n"
        "                current_admitted_at_sequence: current.handle.admitted_at_sequence,\n"
        "                received_admitted_at_sequence: handle.admitted_at_sequence,\n"
        "            });\n"
        "        }\n"
        "        Ok(current)\n"
        "    }\n\n"
        "    fn require_outbound_capacity(&self) -> Result<(), ConnectionActorError> {",
        "handle validation helper",
    )

    tests = r'''#[cfg(test)]
mod tests {
    use super::*;

    fn actor() -> ConnectionActor {
        ConnectionActor::new(ConnectionActorConfig {
            inbound_capacity: 2,
            outbound_capacity: 2,
            pending_capacity: 2,
            max_frame_bytes: 8,
        })
        .unwrap()
    }

    #[test]
    fn configuration_has_checked_hard_memory_budget() {
        assert!(matches!(
            ConnectionActor::new(ConnectionActorConfig {
                inbound_capacity: MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
                outbound_capacity: MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
                pending_capacity: 1,
                max_frame_bytes: MAX_CONNECTION_ACTOR_FRAME_BYTES,
            }),
            Err(ConnectionActorError::BufferBudgetExceeded { .. })
        ));
    }

    #[test]
    fn caller_vec_spare_capacity_is_not_retained() {
        let mut actor = actor();
        let mut payload = Vec::with_capacity(1_000_000);
        payload.push(7);
        actor
            .enqueue_inbound(InboundFrame::from_vec(None, payload))
            .unwrap();
        let stored = actor.pop_inbound().unwrap();
        assert_eq!(&*stored.payload, &[7]);
    }

    #[test]
    fn immediate_request_and_response_admission_is_atomic() {
        let mut actor = ConnectionActor::new(ConnectionActorConfig {
            inbound_capacity: 1,
            outbound_capacity: 1,
            pending_capacity: 1,
            max_frame_bytes: 8,
        })
        .unwrap();
        let first = CorrelationId::new(1).unwrap();
        let admitted = actor.admit_immediate_response(first, vec![1]).unwrap();
        assert_eq!(admitted.admitted_at_sequence, 1);
        assert_eq!(admitted.handle.correlation(), first);
        assert_eq!(admitted.response_sequence, Some(1));
        let before_pending = actor.pending_len();
        let before_outbound = actor.outbound_len();
        assert!(matches!(
            actor.admit_immediate_response(CorrelationId::new(2).unwrap(), vec![2]),
            Err(ConnectionActorError::PendingQueueFull { capacity: 1 })
        ));
        assert_eq!(actor.pending_len(), before_pending);
        assert_eq!(actor.outbound_len(), before_outbound);
    }

    #[test]
    fn correlated_egress_requires_exact_live_handle_and_is_unique() {
        let mut actor = actor();
        let correlation = CorrelationId::new(9).unwrap();
        let forged = RequestHandle {
            correlation,
            admitted_at_sequence: 1,
        };
        assert_eq!(
            actor.enqueue_outbound(Some(forged), vec![1]),
            Err(ConnectionActorError::UnknownCorrelation(correlation))
        );
        let request = actor.begin_request(correlation).unwrap();
        assert_eq!(actor.enqueue_response(request.handle, vec![1]).unwrap(), 1);
        assert_eq!(
            actor.enqueue_response(request.handle, vec![2]),
            Err(ConnectionActorError::ResponseAlreadyQueued(correlation))
        );
        assert_eq!(
            actor.complete_request(request.handle),
            Err(ConnectionActorError::ResponseNotDequeued(correlation))
        );
        let frame = actor.pop_outbound().unwrap();
        assert_eq!(frame.request_handle, Some(request.handle));
        actor.complete_request(request.handle).unwrap();
    }

    #[test]
    fn stale_callbacks_cannot_capture_reused_correlation() {
        let mut actor = actor();
        let correlation = CorrelationId::new(9).unwrap();
        let old = actor.begin_request(correlation).unwrap();
        actor.cancel_request(old.handle).unwrap();
        let current = actor.begin_request(correlation).unwrap();
        assert_ne!(old.handle, current.handle);
        let before_pending = actor.pending_len();
        let before_outbound = actor.outbound_len();
        for error in [
            actor.enqueue_response(old.handle, b"old".to_vec()).unwrap_err(),
            actor.cancel_request(old.handle).unwrap_err(),
            actor.complete_request(old.handle).unwrap_err(),
        ] {
            assert!(matches!(
                error,
                ConnectionActorError::StaleRequestHandle { .. }
            ));
        }
        assert_eq!(actor.pending_len(), before_pending);
        assert_eq!(actor.outbound_len(), before_outbound);
        actor
            .enqueue_response(current.handle, b"current".to_vec())
            .unwrap();
        assert_eq!(&*actor.pop_outbound().unwrap().payload, b"current");
        actor.complete_request(current.handle).unwrap();
    }

    #[test]
    fn stale_callback_is_rejected_during_drain() {
        let mut actor = actor();
        let correlation = CorrelationId::new(1).unwrap();
        let old = actor.begin_request(correlation).unwrap();
        actor.cancel_request(old.handle).unwrap();
        let current = actor.begin_request(correlation).unwrap();
        actor.begin_drain().unwrap();
        assert!(matches!(
            actor.enqueue_response(old.handle, vec![7]),
            Err(ConnectionActorError::StaleRequestHandle { .. })
        ));
        actor.enqueue_response(current.handle, vec![8]).unwrap();
        let frame = actor.pop_outbound().unwrap();
        actor.complete_request(frame.request_handle.unwrap()).unwrap();
        assert!(actor.drain_converged());
        actor.close().unwrap();
    }

    #[test]
    fn drain_has_a_finite_pre_admitted_response_set() {
        let mut actor = actor();
        let first = actor
            .begin_request(CorrelationId::new(1).unwrap())
            .unwrap();
        let second = actor
            .begin_request(CorrelationId::new(2).unwrap())
            .unwrap();
        actor.begin_drain().unwrap();
        assert_eq!(
            actor.begin_request(CorrelationId::new(3).unwrap()),
            Err(ConnectionActorError::Draining)
        );
        assert_eq!(actor.enqueue_control(vec![9]), Err(ConnectionActorError::Draining));
        actor.enqueue_response(first.handle, vec![1]).unwrap();
        actor.enqueue_response(second.handle, vec![2]).unwrap();
        while let Some(frame) = actor.pop_outbound() {
            actor.complete_request(frame.request_handle.unwrap()).unwrap();
        }
        assert!(actor.drain_converged());
    }

    #[test]
    fn async_admission_does_not_claim_write_sequence() {
        let mut actor = actor();
        let first = actor
            .begin_request(CorrelationId::new(1).unwrap())
            .unwrap();
        let second = actor
            .begin_request(CorrelationId::new(2).unwrap())
            .unwrap();
        assert_eq!(first.handle.admitted_at_sequence(), 1);
        assert_eq!(second.handle.admitted_at_sequence(), 2);
        assert_eq!(actor.enqueue_response(second.handle, vec![2]).unwrap(), 1);
        assert_eq!(actor.enqueue_response(first.handle, vec![1]).unwrap(), 2);
    }

    #[test]
    fn oversized_frames_do_not_consume_state() {
        let mut actor = actor();
        let request = actor
            .begin_request(CorrelationId::new(1).unwrap())
            .unwrap();
        assert!(matches!(
            actor.enqueue_response(request.handle, vec![0; 9]),
            Err(ConnectionActorError::FrameTooLarge { limit: 8, actual: 9 })
        ));
        assert_eq!(actor.outbound_len(), 0);
        assert_eq!(actor.enqueue_response(request.handle, vec![1]).unwrap(), 1);
    }
}
'''
    replace_tests(path, tests)


def patch_disconnect_journal() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/src/disconnect_journal.rs"
    replace_once(
        path,
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\npub enum DisconnectState {",
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\n"
        "pub struct DisconnectUnknownEvidence {\n"
        "    pub outcome_digest: [u8; 32],\n"
        "    pub verifier_receipt_digest: [u8; 32],\n"
        "}\n\n"
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\n"
        "pub enum DisconnectState {",
        "unknown evidence type",
    )
    replace_once(
        path,
        "    Indeterminate {\n"
        "        binding: DisconnectDispatchBinding,\n"
        "    },",
        "    Indeterminate {\n"
        "        binding: DisconnectDispatchBinding,\n"
        "        unknown: Option<DisconnectUnknownEvidence>,\n"
        "    },",
        "bounded indeterminate state",
    )
    replace_once(
        path,
        "        if self.records.len() >= self.config.active_capacity {",
        "        if self.records.len().saturating_add(self.tombstones.len())\n"
        "            >= self.config.tombstone_capacity\n"
        "        {\n"
        "            return Err(DisconnectJournalError::TombstoneCapacityExceeded {\n"
        "                capacity: self.config.tombstone_capacity,\n"
        "            });\n"
        "        }\n"
        "        if self.records.len() >= self.config.active_capacity {",
        "archive reservation",
    )
    replace_once(
        path,
        "        let next = DisconnectRecord {\n"
        "            state: DisconnectState::Indeterminate { binding },\n"
        "            ..current\n"
        "        };",
        "        let next = DisconnectRecord {\n"
        "            state: DisconnectState::Indeterminate {\n"
        "                binding,\n"
        "                unknown: None,\n"
        "            },\n"
        "            ..current\n"
        "        };",
        "transport loss state",
    )
    replace_once(
        path,
        "            DisconnectState::Dispatched { binding }\n"
        "            | DisconnectState::Indeterminate { binding } => binding,",
        "            DisconnectState::Dispatched { binding }\n"
        "            | DisconnectState::Indeterminate { binding, .. } => binding,",
        "reconcile binding pattern",
    )
    replace_once(
        path,
        "        if !verifier.verify(&evidence) {\n"
        "            return Err(DisconnectJournalError::OutcomeVerificationFailed(id));\n"
        "        }\n"
        "        self.require_unique_verifier_receipt(id, evidence.verifier_receipt_digest)?;",
        "        if let DisconnectState::Indeterminate {\n"
        "            unknown: Some(previous),\n"
        "            ..\n"
        "        } = current.state\n"
        "        {\n"
        "            if evidence.kind == DisconnectOutcomeKind::Unknown {\n"
        "                if previous.outcome_digest == evidence.outcome_digest\n"
        "                    && previous.verifier_receipt_digest\n"
        "                        == evidence.verifier_receipt_digest\n"
        "                {\n"
        "                    return Ok((current, ReconciliationDisposition::Indeterminate));\n"
        "                }\n"
        "                return Err(DisconnectJournalError::OutcomeMismatch(id));\n"
        "            }\n"
        "            if previous.verifier_receipt_digest\n"
        "                == evidence.verifier_receipt_digest\n"
        "            {\n"
        "                return Err(DisconnectJournalError::OutcomeMismatch(id));\n"
        "            }\n"
        "        }\n"
        "        if !verifier.verify(&evidence) {\n"
        "            return Err(DisconnectJournalError::OutcomeVerificationFailed(id));\n"
        "        }\n"
        "        self.require_unique_verifier_receipt(id, evidence.verifier_receipt_digest)?;",
        "unknown replay fence",
    )
    replace_once(
        path,
        "            DisconnectOutcomeKind::Unknown => (\n"
        "                DisconnectState::Indeterminate {\n"
        "                    binding: expected_binding,\n"
        "                },\n"
        "                ReconciliationDisposition::Indeterminate,\n"
        "            ),",
        "            DisconnectOutcomeKind::Unknown => (\n"
        "                DisconnectState::Indeterminate {\n"
        "                    binding: expected_binding,\n"
        "                    unknown: Some(DisconnectUnknownEvidence {\n"
        "                        outcome_digest: evidence.outcome_digest,\n"
        "                        verifier_receipt_digest: evidence.verifier_receipt_digest,\n"
        "                    }),\n"
        "                },\n"
        "                ReconciliationDisposition::Indeterminate,\n"
        "            ),",
        "unknown state storage",
    )
    replace_once(
        path,
        "    pub fn tombstone_len(&self) -> usize {\n"
        "        self.tombstones.len()\n"
        "    }",
        "    pub fn tombstone_len(&self) -> usize {\n"
        "        self.tombstones.len()\n"
        "    }\n\n"
        "    pub fn verifier_receipt_count(&self) -> usize {\n"
        "        self.verifier_receipt_owners.len()\n"
        "    }",
        "receipt count accessor",
    )

    tests = r'''#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy, Debug)]
    struct ExactVerifier(u8);

    impl DisconnectOutcomeVerifier for ExactVerifier {
        fn verify(&self, evidence: &DisconnectOutcomeEvidence) -> bool {
            evidence.verifier_receipt_digest[0] == self.0
        }
    }

    fn journal(active: usize, tombstones: usize, attempts: u32) -> DisconnectJournal {
        DisconnectJournal::new(DisconnectJournalConfig {
            journal_id: DisconnectJournalId::new([7; 16]).unwrap(),
            epoch: DisconnectJournalEpoch::new(1).unwrap(),
            active_capacity: active,
            tombstone_capacity: tombstones,
            max_attempts: attempts,
        })
        .unwrap()
    }

    fn id(value: u64) -> DisconnectIntentId {
        DisconnectIntentId::new(value).unwrap()
    }

    fn worker(value: u64) -> WorkerId {
        WorkerId::new(value).unwrap()
    }

    fn digest(value: u8) -> [u8; 32] {
        [value; 32]
    }

    fn operation(generation: u64) -> DisconnectOperation {
        DisconnectOperation {
            socket_generation: generation,
            operation_digest: digest(generation as u8),
        }
    }

    fn dispatch(
        journal: &mut DisconnectJournal,
        intent: DisconnectIntentId,
        operation: DisconnectOperation,
        worker_id: WorkerId,
        endpoint: u8,
    ) -> DisconnectDispatchBinding {
        journal.insert(intent, operation).unwrap();
        let leased = journal.lease(intent, worker_id).unwrap();
        let token = match leased.state {
            DisconnectState::Leased { token, .. } => token,
            _ => panic!("expected lease"),
        };
        let record = journal
            .mark_dispatched(intent, worker_id, token, digest(endpoint))
            .unwrap();
        match record.state {
            DisconnectState::Dispatched { binding } => binding,
            _ => panic!("expected dispatch"),
        }
    }

    fn evidence(
        binding: DisconnectDispatchBinding,
        kind: DisconnectOutcomeKind,
        outcome: u8,
        verifier_receipt: u8,
    ) -> DisconnectOutcomeEvidence {
        DisconnectOutcomeEvidence {
            binding,
            kind,
            outcome_digest: digest(outcome),
            verifier_receipt_digest: digest(verifier_receipt),
        }
    }

    #[test]
    fn immutable_identity_and_lease_fences_fail_without_mutation() {
        let mut journal = journal(2, 2, 3);
        let original = journal.insert(id(1), operation(1)).unwrap();
        assert_eq!(journal.insert(id(1), operation(1)).unwrap(), original);
        assert_eq!(
            journal.insert(id(1), operation(2)),
            Err(DisconnectJournalError::ConflictingIntent(id(1)))
        );
        let leased = journal.lease(id(1), worker(1)).unwrap();
        let token = match leased.state {
            DisconnectState::Leased { token, .. } => token,
            _ => unreachable!(),
        };
        assert_eq!(
            journal.mark_dispatched(id(1), worker(2), token, digest(8)),
            Err(DisconnectJournalError::LeaseMismatch(id(1)))
        );
        assert_eq!(journal.get(id(1)), Some(leased));
    }

    #[test]
    fn possible_write_requires_verified_reconciliation_before_retry() {
        let mut journal = journal(2, 2, 3);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        journal
            .mark_transport_lost(id(1), binding.worker, binding.lease_token)
            .unwrap();
        assert!(matches!(
            journal.retry_before_dispatch(
                id(1),
                binding.worker,
                binding.lease_token,
                digest(90),
            ),
            Err(DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(_))
        ));
        let (_, disposition) = journal
            .reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::DefinitelyNotApplied, 71, 70),
                &ExactVerifier(70),
            )
            .unwrap();
        assert_eq!(disposition, ReconciliationDisposition::Pending);
        assert_eq!(journal.lease(id(1), worker(2)).unwrap().attempt, 2);
    }

    #[test]
    fn binding_and_verifier_mismatch_preserve_state() {
        let mut journal = journal(2, 2, 3);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let before = journal.get(id(1));
        let mut wrong = binding;
        wrong.journal_epoch = DisconnectJournalEpoch::new(2).unwrap();
        assert_eq!(
            journal.reconcile(
                id(1),
                evidence(wrong, DisconnectOutcomeKind::Applied, 80, 70),
                &ExactVerifier(70),
            ),
            Err(DisconnectJournalError::OutcomeBindingMismatch(id(1)))
        );
        assert_eq!(
            journal.reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Applied, 80, 70),
                &ExactVerifier(99),
            ),
            Err(DisconnectJournalError::OutcomeVerificationFailed(id(1)))
        );
        assert_eq!(journal.get(id(1)), before);
    }

    #[test]
    fn one_unknown_receipt_is_bounded_and_exactly_idempotent() {
        let mut journal = journal(1, 2, 3);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let unknown = evidence(binding, DisconnectOutcomeKind::Unknown, 81, 70);
        let accepted = journal.reconcile(id(1), unknown, &ExactVerifier(70)).unwrap();
        assert_eq!(accepted.1, ReconciliationDisposition::Indeterminate);
        assert_eq!(journal.verifier_receipt_count(), 1);
        assert_eq!(
            journal.reconcile(id(1), unknown, &ExactVerifier(99)).unwrap(),
            accepted
        );
        assert_eq!(journal.verifier_receipt_count(), 1);
        let before = journal.get(id(1));
        assert_eq!(
            journal.reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Unknown, 82, 71),
                &ExactVerifier(71),
            ),
            Err(DisconnectJournalError::OutcomeMismatch(id(1)))
        );
        assert_eq!(journal.get(id(1)), before);
        assert_eq!(journal.verifier_receipt_count(), 1);
        let terminal = journal
            .reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Applied, 90, 72),
                &ExactVerifier(72),
            )
            .unwrap();
        assert_eq!(terminal.1, ReconciliationDisposition::Applied);
        assert_eq!(journal.verifier_receipt_count(), 2);
    }

    #[test]
    fn outcome_and_verifier_receipts_cannot_cross_intents() {
        let mut journal = journal(2, 2, 3);
        let first = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let second = dispatch(&mut journal, id(2), operation(10), worker(2), 8);
        journal
            .reconcile(
                id(1),
                evidence(first, DisconnectOutcomeKind::Applied, 80, 70),
                &ExactVerifier(70),
            )
            .unwrap();
        assert!(matches!(
            journal.reconcile(
                id(2),
                evidence(second, DisconnectOutcomeKind::Applied, 80, 71),
                &ExactVerifier(71),
            ),
            Err(DisconnectJournalError::OutcomeReceiptReused { .. })
        ));
        assert!(matches!(
            journal.reconcile(
                id(2),
                evidence(second, DisconnectOutcomeKind::Applied, 81, 70),
                &ExactVerifier(70),
            ),
            Err(DisconnectJournalError::VerifierReceiptReused { .. })
        ));
    }

    #[test]
    fn admission_reserves_archive_space_and_prevents_epoch_deadlock() {
        let mut journal = journal(1, 1, 2);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        journal
            .reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Applied, 80, 70),
                &ExactVerifier(70),
            )
            .unwrap();
        journal.archive_terminal(id(1), digest(100)).unwrap();
        assert_eq!(
            journal.insert(id(2), operation(10)),
            Err(DisconnectJournalError::TombstoneCapacityExceeded { capacity: 1 })
        );
        assert_eq!(journal.len(), 0);
        journal
            .advance_epoch(DisconnectJournalEpoch::new(2).unwrap(), digest(110))
            .unwrap();
        journal.insert(id(2), operation(10)).unwrap();
    }

    #[test]
    fn every_admitted_record_can_be_archived() {
        let mut journal = journal(2, 2, 1);
        let first = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let second = dispatch(&mut journal, id(2), operation(10), worker(2), 8);
        for (intent, binding, receipt) in [(id(1), first, 70), (id(2), second, 71)] {
            journal
                .reconcile(
                    intent,
                    evidence(binding, DisconnectOutcomeKind::Rejected, 90, receipt),
                    &ExactVerifier(receipt),
                )
                .unwrap();
            journal.archive_terminal(intent, digest(100)).unwrap();
        }
        assert_eq!(journal.len(), 0);
        assert_eq!(journal.tombstone_len(), 2);
    }

    #[test]
    fn epoch_advance_rejects_old_dispatch_proof() {
        let mut journal = journal(1, 1, 1);
        let old = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        journal
            .reconcile(
                id(1),
                evidence(old, DisconnectOutcomeKind::Applied, 80, 70),
                &ExactVerifier(70),
            )
            .unwrap();
        journal.archive_terminal(id(1), digest(100)).unwrap();
        journal
            .advance_epoch(DisconnectJournalEpoch::new(2).unwrap(), digest(110))
            .unwrap();
        let current = dispatch(&mut journal, id(1), operation(11), worker(1), 8);
        assert_ne!(old.journal_epoch, current.journal_epoch);
        assert_eq!(
            journal.reconcile(
                id(1),
                evidence(old, DisconnectOutcomeKind::Applied, 80, 70),
                &ExactVerifier(70),
            ),
            Err(DisconnectJournalError::OutcomeBindingMismatch(id(1)))
        );
    }

    #[test]
    fn attempt_limit_dead_letters_atomically() {
        let mut journal = journal(1, 1, 1);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let (record, disposition) = journal
            .reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::DefinitelyNotApplied, 90, 70),
                &ExactVerifier(70),
            )
            .unwrap();
        assert_eq!(disposition, ReconciliationDisposition::DeadLettered);
        assert_eq!(record.state, DisconnectState::DeadLetter { reason: digest(90) });
    }
}
'''
    replace_tests(path, tests)


def patch_exports() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/src/lib.rs"
    replace_once(
        path,
        "    CorrelationId, InboundFrame, OutboundFrame, PendingRequest, MAX_CONNECTION_ACTOR_BUFFER_BYTES,",
        "    CorrelationId, InboundFrame, OutboundFrame, PendingRequest, RequestHandle,\n"
        "    MAX_CONNECTION_ACTOR_BUFFER_BYTES,",
        "request handle export",
    )
    replace_once(
        path,
        "    DisconnectOutcomeVerifier, DisconnectRecord, DisconnectState, LeaseToken,",
        "    DisconnectOutcomeVerifier, DisconnectRecord, DisconnectState,\n"
        "    DisconnectUnknownEvidence, LeaseToken,",
        "unknown evidence export",
    )


def main() -> None:
    patch_connection_actor()
    patch_disconnect_journal()
    patch_exports()
    for relative in (
        "scripts/one_shot_pr104_recovery_state_repair.py",
        ".github/workflows/pr104-recovery-state-repair.yml",
    ):
        (ROOT / relative).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
