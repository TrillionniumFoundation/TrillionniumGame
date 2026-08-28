from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def owner_from_key(key: str) -> str:
    return key.rsplit("/", 1)[-1]


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
                if expected == "missing" and existing is not None:
                    raise ValueError("storage_object_already_exists")
                if expected not in {"any", "missing"} and (existing is None or existing["version"] != expected):
                    raise ValueError("storage_version_mismatch")
                if existing is not None and existing["version"] == operation["version"] and existing["value"] != operation["value"]:
                    raise ValueError("storage_version_value_mismatch")
                staged[key] = {k: operation[k] for k in ("value", "version", "read", "write")}
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
        self.assertFalse(any(document["claims"].values()))
        for case in document["cases"]:
            with self.subTest(case=case["id"]):
                objects, error = apply_case(case)
                self.assertEqual(error, case.get("expect_error"))
                expected_objects = {
                    key: {**value, "read": objects.get(key, {}).get("read"), "write": objects.get(key, {}).get("write")}
                    for key, value in case["expect"]["objects"].items()
                }
                projected = {
                    key: {"value": value["value"], "version": value["version"], "read": value["read"], "write": value["write"]}
                    for key, value in objects.items()
                }
                self.assertEqual(projected, expected_objects)


if __name__ == "__main__":
    unittest.main()
