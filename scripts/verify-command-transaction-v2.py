#!/usr/bin/env python3
"""Cross-check the command transaction state machine against both SQL profiles."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "database/schema/v2/database-profile-contract.v2.json"
TRANSACTION_PATH = ROOT / "database/schema/v2/command-transaction-contract.v2.json"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load(path: Path) -> dict[str, object]:
    require(path.is_file(), f"missing contract: {path.relative_to(ROOT)}")
    raw = path.read_text(encoding="utf-8")
    require(raw.endswith("\n"), f"contract lacks final newline: {path.relative_to(ROOT)}")
    value = json.loads(raw)
    require(isinstance(value, dict), f"contract is not an object: {path.relative_to(ROOT)}")
    return value


def normalized_sql(path: Path) -> str:
    require(path.is_file(), f"missing SQL file: {path.relative_to(ROOT)}")
    value = path.read_text(encoding="utf-8")
    value = re.sub(r"--[^\n]*", "", value)
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    return re.sub(r"\s+", " ", value).strip().lower()


def verify() -> dict[str, object]:
    profile = load(PROFILE_PATH)
    transaction = load(TRANSACTION_PATH)

    require(
        transaction.get("schema") == "trillionnium.game.command-transaction.v2",
        "wrong transaction schema",
    )
    require(transaction.get("version") == 2, "wrong transaction version")
    require(transaction.get("isolation") == "serializable", "transaction isolation drift")

    identity = transaction.get("identity")
    require(isinstance(identity, dict), "identity contract missing")
    require(
        identity.get("scope") == ["tenant_id", "entity_id", "command_id"],
        "idempotency identity scope drift",
    )
    require(identity.get("fingerprint_bytes") == 32, "fingerprint size drift")
    retry_reuses = identity.get("retry_reuses")
    require(isinstance(retry_reuses, list), "retry identity list missing")
    for required in (
        "command_id",
        "fingerprint",
        "event_ids",
        "outbox_intent_ids",
        "canonical_payload_bytes",
    ):
        require(required in retry_reuses, f"retry may replace canonical identity: {required}")

    phases = transaction.get("ordered_phases")
    require(isinstance(phases, list), "ordered phases missing")
    phase_names = [phase.get("phase") for phase in phases if isinstance(phase, dict)]
    require(
        phase_names
        == [
            "canonicalize",
            "begin",
            "idempotency_lookup",
            "authority_fence",
            "persist_graph",
            "commit",
            "acknowledge",
        ],
        f"transaction phase order drift: {phase_names!r}",
    )
    commit_index = phase_names.index("commit")
    acknowledge_index = phase_names.index("acknowledge")
    require(commit_index < acknowledge_index, "acknowledgement precedes commit")
    acknowledge = phases[acknowledge_index]
    require(isinstance(acknowledge, dict), "acknowledgement phase malformed")
    requirements = acknowledge.get("requirements")
    require(isinstance(requirements, list), "acknowledgement requirements missing")
    require("ack_only_after_observed_commit_success" in requirements, "commit observation gate missing")
    require("unknown_commit_result_retries_as_exact_duplicate" in requirements, "unknown commit recovery missing")

    profile_map = profile.get("profiles")
    require(isinstance(profile_map, dict), "database profiles missing")
    retry_policy = transaction.get("retry_policy")
    require(isinstance(retry_policy, dict), "retry policy missing")
    for name in ("postgresql", "cockroachdb"):
        database_profile = profile_map.get(name)
        transaction_profile = retry_policy.get(name)
        require(isinstance(database_profile, dict), f"missing database profile: {name}")
        require(isinstance(transaction_profile, dict), f"missing transaction retry profile: {name}")
        require(
            database_profile.get("retry_sqlstates") == transaction_profile.get("retry_sqlstates"),
            f"retry SQLSTATE drift for {name}",
        )
        classification = transaction_profile.get("classification_required")
        require(isinstance(classification, list), f"classification list missing for {name}")
        for state in ("23505", "08006", "08007"):
            require(state in classification, f"{name}: unknown result classification missing {state}")

        migration = ROOT / str(database_profile.get("migration"))
        sql = normalized_sql(migration)
        require("trnm_command_receipts" in sql, f"{name}: receipt table missing")
        require("fingerprint" in sql and "receipt_bytes" in sql, f"{name}: exact receipt replay columns missing")
        require("authority_generation" in sql and "revision" in sql, f"{name}: authority fence columns missing")
        require("trnm_events" in sql and "trnm_outbox" in sql, f"{name}: atomic graph tables missing")
        require(re.search(r"\bcreate\s+trigger\b", sql) is None, f"{name}: trigger side effect forbidden")
        require(re.search(r"\b(gen_random_uuid|uuid_generate_v[0-9]+|unique_rowid)\s*\(", sql) is None, f"{name}: generated business identity forbidden")

        lease = normalized_sql(ROOT / str(database_profile.get("lease_query")))
        for fence in ("state = 'leased'", "lease_owner", "lease_generation"):
            require(fence in lease, f"{name}: outbox completion fence missing {fence}")

    forbidden = transaction.get("forbidden")
    require(isinstance(forbidden, list), "forbidden transition list missing")
    for value in (
        "ack_before_commit",
        "best_effort_dual_write",
        "new_command_id_on_retry",
        "partial_event_or_outbox_commit",
        "database_clock_as_authority_fence",
    ):
        require(value in forbidden, f"forbidden transition omitted: {value}")

    return {
        "schema": "trillionnium.game.command-transaction-verification.v2",
        "profiles": ["cockroachdb", "postgresql"],
        "phases": phase_names,
        "checks": {
            "ack_after_commit": True,
            "exact_identity_reused_on_retry": True,
            "retry_classification_aligned": True,
            "authority_fence_materialized": True,
            "outbox_completion_fenced": True,
            "trigger_side_effect_absent": True,
        },
        "claims": {
            "static_contract_passed": True,
            "runtime_fault_matrix_complete": False,
            "production_ready": False,
        },
    }


def main() -> int:
    try:
        report = verify()
    except (OSError, ValueError, VerificationError) as error:
        print(f"command-transaction-v2 verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
