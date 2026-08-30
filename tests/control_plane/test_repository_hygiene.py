from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-repository-hygiene.py"
SPEC = importlib.util.spec_from_file_location("check_repository_hygiene", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load repository hygiene checker")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RepositoryHygieneTests(unittest.TestCase):
    def test_repository_large_file_exception_is_exact_and_current(self) -> None:
        exceptions = MODULE.load_large_file_exceptions()
        self.assertEqual(
            set(exceptions),
            {"manifests/upstream/candidates/sdk-denominator.candidate.json"},
        )
        for relative, row in exceptions.items():
            path = ROOT / relative
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, MODULE.MAX_TRACKED_FILE_BYTES)
            self.assertLessEqual(path.stat().st_size, row["max_bytes"])
            self.assertEqual(MODULE.file_sha256(path), row["sha256"])
            self.assertTrue((ROOT / row["generator"]).is_file())
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["denominator"], "DEN-SDK")
            self.assertEqual(value["leaf_count"], 1370)
            self.assertFalse(value["sg1_eligible"])
            self.assertFalse(value["compatibility_credit"])

    def test_wildcards_duplicate_paths_and_broad_limits_fail_closed(self) -> None:
        base = {
            "schema": "trillionnium.repository-large-file-exceptions.v1",
            "project_id": "trillionnium-game",
            "policy": {
                "wildcards_allowed": False,
                "exact_path_required": True,
                "sha256_binding_required": True,
                "maximum_exception_bytes": 8 * 1024 * 1024,
                "secret_or_binary_suffix_exceptions_allowed": False,
            },
            "exceptions": [
                {
                    "path": "manifests/upstream/candidates/example.json",
                    "sha256": "sha256:" + "a" * 64,
                    "max_bytes": 7 * 1024 * 1024,
                    "content_type": "application/json",
                    "generator": "scripts/example.py",
                    "reason": "A deliberately long review explanation that cannot be mistaken for an implicit exception.",
                }
            ],
            "claims": {
                "general_large_files_allowed": False,
                "digest_change_allowed_without_review": False,
                "runtime_artifacts_allowed": False,
                "secret_material_allowed": False,
                "compatibility_credit": False,
            },
        }

        def rejected(value: dict[str, object]) -> None:
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "exceptions.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(MODULE.HygieneError):
                    MODULE.load_large_file_exceptions(path)

        wildcard = json.loads(json.dumps(base))
        wildcard["exceptions"][0]["path"] = "manifests/upstream/candidates/*.json"
        rejected(wildcard)

        duplicate = json.loads(json.dumps(base))
        duplicate["exceptions"].append(dict(duplicate["exceptions"][0]))
        rejected(duplicate)

        broad = json.loads(json.dumps(base))
        broad["policy"]["maximum_exception_bytes"] = 64 * 1024 * 1024
        rejected(broad)

        secret = json.loads(json.dumps(base))
        secret["exceptions"][0]["path"] = "manifests/upstream/candidates/private.key"
        rejected(secret)

    def test_hash_changes_are_observable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate.json"
            path.write_text('{"value":1}\n', encoding="utf-8")
            first = MODULE.file_sha256(path)
            path.write_text('{"value":2}\n', encoding="utf-8")
            second = MODULE.file_sha256(path)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
