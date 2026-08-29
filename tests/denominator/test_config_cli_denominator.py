from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate-config-cli-denominator.py"
SPEC = importlib.util.spec_from_file_location("config_denominator", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

from tools.upstream.pinned_archive import LOCK_FILE, git_tree_sha1

CONFIG = r'''package server
import "flag"
type SocketConfig struct {
    ServerKey string `yaml:"server_key" usage:"Server key"`
    MaxMessageSizeBytes int64 `yaml:"max_message_size_bytes" usage:"Maximum message size"`
}
type Config interface { GetSocket() *SocketConfig }
func NewConfig() *SocketConfig {
    c := &SocketConfig{ServerKey: "defaultkey", MaxMessageSizeBytes: 4096}
    c.ServerKey = "overridden"
    return c
}
func ValidateConfig(logger Logger, c *SocketConfig) {
    if c.ServerKey == "" { logger.Fatal("Server key must be set") }
    if c.MaxMessageSizeBytes < 1 { logger.Fatal("Maximum must be positive") }
}
func ParseArgs(logger Logger, args []string) {
    flags := flag.NewFlagSet("nakama", flag.ExitOnError)
    flags.Parse(args)
}
'''
FLAGS = 'package flags\ntype FlagMakingOptions struct { UseLowerCase bool `yaml:"use_lower_case" usage:"lower"` }\n'
MAIN = '''package main
import "os"
func main() { switch os.Args[1] { case "migrate": os.Exit(0); case "healthcheck": os.Exit(0) } }
'''
MIGRATE = '''package migrate
func Run(command string) { switch command { case "up": return; case "down": return } }
'''


def locked(root: Path, repository: str, revision: str) -> str:
    files = {
        "server/config.go": CONFIG,
        "flags/flags.go": FLAGS,
        "flags/vars.go": "package flags\n",
        "main.go": MAIN,
        "migrate/migrate.go": MIGRATE,
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    tree = git_tree_sha1(root)
    (root / LOCK_FILE).write_text(json.dumps({"repository": repository, "revision": revision, "tree": tree, "verification": "recomputed-git-tree-sha1"}), encoding="utf-8")
    return tree


class ConfigCliDenominatorTests(unittest.TestCase):
    def test_helper_extracts_fields_defaults_validations_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = "1" * 40
            locked(root, "test/nakama", revision)
            items = mod.run_helper(root, mod.SOURCE_PATHS)
            classes = {item["class"] for item in items}
            self.assertTrue({"config_type", "config_field", "config_interface", "config_interface_method", "config_default", "config_default_assignment", "config_validation", "cli_flagset", "cli_parse_event", "cli_case_candidate", "cli_exit_path"} <= classes)
            fields = [item for item in items if item["class"] == "config_field"]
            self.assertTrue(any(item.get("metadata", {}).get("usage") == "Server key" for item in fields))

    def test_full_candidate_is_deterministic_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "source"; root.mkdir()
            revision = "1" * 40
            tree = locked(root, "test/nakama", revision)
            original = (mod.REPOSITORY, mod.COMMIT, mod.TREE)
            mod.REPOSITORY, mod.COMMIT, mod.TREE = "test/nakama", revision, tree
            try:
                a = base / "a"; b = base / "b"
                first = mod.generate(root, a); second = mod.generate(root, b)
                self.assertEqual(first, second)
                for name in ("config-denominator.candidate.json", "cli-denominator.candidate.json", "config-cli-reconciliation.candidate.json"):
                    self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())
                config = json.loads((a / "config-denominator.candidate.json").read_text())
                cli = json.loads((a / "cli-denominator.candidate.json").read_text())
                self.assertEqual(config["unclassified_count"], config["leaf_count"])
                self.assertEqual(cli["unreviewed_count"], cli["leaf_count"])
                self.assertFalse(any(config["claims"].values()))
                self.assertFalse(any(cli["claims"].values()))
                with self.assertRaises(mod.DenominatorError):
                    mod.require_sg1(a)
            finally:
                mod.REPOSITORY, mod.COMMIT, mod.TREE = original

    def test_post_fetch_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "source"; root.mkdir()
            revision = "1" * 40
            tree = locked(root, "test/nakama", revision)
            (root / "main.go").write_text(MAIN + "// tamper\n", encoding="utf-8")
            original = (mod.REPOSITORY, mod.COMMIT, mod.TREE)
            mod.REPOSITORY, mod.COMMIT, mod.TREE = "test/nakama", revision, tree
            try:
                with self.assertRaises(mod.DenominatorError):
                    mod.generate(root, base / "out")
            finally:
                mod.REPOSITORY, mod.COMMIT, mod.TREE = original

    def test_generated_flags_derive_from_yaml_tags_without_promotion(self) -> None:
        item = {"class": "config_field", "symbol": "server.SocketConfig.ServerKey", "signature": "string", "path": "server/config.go", "start_line": 1, "end_line": 1, "metadata": {"yaml": "server_key", "usage": "Server key"}}
        generated = mod.derive_generated_flags([item])
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["metadata"]["yaml"], "server_key")


if __name__ == "__main__":
    unittest.main()
