#!/usr/bin/env python3
"""Fail-closed source contract for PostgreSQL total deadlines and cancellation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
        "pub fn run_with_deadline<T>",
        "CancelState",
        "DeadlineGuard",
        "cancel_all_for_shutdown",
        "database_operation_deadline_exceeded",
        "database_operation_shutdown_cancelled",
        "database_cancellation_id_exhausted",
        "fetch_update(Ordering::AcqRel",
        "cancellation_id_exhaustion_is_atomic_and_fail_closed",
        "repository.client.cancel_token()",
        "token.cancel_query(NoTls)",
        "token.cancel_query(connector.clone())",
        "statement_timeout",
        "lock_timeout",
        "idle_in_transaction_session_timeout",
        "let cancellation_reason = deadline.finish();",
        "let elapsed = started.elapsed();",
        "live_deadline_cancels_blocking_query_and_keeps_pool_usable",
        "live_shutdown_cancels_inflight_query_and_preserves_connection_pool",
        'batch_execute("SELECT pg_sleep(10)")',
        'batch_execute("SELECT 1")',
    ):
        require(source.pool, fragment, failures, "pool source set")

    finish_position = source.pool.find("let cancellation_reason = deadline.finish();")
    elapsed_position = source.pool.find("let elapsed = started.elapsed();", finish_position)
    if not (0 <= finish_position < elapsed_position):
        failures.append("pool source set: watchdog cleanup must remain inside total elapsed budget")

    for fragment in (
        "pub trait BudgetedRepository",
        "DATABASE_OPERATION_BUDGET",
        "commit_command_with_budget",
        "let remaining = policy.total_budget.saturating_sub(started.elapsed());",
        "operation(remaining)",
        "successful_result_after_budget_is_rejected",
        "each_attempt_receives_only_the_remaining_total_budget",
    ):
        require(source.retry, fragment, failures, str(RETRY))

    for fragment in (
        "run_with_deadline",
        "operation_budget.min(self.operation_budget)",
        "impl BudgetedRepository for PooledRepository",
        "impl InflightCancellation for PooledRepository",
        "self.pool.cancel_inflight()",
        "database_inflight_operations: snapshot.inflight_operations",
        "database_deadline_cancellations: snapshot.deadline_cancellations",
        "database_shutdown_cancellations: snapshot.shutdown_cancellations",
        "database_cancellation_deliveries: snapshot.cancellation_deliveries",
        "database_cancellation_failures: snapshot.cancellation_failures",
    ):
        require(source.server_pool, fragment, failures, str(SERVER_POOL))

    for fragment in (
        "pub database_inflight_operations: u64",
        "pub database_deadline_cancellations: u64",
        "pub database_shutdown_cancellations: u64",
        "pub database_cancellation_deliveries: u64",
        "pub database_cancellation_failures: u64",
        "trnm_server_database_inflight_operations",
        "trnm_server_database_deadline_cancellations_total",
        "trnm_server_database_shutdown_cancellations_total",
        "trnm_server_database_cancellation_deliveries_total",
        "trnm_server_database_cancellation_failures_total",
        "cancellation_metrics_are_exported_without_query_or_credential_labels",
    ):
        require(source.app, fragment, failures, str(APP))

    for fragment in (
        "BudgetedRepository + InflightCancellation",
        "let cancelled_operations = repository.cancel_inflight();",
        "drop(sender);",
        "join_workers(workers)",
    ):
        require(source.server, fragment, failures, str(SERVER))

    cancel_position = source.server.find(
        "let cancelled_operations = repository.cancel_inflight();"
    )
    drop_position = source.server.find("drop(sender);")
    join_position = source.server.find("join_workers(workers)")
    if not (0 <= cancel_position < drop_position < join_position):
        failures.append(
            f"{SERVER}: shutdown cancellation must precede queue close and worker join"
        )

    for fragment in (
        "TRNM_REQUIRE_LIVE_PG_DEADLINE: '1'",
        "TRNM_TEST_DATABASE_URL:",
        "pool::tests::live_deadline_cancels_blocking_query_and_keeps_pool_usable",
        "pool::tests::live_shutdown_cancels_inflight_query_and_preserves_connection_pool",
        "cargo fmt --all --check",
        "cargo clippy -p trnm-persistence-pg --all-targets -- -D warnings",
        "permissions:\n  contents: read",
    ):
        require(source.workflow, fragment, failures, str(WORKFLOW))

    for fragment in (
        "continue-on-error: true",
        "|| true",
        "if: always()",
        "pull_request_target:",
        "actions/checkout",
    ):
        forbid(source.workflow, fragment, failures, str(WORKFLOW))

    if source.pool.count("cancel_query(") != 2:
        failures.append("pool source set: exactly two transport-matched cancel calls required")
    if source.pool.count("pg_sleep(10)") != 2:
        failures.append("pool source set: both live scenarios must block in SQL")
    if source.workflow.count("cargo test -p trnm-persistence-pg") < 3:
        failures.append(f"{WORKFLOW}: source/unit plus two live invocations required")
    return failures


def assert_rejected(name: str, source: SourceSet) -> None:
    if not validate(source):
        raise AssertionError(f"hostile fixture unexpectedly accepted: {name}")


def self_test(source: SourceSet) -> None:
    baseline = validate(source)
    if baseline:
        raise AssertionError("baseline contract failed: " + "; ".join(baseline))
    assert_rejected(
        "plaintext-only-cancel",
        replace(source, pool=source.pool.replace(
            "token.cancel_query(connector.clone())", "token.cancel_query(NoTls)", 1
        )),
    )
    assert_rejected(
        "fixed-retry-budget",
        replace(source, retry=source.retry.replace(
            "operation(remaining)", "operation(policy.total_budget)", 1
        )),
    )
    assert_rejected(
        "shutdown-after-join",
        replace(source, server=source.server.replace(
            "let cancelled_operations = repository.cancel_inflight();",
            "let cancelled_operations = 0;",
            1,
        )),
    )
    assert_rejected(
        "metrics-not-exported",
        replace(source, app=source.app.replace(
            "trnm_server_database_cancellation_failures_total",
            "trnm_server_database_hidden_cancellation_failures_total",
        )),
    )
    assert_rejected(
        "wrapping-cancellation-id",
        replace(source, pool=source.pool.replace(
            "fetch_update(Ordering::AcqRel", "fetch_add(Ordering::AcqRel", 1
        )),
    )
    assert_rejected(
        "elapsed-before-cleanup",
        replace(source, pool=source.pool.replace(
            "let cancellation_reason = deadline.finish();\n        let elapsed = started.elapsed();",
            "let elapsed = started.elapsed();\n        let cancellation_reason = deadline.finish();",
            1,
        )),
    )
    assert_rejected(
        "live-lane-optional",
        replace(source, workflow=source.workflow.replace(
            "TRNM_REQUIRE_LIVE_PG_DEADLINE: '1'",
            "TRNM_REQUIRE_LIVE_PG_DEADLINE: '0'",
            1,
        )),
    )
    assert_rejected(
        "continued-live-failure",
        replace(source, workflow=source.workflow.replace(
            "cargo fmt --all --check", "cargo fmt --all --check || true", 1
        )),
    )
    assert_rejected(
        "unbound-part",
        replace(source, pool_root=source.pool_root.replace(
            'include!("pool_parts/tests.rs");\n', ""
        )),
    )


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
