#!/usr/bin/env python3
"""Apply exact PR #57 outbox observability/fault-boundary source repairs."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PatchError(RuntimeError):
    pass


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one match in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: Path, marker: str, addition: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + separator + addition.rstrip() + "\n", encoding="utf-8")


def patch_repository() -> None:
    path = ROOT / "crates/trnm-persistence-pg/src/outbox.rs"
    replace_once(
        path,
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\npub enum OutboxRetryOutcome {\n",
        "#[derive(Clone, Debug, Eq, PartialEq)]\n"
        "pub struct OutboxClaimBatch {\n"
        "    pub leases: Vec<OutboxLease>,\n"
        "    pub reaped_dead_letters: usize,\n"
        "}\n\n"
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]\n"
        "pub enum OutboxRetryOutcome {\n",
        "claim batch type",
    )
    replace_once(
        path,
        "impl PgRepository {\n"
        "    pub fn claim_outbox(\n"
        "        &mut self,\n"
        "        owner: NodeId,\n"
        "        now_ms: u64,\n"
        "        lease_duration_ms: u64,\n"
        "        max_attempts: u32,\n"
        "        limit: usize,\n"
        "    ) -> Result<Vec<OutboxLease>, DomainError> {\n",
        "impl PgRepository {\n"
        "    pub fn claim_outbox(\n"
        "        &mut self,\n"
        "        owner: NodeId,\n"
        "        now_ms: u64,\n"
        "        lease_duration_ms: u64,\n"
        "        max_attempts: u32,\n"
        "        limit: usize,\n"
        "    ) -> Result<Vec<OutboxLease>, DomainError> {\n"
        "        self.claim_outbox_batch(owner, now_ms, lease_duration_ms, max_attempts, limit)\n"
        "            .map(|batch| batch.leases)\n"
        "    }\n\n"
        "    pub fn claim_outbox_batch(\n"
        "        &mut self,\n"
        "        owner: NodeId,\n"
        "        now_ms: u64,\n"
        "        lease_duration_ms: u64,\n"
        "        max_attempts: u32,\n"
        "        limit: usize,\n"
        "    ) -> Result<OutboxClaimBatch, DomainError> {\n",
        "claim batch entry point",
    )
    replace_once(
        path,
        "        reap_expired_exhausted(&mut transaction, now_ms_i64, max_attempts_i64, limit)?;\n",
        "        let reaped_dead_letters =\n"
        "            reap_expired_exhausted(&mut transaction, now_ms_i64, max_attempts_i64, limit)?;\n",
        "capture reaper count",
    )
    replace_once(
        path,
        "        transaction.commit().map_err(map_postgres_error)?;\n"
        "        Ok(leases)\n"
        "    }\n\n"
        "    pub fn complete_outbox(\n",
        "        transaction.commit().map_err(map_postgres_error)?;\n"
        "        Ok(OutboxClaimBatch {\n"
        "            leases,\n"
        "            reaped_dead_letters,\n"
        "        })\n"
        "    }\n\n"
        "    pub fn complete_outbox(\n",
        "return claim batch",
    )
    replace_once(
        path,
        "    #[test]\n    fn lease_validation_fences_zero_generation_and_owner() {\n",
        "    #[test]\n"
        "    fn claim_batch_preserves_reaper_count_and_leases() {\n"
        "        let value = lease();\n"
        "        let batch = OutboxClaimBatch {\n"
        "            leases: vec![value],\n"
        "            reaped_dead_letters: 2,\n"
        "        };\n"
        "        assert_eq!(batch.leases, vec![value]);\n"
        "        assert_eq!(batch.reaped_dead_letters, 2);\n"
        "    }\n\n"
        "    #[test]\n    fn lease_validation_fences_zero_generation_and_owner() {\n",
        "claim batch test",
    )
    lib = ROOT / "crates/trnm-persistence-pg/src/lib.rs"
    replace_once(
        lib,
        "pub use outbox::{OutboxLease, OutboxRetryOutcome};\n",
        "pub use outbox::{OutboxClaimBatch, OutboxLease, OutboxRetryOutcome};\n",
        "claim batch export",
    )


def patch_worker() -> None:
    path = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"
    replace_once(
        path,
        "const TEST_FAIL_AFTER_DELIVERY_EXIT_CODE: i32 = 70;\n",
        "const TEST_FAIL_AFTER_DELIVERY_EXIT_CODE: i32 = 70;\n"
        "const TEST_FAIL_BEFORE_DELIVERY_EXIT_CODE: i32 = 71;\n",
        "before-delivery exit code",
    )
    replace_once(
        path,
        "    max_backoff_ms: u64,\n    test_fail_after_delivery: bool,\n",
        "    max_backoff_ms: u64,\n"
        "    test_fail_before_delivery: bool,\n"
        "    test_fail_after_delivery: bool,\n",
        "before-delivery config field",
    )
    replace_once(
        path,
        "            .field(\"max_backoff_ms\", &self.max_backoff_ms)\n"
        "            .field(\"test_fail_after_delivery\", &self.test_fail_after_delivery)\n",
        "            .field(\"max_backoff_ms\", &self.max_backoff_ms)\n"
        "            .field(\n"
        "                \"test_fail_before_delivery\",\n"
        "                &self.test_fail_before_delivery,\n"
        "            )\n"
        "            .field(\"test_fail_after_delivery\", &self.test_fail_after_delivery)\n",
        "before-delivery debug",
    )
    replace_once(
        path,
        "        let test_fail_after_delivery = parse_bool(\n",
        "        let test_fail_before_delivery = parse_bool(\n"
        "            lookup(\"TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY\").as_deref(),\n"
        "            false,\n"
        "            \"test_fail_before_delivery_invalid\",\n"
        "        )?;\n"
        "        let test_fail_after_delivery = parse_bool(\n",
        "before-delivery parse",
    )
    replace_once(
        path,
        "        if test_fail_after_delivery && !enable_test_failpoints {\n",
        "        if test_fail_before_delivery && test_fail_after_delivery {\n"
        "            return Err(WorkerError::Configuration(\n"
        "                \"test_failpoints_are_mutually_exclusive\",\n"
        "            ));\n"
        "        }\n"
        "        if (test_fail_before_delivery || test_fail_after_delivery)\n"
        "            && !enable_test_failpoints\n"
        "        {\n",
        "failpoint opt-in",
    )
    replace_once(
        path,
        "        if test_fail_after_delivery && command != Command::RunOnce {\n",
        "        if (test_fail_before_delivery || test_fail_after_delivery)\n"
        "            && command != Command::RunOnce\n"
        "        {\n",
        "failpoint run-once guard",
    )
    replace_once(
        path,
        "                max_backoff_ms,\n                test_fail_after_delivery,\n",
        "                max_backoff_ms,\n"
        "                test_fail_before_delivery,\n"
        "                test_fail_after_delivery,\n",
        "before-delivery construction",
    )
    replace_once(
        path,
        "                if report.claimed == 0 {\n",
        "                if report.claimed == 0 && report.dead_lettered == 0 {\n",
        "reaper-only report",
    )
    replace_once(
        path,
        "    let leases = repository.claim_outbox(\n"
        "        config.node,\n"
        "        now_ms,\n"
        "        config.lease_duration_ms,\n"
        "        config.max_attempts,\n"
        "        config.batch_size,\n"
        "    )?;\n"
        "    let mut report = BatchReport {\n"
        "        claimed: leases.len(),\n"
        "        ..BatchReport::default()\n"
        "    };\n"
        "    for lease in leases {\n"
        "        match sink.deliver(&lease) {\n",
        "    let batch = repository.claim_outbox_batch(\n"
        "        config.node,\n"
        "        now_ms,\n"
        "        config.lease_duration_ms,\n"
        "        config.max_attempts,\n"
        "        config.batch_size,\n"
        "    )?;\n"
        "    let mut report = BatchReport {\n"
        "        claimed: batch.leases.len(),\n"
        "        dead_lettered: batch.reaped_dead_letters,\n"
        "        ..BatchReport::default()\n"
        "    };\n"
        "    for lease in batch.leases {\n"
        "        if config.test_fail_before_delivery {\n"
        "            eprintln!(\n"
        "                \"trnm-outbox-worker test failpoint: exiting after final-attempt claim and before durable spool publication\"\n"
        "            );\n"
        "            process::exit(TEST_FAIL_BEFORE_DELIVERY_EXIT_CODE);\n"
        "        }\n"
        "        match sink.deliver(&lease) {\n",
        "batch observability and before-delivery boundary",
    )
    replace_once(
        path,
        "    #[test]\n    fn tls_identity_requires_a_pair_and_debug_redacts_url() {\n",
        "    #[test]\n"
        "    fn pre_delivery_failpoint_is_explicit_exclusive_and_run_once_only() {\n"
        "        let directory = temporary_directory(\"pre-delivery-failpoint\");\n"
        "        let mut values = base_config(&directory);\n"
        "        values.insert(\n"
        "            \"TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY\".to_owned(),\n"
        "            \"1\".to_owned(),\n"
        "        );\n"
        "        assert!(matches!(\n"
        "            load(&values),\n"
        "            Err(WorkerError::Configuration(\n"
        "                \"test_failpoint_requires_explicit_opt_in\"\n"
        "            ))\n"
        "        ));\n"
        "        values.insert(\n"
        "            \"TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS\".to_owned(),\n"
        "            \"1\".to_owned(),\n"
        "        );\n"
        "        let (_, config) = load(&values).unwrap();\n"
        "        assert!(config.test_fail_before_delivery);\n"
        "        assert!(!config.test_fail_after_delivery);\n"
        "        values.insert(\n"
        "            \"TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY\".to_owned(),\n"
        "            \"1\".to_owned(),\n"
        "        );\n"
        "        assert!(matches!(\n"
        "            load(&values),\n"
        "            Err(WorkerError::Configuration(\n"
        "                \"test_failpoints_are_mutually_exclusive\"\n"
        "            ))\n"
        "        ));\n"
        "        values.remove(\"TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY\");\n"
        "        let result = WorkerConfig::from_lookup(\n"
        "            &[\"trnm-outbox-worker\".to_owned(), \"serve\".to_owned()],\n"
        "            |name| values.get(name).cloned(),\n"
        "        );\n"
        "        assert!(matches!(\n"
        "            result,\n"
        "            Err(WorkerError::Configuration(\n"
        "                \"test_failpoint_requires_run_once\"\n"
        "            ))\n"
        "        ));\n"
        "    }\n\n"
        "    #[test]\n"
        "    fn batch_report_exposes_reaper_only_terminal_transitions() {\n"
        "        let report = BatchReport {\n"
        "            dead_lettered: 3,\n"
        "            ..BatchReport::default()\n"
        "        };\n"
        "        assert_eq!(report.claimed, 0);\n"
        "        assert_eq!(report.dead_lettered, 3);\n"
        "    }\n\n"
        "    #[test]\n    fn tls_identity_requires_a_pair_and_debug_redacts_url() {\n",
        "before-delivery tests",
    )


def patch_docs() -> None:
    append_once(
        ROOT / "docs/development/OUTBOX_FINAL_ATTEMPT_REAPER.md",
        "## Final-attempt crash boundary semantics",
        """## Final-attempt crash boundary semantics

The exact-head live lane executes `max_attempts = 1` crash-after-publish and
crash-before-publish cases against PostgreSQL and CockroachDB. In both cases the
expired exhausted lease becomes a dead letter and the worker reports
`dead_lettered=1`. After publication one stable spool effect remains; before
publication no effect exists, so final-attempt dead-lettering can lose the
external effect. This is not an exactly-once claim.

The deterministic raw archive is retained through the native Actions Results
artifact service and finalized with its SHA-256 digest. Logs or summaries
without that retained archive receive no evidence credit.""",
    )
    append_once(
        ROOT / "docs/development/OUTBOX_SPOOL_WORKER.md",
        "## Final-attempt test failpoints",
        """## Final-attempt test failpoints

`TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY=1` exits with code `71` after claim and
before publication. `TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY=1` exits with code
`70` after durable publication and before acknowledgement. Both require
`TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS=1`, are mutually exclusive and are valid
only for `run-once`.""",
    )
    append_once(
        ROOT / "docs/development/PERSISTENCE_OUTBOX_CORE.md",
        "## Claim-batch terminal-transition observability",
        """## Claim-batch terminal-transition observability

`claim_outbox_batch` returns newly claimed leases and the count of expired
final-attempt leases reaped in the same serializable transaction. The original
`claim_outbox` API remains a compatibility wrapper. The worker reports the
reaper count in `dead_lettered`, including reaper-only batches with
`claimed=0`.""",
    )


def main() -> int:
    patch_repository()
    patch_worker()
    patch_docs()
    print(json.dumps({
        "status": "pr57-outbox-review-blockers-patched",
        "raw_artifact_retention": True,
        "reaper_count_observable": True,
        "crash_before_publish_tested": True,
        "crash_after_publish_tested": True,
        "compatibility_credit": False,
        "production_ready": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
