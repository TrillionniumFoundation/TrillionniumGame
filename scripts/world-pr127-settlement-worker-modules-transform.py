#!/usr/bin/env python3
"""Close the final settlement module seams and exact contract-doc trace gaps."""
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

AUDIT_TRACE_OLD = (
    "Primary source is `contracts/audit-events/src/`. The workspace boundary and honest "
    "status are defined by `contracts/README.md` and this index. Tests must cover "
    "deterministic round trips, stable spellings, duplicate/oversized/unsupported values, "
    "secret-like field rejection where supported, exact duplicate identity and cross-crate "
    "fixture emission. Future sink conformance requires golden bytes, database/outbox fault "
    "tests and independent review."
)
AUDIT_TRACE_NEW = (
    "Primary source is `contracts/audit-events/src/`. The local contract is bound by "
    "`contracts/audit-events/README.md`; the maintained documentation surfaces are "
    "`docs/modules/contracts/audit-events-design.md` and "
    "`docs/modules/contracts/README.md`. Executable traceability is carried by the inline "
    "Rust tests under `contracts/audit-events/src/` and by "
    "`scripts/check-trnm-world-contract-module-documentation.py` plus its hostile fixture "
    "suite. Tests must cover deterministic round trips, stable spellings, "
    "duplicate/oversized/unsupported values, secret-like field rejection where supported, "
    "exact duplicate identity and cross-crate fixture emission. Future sink conformance "
    "requires golden bytes, database/outbox fault tests and independent review."
)
GOVERNANCE_TRACE_OLD = (
    "Primary source is `contracts/governance-guard/src/`; current scope is defined by "
    "`contracts/README.md`. Tests cover schedule/execute/cancel, too-early and expired "
    "execution, action/version drift, exact/altered duplicate, pause/resume role/state "
    "behavior, overflow/bounds, state preservation and audit normalization. Host time, "
    "signatures/quorum, durable execution and live governance probes remain absent."
)
GOVERNANCE_TRACE_NEW = (
    "Primary source is `contracts/governance-guard/src/`; the local contract is "
    "`contracts/governance-guard/README.md`, and the maintained documentation surfaces are "
    "`docs/modules/contracts/governance-guard-design.md` and "
    "`docs/modules/contracts/README.md`. Executable traceability is carried by the Rust "
    "tests under `contracts/governance-guard/src/` and by "
    "`scripts/check-trnm-world-contract-module-documentation.py` plus its hostile fixture "
    "suite. Tests cover schedule/execute/cancel, too-early and expired execution, "
    "action/version drift, exact/altered duplicate, pause/resume role/state behavior, "
    "overflow/bounds, state preservation and audit normalization. Host time, "
    "signatures/quorum, durable execution and live governance probes remain absent."
)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} is unavailable: {path}")
    text = path.read_text(encoding="utf-8", errors="strict")
    if text.count(old) != 1:
        raise RuntimeError(f"{label} traceability boundary drift")
    if new in text:
        raise RuntimeError(f"{label} traceability repair already present")
    path.write_text(text.replace(old, new), encoding="utf-8")


def repair_contract_documentation(root: Path) -> None:
    replace_once(
        root / "docs/modules/contracts/audit-events-design.md",
        AUDIT_TRACE_OLD,
        AUDIT_TRACE_NEW,
        "audit-events design",
    )
    replace_once(
        root / "docs/modules/contracts/governance-guard-design.md",
        GOVERNANCE_TRACE_OLD,
        GOVERNANCE_TRACE_NEW,
        "governance-guard design",
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
    if wrapper_text.count(WRAPPER_OLD) != 1:
        raise RuntimeError("settlement wrapper include boundary drift")
    wrapper_text = wrapper_text.replace(WRAPPER_OLD, WRAPPER_NEW)
    if "include!(\"settlement_worker_legacy.rs\")" in wrapper_text or "include!(\"settlement_worker_runtime_v2.rs\")" in wrapper_text:
        raise RuntimeError("settlement wrapper retained a textual include")
    wrapper.write_text(wrapper_text, encoding="utf-8")

    legacy_text = legacy.read_text(encoding="utf-8", errors="strict")
    if legacy_text.count(LEGACY_ANCHOR) != 1:
        raise RuntimeError("settlement legacy module insertion anchor drift")
    if "mod runtime_v2;" in legacy_text or "pub use runtime_v2::run_v2;" in legacy_text:
        raise RuntimeError("settlement runtime module already declared")
    legacy.write_text(
        legacy_text.replace(LEGACY_ANCHOR, LEGACY_ANCHOR + LEGACY_MODULE),
        encoding="utf-8",
    )

    runtime_text = runtime.read_text(encoding="utf-8", errors="strict")
    if runtime_text.startswith("use super::*;\n"):
        raise RuntimeError("settlement runtime already imports its parent module")
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
    existing = {(item.get("path"), item.get("expression")) for item in completed if isinstance(item, dict)}
    for record in records:
        key = (record["path"], record["expression"])
        if key in existing:
            raise RuntimeError(f"settlement include migration already completed: {key}")
        completed.append(record)
        existing.add(key)
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    repair_contract_documentation(root)
    print("WORLD_PR127_SETTLEMENT_WORKER_MODULE_TRANSFORM=PASS seams=2")
    print("WORLD_PR127_CONTRACT_DOCUMENTATION_TRACEABILITY=PASS designs=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
