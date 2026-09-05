#!/usr/bin/env python3
"""Exact World source export and bounded read-only native diagnostics, not acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import zipfile

REPOSITORY = "TrillionniumFoundation/Trillionnium-World"
HEAD = "9a57222d1eacc7059e549df9c62a79046e8ae8ea"
TREE = "57b03d5ae782ee9e1e338afd34842ad8dbb653c9"
TOOLCHAIN = "1.98.1"
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
CLAIMS = dict.fromkeys(("world_required_checks_satisfied", "gap_closed", "independently_reviewed", "production_authorized"), False)


def child_environment() -> dict[str, str]:
    # Product commands receive neither repository nor artifact credentials.
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(CI="true", TERM="dumb", NO_COLOR="1", CARGO_TERM_COLOR="never",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
               PYTHONDONTWRITEBYTECODE="1")
    return env


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   env=child_environment(), timeout=60)


def verify_identity(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("World checkout missing or linked")
    if git(root, "rev-parse", "HEAD").decode().strip() != HEAD:
        raise ValueError("World head changed")
    if git(root, "rev-parse", "HEAD^{tree}").decode().strip() != TREE:
        raise ValueError("World tree changed")
    if git(root, "config", "--get", "remote.origin.url").decode().strip() != f"https://github.com/{REPOSITORY}.git":
        raise ValueError("World origin crossed")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("World tracked/input source changed")
    flags = git(root, "ls-files", "-v", "-z").split(b"\0")
    if any(item and not item.startswith(b"H ") for item in flags):
        raise ValueError("World index flags hide input")
    return {"repository": REPOSITORY, "head": HEAD, "tree": TREE}


def producer() -> dict[str, str]:
    keys = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_ARCH", "RUNNER_OS")
    return {key: os.environ.get(key, "not_available") for key in keys}


def package(output: Path, name: str, files: dict[str, bytes]) -> Path:
    if not files or sum(map(len, files.values())) > MAX_ARCHIVE_BYTES:
        raise ValueError("diagnostic packet size invalid")
    if any("/" in key or "\\" in key or key in {".", "..", "file-index.json"} for key in files):
        raise ValueError("diagnostic packet path invalid")
    index = [{"path": key, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
             for key, data in sorted(files.items())]
    packet = output / f"{name}.zip"
    with zipfile.ZipFile(packet, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for key, data in sorted(files.items()):
            archive.writestr(key, data)
        archive.writestr("file-index.json", json.dumps(index, indent=2) + "\n")
    if packet.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("compressed packet exceeds uploader budget")
    return packet


def export_source(root: Path, output: Path) -> None:
    identity = verify_identity(root)
    raw = git(root, "cat-file", "commit", HEAD)
    actual = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != HEAD or not raw.startswith(f"tree {TREE}\n".encode()):
        raise ValueError("raw commit identity mismatch")
    archive_path = output / "source.tar"
    with archive_path.open("xb") as handle:
        subprocess.run(["git", "-C", str(root), "archive", "--format=tar", HEAD],
                       stdout=handle, env=child_environment(), timeout=90, check=True)
    if not 0 < archive_path.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("source archive exceeds budget")
    verify_identity(root)
    observation = {"schema": "trnm_world_readonly_source_export_v1", **identity,
                   "producer": producer(), "claims": CLAIMS,
                   "scope": "Exact tracked source and raw Git commit; no test, publication or release qualification."}
    files = {"source.tar": archive_path.read_bytes(), "source-tree.z": git(root, "ls-tree", "-rz", "--full-tree", HEAD),
             "source-commit.raw": raw, "observation.json": (json.dumps(observation, indent=2) + "\n").encode()}
    packet = package(output, "world-source-9a57222", files)
    print(json.dumps({"export": packet.name, "sha256": hashlib.sha256(packet.read_bytes()).hexdigest(), "claims": CLAIMS}))


def run_command(root: Path, output: Path, name: str, command: list[str], seconds: int,
                extra_env: dict[str, str] | None = None) -> dict:
    env = child_environment()
    env.update(extra_env or {})
    logfile = output / f"{name}.log"
    started = time.monotonic()
    reason = "exited"
    with logfile.open("xb") as log:
        try:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as error:
            log.write(f"Command unavailable: {type(error).__name__}\n".encode())
            return {"name": name, "argv": command, "exit_code": 127, "reason": "unavailable", "log": logfile.name}
        while process.poll() is None:
            if time.monotonic() - started > seconds:
                reason = "timeout"
            elif logfile.stat().st_size > MAX_LOG_BYTES:
                reason = "log_budget"
            if reason != "exited":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            time.sleep(0.2)
        code = process.wait(timeout=15)
    # Preserve a bounded prefix and explicit budget failure, never a passing truncated log.
    if logfile.stat().st_size > MAX_LOG_BYTES:
        reason = "log_budget"
        with logfile.open("r+b") as log:
            log.truncate(MAX_LOG_BYTES)
    return {"name": name, "argv": command, "exit_code": code,
            "reason": reason, "log": logfile.name, "elapsed_seconds": round(time.monotonic() - started, 3)}


def run_native(root: Path, output: Path) -> bool:
    verify_identity(root)
    results = []
    build_env = {"CARGO_TARGET_DIR": str(output / "cargo-target"), "CARGO_BUILD_JOBS": "4"}
    results.append(run_command(root, output, "project-preflight", ["bash", "scripts/project-preflight.sh"], 120))
    if results[-1]["exit_code"] == 0 and results[-1]["reason"] == "exited":
        results.append(run_command(root, output, "install-toolchain",
                                   ["rustup", "toolchain", "install", TOOLCHAIN, "--profile", "minimal",
                                    "--component", "rustfmt", "--component", "clippy"], 300))
        if results[-1]["exit_code"] == 0 and results[-1]["reason"] == "exited":
            matrix = [
                ("rustc-version", ["rustc", f"+{TOOLCHAIN}", "--version", "--verbose"], 30),
                ("format-check", ["cargo", f"+{TOOLCHAIN}", "fmt", "--manifest-path", "trillionnium/Cargo.toml", "--all", "--", "--check"], 120),
                ("cli-tests", ["cargo", f"+{TOOLCHAIN}", "test", "--manifest-path", "trillionnium/Cargo.toml", "-p", "trnm-game-server", "--bin", "trnm-online-e2e", "--bin", "trnm-moderation-console", "--locked"], 840),
                ("strict-clippy", ["cargo", f"+{TOOLCHAIN}", "clippy", "--manifest-path", "trillionnium/Cargo.toml", "-p", "trnm-game-server", "--all-targets", "--locked", "--", "-D", "warnings"], 600),
            ]
            for name, command, budget in matrix:
                results.append(run_command(root, output, name, command, budget, build_env))
    source_unchanged = False
    try:
        verify_identity(root)
        source_unchanged = True
    except (ValueError, subprocess.SubprocessError):
        pass
    passed = len(results) == 6 and source_unchanged and all(row["exit_code"] == 0 and row["reason"] == "exited" for row in results)
    report = {"schema": "trnm_world_readonly_native_diagnostic_v1", "repository": REPOSITORY,
              "head": HEAD, "tree": TREE, "diagnostic_toolchain": TOOLCHAIN,
              "producer": producer(), "commands": results, "selected_matrix_passed": passed,
              "tracked_source_unchanged": source_unchanged, "claims": CLAIMS,
              "limitations": ["External diagnostic relay, not World required CI or prospective merge qualification.",
                              "Historical 1.98.0 artifact is unchanged; this corrected-toolchain run has independent identity.",
                              "No fixed-source import, database, native GUI, custody, deployment or independent acceptance."]}
    report_bytes = (json.dumps(report, indent=2) + "\n").encode()
    (output / "report.json").write_bytes(report_bytes)
    files = {row["log"]: (output / row["log"]).read_bytes() for row in results}
    files["report.json"] = report_bytes
    package(output, "world-native-9a57222", files)
    print(json.dumps({"selected_matrix_passed": passed, "command_results": [(r["name"], r["exit_code"], r["reason"]) for r in results], "claims": CLAIMS}))
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "run", "finish"))
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.output.is_symlink() or args.output.resolve().is_relative_to(args.root.resolve()):
        raise ValueError("outputs must be outside product checkout")
    if args.mode == "export":
        export_source(args.root, args.output)
    elif args.mode == "run":
        # The finish step enforces failures after diagnostic logs have uploaded.
        run_native(args.root, args.output)
    else:
        report = json.loads((args.output / "report.json").read_text())
        return 0 if report.get("selected_matrix_passed") is True else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
