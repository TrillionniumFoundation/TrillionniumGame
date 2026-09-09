#!/usr/bin/env python3
"""Idempotent v2 composer for command durable-boundary rollback hooks."""
from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path
from types import ModuleType


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load_support() -> ModuleType:
    path = Path(__file__).with_name("compose_commit_fault_boundaries.py")
    spec = importlib.util.spec_from_file_location("commit_fault_boundary_support", path)
    require(spec is not None and spec.loader is not None, "support composer loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update_library(root: Path, support: ModuleType) -> None:
    path = root / "crates/trnm-persistence-pg/src/lib.rs"
    text = support.read(path)
    if "mod fault;" not in text:
        anchor = "mod authority;\n" if "mod authority;\n" in text else "mod auth;\n"
        require(anchor in text, "module anchor missing")
        text = text.replace(anchor, anchor + "mod fault;\n", 1)
    export = '#[cfg(feature = "transaction-test-hooks")]\npub use fault::CommitMutationPoint;\n'
    if export not in text:
        anchor = "pub use auth::{\n"
        require(anchor in text, "public export anchor missing")
        text = text.replace(anchor, export + anchor, 1)

    if "fn commit_command_with_hook(" not in text:
        pattern = re.compile(
            r"(?m)^    pub fn commit_command\(\n"
            r"        &mut self,\n"
            r"        request: &CommitRequest,\n"
            r"    \) -> Result<CommitOutcome, DomainError> \{\n"
            r"        validate_request\(request\)\?;\n"
        )
        replacement = (
            "    pub fn commit_command(\n"
            "        &mut self,\n"
            "        request: &CommitRequest,\n"
            "    ) -> Result<CommitOutcome, DomainError> {\n"
            "        self.commit_command_with_hook(request, |_| Ok(()))\n"
            "    }\n\n"
            "    #[cfg(feature = \"transaction-test-hooks\")]\n"
            "    pub fn commit_command_with_test_hook(\n"
            "        &mut self,\n"
            "        request: &CommitRequest,\n"
            "        hook: impl FnMut(fault::CommitMutationPoint) -> Result<(), DomainError>,\n"
            "    ) -> Result<CommitOutcome, DomainError> {\n"
            "        self.commit_command_with_hook(request, hook)\n"
            "    }\n\n"
            "    fn commit_command_with_hook(\n"
            "        &mut self,\n"
            "        request: &CommitRequest,\n"
            "        mut hook: impl FnMut(fault::CommitMutationPoint) -> Result<(), DomainError>,\n"
            "    ) -> Result<CommitOutcome, DomainError> {\n"
            "        validate_request(request)?;\n"
        )
        text, count = pattern.subn(replacement, text, count=1)
        require(count == 1, "commit_command opening anchor missing")

    if "CommitMutationPoint::HeadComparedAndSwapped" not in text:
        anchor = (
            "        if updated != 1 {\n"
            "            return Err(error(\n"
            "                StableCode::Aborted,\n"
            "                \"entity_compare_and_swap_failed\",\n"
            "                RetryClass::ResyncRequired,\n"
            "            ));\n"
            "        }\n\n"
            "        let first_event_sequence_i64 = first_event_sequence.map(to_i64).transpose()?;\n"
        )
        require(anchor in text, "head-CAS hook anchor missing")
        text = text.replace(
            anchor,
            anchor.replace(
                "\n        let first_event_sequence_i64",
                "\n        hook(fault::CommitMutationPoint::HeadComparedAndSwapped)?;"
                "\n\n        let first_event_sequence_i64",
                1,
            ),
            1,
        )

    if "CommitMutationPoint::ReceiptInserted" not in text:
        pattern = re.compile(
            r'(?s)(\n        transaction\n            \.execute\(\n                "INSERT INTO trnm_command_receipts .*?\n            \.map_err\(map_postgres_error\)\?;)(\n\n        let mut sequence = last_event_sequence;)'
        )
        text, count = pattern.subn(
            r"\1\n        hook(fault::CommitMutationPoint::ReceiptInserted)?;\2",
            text,
            count=1,
        )
        require(count == 1, "receipt-insert hook anchor missing")

    if "CommitMutationPoint::EventsAppended" not in text:
        anchor = "        }\n\n        for (position, intent) in request.outbox.iter().enumerate() {\n"
        require(anchor in text, "event-loop hook anchor missing")
        text = text.replace(
            anchor,
            "        }\n        hook(fault::CommitMutationPoint::EventsAppended)?;\n\n"
            "        for (position, intent) in request.outbox.iter().enumerate() {\n",
            1,
        )

    if "CommitMutationPoint::OutboxAppended" not in text:
        pattern = re.compile(
            r'(\n        transaction\.commit\(\)\.map_err\(map_postgres_error\)\?;\n        Ok\(CommitOutcome::Applied)'
        )
        text, count = pattern.subn(
            "\n        hook(fault::CommitMutationPoint::OutboxAppended)?;"
            "\n        hook(fault::CommitMutationPoint::BeforeCommit)?;"
            r"\1",
            text,
            count=1,
        )
        require(count == 1, "pre-commit hook anchor missing")
    support.write(path, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    support = load_support()
    support.update_manifest(root)
    update_library(root, support)
    support.write(root / "crates/trnm-persistence-pg/src/fault.rs", support.fault_source())
    support.write(
        root / "crates/trnm-persistence-pg/tests/commit_fault_boundaries.rs",
        support.test_source(),
    )
    support.update_contracts(root)
    support.update_documents(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
