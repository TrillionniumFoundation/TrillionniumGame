#!/usr/bin/env python3
"""Compose a synchronous PostgreSQL primary-to-standby failover evidence candidate."""
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
        """Validate the PostgreSQL synchronous failover source contract."""
        from __future__ import annotations

        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        HARNESS = ROOT / "scripts/ci-postgresql-primary-failover.sh"
        REQUIRED = (
            "pg_basebackup",
            "synchronous_standby_names",
            "synchronous_commit",
            "remote_apply",
            "pg_stat_replication",
            "pg_last_wal_replay_lsn",
            "pg_ctl",
            "promote",
            "postgresql-semantic-snapshot.sql",
            "acknowledged-snapshot.txt",
            "promoted-snapshot.txt",
            "cmp --silent",
            "post-failover-write",
            "measured_failover_ms",
            '"zero_acknowledged_loss_observed": True',
            '"approved_rpo_rto": False',
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
                require(marker in text, f"primary-failover harness missing {marker}")
            require("database/schema/v2" not in text, "non-authoritative schema referenced")
            require("DROP TABLE" not in text.upper(), "destructive schema rollback introduced")
            require("|| true" not in text, "failure suppression introduced")
            require("docker start trnm-pg-primary" not in text, "old primary automatically reintroduced")

        def main() -> int:
            try:
                validate_text(HARNESS.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                print(f"PostgreSQL primary failover contract failed: {error}", file=sys.stderr)
                return 1
            print("PostgreSQL primary failover source contract: OK")
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
        EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-primary-failover}
        NETWORK=${POSTGRES_NETWORK:-trnm-pg-ha-network}
        PRIMARY=${POSTGRES_PRIMARY_NAME:-trnm-pg-primary}
        STANDBY=${POSTGRES_STANDBY_NAME:-trnm-pg-standby}
        PRIMARY_VOLUME=${POSTGRES_PRIMARY_VOLUME:-trnm-pg-primary-data}
        STANDBY_VOLUME=${POSTGRES_STANDBY_VOLUME:-trnm-pg-standby-data}
        PRIMARY_PORT=${POSTGRES_PRIMARY_PORT:-55436}
        STANDBY_PORT=${POSTGRES_STANDBY_PORT:-55437}
        mkdir -p "$EVIDENCE_DIR"

        cleanup() {
          docker rm -f "$PRIMARY" "$STANDBY" >/dev/null 2>&1 || :
          docker volume rm -f "$PRIMARY_VOLUME" "$STANDBY_VOLUME" >/dev/null 2>&1 || :
          docker network rm "$NETWORK" >/dev/null 2>&1 || :
        }
        trap cleanup EXIT
        cleanup
        docker pull "$POSTGRES_IMAGE"
        docker network create "$NETWORK" >/dev/null
        docker volume create "$PRIMARY_VOLUME" >/dev/null
        docker volume create "$STANDBY_VOLUME" >/dev/null
        docker run -d --name "$PRIMARY" --network "$NETWORK" \
          -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=trnm \
          -v "$PRIMARY_VOLUME:/var/lib/postgresql/data" \
          -p "${PRIMARY_PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
        for _ in $(seq 1 90); do
          docker exec "$PRIMARY" pg_isready -U postgres -d trnm >/dev/null 2>&1 && break
          sleep 1
        done
        docker exec "$PRIMARY" pg_isready -U postgres -d trnm >/dev/null

        primary_sql() {
          docker exec "$PRIMARY" psql -v ON_ERROR_STOP=1 -X -q -U postgres -d trnm -c "$1"
        }
        standby_scalar() {
          docker exec "$STANDBY" psql -X -q -tA -U postgres -d trnm -c "$1"
        }
        primary_scalar() {
          docker exec "$PRIMARY" psql -X -q -tA -U postgres -d trnm -c "$1"
        }
        snapshot() {
          local container=$1
          docker exec -i "$container" psql -v ON_ERROR_STOP=1 -X -q -tA \
            -U postgres -d trnm < "$ROOT/scripts/postgresql-semantic-snapshot.sql"
        }

        mapfile -t migrations < <(find "$ROOT/migrations/postgresql" -maxdepth 1 -type f -name '*_up.sql' | sort)
        test "${#migrations[@]}" -gt 0
        cat "${migrations[@]}" | docker exec -i "$PRIMARY" \
          psql -v ON_ERROR_STOP=1 -U postgres -d trnm >/dev/null
        primary_sql "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator'" >/dev/null
        docker exec "$PRIMARY" sh -ceu \
          "printf '%s\\n' 'host replication replicator 0.0.0.0/0 scram-sha-256' >> \"\$PGDATA/pg_hba.conf\""
        primary_sql "SELECT pg_reload_conf()" >/dev/null
        primary_sql "INSERT INTO trnm_schema_metadata VALUES (1,1,'postgresql',repeat('a',40),10)" >/dev/null
        primary_sql "INSERT INTO trnm_entity_heads VALUES (decode(repeat('11',16),'hex'),0,0,1,decode(repeat('12',32),'hex'),10)" >/dev/null

        docker run --rm --network "$NETWORK" -e PGPASSWORD=replicator \
          -v "$STANDBY_VOLUME:/var/lib/postgresql/data" --entrypoint bash "$POSTGRES_IMAGE" \
          -ceu "rm -rf /var/lib/postgresql/data/*; chown -R postgres:postgres /var/lib/postgresql/data; \
                 gosu postgres pg_basebackup -h ${PRIMARY} -p 5432 -U replicator \
                   -D /var/lib/postgresql/data -Fp -Xs -P -R; \
                 printf '%s\\n' \"primary_conninfo = 'host=${PRIMARY} port=5432 user=replicator password=replicator application_name=trnm_standby'\" \
                   >> /var/lib/postgresql/data/postgresql.auto.conf; \
                 chown postgres:postgres /var/lib/postgresql/data/postgresql.auto.conf"
        docker run -d --name "$STANDBY" --network "$NETWORK" \
          -v "$STANDBY_VOLUME:/var/lib/postgresql/data" \
          -p "${STANDBY_PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
        for _ in $(seq 1 120); do
          docker exec "$STANDBY" pg_isready -U postgres -d trnm >/dev/null 2>&1 && break
          sleep 1
        done
        docker exec "$STANDBY" pg_isready -U postgres -d trnm >/dev/null
        test "$(standby_scalar 'SELECT pg_is_in_recovery()')" = t
        for _ in $(seq 1 80); do
          streaming=$(primary_scalar "SELECT count(*) FROM pg_stat_replication WHERE application_name='trnm_standby' AND state='streaming'")
          [[ "$streaming" = 1 ]] && break
          sleep 0.5
        done
        test "$streaming" = 1
        primary_sql "ALTER SYSTEM SET synchronous_standby_names='*'" >/dev/null
        primary_sql "ALTER SYSTEM SET synchronous_commit='remote_apply'" >/dev/null
        primary_sql "SELECT pg_reload_conf()" >/dev/null
        for _ in $(seq 1 60); do
          sync_state=$(primary_scalar "SELECT sync_state FROM pg_stat_replication WHERE application_name='trnm_standby'")
          [[ "$sync_state" = sync ]] && break
          sleep 0.5
        done
        test "$sync_state" = sync

        cat <<'SQL' | docker exec -i "$PRIMARY" psql -v ON_ERROR_STOP=1 -X -q -U postgres -d trnm
        BEGIN;
        UPDATE trnm_entity_heads
        SET revision=1, last_event_sequence=1, state_digest=decode(repeat('13',32),'hex'), updated_at_ms=20
        WHERE entity_id=decode(repeat('11',16),'hex') AND revision=0 AND authority_generation=1;
        INSERT INTO trnm_command_receipts VALUES
          (decode(repeat('11',16),'hex'),decode(repeat('21',16),'hex'),decode(repeat('22',32),'hex'),
           1,decode(repeat('13',32),'hex'),1,1,1,20);
        INSERT INTO trnm_events VALUES
          (decode(repeat('11',16),'hex'),1,decode(repeat('31',16),'hex'),decode(repeat('21',16),'hex'),
           decode(repeat('32',32),'hex'),20);
        INSERT INTO trnm_outbox VALUES
          (decode(repeat('41',16),'hex'),decode(repeat('11',16),'hex'),decode(repeat('21',16),'hex'),
           4,decode(repeat('42',32),'hex'),0,0,0,NULL,NULL,NULL,20,20);
        INSERT INTO trnm_command_outbox VALUES
          (decode(repeat('11',16),'hex'),decode(repeat('21',16),'hex'),0,decode(repeat('41',16),'hex'));
        COMMIT;
        SQL
        test "$(primary_scalar "SHOW synchronous_commit")" = remote_apply
        for _ in $(seq 1 80); do
          replicated=$(standby_scalar "SELECT count(*) FROM trnm_command_receipts WHERE command_id=decode(repeat('21',16),'hex')")
          [[ "$replicated" = 1 ]] && break
          sleep 0.25
        done
        test "$replicated" = 1
        snapshot "$PRIMARY" > "$EVIDENCE_DIR/acknowledged-snapshot.txt"
        snapshot "$STANDBY" > "$EVIDENCE_DIR/standby-before-failover.txt"
        cmp --silent "$EVIDENCE_DIR/acknowledged-snapshot.txt" \
          "$EVIDENCE_DIR/standby-before-failover.txt"
        acknowledged_lsn=$(primary_scalar 'SELECT pg_current_wal_flush_lsn()')
        replay_lsn=$(standby_scalar 'SELECT pg_last_wal_replay_lsn()')
        replay_covers_ack=$(standby_scalar "SELECT pg_wal_lsn_diff(pg_last_wal_replay_lsn(),'${acknowledged_lsn}') >= 0")
        test "$replay_covers_ack" = t
        printf '%s\n' "$acknowledged_lsn" > "$EVIDENCE_DIR/acknowledged-lsn.txt"
        printf '%s\n' "$replay_lsn" > "$EVIDENCE_DIR/replay-lsn.txt"

        failover_started_ms=$(date +%s%3N)
        docker stop --time 2 "$PRIMARY" >/dev/null
        docker exec -u postgres "$STANDBY" pg_ctl -D /var/lib/postgresql/data -w promote \
          > "$EVIDENCE_DIR/promote.stdout"
        for _ in $(seq 1 80); do
          promoted=$(standby_scalar 'SELECT NOT pg_is_in_recovery()')
          [[ "$promoted" = t ]] && break
          sleep 0.25
        done
        test "$promoted" = t
        failover_finished_ms=$(date +%s%3N)
        measured_failover_ms=$((failover_finished_ms-failover_started_ms))
        printf '%s\n' "$measured_failover_ms" > "$EVIDENCE_DIR/measured-failover-ms.txt"
        snapshot "$STANDBY" > "$EVIDENCE_DIR/promoted-snapshot.txt"
        cmp --silent "$EVIDENCE_DIR/acknowledged-snapshot.txt" \
          "$EVIDENCE_DIR/promoted-snapshot.txt"
        docker exec "$STANDBY" psql -v ON_ERROR_STOP=1 -X -q -U postgres -d trnm \
          -c "INSERT INTO trnm_storage_objects VALUES ('post-failover-write','accepted',decode(repeat('51',16),'hex'),decode('0102','hex'),decode(repeat('52',32),'hex'),2,1,30)" \
          >/dev/null
        post_failover_write=$(standby_scalar "SELECT count(*) FROM trnm_storage_objects WHERE collection='post-failover-write'")
        test "$post_failover_write" = 1
        printf '%s\n' "$post_failover_write" > "$EVIDENCE_DIR/post-failover-write.txt"

        docker inspect --format='{{.Image}}' "$PRIMARY" > "$EVIDENCE_DIR/primary-image-id.txt"
        docker inspect --format='{{.Image}}' "$STANDBY" > "$EVIDENCE_DIR/standby-image-id.txt"
        docker exec "$STANDBY" pg_controldata /var/lib/postgresql/data \
          > "$EVIDENCE_DIR/promoted-pg-controldata.txt"
        python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" "$measured_failover_ms" <<'PY'
        import hashlib,json,sys
        from pathlib import Path
        root=Path(sys.argv[1]); evidence=Path(sys.argv[2])
        def digest(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
        manifest={
          "schema":"trillionnium.postgresql-primary-failover-evidence.v1",
          "profile":"postgresql",
          "image_reference":sys.argv[3],
          "primary_image_id":(evidence/"primary-image-id.txt").read_text().strip(),
          "standby_image_id":(evidence/"standby-image-id.txt").read_text().strip(),
          "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
          "acknowledged_snapshot_sha256":digest(evidence/"acknowledged-snapshot.txt"),
          "promoted_snapshot_sha256":digest(evidence/"promoted-snapshot.txt"),
          "acknowledged_lsn":(evidence/"acknowledged-lsn.txt").read_text().strip(),
          "replay_lsn_before_failover":(evidence/"replay-lsn.txt").read_text().strip(),
          "synchronous_commit":"remote_apply",
          "measured_failover_ms":int(sys.argv[4]),
          "zero_acknowledged_loss_observed":True,
          "post_failover_write_succeeded":True,
          "claim_boundary":{
            "approved_rpo_rto":False,
            "automatic_failback_proven":False,
            "split_brain_prevention_independently_accepted":False,
            "accepted_evidence":False,
            "independently_accepted":False,
            "production_ready":False,
          },
        }
        if manifest["acknowledged_snapshot_sha256"] != manifest["promoted_snapshot_sha256"]:
          raise SystemExit('acknowledged state changed across promotion')
        (evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        print(json.dumps(manifest,sort_keys=True))
        PY
        python3 "$ROOT/scripts/check-postgresql-primary-failover.py"
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,subprocess,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]
        CHECKER=ROOT/"scripts/check-postgresql-primary-failover.py"
        def load_checker():
            spec=importlib.util.spec_from_file_location("postgresql_primary_failover_contract",CHECKER)
            if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
        class PostgreSqlPrimaryFailoverContractTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.checker=load_checker(); cls.text=cls.checker.HARNESS.read_text(encoding="utf-8")
            def test_real_contract_passes(self): cls.checker.validate_text(self.text)
            def test_async_or_old_primary_reintroduction_rejected(self):
                with self.assertRaisesRegex(self.checker.ValidationError,"remote_apply"):
                    self.checker.validate_text(self.text.replace("remote_apply","local",1))
                with self.assertRaisesRegex(self.checker.ValidationError,"old primary"):
                    self.checker.validate_text(self.text+"\ndocker start trnm-pg-primary\n")
            def test_command_line_checker_passes(self):
                result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn("primary failover source contract: OK",result.stdout)
        if __name__=="__main__": unittest.main()
        '''
    )


def update_documents(root: Path) -> None:
    path=root/"docs/development/SCHEMA_AUTHORITY.json"
    value:dict[str,Any]=json.loads(read(path))
    authority=value.setdefault("authority",{})
    authority["postgresql_primary_failover_source_candidate"]={
      "harness":"scripts/ci-postgresql-primary-failover.sh",
      "source_contract":"python3 scripts/check-postgresql-primary-failover.py",
      "semantics":[
        "streaming physical standby from pg_basebackup",
        "synchronous remote_apply acknowledged command",
        "standby replay covers acknowledged WAL LSN",
        "primary stopped before promotion",
        "canonical state equal after promotion",
        "post-promotion write succeeds",
      ],
      "accepted":False,
    }
    boundary=value.setdefault("claim_boundary",{})
    boundary["postgresql_primary_failover_source_candidate"]=True
    boundary["ha_proven"]=False
    boundary["production_ready"]=False
    write(path,json.dumps(value,indent=2,ensure_ascii=False))
    operations=root/"docs/OPERATIONS_AND_RELEASE.md"
    text=read(operations)
    section=textwrap.dedent(
      '''\

      ## PostgreSQL synchronous primary failover candidate

      `scripts/ci-postgresql-primary-failover.sh` creates a physical standby with `pg_basebackup`, requires streaming state, changes acknowledgement to `synchronous_commit=remote_apply`, commits the authoritative command/event/outbox transaction, and proves the standby replay LSN covers that acknowledgement. It then stops the primary, promotes the standby, compares canonical state byte-for-byte and writes successfully after promotion.

      The measured failover interval is retained, not declared an approved RTO. The packet does not prove automatic failback, production fencing or split-brain controls, repeated regional failure, independent acceptance or production readiness.
      '''
    )
    if "## PostgreSQL synchronous primary failover candidate" not in text: text+=section
    write(operations,text)


def run(root:Path)->None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/check-postgresql-primary-failover.py",checker_source())
    write(root/"scripts/ci-postgresql-primary-failover.sh",harness_source())
    write(root/"tests/control_plane/test_postgresql_primary_failover_contract.py",test_source())
    update_documents(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
