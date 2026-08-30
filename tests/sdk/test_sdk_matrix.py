from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tools.sdk.matrix import SDKMatrixError, generate, load_registry
from tools.upstream.pinned_archive import (
    git_tree_sha1,
    verify_source_lock as strict_verify_source_lock,
)

NAKAMA_COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
COMMON_COMMIT = "449b77ecc8789aa466c36b67f6e498033dfcd9c5"


def lock(root: Path, repository: str, commit: str) -> dict[str, str]:
    tree = git_tree_sha1(root)
    identity = {"repository": repository, "revision": commit, "tree": tree}
    (root / ".trillionnium-source-lock.json").write_text(
        json.dumps({**identity, "verification": "recomputed-git-tree-sha1"}),
        encoding="utf-8",
    )
    return identity


def fixture(base: Path):
    nakama = base / "nakama"
    common = base / "common"
    sdks = base / "sdks"

    (nakama / "apigrpc").mkdir(parents=True)
    (nakama / "apigrpc/apigrpc.proto").write_text(
        "service N { "
        + "".join(f"rpc Op{i} (R) returns (R);" for i in range(60))
        + " }",
        encoding="utf-8",
    )
    nakama_identity = lock(nakama, "heroiclabs/nakama", NAKAMA_COMMIT)

    (common / "rtapi").mkdir(parents=True)
    (common / "rtapi/realtime.proto").write_text(
        "".join(f"message Event{i} {{ string id = 1; }}" for i in range(30)),
        encoding="utf-8",
    )
    common_identity = lock(common, "heroiclabs/nakama-common", COMMON_COMMIT)

    profiles = []
    for index in range(10):
        profile = {
            "id": f"sdk{index}",
            "repository": f"heroiclabs/nakama-sdk{index}",
            "branch": "main",
            "commit": f"{index + 1:040x}"[-40:],
            "tree": "0" * 40,
            "language": "x",
            "platform": "x",
        }
        root = sdks / profile["id"]
        root.mkdir(parents=True)
        (root / "client.ts").write_text(
            "async function Op1Async(){} class Event2 {}",
            encoding="utf-8",
        )
        profile["tree"] = lock(root, profile["repository"], profile["commit"])["tree"]
        profiles.append(profile)

    registry = {
        "schema": "trillionnium.sdk-source-snapshots.v1",
        "project_id": "trillionnium-game",
        "status": "candidate-default-branch-snapshots",
        "profiles": profiles,
        "claims": {
            "release_line_selected": False,
            "operation_coverage_verified": False,
            "transport_profiles_verified": False,
            "support_windows_verified": False,
            "sg1_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }
    return registry, sdks, nakama, common, nakama_identity, common_identity


@contextmanager
def strict_synthetic_upstreams(
    nakama: Path,
    common: Path,
    nakama_identity: dict[str, str],
    common_identity: dict[str, str],
):
    nakama_resolved = nakama.resolve()
    common_resolved = common.resolve()

    def verify(root: Path, *, repository: str, revision: str, tree: str):
        resolved = root.resolve()
        if resolved == nakama_resolved:
            expected = nakama_identity
        elif resolved == common_resolved:
            expected = common_identity
        else:
            expected = {
                "repository": repository,
                "revision": revision,
                "tree": tree,
            }
        return strict_verify_source_lock(root, **expected)

    # Production keeps fixed Nakama/nakama-common trees. This test-only binding
    # makes the same strict verifier use the independently recomputed trees of
    # the small synthetic upstream fixtures.
    with patch("tools.sdk.matrix.verify_source_lock", side_effect=verify):
        yield


class Tests(unittest.TestCase):
    def test_matrix_is_finite_deterministic_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry, sdks, nakama, common, nakama_id, common_id = fixture(Path(temporary))
            with strict_synthetic_upstreams(nakama, common, nakama_id, common_id):
                first = generate(registry, sdks, nakama, common)
                second = generate(registry, sdks, nakama, common)
            self.assertEqual(first, second)
            self.assertEqual(first["leaf_count"], 10 * (60 + 30))
            self.assertEqual(first["unclassified_count"], first["leaf_count"])
            self.assertFalse(first["sg1_eligible"])
            self.assertFalse(first["operation_coverage_verified"])
            self.assertTrue(
                any(
                    leaf["contract"]["candidate_presence"] == "candidate-present"
                    for leaf in first["leaves"]
                )
            )
            self.assertTrue(
                any(
                    leaf["contract"]["candidate_presence"] == "candidate-missing"
                    for leaf in first["leaves"]
                )
            )

    def test_registry_rejects_duplicates_and_positive_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry, *_ = fixture(Path(temporary))
            path = Path(temporary) / "registry.json"
            registry["profiles"][1]["id"] = registry["profiles"][0]["id"]
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(SDKMatrixError):
                load_registry(path)

            registry, *_ = fixture(Path(temporary) / "positive")
            registry["claims"]["sg1_complete"] = True
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(SDKMatrixError):
                load_registry(path)

    def test_source_lock_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry, sdks, nakama, common, nakama_id, common_id = fixture(Path(temporary))
            (sdks / "sdk0/.trillionnium-source-lock.json").write_text("{}", encoding="utf-8")
            with strict_synthetic_upstreams(nakama, common, nakama_id, common_id):
                with self.assertRaises(Exception):
                    generate(registry, sdks, nakama, common)


if __name__ == "__main__":
    unittest.main()
