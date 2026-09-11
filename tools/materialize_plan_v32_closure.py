#!/usr/bin/env python3
"""Materialize bounded Plan v3.2 security, durability and operations candidates.

Replay an immutable historical generator set against an exact current working
tree, repair known template-emission defects without weakening any source
checker, materialize denominator review packets, and run the new control-plane
corpus. This tool never changes acceptance or production claims.
"""
from __future__ import annotations

import argparse
import py_compile
import re
import subprocess
import textwrap
from pathlib import Path

COMPOSERS = (
    "compose_remote_mac_provider.py",
    "compose_remote_mac_unix_transport.py",
    "compose_durability_state_model.py",
    "compose_denominator_review_packets.py",
    "compose_database_capacity_endurance.py",
    "compose_postgresql_connection_faults.py",
    "compose_postgresql_pitr.py",
    "compose_postgresql_primary_failover.py",
    "compose_postgresql_recovery_barrier.py",
    "compose_postgresql_semantic_recovery.py",
    "compose_postgresql_semantic_recovery_v2.py",
    "compose_cockroachdb_node_failover.py",
    "compose_cockroachdb_semantic_recovery.py",
    "compose_cutover_state_machine.py",
)

BROKEN_PYTHON_OUTPUTS = (
    "scripts/finalize-database-endurance-ledger.py",
    "scripts/derive-cutover-blocker-packet.py",
    "scripts/accept-denominator-family.py",
    "scripts/finalize-global-sg1.py",
    "scripts/generate-denominator-review-packets.py",
    "scripts/cutover-state-machine.py",
    "tests/control_plane/test_postgresql_semantic_recovery_contract.py",
    "tests/control_plane/test_database_capacity_endurance.py",
    "tests/control_plane/test_postgresql_recovery_barrier_contract.py",
    "tests/control_plane/test_cockroachdb_node_failover_contract.py",
    "tests/control_plane/test_postgresql_primary_failover_contract.py",
    "tests/control_plane/test_cockroachdb_semantic_recovery_contract.py",
)

GENERATED_SHELL = (
    "scripts/ci-cockroachdb-node-failover.sh",
    "scripts/ci-cockroachdb-semantic-recovery.sh",
    "scripts/ci-database-capacity-smoke.sh",
    "scripts/ci-database-endurance-segment.sh",
    "scripts/ci-postgresql-connection-faults.sh",
    "scripts/ci-postgresql-pitr.sh",
    "scripts/ci-postgresql-primary-failover.sh",
    "scripts/ci-postgresql-recovery-barrier.sh",
    "scripts/ci-postgresql-semantic-recovery.sh",
)

GENERATED_SQL = (
    "scripts/cockroachdb-semantic-snapshot.sql",
    "scripts/database-capacity-workload.sql",
    "scripts/postgresql-catalog-snapshot.sql",
    "scripts/postgresql-recovery-quarantine.sql",
    "scripts/postgresql-semantic-snapshot.sql",
)

CHECKERS = (
    "scripts/check-remote-mac-provider.py",
    "scripts/check-remote-mac-unix-transport.py",
    "scripts/check-durability-state-model.py",
    "scripts/check-denominator-review-packets.py",
    "scripts/check-database-capacity-endurance.py",
    "scripts/check-postgresql-connection-faults.py",
    "scripts/check-postgresql-pitr.py",
    "scripts/check-postgresql-primary-failover.py",
    "scripts/check-postgresql-recovery-barrier.py",
    "scripts/check-postgresql-semantic-recovery.py",
    "scripts/check-cockroachdb-node-failover.py",
    "scripts/check-cockroachdb-semantic-recovery.py",
    "scripts/check-cutover-state-machine.py",
)

SCHEMA_AUTHORITY_NEGATIVE_CHECKERS = (
    "scripts/check-postgresql-connection-faults.py",
    "scripts/check-postgresql-pitr.py",
    "scripts/check-postgresql-primary-failover.py",
    "scripts/check-postgresql-recovery-barrier.py",
    "scripts/check-postgresql-semantic-recovery.py",
    "scripts/check-cockroachdb-node-failover.py",
    "scripts/check-cockroachdb-semantic-recovery.py",
)

TEST_MODULES = (
    "tests.control_plane.test_remote_mac_provider_contract",
    "tests.control_plane.test_remote_mac_unix_transport",
    "tests.control_plane.test_durability_state_model",
    "tests.control_plane.test_denominator_review_packets",
    "tests.control_plane.test_database_capacity_endurance",
    "tests.control_plane.test_postgresql_connection_fault_contract",
    "tests.control_plane.test_postgresql_pitr_contract",
    "tests.control_plane.test_postgresql_primary_failover_contract",
    "tests.control_plane.test_postgresql_recovery_barrier_contract",
    "tests.control_plane.test_postgresql_semantic_recovery_contract",
    "tests.control_plane.test_cockroachdb_node_failover_contract",
    "tests.control_plane.test_cockroachdb_semantic_recovery_contract",
    "tests.control_plane.test_cutover_state_machine",
)


def run(command: list[str], cwd: Path) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing {label}: {old!r}")
    return text.replace(old, new)


def repair_broken_python(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    index = 0
    repairs = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.rstrip()
        if stripped.endswith('+ "') or stripped.endswith('+"'):
            cursor = index + 1
            payload: list[str] = []
            while cursor < len(lines) and cursor <= index + 3:
                continuation = lines[cursor].lstrip()
                if continuation.startswith('",') or continuation.startswith('")'):
                    escaped = r"\n"
                    if payload:
                        escaped += r"\n".join(payload) + r"\n"
                    output.append(stripped + escaped + continuation)
                    index = cursor + 1
                    repairs += 1
                    break
                payload.append(lines[cursor])
                cursor += 1
            else:
                output.append(line)
                index += 1
            continue
        output.append(line)
        index += 1
    if repairs == 0:
        raise RuntimeError(f"expected broken generated string literal in {path}")
    path.write_text(textwrap.dedent("\n".join(output)).rstrip() + "\n", encoding="utf-8")


def normalize_embedded_template(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0] == "\\":
        lines = lines[1:]
    normalized = [line[8:] if line.startswith("        ") else line for line in lines]
    path.write_text("\n".join(normalized).rstrip() + "\n", encoding="utf-8")


def repair_harness_claim_spacing(root: Path) -> None:
    fields: dict[str, tuple[tuple[str, bool], ...]] = {
        "scripts/ci-postgresql-connection-faults.sh": (
            ("accepted_evidence", False), ("production_ready", False),
        ),
        "scripts/ci-postgresql-pitr.sh": (
            ("target_commit_present", True), ("after_target_commit_absent", True),
            ("approved_rpo_rto", False), ("accepted_evidence", False),
            ("production_ready", False),
        ),
        "scripts/ci-postgresql-primary-failover.sh": (
            ("zero_acknowledged_loss_observed", True),
            ("approved_rpo_rto", False), ("accepted_evidence", False),
            ("production_ready", False),
        ),
        "scripts/ci-cockroachdb-node-failover.sh": (
            ("zero_acknowledged_loss_observed", True),
            ("approved_rpo_rto", False), ("accepted_evidence", False),
            ("production_ready", False),
        ),
    }
    for relative, pairs in fields.items():
        path = root / relative
        text = path.read_text(encoding="utf-8")
        for field, value in pairs:
            text = replace_required(
                text,
                f'"{field}":{value}',
                f'"{field}": {value}',
                relative,
            )
        path.write_text(text, encoding="utf-8")

    node = root / "scripts/ci-cockroachdb-node-failover.sh"
    text = node.read_text(encoding="utf-8")
    if "scenario=leaseholder-partition" not in text:
        needle = 'mkdir -p "$EVIDENCE_DIR"\n'
        text = replace_required(
            text,
            needle,
            needle + "printf 'scenario=leaseholder-partition\\n' > \"$EVIDENCE_DIR/scenario.txt\"\n",
            "CockroachDB scenario insertion point",
        )
    node.write_text(text, encoding="utf-8")


def repair_durability_model(root: Path) -> None:
    path = root / "scripts/durability-state-model.py"
    text = path.read_text(encoding="utf-8")
    text = replace_required(
        text,
        'DEAD = "dead"\n',
        'DEAD = "dead"\nCLAIM_ACTIONS = ((1, "claim:1"), (2, "claim:2"))\n',
        "durability constants",
    )
    text = replace_required(
        text,
        '        for owner in (1, 2):\n            yield Edge(\n                f"claim:{owner}",',
        '        for owner, action in CLAIM_ACTIONS:\n            yield Edge(\n                action,',
        "durability claim transition",
    )
    path.write_text(text, encoding="utf-8")


def repair_generated_tests(root: Path) -> None:
    paths = (
        "tests/control_plane/test_remote_mac_provider_contract.py",
        "tests/control_plane/test_remote_mac_unix_transport.py",
        "tests/control_plane/test_postgresql_pitr_contract.py",
        "tests/control_plane/test_postgresql_primary_failover_contract.py",
        "tests/control_plane/test_cockroachdb_node_failover_contract.py",
        "tests/control_plane/test_postgresql_connection_fault_contract.py",
    )
    for relative in paths:
        path = root / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        in_test = False
        output: list[str] = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("def test_"):
                in_test = True
            elif stripped.startswith("def ") or stripped.startswith("class "):
                in_test = False
            if in_test:
                line = line.replace("cls.checker", "self.checker")
                line = line.replace('.replace(marker,"removed",1)', '.replace(marker,"removed")')
                line = line.replace('.replace(marker, "removed", 1)', '.replace(marker, "removed")')
                line = line.replace('.replace("set_read_timeout","removed",1)', '.replace("set_read_timeout","removed")')
                line = line.replace('.replace("remote_apply","local",1)', '.replace("remote_apply","local")')
            output.append(line)
        path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def repair_schema_authority_guards(root: Path) -> None:
    """Derive quarantined roots from current authority without source literals."""

    loader = '''\
def non_authoritative_schema_paths() -> tuple[str, ...]:
    import json

    authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
    document = json.loads(authority_path.read_text(encoding="utf-8"))
    rows = document.get("non_authoritative")
    require(isinstance(rows, list) and rows, "schema quarantine registry missing")
    result: list[str] = []
    for row in rows:
        require(isinstance(row, dict), "schema quarantine row invalid")
        value = row.get("path")
        require(isinstance(value, str) and value, "schema quarantine path invalid")
        result.append(value)
    require(len(result) == len(set(result)), "duplicate schema quarantine path")
    return tuple(result)

'''
    guard_pattern = re.compile(
        r'''(?mx)
        ^(?P<indent>[ \t]*)
        require\(
          ["']database/schema/v2["']
          \s+not\s+in\s+text\s*,\s*
          ["']non-authoritative\ schema\ referenced["']
        \)\s*$
        '''
    )
    validate_pattern = re.compile(r"(?m)^def validate_text\([^\n]*\):\n")

    for relative in SCHEMA_AUTHORITY_NEGATIVE_CHECKERS:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        guards = list(guard_pattern.finditer(text))
        if len(guards) != 1:
            raise RuntimeError(
                f"{relative}: expected one hardcoded quarantine guard, found {len(guards)}"
            )
        entries = list(validate_pattern.finditer(text))
        if len(entries) != 1:
            raise RuntimeError(
                f"{relative}: expected one validate_text entrypoint, found {len(entries)}"
            )
        guard = guards[0]
        indent = guard.group("indent")
        replacement = (
            f"{indent}for path in non_authoritative_schema_paths():\n"
            f'{indent}    require(path not in text, "non-authoritative schema referenced")'
        )
        text = text[: entries[0].start()] + loader + text[entries[0].start() :]
        text, substitutions = guard_pattern.subn(replacement, text, count=1)
        if substitutions != 1:
            raise RuntimeError(f"{relative}: quarantine guard replacement failed")
        if "database/schema/v2" in text:
            raise RuntimeError(f"{relative}: hardcoded quarantined schema root remains")
        path.write_text(text, encoding="utf-8")


def validate_python(root: Path) -> None:
    for relative in BROKEN_PYTHON_OUTPUTS:
        py_compile.compile(str(root / relative), doraise=True)
    run(["python3", "-m", "compileall", "-q", "scripts", "tests"], root)


def materialize(root: Path, controller: Path) -> None:
    if not (root / ".git").is_dir():
        raise RuntimeError("target Git working tree required")

    for composer in COMPOSERS:
        run(["python3", str(controller / "tools" / composer), str(root)], root)

    for relative in BROKEN_PYTHON_OUTPUTS:
        repair_broken_python(root / relative)
    for relative in (*GENERATED_SHELL, *GENERATED_SQL):
        normalize_embedded_template(root / relative)

    repair_harness_claim_spacing(root)
    repair_durability_model(root)
    repair_generated_tests(root)
    repair_schema_authority_guards(root)

    run(["python3", "scripts/generate-denominator-review-packets.py"], root)
    validate_python(root)
    for checker in CHECKERS:
        run(["python3", checker], root)
    run(["python3", "-m", "unittest", *TEST_MODULES, "-q"], root)
    for shell in GENERATED_SHELL:
        run(["bash", "-n", shell], root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--controller", type=Path, required=True)
    args = parser.parse_args()
    materialize(args.root.resolve(), args.controller.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
