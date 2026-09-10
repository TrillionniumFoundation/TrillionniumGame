#!/usr/bin/env python3
"""Ensure the cross-module Serde default callback remains explicitly bound."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)

    state = root / "trillionnium/crates/trnm-campaign-core/src/lib_parts/campaign_state/part_01.rs"
    state_source = state.read_text(encoding="utf-8", errors="strict")
    semantic_use = '#[serde(default = "legacy_campaign_schema_revision")]'
    if state_source.count(semantic_use) != 1:
        raise RuntimeError("campaign schema Serde callback anchor drift")

    lib = root / "trillionnium/crates/trnm-campaign-core/src/lib.rs"
    source = lib.read_text(encoding="utf-8", errors="strict")
    old = "use contracts_and_domain::{default_campaign_credits};"
    new = (
        "use contracts_and_domain::{"
        "default_campaign_credits, legacy_campaign_schema_revision"
        "};"
    )
    if source.count(old) == 1 and source.count(new) == 0:
        lib.write_text(source.replace(old, new), encoding="utf-8")
        disposition = "inserted"
    elif source.count(old) == 0 and source.count(new) == 1:
        disposition = "already-bound"
    else:
        raise RuntimeError(
            "campaign semantic import drift: "
            f"old={source.count(old)} new={source.count(new)}"
        )
    print(
        "WORLD_CAMPAIGN_RUNTIME_SEMANTIC_IMPORT_REPAIR=PASS "
        f"serde_callbacks=1 disposition={disposition}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
