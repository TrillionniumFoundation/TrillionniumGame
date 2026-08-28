from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.oracle.hook_inventory import (
    InventoryError,
    canonical_bytes,
    generate_inventory,
    import_aliases,
)

COMMIT = "1" * 40


def policy() -> dict:
    return {
        "upstream": {"repository": "heroiclabs/nakama", "commit": COMMIT, "tree": "TREE"},
        "include_roots": ["server", "social", "iap"],
        "restricted_paths": ["server/restricted.go"],
        "capabilities": {
            "clock_capture": ["time.Now"],
            "random_capture": ["uuid.NewV4", "rand.Read"],
            "provider_intent_capture": ["http.NewRequest", ".Do"],
            "database_effect_capture": [".ExecContext"],
            "runtime_hook_capture": ["RegisterBefore"],
            "trace_correlation": ["TraceID"],
        },
    }


def init_repo(root: Path) -> dict:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "server").mkdir()
    (root / "social").mkdir()
    (root / "iap").mkdir()
    (root / "server/main.go").write_text(
        'package server\nimport (\n "time"\n "github.com/gofrs/uuid/v5"\n "database/sql"\n)\nfunc f(db *sql.DB) { _ = time.Now(); _, _ = uuid.NewV4(); _, _ = db.ExecContext(nil, "x") }\n',
        encoding="utf-8",
    )
    (root / "social/provider.go").write_text(
        'package social\nimport "net/http"\nfunc f(c *http.Client) { r, _ := http.NewRequest("GET", "x", nil); _, _ = c.Do(r) }\n',
        encoding="utf-8",
    )
    (root / "server/restricted.go").write_text(
        'package server\nfunc CheckACL() {}\n', encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True)
    actual_commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    actual_tree = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip()
    value = policy()
    value["upstream"]["commit"] = actual_commit
    value["upstream"]["tree"] = actual_tree
    return value


class HookInventoryTests(unittest.TestCase):
    def test_extracts_exact_capabilities_and_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = generate_inventory(root, init_repo(root))
            self.assertGreaterEqual(value["site_count"], 5)
            capabilities = {site["capability"] for site in value["sites"]}
            self.assertTrue({"clock_capture", "random_capture", "provider_intent_capture", "database_effect_capture"} <= capabilities)
            self.assertTrue(all(site["line"] > 0 and site["column"] > 0 for site in value["sites"]))
            self.assertTrue(all(len(site["git_blob"]) == 40 for site in value["sites"]))

    def test_versioned_go_import_uses_package_before_version_segment(self):
        aliases = import_aliases('import "github.com/gofrs/uuid/v5"\n')
        self.assertEqual(aliases["uuid"], "github.com/gofrs/uuid/v5")

    def test_restricted_source_becomes_manual_contract_not_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = generate_inventory(root, init_repo(root))
            self.assertEqual(len(value["manual_contracts"]), 1)
            self.assertFalse(any(site["path"] == "server/restricted.go" for site in value["sites"]))
            self.assertNotIn("CheckACL", canonical_bytes(value["manual_contracts"]).decode())

    def test_candidate_never_authorizes_or_generates_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = generate_inventory(root, init_repo(root))
            self.assertFalse(any(value["claims"].values()))
            self.assertTrue(all(site["patch_authorized"] is False for site in value["sites"]))

    def test_output_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p = init_repo(root)
            self.assertEqual(canonical_bytes(generate_inventory(root, p)), canonical_bytes(generate_inventory(root, p)))

    def test_upstream_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p = init_repo(root)
            p["upstream"]["tree"] = "0" * 40
            with self.assertRaises(InventoryError):
                generate_inventory(root, p)

    def test_stable_ids_differ_by_line_and_column(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p = init_repo(root)
            source = root / "server/main.go"
            source.write_text(source.read_text() + '\nfunc g() { _ = time.Now(); _ = time.Now() }\n')
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "two calls"], check=True)
            p["upstream"]["commit"] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            p["upstream"]["tree"] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip()
            value = generate_inventory(root, p)
            ids = [site["id"] for site in value["sites"]]
            self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
