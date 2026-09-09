#!/usr/bin/env python3
"""Finalize the PostgreSQL semantic-recovery composer with targeted constraint fixtures."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load_support() -> ModuleType:
    path = Path(__file__).with_name("compose_postgresql_semantic_recovery.py")
    spec = importlib.util.spec_from_file_location("postgresql_semantic_recovery_support", path)
    require(spec is not None and spec.loader is not None, "semantic recovery support unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    support = load_support()
    support.run(root)
    harness = root / "scripts/ci-postgresql-semantic-recovery.sh"
    text = support.read(harness)
    second_entity = (
        "        INSERT INTO trnm_entity_heads VALUES\n"
        "          (decode(repeat('1a',16),'hex'), 0, 0, 1, decode(repeat('1b',32),'hex'), 10);\n"
    )
    if second_entity not in text:
        anchor = (
            "        INSERT INTO trnm_entity_heads VALUES\n"
            "          (decode(repeat('11',16),'hex'), 1, 1, 1, decode(repeat('12',32),'hex'), 10);\n"
        )
        require(anchor in text, "primary entity fixture anchor missing")
        text = text.replace(anchor, anchor + second_entity, 1)
    support.write(harness, text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
