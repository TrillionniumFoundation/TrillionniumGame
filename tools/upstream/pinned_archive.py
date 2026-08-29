# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

DEFAULT_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILES = 250_000
LOCK_FILE = ".trillionnium-source-lock.json"


class SourceArchiveError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"


@dataclass(frozen=True, slots=True)
class ArchiveEvidence:
    repository: str
    revision: str
    tree: str
    archive_sha256: str
    archive_size: int
    source_url: str
    resolved_url: str
    file_count: int
    extracted_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "trillionnium.pinned-source-archive.v1",
            "repository": self.repository,
            "revision": self.revision,
            "tree": self.tree,
            "archive_sha256": self.archive_sha256,
            "archive_size": self.archive_size,
            "source_url": self.source_url,
            "resolved_url": self.resolved_url,
            "file_count": self.file_count,
            "extracted_bytes": self.extracted_bytes,
            "verification": "recomputed-git-tree-sha1",
        }


def _require_sha(value: str, label: str) -> str:
    if len(value) != 40 or value == "0" * 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise SourceArchiveError(f"{label} must be a non-zero lowercase 40-character SHA")
    return value


def _git_object_sha1(kind: str, payload: bytes) -> bytes:
    header = f"{kind} {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).digest()


def git_blob_sha1_bytes(payload: bytes) -> str:
    return _git_object_sha1("blob", payload).hex()


def _tree_sort_key(path: Path) -> bytes:
    suffix = b"/" if path.is_dir() else b""
    return os.fsencode(path.name) + suffix


def git_tree_sha1(root: Path, *, ignored_names: Iterable[str] = (LOCK_FILE, ".git")) -> str:
    """Recompute Git's root tree object SHA-1 from a regular filesystem tree."""
    root = root.resolve(strict=True)
    ignored = set(ignored_names)

    def build(directory: Path) -> bytes:
        entries: list[tuple[bytes, bytes, bytes]] = []
        for child in sorted(directory.iterdir(), key=_tree_sort_key):
            if child.name in ignored:
                continue
            metadata = child.lstat()
            name = os.fsencode(child.name)
            if b"\x00" in name or b"/" in name:
                raise SourceArchiveError(f"unsafe filesystem name while hashing tree: {child}")
            if stat.S_ISLNK(metadata.st_mode):
                raise SourceArchiveError(f"source symlinks are not accepted: {child}")
            if stat.S_ISDIR(metadata.st_mode):
                mode = b"40000"
                digest = build(child)
            elif stat.S_ISREG(metadata.st_mode):
                mode = b"100755" if metadata.st_mode & 0o111 else b"100644"
                digest = _git_object_sha1("blob", child.read_bytes())
            else:
                raise SourceArchiveError(f"special source file is not accepted: {child}")
            entries.append((mode, name, digest))
        payload = b"".join(mode + b" " + name + b"\0" + digest for mode, name, digest in entries)
        return _git_object_sha1("tree", payload)

    return build(root).hex()


def verify_source_lock(root: Path, *, repository: str, revision: str, tree: str) -> dict[str, Any]:
    root = root.resolve(strict=True)
    marker = root / LOCK_FILE
    if not marker.is_file():
        raise SourceArchiveError(f"source-lock marker is missing: {marker}")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceArchiveError(f"invalid source-lock marker: {exc}") from exc
    expected = {"repository": repository, "revision": revision, "tree": tree}
    actual = {key: value.get(key) for key in expected}
    if actual != expected or value.get("verification") != "recomputed-git-tree-sha1":
        raise SourceArchiveError(f"source-lock identity mismatch: expected {expected}, got {actual}")
    actual_tree = git_tree_sha1(root)
    if actual_tree != tree:
        raise SourceArchiveError(f"source content tree mismatch: expected {tree}, got {actual_tree}")
    return value


def _safe_member_parts(name: str) -> tuple[str, ...]:
    if "\\" in name or "\x00" in name:
        raise SourceArchiveError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceArchiveError(f"unsafe archive path: {name!r}")
    return path.parts


def extract_github_tarball(
    archive_path: Path,
    destination: Path,
    *,
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> tuple[int, int]:
    archive_path = archive_path.resolve(strict=True)
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise SourceArchiveError(f"archive destination must be empty: {destination}")

    file_count = 0
    extracted_bytes = 0
    seen_outputs: set[Path] = set()
    root_prefix: str | None = None
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive:
            parts = _safe_member_parts(member.name)
            if root_prefix is None:
                root_prefix = parts[0]
            if parts[0] != root_prefix:
                raise SourceArchiveError("archive must have one stable top-level directory")
            if len(parts) == 1:
                if not member.isdir():
                    raise SourceArchiveError("archive root entry must be a directory")
                continue
            target = destination.joinpath(*parts[1:])
            try:
                target.resolve(strict=False).relative_to(destination)
            except ValueError as exc:
                raise SourceArchiveError(f"archive member escapes destination: {member.name}") from exc
            if target in seen_outputs:
                raise SourceArchiveError(f"duplicate archive output path: {member.name}")
            seen_outputs.add(target)
            if member.issym() or member.islnk():
                raise SourceArchiveError(f"archive links are not accepted: {member.name}")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise SourceArchiveError(f"archive special files are not accepted: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            if not member.isfile():
                raise SourceArchiveError(f"unsupported archive member type: {member.name}")
            file_count += 1
            extracted_bytes += member.size
            if file_count > max_files:
                raise SourceArchiveError(f"archive exceeds file-count limit {max_files}")
            if extracted_bytes > max_extracted_bytes:
                raise SourceArchiveError(f"archive exceeds extracted-byte limit {max_extracted_bytes}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise SourceArchiveError(f"archive file has no payload: {member.name}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if target.stat().st_size != member.size:
                raise SourceArchiveError(f"archive member size mismatch: {member.name}")
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    if root_prefix is None:
        raise SourceArchiveError("archive is empty")
    return file_count, extracted_bytes


def _stream_download(response: BinaryIO, destination: Path, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise SourceArchiveError(f"archive exceeds download limit {max_bytes}")
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return digest.hexdigest(), size


def fetch_pinned_github_source(
    *,
    repository: str,
    revision: str,
    tree: str,
    output_dir: Path,
    token: str | None = None,
    timeout_seconds: int = 120,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> ArchiveEvidence:
    if repository.count("/") != 1:
        raise SourceArchiveError("repository must be owner/name")
    revision = _require_sha(revision, "revision")
    tree = _require_sha(tree, "tree")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SourceArchiveError(f"output directory must be absent or empty: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    source_url = f"https://api.github.com/repos/{repository}/tarball/{revision}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "TrillionniumGame-pinned-source-fetcher/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(source_url, headers=headers, method="GET")

    temp_parent = Path(tempfile.mkdtemp(prefix="tg-pinned-source-", dir=output_dir.parent))
    archive_path = temp_parent / "source.tar.gz"
    extract_dir = temp_parent / "source"
    try:
        try:
            response = urllib.request.urlopen(request, timeout=timeout_seconds)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SourceArchiveError(f"could not download pinned source archive: {exc}") from exc
        with response:
            resolved_url = response.geturl()
            if not resolved_url.startswith("https://"):
                raise SourceArchiveError(f"archive redirect resolved to non-HTTPS URL: {resolved_url}")
            archive_sha256, archive_size = _stream_download(response, archive_path, max_archive_bytes)
        file_count, extracted_bytes = extract_github_tarball(archive_path, extract_dir)
        actual_tree = git_tree_sha1(extract_dir)
        if actual_tree != tree:
            raise SourceArchiveError(f"recomputed Git tree mismatch: expected {tree}, got {actual_tree}")
        evidence = ArchiveEvidence(
            repository=repository,
            revision=revision,
            tree=tree,
            archive_sha256=archive_sha256,
            archive_size=archive_size,
            source_url=source_url,
            resolved_url=resolved_url,
            file_count=file_count,
            extracted_bytes=extracted_bytes,
        )
        (extract_dir / LOCK_FILE).write_bytes(canonical_bytes(evidence.to_dict()))
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(extract_dir, output_dir)
        return evidence
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)
