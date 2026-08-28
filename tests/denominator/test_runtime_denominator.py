from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate-runtime-denominator.py"
SPEC = importlib.util.spec_from_file_location("runtime_denominator", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

from tools.upstream.pinned_archive import LOCK_FILE, git_tree_sha1

GO_SOURCE = '''package runtime
import "errors"
const RUNTIME_CTX_USER_ID = "user_id"
var ErrExample = errors.New("example")
type Logger interface {
    Info(format string, v ...interface{})
    WithField(key string, v interface{}) Logger
}
type Handler func(payload string) (string, error)
type Config struct { Name string `json:"name"`; hidden string }
func NewError(message string, code int) error { return nil }
'''
TS_SOURCE = '''declare namespace nkruntime {
  interface Logger {
    info(format: string, ...args: any[]): void;
    withField(key: string, value: any): Logger;
  }
  type Handler = (payload: string) => string;
  enum Mode { Before = "before", After = "after" }
  function rpc(id: string, payload?: string): string;
}
'''
ADAPTER_SOURCE = '''package server
type RuntimeAdapter struct{}
func newRuntimeAdapter() *RuntimeAdapter { return &RuntimeAdapter{} }
'''


def write_locked(root: Path, repository: str, revision: str, files: dict[str, str]) -> str:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    tree = git_tree_sha1(root)
    marker = {
        "schema": "trillionnium.pinned-source-archive.v1",
        "repository": repository,
        "revision": revision,
        "tree": tree,
        "verification": "recomputed-git-tree-sha1",
    }
    (root / LOCK_FILE).write_text(json.dumps(marker), encoding="utf-8")
    return tree


class RuntimeDenominatorTests(unittest.TestCase):
    def test_go_ast_helper_extracts_runtime_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "runtime/runtime.go"
            source.parent.mkdir(parents=True)
            source.write_text(GO_SOURCE, encoding="utf-8")
            value = mod.run_go_extractor(root, ["runtime/runtime.go"])
            classes = {item["class"] for item in value["items"]}
            self.assertTrue({"go_constant", "go_variable", "go_interface", "go_interface_method", "go_function_type", "go_struct", "go_struct_field", "go_function"} <= classes)

    def test_typescript_parser_extracts_namespace_interface_type_enum_and_function(self) -> None:
        items, manual = mod.TypeScriptParser(TS_SOURCE, "index.d.ts").parse()
        classes = {item["class"] for item in items}
        self.assertTrue({"typescript_namespace", "typescript_interface", "typescript_interface_member", "typescript_type_alias", "typescript_enum", "typescript_enum_value", "typescript_function"} <= classes)
        self.assertEqual(manual, [])

    def test_full_candidate_is_deterministic_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            nakama = base / "nakama"
            common = base / "common"
            nakama.mkdir(); common.mkdir()
            nrev = "1" * 40; crev = "2" * 40
            ntree = write_locked(nakama, "test/nakama", nrev, {"server/runtime_adapter.go": ADAPTER_SOURCE})
            ctree = write_locked(common, "test/common", crev, {"runtime/runtime.go": GO_SOURCE, "runtime/config.go": "package runtime\ntype ConfigView interface { GetName() string }\n", "index.d.ts": TS_SOURCE})
            original = (mod.NAKAMA_REPOSITORY, mod.NAKAMA_COMMIT, mod.NAKAMA_TREE, mod.COMMON_REPOSITORY, mod.COMMON_COMMIT, mod.COMMON_TREE)
            mod.NAKAMA_REPOSITORY, mod.NAKAMA_COMMIT, mod.NAKAMA_TREE = "test/nakama", nrev, ntree
            mod.COMMON_REPOSITORY, mod.COMMON_COMMIT, mod.COMMON_TREE = "test/common", crev, ctree
            try:
                out_a = base / "a"; out_b = base / "b"
                first = mod.generate(nakama, common, out_a)
                second = mod.generate(nakama, common, out_b)
                self.assertEqual(first, second)
                self.assertEqual((out_a / "runtime-denominator.candidate.json").read_bytes(), (out_b / "runtime-denominator.candidate.json").read_bytes())
                manifest = json.loads((out_a / "runtime-denominator.candidate.json").read_text())
                self.assertEqual(manifest["unclassified_count"], manifest["leaf_count"])
                self.assertEqual(manifest["unreviewed_count"], manifest["leaf_count"])
                self.assertFalse(any(manifest["claims"].values()))
                with self.assertRaises(mod.DenominatorError):
                    mod.require_sg1(out_a)
            finally:
                (mod.NAKAMA_REPOSITORY, mod.NAKAMA_COMMIT, mod.NAKAMA_TREE, mod.COMMON_REPOSITORY, mod.COMMON_COMMIT, mod.COMMON_TREE) = original

    def test_post_fetch_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            nakama = base / "nakama"; common = base / "common"
            nakama.mkdir(); common.mkdir()
            nrev = "1" * 40; crev = "2" * 40
            ntree = write_locked(nakama, "test/nakama", nrev, {"server/runtime_adapter.go": ADAPTER_SOURCE})
            ctree = write_locked(common, "test/common", crev, {"runtime/runtime.go": GO_SOURCE, "runtime/config.go": "package runtime\n", "index.d.ts": TS_SOURCE})
            (common / "index.d.ts").write_text(TS_SOURCE + "// tamper\n", encoding="utf-8")
            original = (mod.NAKAMA_REPOSITORY, mod.NAKAMA_COMMIT, mod.NAKAMA_TREE, mod.COMMON_REPOSITORY, mod.COMMON_COMMIT, mod.COMMON_TREE)
            mod.NAKAMA_REPOSITORY, mod.NAKAMA_COMMIT, mod.NAKAMA_TREE = "test/nakama", nrev, ntree
            mod.COMMON_REPOSITORY, mod.COMMON_COMMIT, mod.COMMON_TREE = "test/common", crev, ctree
            try:
                with self.assertRaises(mod.DenominatorError):
                    mod.generate(nakama, common, base / "out")
            finally:
                (mod.NAKAMA_REPOSITORY, mod.NAKAMA_COMMIT, mod.NAKAMA_TREE, mod.COMMON_REPOSITORY, mod.COMMON_COMMIT, mod.COMMON_TREE) = original


if __name__ == "__main__":
    unittest.main()
