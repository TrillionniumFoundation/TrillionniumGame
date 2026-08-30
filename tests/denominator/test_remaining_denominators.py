from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SCRIPT = ROOT / "scripts/generate-remaining-denominators.py"
SPEC = importlib.util.spec_from_file_location("remaining_generator", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

from tools.denominator import common
from tools.denominator.common import REVISION, canonical_bytes
from tools.denominator.console import parse_proto, parse_swagger
from tools.upstream.pinned_archive import git_tree_sha1

APACHE = "// Licensed under the Apache License, Version 2.0 (the License);\n"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fixture(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    write(
        root / "console/console.proto",
        APACHE
        + '''syntax = "proto3";
package nakama.console;
service Console { rpc GetUser (GetUserRequest) returns (User); }
message GetUserRequest { string id = 1; }
message User { string id = 1; repeated string roles = 2; }
enum Role { ROLE_UNKNOWN = 0; ROLE_ADMIN = 1; }
''',
    )
    swagger = {
        "swagger": "2.0",
        "paths": {
            "/v2/console/user/{id}": {
                "get": {
                    "operationId": "Console_GetUser",
                    "parameters": [{"name": "id", "in": "path", "required": True}],
                    "responses": {
                        "200": {"schema": {"$ref": "#/definitions/nakama.console.User"}}
                    },
                }
            }
        },
        "definitions": {
            "nakama.console.User": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }
        },
    }
    write(root / "console/console.swagger.json", json.dumps(swagger))
    write(
        root / "console/api.swagger.json",
        json.dumps({"swagger": "2.0", "paths": {}, "definitions": {}}),
    )
    write(
        root / "console/acl/acl.go",
        "// Copyright Heroic Labs. All rights reserved. Proprietary; reproduction strictly forbidden.\npackage acl\n",
    )
    write(root / "console/ui/dist/index.html", '<script src="/static/index.js"></script>')
    write(root / "console/ui/dist/static/index.js", "console.log('ui')")

    provider = APACHE + '''package social
const AppleEndpoint = "https://apple.example/token"
type Client struct{}
func AuthenticateApple() { _ = http.MethodPost }
'''
    for relative in (
        "social/social.go",
        "server/api_authenticate.go",
        "server/api_link.go",
        "server/api_unlink.go",
    ):
        write(root / relative, provider)
    iap = APACHE + '''package iap
const PurchaseState = "refund"
type Receipt struct{}
func ValidatePurchase() { _ = http.MethodPost; _ = "https://iap.example/verify"; _ = "SELECT id FROM purchase" }
'''
    for relative in (
        "iap/iap.go",
        "iap/iap_samsung.go",
        "server/api_purchase.go",
        "server/api_subscription.go",
    ):
        write(root / relative, iap)

    metrics = APACHE + '''package server
func NewMetrics() { _ = "api_request_duration"; _ = "/metrics" }
'''
    write(root / "server/metrics.go", metrics)
    write(
        root / "server/status.go",
        APACHE + 'package server\nfunc Health() { _ = "/healthcheck" }\n',
    )
    write(root / "main.go", APACHE + "package main\n")
    write(
        root / "Dockerfile",
        "FROM alpine:3.22\nEXPOSE 7350\nHEALTHCHECK CMD wget -q localhost:7350/healthcheck\n",
    )
    write(root / "Makefile", "build:\n\techo build\n")
    write(
        root / "docker-compose.yml",
        "services:\n  nakama:\n    ports:\n      - '127.0.0.1:7350:7350'\n",
    )
    write(root / "buf.yaml", "version: v2\n")
    write(root / "buf.lock", "# lock\n")
    write(root / "buf.sh", "#!/bin/sh\n")
    write(root / "build/package.sh", "#!/bin/sh\necho package\n")

    tree = git_tree_sha1(root)
    (root / ".trillionnium-source-lock.json").write_text(
        json.dumps(
            {
                "repository": "heroiclabs/nakama",
                "revision": REVISION,
                "tree": tree,
                "verification": "recomputed-git-tree-sha1",
            }
        ),
        encoding="utf-8",
    )
    return tree


@contextmanager
def honest_fixture(root: Path):
    tree = fixture(root)
    # Production keeps the fixed upstream tree. Unit fixtures temporarily bind
    # the same strict verifier to their independently recomputed synthetic tree.
    with patch.object(common, "TREE", tree):
        yield


class RemainingDenominatorTests(unittest.TestCase):
    def test_proto_and_swagger_extract_wire_contracts(self) -> None:
        items, manual = parse_proto(
            "package x; service C { rpc Get (Req) returns (Res); } "
            "message Req { string id = 1; } enum E { X = 0; }"
        )
        classes = {item["class"] for item in items}
        self.assertTrue(
            {
                "console_grpc_service",
                "console_grpc_method",
                "console_proto_message",
                "console_proto_field",
                "console_proto_enum",
                "console_proto_enum_value",
            }
            <= classes
        )
        self.assertEqual(manual, [])
        parsed, manual = parse_swagger(
            {
                "swagger": "2.0",
                "paths": {"/x": {"get": {"operationId": "C_Get", "responses": {}}}},
                "definitions": {},
            },
            "console",
        )
        self.assertEqual(parsed[0]["method"], "GET")
        self.assertEqual(manual, [])

    def test_full_generation_is_deterministic_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            with honest_fixture(source):
                first = base / "a"
                second = base / "b"
                summary = mod.generate(source, first)
                mod.generate(source, second)
            self.assertEqual(
                (first / "SHA256SUMS").read_bytes(),
                (second / "SHA256SUMS").read_bytes(),
            )
            for path in first.glob("*-denominator.candidate.json"):
                value = json.loads(path.read_text())
                self.assertEqual(value["status"], "candidate-unclassified")
                self.assertEqual(value["leaf_count"], value["unclassified_count"])
                self.assertFalse(value["sg1_eligible"])
                self.assertFalse(value["compatibility_credit"])
            self.assertGreaterEqual(summary["console"], 5)
            self.assertGreaterEqual(summary["providers"], 8)
            self.assertGreaterEqual(summary["iap"], 8)
            self.assertGreaterEqual(summary["metrics"], 2)
            self.assertGreaterEqual(summary["ops"], 5)

    def test_restricted_acl_is_not_semantically_reproduced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            output = Path(temporary) / "out"
            with honest_fixture(source):
                mod.generate(source, output)
            value = json.loads((output / "console-denominator.candidate.json").read_text())
            restricted = [
                item
                for item in value["manual_contracts"]
                if item["class"] == "restricted_console_acl_source"
            ]
            self.assertEqual(len(restricted), 1)
            self.assertNotIn("CheckACL", canonical_bytes(restricted).decode())

    def test_provider_iap_and_ops_candidates_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            output = Path(temporary) / "out"
            with honest_fixture(source):
                mod.generate(source, output)
            providers = json.loads((output / "providers-denominator.candidate.json").read_text())
            iap = json.loads((output / "iap-denominator.candidate.json").read_text())
            metrics = json.loads((output / "metrics-denominator.candidate.json").read_text())
            ops = json.loads((output / "operations-denominator.candidate.json").read_text())
            self.assertTrue(
                any(
                    item["class"] == "provider_endpoint_candidate"
                    for item in providers["leaves"]
                )
            )
            self.assertTrue(
                any(
                    item["class"] == "iap_database_statement_candidate"
                    for item in iap["leaves"]
                )
            )
            self.assertTrue(
                any(
                    item["class"] == "metric_name_candidate"
                    for item in metrics["leaves"]
                )
            )
            self.assertTrue(
                any(item["class"] == "dockerfile_instruction" for item in ops["leaves"])
            )

    def test_require_sg1_rejects_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            output = Path(temporary) / "out"
            with honest_fixture(source):
                mod.generate(source, output)
            with self.assertRaises(Exception):
                mod.require_sg1(output)


if __name__ == "__main__":
    unittest.main()
