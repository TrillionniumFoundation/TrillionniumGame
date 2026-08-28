#!/usr/bin/env python3
"""Build a fail-closed evidence summary from `go test -json` output."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import defaultdict

REQUIRED_TESTS = (
    "TestResponseLossReusesExactRequestAcrossRestart",
    "TestCommittedDuplicateDoesNotCallWorld",
    "TestCommittedDuplicateSurvivesAdvancedAuthorityCursor",
    "TestCommittedReceiptClosesAttemptEvidence",
    "TestSameCommandIDDifferentIntentFailsClosed",
    "TestConcurrentReservationsProduceOneCommitAndOneStale",
    "TestTwoWorkersConvergeOnOneReceipt",
    "TestSeparateStoreWritersFailClosedOnCASConflict",
    "TestTakeoverRejectsPreviousGeneration",
    "TestPersistenceFailureLeavesStateAndReservationUnchanged",
    "TestRetryableRejectionAndInvalidResultStayPending",
    "TestCancellationBeforeExecutePreservesReservation",
    "TestNonretryableRejectionCreatesReceiptWithoutAdvancingState",
    "TestAbortIsGenerationBoundAndNeverCreatesReceipt",
    "TestCorruptedSnapshotFailsClosed",
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--repository", default="TrillionniumFoundation/TrillionniumGame")
    args = parser.parse_args()

    source_commit = args.commit.strip().lower()
    if not SHA40.fullmatch(source_commit):
        raise SystemExit("--commit must be an exact lowercase 40-hex SHA")
    source_tree = args.tree.strip().lower()
    if not SHA40.fullmatch(source_tree):
        raise SystemExit("--tree must be an exact lowercase 40-hex SHA")
    if args.repository != "TrillionniumFoundation/TrillionniumGame":
        raise SystemExit("evidence repository must use the canonical renamed repository")

    input_path = pathlib.Path(args.input)
    states: dict[str, list[str]] = defaultdict(list)
    package_failed = False
    malformed = 0
    for line_number, raw in enumerate(input_path.read_text().splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            malformed += 1
            continue
        action = event.get("Action")
        test = event.get("Test")
        package = event.get("Package")
        if test and action in {"run", "pass", "fail", "skip"}:
            states[test].append(action)
        if package and not test and action == "fail":
            package_failed = True

    scenario_status: dict[str, str] = {}
    failures: list[str] = []
    for test in REQUIRED_TESTS:
        actions = states.get(test, [])
        if "fail" in actions:
            status = "failed"
        elif "pass" in actions:
            status = "passed"
        elif "skip" in actions:
            status = "skipped"
        else:
            status = "missing"
        scenario_status[test] = status
        if status != "passed":
            failures.append(f"{test}:{status}")
    if malformed:
        failures.append(f"malformed_json_lines:{malformed}")
    if package_failed:
        failures.append("go_test_package_failed")

    report = {
        "contract_version": "trnm_game_world_command_fault_evidence_v1",
        "repository": args.repository,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "evidence_kind": "source_level_deterministic_fault_matrix",
        "status": "passed" if not failures else "failed",
        "input_sha256": sha256(input_path),
        "required_scenario_count": len(REQUIRED_TESTS),
        "passed_scenario_count": sum(value == "passed" for value in scenario_status.values()),
        "scenarios": scenario_status,
        "failures": failures,
        "authority": {
            "cutover_authorized": False,
            "closed_online_promotion": False,
            "public_online_enabled": False,
            "public_player_market_enabled": False,
        },
        "limitations": [
            "This report is generated from deterministic source-level Go tests, not a deployed Nakama process.",
            "No real World HTTPS response-loss proxy, Nakama storage cluster, PostgreSQL transaction, process kill, or multi-host fencing is exercised.",
            "The report does not grant authority cutover, closed-online promotion, public-online release, or player-market credit.",
        ],
    }
    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": report["status"],
        "passed": report["passed_scenario_count"],
        "required": report["required_scenario_count"],
        "output": str(output_path),
    }, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
