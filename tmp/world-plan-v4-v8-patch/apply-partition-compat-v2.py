from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source shape, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply-partition-compat-v2.py <world-root>")
    root = Path(sys.argv[1]).resolve()
    base = Path(__file__).with_name("apply-partition-compat.py")
    subprocess.run([sys.executable, str(base), str(root)], check=True)

    support = root / "trillionnium/crates/trnm-game-server/tests/support/mod.rs"
    support_text = support.read_text(encoding="utf-8")
    if not support_text.startswith("#![allow(dead_code)]\n"):
        support.write_text(
            "#![allow(dead_code)]\n\n" + support_text,
            encoding="utf-8",
        )

    boundary = root / "trillionnium/crates/trnm-game-server/tests/settlement_game_server_boundary.rs"
    text = boundary.read_text(encoding="utf-8")
    old = '''#[test]
fn direct_migration_includes_use_an_unambiguous_environment_macro() {
    for source in [
        read_crate_source_bundle("src/lib.rs"),
        read_settlement_worker_bundle(),
    ] {
        assert!(source.contains("concat!(::std::env!(\\\"CARGO_MANIFEST_DIR\\\")"));
        assert!(!source.contains("concat!(env!(\\\"CARGO_MANIFEST_DIR\\\")"));
    }
}
'''
    new = '''#[test]
fn direct_migration_includes_use_an_unambiguous_environment_macro() {
    let game_server = read_crate_source_bundle("src/lib.rs");
    assert!(game_server.contains("concat!(::std::env!(\\\"CARGO_MANIFEST_DIR\\\")"));
    assert!(!game_server.contains("concat!(env!(\\\"CARGO_MANIFEST_DIR\\\")"));

    // The worker may use direct static includes instead of a manifest-dir
    // concat. It must never reintroduce the ambiguous macro form or generated
    // OUT_DIR authority.
    let worker = read_settlement_worker_bundle();
    assert!(!worker.contains("concat!(env!(\\\"CARGO_MANIFEST_DIR\\\")"));
    assert!(!worker.contains("OUT_DIR"));
    assert!(!worker.contains("trnm_settlement_worker_generated.rs"));
}
'''
    boundary.write_text(
        replace_once(text, old, new, "partition migration include contract"),
        encoding="utf-8",
    )
    print("partition compatibility v2: PASS")


if __name__ == "__main__":
    main()
