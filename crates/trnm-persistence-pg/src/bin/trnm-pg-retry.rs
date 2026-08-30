#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::thread;
use std::time::{Duration, Instant};

use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, DatabaseProfile, EntityId, EventId, EventInput, IntentId,
    IntentKind, OutboxInput, PgRepository,
};

const DEFAULT_MAX_ATTEMPTS: u32 = 5;
const MAX_MAX_ATTEMPTS: u32 = 16;
const DEFAULT_TOTAL_DEADLINE_MS: u64 = 10_000;
const DEFAULT_BASE_BACKOFF_MS: u64 = 10;
const MAX_BACKOFF_MS: u64 = 1_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RetryPolicy {
    max_attempts: u32,
    total_deadline: Duration,
    base_backoff: Duration,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RetryDecision {
    RetryAfter(Duration),
    Stop,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ApplyArguments {
    entity: u8,
    command: u8,
    fingerprint: u8,
    expected_revision: u64,
    authority_generation: u64,
    state: u8,
    committed_at_ms: u64,
}

fn main() {
    if let Err(error) = run(env::args().skip(1), &env::vars().collect()) {
        eprintln!("trnm-pg-retry: {error}");
        std::process::exit(1);
    }
}

fn run(
    arguments: impl Iterator<Item = String>,
    environment: &BTreeMap<String, String>,
) -> Result<(), String> {
    let arguments = parse_apply_arguments(arguments)?;
    let policy = policy_from_environment(environment)?;
    let database_url = required_environment(environment, "TRNM_DATABASE_URL")?;
    let profile = parse_profile(required_environment(environment, "TRNM_DATABASE_PROFILE")?)?;
    let source_commit = required_environment(environment, "TRNM_SCHEMA_SOURCE_COMMIT")?;
    validate_source_commit(source_commit)?;
    let schema_applied_at_ms = environment_u64(environment, "TRNM_SCHEMA_APPLIED_AT_MS")?;
    let request = build_request(arguments)?;

    let started = Instant::now();
    let mut attempt = 0_u32;
    loop {
        attempt = attempt
            .checked_add(1)
            .ok_or_else(|| "retry attempt counter overflow".to_owned())?;
        let result = (|| {
            let mut repository = PgRepository::connect(database_url, profile)?;
            repository.bind_schema_metadata(source_commit, schema_applied_at_ms)?;
            repository.commit_command(&request)
        })();

        match result {
            Ok(outcome) => {
                let (kind, receipt) = match outcome {
                    CommitOutcome::Applied(receipt) => ("applied", receipt),
                    CommitOutcome::Duplicate(receipt) => ("duplicate", receipt),
                };
                println!(
                    "{{\"outcome\":\"{kind}\",\"attempts\":{attempt},\"revision\":{},\"event_count\":{},\"outbox_count\":{},\"elapsed_ms\":{},\"compatibility_credit\":false}}",
                    receipt.revision,
                    receipt.event_count,
                    receipt.outbox.len(),
                    started.elapsed().as_millis()
                );
                return Ok(());
            }
            Err(error) => match retry_decision(policy, attempt, started.elapsed(), error) {
                RetryDecision::RetryAfter(delay) => {
                    eprintln!(
                        "trnm-pg-retry attempt={attempt} retry={:?} reason={} delay_ms={}",
                        error.retry(),
                        error.reason(),
                        delay.as_millis()
                    );
                    thread::sleep(delay);
                }
                RetryDecision::Stop => {
                    return Err(format!(
                        "commit stopped after {attempt} attempt(s): code={} reason={} retry={:?} elapsed_ms={}",
                        error.code().as_str(),
                        error.reason(),
                        error.retry(),
                        started.elapsed().as_millis()
                    ));
                }
            },
        }
    }
}

fn retry_decision(
    policy: RetryPolicy,
    attempt: u32,
    elapsed: Duration,
    error: DomainError,
) -> RetryDecision {
    if attempt >= policy.max_attempts || elapsed >= policy.total_deadline {
        return RetryDecision::Stop;
    }
    let delay = match error.retry() {
        RetryClass::SafeImmediate => Duration::ZERO,
        RetryClass::SafeBackoff => exponential_backoff(policy.base_backoff, attempt),
        RetryClass::Never | RetryClass::ResyncRequired => return RetryDecision::Stop,
    };
    if elapsed.saturating_add(delay) >= policy.total_deadline {
        RetryDecision::Stop
    } else {
        RetryDecision::RetryAfter(delay)
    }
}

fn exponential_backoff(base: Duration, completed_attempts: u32) -> Duration {
    let exponent = completed_attempts.saturating_sub(1).min(10);
    let multiplier = 1_u64.checked_shl(exponent).unwrap_or(u64::MAX);
    let milliseconds = u64::try_from(base.as_millis())
        .unwrap_or(u64::MAX)
        .saturating_mul(multiplier)
        .min(MAX_BACKOFF_MS);
    Duration::from_millis(milliseconds)
}

fn policy_from_environment(environment: &BTreeMap<String, String>) -> Result<RetryPolicy, String> {
    let max_attempts = optional_u32(
        environment,
        "TRNM_DATABASE_MAX_ATTEMPTS",
        DEFAULT_MAX_ATTEMPTS,
    )?;
    if !(1..=MAX_MAX_ATTEMPTS).contains(&max_attempts) {
        return Err(format!(
            "TRNM_DATABASE_MAX_ATTEMPTS must be in 1..={MAX_MAX_ATTEMPTS}"
        ));
    }
    let total_deadline_ms = optional_u64(
        environment,
        "TRNM_DATABASE_TOTAL_DEADLINE_MS",
        DEFAULT_TOTAL_DEADLINE_MS,
    )?;
    if total_deadline_ms == 0 {
        return Err("TRNM_DATABASE_TOTAL_DEADLINE_MS must be greater than zero".to_owned());
    }
    let base_backoff_ms = optional_u64(
        environment,
        "TRNM_DATABASE_BASE_BACKOFF_MS",
        DEFAULT_BASE_BACKOFF_MS,
    )?;
    if base_backoff_ms > MAX_BACKOFF_MS {
        return Err(format!(
            "TRNM_DATABASE_BASE_BACKOFF_MS must be <= {MAX_BACKOFF_MS}"
        ));
    }
    Ok(RetryPolicy {
        max_attempts,
        total_deadline: Duration::from_millis(total_deadline_ms),
        base_backoff: Duration::from_millis(base_backoff_ms),
    })
}

fn parse_apply_arguments(arguments: impl Iterator<Item = String>) -> Result<ApplyArguments, String> {
    let mut arguments = arguments.peekable();
    let command = arguments.next().ok_or_else(|| {
        "expected apply command and typed options; use --help in trnm-pg-command for the field contract"
            .to_owned()
    })?;
    if command != "apply" {
        return Err(format!("expected apply command, received {command:?}"));
    }
    let options = parse_options(arguments)?;
    Ok(ApplyArguments {
        entity: option(&options, "entity-byte")?,
        command: option(&options, "command-byte")?,
        fingerprint: option(&options, "fingerprint-byte")?,
        expected_revision: option(&options, "expected-revision")?,
        authority_generation: option(&options, "authority-generation")?,
        state: option(&options, "state-byte")?,
        committed_at_ms: option(&options, "committed-at-ms")?,
    })
}

fn parse_options(arguments: impl Iterator<Item = String>) -> Result<BTreeMap<String, String>, String> {
    let mut arguments = arguments.peekable();
    let mut result = BTreeMap::new();
    while let Some(argument) = arguments.next() {
        let name = argument
            .strip_prefix("--")
            .ok_or_else(|| format!("expected --option, received {argument:?}"))?;
        let value = arguments
            .next()
            .ok_or_else(|| format!("--{name} requires a value"))?;
        if value.starts_with("--") {
            return Err(format!("--{name} requires a value"));
        }
        if result.insert(name.to_owned(), value).is_some() {
            return Err(format!("duplicate option --{name}"));
        }
    }
    Ok(result)
}

fn option<T>(options: &BTreeMap<String, String>, name: &str) -> Result<T, String>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    options
        .get(name)
        .ok_or_else(|| format!("missing --{name}"))?
        .parse()
        .map_err(|error| format!("invalid --{name}: {error}"))
}

fn required_environment<'a>(
    environment: &'a BTreeMap<String, String>,
    name: &str,
) -> Result<&'a str, String> {
    match environment.get(name) {
        Some(value) if !value.is_empty() => Ok(value),
        _ => Err(format!("{name} is required and must not be empty")),
    }
}

fn environment_u64(environment: &BTreeMap<String, String>, name: &str) -> Result<u64, String> {
    required_environment(environment, name)?
        .parse()
        .map_err(|error| format!("invalid {name}: {error}"))
}

fn optional_u64(
    environment: &BTreeMap<String, String>,
    name: &str,
    default: u64,
) -> Result<u64, String> {
    environment
        .get(name)
        .map_or(Ok(default), |value| {
            value.parse().map_err(|error| format!("invalid {name}: {error}"))
        })
}

fn optional_u32(
    environment: &BTreeMap<String, String>,
    name: &str,
    default: u32,
) -> Result<u32, String> {
    environment
        .get(name)
        .map_or(Ok(default), |value| {
            value.parse().map_err(|error| format!("invalid {name}: {error}"))
        })
}

fn parse_profile(value: &str) -> Result<DatabaseProfile, String> {
    match value {
        "postgresql" => Ok(DatabaseProfile::PostgreSql),
        "cockroachdb" => Ok(DatabaseProfile::CockroachDb),
        other => Err(format!(
            "unsupported TRNM_DATABASE_PROFILE={other:?}; expected postgresql or cockroachdb"
        )),
    }
}

fn validate_source_commit(value: &str) -> Result<(), String> {
    if value.len() != 40 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("TRNM_SCHEMA_SOURCE_COMMIT must be exactly 40 hexadecimal characters".to_owned());
    }
    Ok(())
}

fn nonzero_byte(value: u8, label: &str) -> Result<u8, String> {
    if value == 0 {
        Err(format!("{label} must be non-zero"))
    } else {
        Ok(value)
    }
}

fn build_request(arguments: ApplyArguments) -> Result<CommitRequest, String> {
    let entity = nonzero_byte(arguments.entity, "entity byte")?;
    let command = nonzero_byte(arguments.command, "command byte")?;
    let fingerprint = nonzero_byte(arguments.fingerprint, "fingerprint byte")?;
    let state = nonzero_byte(arguments.state, "state byte")?;
    if arguments.authority_generation == 0 {
        return Err("authority generation must be non-zero".to_owned());
    }
    let event = command
        .checked_add(1)
        .ok_or_else(|| "command byte leaves no event-ID range".to_owned())?;
    let intent = command
        .checked_add(2)
        .ok_or_else(|| "command byte leaves no intent-ID range".to_owned())?;
    Ok(CommitRequest {
        entity: EntityId::new([entity; 16]),
        command: CommandId::new([command; 16]),
        fingerprint: Digest32::new([fingerprint; 32]),
        expected_revision: arguments.expected_revision,
        authority_generation: arguments.authority_generation,
        next_state: Digest32::new([state; 32]),
        committed_at_ms: arguments.committed_at_ms,
        events: vec![EventInput {
            id: EventId::new([event; 16]),
            payload: Digest32::new([event; 32]),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([intent; 16]),
            kind: IntentKind::Broadcast,
            payload: Digest32::new([intent; 32]),
            available_at_ms: arguments.committed_at_ms,
        }],
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_contracts::{StableCode, RetryClass};

    fn error(retry: RetryClass) -> DomainError {
        DomainError::new(StableCode::Aborted, "test", retry)
    }

    fn policy() -> RetryPolicy {
        RetryPolicy {
            max_attempts: 5,
            total_deadline: Duration::from_millis(500),
            base_backoff: Duration::from_millis(10),
        }
    }

    #[test]
    fn only_explicitly_safe_classes_retry() {
        assert_eq!(
            retry_decision(policy(), 1, Duration::ZERO, error(RetryClass::SafeImmediate)),
            RetryDecision::RetryAfter(Duration::ZERO)
        );
        assert_eq!(
            retry_decision(policy(), 1, Duration::ZERO, error(RetryClass::SafeBackoff)),
            RetryDecision::RetryAfter(Duration::from_millis(10))
        );
        assert_eq!(
            retry_decision(policy(), 1, Duration::ZERO, error(RetryClass::Never)),
            RetryDecision::Stop
        );
        assert_eq!(
            retry_decision(policy(), 1, Duration::ZERO, error(RetryClass::ResyncRequired)),
            RetryDecision::Stop
        );
    }

    #[test]
    fn attempt_and_total_deadline_are_hard_bounds() {
        assert_eq!(
            retry_decision(policy(), 5, Duration::ZERO, error(RetryClass::SafeImmediate)),
            RetryDecision::Stop
        );
        assert_eq!(
            retry_decision(
                policy(),
                1,
                Duration::from_millis(499),
                error(RetryClass::SafeBackoff)
            ),
            RetryDecision::Stop
        );
    }

    #[test]
    fn backoff_is_bounded_and_monotonic() {
        let values: Vec<_> = (1..=16)
            .map(|attempt| exponential_backoff(Duration::from_millis(10), attempt))
            .collect();
        assert!(values.windows(2).all(|pair| pair[0] <= pair[1]));
        assert_eq!(values.last().copied(), Some(Duration::from_millis(MAX_BACKOFF_MS)));
    }

    #[test]
    fn policy_rejects_unbounded_configuration() {
        let too_many = BTreeMap::from([(
            "TRNM_DATABASE_MAX_ATTEMPTS".to_owned(),
            (MAX_MAX_ATTEMPTS + 1).to_string(),
        )]);
        assert!(policy_from_environment(&too_many).is_err());
        let zero_deadline = BTreeMap::from([(
            "TRNM_DATABASE_TOTAL_DEADLINE_MS".to_owned(),
            "0".to_owned(),
        )]);
        assert!(policy_from_environment(&zero_deadline).is_err());
    }

    #[test]
    fn request_builder_keeps_retry_identity_stable() {
        let arguments = ApplyArguments {
            entity: 1,
            command: 2,
            fingerprint: 3,
            expected_revision: 0,
            authority_generation: 1,
            state: 4,
            committed_at_ms: 5,
        };
        assert_eq!(build_request(arguments).unwrap(), build_request(arguments).unwrap());
    }
}
