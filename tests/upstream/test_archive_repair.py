from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.upstream.archive_repair import repair_profile
from tools.upstream.pinned_archive import (
    LOCK_FILE,
    SourceArchiveError,
    canonical_bytes,
    git_blob_sha1_bytes,
    git_tree_sha1,
    verify_source_lock,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)

    def geturl(self) -> str:
        return "https://api.github.com/repos/owner/repo/git/blobs/pinned"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


def blob_response(payload: bytes) -> FakeResponse:
    blob = git_blob_sha1_bytes(payload)
    value = {
        "sha": blob,
        "size": len(payload),
        "encoding": "base64",
        "content": base64.b64encode(payload).decode("ascii"),
    }
    return FakeResponse(json.dumps(value).encode("utf-8"))


class ArchiveRepairTests(unittest.TestCase):
    def fixture(self, base: Path) -> tuple[Path, Path, bytes, str, str, str]:
        root = base / "source"
        target = root / "integrationtests" / "android" / "gradlew.bat"
        target.parent.mkdir(parents=True)
        canonical_payload = b"@rem canonical\r\n@echo off\r\n"
        archive_payload = canonical_payload.replace(b"\r\n", b"\n")
        target.write_bytes(archive_payload)
        archive_blob = git_blob_sha1_bytes(archive_payload)
        canonical_blob = git_blob_sha1_bytes(canonical_payload)
        archive_tree = git_tree_sha1(root)

        marker = {
            "schema": "trillionnium.pinned-source-archive.v1",
            "repository": "owner/repo",
            "revision": "1" * 40,
            "tree": archive_tree,
            "verification": "recomputed-git-tree-sha1",
            "gitlinks": [],
        }
        (root / LOCK_FILE).write_bytes(canonical_bytes(marker))

        canonical_copy = base / "canonical"
        canonical_target = canonical_copy / "integrationtests" / "android" / "gradlew.bat"
        canonical_target.parent.mkdir(parents=True)
        canonical_target.write_bytes(canonical_payload)
        canonical_tree = git_tree_sha1(canonical_copy)

        registry = base / "registry.json"
        registry.write_text(
            json.dumps(
                {
                    "profiles": [
                        {
                            "id": "cpp",
                            "repository": "owner/repo",
                            "commit": "1" * 40,
                            "tree": canonical_tree,
                            "archive_tree": archive_tree,
                            "archive_repairs": [
                                {
                                    "path": "integrationtests/android/gradlew.bat",
                                    "archive_blob": archive_blob,
                                    "canonical_blob": canonical_blob,
                                    "reason": "fixture line-ending normalization",
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return root, registry, canonical_payload, archive_blob, canonical_blob, canonical_tree

    def test_pinned_blob_repair_restores_exact_canonical_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, registry, canonical_payload, _, _, canonical_tree = self.fixture(base)
            with mock.patch(
                "tools.upstream.archive_repair.urllib.request.urlopen",
                return_value=blob_response(canonical_payload),
            ):
                result = repair_profile(
                    registry=registry,
                    profile_id="cpp",
                    root=root,
                    token="test-token",
                )

            target = root / "integrationtests" / "android" / "gradlew.bat"
            self.assertEqual(target.read_bytes(), canonical_payload)
            self.assertEqual(result["canonical_tree"], canonical_tree)
            self.assertFalse(result["compatibility_credit"])
            marker = verify_source_lock(
                root,
                repository="owner/repo",
                revision="1" * 40,
                tree=canonical_tree,
            )
            self.assertEqual(marker["archive_repairs"], result["repairs"])
            self.assertEqual(
                marker["transport_verification"],
                "archive-tree-verified-before-pinned-canonical-blob-repair",
            )

    def test_archive_blob_drift_fails_without_publishing_a_repaired_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, registry, canonical_payload, archive_blob, _, _ = self.fixture(base)
            registry_value = json.loads(registry.read_text(encoding="utf-8"))
            registry_value["profiles"][0]["archive_repairs"][0]["archive_blob"] = "f" * 40
            registry.write_text(json.dumps(registry_value), encoding="utf-8")

            with mock.patch(
                "tools.upstream.archive_repair.urllib.request.urlopen",
                return_value=blob_response(canonical_payload),
            ):
                with self.assertRaises(SourceArchiveError):
                    repair_profile(
                        registry=registry,
                        profile_id="cpp",
                        root=root,
                        token=None,
                    )

            marker = json.loads((root / LOCK_FILE).read_text(encoding="utf-8"))
            self.assertNotIn("archive_repairs", marker)
            self.assertNotEqual(archive_blob, "f" * 40)


if __name__ == "__main__":
    unittest.main()
