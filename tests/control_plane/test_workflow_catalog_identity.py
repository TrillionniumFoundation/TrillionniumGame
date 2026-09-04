from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "catalog_identity_contract_tests", ROOT / "scripts/workflow_catalog_identity.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load catalog identity verifier")
CATALOG = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CATALOG
SPEC.loader.exec_module(CATALOG)
HEAD = "a" * 40


def blob(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


class CatalogIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / ".github/workflows/proof.yml"
        self.path.parent.mkdir(parents=True)
        self.payload = b"name: proof\non:\n  pull_request:\njobs: {}\n"
        self.path.write_bytes(self.payload)
        self.requirement = SimpleNamespace(
            workflow_id=10, name="proof", path=".github/workflows/proof.yml",
            git_blob_sha1=blob(self.payload), allowed_events=("pull_request",),
        )
        self.catalog = {
            "id": 10, "name": self.requirement.path,
            "path": self.requirement.path, "state": "active",
        }
        self.run = SimpleNamespace(
            id=20, attempt=1, workflow_id=10, name="proof",
            path=self.requirement.path, head_sha=HEAD, event="pull_request",
            status="completed", conclusion="success",
        )

    def verify(self, **overrides):
        values = {"source_root": self.root, "run": self.run, "expected_head": HEAD}
        values.update(overrides)
        return CATALOG.verified_catalog_path_alias(self.requirement, self.catalog, **values)

    def write_and_repin(self, payload):
        self.path.write_bytes(payload)
        self.requirement.git_blob_sha1 = blob(payload)

    def test_exact_path_alias_requires_real_source_and_run_identity(self):
        self.assertTrue(self.verify())

    def test_canonical_catalog_name_is_not_an_alias(self):
        self.catalog["name"] = "proof"
        self.assertFalse(self.verify())

    def test_no_context_is_never_implicit_acceptance(self):
        for field in ("source_root", "run", "expected_head"):
            with self.subTest(field=field):
                self.assertFalse(self.verify(**{field: None}))

    def test_missing_source_fails(self):
        self.path.unlink()
        self.assertFalse(self.verify())

    def test_changed_source_bytes_fail(self):
        self.path.write_bytes(self.payload + b"# drift\n")
        self.assertFalse(self.verify())

    def test_repinning_wrong_declared_name_still_fails(self):
        self.write_and_repin(b"name: renamed\non:\n  pull_request:\njobs: {}\n")
        self.assertFalse(self.verify())

    def test_duplicate_name_fails_even_with_matching_blob(self):
        self.write_and_repin(self.payload + b"name: proof\n")
        self.assertFalse(self.verify())

    def test_ambiguous_or_complex_name_fails_even_with_matching_blob(self):
        for prefix in (b"name: 'proof'\n", b'name: "proof"\n', b"name: &anchor proof\n", b"# prefix\nname: proof\n"):
            with self.subTest(prefix=prefix):
                self.write_and_repin(prefix + b"on:\n  pull_request:\njobs: {}\n")
                self.assertFalse(self.verify())

    def test_non_utf8_definition_fails(self):
        self.write_and_repin(self.payload + b"\xff")
        self.assertFalse(self.verify())

    def test_oversized_definition_fails_without_unbounded_read(self):
        self.write_and_repin(b"x" * (CATALOG.MAX_DEFINITION_BYTES + 1))
        self.assertFalse(self.verify())

    def test_file_symlink_fails_even_with_correct_payload(self):
        alternate = self.root / "same.yml"
        alternate.write_bytes(self.payload)
        self.path.unlink()
        self.path.symlink_to(alternate)
        self.assertFalse(self.verify())

    def test_parent_symlink_fails_even_inside_checkout(self):
        original = self.path.parent
        alternate = self.root / "elsewhere"
        original.rename(alternate)
        original.symlink_to(alternate, target_is_directory=True)
        self.assertFalse(self.verify())

    def test_directory_instead_of_definition_fails(self):
        self.path.unlink()
        self.path.mkdir()
        self.assertFalse(self.verify())

    def test_noncanonical_and_traversing_paths_fail(self):
        for path in ("../proof.yml", "/.github/workflows/proof.yml", ".github//workflows/proof.yml", ".github/workflows/./proof.yml", ".github\\workflows\\proof.yml", ".github/workflows/proof.txt"):
            with self.subTest(path=path):
                self.requirement.path = path
                self.catalog.update(name=path, path=path)
                self.run.path = path
                self.assertFalse(self.verify())

    def test_catalog_fields_cannot_substitute_identity(self):
        for field, value in (("id", 11), ("id", "10"), ("id", True), ("path", ".github/workflows/other.yml"), ("state", "disabled_manually"), ("state", "deleted"), ("name", "renamed"), ("name", "proof.yml")):
            with self.subTest(field=field, value=value):
                before = self.catalog[field]
                self.catalog[field] = value
                self.assertFalse(self.verify())
                self.catalog[field] = before

    def test_run_fields_cannot_substitute_execution(self):
        for field, value in (("id", 0), ("id", True), ("attempt", 0), ("attempt", True), ("workflow_id", 11), ("name", "renamed"), ("path", ".github/workflows/other.yml"), ("head_sha", "b" * 40), ("event", "workflow_dispatch"), ("status", "in_progress"), ("conclusion", "failure"), ("conclusion", "skipped"), ("conclusion", "neutral")):
            with self.subTest(field=field, value=value):
                before = getattr(self.run, field)
                setattr(self.run, field, value)
                self.assertFalse(self.verify())
                setattr(self.run, field, before)

    def test_invalid_expected_head_fails(self):
        for head in ("", "A" * 40, "a" * 39, "a" * 41):
            with self.subTest(head=head):
                self.assertFalse(self.verify(expected_head=head))

    def test_disallowed_event_fails(self):
        self.requirement.allowed_events = ("push",)
        self.assertFalse(self.verify())

    def test_malformed_manifest_blob_fails(self):
        self.requirement.git_blob_sha1 = "bad"
        self.assertFalse(self.verify())

    def test_run_using_catalog_alias_instead_of_declared_name_fails(self):
        self.run.name = self.requirement.path
        self.assertFalse(self.verify())


class CatalogGateWiringTests(unittest.TestCase):
    def test_external_call_binds_context_and_aggregate_remains_strict(self):
        hardened = ROOT / "scripts/check-required-workflow-runs-hardened-core.py"
        path = hardened if hardened.exists() else ROOT / "scripts/check-required-workflow-runs.py"
        tree = ast.parse(path.read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = [node for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "workflow_metadata_failures"]
        self.assertEqual(len(calls), 2)
        scoped = [node for node in calls if node.keywords]
        self.assertEqual(len(scoped), 1)
        self.assertEqual({kw.arg for kw in scoped[0].keywords}, {"source_root", "run", "expected_head"})
        values = {kw.arg: ast.unparse(kw.value) for kw in scoped[0].keywords}
        self.assertEqual(values, {"source_root": "Path.cwd()", "run": "run", "expected_head": "options.head_sha"})

    def test_receipt_preserves_catalog_observation_and_source_identity(self):
        hardened = ROOT / "scripts/check-required-workflow-runs-hardened-core.py"
        path = hardened if hardened.exists() else ROOT / "scripts/check-required-workflow-runs.py"
        tree = ast.parse(path.read_text())
        keys = {key.value for node in ast.walk(tree) if isinstance(node, ast.Dict) for key in node.keys if isinstance(key, ast.Constant)}
        self.assertTrue({"catalog_name_observed", "run_name", "definition_blob_sha1", "catalog_path_alias_verified"}.issubset(keys))


if __name__ == "__main__":
    unittest.main()
