#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.sdk.matrix import SDKMatrixError,canonical,generate,load_registry

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--registry',type=Path,required=True);p.add_argument('--sdk-root',type=Path,required=True);p.add_argument('--nakama-root',type=Path,required=True);p.add_argument('--common-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--require-sg1',action='store_true');a=p.parse_args()
 try:
  registry=load_registry(a.registry);value=generate(registry,a.sdk_root,a.nakama_root,a.common_root);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(canonical(value)+b'\n')
  if a.require_sg1:
   raise SDKMatrixError('SG1 remains open: SDK candidate is unclassified and release/transport/support profiles are unreviewed')
  print(json.dumps({'profiles':value['profile_count'],'api_targets':value['api_target_count'],'realtime_targets':value['realtime_target_count'],'leaves':value['leaf_count'],'sg1_eligible':False},sort_keys=True))
 except (SDKMatrixError,OSError,json.JSONDecodeError) as exc:
  print(f'SDK denominator generation failed: {exc}',file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
