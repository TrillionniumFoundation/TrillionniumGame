#!/usr/bin/env python3
"""Compose a three-node CockroachDB leaseholder and node-failover evidence candidate."""
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
        """Validate CockroachDB leaseholder and node-failover evidence source."""
        from __future__ import annotations
        import sys
        from pathlib import Path

        ROOT=Path(__file__).resolve().parents[1]
        HARNESS=ROOT/"scripts/ci-cockroachdb-node-failover.sh"
        REQUIRED=(
          "start --insecure --join=",
          "cockroach init",
          "SHOW RANGES FROM TABLE trnm_entity_heads WITH DETAILS",
          "RELOCATE LEASE TO 1",
          "docker network disconnect",
          "leaseholder-partition",
          "partition-acknowledged-snapshot.txt",
          "cmp --silent",
          "partition-write",
          "docker stop --time 2",
          "node-stop-write",
          "rejoined-node-snapshot.txt",
          "partition_recovery_ms",
          "node_stop_recovery_ms",
          '"zero_acknowledged_loss_observed": True',
          '"approved_rpo_rto": False',
          '"accepted_evidence": False',
          '"production_ready": False',
        )
        class ValidationError(RuntimeError): pass
        def require(value:bool,message:str)->None:
          if not value: raise ValidationError(message)
        def validate_text(text:str)->None:
          for marker in REQUIRED: require(marker in text,f"CockroachDB failover harness missing {marker}")
          require("migrations/postgresql" not in text,"PostgreSQL evidence inherited")
          require("database/schema/v2" not in text,"non-authoritative schema referenced")
          require("DROP TABLE" not in text.upper(),"destructive schema rollback introduced")
          require("|| true" not in text,"failure suppression introduced")
          require("single-node" not in text,"single-node execution cannot claim node failover")
        def main()->int:
          try: validate_text(HARNESS.read_text(encoding="utf-8"))
          except (OSError,ValidationError) as error:
            print(f"CockroachDB node failover contract failed: {error}",file=sys.stderr); return 1
          print("CockroachDB node failover source contract: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
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
        : "${COCKROACH_IMAGE:?COCKROACH_IMAGE is required}"
        EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/cockroachdb-node-failover}
        NETWORK=${COCKROACH_NETWORK:-trnm-crdb-ha-network}
        N1=${COCKROACH_NODE1:-trnm-crdb1}
        N2=${COCKROACH_NODE2:-trnm-crdb2}
        N3=${COCKROACH_NODE3:-trnm-crdb3}
        V1=${COCKROACH_VOLUME1:-trnm-crdb1-data}
        V2=${COCKROACH_VOLUME2:-trnm-crdb2-data}
        V3=${COCKROACH_VOLUME3:-trnm-crdb3-data}
        mkdir -p "$EVIDENCE_DIR"

        cleanup() {
          docker rm -f "$N1" "$N2" "$N3" >/dev/null 2>&1 || :
          docker volume rm -f "$V1" "$V2" "$V3" >/dev/null 2>&1 || :
          docker network rm "$NETWORK" >/dev/null 2>&1 || :
        }
        trap cleanup EXIT
        cleanup
        docker pull "$COCKROACH_IMAGE"
        docker network create "$NETWORK" >/dev/null
        docker volume create "$V1" >/dev/null
        docker volume create "$V2" >/dev/null
        docker volume create "$V3" >/dev/null
        join="${N1}:26257,${N2}:26257,${N3}:26257"
        for spec in "$N1:$V1:26261:18091" "$N2:$V2:26262:18092" "$N3:$V3:26263:18093"; do
          IFS=: read -r node volume sql_port http_port <<< "$spec"
          docker create --name "$node" --hostname "$node" --network "$NETWORK" \
            -v "$volume:/cockroach/cockroach-data" \
            -p "${sql_port}:26257" -p "${http_port}:8080" "$COCKROACH_IMAGE" \
            start --insecure --join="$join" --advertise-addr="${node}:26257" \
            --listen-addr=0.0.0.0:26257 --http-addr=0.0.0.0:8080 >/dev/null
        done
        docker start "$N1" "$N2" "$N3" >/dev/null
        initialized=false
        for _ in $(seq 1 100); do
          if docker exec "$N1" cockroach init --insecure --host="${N1}:26257" \
               > "$EVIDENCE_DIR/cockroach-init.stdout" 2> "$EVIDENCE_DIR/cockroach-init.stderr"; then
            initialized=true; break
          fi
          grep -Eqi 'cluster has already been initialized|already initialized' \
            "$EVIDENCE_DIR/cockroach-init.stderr" && { initialized=true; break; }
          sleep 1
        done
        test "$initialized" = true

        sql() {
          local node=$1 database=$2 statement=$3
          docker exec "$node" cockroach sql --insecure --host="${node}:26257" \
            --database="$database" --format=tsv -e "$statement"
        }
        scalar() {
          sql "$1" "$2" "$3" | tail -n 1 | tr -d '\r'
        }
        snapshot() {
          local node=$1
          docker exec -i "$node" cockroach sql --insecure --host="${node}:26257" \
            --database=trnm --format=tsv < "$ROOT/scripts/cockroachdb-semantic-snapshot.sql"
        }
        for node in "$N1" "$N2" "$N3"; do
          for _ in $(seq 1 120); do
            sql "$node" defaultdb 'SELECT 1' >/dev/null 2>&1 && break
            sleep 1
          done
          test "$(scalar "$node" defaultdb 'SELECT 1')" = 1
        done
        sql "$N1" defaultdb 'CREATE DATABASE IF NOT EXISTS trnm' >/dev/null
        mapfile -t migrations < <(find "$ROOT/migrations/cockroachdb" -maxdepth 1 -type f -name '*_up.sql' | sort)
        test "${#migrations[@]}" -gt 0
        cat "${migrations[@]}" | docker exec -i "$N1" cockroach sql --insecure \
          --host="${N1}:26257" --database=trnm >/dev/null
        cat <<'SQL' | docker exec -i "$N1" cockroach sql --insecure --host="trnm-crdb1:26257" --database=trnm >/dev/null
        INSERT INTO trnm_schema_metadata VALUES (1,1,'cockroachdb',repeat('a',40),10);
        INSERT INTO trnm_entity_heads VALUES
          (decode(repeat('11',16),'hex'),1,1,1,decode(repeat('12',32),'hex'),10);
        INSERT INTO trnm_command_receipts VALUES
          (decode(repeat('11',16),'hex'),decode(repeat('21',16),'hex'),decode(repeat('22',32),'hex'),
           1,decode(repeat('12',32),'hex'),1,1,1,10);
        INSERT INTO trnm_events VALUES
          (decode(repeat('11',16),'hex'),1,decode(repeat('31',16),'hex'),decode(repeat('21',16),'hex'),
           decode(repeat('32',32),'hex'),10);
        INSERT INTO trnm_outbox VALUES
          (decode(repeat('41',16),'hex'),decode(repeat('11',16),'hex'),decode(repeat('21',16),'hex'),
           4,decode(repeat('42',32),'hex'),0,0,0,NULL,NULL,NULL,10,10);
        INSERT INTO trnm_command_outbox VALUES
          (decode(repeat('11',16),'hex'),decode(repeat('21',16),'hex'),0,decode(repeat('41',16),'hex'));
        SQL

        range_id=''
        for _ in $(seq 1 120); do
          range_id=$(scalar "$N1" trnm \
            "SELECT range_id FROM [SHOW RANGES FROM TABLE trnm_entity_heads WITH DETAILS] ORDER BY range_id LIMIT 1")
          replicas=$(scalar "$N1" trnm \
            "SELECT array_length(voting_replicas,1) FROM [SHOW RANGES FROM TABLE trnm_entity_heads WITH DETAILS] WHERE range_id=${range_id} LIMIT 1" 2>/dev/null || :)
          [[ -n "$range_id" && "$replicas" = 3 ]] && break
          sleep 1
        done
        test -n "$range_id"
        test "$replicas" = 3
        sql "$N1" trnm "ALTER RANGE ${range_id} RELOCATE LEASE TO 1" >/dev/null
        for _ in $(seq 1 80); do
          lease_holder=$(scalar "$N1" trnm \
            "SELECT lease_holder FROM [SHOW RANGES FROM TABLE trnm_entity_heads WITH DETAILS] WHERE range_id=${range_id}")
          [[ "$lease_holder" = 1 ]] && break
          sleep 0.5
        done
        test "$lease_holder" = 1
        sql "$N1" defaultdb \
          "SELECT node_id,address FROM crdb_internal.gossip_nodes ORDER BY node_id" \
          > "$EVIDENCE_DIR/node-topology.txt"
        snapshot "$N1" > "$EVIDENCE_DIR/partition-acknowledged-snapshot.txt"

        partition_started_ms=$(date +%s%3N)
        docker network disconnect "$NETWORK" "$N1"
        partition_available=false
        for _ in $(seq 1 160); do
          if test "$(scalar "$N2" trnm "SELECT count(*) FROM trnm_command_receipts WHERE command_id=decode(repeat('21',16),'hex')" 2>/dev/null || :)" = 1; then
            partition_available=true; break
          fi
          sleep 0.25
        done
        test "$partition_available" = true
        partition_finished_ms=$(date +%s%3N)
        partition_recovery_ms=$((partition_finished_ms-partition_started_ms))
        snapshot "$N2" > "$EVIDENCE_DIR/partition-survivor-snapshot.txt"
        cmp --silent "$EVIDENCE_DIR/partition-acknowledged-snapshot.txt" \
          "$EVIDENCE_DIR/partition-survivor-snapshot.txt"
        sql "$N2" trnm \
          "INSERT INTO trnm_storage_objects VALUES ('partition-write','accepted',decode(repeat('51',16),'hex'),decode('01','hex'),decode(repeat('52',32),'hex'),2,1,20)" \
          >/dev/null
        test "$(scalar "$N2" trnm "SELECT count(*) FROM trnm_storage_objects WHERE collection='partition-write'")" = 1
        printf '%s\n' "$partition_recovery_ms" > "$EVIDENCE_DIR/partition-recovery-ms.txt"
        docker network connect "$NETWORK" "$N1"
        for _ in $(seq 1 160); do
          rejoined=$(scalar "$N1" trnm "SELECT count(*) FROM trnm_storage_objects WHERE collection='partition-write'" 2>/dev/null || :)
          [[ "$rejoined" = 1 ]] && break
          sleep 0.25
        done
        test "$rejoined" = 1

        current_holder=$(scalar "$N2" trnm \
          "SELECT lease_holder FROM [SHOW RANGES FROM TABLE trnm_entity_heads WITH DETAILS] WHERE range_id=${range_id}")
        if [[ "$current_holder" = 3 ]]; then
          sql "$N2" trnm "ALTER RANGE ${range_id} RELOCATE LEASE TO 2" >/dev/null
        fi
        for _ in $(seq 1 80); do
          current_holder=$(scalar "$N2" trnm \
            "SELECT lease_holder FROM [SHOW RANGES FROM TABLE trnm_entity_heads WITH DETAILS] WHERE range_id=${range_id}")
          [[ "$current_holder" != 3 ]] && break
          sleep 0.5
        done
        test "$current_holder" != 3
        node_stop_started_ms=$(date +%s%3N)
        docker stop --time 2 "$N3" >/dev/null
        node_available=false
        for _ in $(seq 1 120); do
          if test "$(scalar "$N2" trnm "SELECT count(*) FROM trnm_storage_objects WHERE collection='partition-write'" 2>/dev/null || :)" = 1; then
            node_available=true; break
          fi
          sleep 0.25
        done
        test "$node_available" = true
        node_stop_finished_ms=$(date +%s%3N)
        node_stop_recovery_ms=$((node_stop_finished_ms-node_stop_started_ms))
        sql "$N2" trnm \
          "INSERT INTO trnm_storage_objects VALUES ('node-stop-write','accepted',decode(repeat('61',16),'hex'),decode('02','hex'),decode(repeat('62',32),'hex'),2,1,30)" \
          >/dev/null
        test "$(scalar "$N2" trnm "SELECT count(*) FROM trnm_storage_objects WHERE collection='node-stop-write'")" = 1
        printf '%s\n' "$node_stop_recovery_ms" > "$EVIDENCE_DIR/node-stop-recovery-ms.txt"
        docker start "$N3" >/dev/null
        for _ in $(seq 1 180); do
          final_rejoin=$(scalar "$N3" trnm \
            "SELECT count(*) FROM trnm_storage_objects WHERE collection IN ('partition-write','node-stop-write')" 2>/dev/null || :)
          [[ "$final_rejoin" = 2 ]] && break
          sleep 0.5
        done
        test "$final_rejoin" = 2
        snapshot "$N3" > "$EVIDENCE_DIR/rejoined-node-snapshot.txt"
        for node in "$N1" "$N2" "$N3"; do
          docker inspect --format='{{.Image}}' "$node" >> "$EVIDENCE_DIR/node-image-ids.txt"
        done

        python3 - "$ROOT" "$EVIDENCE_DIR" "$COCKROACH_IMAGE" "$range_id" "$partition_recovery_ms" "$node_stop_recovery_ms" <<'PY'
        import hashlib,json,sys
        from pathlib import Path
        root=Path(sys.argv[1]); evidence=Path(sys.argv[2])
        def digest(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
        manifest={
          "schema":"trillionnium.cockroachdb-node-failover-evidence.v1",
          "profile":"cockroachdb",
          "image_reference":sys.argv[3],
          "node_image_ids":sorted(set((evidence/"node-image-ids.txt").read_text().splitlines())),
          "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
          "range_id":int(sys.argv[4]),
          "initial_lease_holder":1,
          "partition_recovery_ms":int(sys.argv[5]),
          "node_stop_recovery_ms":int(sys.argv[6]),
          "acknowledged_snapshot_sha256":digest(evidence/"partition-acknowledged-snapshot.txt"),
          "partition_survivor_snapshot_sha256":digest(evidence/"partition-survivor-snapshot.txt"),
          "rejoined_node_snapshot_sha256":digest(evidence/"rejoined-node-snapshot.txt"),
          "zero_acknowledged_loss_observed":True,
          "partition_write_succeeded":True,
          "node_stop_write_succeeded":True,
          "claim_boundary":{
            "approved_rpo_rto":False,
            "regional_partition_proven":False,
            "long_duration_partition_proven":False,
            "accepted_evidence":False,
            "independently_accepted":False,
            "production_ready":False,
          },
        }
        if manifest["acknowledged_snapshot_sha256"] != manifest["partition_survivor_snapshot_sha256"]:
          raise SystemExit('acknowledged data changed during leaseholder partition')
        (evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        print(json.dumps(manifest,sort_keys=True))
        PY
        python3 "$ROOT/scripts/check-cockroachdb-node-failover.py"
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,subprocess,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]; CHECKER=ROOT/"scripts/check-cockroachdb-node-failover.py"
        def load_checker():
          spec=importlib.util.spec_from_file_location("cockroachdb_node_failover_contract",CHECKER)
          if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
          module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
        class CockroachDbNodeFailoverContractTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.checker=load_checker(); cls.text=cls.checker.HARNESS.read_text(encoding="utf-8")
          def test_real_contract_passes(self): cls.checker.validate_text(self.text)
          def test_single_node_or_suppression_rejected(self):
            with self.assertRaisesRegex(self.checker.ValidationError,"single-node"): cls.checker.validate_text(self.text+"\nsingle-node\n")
            with self.assertRaisesRegex(self.checker.ValidationError,"failure suppression"): cls.checker.validate_text(self.text+"\nfalse || true\n")
          def test_command_line_checker_passes(self):
            result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn("node failover source contract: OK",result.stdout)
        if __name__=="__main__": unittest.main()
        '''
    )


def update_documents(root:Path)->None:
    path=root/"docs/development/SCHEMA_AUTHORITY.json"; value:dict[str,Any]=json.loads(read(path))
    authority=value.setdefault("authority",{})
    authority["cockroachdb_node_failover_source_candidate"]={
      "harness":"scripts/ci-cockroachdb-node-failover.sh",
      "source_contract":"python3 scripts/check-cockroachdb-node-failover.py",
      "semantics":[
        "three-node cluster and three voting replicas",
        "known range lease relocated to node one",
        "leaseholder network isolation with acknowledged-state equality",
        "quorum write during leaseholder isolation",
        "separate non-leaseholder node stop and write",
        "isolated and stopped nodes rejoin and observe committed writes",
      ],
      "accepted":False,
    }
    boundary=value.setdefault("claim_boundary",{})
    boundary["cockroachdb_node_failover_source_candidate"]=True
    boundary["ha_proven"]=False; boundary["production_ready"]=False
    write(path,json.dumps(value,indent=2,ensure_ascii=False))
    operations=root/"docs/OPERATIONS_AND_RELEASE.md"; text=read(operations)
    section=textwrap.dedent(
      '''\

      ## CockroachDB leaseholder and node failover candidate

      `scripts/ci-cockroachdb-node-failover.sh` starts three CockroachDB nodes and waits for three voting replicas. It relocates the authoritative entity range lease to node 1, isolates that leaseholder from the cluster, proves acknowledged state is unchanged and writes through the surviving quorum. It then reconnects node 1, stops a non-leaseholder node, writes again, restarts the node and requires it to observe both commits.

      Measured recovery intervals are evidence values, not approved RTOs. The lane does not prove regional or long-duration partitions, production topology, independent acceptance or production readiness.
      '''
    )
    if "## CockroachDB leaseholder and node failover candidate" not in text: text+=section
    write(operations,text)


def run(root:Path)->None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/check-cockroachdb-node-failover.py",checker_source())
    write(root/"scripts/ci-cockroachdb-node-failover.sh",harness_source())
    write(root/"tests/control_plane/test_cockroachdb_node_failover_contract.py",test_source())
    update_documents(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
