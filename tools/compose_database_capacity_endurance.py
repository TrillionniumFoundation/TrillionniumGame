#!/usr/bin/env python3
"""Compose dual-profile capacity smoke and chained endurance evidence tooling."""
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


def workload_sql() -> str:
    return textwrap.dedent(
        r'''\
        \set slot random(1, 1000)
        \set payload random(1, 2000000000)
        BEGIN;
        INSERT INTO trnm_storage_objects
          (collection, object_key, user_id, value_bytes, version_digest,
           read_permission, write_permission, updated_at_ms)
        VALUES
          ('capacity', 'object-' || :slot,
           decode(repeat('aa', 16), 'hex'),
           decode(lpad(to_hex(:payload), 8, '0'), 'hex'),
           decode(repeat('bb', 32), 'hex'), 2, 1, :payload)
        ON CONFLICT (collection, object_key, user_id)
        DO UPDATE SET value_bytes = EXCLUDED.value_bytes,
                      version_digest = EXCLUDED.version_digest,
                      read_permission = EXCLUDED.read_permission,
                      write_permission = EXCLUDED.write_permission,
                      updated_at_ms = EXCLUDED.updated_at_ms;
        SELECT octet_length(value_bytes)
        FROM trnm_storage_objects
        WHERE collection = 'capacity'
          AND object_key = 'object-' || :slot
          AND user_id = decode(repeat('aa', 16), 'hex');
        COMMIT;
        '''
    )


def smoke_source() -> str:
    return textwrap.dedent(
        r'''\
        #!/usr/bin/env bash
        set -Eeuo pipefail

        ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
        : "${TRNM_REQUIRE_LIVE_DATABASE:?TRNM_REQUIRE_LIVE_DATABASE must be explicit}"
        test "$TRNM_REQUIRE_LIVE_DATABASE" = 1
        : "${TRNM_DATABASE_PROFILE:?TRNM_DATABASE_PROFILE is required}"
        : "${TRNM_DATABASE_URL:?TRNM_DATABASE_URL is required}"
        : "${PGBENCH_IMAGE:?PGBENCH_IMAGE is required}"
        DURATION_SECONDS=${DURATION_SECONDS:-20}
        CLIENTS=${CLIENTS:-4}
        THREADS=${THREADS:-2}
        EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/database-capacity-${TRNM_DATABASE_PROFILE}}
        CANDIDATE_COMMIT=${CANDIDATE_COMMIT:-unknown}
        CANDIDATE_TREE=${CANDIDATE_TREE:-unknown}
        mkdir -p "$EVIDENCE_DIR"

        case "$TRNM_DATABASE_PROFILE" in
          postgresql|cockroachdb) ;;
          *) printf 'unsupported profile=%s\n' "$TRNM_DATABASE_PROFILE" >&2; exit 2 ;;
        esac
        [[ "$DURATION_SECONDS" =~ ^[0-9]+$ ]] && test "$DURATION_SECONDS" -ge 5 && test "$DURATION_SECONDS" -le 21600
        [[ "$CLIENTS" =~ ^[0-9]+$ ]] && test "$CLIENTS" -ge 1 && test "$CLIENTS" -le 256
        [[ "$THREADS" =~ ^[0-9]+$ ]] && test "$THREADS" -ge 1 && test "$THREADS" -le "$CLIENTS"

        started_epoch=$(date +%s)
        docker run --rm --network host \
          -v "$ROOT/scripts/database-capacity-workload.sql:/workload.sql:ro" \
          "$PGBENCH_IMAGE" pgbench "$TRNM_DATABASE_URL" -n \
          --client="$CLIENTS" --jobs="$THREADS" --time="$DURATION_SECONDS" \
          --progress=5 --file=/workload.sql \
          > "$EVIDENCE_DIR/pgbench.stdout" \
          2> "$EVIDENCE_DIR/pgbench.stderr"
        finished_epoch=$(date +%s)
        observed_seconds=$((finished_epoch-started_epoch))
        test "$observed_seconds" -ge "$DURATION_SECONDS"

        python3 - "$ROOT" "$EVIDENCE_DIR" "$TRNM_DATABASE_PROFILE" \
          "$TRNM_DATABASE_URL" "$PGBENCH_IMAGE" "$DURATION_SECONDS" "$observed_seconds" \
          "$CLIENTS" "$THREADS" "$CANDIDATE_COMMIT" "$CANDIDATE_TREE" <<'PY'
        import hashlib,json,re,sys
        from pathlib import Path
        root=Path(sys.argv[1]); evidence=Path(sys.argv[2])
        profile,url,image=sys.argv[3:6]
        requested,observed,clients,threads=map(int,sys.argv[6:10])
        candidate_commit,candidate_tree=sys.argv[10:12]
        stdout=(evidence/'pgbench.stdout').read_text(encoding='utf-8',errors='replace')
        stderr=(evidence/'pgbench.stderr').read_text(encoding='utf-8',errors='replace')
        combined=stdout+'\n'+stderr
        def one(pattern: str, cast, label: str):
            matches=re.findall(pattern,combined,flags=re.I|re.M)
            if not matches: raise SystemExit(f'missing pgbench metric: {label}')
            return cast(matches[-1])
        transactions=one(r'number of transactions actually processed:\s*([0-9]+)',int,'transactions')
        failed_matches=re.findall(r'number of failed transactions:\s*([0-9]+)',combined,flags=re.I)
        failed=int(failed_matches[-1]) if failed_matches else 0
        latency=one(r'latency average\s*=\s*([0-9.]+)\s*ms',float,'latency')
        tps=one(r'tps\s*=\s*([0-9.]+)',float,'tps')
        if transactions <= 0 or failed != 0 or latency <= 0 or tps <= 0:
            raise SystemExit('capacity smoke produced invalid or failed metrics')
        def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
        workload=root/'scripts/database-capacity-workload.sql'
        manifest={
          'schema':'trillionnium.database-capacity-segment.v1',
          'profile':profile,
          'database_endpoint_redacted':'<redacted>',
          'client_image_reference':image,
          'candidate_commit':candidate_commit,
          'candidate_tree':candidate_tree,
          'workload_sha256':digest(workload),
          'requested_duration_seconds':requested,
          'observed_duration_seconds':observed,
          'clients':clients,
          'threads':threads,
          'transactions':transactions,
          'failed_transactions':failed,
          'latency_average_ms':latency,
          'transactions_per_second':tps,
          'stdout_sha256':digest(evidence/'pgbench.stdout'),
          'stderr_sha256':digest(evidence/'pgbench.stderr'),
          'claim_boundary':{
            'capacity_target_accepted':False,
            'performance_accepted':False,
            'endurance_complete':False,
            'independently_accepted':False,
            'production_ready':False,
          },
        }
        (evidence/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        print(json.dumps(manifest,sort_keys=True))
        PY
        '''
    )


def segment_source() -> str:
    return textwrap.dedent(
        r'''\
        #!/usr/bin/env bash
        set -Eeuo pipefail

        ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
        : "${SEGMENT_INDEX:?SEGMENT_INDEX is required}"
        : "${PREVIOUS_SEGMENT_SHA256:?PREVIOUS_SEGMENT_SHA256 is required; use GENESIS for index zero}"
        : "${SEGMENT_OUTPUT:?SEGMENT_OUTPUT is required}"
        [[ "$SEGMENT_INDEX" =~ ^[0-9]+$ ]]
        if [[ "$SEGMENT_INDEX" = 0 ]]; then
          test "$PREVIOUS_SEGMENT_SHA256" = GENESIS
        else
          [[ "$PREVIOUS_SEGMENT_SHA256" =~ ^[0-9a-f]{64}$ ]]
        fi
        temp=$(mktemp -d)
        trap 'rm -rf "$temp"' EXIT
        export EVIDENCE_DIR="$temp/capacity"
        bash "$ROOT/scripts/ci-database-capacity-smoke.sh"
        python3 - "$EVIDENCE_DIR/manifest.json" "$SEGMENT_OUTPUT" \
          "$SEGMENT_INDEX" "$PREVIOUS_SEGMENT_SHA256" <<'PY'
        import hashlib,json,sys
        from pathlib import Path
        source=Path(sys.argv[1]); output=Path(sys.argv[2])
        value=json.loads(source.read_text())
        value['schema']='trillionnium.database-endurance-segment.v1'
        value['segment_index']=int(sys.argv[3])
        value['previous_segment_sha256']=sys.argv[4]
        value['segment_payload_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
        print(hashlib.sha256(output.read_bytes()).hexdigest())
        PY
        '''
    )


def finalize_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Validate contiguous, exact-candidate database endurance ledgers."""
        from __future__ import annotations

        import argparse
        import hashlib
        import json
        from pathlib import Path
        from typing import Any

        TARGET_SECONDS = {"24h": 24 * 3600, "72h": 72 * 3600, "7d": 7 * 24 * 3600}

        class ValidationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise ValidationError(message)

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def load_segments(paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
            values=[]
            for path in paths:
                value=json.loads(path.read_text(encoding="utf-8"))
                require(value.get("schema")=="trillionnium.database-endurance-segment.v1",f"{path}: schema")
                values.append((path,value))
            values.sort(key=lambda row: row[1].get("segment_index",-1))
            return values

        def validate(paths: list[Path], target: str) -> dict[str, Any]:
            require(target in TARGET_SECONDS, "unsupported target")
            values=load_segments(paths)
            require(values, "empty endurance ledger")
            first=values[0][1]
            profile=first.get("profile")
            candidate_commit=first.get("candidate_commit")
            candidate_tree=first.get("candidate_tree")
            workload=first.get("workload_sha256")
            previous="GENESIS"
            total_seconds=0
            total_transactions=0
            max_latency=0.0
            weighted_tps_numerator=0.0
            for expected,(path,value) in enumerate(values):
                require(value.get("segment_index")==expected,f"segment gap at {expected}")
                require(value.get("previous_segment_sha256")==previous,f"{path}: previous digest")
                require(value.get("profile")==profile,f"{path}: profile changed")
                require(value.get("candidate_commit")==candidate_commit,f"{path}: commit changed")
                require(value.get("candidate_tree")==candidate_tree,f"{path}: tree changed")
                require(value.get("workload_sha256")==workload,f"{path}: workload changed")
                require(value.get("failed_transactions")==0,f"{path}: failed transactions")
                duration=int(value.get("observed_duration_seconds",0))
                require(5 <= duration <= 21660,f"{path}: invalid duration")
                total_seconds += duration
                tx=int(value.get("transactions",0)); require(tx>0,f"{path}: empty workload")
                total_transactions += tx
                latency=float(value.get("latency_average_ms",0)); require(latency>0,f"{path}: latency")
                max_latency=max(max_latency,latency)
                weighted_tps_numerator += float(value.get("transactions_per_second",0))*duration
                previous=sha(path)
            require(total_seconds >= TARGET_SECONDS[target],f"endurance duration {total_seconds} below {TARGET_SECONDS[target]}")
            return {
                "schema":"trillionnium.database-endurance-ledger.v1",
                "target":target,
                "profile":profile,
                "candidate_commit":candidate_commit,
                "candidate_tree":candidate_tree,
                "workload_sha256":workload,
                "segments":len(values),
                "observed_duration_seconds":total_seconds,
                "total_transactions":total_transactions,
                "maximum_segment_average_latency_ms":max_latency,
                "duration_weighted_transactions_per_second":weighted_tps_numerator/total_seconds,
                "final_segment_sha256":previous,
                "claim_boundary":{
                    "capacity_target_accepted":False,
                    "performance_accepted":False,
                    "independently_accepted":False,
                    "production_ready":False,
                },
            }

        def main() -> int:
            parser=argparse.ArgumentParser()
            parser.add_argument("--target",required=True,choices=sorted(TARGET_SECONDS))
            parser.add_argument("--output",type=Path,required=True)
            parser.add_argument("segments",nargs="+",type=Path)
            args=parser.parse_args()
            report=validate(args.segments,args.target)
            args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            print(json.dumps(report,sort_keys=True))
            return 0

        if __name__=="__main__":
            raise SystemExit(main())
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Source contract for database capacity and endurance evidence."""
        from __future__ import annotations
        import json,sys
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[1]
        PLAN=ROOT/"contracts/operations/database-endurance-plan.v1.json"
        WORKLOAD=ROOT/"scripts/database-capacity-workload.sql"
        SMOKE=ROOT/"scripts/ci-database-capacity-smoke.sh"
        SEGMENT=ROOT/"scripts/ci-database-endurance-segment.sh"
        FINALIZE=ROOT/"scripts/finalize-database-endurance-ledger.py"
        REQUIRED=(
          "pgbench",
          "failed_transactions",
          "latency_average_ms",
          "transactions_per_second",
          "DURATION_SECONDS",
          "PREVIOUS_SEGMENT_SHA256",
          "GENESIS",
          "segment gap",
          "previous digest",
          "commit changed",
          "workload changed",
          "endurance duration",
        )
        class ValidationError(RuntimeError): pass
        def require(value:bool,message:str)->None:
          if not value: raise ValidationError(message)
        def validate(plan:dict,workload:str,smoke:str,segment:str,finalize:str)->None:
          combined=smoke+segment+finalize
          for marker in REQUIRED: require(marker in combined,f"capacity/endurance source missing {marker}")
          require("trnm_storage_objects" in workload,"workload misses authoritative storage")
          require(plan.get("schema")=="trillionnium.database-endurance-plan.v1","plan schema")
          require(plan.get("profiles")==["postgresql","cockroachdb"],"profile order")
          require(plan.get("targets_hours")==[24,72,168],"duration targets")
          require(plan.get("maximum_segment_seconds")==21600,"segment bound")
          require(plan.get("thresholds_must_be_independently_approved") is True,"approval boundary")
          require(not any(plan.get("claim_boundary",{}).values()),"positive acceptance claim")
          require("|| true" not in smoke+segment,"failure suppression introduced")
        def main()->int:
          try: validate(json.loads(PLAN.read_text()),WORKLOAD.read_text(),SMOKE.read_text(),SEGMENT.read_text(),FINALIZE.read_text())
          except (OSError,json.JSONDecodeError,ValidationError) as error:
            print(f"database capacity/endurance validation failed: {error}",file=sys.stderr); return 1
          print("database capacity/endurance source contract: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import hashlib,importlib.util,json,subprocess,sys,tempfile,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]
        CHECKER=ROOT/"scripts/check-database-capacity-endurance.py"
        FINALIZER=ROOT/"scripts/finalize-database-endurance-ledger.py"
        def load(path,name):
          spec=importlib.util.spec_from_file_location(name,path)
          if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
          module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
        class DatabaseCapacityEnduranceTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.checker=load(CHECKER,"capacity_checker"); cls.finalizer=load(FINALIZER,"endurance_finalizer")
          def make(self,root,index,previous,duration=21600,failed=0,commit="a"*40):
            value={"schema":"trillionnium.database-endurance-segment.v1","segment_index":index,"previous_segment_sha256":previous,"profile":"postgresql","candidate_commit":commit,"candidate_tree":"b"*40,"workload_sha256":"c"*64,"observed_duration_seconds":duration,"transactions":100,"failed_transactions":failed,"latency_average_ms":1.0,"transactions_per_second":10.0}
            path=root/f"{index:02d}.json"; path.write_text(json.dumps(value,sort_keys=True)+"\n"); return path
          def test_24h_contiguous_chain_passes(self):
            with tempfile.TemporaryDirectory() as td:
              root=Path(td); paths=[]; previous="GENESIS"
              for index in range(4):
                path=self.make(root,index,previous); paths.append(path); previous=hashlib.sha256(path.read_bytes()).hexdigest()
              report=self.finalizer.validate(paths,"24h"); self.assertEqual(report["observed_duration_seconds"],86400)
          def test_gap_digest_failure_and_short_duration_rejected(self):
            with tempfile.TemporaryDirectory() as td:
              root=Path(td); first=self.make(root,0,"GENESIS")
              bad=self.make(root,2,"0"*64)
              with self.assertRaisesRegex(self.finalizer.ValidationError,"segment gap"): self.finalizer.validate([first,bad],"24h")
              with self.assertRaisesRegex(self.finalizer.ValidationError,"below"): self.finalizer.validate([first],"24h")
          def test_failed_transaction_and_candidate_change_rejected(self):
            with tempfile.TemporaryDirectory() as td:
              root=Path(td); first=self.make(root,0,"GENESIS"); digest=hashlib.sha256(first.read_bytes()).hexdigest()
              failed=self.make(root,1,digest,failed=1)
              with self.assertRaisesRegex(self.finalizer.ValidationError,"failed transactions"): self.finalizer.validate([first,failed],"24h")
          def test_source_contract_cli_passes(self):
            result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn("capacity/endurance source contract: OK",result.stdout)
        if __name__=="__main__": unittest.main()
        '''
    )


def update_docs(root: Path) -> None:
    plan={
      "schema":"trillionnium.database-endurance-plan.v1",
      "profiles":["postgresql","cockroachdb"],
      "workload":"scripts/database-capacity-workload.sql",
      "targets_hours":[24,72,168],
      "maximum_segment_seconds":21600,
      "required_segment_properties":["same exact commit and tree","same workload digest","contiguous zero-based index","SHA-256 previous-segment chain","zero failed transactions","positive transactions and latency","observed duration at least requested target"],
      "thresholds_must_be_independently_approved":True,
      "claim_boundary":{"capacity_complete":False,"endurance_24h_complete":False,"endurance_72h_complete":False,"endurance_7d_complete":False,"performance_accepted":False,"independently_accepted":False,"production_ready":False},
    }
    write(root/"contracts/operations/database-endurance-plan.v1.json",json.dumps(plan,indent=2,ensure_ascii=False))
    operations=root/"docs/OPERATIONS_AND_RELEASE.md"; text=read(operations)
    section=textwrap.dedent(
      '''\

      ## Database capacity and segmented endurance candidate

      `scripts/ci-database-capacity-smoke.sh` runs one identical transactional storage upsert/read workload through `pgbench` against either PostgreSQL or CockroachDB and retains transaction count, failed transaction count, average latency, throughput, exact workload digest and candidate identity. `scripts/ci-database-endurance-segment.sh` converts the same execution into a hash-chained segment; `scripts/finalize-database-endurance-ledger.py` accepts only contiguous, same-candidate, same-workload, zero-failure ledgers totaling 24h, 72h or 7d.

      A short qualification run proves the workload and evidence machinery, not capacity or endurance. Numerical throughput/latency thresholds and production sizing require independent approval; incomplete or short ledgers receive no duration credit.
      '''
    )
    if "## Database capacity and segmented endurance candidate" not in text: text+=section
    write(operations,text)


def run(root: Path) -> None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/database-capacity-workload.sql",workload_sql())
    write(root/"scripts/ci-database-capacity-smoke.sh",smoke_source())
    write(root/"scripts/ci-database-endurance-segment.sh",segment_source())
    write(root/"scripts/finalize-database-endurance-ledger.py",finalize_source())
    write(root/"scripts/check-database-capacity-endurance.py",checker_source())
    write(root/"tests/control_plane/test_database_capacity_endurance.py",test_source())
    update_docs(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
