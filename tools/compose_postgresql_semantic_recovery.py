#!/usr/bin/env python3
"""Compose PostgreSQL semantic backup/restore and catalog recovery evidence tooling."""
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


def data_snapshot_sql() -> str:
    return textwrap.dedent(
        r'''\
        \set ON_ERROR_STOP on
        \pset tuples_only on
        \pset format unaligned

        WITH rows AS (
          SELECT 'trnm_schema_metadata' AS table_name,
                 jsonb_build_object(
                   'singleton', singleton,
                   'schema_version', schema_version,
                   'profile', profile,
                   'source_commit', source_commit,
                   'applied_at_ms', applied_at_ms
                 ) AS value
          FROM trnm_schema_metadata
          UNION ALL
          SELECT 'trnm_entity_heads', jsonb_build_object(
                   'entity_id', encode(entity_id, 'hex'),
                   'revision', revision,
                   'last_event_sequence', last_event_sequence,
                   'authority_generation', authority_generation,
                   'state_digest', encode(state_digest, 'hex'),
                   'updated_at_ms', updated_at_ms
                 )
          FROM trnm_entity_heads
          UNION ALL
          SELECT 'trnm_command_receipts', jsonb_build_object(
                   'entity_id', encode(entity_id, 'hex'),
                   'command_id', encode(command_id, 'hex'),
                   'fingerprint', encode(fingerprint, 'hex'),
                   'revision', revision,
                   'state_digest', encode(state_digest, 'hex'),
                   'first_event_sequence', first_event_sequence,
                   'last_event_sequence', last_event_sequence,
                   'event_count', event_count,
                   'committed_at_ms', committed_at_ms
                 )
          FROM trnm_command_receipts
          UNION ALL
          SELECT 'trnm_events', jsonb_build_object(
                   'entity_id', encode(entity_id, 'hex'),
                   'sequence', sequence,
                   'event_id', encode(event_id, 'hex'),
                   'command_id', encode(command_id, 'hex'),
                   'payload_digest', encode(payload_digest, 'hex'),
                   'created_at_ms', created_at_ms
                 )
          FROM trnm_events
          UNION ALL
          SELECT 'trnm_outbox', jsonb_build_object(
                   'intent_id', encode(intent_id, 'hex'),
                   'entity_id', encode(entity_id, 'hex'),
                   'command_id', encode(command_id, 'hex'),
                   'kind', kind,
                   'payload_digest', encode(payload_digest, 'hex'),
                   'attempt', attempt,
                   'lease_generation', lease_generation,
                   'state', state,
                   'owner_node', CASE WHEN owner_node IS NULL THEN NULL ELSE encode(owner_node, 'hex') END,
                   'receipt_digest', CASE WHEN receipt_digest IS NULL THEN NULL ELSE encode(receipt_digest, 'hex') END,
                   'dead_reason_digest', CASE WHEN dead_reason_digest IS NULL THEN NULL ELSE encode(dead_reason_digest, 'hex') END,
                   'available_at_ms', available_at_ms,
                   'updated_at_ms', updated_at_ms
                 )
          FROM trnm_outbox
          UNION ALL
          SELECT 'trnm_command_outbox', jsonb_build_object(
                   'entity_id', encode(entity_id, 'hex'),
                   'command_id', encode(command_id, 'hex'),
                   'position', position,
                   'intent_id', encode(intent_id, 'hex')
                 )
          FROM trnm_command_outbox
          UNION ALL
          SELECT 'trnm_authority_leases', jsonb_build_object(
                   'entity_id', encode(entity_id, 'hex'),
                   'owner_node', encode(owner_node, 'hex'),
                   'lease_generation', lease_generation,
                   'authority_generation', authority_generation,
                   'expires_at_ms', expires_at_ms,
                   'updated_at_ms', updated_at_ms
                 )
          FROM trnm_authority_leases
          UNION ALL
          SELECT 'trnm_session_families', jsonb_build_object(
                   'family_id', encode(family_id, 'hex'),
                   'user_id', encode(user_id, 'hex'),
                   'generation', generation,
                   'active_token_id', CASE WHEN active_token_id IS NULL THEN NULL ELSE encode(active_token_id, 'hex') END,
                   'revoked_reason', revoked_reason,
                   'created_at_ms', created_at_ms,
                   'updated_at_ms', updated_at_ms
                 )
          FROM trnm_session_families
          UNION ALL
          SELECT 'trnm_refresh_tokens', jsonb_build_object(
                   'family_id', encode(family_id, 'hex'),
                   'token_id', encode(token_id, 'hex'),
                   'token_digest', encode(token_digest, 'hex'),
                   'generation', generation,
                   'state', state,
                   'issued_at_ms', issued_at_ms,
                   'consumed_at_ms', consumed_at_ms
                 )
          FROM trnm_refresh_tokens
          UNION ALL
          SELECT 'trnm_storage_objects', jsonb_build_object(
                   'collection', collection,
                   'object_key', object_key,
                   'user_id', encode(user_id, 'hex'),
                   'value_bytes', encode(value_bytes, 'hex'),
                   'version_digest', encode(version_digest, 'hex'),
                   'read_permission', read_permission,
                   'write_permission', write_permission,
                   'updated_at_ms', updated_at_ms
                 )
          FROM trnm_storage_objects
        )
        SELECT table_name || '|' || value::text
        FROM rows
        ORDER BY table_name, value::text;
        '''
    )


def catalog_snapshot_sql() -> str:
    return textwrap.dedent(
        r'''\
        \set ON_ERROR_STOP on
        \pset tuples_only on
        \pset format unaligned

        SELECT 'column|' || table_name || '|' || lpad(ordinal_position::text, 4, '0') || '|' ||
               column_name || '|' || data_type || '|' || is_nullable || '|' ||
               coalesce(column_default, '')
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name LIKE 'trnm\_%' ESCAPE '\'
        UNION ALL
        SELECT 'constraint|' || c.conrelid::regclass::text || '|' || c.conname || '|' ||
               c.contype || '|' || pg_get_constraintdef(c.oid, true)
        FROM pg_constraint AS c
        WHERE c.connamespace = 'public'::regnamespace
          AND c.conrelid::regclass::text LIKE 'trnm\_%' ESCAPE '\'
        UNION ALL
        SELECT 'index|' || tablename || '|' || indexname || '|' || indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename LIKE 'trnm\_%' ESCAPE '\'
        ORDER BY 1;
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Fail-closed source contract for PostgreSQL semantic recovery evidence."""
        from __future__ import annotations

        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        DATA = ROOT / "scripts/postgresql-semantic-snapshot.sql"
        CATALOG = ROOT / "scripts/postgresql-catalog-snapshot.sql"
        HARNESS = ROOT / "scripts/ci-postgresql-semantic-recovery.sh"
        REQUIRED_TABLES = {
            "trnm_schema_metadata",
            "trnm_entity_heads",
            "trnm_command_receipts",
            "trnm_events",
            "trnm_outbox",
            "trnm_command_outbox",
            "trnm_authority_leases",
            "trnm_session_families",
            "trnm_refresh_tokens",
            "trnm_storage_objects",
        }

        class ValidationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise ValidationError(message)

        def validate_texts(data: str, catalog: str, harness: str) -> None:
            for table in sorted(REQUIRED_TABLES):
                require(table in data, f"semantic snapshot missing {table}")
            for marker in (
                "information_schema.columns",
                "pg_constraint",
                "pg_get_constraintdef",
                "pg_indexes",
            ):
                require(marker in catalog, f"catalog snapshot missing {marker}")
            for marker in (
                "migrations/postgresql",
                "pg_dump",
                "pg_restore",
                "--exit-on-error",
                "repeat-migration",
                "negative-constraint",
                "postgresql-semantic-snapshot.sql",
                "postgresql-catalog-snapshot.sql",
                "cmp --silent",
                "sha256",
                '"semantic_data_equal": True',
                '"semantic_catalog_equal": True',
                '"accepted_evidence": False',
                '"production_ready": False',
            ):
                require(marker in harness, f"recovery harness missing {marker}")
            combined = data + catalog + harness
            require("database/schema/v2" not in combined, "non-authoritative schema referenced")
            require("DROP TABLE" not in combined.upper(), "destructive table rollback introduced")
            require("|| true" not in harness, "failure suppression introduced")

        def main() -> int:
            try:
                validate_texts(
                    DATA.read_text(encoding="utf-8"),
                    CATALOG.read_text(encoding="utf-8"),
                    HARNESS.read_text(encoding="utf-8"),
                )
            except (OSError, ValidationError) as error:
                print(f"PostgreSQL semantic recovery contract failed: {error}", file=sys.stderr)
                return 1
            print("PostgreSQL semantic recovery source contract: OK")
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
        : "${POSTGRES_IMAGE:?POSTGRES_IMAGE must bind an immutable or recorded image reference}"
        EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-semantic-recovery}
        CONTAINER=${POSTGRES_CONTAINER_NAME:-trnm-semantic-recovery-pg}
        PORT=${POSTGRES_PORT:-55433}
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

        psql_file() {
          local database=$1
          local file=$2
          docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm -d "$database" \
            < "$file"
        }
        psql_command() {
          local database=$1
          local sql=$2
          docker exec "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm -d "$database" \
            -c "$sql"
        }

        psql_file trnm_source "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
          > "$EVIDENCE_DIR/catalog-before-repeat.txt"
        if cat "${migrations[@]}" | docker exec -i "$CONTAINER" \
             psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm_source \
             > "$EVIDENCE_DIR/repeat-migration.stdout" \
             2> "$EVIDENCE_DIR/repeat-migration.stderr"; then
          echo "repeat-migration unexpectedly succeeded" >&2
          exit 1
        fi
        psql_file trnm_source "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
          > "$EVIDENCE_DIR/catalog-after-repeat.txt"
        cmp --silent "$EVIDENCE_DIR/catalog-before-repeat.txt" \
          "$EVIDENCE_DIR/catalog-after-repeat.txt"

        cat <<'SQL' | docker exec -i "$CONTAINER" \
          psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm_source
        INSERT INTO trnm_schema_metadata VALUES
          (1, 1, 'postgresql', repeat('a', 40), 10);
        INSERT INTO trnm_entity_heads VALUES
          (decode(repeat('11',16),'hex'), 1, 1, 1, decode(repeat('12',32),'hex'), 10);
        INSERT INTO trnm_command_receipts VALUES
          (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'),
           decode(repeat('23',32),'hex'), 1, decode(repeat('24',32),'hex'), 1, 1, 1, 10);
        INSERT INTO trnm_events VALUES
          (decode(repeat('11',16),'hex'), 1, decode(repeat('33',16),'hex'),
           decode(repeat('22',16),'hex'), decode(repeat('34',32),'hex'), 10);
        INSERT INTO trnm_outbox VALUES
          (decode(repeat('44',16),'hex'), decode(repeat('11',16),'hex'),
           decode(repeat('22',16),'hex'), 0, decode(repeat('45',32),'hex'),
           0, 0, 0, NULL, NULL, NULL, 10, 10);
        INSERT INTO trnm_command_outbox VALUES
          (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'), 0,
           decode(repeat('44',16),'hex'));
        INSERT INTO trnm_authority_leases VALUES
          (decode(repeat('11',16),'hex'), decode(repeat('55',16),'hex'), 1, 1, 1000, 10);
        INSERT INTO trnm_session_families VALUES
          (decode(repeat('66',16),'hex'), decode(repeat('77',16),'hex'), 0,
           decode(repeat('88',16),'hex'), NULL, 10, 10);
        INSERT INTO trnm_refresh_tokens VALUES
          (decode(repeat('66',16),'hex'), decode(repeat('88',16),'hex'),
           decode(repeat('89',32),'hex'), 0, 0, 10, NULL);
        INSERT INTO trnm_storage_objects VALUES
          ('recovery', 'fixture', decode(repeat('77',16),'hex'), decode('010203','hex'),
           decode(repeat('99',32),'hex'), 2, 1, 10);
        SQL

        negative_constraint() {
          local label=$1
          local sql=$2
          if psql_command trnm_source "$sql" \
               > "$EVIDENCE_DIR/negative-constraint-${label}.stdout" \
               2> "$EVIDENCE_DIR/negative-constraint-${label}.stderr"; then
            echo "negative-constraint ${label} unexpectedly succeeded" >&2
            exit 1
          fi
        }
        negative_constraint metadata-singleton \
          "INSERT INTO trnm_schema_metadata VALUES (2,1,'postgresql',repeat('b',40),10)"
        negative_constraint entity-id-width \
          "INSERT INTO trnm_entity_heads VALUES (decode('01','hex'),0,0,1,decode(repeat('02',32),'hex'),10)"
        negative_constraint receipt-event-range \
          "INSERT INTO trnm_command_receipts VALUES (decode(repeat('11',16),'hex'),decode(repeat('2a',16),'hex'),decode(repeat('2b',32),'hex'),2,decode(repeat('2c',32),'hex'),NULL,1,1,10)"
        negative_constraint event-foreign-key \
          "INSERT INTO trnm_events VALUES (decode(repeat('11',16),'hex'),2,decode(repeat('3a',16),'hex'),decode(repeat('3b',16),'hex'),decode(repeat('3c',32),'hex'),10)"
        negative_constraint outbox-state-shape \
          "INSERT INTO trnm_outbox VALUES (decode(repeat('4a',16),'hex'),decode(repeat('11',16),'hex'),decode(repeat('22',16),'hex'),0,decode(repeat('4b',32),'hex'),0,1,1,NULL,NULL,NULL,10,10)"
        negative_constraint command-outbox-position \
          "INSERT INTO trnm_command_outbox VALUES (decode(repeat('11',16),'hex'),decode(repeat('22',16),'hex'),64,decode(repeat('44',16),'hex'))"
        negative_constraint lease-generation \
          "INSERT INTO trnm_authority_leases VALUES (decode(repeat('1a',16),'hex'),decode(repeat('5a',16),'hex'),0,1,100,10)"
        negative_constraint session-state-shape \
          "INSERT INTO trnm_session_families VALUES (decode(repeat('6a',16),'hex'),decode(repeat('7a',16),'hex'),0,NULL,NULL,10,10)"
        negative_constraint refresh-consumed-shape \
          "INSERT INTO trnm_refresh_tokens VALUES (decode(repeat('66',16),'hex'),decode(repeat('8a',16),'hex'),decode(repeat('8b',32),'hex'),1,1,10,NULL)"
        negative_constraint storage-collection \
          "INSERT INTO trnm_storage_objects VALUES ('','bad',decode(repeat('7a',16),'hex'),decode('01','hex'),decode(repeat('9a',32),'hex'),2,1,10)"

        psql_file trnm_source "$ROOT/scripts/postgresql-semantic-snapshot.sql" \
          > "$EVIDENCE_DIR/source-data.txt"
        psql_file trnm_source "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
          > "$EVIDENCE_DIR/source-catalog.txt"
        docker exec "$CONTAINER" pg_dump -U trnm --format=custom --no-owner --no-acl trnm_source \
          > "$EVIDENCE_DIR/source.dump"
        test -s "$EVIDENCE_DIR/source.dump"
        psql_command postgres "CREATE DATABASE trnm_restored" >/dev/null
        docker exec -i "$CONTAINER" pg_restore -U trnm --exit-on-error --no-owner --no-acl \
          -d trnm_restored < "$EVIDENCE_DIR/source.dump"
        psql_file trnm_restored "$ROOT/scripts/postgresql-semantic-snapshot.sql" \
          > "$EVIDENCE_DIR/restored-data.txt"
        psql_file trnm_restored "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
          > "$EVIDENCE_DIR/restored-catalog.txt"
        cmp --silent "$EVIDENCE_DIR/source-data.txt" "$EVIDENCE_DIR/restored-data.txt"
        cmp --silent "$EVIDENCE_DIR/source-catalog.txt" "$EVIDENCE_DIR/restored-catalog.txt"

        docker inspect --format='{{.Image}}' "$CONTAINER" > "$EVIDENCE_DIR/image-id.txt"
        python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" <<'PY'
        import hashlib
        import json
        import sys
        from pathlib import Path
        root=Path(sys.argv[1])
        evidence=Path(sys.argv[2])
        image=sys.argv[3]
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        manifest={
            "schema":"trillionnium.postgresql-semantic-recovery-evidence.v1",
            "profile":"postgresql",
            "image_reference":image,
            "image_id":(evidence/"image-id.txt").read_text().strip(),
            "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
            "backup_sha256":digest(evidence/"source.dump"),
            "source_data_sha256":digest(evidence/"source-data.txt"),
            "restored_data_sha256":digest(evidence/"restored-data.txt"),
            "source_catalog_sha256":digest(evidence/"source-catalog.txt"),
            "restored_catalog_sha256":digest(evidence/"restored-catalog.txt"),
            "semantic_data_equal": True,
            "semantic_catalog_equal": True,
            "repeat_migration_rejected_without_catalog_change": True,
            "negative_constraint_probe_count": 10,
            "claim_boundary": {
                "accepted_evidence": False,
                "independently_accepted": False,
                "pitr_proven": False,
                "approved_rpo_rto": False,
                "ha_proven": False,
                "production_ready": False,
            },
        }
        if manifest["source_data_sha256"] != manifest["restored_data_sha256"]:
            raise SystemExit("semantic data digest mismatch")
        if manifest["source_catalog_sha256"] != manifest["restored_catalog_sha256"]:
            raise SystemExit("semantic catalog digest mismatch")
        (evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
        print(json.dumps(manifest,sort_keys=True))
        PY
        python3 "$ROOT/scripts/check-postgresql-semantic-recovery.py"
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
        CHECKER = ROOT / "scripts/check-postgresql-semantic-recovery.py"

        def load_checker():
            spec = importlib.util.spec_from_file_location("postgresql_semantic_recovery_contract", CHECKER)
            if spec is None or spec.loader is None:
                raise RuntimeError("checker loader unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        class PostgreSqlSemanticRecoveryContractTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.checker = load_checker()
                cls.data = cls.checker.DATA.read_text(encoding="utf-8")
                cls.catalog = cls.checker.CATALOG.read_text(encoding="utf-8")
                cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

            def test_real_contract_passes(self):
                self.checker.validate_texts(self.data, self.catalog, self.harness)

            def test_missing_table_rejected(self):
                with self.assertRaisesRegex(self.checker.ValidationError, "semantic snapshot missing"):
                    self.checker.validate_texts(
                        self.data.replace("trnm_storage_objects", "removed_storage_table"),
                        self.catalog,
                        self.harness,
                    )

            def test_missing_restore_or_failure_suppression_rejected(self):
                with self.assertRaisesRegex(self.checker.ValidationError, "pg_restore"):
                    self.checker.validate_texts(
                        self.data,
                        self.catalog,
                        self.harness.replace("pg_restore", "removed_restore", 1),
                    )
                with self.assertRaisesRegex(self.checker.ValidationError, "failure suppression"):
                    self.checker.validate_texts(
                        self.data,
                        self.catalog,
                        self.harness + "\nfalse || true\n",
                    )

            def test_command_line_checker_passes(self):
                result = subprocess.run(
                    [sys.executable, str(CHECKER)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("semantic recovery source contract: OK", result.stdout)

        if __name__ == "__main__":
            unittest.main()
        '''
    )


def update_documents(root: Path) -> None:
    path = root / "docs/development/SCHEMA_AUTHORITY.json"
    value: dict[str, Any] = json.loads(read(path))
    authority = value.setdefault("authority", {})
    authority["postgresql_semantic_recovery_source_candidate"] = {
        "harness": "scripts/ci-postgresql-semantic-recovery.sh",
        "data_snapshot": "scripts/postgresql-semantic-snapshot.sql",
        "catalog_snapshot": "scripts/postgresql-catalog-snapshot.sql",
        "source_contract": "python3 scripts/check-postgresql-semantic-recovery.py",
        "covers": [
            "repeat migration rejection without catalog drift",
            "ten critical negative-constraint probes",
            "custom-format logical backup",
            "empty-database restore",
            "exact canonical data comparison",
            "column constraint and index semantic comparison",
            "migration-lock and artifact digests",
        ],
        "accepted": False,
    }
    boundary = value.setdefault("claim_boundary", {})
    boundary["postgresql_semantic_restore_source_candidate"] = True
    boundary["rollback_proven"] = False
    boundary["pitr_proven"] = False
    boundary["ha_proven"] = False
    boundary["production_ready"] = False
    write(path, json.dumps(value, indent=2, ensure_ascii=False))

    operations = root / "docs/OPERATIONS_AND_RELEASE.md"
    text = read(operations)
    section = textwrap.dedent(
        '''\

        ## PostgreSQL semantic recovery candidate

        `scripts/ci-postgresql-semantic-recovery.sh` applies only the authoritative `migrations/postgresql` chain, proves that a repeat application fails without catalog drift, executes ten malformed-row/constraint probes, creates a custom-format logical backup, restores into an empty database, and compares canonical data plus columns, constraints and indexes. The retained manifest binds the database image ID, migration-lock digest, backup digest and source/restored semantic digests.

        This packet is a repository-controlled recovery source candidate. It does not establish PITR, approved RPO/RTO, primary failover, multi-node durability, independent acceptance, production readiness, cutover or rollback authorization.
        '''
    )
    if "## PostgreSQL semantic recovery candidate" not in text:
        text += section
    write(operations, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    write(root / "scripts/postgresql-semantic-snapshot.sql", data_snapshot_sql())
    write(root / "scripts/postgresql-catalog-snapshot.sql", catalog_snapshot_sql())
    write(root / "scripts/check-postgresql-semantic-recovery.py", checker_source())
    write(root / "scripts/ci-postgresql-semantic-recovery.sh", harness_source())
    write(
        root / "tests/control_plane/test_postgresql_semantic_recovery_contract.py",
        test_source(),
    )
    update_documents(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
