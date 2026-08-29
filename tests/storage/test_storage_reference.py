from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def owner_from_key(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def content_version(value: str) -> str:
    """Reproduce the pinned Nakama public storage version for exact UTF-8 bytes."""
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def apply_case(case: dict) -> tuple[dict, str | None]:
    objects = {
        item["key"]: {k: item[k] for k in ("value", "version", "read", "write")}
        for item in case.get("seed", [])
    }
    before = copy.deepcopy(objects)
    staged = copy.deepcopy(objects)
    seen: set[str] = set()
    try:
        for operation in case["operations"]:
            key = operation["key"]
            if key in seen:
                raise ValueError("duplicate_storage_key_in_batch")
            seen.add(key)
            actor = operation["actor"]
            existing = staged.get(key)
            if actor.startswith("user:"):
                user = actor.split(":", 1)[1]
                if user == "00" or user != owner_from_key(key):
                    raise ValueError("storage_write_permission_denied")
                if existing is not None and existing["write"] != 1:
                    raise ValueError("storage_write_permission_denied")
            if operation["op"] == "write":
                expected = operation["expected"]
                if expected == "*" and existing is not None:
                    raise ValueError("storage_object_already_exists")
                if expected not in {"", "*"} and (
                    existing is None or existing["version"] != expected
                ):
                    raise ValueError("storage_version_mismatch")
                version = content_version(operation["value"])
                staged[key] = {
                    "value": operation["value"],
                    "version": version,
                    "read": operation["read"],
                    "write": operation["write"],
                }
            elif operation["op"] == "delete":
                if existing is None:
                    raise ValueError("storage_object_not_found")
                if operation.get("expected") and existing["version"] != operation["expected"]:
                    raise ValueError("storage_version_mismatch")
                staged.pop(key)
            else:
                raise AssertionError(f"unknown operation {operation['op']}")
    except ValueError as exc:
        return before, str(exc)
    return staged, None


class StorageVectorTests(unittest.TestCase):
    def test_storage_vectors(self) -> None:
        document = json.loads((ROOT / "contracts/storage/storage-vectors.json").read_text())
        self.assertTrue(document["claims"]["public_version_source_candidate"])
        for claim in (
            "storage_behavior_compatible",
            "database_durable",
            "production_ready",
        ):
            self.assertFalse(document["claims"][claim], claim)
        for case in document["cases"]:
            with self.subTest(case=case["id"]):
                objects, error = apply_case(case)
                self.assertEqual(error, case.get("expect_error"))
                expected_objects = {
                    key: {
                        **value,
                        "read": objects.get(key, {}).get("read"),
                        "write": objects.get(key, {}).get("write"),
                    }
                    for key, value in case["expect"]["objects"].items()
                }
                projected = {
                    key: {
                        "value": value["value"],
                        "version": value["version"],
                        "read": value["read"],
                        "write": value["write"],
                    }
                    for key, value in objects.items()
                }
                self.assertEqual(projected, expected_objects)


if __name__ == "__main__":
    unittest.main()
