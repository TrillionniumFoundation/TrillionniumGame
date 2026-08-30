#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::fmt::Write as _;

use trnm_contracts::{CommandId, Digest32};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, DatabaseProfile, EntityId, EventId, EventInput, IntentId,
    IntentKind, OutboxInput, PgRepository,
};

#[derive(Clone, Debug, Eq, PartialEq)]
enum Command {
    Bootstrap {
        entity: u8,
        generation: u64,
        state: u8,
        updated_at_ms: u64,
    },
    Head {
        entity: u8,
    },
    Apply {
        entity: u8,
        command: u8,
        fingerprint: u8,
        expected_revision: u64,
        generation: u64,
        state: u8,
        committed_at_ms: u64,
    },
    Help,
}

fn main() {
    if let Err(error) = run(env::args().skip(1), &env::vars().collect()) {
        eprintln!("trnm-pg-command: {error}");
        std::process::exit(1);
    }
}

fn run(
    arguments: impl Iterator<Item = String>,
    environment: &BTreeMap<String, String>,
) -> Result<(), String> {
    let command = parse_command(arguments)?;
    if command == Command::Help {
        print_help();
        return Ok(());
    }

    let database_url = required_environment(environment, "TRNM_DATABASE_URL")?;
    let profile = parse_profile(required_environment(environment, "TRNM_DATABASE_PROFILE")?)?;
    let source_commit = required_environment(environment, "TRNM_SCHEMA_SOURCE_COMMIT")?;
    validate_source_commit(source_commit)?;

    let mut repository = PgRepository::connect(database_url, profile)
        .map_err(|error| format!("database connect failed: {error}"))?;
    repository
        .bind_schema_metadata(source_commit, environment_u64(environment, "TRNM_SCHEMA_APPLIED_AT_MS")?)
        .map_err(|error| format!("schema metadata check failed: {error}"))?;

    match command {
        Command::Bootstrap {
            entity,
            generation,
            state,
            updated_at_ms,
        } => {
            let head = repository
                .bootstrap_entity(
                    entity_id(entity)?,
                    nonzero_u64(generation, "authority generation")?,
                    digest(state)?,
                    updated_at_ms,
                )
                .map_err(|error| format!("bootstrap failed: {error}"))?;
            println!(
                "{{\"outcome\":\"bootstrapped\",\"revision\":{},\"last_event_sequence\":{},\"authority_generation\":{},\"compatibility_credit\":false}}",
                head.revision, head.last_event_sequence, head.authority_generation
            );
        }
        Command::Head { entity } => match repository
            .load_head(entity_id(entity)?)
            .map_err(|error| format!("load head failed: {error}"))?
        {
            Some(head) => println!(
                "{{\"outcome\":\"found\",\"revision\":{},\"last_event_sequence\":{},\"authority_generation\":{},\"updated_at_ms\":{},\"compatibility_credit\":false}}",
                head.revision,
                head.last_event_sequence,
                head.authority_generation,
                head.updated_at_ms
            ),
            None => println!(
                "{{\"outcome\":\"not_found\",\"compatibility_credit\":false}}"
            ),
        },
        Command::Apply {
            entity,
            command,
            fingerprint,
            expected_revision,
            generation,
            state,
            committed_at_ms,
        } => {
            let request = build_request(
                entity,
                command,
                fingerprint,
                expected_revision,
                generation,
                state,
                committed_at_ms,
            )?;
            let outcome = repository
                .commit_command(&request)
                .map_err(|error| format!("commit failed: {error}"))?;
            let (kind, receipt) = match outcome {
                CommitOutcome::Applied(receipt) => ("applied", receipt),
                CommitOutcome::Duplicate(receipt) => ("duplicate", receipt),
            };
            let mut body = String::with_capacity(256);
            write!(
                body,
                "{{\"outcome\":\"{kind}\",\"revision\":{},\"first_event_sequence\":{},\"last_event_sequence\":{},\"event_count\":{},\"outbox_count\":{},\"compatibility_credit\":false}}",
                receipt.revision,
                receipt
                    .first_event_sequence
                    .map_or_else(|| "null".to_owned(), |value| value.to_string()),
                receipt.last_event_sequence,
                receipt.event_count,
                receipt.outbox.len()
            )
            .expect("writing to String cannot fail");
            println!("{body}");
        }
        Command::Help => unreachable!("handled before database connection"),
    }
    Ok(())
}

fn parse_command(mut arguments: impl Iterator<Item = String>) -> Result<Command, String> {
    let Some(name) = arguments.next() else {
        return Ok(Command::Help);
    };
    let options = parse_options(arguments)?;
    match name.as_str() {
        "bootstrap" => Ok(Command::Bootstrap {
            entity: option(&options, "entity-byte")?,
            generation: option(&options, "authority-generation")?,
            state: option(&options, "state-byte")?,
            updated_at_ms: option(&options, "updated-at-ms")?,
        }),
        "head" => Ok(Command::Head {
            entity: option(&options, "entity-byte")?,
        }),
        "apply" => Ok(Command::Apply {
            entity: option(&options, "entity-byte")?,
            command: option(&options, "command-byte")?,
            fingerprint: option(&options, "fingerprint-byte")?,
            expected_revision: option(&options, "expected-revision")?,
            generation: option(&options, "authority-generation")?,
            state: option(&options, "state-byte")?,
            committed_at_ms: option(&options, "committed-at-ms")?,
        }),
        "help" | "--help" | "-h" => {
            if options.is_empty() {
                Ok(Command::Help)
            } else {
                Err("help does not accept options".to_owned())
            }
        }
        other => Err(format!("unknown command {other:?}")),
    }
}

fn parse_options(arguments: impl Iterator<Item = String>) -> Result<BTreeMap<String, String>, String> {
    let mut arguments = arguments.peekable();
    let mut result = BTreeMap::new();
    while let Some(argument) = arguments.next() {
        let name = argument
            .strip_prefix("--")
            .ok_or_else(|| format!("expected --option, received {argument:?}"))?;
        if name.is_empty() {
            return Err("empty option name".to_owned());
        }
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

fn nonzero_u64(value: u64, label: &str) -> Result<u64, String> {
    if value == 0 {
        Err(format!("{label} must be non-zero"))
    } else {
        Ok(value)
    }
}

fn entity_id(value: u8) -> Result<EntityId, String> {
    Ok(EntityId::new([nonzero_byte(value, "entity byte")?; 16]))
}

fn digest(value: u8) -> Result<Digest32, String> {
    Ok(Digest32::new([nonzero_byte(value, "digest byte")?; 32]))
}

fn build_request(
    entity: u8,
    command: u8,
    fingerprint: u8,
    expected_revision: u64,
    generation: u64,
    state: u8,
    committed_at_ms: u64,
) -> Result<CommitRequest, String> {
    let command = nonzero_byte(command, "command byte")?;
    let event = command
        .checked_add(1)
        .ok_or_else(|| "command byte leaves no event-ID range".to_owned())?;
    let intent = command
        .checked_add(2)
        .ok_or_else(|| "command byte leaves no intent-ID range".to_owned())?;
    Ok(CommitRequest {
        entity: entity_id(entity)?,
        command: CommandId::new([command; 16]),
        fingerprint: digest(fingerprint)?,
        expected_revision,
        authority_generation: nonzero_u64(generation, "authority generation")?,
        next_state: digest(state)?,
        committed_at_ms,
        events: vec![EventInput {
            id: EventId::new([event; 16]),
            payload: Digest32::new([event; 32]),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([intent; 16]),
            kind: IntentKind::Broadcast,
            payload: Digest32::new([intent; 32]),
            available_at_ms: committed_at_ms,
        }],
    })
}

fn print_help() {
    println!(
        "trnm-pg-command source candidate\n\n\
         Required environment:\n\
           TRNM_DATABASE_URL\n\
           TRNM_DATABASE_PROFILE=postgresql|cockroachdb\n\
           TRNM_SCHEMA_SOURCE_COMMIT=<40 hex>\n\
           TRNM_SCHEMA_APPLIED_AT_MS=<u64>\n\n\
         Commands:\n\
           bootstrap --entity-byte N --authority-generation N --state-byte N --updated-at-ms N\n\
           head --entity-byte N\n\
           apply --entity-byte N --command-byte N --fingerprint-byte N --expected-revision N \\\n                 --authority-generation N --state-byte N --committed-at-ms N\n\n\
         The database must already contain the production-authoritative migration.\n\
         This binary does not apply migrations or grant durability, compatibility, HA or production credit."
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parser_requires_complete_typed_options() {
        let parsed = parse_command(
            [
                "apply",
                "--entity-byte",
                "1",
                "--command-byte",
                "2",
                "--fingerprint-byte",
                "3",
                "--expected-revision",
                "0",
                "--authority-generation",
                "1",
                "--state-byte",
                "4",
                "--committed-at-ms",
                "5",
            ]
            .map(str::to_owned)
            .into_iter(),
        )
        .unwrap();
        assert!(matches!(parsed, Command::Apply { command: 2, .. }));
        assert!(parse_command(["apply", "--entity-byte", "1"].map(str::to_owned).into_iter()).is_err());
    }

    #[test]
    fn request_builder_rejects_zero_and_overflowing_identity_material() {
        assert!(build_request(0, 2, 3, 0, 1, 4, 5).is_err());
        assert!(build_request(1, 0, 3, 0, 1, 4, 5).is_err());
        assert!(build_request(1, 255, 3, 0, 1, 4, 5).is_err());
        assert!(build_request(1, 2, 0, 0, 1, 4, 5).is_err());
        assert!(build_request(1, 2, 3, 0, 0, 4, 5).is_err());
    }

    #[test]
    fn environment_validation_is_fail_closed() {
        let empty = BTreeMap::new();
        assert!(run(["head", "--entity-byte", "1"].map(str::to_owned).into_iter(), &empty).is_err());

        let environment = BTreeMap::from([
            ("TRNM_DATABASE_URL".to_owned(), "postgresql://example.invalid/db".to_owned()),
            ("TRNM_DATABASE_PROFILE".to_owned(), "sqlite".to_owned()),
            ("TRNM_SCHEMA_SOURCE_COMMIT".to_owned(), "x".repeat(40)),
            ("TRNM_SCHEMA_APPLIED_AT_MS".to_owned(), "1".to_owned()),
        ]);
        assert!(run(["head", "--entity-byte", "1"].map(str::to_owned).into_iter(), &environment).is_err());
    }

    #[test]
    fn source_commit_requires_exact_hex_identity() {
        assert!(validate_source_commit("0123456789012345678901234567890123456789").is_ok());
        assert!(validate_source_commit("xyz").is_err());
        assert!(validate_source_commit(&"g".repeat(40)).is_err());
    }
}
