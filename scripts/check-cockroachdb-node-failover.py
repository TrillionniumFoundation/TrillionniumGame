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
  authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
  authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))
  quarantines = authority.get("non_authoritative")
  require(isinstance(quarantines, list) and quarantines, "schema quarantine registry missing")
  for quarantine in quarantines:
      require(isinstance(quarantine, dict), "schema quarantine row invalid")
      quarantine_path = quarantine.get("path")
      require(isinstance(quarantine_path, str) and quarantine_path, "schema quarantine path invalid")
      require(quarantine_path not in text, "non-authoritative schema referenced")
  require("DROP TABLE" not in text.upper(),"destructive schema rollback introduced")
  require("|| true" not in text,"failure suppression introduced")
  require("single-node" not in text,"single-node execution cannot claim node failover")
def main()->int:
  try: validate_text(HARNESS.read_text(encoding="utf-8"))
  except (OSError,ValidationError) as error:
    print(f"CockroachDB node failover contract failed: {error}",file=sys.stderr); return 1
  print("CockroachDB node failover source contract: OK"); return 0
if __name__=="__main__": raise SystemExit(main())
