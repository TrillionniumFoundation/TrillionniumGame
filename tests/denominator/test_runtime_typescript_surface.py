from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.denominator.typescript_runtime_surface import augment, extract_interfaces


REALISTIC_SOURCE = '''declare namespace nkruntime {
  export type Context = {
    env: {[key: string]: string},
    userId?: string,
  }

  type ReadPermissionValues = 0 | 1 | 2;

  const enum Codes {
    INVALID_ARGUMENT = 3,
    INTERNAL = 13,
  }

  export type Error = {
    message: string
    code: Codes
  }

  export interface RpcFunction {
    (ctx: Context, payload: string): string | void;
  }

  export interface Logger {
    info(format: string, ...args: any[]): void;
    withField(key: string, value: any): Logger;
    fields?: {[key: string]: string};
  }
}
'''


class RuntimeTypeScriptSurfaceTests(unittest.TestCase):
    def test_const_enum_and_unterminated_object_alias_do_not_hide_interfaces(self) -> None:
        items, manual = extract_interfaces(REALISTIC_SOURCE)
        interfaces = {
            item["symbol"]
            for item in items
            if item["class"] == "typescript_interface"
        }
        members = {
            item["symbol"]
            for item in items
            if item["class"] == "typescript_interface_member"
        }
        self.assertEqual(
            interfaces,
            {"nkruntime.Logger", "nkruntime.RpcFunction"},
        )
        self.assertIn("nkruntime.RpcFunction.[call]", members)
        self.assertIn("nkruntime.Logger.info", members)
        self.assertIn("nkruntime.Logger.withField", members)
        self.assertIn("nkruntime.Logger.fields", members)
        self.assertEqual(manual, [])

    def test_augmentation_replaces_interface_leaves_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            common = root / "common"
            output = root / "candidate"
            common.mkdir()
            output.mkdir()
            (common / "index.d.ts").write_text(REALISTIC_SOURCE, encoding="utf-8")
            manifest = {
                "schema": "trillionnium.runtime-denominator-candidate.v1",
                "project_id": "trillionnium-game",
                "status": "candidate-unclassified",
                "leaves": [
                    {
                        "id": "OLD",
                        "class": "typescript_interface_member",
                        "symbol": "stale.member",
                    },
                    {
                        "id": "KEEP",
                        "class": "go_interface_method",
                        "symbol": "runtime.Logger.Info",
                    },
                ],
                "manual_contracts": [],
                "claims": {"sg1_complete": False, "compatibility_credit": False},
            }
            manifest_path = output / "runtime-denominator.candidate.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            first = augment(common, output)
            first_bytes = manifest_path.read_bytes()
            second = augment(common, output)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, manifest_path.read_bytes())

            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(
                value["counts_by_class"]["typescript_interface_member"], 4
            )
            self.assertEqual(
                [leaf["id"] for leaf in value["leaves"]].count("OLD"), 0
            )
            self.assertIn("KEEP", {leaf["id"] for leaf in value["leaves"]})
            self.assertFalse(value["claims"]["sg1_complete"])
            self.assertFalse(value["claims"]["compatibility_credit"])


if __name__ == "__main__":
    unittest.main()
