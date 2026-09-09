from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STABLE_PATH = ROOT / "scripts/verify-branch-inventory-stable.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "branch_inventory_stable_test_module", STABLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StableBranchInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.main = ("main", "a" * 40, "b" * 40)
        self.candidate = ("codex/candidate", "c" * 40, "d" * 40)
        self.captured = [self.main, self.candidate]

    def test_bounded_additions_do_not_invalidate_exact_before_state(self) -> None:
        live = self.captured + [("codex/new", "e" * 40, "f" * 40)]
        result = self.module.compare_snapshot_to_live(self.captured, live)
        self.assertEqual(result["captured_branch_count"], 2)
        self.assertEqual(result["live_branch_count"], 3)
        self.assertEqual(result["concurrent_addition_count"], 1)
        self.assertEqual(result["concurrent_additions"][0]["name"], "codex/new")
        self.assertTrue(result["captured_remote_refs_reverified"])
        self.assertEqual(result["concurrent_removed_branch_count"], 0)
        self.assertEqual(result["concurrent_moved_branch_count"], 0)

    def test_captured_deletion_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            self.module.BASE.VerificationError,
            "captured branch disappeared",
        ):
            self.module.compare_snapshot_to_live(self.captured, [self.main])

    def test_captured_commit_or_tree_movement_is_rejected(self) -> None:
        for moved in [
            ("codex/candidate", "e" * 40, "d" * 40),
            ("codex/candidate", "c" * 40, "f" * 40),
        ]:
            with self.subTest(moved=moved):
                with self.assertRaisesRegex(
                    self.module.BASE.VerificationError,
                    "captured branch moved",
                ):
                    self.module.compare_snapshot_to_live([self.main, self.candidate], [self.main, moved])

    def test_duplicate_names_are_rejected(self) -> None:
        duplicate = [self.main, ("main", "e" * 40, "f" * 40)]
        with self.assertRaisesRegex(
            self.module.BASE.VerificationError,
            "duplicate names",
        ):
            self.module.compare_snapshot_to_live(duplicate, [self.main])
        with self.assertRaisesRegex(
            self.module.BASE.VerificationError,
            "duplicate names",
        ):
            self.module.compare_snapshot_to_live([self.main], duplicate)

    def test_additions_are_bounded(self) -> None:
        live = list(self.captured)
        for index in range(self.module.MAX_CONCURRENT_ADDITIONS + 1):
            live.append((f"codex/new-{index}", f"{index:040x}", f"{index + 1:040x}"))
        with self.assertRaisesRegex(
            self.module.BASE.VerificationError,
            "additions exceeded verification bound",
        ):
            self.module.compare_snapshot_to_live(self.captured, live)


if __name__ == "__main__":
    unittest.main()
