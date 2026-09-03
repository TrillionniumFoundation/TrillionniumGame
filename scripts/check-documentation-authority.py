#!/usr/bin/env python3
"""Enforce one current documentation system and complete module documentation."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
STATUS_MARKER = "Status: **authoritative current documentation**"
MODULE_STATUS_PREFIX = "Status: **module documentation;"
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
EXPECTED_CURRENT = {
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
EXPECTED_MODULE_SECTIONS = (
    "## Status and authority",
    "## Responsibilities",
    "## Architecture and dependencies",
    "## Public contracts",
    "## Correctness and failure model",
    "## Security and privacy",
    "## Build and test",
    "## Operations",
    "## Compatibility and evidence",
    "## Known gaps and exit criteria",
)


class ValidationError(RuntimeError):
    """Raised when the active documentation surface is ambiguous or broken."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSON keys instead of silently accepting the last value."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    require(isinstance(value, dict), f"{path}: top-level value must be an object")
    return value


def valid_revision(value: Any, label: str) -> str:
    """Validate a canonical real calendar date without consulting wall-clock time."""
    require(
        isinstance(value, str)
        and re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value) is not None,
        f"{label}: revision must be YYYY-MM-DD",
    )
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(f"{label}: revision is not a valid calendar date") from error
    return value


def expected_document_revisions(
    authority: dict[str, Any], current_documents: set[str]
) -> dict[str, str]:
    """Retain the v1 baseline and bind registered topic updates individually."""
    baseline = valid_revision(authority.get("revision"), "documentation")
    overrides = authority.get("document_revisions", {})
    require(isinstance(overrides, dict), "document_revisions must be an object")
    result = dict.fromkeys(sorted(current_documents), baseline)
    for path, value in overrides.items():
        require(
            isinstance(path, str) and path in current_documents,
            f"document_revisions: unregistered current document: {path!r}",
        )
        revision = valid_revision(value, path)
        require(revision >= baseline, f"{path}: revision predates documentation baseline")
        result[path] = revision
    return result


def has_exact_revision_marker(text: str, expected: str) -> bool:
    """Require one whole-line marker; reject duplicate and substring matches."""
    markers = [
        line.rstrip(" \t")
        for line in text.splitlines()
        if re.match(r"^[ \t]*Revision[ \t]*:", line)
    ]
    return markers == [f"Revision: {expected}"]


def canonical_path(root: Path, value: Any, label: str) -> Path:
    require(isinstance(value, str) and value, f"{label}: path must be non-empty text")
    require("\\" not in value, f"{label}: backslashes are forbidden")
    candidate = Path(value)
    require(not candidate.is_absolute(), f"{label}: absolute path is forbidden")
    require(".." not in candidate.parts, f"{label}: parent traversal is forbidden")
    normalized = candidate.as_posix()
    require(normalized == value, f"{label}: path must be canonical: {value!r}")
    return root / candidate


def path_list(root: Path, value: Any, label: str, *, allow_empty: bool = False) -> list[Path]:
    require(
        isinstance(value, list) and (allow_empty or bool(value)),
        f"{label}: {'list' if allow_empty else 'non-empty list'} required",
    )
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


def validate_module_registry(
    root: Path,
    registry_path: Path,
) -> tuple[list[Path], dict[str, int]]:
    registry = load_object(registry_path)
    require(
        registry.get("schema") == "trillionnium.module-documentation.v1",
        "unexpected module documentation schema",
    )
    require(registry.get("project_id") == "trillionnium-game", "unexpected module project_id")
    require(registry.get("plan_version") == 3, "module registry must target plan v3")
    required_sections = registry.get("required_sections")
    require(
        isinstance(required_sections, list)
        and tuple(required_sections) == EXPECTED_MODULE_SECTIONS,
        "module registry required_sections changed",
    )

    rows = registry.get("modules")
    require(isinstance(rows, list) and rows, "module registry must contain modules")
    documented: list[Path] = []
    manifests: set[str] = set()
    module_ids: set[str] = set()
    root_count = 0
    isolated_count = 0

    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"modules[{index}] must be an object")
        module_id = row.get("id")
        require(
            isinstance(module_id, str)
            and re.fullmatch(r"trnm-[a-z0-9-]+", module_id) is not None,
            f"modules[{index}].id is invalid",
        )
        require(module_id not in module_ids, f"duplicate module id: {module_id}")
        module_ids.add(module_id)

        expected_path = f"crates/{module_id}"
        require(row.get("path") == expected_path, f"{module_id}: path must be {expected_path}")
        manifest_value = row.get("manifest")
        documentation_value = row.get("documentation")
        expected_manifest = f"{expected_path}/Cargo.toml"
        expected_documentation = f"{expected_path}/README.md"
        require(
            manifest_value == expected_manifest,
            f"{module_id}: manifest must be {expected_manifest}",
        )
        require(
            documentation_value == expected_documentation,
            f"{module_id}: documentation must be {expected_documentation}",
        )
        manifest = canonical_path(root, manifest_value, f"{module_id}.manifest")
        documentation = canonical_path(
            root, documentation_value, f"{module_id}.documentation"
        )
        require(manifest.is_file(), f"{module_id}: manifest is missing")
        require(documentation.is_file(), f"{module_id}: README is missing")
        manifests.add(manifest.relative_to(root).as_posix())
        documented.append(documentation)

        workspace = row.get("workspace")
        require(workspace in {"root", "isolated"}, f"{module_id}: invalid workspace")
        if workspace == "root":
            root_count += 1
        else:
            isolated_count += 1
        for field in ("lifecycle", "maturity", "owner_role", "authority"):
            value = row.get(field)
            require(
                isinstance(value, str) and value.strip() == value and bool(value),
                f"{module_id}: {field} must be canonical non-empty text",
            )
        gaps = row.get("blocking_gaps")
        require(
            isinstance(gaps, list)
            and gaps
            and all(isinstance(value, str) and value.startswith("GAP-") for value in gaps),
            f"{module_id}: blocking_gaps are required",
        )
        require(len(gaps) == len(set(gaps)), f"{module_id}: duplicate blocking gaps")
        require(row.get("claim_credit") is False, f"{module_id}: claim credit must remain false")

        text = documentation.read_text(encoding="utf-8")
        failures: list[str] = []
        if not text.startswith(f"# {module_id}\n"):
            failures.append("canonical title required")
        if MODULE_STATUS_PREFIX not in text:
            failures.append("module status marker required")
        if len(text.splitlines()) < 45:
            failures.append("module document is unexpectedly small")
        for section in EXPECTED_MODULE_SECTIONS:
            if text.count(section) != 1:
                failures.append(f"exactly one section required: {section}")
        require(
            not failures,
            f"{module_id}: module documentation contract failures:\n- "
            + "\n- ".join(failures),
        )

    discovered = {
        path.relative_to(root).as_posix()
        for path in (root / "crates").glob("*/Cargo.toml")
        if path.is_file()
    }
    require(
        manifests == discovered,
        "module registry does not match crate manifests: "
        f"unregistered={sorted(discovered - manifests)}, "
        f"missing={sorted(manifests - discovered)}",
    )

    summary = registry.get("summary")
    require(isinstance(summary, dict), "module registry summary is required")
    require(summary.get("module_count") == len(rows), "module summary.module_count is stale")
    require(
        summary.get("documented_count") == len(documented),
        "module summary.documented_count is stale",
    )
    require(summary.get("root_workspace_count") == root_count, "root workspace count is stale")
    require(
        summary.get("isolated_workspace_count") == isolated_count,
        "isolated workspace count is stale",
    )
    require(summary.get("undocumented_count") == 0, "undocumented modules must be zero")
    return documented, {
        "module_count": len(rows),
        "root_workspace_count": root_count,
        "isolated_workspace_count": isolated_count,
    }


def is_active_reference_surface(
    root: Path,
    path: Path,
    root_docs: set[Path],
    current_docs: set[Path],
    generated_rollups: set[Path],
    machine_docs: set[Path],
) -> bool:
    """Return whether a tracked text file may influence current development."""

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


def validate_repository_markdown_allowlist(
    root: Path,
    files: Iterable[Path],
    allowed: set[Path],
) -> int:
    actual = {
        path
        for path in files
        if path.suffix.lower() == ".md"
    }
    extra = sorted(path.relative_to(root).as_posix() for path in actual - allowed)
    missing = sorted(path.relative_to(root).as_posix() for path in allowed - actual)
    require(not extra, f"undeclared Markdown remains in repository: {extra}")
    require(not missing, f"declared Markdown is not tracked: {missing}")
    return len(actual)


def validate(root: Path = ROOT, authority_path: Path | None = None) -> dict[str, Any]:
    authority_path = authority_path or root / "docs/DOCUMENTATION_AUTHORITY.json"
    authority = load_object(authority_path)
    require(
        authority.get("schema") == "trillionnium.documentation-authority.v1",
        "unexpected documentation authority schema",
    )
    require(authority.get("project_id") == "trillionnium-game", "unexpected project_id")
    require(authority.get("plan_version") == 3, "documentation authority must target plan v3")
    revision = valid_revision(authority.get("revision"), "documentation")

    policy = authority.get("policy")
    require(isinstance(policy, dict), "documentation policy must be an object")
    required_policy = {
        "single_current_human_document_per_topic": True,
        "historical_markdown_allowed_in_active_tree": False,
        "git_history_is_the_human_document_archive": True,
        "machine_evidence_may_be_immutable_and_dated": True,
        "broken_repository_document_references_allowed": False,
        "legacy_document_names_allowed": False,
        "repository_wide_markdown_allowlist": True,
        "every_rust_package_has_module_documentation": True,
    }
    for key, expected in required_policy.items():
        require(
            policy.get(key) is expected,
            f"documentation policy {key} must be {expected}",
        )

    root_documents = path_list(
        root, authority.get("root_human_documents"), "root_human_documents"
    )
    current_documents = path_list(
        root, authority.get("current_human_documents"), "current_human_documents"
    )
    generated_rollups = path_list(
        root, authority.get("generated_human_rollups"), "generated_human_rollups"
    )
    permitted_markdown = path_list(
        root,
        authority.get("permitted_non_development_markdown"),
        "permitted_non_development_markdown",
        allow_empty=True,
    )
    machine_documents = path_list(
        root, authority.get("machine_control_documents"), "machine_control_documents"
    )
    removed_roots = path_list(
        root, authority.get("removed_human_document_roots"), "removed_human_document_roots"
    )
    registry_path = canonical_path(
        root, authority.get("module_document_registry"), "module_document_registry"
    )

    for path in (
        root_documents
        + current_documents
        + generated_rollups
        + permitted_markdown
        + machine_documents
        + [registry_path]
    ):
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

    actual_current = {path.relative_to(root).as_posix() for path in current_documents}
    require(actual_current == EXPECTED_CURRENT, "current human documentation set changed")
    document_revisions = expected_document_revisions(authority, actual_current)

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
    extra_docs = sorted(actual_docs_markdown - allowed_docs_markdown)
    missing_docs = sorted(allowed_docs_markdown - actual_docs_markdown)
    require(not extra_docs, f"undeclared or historical Markdown remains under docs/: {extra_docs}")
    require(not missing_docs, f"declared Markdown is not tracked: {missing_docs}")

    root_markdown = {
        path.relative_to(root).as_posix()
        for path in files
        if path.parent == root and path.suffix.lower() == ".md"
    }
    allowed_root = {path.relative_to(root).as_posix() for path in root_documents}
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
        expected_revision = document_revisions[relative.as_posix()]
        if not has_exact_revision_marker(text, expected_revision):
            document_failures.append(
                f"{relative}: exactly one Revision: {expected_revision} marker required"
            )
        if len(text.splitlines()) < 20:
            document_failures.append(f"{relative}: document is unexpectedly small")
    require(
        not document_failures,
        "current documentation contract failures:\n- "
        + "\n- ".join(sorted(document_failures)),
    )

    module_documents, module_counts = validate_module_registry(root, registry_path)

    allowed_repository_markdown = set(
        root_documents
        + current_documents
        + generated_rollups
        + permitted_markdown
        + module_documents
    )
    markdown_count = validate_repository_markdown_allowlist(
        root, files, allowed_repository_markdown
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
        claims.get("repository_markdown_allowlist_enforced") is True,
        "repository Markdown allowlist claim missing",
    )
    require(
        claims.get("module_documentation_registry_enforced") is True,
        "module documentation registry claim missing",
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

    link_count = validate_markdown_links(
        root, root_documents + current_documents + permitted_markdown + module_documents
    )
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
        "document_revisions": document_revisions,
        "root_human_document_count": len(root_documents),
        "current_human_document_count": len(current_documents),
        "generated_rollup_count": len(generated_rollups),
        "permitted_non_development_markdown_count": len(permitted_markdown),
        "machine_control_document_count": len(machine_documents),
        "repository_markdown_count": markdown_count,
        "module_document_count": module_counts["module_count"],
        "root_workspace_module_count": module_counts["root_workspace_count"],
        "isolated_workspace_module_count": module_counts["isolated_workspace_count"],
        "undocumented_module_count": 0,
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
