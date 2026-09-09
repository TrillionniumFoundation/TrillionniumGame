#!/usr/bin/env python3
"""Compose PostgreSQL connection churn, exhaustion, cancellation and loss evidence tooling."""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Validate PostgreSQL connection-fault evidence source."""
        from __future__ import annotations

        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        HARNESS = ROOT / "scripts/ci-postgresql-connection-faults.sh"
        REQUIRED = (
            "connection-churn",
            "CONNECTION LIMIT 4",
            "pool-exhaustion",
            "pg_cancel_backend",
            "cancel-transaction",
            "pg_terminate_backend",
            "terminate-transaction",
            "statement_timeout",
            "fresh-connection-after-faults",
            "cancel_rollback_verified",
            "terminate_rollback_verified",
            "pool_exhaustion_rejected",
            '"accepted_evidence": False',
            '"production_ready": False',
        )

        class ValidationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise ValidationError(message)

        def validate_text(text: str) -> None:
            for marker in REQUIRED:
                require(marker in text, f"connection-fault harness missing {marker}")
            require("database/schema/v2" not in text, "non-authoritative schema referenced")
            require("DROP TABLE" not in text.upper(), "destructive schema operation introduced")
            require("|| true" not in text, "failure suppression introduced")
            require("TRNM_REQUIRE_LIVE_DATABASE" in text, "mandatory live-profile marker absent")

        def main() -> int:
            try:
                validate_text(HARNESS.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                print(f"PostgreSQL connection-fault contract failed: {error}", file=sys.stderr)
                return 1
            print("PostgreSQL connection-fault source contract: OK")
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def harness_source() -> str:
    return textwrap.dedent(
        r'''\
        #!/usr/bin/env bash
        set -Eeuo pipefail

        ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
        : "${TRNM_REQUIRE_LIVE_DATABASE:?TRNM_REQUIRE_LIVE_DATABASE must be explicit}"
        test "$TRNM_REQUIRE_LIVE_DATABASE" = 1
        : "${POSTGRES_IMAGE:?POSTGRES_IMAGE is required}"
        EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-connection-faults}
        CONTAINER=${POSTGRES_CONTAINER_NAME:-trnm-connection-fault-pg}
        PORT=${POSTGRES_PORT:-55435}
        mkdir -p "$EVIDENCE_DIR"
        background_pids=()

        cleanup() {
          for pid in "${background_pids[@]:-}"; do
            kill "$pid" >/dev/null 2>&1 || :
          done
          docker rm -f "$CONTAINER" >/dev/null 2>&1 || :
        }
        trap cleanup EXIT
        cleanup
        docker pull "$POSTGRES_IMAGE"
        docker run -d --name "$CONTAINER" \
          -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm \
          -p "${PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
        for _ in $(seq 1 90); do
          docker exec "$CONTAINER" pg_isready -U trnm -d trnm >/dev/null 2>&1 && break
          sleep 1
        done
        docker exec "$CONTAINER" pg_isready -U trnm -d trnm >/dev/null
        mapfile -t migrations < <(find "$ROOT/migrations/postgresql" -maxdepth 1 -type f -name '*_up.sql' | sort)
        test "${#migrations[@]}" -gt 0
        cat "${migrations[@]}" | docker exec -i "$CONTAINER" \
          psql -v ON_ERROR_STOP=1 -U trnm -d trnm >/dev/null

        cat <<'SQL' | docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='trnm_runtime_fault') THEN
            CREATE ROLE trnm_runtime_fault LOGIN PASSWORD 'trnm_runtime_fault' CONNECTION LIMIT 4;
          END IF;
        END
        $$;
        ALTER ROLE trnm_runtime_fault CONNECTION LIMIT 4;
        GRANT CONNECT ON DATABASE trnm TO trnm_runtime_fault;
        GRANT USAGE ON SCHEMA public TO trnm_runtime_fault;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trnm_runtime_fault;
        INSERT INTO trnm_entity_heads VALUES
          (decode(repeat('b1',16),'hex'), 0, 0, 1, decode(repeat('b2',32),'hex'), 10);
        SQL

        runtime_psql() {
          PGPASSWORD=trnm_runtime_fault psql -v ON_ERROR_STOP=1 -X -q \
            -h 127.0.0.1 -p "$PORT" -U trnm_runtime_fault -d trnm "$@"
        }
        admin_scalar() {
          docker exec "$CONTAINER" psql -X -q -tA -U trnm -d trnm -c "$1"
        }

        : > "$EVIDENCE_DIR/connection-churn.txt"
        for index in $(seq 1 80); do
          value=$(PGAPPNAME="trnm-churn-${index}" runtime_psql -tA -c 'SELECT 1')
          test "$value" = 1
          printf '%s|%s\n' "$index" "$value" >> "$EVIDENCE_DIR/connection-churn.txt"
        done
        test "$(wc -l < "$EVIDENCE_DIR/connection-churn.txt")" = 80

        for index in $(seq 1 4); do
          PGAPPNAME="trnm-hold-${index}" runtime_psql -c 'SELECT pg_sleep(30)' \
            > "$EVIDENCE_DIR/hold-${index}.stdout" \
            2> "$EVIDENCE_DIR/hold-${index}.stderr" &
          background_pids+=("$!")
        done
        for _ in $(seq 1 40); do
          held=$(admin_scalar "SELECT count(*) FROM pg_stat_activity WHERE usename='trnm_runtime_fault' AND application_name LIKE 'trnm-hold-%'")
          [[ "$held" = 4 ]] && break
          sleep 0.25
        done
        test "$held" = 4
        if PGAPPNAME=trnm-pool-exhaustion runtime_psql -c 'SELECT 1' \
             > "$EVIDENCE_DIR/pool-exhaustion.stdout" \
             2> "$EVIDENCE_DIR/pool-exhaustion.stderr"; then
          echo "pool-exhaustion connection unexpectedly succeeded" >&2
          exit 1
        fi
        grep -Ei 'too many connections|connection limit' "$EVIDENCE_DIR/pool-exhaustion.stderr" >/dev/null
        terminated_holds=$(admin_scalar "SELECT count(*) FROM (SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='trnm_runtime_fault' AND application_name LIKE 'trnm-hold-%') AS stopped")
        test "$terminated_holds" = 4
        set +e
        for pid in "${background_pids[@]}"; do wait "$pid"; done
        set -e
        background_pids=()

        original_digest=$(admin_scalar "SELECT encode(state_digest,'hex') FROM trnm_entity_heads WHERE entity_id=decode(repeat('b1',16),'hex')")
        cat > "$EVIDENCE_DIR/cancel-transaction.sql" <<'SQL'
        BEGIN;
        UPDATE trnm_entity_heads
        SET state_digest=decode(repeat('c1',32),'hex'), updated_at_ms=20
        WHERE entity_id=decode(repeat('b1',16),'hex');
        SELECT pg_sleep(30);
        COMMIT;
        SQL
        PGAPPNAME=trnm-cancel-transaction runtime_psql -f "$EVIDENCE_DIR/cancel-transaction.sql" \
          > "$EVIDENCE_DIR/cancel-transaction.stdout" \
          2> "$EVIDENCE_DIR/cancel-transaction.stderr" &
        cancel_client=$!
        background_pids=("$cancel_client")
        cancel_pid=''
        for _ in $(seq 1 60); do
          cancel_pid=$(admin_scalar "SELECT pid FROM pg_stat_activity WHERE application_name='trnm-cancel-transaction' AND state='active' LIMIT 1")
          [[ -n "$cancel_pid" ]] && break
          sleep 0.25
        done
        test -n "$cancel_pid"
        test "$(admin_scalar "SELECT pg_cancel_backend(${cancel_pid})")" = t
        set +e
        wait "$cancel_client"
        cancel_status=$?
        set -e
        background_pids=()
        test "$cancel_status" -ne 0
        grep -Ei 'canceling statement|cancelled statement' "$EVIDENCE_DIR/cancel-transaction.stderr" >/dev/null
        cancel_digest=$(admin_scalar "SELECT encode(state_digest,'hex') FROM trnm_entity_heads WHERE entity_id=decode(repeat('b1',16),'hex')")
        test "$cancel_digest" = "$original_digest"

        cat > "$EVIDENCE_DIR/terminate-transaction.sql" <<'SQL'
        BEGIN;
        UPDATE trnm_entity_heads
        SET state_digest=decode(repeat('d1',32),'hex'), updated_at_ms=30
        WHERE entity_id=decode(repeat('b1',16),'hex');
        SELECT pg_sleep(30);
        COMMIT;
        SQL
        PGAPPNAME=trnm-terminate-transaction runtime_psql -f "$EVIDENCE_DIR/terminate-transaction.sql" \
          > "$EVIDENCE_DIR/terminate-transaction.stdout" \
          2> "$EVIDENCE_DIR/terminate-transaction.stderr" &
        terminate_client=$!
        background_pids=("$terminate_client")
        terminate_pid=''
        for _ in $(seq 1 60); do
          terminate_pid=$(admin_scalar "SELECT pid FROM pg_stat_activity WHERE application_name='trnm-terminate-transaction' AND state='active' LIMIT 1")
          [[ -n "$terminate_pid" ]] && break
          sleep 0.25
        done
        test -n "$terminate_pid"
        test "$(admin_scalar "SELECT pg_terminate_backend(${terminate_pid})")" = t
        set +e
        wait "$terminate_client"
        terminate_status=$?
        set -e
        background_pids=()
        test "$terminate_status" -ne 0
        terminate_digest=$(admin_scalar "SELECT encode(state_digest,'hex') FROM trnm_entity_heads WHERE entity_id=decode(repeat('b1',16),'hex')")
        test "$terminate_digest" = "$original_digest"

        if PGAPPNAME=trnm-statement-timeout runtime_psql \
             -c "SET statement_timeout='400ms'; SELECT pg_sleep(5)" \
             > "$EVIDENCE_DIR/statement-timeout.stdout" \
             2> "$EVIDENCE_DIR/statement-timeout.stderr"; then
          echo "statement_timeout query unexpectedly succeeded" >&2
          exit 1
        fi
        grep -Ei 'statement timeout|canceling statement' "$EVIDENCE_DIR/statement-timeout.stderr" >/dev/null
        fresh_connection_after_faults=$(PGAPPNAME=trnm-fresh-connection-after-faults runtime_psql -tA -c 'SELECT 1')
        test "$fresh_connection_after_faults" = 1
        printf '%s\n' "$fresh_connection_after_faults" > "$EVIDENCE_DIR/fresh-connection-after-faults.txt"

        docker inspect --format='{{.Image}}' "$CONTAINER" > "$EVIDENCE_DIR/image-id.txt"
        python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" <<'PY'
        import hashlib
        import json
        import sys
        from pathlib import Path
        root=Path(sys.argv[1])
        evidence=Path(sys.argv[2])
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        manifest={
            "schema":"trillionnium.postgresql-connection-fault-evidence.v1",
            "profile":"postgresql",
            "image_reference":sys.argv[3],
            "image_id":(evidence/"image-id.txt").read_text().strip(),
            "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
            "connection_churn_count":80,
            "pool_exhaustion_rejected":True,
            "cancel_rollback_verified":True,
            "terminate_rollback_verified":True,
            "statement_timeout_verified":True,
            "fresh_connection_after_faults":True,
            "churn_sha256":digest(evidence/"connection-churn.txt"),
            "cancel_error_sha256":digest(evidence/"cancel-transaction.stderr"),
            "terminate_error_sha256":digest(evidence/"terminate-transaction.stderr"),
            "claim_boundary":{
                "application_pool_exhaustion_proven":False,
                "multi_node_failover_proven":False,
                "accepted_evidence":False,
                "independently_accepted":False,
                "performance_accepted":False,
                "production_ready":False,
            },
        }
        (evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        print(json.dumps(manifest,sort_keys=True))
        PY
        python3 "$ROOT/scripts/check-postgresql-connection-faults.py"
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations

        import importlib.util
        import subprocess
        import sys
        from pathlib import Path
        import unittest

        ROOT = Path(__file__).resolve().parents[2]
        CHECKER = ROOT / "scripts/check-postgresql-connection-faults.py"

        def load_checker():
            spec = importlib.util.spec_from_file_location("postgresql_connection_fault_contract", CHECKER)
            if spec is None or spec.loader is None:
                raise RuntimeError("checker loader unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        class PostgreSqlConnectionFaultContractTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.checker = load_checker()
                cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

            def test_real_contract_passes(self):
                self.checker.validate_text(self.harness)

            def test_cancel_or_exhaustion_removal_rejected(self):
                for marker in ("pg_cancel_backend", "pool-exhaustion"):
                    with self.subTest(marker=marker):
                        with self.assertRaisesRegex(self.checker.ValidationError, "missing"):
                            self.checker.validate_text(self.harness.replace(marker, "removed", 1))

            def test_command_line_checker_passes(self):
                result = subprocess.run(
                    [sys.executable, str(CHECKER)], cwd=ROOT, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("connection-fault source contract: OK", result.stdout)

        if __name__ == "__main__":
            unittest.main()
        '''
    )


def update_documents(root: Path) -> None:
    path = root / "docs/development/SCHEMA_AUTHORITY.json"
    value: dict[str, Any] = json.loads(read(path))
    authority = value.setdefault("authority", {})
    authority["postgresql_connection_fault_source_candidate"] = {
        "harness":"scripts/ci-postgresql-connection-faults.sh",
        "source_contract":"python3 scripts/check-postgresql-connection-faults.py",
        "covers":[
            "eighty fresh connection churn cycles",
            "role-level connection exhaustion rejection",
            "active transaction query cancellation rollback",
            "active transaction backend termination rollback",
            "statement timeout",
            "fresh connection after all injected faults",
        ],
        "accepted":False,
    }
    boundary=value.setdefault("claim_boundary",{})
    boundary["postgresql_connection_fault_source_candidate"]=True
    boundary["ha_proven"]=False
    boundary["production_ready"]=False
    write(path,json.dumps(value,indent=2,ensure_ascii=False))

    operations=root/"docs/OPERATIONS_AND_RELEASE.md"
    text=read(operations)
    section=textwrap.dedent(
        '''\

        ## PostgreSQL connection fault candidate

        `scripts/ci-postgresql-connection-faults.sh` opens eighty fresh runtime connections, fills an exact role connection limit and proves the next connection is rejected, cancels and terminates separate in-flight transactions and verifies both updates roll back, exercises `statement_timeout`, then requires a new connection to succeed. Evidence binds the image, migration chain and error/output digests.

        Role-level exhaustion is not automatically application-pool acceptance. Multi-node failover, capacity targets, performance acceptance, independent review and production readiness remain separate facts.
        '''
    )
    if "## PostgreSQL connection fault candidate" not in text:
        text+=section
    write(operations,text)


def run(root: Path) -> None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/check-postgresql-connection-faults.py",checker_source())
    write(root/"scripts/ci-postgresql-connection-faults.sh",harness_source())
    write(root/"tests/control_plane/test_postgresql_connection_fault_contract.py",test_source())
    update_documents(root)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    run(parser.parse_args().root.resolve())
