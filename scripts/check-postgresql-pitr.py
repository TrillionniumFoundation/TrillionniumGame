#!/usr/bin/env python3
"""Validate PostgreSQL point-in-time recovery evidence source."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HARNESS=ROOT/"scripts/ci-postgresql-pitr.sh"
REQUIRED=(
  "archive_mode=on",
  "archive_command=",
  "archive_timeout=1s",
  "pg_basebackup",
  "pg_current_wal_flush_lsn",
  "pg_switch_wal",
  "recovery.signal",
  "restore_command",
  "recovery_target_lsn",
  "recovery_target_action = 'promote'",
  "target-snapshot.txt",
  "restored-snapshot.txt",
  "cmp --silent",
  "pitr-target",
  "pitr-after-target",
  "measured_recovery_ms",
  '"target_commit_present": True',
  '"after_target_commit_absent": True',
  '"approved_rpo_rto": False',
  '"accepted_evidence": False',
  '"production_ready": False',
)
class ValidationError(RuntimeError): pass
def require(value:bool,message:str)->None:
  if not value: raise ValidationError(message)
def validate_text(text:str)->None:
  for marker in REQUIRED: require(marker in text,f"PITR harness missing {marker}")
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
  require("rm -rf /var/lib/postgresql/data/*" not in text,"live primary data deletion introduced")
def main()->int:
  try: validate_text(HARNESS.read_text(encoding="utf-8"))
  except (OSError,ValidationError) as error:
    print(f"PostgreSQL PITR contract failed: {error}",file=sys.stderr); return 1
  print("PostgreSQL PITR source contract: OK"); return 0
if __name__=="__main__": raise SystemExit(main())
