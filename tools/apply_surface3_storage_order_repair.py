#!/usr/bin/env python3
"""Apply the exact UTF-8 byte-order storage listing repair to Surface 3."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one target, found {count}")
    return text.replace(old, new, 1)


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def patch_storage_core(root: Path) -> None:
    path = root / "crates/trnm-storage-core/src/lib.rs"
    text = path.read_text(encoding="utf-8")
    text = replace_one(
        text,
        "use std::collections::{BTreeMap, BTreeSet};\n",
        "use std::cmp::Ordering;\nuse std::collections::{BTreeMap, BTreeSet};\n",
        "Ordering import",
    )
    old = '''#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct StorageObjectKey {
    collection: String,
    key: String,
    user_id: UserId,
}

impl StorageObjectKey {
'''
    new = '''#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageObjectKey {
    collection: String,
    key: String,
    user_id: UserId,
}

/// Canonical storage identity and listing order.
///
/// Rust string comparison is not left implicit here: collection and object-key
/// values are ordered lexicographically by their exact UTF-8 bytes, followed by
/// the 16-byte user identifier. PostgreSQL binds the same byte order through
/// the `C` collation, while ordinary CockroachDB STRING keys use UTF-8 order.
impl Ord for StorageObjectKey {
    fn cmp(&self, other: &Self) -> Ordering {
        self.collection
            .as_bytes()
            .cmp(other.collection.as_bytes())
            .then_with(|| self.key.as_bytes().cmp(other.key.as_bytes()))
            .then_with(|| self.user_id.cmp(&other.user_id))
    }
}

impl PartialOrd for StorageObjectKey {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl StorageObjectKey {
'''
    text = replace_one(text, old, new, "StorageObjectKey canonical ordering")
    anchor = '''    #[test]
    fn content_version_matches_pinned_nakama_md5_hex() {
'''
    test = '''    #[test]
    fn storage_object_key_order_is_explicit_utf8_bytes() {
        let mut keys = ["é", "a", "A", "!", "中", "_", "0", "~"]
            .into_iter()
            .map(|name| StorageObjectKey::new("profile", name, user(1)).unwrap())
            .collect::<Vec<_>>();
        keys.sort();
        assert_eq!(
            keys.iter().map(StorageObjectKey::key).collect::<Vec<_>>(),
            vec!["!", "0", "A", "_", "a", "~", "é", "中"]
        );

        let upper = StorageObjectKey::new("A", "same", user(1)).unwrap();
        let lower = StorageObjectKey::new("a", "same", user(1)).unwrap();
        assert!(upper < lower);
    }

'''
    text = replace_one(text, anchor, test + anchor, "storage ordering unit test")
    path.write_text(text, encoding="utf-8")


def patch_storage_adapter(root: Path) -> None:
    prelude = root / "crates/trnm-persistence-pg/src/storage_parts/00_prelude.rs"
    text = prelude.read_text(encoding="utf-8")
    text = replace_one(
        text,
        "    data_loss, decode_digest, decode_id16, error, invalid, map_postgres_error, to_i64, PgRepository,\n",
        "    data_loss, decode_digest, decode_id16, error, invalid, map_postgres_error, to_i64,\n    DatabaseProfile, PgRepository,\n",
        "DatabaseProfile import",
    )
    prelude.write_text(text, encoding="utf-8")

    helpers = root / "crates/trnm-persistence-pg/src/storage_parts/02_list_helpers.rs"
    text = helpers.read_text(encoding="utf-8")
    prefix = '''const POSTGRESQL_STORAGE_LIST_QUERY: &str = concat!(
    "SELECT object_key, user_id, value_bytes, version_digest, ",
    "read_permission, write_permission ",
    "FROM trnm_storage_objects ",
    "WHERE collection COLLATE \\"C\\" = ($1::text COLLATE \\"C\\") ",
    "AND ($2::bytea IS NULL OR user_id = $2) ",
    "AND (object_key COLLATE \\"C\\" > ($3::text COLLATE \\"C\\") ",
    "OR (object_key COLLATE \\"C\\" = ($3::text COLLATE \\"C\\") AND user_id > $4)) ",
    "AND ($5::bytea IS NULL OR read_permission = 2 ",
    "OR (user_id = $5 AND read_permission = 1)) ",
    "ORDER BY object_key COLLATE \\"C\\" ASC, user_id ASC ",
    "LIMIT $6"
);

const COCKROACHDB_STORAGE_LIST_QUERY: &str = concat!(
    "SELECT object_key, user_id, value_bytes, version_digest, ",
    "read_permission, write_permission ",
    "FROM trnm_storage_objects ",
    "WHERE collection = $1 ",
    "AND ($2::bytea IS NULL OR user_id = $2) ",
    "AND (object_key > $3 OR (object_key = $3 AND user_id > $4)) ",
    "AND ($5::bytea IS NULL OR read_permission = 2 ",
    "OR (user_id = $5 AND read_permission = 1)) ",
    "ORDER BY object_key ASC, user_id ASC ",
    "LIMIT $6"
);

fn storage_list_query(profile: DatabaseProfile) -> &'static str {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_STORAGE_LIST_QUERY,
        DatabaseProfile::CockroachDb => COCKROACHDB_STORAGE_LIST_QUERY,
    }
}

'''
    text = replace_one(text, "fn validate_list_request(\n", prefix + "fn validate_list_request(\n", "list query profile contract")
    helpers.write_text(text, encoding="utf-8")

    repository = root / "crates/trnm-persistence-pg/src/storage_parts/01_repository.rs"
    text = repository.read_text(encoding="utf-8")
    old_query = '''                "SELECT object_key, user_id, value_bytes, version_digest, \\
                        read_permission, write_permission \\
                 FROM trnm_storage_objects \\
                 WHERE collection = $1 \\
                   AND ($2::bytea IS NULL OR user_id = $2) \\
                   AND (object_key > $3 OR (object_key = $3 AND user_id > $4)) \\
                   AND ($5::bytea IS NULL OR read_permission = 2 \\
                        OR (user_id = $5 AND read_permission = 1)) \\
                 ORDER BY object_key ASC, user_id ASC \\
                 LIMIT $6",
'''
    text = replace_one(text, old_query, "                storage_list_query(self.profile),\n", "profile-specific list query")
    repository.write_text(text, encoding="utf-8")

    tests = root / "crates/trnm-persistence-pg/src/storage_parts/92_test_list.rs"
    text = tests.read_text(encoding="utf-8")
    addition = '''
    #[test]
    fn storage_list_queries_bind_profile_neutral_byte_order() {
        let postgresql = storage_list_query(DatabaseProfile::PostgreSql);
        assert!(postgresql.contains("collection COLLATE \\"C\\""));
        assert!(postgresql.contains("object_key COLLATE \\"C\\" >"));
        assert!(postgresql.contains("ORDER BY object_key COLLATE \\"C\\" ASC, user_id ASC"));

        let cockroach = storage_list_query(DatabaseProfile::CockroachDb);
        assert!(!cockroach.contains("COLLATE"));
        assert!(cockroach.contains("object_key > $3"));
        assert!(cockroach.contains("ORDER BY object_key ASC, user_id ASC"));
    }
'''
    tests.write_text(text.rstrip() + "\n" + addition, encoding="utf-8")


def patch_live_test(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/tests/authority_storage_parts/03_storage_list.rs"
    text = path.read_text(encoding="utf-8")
    addition = '''

#[test]
fn storage_listing_utf8_byte_order_is_profile_neutral() {
    let Some((database_url, profile)) = live_database_environment("storage UTF-8 order contract")
    else {
        return;
    };
    let owner = UserId::new([0xA1; 16]);
    let collection = "listing-utf8-byte-order-v1";
    let names = ["é", "a", "A", "!", "中", "_", "0", "~"];
    let keys = names
        .into_iter()
        .map(|name| StorageObjectKey::new(collection, name, owner).unwrap())
        .collect::<Vec<_>>();
    let writes = keys
        .iter()
        .enumerate()
        .map(|(index, key)| {
            StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: vec![u8::try_from(index + 1).unwrap()],
                expected: VersionCheck::MustNotExist,
                read_permission: ReadPermission::Public,
                write_permission: WritePermission::Owner,
            })
        })
        .collect::<Vec<_>>();

    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    repository
        .apply_storage_batch(StorageActor::Server, &writes, 60)
        .unwrap();

    let mut cursor = None;
    let mut actual = Vec::new();
    loop {
        let (page, next) = repository
            .list_storage_objects(
                StorageActor::Server,
                collection,
                Some(owner),
                cursor.as_ref(),
                3,
            )
            .unwrap();
        actual.extend(page.into_iter().map(|object| object.key.key().to_owned()));
        if next.is_none() {
            break;
        }
        cursor = next;
    }

    assert_eq!(actual, ["!", "0", "A", "_", "a", "~", "é", "中"]);

    let mut rust_sorted = keys;
    rust_sorted.sort();
    assert_eq!(
        actual,
        rust_sorted
            .iter()
            .map(|key| key.key().to_owned())
            .collect::<Vec<_>>()
    );
}
'''
    path.write_text(text.rstrip() + addition + "\n", encoding="utf-8")


def patch_migrations(root: Path) -> None:
    postgresql = root / "migrations/postgresql/0001_foundation_up.sql"
    text = postgresql.read_text(encoding="utf-8")
    text = replace_one(
        text,
        "    collection TEXT NOT NULL CHECK (length(collection) BETWEEN 1 AND 128),\n    object_key TEXT NOT NULL CHECK (length(object_key) BETWEEN 1 AND 128),\n",
        "    collection TEXT COLLATE \"C\" NOT NULL CHECK (octet_length(collection) BETWEEN 1 AND 128),\n    object_key TEXT COLLATE \"C\" NOT NULL CHECK (octet_length(object_key) BETWEEN 1 AND 128),\n",
        "PostgreSQL C-collated storage key columns",
    )
    postgresql.write_text(text, encoding="utf-8")

    cockroach = root / "migrations/cockroachdb/0001_foundation_up.sql"
    text = cockroach.read_text(encoding="utf-8")
    text = replace_one(
        text,
        "    collection STRING NOT NULL CHECK (length(collection) BETWEEN 1 AND 128),\n    object_key STRING NOT NULL CHECK (length(object_key) BETWEEN 1 AND 128),\n",
        "    collection STRING NOT NULL CHECK (octet_length(collection) BETWEEN 1 AND 128),\n    object_key STRING NOT NULL CHECK (octet_length(object_key) BETWEEN 1 AND 128),\n",
        "CockroachDB UTF-8 byte-length storage key columns",
    )
    cockroach.write_text(text, encoding="utf-8")

    lock_path = root / "migrations/MIGRATION_CHAIN.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for profile, migration in (
        ("postgresql", postgresql),
        ("cockroachdb", cockroach),
    ):
        rows = lock["profiles"][profile]["ordered_files"]
        if len(rows) != 1 or rows[0]["path"] != migration.relative_to(root).as_posix():
            raise RuntimeError(f"{profile}: unexpected migration lock topology")
        rows[0]["git_blob_sha1"] = git_blob_sha1(migration.read_bytes())
    lock["generated_from_base"] = "surface3-canonical-storage-order-decision"
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def write_contracts(root: Path) -> None:
    contract = {
        "schema": "trillionnium.storage-list-order.v1",
        "encoding": "UTF-8",
        "canonical_order": [
            "collection UTF-8 bytes ascending",
            "object_key UTF-8 bytes ascending",
            "user_id 16 bytes ascending",
        ],
        "rust": {
            "type": "StorageObjectKey",
            "implementation": "explicit Ord over as_bytes followed by UserId",
        },
        "postgresql": {
            "column_collation": "C",
            "primary_key_uses_column_collation": True,
            "range_predicate_collation": "C",
            "order_by_collation": "C",
            "length_unit": "octets",
        },
        "cockroachdb": {
            "column_type": "ordinary STRING",
            "ordering": "UTF-8",
            "primary_key_order": "ordinary STRING then BYTES user_id",
            "length_unit": "octets",
        },
        "vectors": ["!", "0", "A", "_", "a", "~", "é", "中"],
        "claim_boundary": {
            "public_wire_cursor": False,
            "official_sdk_compatibility": False,
            "accepted_evidence": False,
            "production_ready": False,
        },
    }
    path = root / "contracts/storage/storage-list-order-v1.json"
    path.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    decision = {
        "schema": "trillionnium.architecture-decision.v1",
        "id": "ADR-STORAGE-LIST-ORDER-001",
        "status": "source-candidate",
        "decision": "Storage collection and object key ordering is exact UTF-8 byte order, followed by user_id bytes.",
        "postgresql_binding": "TEXT COLLATE C columns, primary key, predicates and ORDER BY",
        "cockroachdb_binding": "ordinary STRING UTF-8 order and BYTES user_id",
        "rust_binding": "explicit StorageObjectKey Ord over UTF-8 bytes and UserId",
        "migration_strategy": "pre-production authoritative foundation migration updated and lock rebound; deployed in-place migration remains outside this source candidate",
        "rollback": "retain previous main authority until exact dual-profile execution and independent database review",
        "claim_boundary": {
            "migration_compatible": False,
            "independently_accepted": False,
            "production_ready": False,
        },
    }
    path = root / "docs/development/STORAGE_LIST_ORDER_DECISION.json"
    path.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    authority_path = root / "docs/development/SCHEMA_AUTHORITY.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    decisions = authority.setdefault("active_decisions", [])
    decision_path = "docs/development/STORAGE_LIST_ORDER_DECISION.json"
    if decision_path not in decisions:
        decisions.append(decision_path)
    semantics = authority["adapter_abi"]["required_semantics"]
    marker = "canonical UTF-8 byte ordering for storage collection and object keys"
    if marker not in semantics:
        semantics.append(marker)
    authority["adapter_abi"]["source_coverage_candidate"]["storage_list_order_profile_neutral"] = True
    authority_path.write_text(json.dumps(authority, indent=2) + "\n", encoding="utf-8")


def write_checker(root: Path) -> None:
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
class ValidationError(RuntimeError): pass
def require(value:bool,message:str)->None:
  if not value: raise ValidationError(message)
def validate(core:str,prelude:str,helpers:str,repository:str,live:str,postgres:str,cockroach:str,contract:dict)->None:
  for marker in ("impl Ord for StorageObjectKey","self.collection",".as_bytes().cmp(","self.user_id.cmp"):
    require(marker in core,f"Rust byte ordering missing {marker}")
  require("DatabaseProfile" in prelude,"database profile import missing")
  for marker in ('POSTGRESQL_STORAGE_LIST_QUERY','collection COLLATE \\"C\\"','object_key COLLATE \\"C\\" >','ORDER BY object_key COLLATE \\"C\\" ASC, user_id ASC','COCKROACHDB_STORAGE_LIST_QUERY','ORDER BY object_key ASC, user_id ASC'):
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
    path = root / "scripts/check-storage-list-order.py"
    path.write_text(checker, encoding="utf-8")

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
    for position,marker in ((0,"impl Ord for StorageObjectKey"),(2,'ORDER BY object_key COLLATE \\"C\\" ASC, user_id ASC'),(5,'collection TEXT COLLATE "C"'),(6,"octet_length(object_key)")):
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
    path = root / "tests/control_plane/test_storage_list_order_contract.py"
    path.write_text(test, encoding="utf-8")


def patch_readme(root: Path) -> None:
    path = root / "crates/trnm-persistence-pg/README.md"
    text = path.read_text(encoding="utf-8")
    section = '''## Canonical storage listing order

Storage collection and object-key identity is ordered by exact UTF-8 bytes, followed by the 16-byte user identifier. `StorageObjectKey` implements this comparator explicitly rather than inheriting an unstated string-order assumption. PostgreSQL declares both storage text columns with the `C` collation, so the primary-key index, equality/range predicates and explicit listing `ORDER BY` use byte order independently of the database's ambient locale. CockroachDB uses ordinary, non-collated `STRING` keys and its UTF-8 order; both schemas enforce the same 128-octet limits.

The profile-neutral live vector includes punctuation, digits, uppercase, lowercase, Latin non-ASCII and CJK keys. It concatenates multiple pages and requires both database profiles and Rust sorting to produce `!`, `0`, `A`, `_`, `a`, `~`, `é`, `中`. A profile-specific SQL selector and hostile source contract reject removal of the PostgreSQL `C` collation, the Rust byte comparator, the Cockroach ordinary-string path or the retained vector.

This is a source and fresh-schema decision. It does not define a public wire cursor, prove compatibility with all Nakama key inputs, migrate an existing production catalog, or grant database, SDK, evidence or production acceptance.

'''
    text = replace_one(text, "## Operations\n", section + "## Operations\n", "README canonical order section")
    path.write_text(text, encoding="utf-8")


def apply(root: Path) -> None:
    patch_storage_core(root)
    patch_storage_adapter(root)
    patch_live_test(root)
    patch_migrations(root)
    write_contracts(root)
    write_checker(root)
    patch_readme(root)


def main() -> int:
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    args=parser.parse_args()
    apply(args.root.resolve())
    return 0

if __name__=="__main__": raise SystemExit(main())
