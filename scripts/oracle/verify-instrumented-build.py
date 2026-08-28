#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tools.oracle.build_provenance import ProvenanceError,canonical,load_object,validate_manifest

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--policy',type=Path,required=True);p.add_argument('--output',type=Path);p.add_argument('--require-approved',action='store_true');a=p.parse_args()
 try:
  result=validate_manifest(load_object(a.manifest),load_object(a.policy),require_approved=a.require_approved)
  if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(canonical(result)+b'\n')
  print(json.dumps(result,sort_keys=True))
 except (ProvenanceError,OSError,json.JSONDecodeError) as exc:
  print(f'instrumented build verification failed: {exc}',file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
