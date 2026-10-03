use std::env;
use std::path::PathBuf;
use std::time::Duration;

use trnm_contracts::Digest32;
use trnm_persistence_pg::{
    verify_storage_export, DatabaseProfile, IntegrityDigest, PgPool, PgPoolConfig,
    StorageExportOptions, StorageImportCustody, StorageImportOptions,
};

#[derive(Debug)]
pub enum Command {
    Export,
    Import,
}

fn required(name: &str) -> Result<String, &'static str> {
    env::var(name)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or("required_storage_transfer_environment_missing")
}

fn number(name: &str) -> Result<u64, &'static str> {
    required(name)?
        .parse()
        .map_err(|_| "storage_transfer_number_invalid")
}

fn digest(name: &str) -> Result<IntegrityDigest, &'static str> {
    let text = required(name)?;
    if text.len() != 64
        || !text
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err("storage_transfer_digest_invalid");
    }
    let mut raw = [0_u8; 32];
    for (index, byte) in raw.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&text[index * 2..index * 2 + 2], 16)
            .map_err(|_| "storage_transfer_digest_invalid")?;
    }
    IntegrityDigest::new(Digest32::new(raw)).map_err(|_| "storage_transfer_digest_invalid")
}

fn pool() -> Result<PgPool, &'static str> {
    let profile = match required("TRNM_DATABASE_PROFILE")?.as_str() {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        _ => return Err("storage_transfer_profile_invalid"),
    };
    PgPool::connect_plain(
        &required("TRNM_DATABASE_URL")?,
        profile,
        PgPoolConfig {
            max_size: 1,
            min_idle: 0,
            acquire_timeout: Duration::from_secs(2),
            statement_timeout: Duration::from_secs(5),
            lock_timeout: Duration::from_secs(2),
            ..PgPoolConfig::default()
        },
    )
    .map_err(|error| error.reason())
}

fn print_report(value: &impl serde::Serialize) -> Result<(), &'static str> {
    let json = serde_json::to_string(value).map_err(|_| "storage_transfer_report_invalid")?;
    println!("{json}");
    Ok(())
}

pub fn run(export: bool) -> Result<(), &'static str> {
    let command = if export {
        Command::Export
    } else {
        Command::Import
    };
    let mut arguments = env::args().skip(1);
    let mode = arguments
        .next()
        .ok_or("storage_transfer_command_required")?;
    if mode == "--help" {
        match command {
            Command::Export => println!("trnm-storage-export snapshot PACKET_DIRECTORY --candidate-plaintext\nEnvironment: TRNM_DATABASE_URL, TRNM_DATABASE_PROFILE, TRNM_STORAGE_EXPORT_PAGE_ROWS, TRNM_STORAGE_SOURCE_EXECUTION_CLASS, TRNM_STORAGE_PRODUCER_COMMIT, TRNM_STORAGE_PRODUCER_TREE, TRNM_STORAGE_PRODUCER_SOURCE_FILE, TRNM_STORAGE_PRODUCER_SOURCE_SHA256, TRNM_STORAGE_PRODUCER_BINARY_SHA256, TRNM_STORAGE_EXECUTION_ID, TRNM_STORAGE_UPSTREAM_DIRECTORY. New private packet only; source snapshot is read-only. Candidate plaintext development transport; no compatibility or production credit."),
            Command::Import => println!("trnm-storage-import <verify-packet|preflight|begin|apply-page|apply|resume|verify-applied|finish> PACKET_DIRECTORY (database modes additionally require --candidate-plaintext)\nAll modes require independently supplied TRNM_STORAGE_EXPECTED_MANIFEST_SHA256, TRNM_STORAGE_EXPECTED_RECEIPT_SHA256, TRNM_STORAGE_PRODUCER_COMMIT, TRNM_STORAGE_PRODUCER_TREE, TRNM_STORAGE_PRODUCER_SOURCE_SHA256, TRNM_STORAGE_PRODUCER_BINARY_SHA256, TRNM_STORAGE_EXECUTION_ID. Database modes require the plaintext flag and TRNM_DATABASE_URL, TRNM_DATABASE_PROFILE, TRNM_STORAGE_IMPORT_AUDIT_AT_MS, TRNM_STORAGE_LEGACY_WRITER_ROLE, TRNM_STORAGE_EXPECTED_TARGET_SCOPE_SHA256. apply resumes a proven prefix and finalizes only after complete native reconciliation. resume/verify-applied are read-only. No implicit transaction retry, writer revocation or production custody credit."),
        }
        return Ok(());
    }
    let directory = PathBuf::from(
        arguments
            .next()
            .ok_or("storage_transfer_directory_required")?,
    );
    let offline = matches!(command, Command::Import) && mode == "verify-packet";
    if (!offline && arguments.next().as_deref() != Some("--candidate-plaintext"))
        || arguments.next().is_some()
    {
        return Err("storage_transfer_arguments_invalid");
    }
    match command {
        Command::Export => {
            if mode != "snapshot" {
                return Err("storage_export_command_invalid");
            }
            let options = StorageExportOptions {
                output_directory: directory,
                page_rows: usize::try_from(number("TRNM_STORAGE_EXPORT_PAGE_ROWS")?)
                    .map_err(|_| "storage_transfer_number_invalid")?,
                execution_class: required("TRNM_STORAGE_SOURCE_EXECUTION_CLASS")?,
                producer_commit: required("TRNM_STORAGE_PRODUCER_COMMIT")?,
                producer_tree: required("TRNM_STORAGE_PRODUCER_TREE")?,
                producer_source_file: PathBuf::from(required("TRNM_STORAGE_PRODUCER_SOURCE_FILE")?),
                producer_source_sha256: required("TRNM_STORAGE_PRODUCER_SOURCE_SHA256")?,
                producer_binary_sha256: required("TRNM_STORAGE_PRODUCER_BINARY_SHA256")?,
                execution_id: required("TRNM_STORAGE_EXECUTION_ID")?,
                upstream_directory: PathBuf::from(required("TRNM_STORAGE_UPSTREAM_DIRECTORY")?),
            };
            let result = pool()?
                .run_with_deadline(Duration::from_secs(300), |repository| {
                    repository.export_storage_snapshot(&options)
                })
                .map_err(|error| error.reason())?;
            print_report(&result)
        }
        Command::Import => {
            if !matches!(
                mode.as_str(),
                "verify-packet"
                    | "preflight"
                    | "begin"
                    | "apply-page"
                    | "apply"
                    | "resume"
                    | "verify-applied"
                    | "finish"
            ) {
                return Err("storage_import_command_invalid");
            }
            let custody = StorageImportCustody::new(
                &required("TRNM_STORAGE_EXPECTED_MANIFEST_SHA256")?,
                &required("TRNM_STORAGE_EXPECTED_RECEIPT_SHA256")?,
                required("TRNM_STORAGE_PRODUCER_COMMIT")?,
                required("TRNM_STORAGE_PRODUCER_TREE")?,
                &required("TRNM_STORAGE_PRODUCER_SOURCE_SHA256")?,
                &required("TRNM_STORAGE_PRODUCER_BINARY_SHA256")?,
                required("TRNM_STORAGE_EXECUTION_ID")?,
            )
            .map_err(|error| error.reason())?;
            // File verification and all resource/domain checks precede database
            // acquisition. Mutable transactions only use these owned bytes.
            let packet =
                verify_storage_export(&directory, &custody).map_err(|error| error.reason())?;
            if offline {
                return print_report(&packet.summary());
            }
            let options = StorageImportOptions {
                audit_at_ms: number("TRNM_STORAGE_IMPORT_AUDIT_AT_MS")?,
                legacy_writer_role: required("TRNM_STORAGE_LEGACY_WRITER_ROLE")?,
                expected_target_scope: digest("TRNM_STORAGE_EXPECTED_TARGET_SCOPE_SHA256")?,
            };
            let output = pool()?
                .run_with_deadline(Duration::from_secs(300), |repository| {
                    let checked = repository.preflight_storage_import(&packet, options)?;
                    let report = match mode.as_str() {
                        "preflight" => serde_json::to_value(packet.summary()),
                        "begin" => serde_json::to_value(repository.begin_storage_import(&checked)?),
                        "apply-page" => serde_json::to_value(
                            repository.apply_next_storage_import_page(&checked)?,
                        ),
                        "resume" => {
                            serde_json::to_value(repository.resume_storage_import(&checked)?)
                        }
                        "verify-applied" => serde_json::to_value(
                            repository.verify_applied_storage_import(&checked)?,
                        ),
                        "finish" => {
                            serde_json::to_value(repository.finalize_storage_import(&checked)?)
                        }
                        "apply" => {
                            let mut progress = repository.begin_storage_import(&checked)?;
                            while progress.next_page < progress.total_pages {
                                progress = repository
                                    .apply_next_storage_import_page(&checked)?
                                    .progress;
                            }
                            serde_json::to_value(repository.finalize_storage_import(&checked)?)
                        }
                        _ => {
                            unreachable!("closed mode was checked before any database acquisition")
                        }
                    };
                    report.map_err(|_| {
                        trnm_contracts::DomainError::new(
                            trnm_contracts::StableCode::Internal,
                            "storage_transfer_report_invalid",
                            trnm_contracts::RetryClass::Never,
                        )
                    })
                })
                .map_err(|error| error.reason())?;
            print_report(&output)
        }
    }
}
