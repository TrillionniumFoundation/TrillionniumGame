from __future__ import annotations

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply-transition-result-layout-compat.py <world-root>")

    root = Path(sys.argv[1]).resolve()
    source = (
        root
        / "trillionnium/contracts/trnm-world-transition-v1/src/lib.rs"
    )
    text = source.read_text(encoding="utf-8")
    old = '''#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WorldTransitionResultV1 {
'''
    new = '''// The accepted result intentionally owns its complete authoritative material
// inline. Boxing it would add a heap allocation to every successful transition
// and change the public Rust API while leaving the canonical wire payload
// unchanged, so the size asymmetry is an explicit contract-level tradeoff.
#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WorldTransitionResultV1 {
'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"transition result layout source drifted: {count}")
    source.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("transition result inline-layout contract: PASS")


if __name__ == "__main__":
    main()
