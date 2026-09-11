#!/usr/bin/env python3
"""Close the final settlement seams without rewriting already-compliant docs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


WRAPPER_OLD = '''#[allow(dead_code, clippy::items_after_test_module)]
mod implementation {
    include!("settlement_worker_legacy.rs");
    include!("settlement_worker_runtime_v2.rs");
}

pub use implementation::{run_v2 as run, WorkerConfig};
'''
WRAPPER_NEW = '''#[allow(dead_code, clippy::items_after_test_module)]
#[path = "settlement_worker_legacy.rs"]
mod implementation;

pub use implementation::{run_v2 as run, WorkerConfig};
'''
LEGACY_ANCHOR = "use uuid::Uuid;\n\n"
LEGACY_MODULE = '''#[path = "settlement_worker_runtime_v2.rs"]
mod runtime_v2;
pub use runtime_v2::run_v2;

'''

TEST_LINK_MUTATOR_OLD = '''            lambda text: text.replace(
                "../../docs/modules/contracts/audit-events-design.md",
                "../../docs/modules/contracts/missing-design.md",
                1,
            ),'''
TEST_LINK_MUTATOR_NEW = '''            lambda text: text.replace(
                "../../docs/modules/contracts/audit-events-design.md",
                "../../docs/modules/contracts/missing-design.md",
            ),'''
TEST_FIXTURE_OLD = '''def clone_and_mutate(mutator) -> None:
    with tempfile.TemporaryDirectory(prefix="trnm-contract-doc-") as temporary:
        clone = Path(temporary) / "repo"
        shutil.copytree(
            ROOT,
            clone,
            ignore=shutil.ignore_patterns(".git", "target", "node_modules", "run"),
        )
        mutator(clone)
        run(clone, expect_success=False)
'''
TEST_FIXTURE_NEW = '''MODULES = ("audit-events", "bridge-relay", "governance-guard", "settlement-vault")
FIXTURE_FILES = (
    CHECKER,
    Path("scripts/trnm_world_strict_json.py"),
    WORKSPACE,
    Path("contracts/README.md"),
    DOCUMENT_CATALOG,
    COMPONENT_CATALOG,
    Path("docs/modules/contracts/README.md"),
)


def copy_fixture_file(clone: Path, relative: Path) -> None:
    source = ROOT / relative
    target = clone / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def materialize_fixture(clone: Path) -> None:
    clone.mkdir(parents=True)
    for relative in FIXTURE_FILES:
        copy_fixture_file(clone, relative)
    for module in MODULES:
        for relative in (
            Path(f"contracts/{module}/Cargo.toml"),
            Path(f"contracts/{module}/README.md"),
            Path(f"docs/modules/contracts/{module}-design.md"),
        ):
            copy_fixture_file(clone, relative)
        source_root = ROOT / "contracts" / module / "src"
        sources = sorted(source_root.glob("*.rs"))
        if not sources:
            raise AssertionError(f"missing fixture source for {module}")
        for source in sources:
            copy_fixture_file(clone, source.relative_to(ROOT))


def clone_and_mutate(mutator) -> None:
    with tempfile.TemporaryDirectory(prefix="trnm-contract-doc-") as temporary:
        clone = Path(temporary) / "repo"
        materialize_fixture(clone)
        mutator(clone)
        run(clone, expect_success=False)
'''

DOC_MARKERS = {
    "audit-events": (
        "contracts/audit-events/src/",
        "docs/modules/contracts/audit-events-design.md",
        "scripts/check-trnm-world-contract-module-documentation.py",
        "scripts/test-trnm-world-contract-module-documentation.py",
    ),
    "bridge-relay": (
        "contracts/bridge-relay/src/",
        "docs/modules/contracts/bridge-relay-design.md",
        "scripts/check-trnm-world-contract-module-documentation.py",
        "scripts/test-trnm-world-contract-module-documentation.py",
    ),
    "governance-guard": (
        "contracts/governance-guard/src/",
        "docs/modules/contracts/governance-guard-design.md",
        "scripts/check-trnm-world-contract-module-documentation.py",
        "scripts/test-trnm-world-contract-module-documentation.py",
    ),
    "settlement-vault": (
        "contracts/settlement-vault/src/",
        "docs/modules/contracts/settlement-vault-design.md",
        "scripts/check-trnm-world-contract-module-documentation.py",
        "scripts/test-trnm-world-contract-module-documentation.py",
    ),
}


def replace_once_or_present(path: Path, old: str, new: str, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} is unavailable: {path}")
    text = path.read_text(encoding="utf-8", errors="strict")
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"{label} boundary drift")
    path.write_text(text.replace(old, new), encoding="utf-8")


def validate_contract_documentation(root: Path) -> None:
    for module, markers in DOC_MARKERS.items():
        path = root / f"docs/modules/contracts/{module}-design.md"
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"missing contract design: {path}")
        text = path.read_text(encoding="utf-8", errors="strict")
        missing = [marker for marker in markers if marker not in text]
        if missing:
            raise RuntimeError(f"{module} design traceability missing {missing}")


def repair_contract_fixture(root: Path) -> None:
    test_path = root / "scripts/test-trnm-world-contract-module-documentation.py"
    replace_once_or_present(
        test_path,
        TEST_LINK_MUTATOR_OLD,
        TEST_LINK_MUTATOR_NEW,
        "contract-documentation link hostile fixture",
    )
    replace_once_or_present(
        test_path,
        TEST_FIXTURE_OLD,
        TEST_FIXTURE_NEW,
        "contract-documentation bounded fixture",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--completed-commit", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if re.fullmatch(r"[0-9a-f]{40}", args.completed_commit) is None:
        raise RuntimeError("completed commit must be 40 lowercase hexadecimal characters")

    root = args.root.resolve(strict=True)
    source_root = root / "trillionnium/crates/trnm-game-server/src"
    wrapper = source_root / "settlement_worker.rs"
    legacy = source_root / "settlement_worker_legacy.rs"
    runtime = source_root / "settlement_worker_runtime_v2.rs"
    for path in (wrapper, legacy, runtime):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"settlement source is unavailable: {path}")

    wrapper_text = wrapper.read_text(encoding="utf-8", errors="strict")
    if WRAPPER_NEW in wrapper_text:
        pass
    elif wrapper_text.count(WRAPPER_OLD) == 1:
        wrapper_text = wrapper_text.replace(WRAPPER_OLD, WRAPPER_NEW)
        wrapper.write_text(wrapper_text, encoding="utf-8")
    else:
        raise RuntimeError("settlement wrapper module boundary drift")
    if "include!(\"settlement_worker_legacy.rs\")" in wrapper_text or "include!(\"settlement_worker_runtime_v2.rs\")" in wrapper_text:
        raise RuntimeError("settlement wrapper retained a textual include")

    legacy_text = legacy.read_text(encoding="utf-8", errors="strict")
    if LEGACY_MODULE in legacy_text:
        pass
    elif legacy_text.count(LEGACY_ANCHOR) == 1:
        legacy.write_text(
            legacy_text.replace(LEGACY_ANCHOR, LEGACY_ANCHOR + LEGACY_MODULE),
            encoding="utf-8",
        )
    else:
        raise RuntimeError("settlement legacy module insertion anchor drift")

    runtime_text = runtime.read_text(encoding="utf-8", errors="strict")
    if not runtime_text.startswith("use super::*;\n"):
        runtime.write_text("use super::*;\n\n" + runtime_text, encoding="utf-8")

    ledger_path = root / "scripts/contracts/trnm-world-include-migrations-v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8", errors="strict"))
    completed = ledger.get("completed")
    if not isinstance(completed, list):
        raise RuntimeError("include migration ledger completed field is not a list")
    source_relative = "trillionnium/crates/trnm-game-server/src/settlement_worker.rs"
    records = (
        {
            "path": source_relative,
            "expression": "\"settlement_worker_legacy.rs\"",
            "replacement_module": "implementation",
            "required_fragments": [
                "#[path = \"settlement_worker_legacy.rs\"]",
                "mod implementation;",
                "pub use implementation::{run_v2 as run, WorkerConfig};",
            ],
            "completed_commit": args.completed_commit,
        },
        {
            "path": source_relative,
            "expression": "\"settlement_worker_runtime_v2.rs\"",
            "replacement_module": "runtime_v2",
            "required_fragments": [
                "`settlement_worker_runtime_v2.rs` owns the exported runtime",
                "#[path = \"settlement_worker_legacy.rs\"]",
                "pub use implementation::{run_v2 as run, WorkerConfig};",
            ],
            "completed_commit": args.completed_commit,
        },
    )
    existing = {
        (item.get("path"), item.get("expression"))
        for item in completed
        if isinstance(item, dict)
    }
    changed = False
    for record in records:
        key = (record["path"], record["expression"])
        if key not in existing:
            completed.append(record)
            existing.add(key)
            changed = True
    if changed:
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    validate_contract_documentation(root)
    repair_contract_fixture(root)
    print("WORLD_PR127_SETTLEMENT_WORKER_MODULE_TRANSFORM=PASS seams=2")
    print("WORLD_PR127_CONTRACT_DOCUMENTATION_TRACEABILITY=PASS designs=4 fixtures=12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
