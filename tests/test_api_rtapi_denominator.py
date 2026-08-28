from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate-api-rtapi-denominator.py"
SPEC = importlib.util.spec_from_file_location("denominator", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

PROTO = '''syntax = "proto3";
package test.api;
message Request { string id = 1; oneof choice { bytes raw = 2; } }
enum State { UNKNOWN = 0; READY = 1; }
service Test {
  rpc Run (Request) returns (Request) {
    option (google.api.http) = { post: "/v1/run" body: "*" additional_bindings { get: "/v1/run/{id}" } };
  }
}
'''

SWAGGER = {
    "swagger": "2.0",
    "paths": {
        "/v1/run": {
            "post": {
                "operationId": "Test_Run",
                "parameters": [{"name": "body", "in": "body", "schema": {"$ref": "#/definitions/test.api.Request"}}],
                "responses": {"200": {"schema": {"$ref": "#/definitions/test.api.Request"}}},
            }
        }
    },
    "definitions": {
        "test.api.Request": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}, "raw": {"type": "string", "format": "byte"}},
        }
    },
}


class DenominatorTests(unittest.TestCase):
    def test_proto_parser_extracts_nested_wire_contracts(self) -> None:
        items, manual = mod.ProtoParser(PROTO).parse()
        classes = {item["class"] for item in items}
        self.assertIn("grpc_service", classes)
        self.assertIn("grpc_method", classes)
        self.assertIn("proto_http_binding", classes)
        self.assertIn("proto_message", classes)
        self.assertIn("proto_field", classes)
        self.assertIn("proto_enum", classes)
        self.assertIn("proto_enum_value", classes)
        routes = {(item.get("method"), item.get("path")) for item in items if item["class"] == "proto_http_binding"}
        self.assertEqual(routes, {("POST", "/v1/run"), ("GET", "/v1/run/{id}")})
        self.assertEqual(manual, [])

    def test_openapi_parser_extracts_operation_and_properties(self) -> None:
        items, manual = mod.parse_openapi(SWAGGER)
        self.assertEqual(manual, [])
        operation = next(item for item in items if item["class"] == "openapi_operation")
        self.assertEqual((operation["method"], operation["path"], operation["request"]), ("POST", "/v1/run", "test.api.Request"))
        properties = {item["symbol"] for item in items if item["class"] == "openapi_property"}
        self.assertEqual(properties, {"test.api.Request.id", "test.api.Request.raw"})

    def test_blob_identity_rejects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for source in mod.SOURCES.values():
                path = root / source["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"tampered")
            with self.assertRaises(mod.ContractError):
                mod.load_sources(root)

    def test_stable_ids_and_canonical_json_are_deterministic(self) -> None:
        self.assertEqual(mod.leaf_id("D1", "grpc_method", "test.api.Test.Run"), mod.leaf_id("D1", "grpc_method", "test.api.Test.Run"))
        self.assertEqual(mod.canonical({"b": 2, "a": 1}), b'{"a":1,"b":2}')

    def test_candidate_manifest_never_overclaims(self) -> None:
        source = {"key": "synthetic", "repository": "test/repo", "commit": "a" * 40, "path": "api.proto", "blob": "b" * 40, "size": 1, "lines": 1, "sha256": "sha256:" + "c" * 64}
        leaf = mod.make_leaf("D1", {"class": "grpc_method", "symbol": "test.api.Test.Run", "start": 1, "end": 1}, source)
        manifest = mod.build_manifest("DEN-API", "D1", [leaf], [], [source])
        self.assertEqual(manifest["status"], "candidate-unclassified")
        self.assertEqual(manifest["unclassified_count"], 1)
        self.assertFalse(manifest["sg1_eligible"])
        self.assertFalse(manifest["compatibility_credit"])

    def test_require_sg1_fails_for_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = {"status": "candidate-unclassified", "unclassified_count": 1, "manual_contract_count": 0, "sg1_eligible": False}
            for name in ("api-denominator.candidate.json", "rtapi-denominator.candidate.json"):
                (root / name).write_text(json.dumps(candidate), encoding="utf-8")
            with self.assertRaises(mod.ContractError):
                mod.require_sg1(root)


if __name__ == "__main__":
    unittest.main()
