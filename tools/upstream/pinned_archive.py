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
from typing import Any, BinaryIO, Iterable, Mapping

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


def _require_sha(value: str, label: str) -> str:
    if len(value) != 40 or value == "0" * 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise SourceArchiveError(f"{label} must be a non-zero lowercase 40-character SHA")
    return value


def _gitlink_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        raw_rows: Iterable[Any] = value.items()
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        raw_rows = value
    else:
        raise SourceArchiveError("gitlinks must be an object or an iterable of path/commit rows")

    result: dict[str, str] = {}
    for raw in raw_rows:
        if isinstance(raw, Mapping):
            path_value = raw.get("path")
            commit_value = raw.get("commit")
        else:
            try:
                path_value, commit_value = raw
            except (TypeError, ValueError) as exc:
                raise SourceArchiveError("invalid gitlink row") from exc
        if not isinstance(path_value, str) or not isinstance(commit_value, str):
            raise SourceArchiveError("gitlink path and commit must be strings")
        parts = _safe_member_parts(path_value)
        canonical = PurePosixPath(*parts).as_posix()
        if canonical != path_value:
            raise SourceArchiveError(f"gitlink path must be canonical: {path_value!r}")
        if any(part in {LOCK_FILE, ".git"} for part in parts):
            raise SourceArchiveError(f"gitlink path uses a reserved component: {path_value!r}")
        commit = _require_sha(commit_value, f"gitlink {path_value} commit")
        if canonical in result:
            raise SourceArchiveError(f"duplicate gitlink path: {canonical}")
        result[canonical] = commit

    paths = sorted(result)
    for index, path in enumerate(paths):
        prefix = path + "/"
        for candidate in paths[index + 1 :]:
            if candidate.startswith(prefix):
                raise SourceArchiveError(
                    f"gitlink paths may not contain another gitlink: {path!r}, {candidate!r}"
                )
    return result


def _gitlink_rows(value: Any) -> list[dict[str, str]]:
    return [
        {"path": path, "commit": commit}
        for path, commit in sorted(_gitlink_mapping(value).items())
    ]


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
    gitlinks: tuple[tuple[str, str], ...] = ()

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
            "gitlinks": [
                {"path": path, "commit": commit}
                for path, commit in self.gitlinks
            ],
            "verification": "recomputed-git-tree-sha1",
        }


def _git_object_sha1(kind: str, payload: bytes) -> bytes:
    header = f"{kind} {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).digest()


def git_blob_sha1_bytes(payload: bytes) -> str:
    return _git_object_sha1("blob", payload).hex()


def git_tree_sha1(
    root: Path,
    *,
    ignored_names: Iterable[str] = (LOCK_FILE, ".git"),
    gitlinks: Any = None,
) -> str:
    """Recompute Git's root tree SHA-1 from files, symlinks and pinned gitlinks.

    GitHub source archives do not materialize submodule commit objects. A
    caller may therefore supply the exact mode-160000 path/commit entries from
    the pinned root tree. They participate in tree hashing but their contents
    are never fetched and never enter denominator discovery.
    """
    root = root.resolve(strict=True)
    ignored = set(ignored_names)
    links = _gitlink_mapping(gitlinks)
    link_parts = {
        tuple(PurePosixPath(path).parts): bytes.fromhex(commit)
        for path, commit in links.items()
    }

    def build(directory: Path, relative: tuple[str, ...]) -> bytes:
        actual: dict[str, Path] = {}
        if directory.exists():
            metadata = directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise SourceArchiveError(f"tree directory is not a regular directory: {directory}")
            actual = {child.name: child for child in directory.iterdir() if child.name not in ignored}

        virtual_names: set[str] = set()
        exact_links: dict[str, bytes] = {}
        for parts, commit_digest in link_parts.items():
            if parts[: len(relative)] != relative:
                continue
            remainder = parts[len(relative) :]
            if not remainder:
                raise SourceArchiveError(f"gitlink collides with tree root: {'/'.join(parts)}")
            virtual_names.add(remainder[0])
            if len(remainder) == 1:
                exact_links[remainder[0]] = commit_digest

        entries: list[tuple[bytes, bytes, bytes]] = []
        for name_text in actual.keys() | virtual_names:
            name = os.fsencode(name_text)
            if b"\x00" in name or b"/" in name:
                raise SourceArchiveError(f"unsafe filesystem name while hashing tree: {name_text!r}")
            child = actual.get(name_text)
            child_relative = relative + (name_text,)

            if name_text in exact_links:
                if child is not None:
                    metadata = child.lstat()
                    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                        raise SourceArchiveError(
                            f"archive path conflicts with pinned gitlink: {'/'.join(child_relative)}"
                        )
                    if any(child.iterdir()):
                        raise SourceArchiveError(
                            f"archive expanded pinned gitlink contents: {'/'.join(child_relative)}"
                        )
                mode = b"160000"
                digest = exact_links[name_text]
            else:
                has_descendant_link = any(
                    parts[: len(child_relative)] == child_relative
                    for parts in link_parts
                )
                if child is None:
                    if not has_descendant_link:
                        raise SourceArchiveError(
                            f"internal gitlink tree construction error: {'/'.join(child_relative)}"
                        )
                    mode = b"40000"
                    digest = build(directory / name_text, child_relative)
                else:
                    metadata = child.lstat()
                    if has_descendant_link and (
                        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
                    ):
                        raise SourceArchiveError(
                            f"archive path blocks pinned gitlink parent: {'/'.join(child_relative)}"
                        )
                    if stat.S_ISLNK(metadata.st_mode):
                        mode = b"120000"
                        digest = _git_object_sha1("blob", os.fsencode(os.readlink(child)))
                    elif stat.S_ISDIR(metadata.st_mode):
                        mode = b"40000"
                        digest = build(child, child_relative)
                    elif stat.S_ISREG(metadata.st_mode):
                        mode = b"100755" if metadata.st_mode & 0o111 else b"100644"
                        digest = _git_object_sha1("blob", child.read_bytes())
                    else:
                        raise SourceArchiveError(f"special source file is not accepted: {child}")
            entries.append((mode, name, digest))

        entries.sort(key=lambda entry: entry[1] + (b"/" if entry[0] == b"40000" else b""))
        payload = b"".join(mode + b" " + name + b"\0" + digest for mode, name, digest in entries)
        return _git_object_sha1("tree", payload)

    return build(root, ()).hex()


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
    marker_gitlinks = _gitlink_rows(value.get("gitlinks", []))
    if value.get("gitlinks", []) != marker_gitlinks:
        raise SourceArchiveError("source-lock gitlinks must be canonical and sorted")
    actual_tree = git_tree_sha1(root, gitlinks=marker_gitlinks)
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


def _safe_symlink_target(destination: Path, target: Path, linkname: str) -> str:
    if "\\" in linkname or "\x00" in linkname:
        raise SourceArchiveError(f"unsafe archive symlink target: {linkname!r}")
    link = PurePosixPath(linkname)
    if link.is_absolute() or not link.parts or any(part in {"", "."} for part in link.parts):
        raise SourceArchiveError(f"unsafe archive symlink target: {linkname!r}")
    resolved = target.parent.joinpath(*link.parts).resolve(strict=False)
    try:
        resolved.relative_to(destination)
    except ValueError as exc:
        raise SourceArchiveError(f"archive symlink escapes destination: {linkname!r}") from exc
    return linkname


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
            if member.islnk():
                raise SourceArchiveError(f"archive hard links are not accepted: {member.name}")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise SourceArchiveError(f"archive special files are not accepted: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            if member.issym():
                linkname = _safe_symlink_target(destination, target, member.linkname)
                link_bytes = os.fsencode(linkname)
                file_count += 1
                extracted_bytes += len(link_bytes)
                if file_count > max_files:
                    raise SourceArchiveError(f"archive exceeds file-count limit {max_files}")
                if extracted_bytes > max_extracted_bytes:
                    raise SourceArchiveError(f"archive exceeds extracted-byte limit {max_extracted_bytes}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(linkname, target)
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
    gitlinks: Any = None,
) -> ArchiveEvidence:
    if repository.count("/") != 1:
        raise SourceArchiveError("repository must be owner/name")
    revision = _require_sha(revision, "revision")
    tree = _require_sha(tree, "tree")
    normalized_gitlinks = _gitlink_mapping(gitlinks)
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
        actual_tree = git_tree_sha1(extract_dir, gitlinks=normalized_gitlinks)
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
            gitlinks=tuple(sorted(normalized_gitlinks.items())),
        )
        (extract_dir / LOCK_FILE).write_bytes(canonical_bytes(evidence.to_dict()))
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(extract_dir, output_dir)
        return evidence
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)
