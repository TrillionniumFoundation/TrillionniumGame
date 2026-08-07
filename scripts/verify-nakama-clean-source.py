#!/usr/bin/env python3
"""Verify that HEAD, index, flags, modes and worktree bytes are identical."""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tree", required=True)
    return parser.parse_args()


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main():
    args = parse_args()
    requested_repo = os.fsencode(args.repo_dir)
    repo = os.path.realpath(requested_repo)
    expected_revision = args.revision.encode("ascii")
    expected_tree = args.tree.encode("ascii")
    if not os.path.isabs(requested_repo) or os.path.normpath(requested_repo) != repo:
        fail("Nakama source verifier requires a canonical absolute repository path")
    if not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected_revision):
        fail("Nakama source verifier revision is not canonical")
    if (
        not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected_tree)
        or len(expected_tree) != len(expected_revision)
    ):
        fail("Nakama source verifier tree is not canonical")
    git_path = shutil.which("git", path=os.defpath)
    if git_path is None:
        fail("Nakama source verifier cannot find the system Git binary")
    git_binary = os.fsencode(os.path.realpath(git_path))
    git_environment = {
        b"PATH": os.fsencode(os.defpath),
        b"HOME": b"/nonexistent",
        b"XDG_CONFIG_HOME": b"/nonexistent",
        b"LC_ALL": b"C",
        b"GIT_CONFIG_NOSYSTEM": b"1",
        b"GIT_CONFIG_GLOBAL": b"/dev/null",
        b"GIT_NO_REPLACE_OBJECTS": b"1",
    }

    def git_output(*arguments):
        command = [
            git_binary,
            b"--no-replace-objects",
            b"-c",
            b"core.fsmonitor=false",
            b"-c",
            b"core.untrackedCache=false",
            b"-C",
            repo,
        ]
        command.extend(
            argument if isinstance(argument, bytes) else os.fsencode(argument)
            for argument in arguments
        )
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=git_environment,
        )
        if result.returncode != 0:
            fail("Nakama source verifier could not verify Git authority")
        return result.stdout

    def nul_records(payload, authority):
        if not payload:
            return []
        if not payload.endswith(b"\0"):
            fail(f"Nakama source verifier received malformed {authority}")
        records = payload[:-1].split(b"\0")
        if any(not record for record in records):
            fail(f"Nakama source verifier received malformed {authority}")
        return records

    def split_metadata_path(record, authority):
        metadata, separator, path = record.partition(b"\t")
        if not separator or not metadata or not path:
            fail(f"Nakama source verifier received malformed {authority}")
        return metadata, path

    def display_path(path):
        return os.fsdecode(path).encode("unicode_escape").decode("ascii")

    def verify_identity_and_status():
        observed_root = os.path.realpath(
            git_output(b"rev-parse", b"--show-toplevel").rstrip(b"\n")
        )
        observed_revision = git_output(
            b"rev-parse", b"--verify", b"HEAD^{commit}"
        ).strip()
        observed_tree = git_output(
            b"rev-parse", b"--verify", observed_revision + b"^{tree}"
        ).strip()
        if observed_root != repo:
            fail("Nakama source verifier repository identity changed")
        if observed_revision != expected_revision:
            fail("Nakama source verifier HEAD changed")
        if observed_tree != expected_tree:
            fail("Nakama source verifier tree changed")
        if git_output(
            b"status",
            b"--porcelain=v1",
            b"-z",
            b"--untracked-files=all",
            b"--ignore-submodules=none",
        ):
            fail("Nakama source verifier requires a clean worktree")

    verify_identity_and_status()

    commit_entries = {}
    for record in nul_records(
        git_output(b"ls-tree", b"-r", b"-z", b"--full-tree", expected_revision),
        "commit tree",
    ):
        metadata, path = split_metadata_path(record, "commit tree")
        fields = metadata.split(b" ")
        if len(fields) != 3:
            fail("Nakama source verifier received malformed commit tree metadata")
        mode, object_type, object_id = fields
        if object_type != b"blob" or mode not in {b"100644", b"100755"}:
            fail(f"Nakama source verifier rejects tracked entry: {display_path(path)}")
        if path in commit_entries:
            fail("Nakama source verifier found a duplicate commit-tree path")
        commit_entries[path] = (mode, object_id)

    index_entries = {}
    for record in nul_records(
        git_output(b"ls-files", b"--stage", b"-z"),
        "index",
    ):
        metadata, path = split_metadata_path(record, "index")
        fields = metadata.split(b" ")
        if len(fields) != 3:
            fail("Nakama source verifier received malformed index metadata")
        mode, object_id, stage = fields
        if stage != b"0" or mode not in {b"100644", b"100755"}:
            fail(f"Nakama source verifier rejects index entry: {display_path(path)}")
        if path in index_entries:
            fail("Nakama source verifier found a duplicate index path")
        if commit_entries.get(path) != (mode, object_id):
            fail(f"Nakama source verifier index differs from HEAD: {display_path(path)}")
        index_entries[path] = (mode, object_id)
    if index_entries != commit_entries:
        fail("Nakama source verifier index path set differs from HEAD")

    index_flag_paths = set()
    for record in nul_records(
        git_output(b"ls-files", b"-v", b"-z", b"--cached"),
        "index flags",
    ):
        if len(record) < 3 or record[1:2] != b" ":
            fail("Nakama source verifier received malformed index flags")
        flag = record[:1]
        path = record[2:]
        if flag != b"H":
            fail(
                "Nakama source verifier rejects assume-unchanged, skip-worktree, "
                f"or non-ordinary index entry: {display_path(path)}"
            )
        if path in index_flag_paths:
            fail("Nakama source verifier found duplicate index flag metadata")
        index_flag_paths.add(path)
    if index_flag_paths != set(index_entries):
        fail("Nakama source verifier index flag path set differs from HEAD")

    for authority_path in (
        b"go.work",
        b"go.work.sum",
        b"runtime/go.work",
        b"runtime/go.work.sum",
        b".cargo/config",
        b".cargo/config.toml",
    ):
        if authority_path not in commit_entries and os.path.lexists(
            os.path.join(repo, authority_path)
        ):
            fail(
                "Nakama source verifier rejects an untracked toolchain authority: "
                f"{display_path(authority_path)}"
            )

    for path, (expected_mode, expected_object_id) in sorted(index_entries.items()):
        if (
            not path
            or path.startswith(b"/")
            or any(component in {b"", b".", b".."} for component in path.split(b"/"))
        ):
            fail("Nakama source verifier rejects a non-canonical tracked path")
        worktree_path = os.path.join(repo, path)
        if os.path.realpath(worktree_path) != worktree_path:
            fail(
                "Nakama source verifier rejects a symlinked tracked path component: "
                f"{display_path(path)}"
            )
        try:
            before = os.stat(worktree_path, follow_symlinks=False)
        except OSError:
            fail(f"Nakama source verifier cannot stat: {display_path(path)}")
        if not stat.S_ISREG(before.st_mode):
            fail(f"Nakama source verifier tracked path is not regular: {display_path(path)}")
        if before.st_uid != os.geteuid() or before.st_nlink != 1:
            fail(
                "Nakama source verifier tracked path owner/link count is unsafe: "
                f"{display_path(path)}"
            )
        expected_executable = expected_mode == b"100755"
        actual_executable = bool(stat.S_IMODE(before.st_mode) & 0o111)
        if actual_executable != expected_executable:
            fail(f"Nakama source verifier mode differs from HEAD: {display_path(path)}")
        actual_object_id = git_output(
            b"hash-object", b"--no-filters", b"--", path
        ).strip()
        if actual_object_id != expected_object_id:
            fail(f"Nakama source verifier bytes differ from HEAD: {display_path(path)}")
        try:
            after = os.stat(worktree_path, follow_symlinks=False)
        except OSError:
            fail(f"Nakama source verifier cannot restat: {display_path(path)}")
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if after_identity != before_identity:
            fail(f"Nakama source verifier path changed while hashing: {display_path(path)}")

    verify_identity_and_status()
    print(
        json.dumps(
            {
                "revision": args.revision,
                "tree": args.tree,
                "tracked_files": len(commit_entries),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
