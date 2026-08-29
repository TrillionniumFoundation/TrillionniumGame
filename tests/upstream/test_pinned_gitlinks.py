from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.upstream.pinned_archive import (
    LOCK_FILE,
    SourceArchiveError,
    git_tree_sha1,
    verify_source_lock,
)


class PinnedGitlinkTests(unittest.TestCase):
    def test_gitlink_tree_matches_git_write_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("root\n", encoding="utf-8")
            commit = "4dda419bcaeae54e1744903f5d5508153d42373e"
            expected = git_tree_sha1(
                root,
                gitlinks={"submodules/json": commit},
            )
            try:
                subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
                subprocess.run(
                    ["git", "-C", str(root), "add", "README.md"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"160000,{commit},submodules/json",
                    ],
                    check=True,
                    capture_output=True,
                )
                actual = subprocess.run(
                    ["git", "-C", str(root), "write-tree"],
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout.strip()
            except FileNotFoundError:
                self.skipTest("git is unavailable")
            self.assertEqual(actual, expected)

    def test_source_lock_replays_canonical_gitlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitmodules").write_text(
                '[submodule "json"]\n\tpath = submodules/json\n\turl = https://example.invalid/json.git\n',
                encoding="utf-8",
            )
            gitlinks = [
                {
                    "path": "submodules/json",
                    "commit": "4dda419bcaeae54e1744903f5d5508153d42373e",
                }
            ]
            tree = git_tree_sha1(root, gitlinks=gitlinks)
            marker = {
                "schema": "trillionnium.pinned-source-archive.v1",
                "repository": "owner/repo",
                "revision": "1" * 40,
                "tree": tree,
                "gitlinks": gitlinks,
                "verification": "recomputed-git-tree-sha1",
            }
            (root / LOCK_FILE).write_text(
                json.dumps(marker, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            verified = verify_source_lock(
                root,
                repository="owner/repo",
                revision="1" * 40,
                tree=tree,
            )
            self.assertEqual(verified["gitlinks"], gitlinks)

    def test_expanded_or_unsafe_gitlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expanded = root / "submodules" / "json"
            expanded.mkdir(parents=True)
            (expanded / "source.cpp").write_text("expanded\n", encoding="utf-8")
            with self.assertRaises(SourceArchiveError):
                git_tree_sha1(
                    root,
                    gitlinks={
                        "submodules/json": "4dda419bcaeae54e1744903f5d5508153d42373e"
                    },
                )

            with self.assertRaises(SourceArchiveError):
                git_tree_sha1(
                    root,
                    gitlinks={
                        "../escape": "4dda419bcaeae54e1744903f5d5508153d42373e"
                    },
                )

            with self.assertRaises(SourceArchiveError):
                git_tree_sha1(
                    root,
                    gitlinks={
                        "submodules": "4dda419bcaeae54e1744903f5d5508153d42373e",
                        "submodules/json": "e2a86523f293c9261e3aa2d865677efbb5df14c9",
                    },
                )


if __name__ == "__main__":
    unittest.main()
