#!/usr/bin/env python3
"""Capture complete schema source annex only after actual local Git custody checks.

This producer checks Git/source identity. It never migrates, connects to a DB,
claims native catalog readiness or turns StorageV4 into account transfer.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
import schema_evidence_binding as BINDING

ROOT=Path(__file__).resolve().parents[1]

def verified_head_binding(source_root, profile, commit, tree, *, target=BINDING.SOURCE.SchemaTarget.StorageV4):
    token=BINDING.verify_binding(source_root,profile=profile,target=target)
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=source_root,text=True,timeout=30).strip()
    BINDING.require(git('rev-parse','HEAD')==commit and git('rev-parse','HEAD^{tree}')==tree,'producer_actual_HEAD_tree_mismatch')
    for row in BINDING.binding_document(token)['full_source_inventory']:
        BINDING.require(git('rev-parse','HEAD:'+row['path'])==row['git_blob_sha1'],'producer_full_source_not_exact_HEAD')
    return token

def capture(root, profile, commit, tree, *, target=BINDING.SOURCE.SchemaTarget.StorageV4):
    token=verified_head_binding(ROOT,profile,commit,tree,target=target)
    BINDING.write_annex(token,ROOT,root,commit=commit,tree=tree)
    return BINDING.identity_fields(token)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--profile',choices=BINDING.PROFILES,required=True)
    p.add_argument('--commit',required=True);p.add_argument('--tree',required=True)
    p.add_argument('--target',choices=[v.value for v in BINDING.SOURCE.SchemaTarget],default='StorageV4')
    a=p.parse_args()
    try:
        result=capture(a.root,a.profile,a.commit,a.tree,target=BINDING.SOURCE.SchemaTarget(a.target))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print('schema source capture rejected: '+str(e),file=sys.stderr);return 1
    print(json.dumps(result,sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
