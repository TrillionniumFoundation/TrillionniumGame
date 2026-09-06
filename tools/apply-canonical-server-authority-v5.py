#!/usr/bin/env python3
"""Prepare and harden the v4 canonical-server migration without weakening gates."""
from __future__ import annotations

import ast
import json
import pprint
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
TOOLING = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent
OLD_BINARY = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
PERSISTENCE_MANIFEST = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
SERVER_MANIFEST = ROOT / "crates/trnm-server/Cargo.toml"


def write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def ensure_explicit_retirement_anchor() -> None:
    if not OLD_BINARY.is_file():
        raise SystemExit("old persistence-owned server binary is missing before atomic migration")
    text = PERSISTENCE_MANIFEST.read_text(encoding="utf-8")
    if 'name = "trnm-server"' not in text:
        text = text.rstrip() + (
            "\n\n[[bin]]\n"
            'name = "trnm-server"\n'
            'path = "src/bin/trnm-server.rs"\n'
        )
        write(PERSISTENCE_MANIFEST, text)


def add_local_import_path(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "from trnm_server_authority import" not in text:
        return
    marker = "from pathlib import Path\n"
    if marker not in text:
        marker = "import sys\n"
    injection = (
        "\nSCRIPT_DIR = Path(__file__).resolve().parent\n"
        "if str(SCRIPT_DIR) not in sys.path:\n"
        "    sys.path.insert(0, str(SCRIPT_DIR))\n"
    )
    if "sys.path.insert(0, str(SCRIPT_DIR))" in text:
        return
    if "from pathlib import Path" not in text:
        text = text.replace("import sys\n", "import sys\nfrom pathlib import Path\n", 1)
    import_line = next(
        line for line in text.splitlines() if line.startswith("from trnm_server_authority import")
    )
    text = text.replace(import_line, injection + "\n" + import_line, 1)
    write(path, text)


def source_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def replace_ast_mapping_entry(
    path: Path,
    variable: str,
    key: str,
    replacement: Any,
) -> bool:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    target: ast.AST | None = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names: list[str] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            names = [item.id for item in node.targets if isinstance(item, ast.Name)]
            value = node.value
        elif isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        if variable not in names or not isinstance(value, ast.Dict):
            continue
        for map_key, map_value in zip(value.keys, value.values):
            if isinstance(map_key, ast.Constant) and map_key.value == key:
                target = map_value
                break
    if target is None or not hasattr(target, "end_lineno"):
        return False
    offsets = source_offsets(text)
    start = offsets[target.lineno - 1] + target.col_offset
    end = offsets[target.end_lineno - 1] + target.end_col_offset
    rendered = pprint.pformat(replacement, sort_dicts=True, width=100)
    if "\n" in rendered:
        indent = " " * target.col_offset
        rendered = rendered.replace("\n", "\n" + indent)
    write(path, text[:start] + rendered + text[end:])
    return True


def synchronize_foundation_dependency_authority() -> None:
    path = ROOT / "scripts/check-rust-foundation.py"
    if not path.is_file():
        raise SystemExit("missing scripts/check-rust-foundation.py")
    manifest = tomllib.loads(SERVER_MANIFEST.read_text(encoding="utf-8"))
    dependencies = manifest.get("dependencies", {})
    build_dependencies = manifest.get("build-dependencies", {})
    replaced = replace_ast_mapping_entry(
        path, "EXPECTED_DEPENDENCIES", "crates/trnm-server", dependencies
    )
    if not replaced:
        text = path.read_text(encoding="utf-8")
        if "crates/trnm-server" in text and "EXPECTED_DEPENDENCIES" in text:
            raise SystemExit("could not update server dependency authority")
    replace_ast_mapping_entry(
        path,
        "EXPECTED_BUILD_DEPENDENCIES",
        "crates/trnm-server",
        build_dependencies,
    )


def install_source_candidate_test() -> None:
    path = ROOT / "tests/control_plane/test_rust_server_source_candidate.py"
    if not path.exists():
        return
    write(
        path,
        '''from __future__ import annotations
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-rust-server-source-candidate.py"
OLD_BINARY = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
OLD_RUNTIME = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server"


class CanonicalRustServerSourceCandidateTests(unittest.TestCase):
    def test_source_checker_validates_the_complete_canonical_runtime(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CHECKER)], cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema"], "trillionnium.server-source-check.v2")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["binary"], "trnm-server")
        self.assertGreaterEqual(result["source_marker_count"], 20)
        self.assertFalse(any(
            result["claims"][key]
            for key in ("compiled", "live_process_executed", "live_database_bound",
                        "wire_compatible", "production_ready")
        ))

    def test_old_persistence_process_root_is_absent(self) -> None:
        self.assertFalse(OLD_BINARY.exists())
        self.assertFalse(OLD_RUNTIME.exists())


if __name__ == "__main__":
    unittest.main()
''',
    )


def install_authority_regression_test() -> None:
    path = ROOT / "tests/control_plane/test_canonical_server_authority.py"
    write(
        path,
        '''from __future__ import annotations
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-trnm-server.py"
MANIFEST = ROOT / "crates/trnm-server/Cargo.toml"
AUTHORITY = ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json"


class CanonicalServerAuthorityRegressionTests(unittest.TestCase):
    def test_single_authority_checker_passes(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CHECKER)], cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["binary"], "trnm-server")
        self.assertEqual(result["source"], "crates/trnm-server/src/main.rs")
        self.assertGreaterEqual(result["source_test_count"], 60)
        self.assertFalse(result["compatibility_credit"])
        self.assertFalse(result["production_ready"])

    def test_manifest_and_machine_authority_are_identical(self) -> None:
        manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["bin"], [{"name": "trnm-server", "path": "src/main.rs"}])
        authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
        self.assertEqual(authority["server_binary_authority"]["manifest"],
                         "crates/trnm-server/Cargo.toml")
        self.assertEqual(authority["server_binary_authority"]["source"],
                         "crates/trnm-server/src/main.rs")


if __name__ == "__main__":
    unittest.main()
''',
    )


def repair_known_module_import_tests() -> None:
    for relative in (
        "scripts/check-trnm-server.py",
        "scripts/check-rust-server-slice.py",
        "scripts/check-rust-server-source-candidate.py",
    ):
        add_local_import_path(ROOT / relative)


def verify() -> None:
    if OLD_BINARY.exists():
        raise SystemExit("old server binary survived hardened migration")
    if (ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server").exists():
        raise SystemExit("old server module tree survived hardened migration")
    authority = json.loads(
        (ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json").read_text(encoding="utf-8")
    )
    server = authority.get("server_binary_authority", {})
    if server.get("manifest") != "crates/trnm-server/Cargo.toml":
        raise SystemExit("machine authority did not converge")


def main() -> int:
    ensure_explicit_retirement_anchor()
    base = TOOLING / "apply-canonical-server-authority-v4.py"
    if not base.is_file():
        base = TOOLING / "tools/apply-canonical-server-authority-v4.py"
    if not base.is_file():
        raise SystemExit(f"missing v4 transformer under {TOOLING}")
    subprocess.run([sys.executable, str(base), str(ROOT)], check=True)
    repair_known_module_import_tests()
    synchronize_foundation_dependency_authority()
    install_source_candidate_test()
    install_authority_regression_test()
    verify()
    print("canonical server authority v5 controls: synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
