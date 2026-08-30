from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.upstream.pinned_archive import (
    LOCK_FILE,
    SourceArchiveError,
    extract_github_tarball,
    fetch_pinned_github_source,
    git_tree_sha1,
    verify_source_lock,
)


def write_fixture(root: Path) -> None:
    (root / "dir").mkdir(parents=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    executable = root / "dir" / "tool.sh"
    executable.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    executable.chmod(0o755)
    (root / "dir" / "data.bin").write_bytes(b"\x00\x01\xff")


def make_tar(source: Path, output: Path, prefix: str = "owner-repo-deadbeef") -> None:
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(source, arcname=prefix, recursive=True)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, resolved_url: str = "https://codeload.github.com/owner/repo/tar.gz/commit") -> None:
        super().__init__(payload)
        self._resolved_url = resolved_url

    def geturl(self) -> str:
        return self._resolved_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class GitTreeTests(unittest.TestCase):
    def test_git_tree_hash_matches_git_write_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            expected = git_tree_sha1(root)
            try:
                subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(root), "add", "README.md", "dir"], check=True, capture_output=True)
                actual = subprocess.run(
                    ["git", "-C", str(root), "write-tree"], check=True, text=True, capture_output=True
                ).stdout.strip()
            except FileNotFoundError:
                self.skipTest("git is unavailable")
            self.assertEqual(actual, expected)

    def test_git_tree_hash_matches_git_for_relative_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            os.symlink("../README.md", root / "dir" / "README-link.md")
            expected = git_tree_sha1(root)
            try:
                subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(root), "add", "README.md", "dir"], check=True, capture_output=True)
                actual = subprocess.run(
                    ["git", "-C", str(root), "write-tree"], check=True, text=True, capture_output=True
                ).stdout.strip()
            except FileNotFoundError:
                self.skipTest("git is unavailable")
            self.assertEqual(actual, expected)

    def test_lock_file_is_excluded_but_post_fetch_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            tree = git_tree_sha1(root)
            marker = {
                "schema": "trillionnium.pinned-source-archive.v1",
                "repository": "owner/repo",
                "revision": "1" * 40,
                "tree": tree,
                "verification": "recomputed-git-tree-sha1",
            }
            (root / LOCK_FILE).write_text(json.dumps(marker), encoding="utf-8")
            verify_source_lock(root, repository="owner/repo", revision="1" * 40, tree=tree)
            (root / "README.md").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(SourceArchiveError):
                verify_source_lock(root, repository="owner/repo", revision="1" * 40, tree=tree)


class ArchiveExtractionTests(unittest.TestCase):
    def test_safe_extract_preserves_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            write_fixture(source)
            tree = git_tree_sha1(source)
            archive = base / "source.tar.gz"
            make_tar(source, archive)
            destination = base / "destination"
            count, size = extract_github_tarball(archive, destination)
            self.assertEqual(count, 3)
            self.assertGreater(size, 0)
            self.assertEqual(git_tree_sha1(destination), tree)

    def test_safe_relative_symlink_preserves_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            write_fixture(source)
            os.symlink("../README.md", source / "dir" / "README-link.md")
            tree = git_tree_sha1(source)
            archive = base / "source.tar.gz"
            make_tar(source, archive)
            destination = base / "destination"
            count, size = extract_github_tarball(archive, destination)
            self.assertEqual(count, 4)
            self.assertGreater(size, 0)
            link = destination / "dir" / "README-link.md"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "../README.md")
            self.assertEqual(git_tree_sha1(destination), tree)

    def test_path_traversal_escaping_links_and_nonempty_destination_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            traversal = base / "traversal.tar.gz"
            with tarfile.open(traversal, "w:gz") as archive:
                info = tarfile.TarInfo("root/../../escape")
                payload = b"bad"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            with self.assertRaises(SourceArchiveError):
                extract_github_tarball(traversal, base / "out1")

            absolute_symlink = base / "absolute-symlink.tar.gz"
            with tarfile.open(absolute_symlink, "w:gz") as archive:
                root = tarfile.TarInfo("root")
                root.type = tarfile.DIRTYPE
                archive.addfile(root)
                link = tarfile.TarInfo("root/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                archive.addfile(link)
            with self.assertRaises(SourceArchiveError):
                extract_github_tarball(absolute_symlink, base / "out2")

            escaping_symlink = base / "escaping-symlink.tar.gz"
            with tarfile.open(escaping_symlink, "w:gz") as archive:
                root = tarfile.TarInfo("root")
                root.type = tarfile.DIRTYPE
                archive.addfile(root)
                nested = tarfile.TarInfo("root/dir")
                nested.type = tarfile.DIRTYPE
                archive.addfile(nested)
                link = tarfile.TarInfo("root/dir/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../../outside"
                archive.addfile(link)
            with self.assertRaises(SourceArchiveError):
                extract_github_tarball(escaping_symlink, base / "out3")

            hard_link = base / "hard-link.tar.gz"
            with tarfile.open(hard_link, "w:gz") as archive:
                root = tarfile.TarInfo("root")
                root.type = tarfile.DIRTYPE
                archive.addfile(root)
                original = tarfile.TarInfo("root/original")
                payload = b"ok"
                original.size = len(payload)
                archive.addfile(original, io.BytesIO(payload))
                link = tarfile.TarInfo("root/link")
                link.type = tarfile.LNKTYPE
                link.linkname = "root/original"
                archive.addfile(link)
            with self.assertRaises(SourceArchiveError):
                extract_github_tarball(hard_link, base / "out4")

            source = base / "source"
            source.mkdir()
            write_fixture(source)
            valid = base / "valid.tar.gz"
            make_tar(source, valid)
            nonempty = base / "out5"
            nonempty.mkdir()
            (nonempty / "stale").write_text("stale", encoding="utf-8")
            with self.assertRaises(SourceArchiveError):
                extract_github_tarball(valid, nonempty)


class ArchiveFetchTests(unittest.TestCase):
    def archive_payload(self, base: Path) -> tuple[bytes, str]:
        source = base / "source"
        source.mkdir()
        write_fixture(source)
        tree = git_tree_sha1(source)
        archive = base / "source.tar.gz"
        make_tar(source, archive)
        return archive.read_bytes(), tree

    def test_fetch_verifies_tree_before_atomic_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload, tree = self.archive_payload(base)
            output = base / "checkout"
            with mock.patch("tools.upstream.pinned_archive.urllib.request.urlopen", return_value=FakeResponse(payload)):
                evidence = fetch_pinned_github_source(
                    repository="owner/repo", revision="1" * 40, tree=tree, output_dir=output
                )
            self.assertEqual(evidence.tree, tree)
            marker = verify_source_lock(output, repository="owner/repo", revision="1" * 40, tree=tree)
            self.assertEqual(marker["verification"], "recomputed-git-tree-sha1")

    def test_wrong_tree_non_https_redirect_size_limit_and_zero_sha_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload, tree = self.archive_payload(base)
            output = base / "checkout"
            with mock.patch("tools.upstream.pinned_archive.urllib.request.urlopen", return_value=FakeResponse(payload)):
                with self.assertRaises(SourceArchiveError):
                    fetch_pinned_github_source(
                        repository="owner/repo", revision="1" * 40, tree="f" * 40, output_dir=output
                    )
            self.assertFalse(output.exists())

            response = FakeResponse(payload, resolved_url="http://example.invalid/archive")
            with mock.patch("tools.upstream.pinned_archive.urllib.request.urlopen", return_value=response):
                with self.assertRaises(SourceArchiveError):
                    fetch_pinned_github_source(
                        repository="owner/repo", revision="1" * 40, tree=tree, output_dir=base / "redirect"
                    )

            with mock.patch("tools.upstream.pinned_archive.urllib.request.urlopen", return_value=FakeResponse(payload)):
                with self.assertRaises(SourceArchiveError):
                    fetch_pinned_github_source(
                        repository="owner/repo", revision="1" * 40, tree=tree,
                        output_dir=base / "large", max_archive_bytes=1
                    )

            with self.assertRaises(SourceArchiveError):
                fetch_pinned_github_source(
                    repository="owner/repo", revision="0" * 40, tree=tree, output_dir=base / "zero"
                )


if __name__ == "__main__":
    unittest.main()
