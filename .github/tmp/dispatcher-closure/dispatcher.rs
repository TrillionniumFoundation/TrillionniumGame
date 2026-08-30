use std::time::{SystemTime, UNIX_EPOCH};

use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode};

use super::{
    counter_overflow, invalid, IntentId, NodeId, OutboxLease, OutboxRetryOutcome, PgRepository,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeliveryFailureClass {
    Retryable,
    Terminal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DeliveryFailure {
    pub class: DeliveryFailureClass,
    pub reason: Digest32,
}

impl DeliveryFailure {
    #[must_use]
    pub const fn retryable(reason: Digest32) -> Self {
        Self {
            class: DeliveryFailureClass::Retryable,
            reason,
        }
    }

    #[must_use]
    pub const fn terminal(reason: Digest32) -> Self {
        Self {
            class: DeliveryFailureClass::Terminal,
            reason,
        }
    }
}

pub trait OutboxSink {
    fn reconcile(
        &mut self,
        idempotency_key: IntentId,
        lease: &OutboxLease,
    ) -> Result<Option<Digest32>, DeliveryFailure>;

    fn deliver(
        &mut self,
        idempotency_key: IntentId,
        lease: &OutboxLease,
    ) -> Result<Digest32, DeliveryFailure>;
}

pub trait DispatchClock {
    fn now_ms(&mut self) -> Result<u64, DomainError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemDispatchClock;

impl DispatchClock for SystemDispatchClock {
    fn now_ms(&mut self) -> Result<u64, DomainError> {
        let duration = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| invalid("system_clock_before_unix_epoch"))?;
        u64::try_from(duration.as_millis()).map_err(|_| counter_overflow())
    }
}

pub trait OutboxRepository {
    fn claim(
        &mut self,
        owner: NodeId,
        now_ms: u64,
        lease_duration_ms: u64,
        max_attempts: u32,
        exhausted_reason: Digest32,
        limit: usize,
    ) -> Result<Vec<OutboxLease>, DomainError>;

    fn complete(
        &mut self,
        lease: &OutboxLease,
        receipt: Digest32,
        completed_at_ms: u64,
    ) -> Result<(), DomainError>;

    fn retry_or_dead_letter(
        &mut self,
        lease: &OutboxLease,
        now_ms: u64,
        next_available_at_ms: u64,
        max_attempts: u32,
        dead_reason: Digest32,
    ) -> Result<OutboxRetryOutcome, DomainError>;
}

impl OutboxRepository for PgRepository {
    fn claim(
        &mut self,
        owner: NodeId,
        now_ms: u64,
        lease_duration_ms: u64,
        max_attempts: u32,
        exhausted_reason: Digest32,
        limit: usize,
    ) -> Result<Vec<OutboxLease>, DomainError> {
        self.claim_outbox(
            owner,
            now_ms,
            lease_duration_ms,
            max_attempts,
            exhausted_reason,
            limit,
        )
    }

    fn complete(
        &mut self,
        lease: &OutboxLease,
        receipt: Digest32,
        completed_at_ms: u64,
    ) -> Result<(), DomainError> {
        self.complete_outbox(lease, receipt, completed_at_ms)
    }

    fn retry_or_dead_letter(
        &mut self,
        lease: &OutboxLease,
        now_ms: u64,
        next_available_at_ms: u64,
        max_attempts: u32,
        dead_reason: Digest32,
    ) -> Result<OutboxRetryOutcome, DomainError> {
        self.retry_or_dead_letter_outbox(
            lease,
            now_ms,
            next_available_at_ms,
            max_attempts,
            dead_reason,
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DispatchConfig {
    pub lease_duration_ms: u64,
    pub retry_delay_ms: u64,
    pub max_attempts: u32,
    pub batch_limit: usize,
    pub exhausted_reason: Digest32,
}

impl DispatchConfig {
    pub fn validate(self) -> Result<Self, DomainError> {
        if self.lease_duration_ms == 0
            || self.retry_delay_ms == 0
            || self.max_attempts == 0
            || self.batch_limit == 0
            || self.batch_limit > 64
            || self.exhausted_reason.is_zero()
        {
            return Err(invalid("invalid_outbox_dispatch_config"));
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct DispatchReport {
    pub claimed: usize,
    pub reconciled: usize,
    pub delivered: usize,
    pub retried: usize,
    pub dead_lettered: usize,
    pub stale_leases: usize,
}

pub struct OutboxDispatcher<'a, R, S, C> {
    repository: &'a mut R,
    sink: &'a mut S,
    clock: &'a mut C,
    owner: NodeId,
    config: DispatchConfig,
}

impl<'a, R, S, C> OutboxDispatcher<'a, R, S, C>
where
    R: OutboxRepository,
    S: OutboxSink,
    C: DispatchClock,
{
    pub fn new(
        repository: &'a mut R,
        sink: &'a mut S,
        clock: &'a mut C,
        owner: NodeId,
        config: DispatchConfig,
    ) -> Result<Self, DomainError> {
        if owner.is_zero() {
            return Err(invalid("invalid_outbox_dispatch_owner"));
        }
        Ok(Self {
            repository,
            sink,
            clock,
            owner,
            config: config.validate()?,
        })
    }

    pub fn run_once(&mut self) -> Result<DispatchReport, DomainError> {
        let claim_time = self.clock.now_ms()?;
        let leases = self.repository.claim(
            self.owner,
            claim_time,
            self.config.lease_duration_ms,
            self.config.max_attempts,
            self.config.exhausted_reason,
            self.config.batch_limit,
        )?;
        let mut report = DispatchReport {
            claimed: leases.len(),
            ..DispatchReport::default()
        };
        for lease in leases {
            let key = lease.id;
            match self.sink.reconcile(key, &lease) {
                Ok(Some(receipt)) => {
                    report.reconciled = report.reconciled.saturating_add(1);
                    self.complete_or_record_stale(&lease, receipt, &mut report)?;
                }
                Ok(None) => match self.sink.deliver(key, &lease) {
                    Ok(receipt) => {
                        report.delivered = report.delivered.saturating_add(1);
                        self.complete_or_record_stale(&lease, receipt, &mut report)?;
                    }
                    Err(failure) => {
                        self.transition_failure(&lease, failure, &mut report)?;
                    }
                },
                Err(failure) => {
                    self.transition_failure(&lease, failure, &mut report)?;
                }
            }
        }
        Ok(report)
    }

    fn complete_or_record_stale(
        &mut self,
        lease: &OutboxLease,
        receipt: Digest32,
        report: &mut DispatchReport,
    ) -> Result<(), DomainError> {
        if receipt.is_zero() {
            return Err(invalid("invalid_outbox_delivery_receipt"));
        }
        let completed_at_ms = self.clock.now_ms()?;
        match self.repository.complete(lease, receipt, completed_at_ms) {
            Ok(()) => Ok(()),
            Err(error) if error.code() == StableCode::Aborted => {
                report.stale_leases = report.stale_leases.saturating_add(1);
                Ok(())
            }
            Err(error) => Err(error),
        }
    }

    fn transition_failure(
        &mut self,
        lease: &OutboxLease,
        failure: DeliveryFailure,
        report: &mut DispatchReport,
    ) -> Result<(), DomainError> {
        if failure.reason.is_zero() {
            return Err(invalid("invalid_outbox_delivery_failure_reason"));
        }
        let now_ms = self.clock.now_ms()?;
        let next_available_at_ms = now_ms
            .checked_add(self.config.retry_delay_ms)
            .ok_or_else(counter_overflow)?;
        let max_attempts = match failure.class {
            DeliveryFailureClass::Retryable => self.config.max_attempts,
            DeliveryFailureClass::Terminal => lease.attempt,
        };
        match self.repository.retry_or_dead_letter(
            lease,
            now_ms,
            next_available_at_ms,
            max_attempts,
            failure.reason,
        ) {
            Ok(OutboxRetryOutcome::Pending { .. }) => {
                report.retried = report.retried.saturating_add(1);
                Ok(())
            }
            Ok(OutboxRetryOutcome::DeadLetter { .. }) => {
                report.dead_lettered = report.dead_lettered.saturating_add(1);
                Ok(())
            }
            Err(error) if error.code() == StableCode::Aborted => {
                report.stale_leases = report.stale_leases.saturating_add(1);
                Ok(())
            }
            Err(error) => Err(error),
        }
    }
}

impl<R, S, C> std::fmt::Debug for OutboxDispatcher<'_, R, S, C> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("OutboxDispatcher")
            .field("owner", &self.owner)
            .field("config", &self.config)
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{EntityId, IntentKind};
    use trnm_contracts::CommandId;

    #[derive(Debug)]
    struct FakeClock {
        values: Vec<u64>,
    }

    impl DispatchClock for FakeClock {
        fn now_ms(&mut self) -> Result<u64, DomainError> {
            if self.values.is_empty() {
                return Err(DomainError::new(
                    StableCode::Internal,
                    "fake_clock_exhausted",
                    RetryClass::Never,
                ));
            }
            Ok(self.values.remove(0))
        }
    }

    #[derive(Debug)]
    struct FakeRepository {
        leases: Vec<OutboxLease>,
        completed: Vec<(IntentId, Digest32)>,
        transitions: Vec<(IntentId, u32, Digest32)>,
    }

    impl OutboxRepository for FakeRepository {
        fn claim(
            &mut self,
            _owner: NodeId,
            _now_ms: u64,
            _lease_duration_ms: u64,
            _max_attempts: u32,
            _exhausted_reason: Digest32,
            _limit: usize,
        ) -> Result<Vec<OutboxLease>, DomainError> {
            Ok(std::mem::take(&mut self.leases))
        }

        fn complete(
            &mut self,
            lease: &OutboxLease,
            receipt: Digest32,
            _completed_at_ms: u64,
        ) -> Result<(), DomainError> {
            self.completed.push((lease.id, receipt));
            Ok(())
        }

        fn retry_or_dead_letter(
            &mut self,
            lease: &OutboxLease,
            _now_ms: u64,
            _next_available_at_ms: u64,
            max_attempts: u32,
            reason: Digest32,
        ) -> Result<OutboxRetryOutcome, DomainError> {
            self.transitions.push((lease.id, max_attempts, reason));
            if lease.attempt >= max_attempts {
                Ok(OutboxRetryOutcome::DeadLetter {
                    attempt: lease.attempt,
                    reason,
                })
            } else {
                Ok(OutboxRetryOutcome::Pending {
                    next_available_at_ms: 20,
                    attempt: lease.attempt,
                })
            }
        }
    }

    #[derive(Debug)]
    struct FakeSink {
        reconcile: Result<Option<Digest32>, DeliveryFailure>,
        deliver: Result<Digest32, DeliveryFailure>,
        delivery_calls: usize,
    }

    impl OutboxSink for FakeSink {
        fn reconcile(
            &mut self,
            idempotency_key: IntentId,
            lease: &OutboxLease,
        ) -> Result<Option<Digest32>, DeliveryFailure> {
            assert_eq!(idempotency_key, lease.id);
            self.reconcile
        }

        fn deliver(
            &mut self,
            idempotency_key: IntentId,
            lease: &OutboxLease,
        ) -> Result<Digest32, DeliveryFailure> {
            assert_eq!(idempotency_key, lease.id);
            self.delivery_calls = self.delivery_calls.saturating_add(1);
            self.deliver
        }
    }

    fn lease(attempt: u32) -> OutboxLease {
        OutboxLease {
            id: IntentId::new([1; 16]),
            entity: EntityId::new([2; 16]),
            command: CommandId::new([3; 16]),
            kind: IntentKind::ExternalEffect,
            payload: Digest32::new([4; 32]),
            attempt,
            lease_generation: 1,
            owner: NodeId::new([5; 16]),
            lease_expires_at_ms: 100,
        }
    }

    fn config() -> DispatchConfig {
        DispatchConfig {
            lease_duration_ms: 50,
            retry_delay_ms: 10,
            max_attempts: 3,
            batch_limit: 8,
            exhausted_reason: Digest32::new([9; 32]),
        }
    }

    #[test]
    fn reconciliation_receipt_prevents_duplicate_delivery() {
        let receipt = Digest32::new([7; 32]);
        let mut repository = FakeRepository {
            leases: vec![lease(1)],
            completed: Vec::new(),
            transitions: Vec::new(),
        };
        let mut sink = FakeSink {
            reconcile: Ok(Some(receipt)),
            deliver: Ok(Digest32::new([8; 32])),
            delivery_calls: 0,
        };
        let mut clock = FakeClock { values: vec![1, 2] };
        let mut dispatcher = OutboxDispatcher::new(
            &mut repository,
            &mut sink,
            &mut clock,
            NodeId::new([6; 16]),
            config(),
        )
        .unwrap();
        let report = dispatcher.run_once().unwrap();
        assert_eq!(report.reconciled, 1);
        assert_eq!(report.delivered, 0);
        assert_eq!(sink.delivery_calls, 0);
        assert_eq!(repository.completed, vec![(IntentId::new([1; 16]), receipt)]);
    }

    #[test]
    fn retryable_and_terminal_failures_use_distinct_attempt_ceiling() {
        for (failure, expected_max, expected_dead) in [
            (DeliveryFailure::retryable(Digest32::new([10; 32])), 3, 0),
            (DeliveryFailure::terminal(Digest32::new([11; 32])), 1, 1),
        ] {
            let mut repository = FakeRepository {
                leases: vec![lease(1)],
                completed: Vec::new(),
                transitions: Vec::new(),
            };
            let mut sink = FakeSink {
                reconcile: Ok(None),
                deliver: Err(failure),
                delivery_calls: 0,
            };
            let mut clock = FakeClock { values: vec![1, 2] };
            let mut dispatcher = OutboxDispatcher::new(
                &mut repository,
                &mut sink,
                &mut clock,
                NodeId::new([6; 16]),
                config(),
            )
            .unwrap();
            let report = dispatcher.run_once().unwrap();
            assert_eq!(repository.transitions[0].1, expected_max);
            assert_eq!(report.dead_lettered, expected_dead);
            assert_eq!(report.retried, 1 - expected_dead);
        }
    }

    #[test]
    fn invalid_config_and_zero_owner_fail_before_claim() {
        let mut repository = FakeRepository {
            leases: vec![],
            completed: vec![],
            transitions: vec![],
        };
        let mut sink = FakeSink {
            reconcile: Ok(None),
            deliver: Ok(Digest32::new([8; 32])),
            delivery_calls: 0,
        };
        let mut clock = FakeClock { values: vec![1] };
        assert_eq!(
            OutboxDispatcher::new(
                &mut repository,
                &mut sink,
                &mut clock,
                NodeId::new([0; 16]),
                config(),
            )
            .unwrap_err()
            .reason(),
            "invalid_outbox_dispatch_owner"
        );
    }
}
