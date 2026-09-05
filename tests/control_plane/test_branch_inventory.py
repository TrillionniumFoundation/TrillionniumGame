from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "scripts/generate-branch-inventory.py"
VERIFIER_PATH = ROOT / "scripts/verify-branch-inventory-log.py"
ACTIVE_PATH = ROOT / "docs/governance/ACTIVE_BRANCHES.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BranchInventoryContractTests(unittest.TestCase):
    def test_current_active_registry_is_valid_and_bounded(self) -> None:
        generator = load_module(GENERATOR_PATH, "branch_inventory_generator_current")
        active, digest = generator.load_active_branches(ACTIVE_PATH)
        self.assertIn("main", active)
        self.assertLessEqual(len(active), 8)
        self.assertRegex(digest, r"^[a-f0-9]{64}$")
        self.assertIn("codex/branch-evidence-closure-2026-09-02", active)

    def test_active_nonancestor_is_never_delete_candidate(self) -> None:
        generator = load_module(GENERATOR_PATH, "branch_inventory_generator_active")
        disposition, _ = generator.classify(
            name="codex/current",
            is_active=True,
            commit_reachable_from_main=False,
            duplicate_tip=True,
        )
        self.assertEqual(disposition, "keep-active")

    def test_archive_is_preserved_before_reachability_classification(self) -> None:
        generator = load_module(GENERATOR_PATH, "branch_inventory_generator_archive")
        disposition, _ = generator.classify(
            name="archive/history",
            is_active=False,
            commit_reachable_from_main=True,
            duplicate_tip=True,
        )
        self.assertEqual(disposition, "keep-archive")

    def test_nonancestor_is_preserved(self) -> None:
        generator = load_module(GENERATOR_PATH, "branch_inventory_generator_nonancestor")
        disposition, _ = generator.classify(
            name="feature/unique",
            is_active=False,
            commit_reachable_from_main=False,
            duplicate_tip=False,
        )
        self.assertEqual(disposition, "preserve-nonancestor")

    def test_only_reviewed_reachable_candidates_can_be_cleanup_candidates(self) -> None:
        generator = load_module(GENERATOR_PATH, "branch_inventory_generator_cleanup")
        duplicate, _ = generator.classify(
            name="feature/duplicate",
            is_active=False,
            commit_reachable_from_main=True,
            duplicate_tip=True,
        )
        unique, _ = generator.classify(
            name="feature/reachable",
            is_active=False,
            commit_reachable_from_main=True,
            duplicate_tip=False,
        )
        self.assertEqual(duplicate, "delete-candidate-after-review")
        self.assertEqual(unique, "archive-or-delete-after-review")

    def test_stale_active_branch_is_rejected_by_build_inventory(self) -> None:
        generator = load_module(GENERATOR_PATH, "branch_inventory_generator_stale")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess = __import__("subprocess")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=root,
                check=True,
            )
            (root / "README").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", head],
                cwd=root,
                check=True,
            )
            active = {
                "schema": "trillionnium.active-branches.v1",
                "project_id": "trillionnium-game",
                "plan_version": 3,
                "generated_at": "2026-09-02T00:00:00Z",
                "active_branches": [
                    {
                        "name": "main",
                        "role": "protected-integration-authority",
                        "pull_request": None,
                    },
                    {
                        "name": "codex/missing",
                        "role": "fixture",
                        "pull_request": 1,
                    },
                ],
                "policy": {
                    "exact_name_required": True,
                    "active_branch_may_be_nonancestor": True,
                    "active_branch_never_auto_deleted": True,
                    "main_must_be_active": True,
                    "maximum_active_branch_count": 8,
                    "cleanup_requires_independent_review": True,
                    "cleanup_requires_before_and_after_manifests": True,
                    "nonancestor_branch_deletion_allowed": False,
                },
                "claims": {
                    "active_line_declared": True,
                    "branch_cleanup_reviewed": False,
                    "branch_cleanup_executed": False,
                    "cleanup_complete": False,
                    "sg0_complete": False,
                },
            }
            active_path = root / "ACTIVE_BRANCHES.json"
            active_path.write_text(json.dumps(active), encoding="utf-8")
            with self.assertRaisesRegex(
                generator.InventoryError,
                "declared active branches are missing",
            ):
                generator.build_inventory(root=root, active_path=active_path)

    def make_archive(self, members: dict[str, bytes]) -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, data in members.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return stream.getvalue()

    def test_retained_archive_requires_exact_closed_member_set(self) -> None:
        verifier = load_module(VERIFIER_PATH, "branch_inventory_verifier_members")
        inventory = b'{"schema":"fixture"}\n'
        sums = (
            hashlib.sha256(inventory).hexdigest()
            + "  branch-inventory.json\n"
        ).encode("ascii")
        archive = self.make_archive(
            {
                "branch-inventory.json": inventory,
                "SHA256SUMS": sums,
            }
        )
        members = verifier.read_archive(archive)
        self.assertEqual(set(members), {"branch-inventory.json", "SHA256SUMS"})

        archive_with_extra = self.make_archive(
            {
                "branch-inventory.json": inventory,
                "SHA256SUMS": sums,
                "unexpected.txt": b"unexpected",
            }
        )
        with self.assertRaisesRegex(
            verifier.VerificationError,
            "member set mismatch",
        ):
            verifier.read_archive(archive_with_extra)

    def test_retained_archive_rejects_path_traversal(self) -> None:
        verifier = load_module(VERIFIER_PATH, "branch_inventory_verifier_traversal")
        archive = self.make_archive(
            {
                "../branch-inventory.json": b"bad",
                "SHA256SUMS": b"bad",
            }
        )
        with self.assertRaises(verifier.VerificationError):
            verifier.read_archive(archive)


if __name__ == "__main__":
    unittest.main()
