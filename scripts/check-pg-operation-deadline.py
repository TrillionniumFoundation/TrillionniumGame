#!/usr/bin/env python3
"""Fail-closed source contract for PostgreSQL total deadlines and cancellation."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TRIGGER_SPEC = importlib.util.spec_from_file_location(
    "trnm_pg_deadline_trigger_contract", Path(__file__).with_name("workflow_trigger_contract.py")
)
if _TRIGGER_SPEC is None or _TRIGGER_SPEC.loader is None:
    raise RuntimeError("cannot load the deadline workflow trigger contract")
TRIGGER = importlib.util.module_from_spec(_TRIGGER_SPEC)
sys.modules[_TRIGGER_SPEC.name] = TRIGGER
_TRIGGER_SPEC.loader.exec_module(TRIGGER)
POOL_ROOT = Path("crates/trnm-persistence-pg/src/pool.rs")
POOL_PARTS = tuple(
    Path("crates/trnm-persistence-pg/src/pool_parts") / name
    for name in ("base.rs", "cancellation.rs", "pool.rs", "tests.rs")
)
APP = Path("crates/trnm-persistence-pg/src/bin/trnm_server/app.rs")
SERVER_POOL = Path("crates/trnm-persistence-pg/src/bin/trnm_server/pool.rs")
RETRY = Path("crates/trnm-persistence-pg/src/bin/trnm_server/retry.rs")
SERVER = Path("crates/trnm-persistence-pg/src/bin/trnm_server/server.rs")
WORKFLOW = Path(".github/workflows/pg-operation-deadline.yml")


@dataclass(frozen=True)
class SourceSet:
    pool_root: str
    pool: str
    app: str
    server_pool: str
    retry: str
    server: str
    workflow: str


def read(root: Path, path: Path) -> str:
    return (root / path).read_text(encoding="utf-8")


def load(root: Path) -> SourceSet:
    pool_root = read(root, POOL_ROOT)
    pool = pool_root + "\n" + "\n".join(read(root, path) for path in POOL_PARTS)
    return SourceSet(
        pool_root=pool_root,
        pool=pool,
        app=read(root, APP),
        server_pool=read(root, SERVER_POOL),
        retry=read(root, RETRY),
        server=read(root, SERVER),
        workflow=read(root, WORKFLOW),
    )


def require(text: str, fragment: str, failures: list[str], label: str) -> None:
    if fragment not in text:
        failures.append(f"{label}: required fragment missing: {fragment!r}")


def forbid(text: str, fragment: str, failures: list[str], label: str) -> None:
    if fragment in text:
        failures.append(f"{label}: forbidden fragment present: {fragment!r}")


def validate(source: SourceSet) -> list[str]:
    failures: list[str] = []
    expected_root = "\n".join(
        f'include!("pool_parts/{path.name}");' for path in POOL_PARTS
    ) + "\n"
    if source.pool_root != expected_root:
        failures.append(f"{POOL_ROOT}: include root must bind exactly four reviewed parts")
    for fragment in (
        "pub fn run_with_deadline<T>", "CancelState", "DeadlineGuard",
        "cancel_all_for_shutdown", "database_operation_deadline_exceeded",
        "database_operation_shutdown_cancelled", "database_cancellation_id_exhausted",
        "fetch_update(Ordering::AcqRel", "cancellation_id_exhaustion_is_atomic_and_fail_closed",
        "repository.client.cancel_token()", "token.cancel_query(NoTls)",
        "token.cancel_query(connector.clone())", "statement_timeout", "lock_timeout",
        "idle_in_transaction_session_timeout", "let cancellation_reason = deadline.finish();",
        "let elapsed = started.elapsed();",
        "live_deadline_cancels_blocking_query_and_keeps_pool_usable",
        "live_shutdown_cancels_inflight_query_and_preserves_connection_pool",
        'const BLOCKING_QUERY: &str = "SELECT pg_sleep(10)";',
        'batch_execute("SELECT 1")', "RetirementManager", "repository.client.retire();",
        "retired_snapshot_never_dispatches_a_late_cancel",
        "completion_waits_for_sender_without_holding_registry_lock",
        "retired_lease_is_dropped_not_recycled_by_r2d2",
        "panicking_sender_records_failure_and_can_be_retired",
        "assert_ne!(backend_pid(&pool), initial_backend);", "FROM pg_stat_activity",
    ):
        require(source.pool, fragment, failures, "pool source set")
    finish_position = source.pool.find("let cancellation_reason = deadline.finish();")
    elapsed_position = source.pool.find("let elapsed = started.elapsed();", finish_position)
    if not (0 <= finish_position < elapsed_position):
        failures.append("pool source set: watchdog cleanup must remain inside total elapsed budget")
    for fragment in (
        "pub trait BudgetedRepository", "DATABASE_OPERATION_BUDGET", "commit_command_with_budget",
        "let remaining = policy.total_budget.saturating_sub(started.elapsed());",
        "operation(remaining)", "successful_result_after_budget_is_rejected",
        "each_attempt_receives_only_the_remaining_total_budget",
    ):
        require(source.retry, fragment, failures, str(RETRY))
    for fragment in (
        "run_with_deadline", "operation_budget.min(self.operation_budget)",
        "impl BudgetedRepository for PooledRepository", "impl InflightCancellation for PooledRepository",
        "self.pool.cancel_inflight()", "database_inflight_operations: snapshot.inflight_operations",
        "database_deadline_cancellations: snapshot.deadline_cancellations",
        "database_shutdown_cancellations: snapshot.shutdown_cancellations",
        "database_cancellation_deliveries: snapshot.cancellation_deliveries",
        "database_cancellation_failures: snapshot.cancellation_failures",
    ):
        require(source.server_pool, fragment, failures, str(SERVER_POOL))
    for fragment in (
        "pub database_inflight_operations: u64", "pub database_deadline_cancellations: u64",
        "pub database_shutdown_cancellations: u64", "pub database_cancellation_deliveries: u64",
        "pub database_cancellation_failures: u64", "trnm_server_database_inflight_operations",
        "trnm_server_database_deadline_cancellations_total",
        "trnm_server_database_shutdown_cancellations_total",
        "trnm_server_database_cancellation_deliveries_total",
        "trnm_server_database_cancellation_failures_total",
        "cancellation_metrics_are_exported_without_query_or_credential_labels",
    ):
        require(source.app, fragment, failures, str(APP))
    for fragment in (
        "BudgetedRepository + InflightCancellation",
        "let cancelled_operations = repository.cancel_inflight();", "drop(sender);", "join_workers(workers)",
    ):
        require(source.server, fragment, failures, str(SERVER))
    cancel_position = source.server.find("let cancelled_operations = repository.cancel_inflight();")
    drop_position = source.server.find("drop(sender);")
    join_position = source.server.find("join_workers(workers)")
    if not (0 <= cancel_position < drop_position < join_position):
        failures.append(f"{SERVER}: shutdown cancellation must precede queue close and worker join")
    for fragment in (
        "TRNM_REQUIRE_LIVE_PG_DEADLINE: '1'", "TRNM_TEST_DATABASE_URL:",
        "pool::tests::live_deadline_cancels_blocking_query_and_keeps_pool_usable",
        "pool::tests::live_shutdown_cancels_inflight_query_and_preserves_connection_pool",
        "cargo fmt --all --check", "cargo clippy -p trnm-persistence-pg --all-targets -- -D warnings",
        "permissions:\n  contents: read", "-p 'test_pg_cancellation_lifecycle.py' -v",
    ):
        require(source.workflow, fragment, failures, str(WORKFLOW))
    for fragment in ("continue-on-error: true", "|| true", "if: always()", "pull_request_target:", "actions/checkout"):
        forbid(source.workflow, fragment, failures, str(WORKFLOW))
    if source.pool.count("cancel_query(") != 2:
        failures.append("pool source set: exactly two transport-matched cancel calls required")
    if source.pool.count(".batch_execute(BLOCKING_QUERY)") != 2:
        failures.append("pool source set: both live scenarios must block in SQL")
    if source.pool.count("assert_ne!(backend_pid(&pool), initial_backend);") != 2:
        failures.append("pool source set: both cancellations must replace the backend")
    if source.workflow.count("cargo test -p trnm-persistence-pg") < 3:
        failures.append(f"{WORKFLOW}: source/unit plus two live invocations required")
    for log in ("deadline-test.log", "shutdown-test.log"):
        require(source.workflow,
                "grep -Eq '^test result: ok[.] 1 passed; 0 failed; 0 ignored;' " + log,
                failures, "nonempty live test receipt")
    try:
        TRIGGER.validate_required_pr_and_main_paths(source.workflow, (
            "crates/trnm-persistence-pg/src/pool_parts/**",
            "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs",
            "tests/control_plane/test_pg_cancellation_lifecycle.py",
        ))
    except TRIGGER.TriggerContractError as error:
        failures.append(f"{WORKFLOW}: {error}")
    return failures


def assert_rejected(name: str, source: SourceSet) -> None:
    if not validate(source):
        raise AssertionError(f"hostile fixture unexpectedly accepted: {name}")


def self_test(source: SourceSet) -> None:
    baseline = validate(source)
    if baseline:
        raise AssertionError("baseline contract failed: " + "; ".join(baseline))
    mutations = (
        ("plaintext-only-cancel", "pool", "token.cancel_query(connector.clone())", "token.cancel_query(NoTls)", 1),
        ("fixed-retry-budget", "retry", "operation(remaining)", "operation(policy.total_budget)", 1),
        ("shutdown-after-join", "server", "let cancelled_operations = repository.cancel_inflight();", "let cancelled_operations = 0;", 1),
        ("metrics-not-exported", "app", "trnm_server_database_cancellation_failures_total", "trnm_server_database_hidden_cancellation_failures_total", -1),
        ("wrapping-cancellation-id", "pool", "fetch_update(Ordering::AcqRel", "fetch_add(Ordering::AcqRel", 1),
        ("elapsed-before-cleanup", "pool", "let cancellation_reason = deadline.finish();\n        let elapsed = started.elapsed();", "let elapsed = started.elapsed();\n        let cancellation_reason = deadline.finish();", 1),
        ("live-lane-optional", "workflow", "TRNM_REQUIRE_LIVE_PG_DEADLINE: '1'", "TRNM_REQUIRE_LIVE_PG_DEADLINE: '0'", 1),
        ("continued-live-failure", "workflow", "cargo fmt --all --check", "cargo fmt --all --check || true", 1),
        ("unbound-part", "pool_root", 'include!("pool_parts/tests.rs");\n', "", 1),
        ("backend-reuse-not-checked", "pool", "assert_ne!(backend_pid(&pool), initial_backend);", "", 1),
        ("zero-tests-accepted", "workflow", "grep -Eq '^test result: ok[.] 1 passed; 0 failed; 0 ignored;' deadline-test.log", "test -f deadline-test.log", 1),
        ("pool-parts-trigger-missing", "workflow", "      - 'crates/trnm-persistence-pg/src/pool_parts/**'\n", "", 1),
        ("filtered-pull-request", "workflow", "  pull_request:\n", "  pull_request:\n    paths: ['docs/**']\n", 1),
        ("wrong-push-branch", "workflow", "    branches: [main]", "    branches: [other]", 1),
    )
    for name, field, before, after, count in mutations:
        original = getattr(source, field)
        if before not in original:
            raise AssertionError(f"hostile fixture did not mutate source: {name}")
        assert_rejected(name, replace(source, **{field: original.replace(before, after, count)}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    source = load(args.root)
    failures = validate(source)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1
    if args.self_test:
        self_test(source)
        print("PostgreSQL deadline/cancellation hostile fixtures: PASS")
    print("PostgreSQL deadline/cancellation source contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
