#!/usr/bin/env python3
"""Run and normalize the canonical storage-order repair generator."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType


def load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("surface3_storage_order_repair", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("repair module loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_rust_quotes(root: Path) -> None:
    for relative in (
        "crates/trnm-persistence-pg/src/storage_parts/02_list_helpers.rs",
        "crates/trnm-persistence-pg/src/storage_parts/92_test_list.rs",
    ):
        path = root / relative
        text = path.read_text(encoding="utf-8")
        text = text.replace(r'\\"C\\"', r'\"C\"')
        path.write_text(text, encoding="utf-8")


def overwrite_checker(root: Path) -> None:
    checker = '''#!/usr/bin/env python3
"""Validate canonical UTF-8 byte ordering across Rust and both SQL profiles."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CORE=ROOT/"crates/trnm-storage-core/src/lib.rs"
PRELUDE=ROOT/"crates/trnm-persistence-pg/src/storage_parts/00_prelude.rs"
HELPERS=ROOT/"crates/trnm-persistence-pg/src/storage_parts/02_list_helpers.rs"
REPOSITORY=ROOT/"crates/trnm-persistence-pg/src/storage_parts/01_repository.rs"
LIVE=ROOT/"crates/trnm-persistence-pg/tests/authority_storage_parts/03_storage_list.rs"
POSTGRES=ROOT/"migrations/postgresql/0001_foundation_up.sql"
COCKROACH=ROOT/"migrations/cockroachdb/0001_foundation_up.sql"
CONTRACT=ROOT/"contracts/storage/storage-list-order-v1.json"
ESCAPED_QUOTE=chr(92)+'"'
class ValidationError(RuntimeError): pass
def require(value:bool,message:str)->None:
  if not value: raise ValidationError(message)
def validate(core:str,prelude:str,helpers:str,repository:str,live:str,postgres:str,cockroach:str,contract:dict)->None:
  for marker in ("impl Ord for StorageObjectKey","self.collection",".as_bytes().cmp(","self.user_id.cmp"):
    require(marker in core,f"Rust byte ordering missing {marker}")
  require("DatabaseProfile" in prelude,"database profile import missing")
  c=f"{ESCAPED_QUOTE}C{ESCAPED_QUOTE}"
  for marker in ("POSTGRESQL_STORAGE_LIST_QUERY",f"collection COLLATE {c}",f"object_key COLLATE {c} >",f"ORDER BY object_key COLLATE {c} ASC, user_id ASC","COCKROACHDB_STORAGE_LIST_QUERY","ORDER BY object_key ASC, user_id ASC"):
    require(marker in helpers,f"profile query marker missing {marker}")
  require("storage_list_query(self.profile)" in repository,"repository does not select profile query")
  require("storage_listing_utf8_byte_order_is_profile_neutral" in live,"dual-profile UTF-8 order test missing")
  require('collection TEXT COLLATE "C"' in postgres and 'object_key TEXT COLLATE "C"' in postgres,"PostgreSQL schema collation missing")
  require("octet_length(collection)" in postgres and "octet_length(object_key)" in postgres,"PostgreSQL byte limits missing")
  require("collection STRING" in cockroach and "object_key STRING" in cockroach,"Cockroach ordinary STRING schema missing")
  require("octet_length(collection)" in cockroach and "octet_length(object_key)" in cockroach,"Cockroach byte limits missing")
  require(contract.get("schema")=="trillionnium.storage-list-order.v1","contract schema")
  require(contract.get("encoding")=="UTF-8","contract encoding")
  require(contract.get("vectors")==["!","0","A","_","a","~","é","中"],"canonical vectors")
  claims=contract.get("claim_boundary",{})
  require(claims and not any(claims.values()),"positive compatibility or production claim")
def main()->int:
  try:
    validate(CORE.read_text(),PRELUDE.read_text(),HELPERS.read_text(),REPOSITORY.read_text(),LIVE.read_text(),POSTGRES.read_text(),COCKROACH.read_text(),json.loads(CONTRACT.read_text()))
  except (OSError,json.JSONDecodeError,ValidationError) as error:
    print(f"storage list order validation failed: {error}",file=sys.stderr); return 1
  print("storage list UTF-8 byte order: OK"); return 0
if __name__=="__main__": raise SystemExit(main())
'''
    (root / "scripts/check-storage-list-order.py").write_text(checker, encoding="utf-8")

    test = '''from __future__ import annotations
import importlib.util,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CHECKER=ROOT/"scripts/check-storage-list-order.py"
def load_checker():
  spec=importlib.util.spec_from_file_location("storage_list_order",CHECKER)
  if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
  module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
class StorageListOrderContractTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.c=load_checker()
    cls.values=(cls.c.CORE.read_text(),cls.c.PRELUDE.read_text(),cls.c.HELPERS.read_text(),cls.c.REPOSITORY.read_text(),cls.c.LIVE.read_text(),cls.c.POSTGRES.read_text(),cls.c.COCKROACH.read_text(),json.loads(cls.c.CONTRACT.read_text()))
  def test_real_contract_passes(self): self.c.validate(*self.values)
  def test_missing_rust_or_sql_binding_fails(self):
    for position,marker in ((0,"impl Ord for StorageObjectKey"),(2,"POSTGRESQL_STORAGE_LIST_QUERY"),(2,"COLLATE"),(5,'collection TEXT COLLATE "C"'),(6,"octet_length(object_key)")):
      values=list(self.values); values[position]=values[position].replace(marker,"removed")
      with self.subTest(position=position,marker=marker):
        with self.assertRaises(self.c.ValidationError): self.c.validate(*values)
  def test_changed_vector_or_positive_claim_fails(self):
    values=list(self.values); contract=json.loads(json.dumps(values[7])); contract["vectors"].reverse(); values[7]=contract
    with self.assertRaises(self.c.ValidationError): self.c.validate(*values)
    values=list(self.values); contract=json.loads(json.dumps(values[7])); contract["claim_boundary"]["production_ready"]=True; values[7]=contract
    with self.assertRaises(self.c.ValidationError): self.c.validate(*values)
  def test_command_line_checker_passes(self):
    result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    self.assertEqual(result.returncode,0,result.stderr); self.assertIn("storage list UTF-8 byte order: OK",result.stdout)
if __name__=="__main__": unittest.main()
'''
    (root / "tests/control_plane/test_storage_list_order_contract.py").write_text(test, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repair", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    module = load_module(args.repair.resolve())
    module.apply(root)
    normalize_rust_quotes(root)
    overwrite_checker(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
