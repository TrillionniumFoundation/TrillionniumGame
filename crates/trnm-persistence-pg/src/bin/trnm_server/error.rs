use std::fmt;
use std::io;

use trnm_contracts::{DomainError, RetryClass};
use trnm_persistence_pg::DatabaseProfile;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InputError {
    reason: &'static str,
}

impl InputError {
    #[must_use]
    pub const fn new(reason: &'static str) -> Self {
        Self { reason }
    }

    #[cfg(test)]
    #[must_use]
    pub const fn reason(self) -> &'static str {
        self.reason
    }
}

impl fmt::Display for InputError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.reason)
    }
}

impl std::error::Error for InputError {}

#[derive(Debug)]
pub enum ServerError {
    Input(InputError),
    Domain(DomainError),
    Database(postgres::Error),
    Io(io::Error),
    Configuration(&'static str),
}

impl fmt::Display for ServerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Input(error) => write!(formatter, "invalid input: {error}"),
            Self::Domain(error) => write!(formatter, "domain failure: {}", error.code().as_str()),
            Self::Database(error) => match error.code() {
                Some(code) => write!(
                    formatter,
                    "database operation failed (SQLSTATE {})",
                    code.code()
                ),
                None => formatter.write_str("database transport operation failed"),
            },
            Self::Io(error) => write!(formatter, "I/O failure: {error}"),
            Self::Configuration(reason) => write!(formatter, "configuration failure: {reason}"),
        }
    }
}

impl std::error::Error for ServerError {}

impl From<InputError> for ServerError {
    fn from(value: InputError) -> Self {
        Self::Input(value)
    }
}

impl From<DomainError> for ServerError {
    fn from(value: DomainError) -> Self {
        Self::Domain(value)
    }
}

impl From<postgres::Error> for ServerError {
    fn from(value: postgres::Error) -> Self {
        Self::Database(value)
    }
}

impl From<io::Error> for ServerError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MigrationPhase {
    BuildPool,
    AcquireSession,
    ApplyAuthoritativeChain,
}

impl MigrationPhase {
    const fn as_str(self) -> &'static str {
        match self {
            Self::BuildPool => "build-pool",
            Self::AcquireSession => "acquire-session",
            Self::ApplyAuthoritativeChain => "apply-authoritative-chain",
        }
    }
}

const MIGRATION_DIAGNOSTIC_MAX_BYTES: usize = 512;

// Only fixed reasons from the pool, authoritative migrator and their shared
// database-error classifier are operator-safe. New reasons fail closed here.
const MIGRATION_REASONS: &[&str] = &[
    "authoritative_schema_baseline_column_drift",
    "authoritative_schema_baseline_column_missing",
    "authoritative_schema_catalog_budget",
    "authoritative_schema_namespace_rejected",
    "authoritative_schema_not_ready",
    "authoritative_schema_partial_action_drift",
    "authoritative_schema_revision_prefix_drift",
    "authoritative_schema_table_inventory_drift",
    "authoritative_schema_table_kind_drift",
    "authoritative_schema_unrecognized_column",
    "authoritative_schema_upgrade_incomplete",
    "counter_overflow",
    "database_connection_failure",
    "database_constraint_violation",
    "database_deadlock",
    "database_foreign_key_violation",
    "database_internal_error",
    "database_pool_acquire_timeout",
    "database_pool_initialization_failed",
    "database_pool_policy_invalid",
    "database_serialization_failure",
    "database_timeout_millis_overflow",
    "database_tls_connector_invalid",
    "database_tls_identity_cert_key_pair_required",
    "database_tls_identity_invalid",
    "database_tls_pool_initialization_failed",
    "database_tls_root_certificate_invalid",
    "database_transport_failure",
    "database_unique_violation",
    "database_schema_missing",
    "invalid_legacy_storage_writer_role",
    "invalid_schema_source_commit",
    "legacy_storage_writer_barrier_required",
    "legacy_storage_writer_not_fenced",
    "legacy_storage_writer_privilege_catalog_missing",
    "legacy_storage_writer_role_missing",
    "schema_action_catalog_not_ready",
    "schema_catalog_duplicate_column",
    "schema_catalog_nullability_invalid",
    "schema_metadata_binding_failed",
    "schema_metadata_cardinality_invalid",
    "schema_metadata_missing",
    "schema_metadata_provenance_invalid",
    "schema_metadata_publish_conflict",
    "schema_metadata_singleton_invalid",
    "schema_metadata_transition_not_adjacent",
    "schema_unpublished_metadata_drift",
    "schema_upgrade_provenance_invalid",
    "schema_upgrade_provenance_missing",
    "schema_version_unsupported",
    "system_clock_before_unix_epoch",
    "system_clock_millis_overflow",
    "unbound_populated_schema_rejected",
];

fn allowlisted_migration_reason(reason: &str) -> &'static str {
    MIGRATION_REASONS
        .iter()
        .copied()
        .find(|allowed| *allowed == reason)
        .unwrap_or("unclassified")
}

fn migration_sqlstate(code: Option<&postgres::error::SqlState>) -> Option<&str> {
    code.map(postgres::error::SqlState::code).filter(|value| {
        value.len() == 5
            && value
                .bytes()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
    })
}

fn migration_failure_record(
    profile: DatabaseProfile,
    phase: MigrationPhase,
    error: &ServerError,
) -> String {
    let (variant, code, retry, reason, sqlstate) = match error {
        ServerError::Domain(error) => (
            "domain",
            Some(error.code().as_str()),
            Some(match error.retry() {
                RetryClass::Never => "never",
                RetryClass::SafeImmediate => "safe_immediate",
                RetryClass::SafeBackoff => "safe_backoff",
                RetryClass::ResyncRequired => "resync_required",
            }),
            allowlisted_migration_reason(error.reason()),
            // map_postgres_error has discarded the original SQLSTATE. The
            // allowlisted reason must not be used to reconstruct or guess it.
            None,
        ),
        ServerError::Database(error) => (
            "database",
            None,
            None,
            "unclassified",
            migration_sqlstate(error.code()),
        ),
        ServerError::Input(_) => ("input", None, None, "unclassified", None),
        ServerError::Io(_) => ("io", None, None, "unclassified", None),
        ServerError::Configuration(reason) => (
            "configuration",
            None,
            None,
            allowlisted_migration_reason(reason),
            None,
        ),
    };
    // Every interpolated value is a closed ASCII literal or a validated
    // five-character SQLSTATE; no error Display, Debug or private detail enters
    // this JSON record, and no escaping of arbitrary input is necessary.
    let quoted = |value: Option<&str>| {
        value.map_or_else(|| "null".to_owned(), |value| format!("\"{value}\""))
    };
    let render = |reason| {
        format!(
            "{{\"schema\":\"trillionnium.server-migration-failure.v1\",\"profile\":\"{}\",\"phase\":\"{}\",\"error_variant\":\"{variant}\",\"stable_code\":{},\"retry\":{},\"allowlisted_reason\":\"{reason}\",\"sqlstate\":{}}}\n",
            profile.metadata_value(),
            phase.as_str(),
            quoted(code),
            quoted(retry),
            quoted(sqlstate),
        )
    };
    let record = render(reason);
    if record.len() <= MIGRATION_DIAGNOSTIC_MAX_BYTES {
        record
    } else {
        // Future additions cannot turn a diagnostic into an unbounded log or
        // emit a truncated, invalid JSON record.
        render("unclassified")
    }
}

pub(crate) fn diagnose_migration_result<T>(
    profile: DatabaseProfile,
    phase: MigrationPhase,
    result: Result<T, ServerError>,
) -> Result<T, ServerError> {
    diagnose_migration_result_to(profile, phase, result, &mut io::stderr().lock())
}

fn diagnose_migration_result_to<T>(
    profile: DatabaseProfile,
    phase: MigrationPhase,
    result: Result<T, ServerError>,
    writer: &mut impl io::Write,
) -> Result<T, ServerError> {
    result.inspect_err(|error| {
        // Diagnostic delivery failure cannot replace the actual migration
        // error. The process retains its original Display and exit handling.
        let _ = writer.write_all(migration_failure_record(profile, phase, error).as_bytes());
    })
}

#[cfg(test)]
mod tests {
    use trnm_contracts::{RetryClass, StableCode};

    use super::*;

    #[test]
    fn process_error_display_redacts_private_domain_reason() {
        let error = ServerError::Domain(DomainError::new(
            StableCode::Internal,
            "private_database_detail",
            RetryClass::Never,
        ));
        let display = error.to_string();
        assert_eq!(display, "domain failure: internal");
        assert!(!display.contains("private_database_detail"));
    }

    #[test]
    fn migration_diagnostic_exposes_only_reviewed_reason_and_domain_guidance() {
        let error = ServerError::Domain(DomainError::new(
            StableCode::Unavailable,
            "database_pool_initialization_failed",
            RetryClass::SafeBackoff,
        ));
        assert_eq!(
            migration_failure_record(
                DatabaseProfile::CockroachDb,
                MigrationPhase::BuildPool,
                &error,
            ),
            "{\"schema\":\"trillionnium.server-migration-failure.v1\",\"profile\":\"cockroachdb\",\"phase\":\"build-pool\",\"error_variant\":\"domain\",\"stable_code\":\"unavailable\",\"retry\":\"safe_backoff\",\"allowlisted_reason\":\"database_pool_initialization_failed\",\"sqlstate\":null}\n"
        );
        assert_eq!(error.to_string(), "domain failure: unavailable");
        let configuration = migration_failure_record(
            DatabaseProfile::PostgreSql,
            MigrationPhase::ApplyAuthoritativeChain,
            &ServerError::Configuration("invalid_legacy_storage_writer_role"),
        );
        assert!(
            configuration.contains("\"allowlisted_reason\":\"invalid_legacy_storage_writer_role\"")
        );
        assert!(configuration.contains("\"stable_code\":null"));
        assert!(configuration.contains("\"retry\":null"));
        assert!(configuration.contains("\"sqlstate\":null"));
    }

    #[test]
    fn migration_diagnostic_unknown_reasons_and_other_variants_do_not_leak_secrets() {
        const PRIVATE: &str = "postgres://private_user:private_password@private_host/db?token=private_token /private/certificate.pem\n::error::private_detail";
        let database =
            "host='private_host' password='private_password' unknown_option='private_token'"
                .parse::<postgres::Config>()
                .unwrap_err();
        let errors = [
            ServerError::Domain(DomainError::new(
                StableCode::Internal,
                PRIVATE,
                RetryClass::Never,
            )),
            ServerError::Input(InputError::new(PRIVATE)),
            ServerError::Io(io::Error::new(io::ErrorKind::PermissionDenied, PRIVATE)),
            ServerError::Configuration(PRIVATE),
            ServerError::Database(database),
        ];
        for error in errors {
            let record = migration_failure_record(
                DatabaseProfile::PostgreSql,
                MigrationPhase::ApplyAuthoritativeChain,
                &error,
            );
            assert!(record.contains("\"allowlisted_reason\":\"unclassified\""));
            assert!(record.contains("\"sqlstate\":null"));
            assert_eq!(record.lines().count(), 1);
            for private in ["private", "postgres://", "certificate.pem", "::error::"] {
                assert!(!record.contains(private));
            }
        }
        for unknown in [
            "database_internal_error: private_detail",
            "database_pool_initialization_failed\nprivate_password",
            "authoritative_schema_namespace_rejected_extra",
        ] {
            assert_eq!(allowlisted_migration_reason(unknown), "unclassified");
        }
    }

    #[test]
    fn migration_diagnostic_never_reconstructs_sqlstate_from_domain_reason() {
        for sqlstate in [
            "40001", "40P01", "23505", "23503", "23502", "23514", "22P02", "42P01", "08006",
            "XX000",
        ] {
            let error = ServerError::Domain(trnm_persistence_pg::classify_sqlstate(sqlstate));
            let record = migration_failure_record(
                DatabaseProfile::CockroachDb,
                MigrationPhase::ApplyAuthoritativeChain,
                &error,
            );
            assert!(record.contains("\"sqlstate\":null"));
            assert!(!record.contains(sqlstate));
            assert!(!record.contains("\"allowlisted_reason\":\"unclassified\""));
        }
    }

    #[test]
    fn migration_sqlstate_projection_rejects_non_code_or_unsafe_driver_values() {
        use postgres::error::SqlState;

        assert_eq!(migration_sqlstate(None), None);
        for code in ["40001", "42P01", "XX000", "P0001"] {
            let state = SqlState::from_code(code);
            assert_eq!(migration_sqlstate(Some(&state)), Some(code));
        }
        for code in [
            "",
            "42P0",
            "42P010",
            "42p01",
            "12\"34",
            "1\n234",
            "12\u{0}34",
            "privé",
        ] {
            let state = SqlState::from_code(code);
            assert_eq!(migration_sqlstate(Some(&state)), None);
        }
    }

    #[test]
    fn migration_diagnostic_profile_phase_and_output_are_bounded() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for phase in [
                MigrationPhase::BuildPool,
                MigrationPhase::AcquireSession,
                MigrationPhase::ApplyAuthoritativeChain,
            ] {
                for reason in MIGRATION_REASONS
                    .iter()
                    .copied()
                    .chain(std::iter::once("unknown private detail"))
                {
                    for retry in [
                        RetryClass::Never,
                        RetryClass::SafeImmediate,
                        RetryClass::SafeBackoff,
                        RetryClass::ResyncRequired,
                    ] {
                        let record = migration_failure_record(
                            profile,
                            phase,
                            &ServerError::Domain(DomainError::new(
                                StableCode::FailedPrecondition,
                                reason,
                                retry,
                            )),
                        );
                        assert!(record.len() <= MIGRATION_DIAGNOSTIC_MAX_BYTES);
                        assert!(record.is_ascii());
                        assert_eq!(record.lines().count(), 1);
                        assert!(record
                            .contains(&format!("\"profile\":\"{}\"", profile.metadata_value())));
                        assert!(record.contains(&format!("\"phase\":\"{}\"", phase.as_str())));
                    }
                }
            }
        }
    }

    #[test]
    fn migration_diagnostic_success_preserves_value_without_output() {
        let value = Box::new([7_u8; 32]);
        let original = std::ptr::from_ref(value.as_ref());
        let mut output = Vec::new();
        let returned = diagnose_migration_result_to(
            DatabaseProfile::PostgreSql,
            MigrationPhase::ApplyAuthoritativeChain,
            Ok(value),
            &mut output,
        )
        .unwrap();
        assert_eq!(std::ptr::from_ref(returned.as_ref()), original);
        assert!(output.is_empty());
    }

    #[test]
    fn migration_diagnostic_sink_failure_preserves_original_error() {
        struct FailedSink;

        impl io::Write for FailedSink {
            fn write(&mut self, _bytes: &[u8]) -> io::Result<usize> {
                Err(io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "private_sink_detail",
                ))
            }

            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }

        let original = DomainError::new(
            StableCode::Aborted,
            "database_serialization_failure",
            RetryClass::SafeImmediate,
        );
        let returned = diagnose_migration_result_to::<()>(
            DatabaseProfile::CockroachDb,
            MigrationPhase::ApplyAuthoritativeChain,
            Err(ServerError::Domain(original)),
            &mut FailedSink,
        )
        .unwrap_err();
        assert!(matches!(returned, ServerError::Domain(error) if error == original));
    }
}
