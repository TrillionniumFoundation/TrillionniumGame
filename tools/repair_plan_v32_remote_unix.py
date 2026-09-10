#!/usr/bin/env python3
"""Repair duplicate Unix module gating in the materialized MAC transport.

The immutable historical generator emits both an outer `#[cfg(unix)]` on the
module declaration and an inner `#![cfg(unix)]` in the module body. Rust accepts
that shape, but strict Clippy rejects it as a duplicated attribute. Preserve the
outer compile gate and gated re-export, remove only the duplicate inner
attribute, and strengthen the generated checker/test so either outer gate being
removed fails closed.
"""
from __future__ import annotations

import argparse
import py_compile
from pathlib import Path


class RepairError(RuntimeError):
    pass


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RepairError(f"expected exactly one {label}, found {count}")
    return text.replace(old, new, 1)


def repair(root: Path) -> None:
    source_path = root / "crates/trnm-token-crypto-provider/src/remote_unix.rs"
    lib_path = root / "crates/trnm-token-crypto-provider/src/lib.rs"
    checker_path = root / "scripts/check-remote-mac-unix-transport.py"
    test_path = root / "tests/control_plane/test_remote_mac_unix_transport.py"

    source = source_path.read_text(encoding="utf-8")
    prefix = "#![cfg(unix)]\n\n"
    if not source.startswith(prefix):
        raise RepairError("generated remote_unix.rs does not have the exact duplicate inner gate")
    source = source[len(prefix) :]
    if "#![cfg(unix)]" in source:
        raise RepairError("remote_unix.rs retains an unexpected inner Unix gate")
    source_path.write_text(source, encoding="utf-8")

    lib = lib_path.read_text(encoding="utf-8")
    module_gate = "#[cfg(unix)]\nmod remote_unix;"
    export_gate = "#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;"
    if lib.count(module_gate) != 1 or lib.count(export_gate) != 1:
        raise RepairError("outer Unix module/export gates are not exact and singular")

    checker = checker_path.read_text(encoding="utf-8")
    checker = replace_once(
        checker,
        '  require("mod remote_unix;" in lib,"Unix module not compiled")\n'
        '  require("UnixSocketRemoteMacTransport" in lib,"Unix transport not exported")',
        '  require("#[cfg(unix)]\\nmod remote_unix;" in lib,"Unix module not gated")\n'
        '  require("#[cfg(unix)]\\npub use remote_unix::UnixSocketRemoteMacTransport;" in lib,"Unix export not gated")',
        "generated Unix checker gate contract",
    )
    checker_path.write_text(checker, encoding="utf-8")

    test = test_path.read_text(encoding="utf-8")
    test = replace_once(
        test,
        "  def test_cli_passes(self):\n",
        "  def test_unix_module_and_export_gates_fail_closed(self):\n"
        "    ungated_module=self.lib.replace(\"#[cfg(unix)]\\nmod remote_unix;\",\"mod remote_unix;\",1)\n"
        "    with self.assertRaisesRegex(self.checker.ValidationError,\"module not gated\"): self.checker.validate(self.source,ungated_module)\n"
        "    ungated_export=self.lib.replace(\"#[cfg(unix)]\\npub use remote_unix::UnixSocketRemoteMacTransport;\",\"pub use remote_unix::UnixSocketRemoteMacTransport;\",1)\n"
        "    with self.assertRaisesRegex(self.checker.ValidationError,\"export not gated\"): self.checker.validate(self.source,ungated_export)\n"
        "  def test_cli_passes(self):\n",
        "generated Unix hostile test insertion point",
    )
    test_path.write_text(test, encoding="utf-8")

    py_compile.compile(str(checker_path), doraise=True)
    py_compile.compile(str(test_path), doraise=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    arguments = parser.parse_args()
    repair(arguments.root.resolve())
