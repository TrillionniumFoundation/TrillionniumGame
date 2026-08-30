#!/usr/bin/env python3
"""Export a deterministic, directly compiled World game-server source patch.

This tool is evidence-only. It edits only an ephemeral exact World checkout and
never commits, pushes, tags, merges, deploys, or promotes source.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

WORLD_REPOSITORY = "TrillionniumFoundation/Trillionnium-World"
WORLD_COMMIT = "cb662abffce882aaeed8ec30402e889ba235d786"
WORLD_TREE = "7a9be1f6859f5f57e4262b047e7b0243d384b9c2"
WORLD_TEMPLATE_BLOB = "8882f47db55ca5993329594901c828c6faf325a8"
CRATE_REL = Path("trillionnium/crates/trnm-game-server")
BODY_MARKER = "use axum::extract::DefaultBodyLimit;\n"

REQUIRED = (
    "const MIGRATION_V16:",
    "const MIGRATION_V17:",
    "const MIGRATION_V18:",
    "const MIGRATION_V19:",
    '(16, "0016_online_settlement_outbox_v1", MIGRATION_V16)',
    '"0017_online_settlement_worker_runtime_v1"',
    '"0018_online_settlement_operator_controls_v1"',
    '"0019_online_settlement_quarantine_v1"',
    "terminal settlement is owned by trnm-settlement-worker; in-process settlement is prohibited",
)
FORBIDDEN = (
    'include!(concat!(env!("OUT_DIR")',
    "trnm_game_server_lib_generated.rs",
    "reconcile_economy(&state.cex",
    "settle_pending_matches(&settlement_state",
)


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=None if env is None else {**os.environ, **env},
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compose_direct_source(template: str, generated: str) -> str:
    if template.count(BODY_MARKER) != 1 or not generated.startswith(BODY_MARKER):
        raise RuntimeError("reviewed source body marker drifted")
    direct = template[: template.index(BODY_MARKER)] + generated
    direct, count = re.subn(
        r'include_str!\(concat!\(::std::env!\("CARGO_MANIFEST_DIR"\), "/migrations/([^"]+)"\)\)',
        r'include_str!("../migrations/\1")',
        direct,
    )
    if count < 19:
        raise RuntimeError(f"expected at least 19 migration include rewrites, observed {count}")
    for marker in REQUIRED:
        if marker not in direct:
            raise RuntimeError(f"direct source omitted required marker: {marker}")
    for marker in FORBIDDEN:
        if marker in direct:
            raise RuntimeError(f"direct source retained forbidden marker: {marker}")
    return direct


def rewrite_boundary_test() -> str:
    return '''use std::path::Path;

const GAME_SERVER_ENTRYPOINT: &str = include_str!("../src/lib.rs");
const SETTLEMENT_WORKER_ENTRYPOINT: &str = include_str!("../src/settlement_worker.rs");
const SETTLEMENT_WORKER_LEGACY: &str = include_str!("../src/settlement_worker_legacy.rs");
const SETTLEMENT_WORKER_RUNTIME_V2: &str =
    include_str!("../src/settlement_worker_runtime_v2.rs");

#[test]
fn game_server_is_direct_source_and_does_not_execute_terminal_economy_settlement() {
    let crate_root = Path::new(env!("CARGO_MANIFEST_DIR"));
    assert!(!crate_root.join("build.rs").exists());
    assert!(!crate_root.join("src/lib.rs.in").exists());
    assert!(!GAME_SERVER_ENTRYPOINT.contains("OUT_DIR"));
    assert!(!GAME_SERVER_ENTRYPOINT.contains("trnm_game_server_lib_generated.rs"));
    assert!(!GAME_SERVER_ENTRYPOINT.contains("reconcile_economy(&state.cex"));
    assert!(!GAME_SERVER_ENTRYPOINT.contains("settle_pending_matches(&settlement_state"));
    assert!(GAME_SERVER_ENTRYPOINT.contains(
        "terminal settlement is owned by trnm-settlement-worker; in-process settlement is prohibited"
    ));
}

#[test]
fn direct_runtime_entrypoints_register_the_complete_settlement_migration_chain() {
    assert!(!SETTLEMENT_WORKER_ENTRYPOINT.contains("OUT_DIR"));
    assert!(!SETTLEMENT_WORKER_ENTRYPOINT.contains("trnm_settlement_worker_generated.rs"));
    let worker = format!("{SETTLEMENT_WORKER_LEGACY}\\n{SETTLEMENT_WORKER_RUNTIME_V2}");
    for marker in [
        "0016_online_settlement_outbox_v1",
        "0017_online_settlement_worker_runtime_v1",
        "0018_online_settlement_operator_controls_v1",
        "0019_online_settlement_quarantine_v1",
    ] {
        assert!(GAME_SERVER_ENTRYPOINT.contains(marker), "direct game server lost {marker}");
        assert!(worker.contains(marker), "direct settlement worker lost {marker}");
    }
}

#[test]
fn directly_compiled_migration_includes_are_source_relative() {
    for source in [
        GAME_SERVER_ENTRYPOINT,
        SETTLEMENT_WORKER_LEGACY,
        SETTLEMENT_WORKER_RUNTIME_V2,
    ] {
        assert!(source.contains("include_str!(\\\"../migrations/"));
        assert!(!source.contains("CARGO_MANIFEST_DIR"));
    }
}
'''


def rewrite_worker_contract(source: str) -> str:
    source, count = re.subn(
        r'const BUILD_SCRIPT: &str = include_str!\("\.\./build\.rs"\);\n', "", source
    )
    if count != 1:
        raise RuntimeError(f"worker build-script constant drifted: {count}")
    replacement = '''#[test]
fn settlement_worker_is_directly_compiled_from_reviewed_modules() {
    assert!(WORKER_WRAPPER.contains("settlement_worker_legacy.rs"));
    assert!(WORKER_WRAPPER.contains("settlement_worker_runtime_v2.rs"));
    assert!(WORKER_WRAPPER.contains("run_v2 as run"));
    assert!(!WORKER_WRAPPER.contains("OUT_DIR"));
    assert!(!WORKER_WRAPPER.contains("trnm_settlement_worker_generated.rs"));

    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert!(!crate_root.join("build.rs").exists());
    assert!(!crate_root.join("src/lib.rs.in").exists());
    assert!(!crate_root.join("src/settlement_worker.rs.in").exists());
}
'''
    source, count = re.subn(
        r'#\[test\]\nfn settlement_worker_is_directly_compiled_from_reviewed_modules\(\) \{.*?\n\}\n(?=\n#\[test\])',
        replacement.rstrip() + "\n",
        source,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError(f"worker direct-source test drifted: {count}")
    if "BUILD_SCRIPT" in source or 'include_str!("../build.rs")' in source:
        raise RuntimeError("worker contract still depends on semantic build source")
    return source


def export(repo: Path, output: Path) -> None:
    repo = repo.resolve()
    output = output.resolve()
    crate = repo / CRATE_REL
    output.mkdir(parents=True, exist_ok=True)
    generated_dir = output / "generated"
    generated_dir.mkdir(exist_ok=True)

    if run(["git", "rev-parse", "HEAD"], cwd=repo) != WORLD_COMMIT:
        raise RuntimeError("World commit drifted")
    if run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo) != WORLD_TREE:
        raise RuntimeError("World tree drifted")
    if run(["git", "status", "--porcelain"], cwd=repo):
        raise RuntimeError("World checkout is dirty before export")
    if run(["git", "hash-object", str(CRATE_REL / "src/lib.rs.in")], cwd=repo) != WORLD_TEMPLATE_BLOB:
        raise RuntimeError("World template blob drifted")

    run(["bash", "scripts/project-preflight.sh", "--audit"], cwd=repo)
    materializer = output / "materialize-game-server"
    run(["rustc", "--edition=2021", str(crate / "build.rs"), "-o", str(materializer)], cwd=repo)
    run(
        [str(materializer)],
        cwd=crate,
        env={"OUT_DIR": str(generated_dir), "CARGO_MANIFEST_DIR": str(crate)},
    )

    template_path = crate / "src/lib.rs.in"
    generated_path = generated_dir / "trnm_game_server_lib_generated.rs"
    direct = compose_direct_source(
        template_path.read_text(encoding="utf-8").replace("\r\n", "\n"),
        generated_path.read_text(encoding="utf-8").replace("\r\n", "\n"),
    )
    (crate / "src/lib.rs").write_text(direct, encoding="utf-8", newline="\n")
    template_path.unlink()
    (crate / "build.rs").unlink()

    cargo_path = crate / "Cargo.toml"
    cargo = cargo_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    cargo, count = re.subn(r'(?m)^build = "build\.rs"\n', "", cargo)
    if count != 1:
        raise RuntimeError(f"Cargo build declaration drifted: {count}")
    cargo_path.write_text(cargo, encoding="utf-8", newline="\n")

    boundary_path = crate / "tests/settlement_game_server_boundary.rs"
    boundary_path.write_text(rewrite_boundary_test(), encoding="utf-8", newline="\n")
    worker_path = crate / "tests/settlement_worker_contract.rs"
    worker_path.write_text(
        rewrite_worker_contract(worker_path.read_text(encoding="utf-8").replace("\r\n", "\n")),
        encoding="utf-8",
        newline="\n",
    )

    run(["rustfmt", "--edition", "2021", str(crate / "src/lib.rs")], cwd=repo)
    run(
        ["rustfmt", "--edition", "2021", str(boundary_path), str(worker_path)], cwd=repo
    )

    patch = run(["git", "diff", "--binary"], cwd=repo)
    if not patch:
        raise RuntimeError("direct-source patch is empty")
    (output / "direct-source.patch").write_text(patch + "\n", encoding="utf-8")
    shutil.copy2(crate / "src/lib.rs", output / "lib.rs")
    shutil.copy2(cargo_path, output / "Cargo.toml")
    shutil.copy2(boundary_path, output / boundary_path.name)
    shutil.copy2(worker_path, output / worker_path.name)
    direct_blob = run(["git", "hash-object", str(crate / "src/lib.rs")], cwd=repo)
    (output / "lib.rs.git-blob").write_text(direct_blob + "\n", encoding="utf-8")
    (output / "identity.txt").write_text(
        f"repository={WORLD_REPOSITORY}\ncommit={WORLD_COMMIT}\ntree={WORLD_TREE}\n"
        f"template_blob={WORLD_TEMPLATE_BLOB}\ndirect_lib_blob={direct_blob}\n",
        encoding="utf-8",
    )

    files = sorted(path for path in output.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.repo, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
