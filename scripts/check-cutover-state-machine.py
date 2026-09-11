#!/usr/bin/env python3
"""Source contract for the cutover and retirement state machine."""
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MACHINE=ROOT/"scripts/cutover-state-machine.py"
DERIVE=ROOT/"scripts/derive-cutover-blocker-packet.py"
CONTRACT=ROOT/"contracts/operations/cutover-state-machine.v1.json"
REQUIRED=("planning","shadow","exclusive-canary","production","retirement-pending","retired","illegal or skipped transition","open P0/P1 blockers remain","required evidence not accepted","independent review incomplete","governance readback incomplete","candidate author cannot approve transition","evidence producer cannot approve own transition","accepted rollback packet required","ordinary protected admission required","24h endurance not accepted","72h endurance not accepted","7d endurance not accepted","RPO/RTO not approved","complete Nakama compatibility not accepted","global SG1 not accepted","explicit retirement decision required","retirement reviewer must be distinct")
class ValidationError(RuntimeError): pass
def require(value:bool,message:str)->None:
  if not value: raise ValidationError(message)
def validate(machine:str,derive:str,contract:dict)->None:
  for marker in REQUIRED: require(marker in machine,f"cutover machine missing {marker}")
  require("open_p0_p1_count" in derive,"blocker derivation missing")
  require(contract.get("schema")=="trillionnium.cutover-state-machine.v1","contract schema")
  require(contract.get("states")==["planning","shadow","exclusive-canary","production","retirement-pending","retired"],"state order")
  require(contract.get("skip_transitions_allowed") is False,"skip boundary")
  require(contract.get("self_approval_allowed") is False,"self approval boundary")
  require(not any(contract.get("claim_boundary",{}).values()),"positive cutover claim")
def main()->int:
  try: validate(MACHINE.read_text(),DERIVE.read_text(),json.loads(CONTRACT.read_text()))
  except (OSError,json.JSONDecodeError,ValidationError) as error:
    print(f"cutover state-machine validation failed: {error}",file=sys.stderr); return 1
  print("cutover state-machine source contract: OK"); return 0
if __name__=="__main__": raise SystemExit(main())
