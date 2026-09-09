#!/usr/bin/env python3
"""Compose PostgreSQL recovery write-fence and outbox quarantine evidence tooling."""
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


def quarantine_sql() -> str:
    return textwrap.dedent(
        r'''\
        \set ON_ERROR_STOP on
        BEGIN;
        LOCK TABLE trnm_outbox IN ACCESS EXCLUSIVE MODE;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM trnm_outbox WHERE state = 1) THEN
            RAISE EXCEPTION 'active outbox lease blocks recovery quarantine';
          END IF;
        END
        $$;
        COPY (
          SELECT encode(intent_id, 'hex') AS intent_id,
                 encode(entity_id, 'hex') AS entity_id,
                 encode(command_id, 'hex') AS command_id,
                 kind,
                 encode(payload_digest, 'hex') AS payload_digest,
                 attempt,
                 lease_generation,
                 state,
                 available_at_ms,
                 updated_at_ms
          FROM trnm_outbox
          WHERE state = 0
          ORDER BY intent_id
        ) TO STDOUT WITH (FORMAT csv, HEADER true);
        UPDATE trnm_outbox
        SET state = 3,
            owner_node = NULL,
            receipt_digest = NULL,
            dead_reason_digest = decode(repeat('f0', 32), 'hex'),
            updated_at_ms = GREATEST(updated_at_ms, available_at_ms)
        WHERE state = 0;
        ALTER ROLE trnm_runtime IN DATABASE trnm_source
          SET default_transaction_read_only = on;
        COMMIT;
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Validate the PostgreSQL recovery barrier source contract."""
        from __future__ import annotations

        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        SQL = ROOT / "scripts/postgresql-recovery-quarantine.sql"
        HARNESS = ROOT / "scripts/ci-postgresql-recovery-barrier.sh"
        REQUIRED_SQL = (
            "BEGIN;",
            "LOCK TABLE trnm_outbox IN ACCESS EXCLUSIVE MODE",
            "active outbox lease blocks recovery quarantine",
            "COPY (",
            "WHERE state = 0",
            "UPDATE trnm_outbox",
            "SET state = 3",
            "dead_reason_digest",
            "ALTER ROLE trnm_runtime IN DATABASE trnm_source",
            "default_transaction_read_only = on",
            "COMMIT;",
        )
        REQUIRED_HARNESS = (
            "active-lease-rejection",
            "postgresql-recovery-quarantine.sql",
            "quarantine-export.csv",
            "read-only runtime write unexpectedly succeeded",
            "runtime-read-after-fence",
            "pending_or_leased_after_quarantine",
            "quarantine_export_sha256",
            '"write_fence_effective_for_new_runtime_connections": True',
            '"accepted_evidence": False',
            '"routing_fence_proven": False',
            '"production_ready": False',
        )

        class ValidationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise ValidationError(message)

        def validate_texts(sql: str, harness: str) -> None:
            for marker in REQUIRED_SQL:
                require(marker in sql, f"quarantine SQL missing {marker}")
            for marker in REQUIRED_HARNESS:
                require(marker in harness, f"barrier harness missing {marker}")
            combined = sql + harness
            require("database/schema/v2" not in combined, "non-authoritative schema referenced")
            require("DROP TABLE" not in combined.upper(), "destructive table rollback introduced")
            require("DELETE FROM trnm_outbox" not in combined, "pending outbox deletion introduced")
            require("|| true" not in harness, "failure suppression introduced")

        def main() -> int:
            try:
                validate_texts(
                    SQL.read_text(encoding="utf-8"),
                    HARNESS.read_text(encoding="utf-8"),
                )
            except (OSError, ValidationError) as error:
                print(f"PostgreSQL recovery barrier contract failed: {error}", file=sys.stderr)
                return 1
            print("PostgreSQL recovery barrier source contract: OK")
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
        : "${POSTGRES_IMAGE:?POSTGRES_IMAGE is required}"
        EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-recovery-barrier}
        CONTAINER=${POSTGRES_CONTAINER_NAME:-trnm-recovery-barrier-pg}
        PORT=${POSTGRES_PORT:-55434}
        mkdir -p "$EVIDENCE_DIR"

        cleanup() {
          docker rm -f "$CONTAINER" >/dev/null 2>&1 || :
        }
        trap cleanup EXIT
        cleanup
        docker pull "$POSTGRES_IMAGE"
        docker run -d --name "$CONTAINER" \
          -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm_source \
          -p "${PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
        for _ in $(seq 1 90); do
          docker exec "$CONTAINER" pg_isready -U trnm -d trnm_source >/dev/null 2>&1 && break
          sleep 1
        done
        docker exec "$CONTAINER" pg_isready -U trnm -d trnm_source >/dev/null
        mapfile -t migrations < <(find "$ROOT/migrations/postgresql" -maxdepth 1 -type f -name '*_up.sql' | sort)
        test "${#migrations[@]}" -gt 0
        cat "${migrations[@]}" | docker exec -i "$CONTAINER" \
          psql -v ON_ERROR_STOP=1 -U trnm -d trnm_source >/dev/null

        cat <<'SQL' | docker exec -i "$CONTAINER" \
          psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm_source
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trnm_runtime') THEN
            CREATE ROLE trnm_runtime LOGIN PASSWORD 'trnm_runtime';
          END IF;
        END
        $$;
        GRANT CONNECT ON DATABASE trnm_source TO trnm_runtime;
        GRANT USAGE ON SCHEMA public TO trnm_runtime;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trnm_runtime;
        INSERT INTO trnm_entity_heads VALUES
          (decode(repeat('11',16),'hex'), 1, 1, 1, decode(repeat('12',32),'hex'), 10);
        INSERT INTO trnm_command_receipts VALUES
          (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'),
           decode(repeat('23',32),'hex'), 1, decode(repeat('24',32),'hex'), 1, 1, 1, 10);
        INSERT INTO trnm_outbox VALUES
          (decode(repeat('41',16),'hex'), decode(repeat('11',16),'hex'),
           decode(repeat('22',16),'hex'), 3, decode(repeat('42',32),'hex'),
           0, 0, 0, NULL, NULL, NULL, 10, 10);
        INSERT INTO trnm_outbox VALUES
          (decode(repeat('43',16),'hex'), decode(repeat('11',16),'hex'),
           decode(repeat('22',16),'hex'), 3, decode(repeat('44',32),'hex'),
           1, 1, 1, decode(repeat('45',16),'hex'), NULL, NULL, 10, 10);
        INSERT INTO trnm_command_outbox VALUES
          (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'), 0,
           decode(repeat('41',16),'hex'));
        INSERT INTO trnm_command_outbox VALUES
          (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'), 1,
           decode(repeat('43',16),'hex'));
        SQL

        if docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm \
             -d trnm_source < "$ROOT/scripts/postgresql-recovery-quarantine.sql" \
             > "$EVIDENCE_DIR/active-lease-rejection.stdout" \
             2> "$EVIDENCE_DIR/active-lease-rejection.stderr"; then
          echo "active-lease-rejection unexpectedly succeeded" >&2
          exit 1
        fi
        grep -F 'active outbox lease blocks recovery quarantine' \
          "$EVIDENCE_DIR/active-lease-rejection.stderr" >/dev/null
        pending_before=$(docker exec "$CONTAINER" psql -X -q -tA -U trnm -d trnm_source \
          -c 'SELECT count(*) FROM trnm_outbox WHERE state IN (0,1)')
        test "$pending_before" = 2

        docker exec "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm_source \
          -c "UPDATE trnm_outbox SET state=0, owner_node=NULL, lease_generation=2, updated_at_ms=11 WHERE state=1" \
          >/dev/null
        docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm \
          -d trnm_source < "$ROOT/scripts/postgresql-recovery-quarantine.sql" \
          > "$EVIDENCE_DIR/quarantine-export.csv"
        test -s "$EVIDENCE_DIR/quarantine-export.csv"
        grep -F '41414141414141414141414141414141' "$EVIDENCE_DIR/quarantine-export.csv" >/dev/null
        grep -F '43434343434343434343434343434343' "$EVIDENCE_DIR/quarantine-export.csv" >/dev/null

        pending_or_leased_after_quarantine=$(docker exec "$CONTAINER" psql -X -q -tA \
          -U trnm -d trnm_source -c 'SELECT count(*) FROM trnm_outbox WHERE state IN (0,1)')
        test "$pending_or_leased_after_quarantine" = 0
        quarantined=$(docker exec "$CONTAINER" psql -X -q -tA -U trnm -d trnm_source \
          -c "SELECT count(*) FROM trnm_outbox WHERE state=3 AND dead_reason_digest=decode(repeat('f0',32),'hex')")
        test "$quarantined" = 2

        runtime_read_after_fence=$(PGPASSWORD=trnm_runtime psql -X -q -tA \
          -h 127.0.0.1 -p "$PORT" -U trnm_runtime -d trnm_source \
          -c 'SELECT count(*) FROM trnm_outbox')
        test "$runtime_read_after_fence" = 2
        printf '%s\n' "$runtime_read_after_fence" > "$EVIDENCE_DIR/runtime-read-after-fence.txt"
        if PGPASSWORD=trnm_runtime psql -v ON_ERROR_STOP=1 -X -q \
             -h 127.0.0.1 -p "$PORT" -U trnm_runtime -d trnm_source \
             -c "INSERT INTO trnm_storage_objects VALUES ('fence','blocked',decode(repeat('77',16),'hex'),decode('01','hex'),decode(repeat('78',32),'hex'),2,1,20)" \
             > "$EVIDENCE_DIR/runtime-write-after-fence.stdout" \
             2> "$EVIDENCE_DIR/runtime-write-after-fence.stderr"; then
          echo "read-only runtime write unexpectedly succeeded" >&2
          exit 1
        fi
        grep -Ei 'read-only|read only' "$EVIDENCE_DIR/runtime-write-after-fence.stderr" >/dev/null

        docker inspect --format='{{.Image}}' "$CONTAINER" > "$EVIDENCE_DIR/image-id.txt"
        python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" "$pending_before" "$quarantined" <<'PY'
        import hashlib
        import json
        import sys
        from pathlib import Path
        root=Path(sys.argv[1])
        evidence=Path(sys.argv[2])
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        manifest={
            "schema":"trillionnium.postgresql-recovery-barrier-evidence.v1",
            "profile":"postgresql",
            "image_reference":sys.argv[3],
            "image_id":(evidence/"image-id.txt").read_text().strip(),
            "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
            "active_lease_rejected": True,
            "pending_before":int(sys.argv[4]),
            "quarantined":int(sys.argv[5]),
            "pending_or_leased_after_quarantine":0,
            "quarantine_export_sha256":digest(evidence/"quarantine-export.csv"),
            "runtime_write_error_sha256":digest(evidence/"runtime-write-after-fence.stderr"),
            "write_fence_effective_for_new_runtime_connections": True,
            "claim_boundary": {
                "routing_fence_proven": False,
                "all_existing_connections_fenced": False,
                "accepted_evidence": False,
                "independently_accepted": False,
                "rollback_authorized": False,
                "production_ready": False,
            },
        }
        (evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        print(json.dumps(manifest,sort_keys=True))
        PY
        python3 "$ROOT/scripts/check-postgresql-recovery-barrier.py"
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
        CHECKER = ROOT / "scripts/check-postgresql-recovery-barrier.py"

        def load_checker():
            spec = importlib.util.spec_from_file_location("postgresql_recovery_barrier_contract", CHECKER)
            if spec is None or spec.loader is None:
                raise RuntimeError("checker loader unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        class PostgreSqlRecoveryBarrierContractTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.checker = load_checker()
                cls.sql = cls.checker.SQL.read_text(encoding="utf-8")
                cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

            def test_real_contract_passes(self):
                self.checker.validate_texts(self.sql, self.harness)

            def test_missing_lease_rejection_rejected(self):
                with self.assertRaisesRegex(self.checker.ValidationError, "active outbox lease"):
                    self.checker.validate_texts(
                        self.sql.replace("active outbox lease blocks recovery quarantine", "removed", 1),
                        self.harness,
                    )

            def test_deletion_and_failure_suppression_rejected(self):
                with self.assertRaisesRegex(self.checker.ValidationError, "pending outbox deletion"):
                    self.checker.validate_texts(self.sql, self.harness + "\nDELETE FROM trnm_outbox;\n")
                with self.assertRaisesRegex(self.checker.ValidationError, "failure suppression"):
                    self.checker.validate_texts(self.sql, self.harness + "\nfalse || true\n")

            def test_command_line_checker_passes(self):
                result = subprocess.run(
                    [sys.executable, str(CHECKER)], cwd=ROOT, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("recovery barrier source contract: OK", result.stdout)

        if __name__ == "__main__":
            unittest.main()
        '''
    )


def update_documents(root: Path) -> None:
    path = root / "docs/development/SCHEMA_AUTHORITY.json"
    value: dict[str, Any] = json.loads(read(path))
    authority = value.setdefault("authority", {})
    authority["postgresql_recovery_barrier_source_candidate"] = {
        "quarantine_transaction": "scripts/postgresql-recovery-quarantine.sql",
        "harness": "scripts/ci-postgresql-recovery-barrier.sh",
        "source_contract": "python3 scripts/check-postgresql-recovery-barrier.py",
        "semantics": [
            "active leases reject quarantine",
            "ready intents exported while an exclusive table lock is held",
            "ready intents atomically become terminal quarantine records",
            "new runtime-role sessions default to read-only",
            "runtime reads remain available for verification",
        ],
        "accepted": False,
    }
    boundary = value.setdefault("claim_boundary", {})
    boundary["postgresql_recovery_barrier_source_candidate"] = True
    boundary["rollback_proven"] = False
    boundary["pitr_proven"] = False
    boundary["ha_proven"] = False
    boundary["production_ready"] = False
    write(path, json.dumps(value, indent=2, ensure_ascii=False))

    operations = root / "docs/OPERATIONS_AND_RELEASE.md"
    text = read(operations)
    section = textwrap.dedent(
        '''\

        ## PostgreSQL recovery write fence and outbox quarantine

        `scripts/postgresql-recovery-quarantine.sql` runs under an exclusive outbox table lock. It refuses to proceed while any lease is active, streams every ready intent to the retained quarantine artifact, atomically converts those rows to a terminal recovery-quarantine state, and changes new `trnm_runtime` sessions to read-only. The live harness proves that runtime reads remain possible while a new mutation fails.

        This is a database-layer source/evidence candidate. Production rollback still requires an upstream routing fence, draining or terminating every existing runtime connection, a conflict-free operator decision, accepted backup/restore evidence and explicit authorization before any write capability is restored.
        '''
    )
    if "## PostgreSQL recovery write fence and outbox quarantine" not in text:
        text += section
    write(operations, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    write(root / "scripts/postgresql-recovery-quarantine.sql", quarantine_sql())
    write(root / "scripts/check-postgresql-recovery-barrier.py", checker_source())
    write(root / "scripts/ci-postgresql-recovery-barrier.sh", harness_source())
    write(
        root / "tests/control_plane/test_postgresql_recovery_barrier_contract.py",
        test_source(),
    )
    update_documents(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
