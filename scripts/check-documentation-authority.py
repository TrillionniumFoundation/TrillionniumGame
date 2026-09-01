#!/usr/bin/env python3
"""Enforce one current human documentation system and reject legacy drift."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
STATUS_MARKER = "Status: **authoritative current documentation**"
DOC_REFERENCE = re.compile(r"(?<![A-Za-z0-9_.-])(docs/[A-Za-z0-9_./-]+\.md)")
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
TEXT_SUFFIXES = {
    ".md",
    ".json",
    ".py",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
    ".rs",
    ".go",
    ".txt",
}


class ValidationError(RuntimeError):
    """Raised when the active documentation surface is ambiguous or broken."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path}: top-level value must be an object")
    return value


def canonical_path(root: Path, value: Any, label: str) -> Path:
    require(isinstance(value, str) and value, f"{label}: path must be non-empty text")
    require("\\" not in value, f"{label}: backslashes are forbidden")
    candidate = Path(value)
    require(not candidate.is_absolute(), f"{label}: absolute path is forbidden")
    require(".." not in candidate.parts, f"{label}: parent traversal is forbidden")
    normalized = candidate.as_posix()
    require(normalized == value, f"{label}: path must be canonical: {value!r}")
    return root / candidate


def path_list(root: Path, value: Any, label: str) -> list[Path]:
    require(isinstance(value, list) and value, f"{label}: non-empty list required")
    result = [
        canonical_path(root, item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]
    relative = [path.relative_to(root).as_posix() for path in result]
    require(len(relative) == len(set(relative)), f"{label}: duplicate paths")
    return result


def tracked_files(root: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(path for path in root.rglob("*") if path.is_file())
    result: list[Path] = []
    for entry in completed.stdout.split(b"\0"):
        if not entry:
            continue
        try:
            relative = entry.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValidationError("git path is not UTF-8") from error
        result.append(root / relative)
    return sorted(result)


def read_text_if_supported(path: Path) -> str | None:
    if path.name == "Makefile" or path.suffix.lower() in TEXT_SUFFIXES:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
    return None


def validate_markdown_links(root: Path, documents: Iterable[Path]) -> int:
    checked = 0
    failures: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not target:
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                failures.append(
                    f"{document.relative_to(root)}: link escapes repository: {raw_target}"
                )
                continue
            if not resolved.exists():
                failures.append(
                    f"{document.relative_to(root)}: broken local link: {raw_target}"
                )
                continue
            checked += 1
    require(not failures, "broken current links:\n- " + "\n- ".join(sorted(failures)))
    return checked


def is_active_reference_surface(
    root: Path,
    path: Path,
    root_docs: set[Path],
    current_docs: set[Path],
    generated_rollups: set[Path],
    machine_docs: set[Path],
) -> bool:
    """Return whether a tracked text file may influence current development.

    Immutable evidence records under ``docs/evidence`` may truthfully retain a
    historical path.  Current evidence indexes/schemas are listed explicitly in
    ``machine_docs`` and remain active.  Test fixtures are excluded because
    their purpose is often to contain deliberately invalid legacy references.
    Every other supported tracked source/control surface is active, so a Rust,
    Go, contract, config, tool, workflow or root policy cannot silently retain
    a reference to a deleted development document.
    """

    relative = path.relative_to(root)
    if path in root_docs or path in current_docs:
        return True
    if path in generated_rollups or path in machine_docs:
        return True
    if relative.parts and relative.parts[0] == "docs":
        return False
    if relative.parts and relative.parts[0] == "tests":
        return False
    return read_text_if_supported(path) is not None


def validate_repository_doc_references(
    root: Path,
    files: Iterable[Path],
    root_docs: set[Path],
    current_docs: set[Path],
    generated_rollups: set[Path],
    machine_docs: set[Path],
) -> tuple[int, int]:
    checked = 0
    surface_count = 0
    failures: set[str] = set()
    for path in files:
        if not is_active_reference_surface(
            root,
            path,
            root_docs,
            current_docs,
            generated_rollups,
            machine_docs,
        ):
            continue
        text = read_text_if_supported(path)
        if text is None:
            continue
        surface_count += 1
        for referenced in DOC_REFERENCE.findall(text):
            target = root / referenced
            if not target.is_file():
                failures.add(f"{path.relative_to(root)} -> {referenced}")
            else:
                checked += 1
    require(
        not failures,
        "active files reference removed documentation:\n- "
        + "\n- ".join(sorted(failures)),
    )
    return checked, surface_count


def validate(root: Path = ROOT, authority_path: Path | None = None) -> dict[str, Any]:
    authority_path = authority_path or root / "docs/DOCUMENTATION_AUTHORITY.json"
    authority = load_object(authority_path)
    require(
        authority.get("schema") == "trillionnium.documentation-authority.v1",
        "unexpected documentation authority schema",
    )
    require(authority.get("project_id") == "trillionnium-game", "unexpected project_id")
    require(authority.get("plan_version") == 3, "documentation authority must target plan v3")
    revision = authority.get("revision")
    require(
        isinstance(revision, str)
        and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", revision) is not None,
        "documentation revision must be YYYY-MM-DD",
    )

    policy = authority.get("policy")
    require(isinstance(policy, dict), "documentation policy must be an object")
    required_policy = {
        "single_current_human_document_per_topic": True,
        "historical_markdown_allowed_in_active_tree": False,
        "git_history_is_the_human_document_archive": True,
        "machine_evidence_may_be_immutable_and_dated": True,
        "broken_repository_document_references_allowed": False,
        "legacy_document_names_allowed": False,
    }
    for key, expected in required_policy.items():
        require(
            policy.get(key) is expected,
            f"documentation policy {key} must be {expected}",
        )

    root_documents = path_list(
        root,
        authority.get("root_human_documents"),
        "root_human_documents",
    )
    current_documents = path_list(
        root,
        authority.get("current_human_documents"),
        "current_human_documents",
    )
    generated_rollups = path_list(
        root,
        authority.get("generated_human_rollups"),
        "generated_human_rollups",
    )
    machine_documents = path_list(
        root,
        authority.get("machine_control_documents"),
        "machine_control_documents",
    )
    removed_roots = path_list(
        root,
        authority.get("removed_human_document_roots"),
        "removed_human_document_roots",
    )

    for path in root_documents + current_documents + generated_rollups + machine_documents:
        require(
            path.is_file(),
            f"declared documentation/control file is missing: {path.relative_to(root)}",
        )
    remaining_removed_roots = [
        path.relative_to(root).as_posix() for path in removed_roots if path.exists()
    ]
    require(
        not remaining_removed_roots,
        f"removed human documentation roots still exist: {remaining_removed_roots}",
    )

    expected_current = {
        "docs/README.md",
        "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT.md",
        "docs/COMPATIBILITY.md",
        "docs/TESTING_AND_EVIDENCE.md",
        "docs/SECURITY_AND_PRIVACY.md",
        "docs/OPERATIONS_AND_RELEASE.md",
        "docs/GOVERNANCE.md",
        "docs/ROADMAP.md",
    }
    actual_current = {
        path.relative_to(root).as_posix() for path in current_documents
    }
    require(actual_current == expected_current, "current human documentation set changed")

    files = tracked_files(root)
    actual_docs_markdown = {
        path.relative_to(root).as_posix()
        for path in files
        if path.suffix.lower() == ".md"
        and path.relative_to(root).parts[:1] == ("docs",)
    }
    allowed_docs_markdown = actual_current | {
        path.relative_to(root).as_posix() for path in generated_rollups
    }
    extra = sorted(actual_docs_markdown - allowed_docs_markdown)
    missing = sorted(allowed_docs_markdown - actual_docs_markdown)
    require(not extra, f"undeclared or historical Markdown remains under docs/: {extra}")
    require(not missing, f"declared Markdown is not tracked: {missing}")

    root_markdown = {
        path.relative_to(root).as_posix()
        for path in files
        if path.parent == root and path.suffix.lower() == ".md"
    }
    allowed_root = {
        path.relative_to(root).as_posix() for path in root_documents
    }
    require(
        root_markdown == allowed_root,
        "root Markdown set differs from authority: "
        f"extra={sorted(root_markdown - allowed_root)}, "
        f"missing={sorted(allowed_root - root_markdown)}",
    )

    patterns_value = authority.get("forbidden_human_filename_patterns")
    require(
        isinstance(patterns_value, list) and patterns_value,
        "forbidden filename patterns required",
    )
    patterns: list[re.Pattern[str]] = []
    for index, value in enumerate(patterns_value):
        require(
            isinstance(value, str) and value,
            f"forbidden pattern {index} must be text",
        )
        try:
            patterns.append(re.compile(value))
        except re.error as error:
            raise ValidationError(f"invalid forbidden pattern {value!r}: {error}") from error
    forbidden = sorted(
        path
        for path in actual_docs_markdown
        if any(pattern.search(path) for pattern in patterns)
    )
    require(not forbidden, f"legacy documentation filename remains: {forbidden}")

    document_failures: list[str] = []
    for document in current_documents:
        text = document.read_text(encoding="utf-8")
        relative = document.relative_to(root)
        if not text.startswith("# "):
            document_failures.append(f"{relative}: title required")
        if STATUS_MARKER not in text:
            document_failures.append(f"{relative}: authority status marker required")
        if "Revision: 2026-09-01" not in text:
            document_failures.append(f"{relative}: revision marker required")
        if len(text.splitlines()) < 20:
            document_failures.append(f"{relative}: document is unexpectedly small")
    require(
        not document_failures,
        "current documentation contract failures:\n- "
        + "\n- ".join(sorted(document_failures)),
    )

    plan = (root / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    require("开发计划 v3.1" in plan, "current plan revision marker missing")
    require(
        "docs/DOCUMENTATION_AUTHORITY.json" in plan,
        "current plan omits documentation authority",
    )
    require(
        "历史信息只保留在 Git 历史" in plan,
        "current plan omits history policy",
    )

    claims = authority.get("claims")
    require(isinstance(claims, dict), "documentation claims must be an object")
    require(
        claims.get("documentation_consolidated") is True,
        "documentation consolidation claim missing",
    )
    require(
        claims.get("historical_human_docs_removed_from_active_tree") is True,
        "historical human docs removal claim missing",
    )
    require(
        claims.get("machine_evidence_deleted") is False,
        "machine evidence must remain retained",
    )
    for forbidden_claim in (
        "compatibility_credit",
        "production_ready",
        "public_online",
        "nakama_retired",
    ):
        require(
            claims.get(forbidden_claim) is False,
            f"premature documentation claim: {forbidden_claim}",
        )

    link_count = validate_markdown_links(root, root_documents + current_documents)
    reference_count, surface_count = validate_repository_doc_references(
        root,
        files,
        set(root_documents),
        set(current_documents),
        set(generated_rollups),
        set(machine_documents),
    )

    return {
        "schema": "trillionnium.documentation-authority-validation.v1",
        "status": "passed",
        "revision": revision,
        "root_human_document_count": len(root_documents),
        "current_human_document_count": len(current_documents),
        "generated_rollup_count": len(generated_rollups),
        "machine_control_document_count": len(machine_documents),
        "active_reference_surface_count": surface_count,
        "local_link_count": link_count,
        "active_repository_reference_count": reference_count,
        "historical_markdown_count": 0,
        "claim_boundary": (
            "Documentation consistency grants no compatibility or production credit."
        ),
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"documentation authority validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
