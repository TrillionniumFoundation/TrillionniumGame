#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUEST_PATH = ROOT / ".github/world-publication-requests/world-v13k-2026-09-08.json"
WORKFLOW_PATH = ROOT / ".github/workflows/world-command-deployed-runtime-v1.yml"
OVERLAY_PATH = ROOT / "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json"
ARTIFACT_PATH = Path("/tmp/world-v13k-source-candidate.zip")
IMPORTER_PATH = Path("/tmp/import-qualified-world-v13k.py")
WORLD_REPOSITORY = "TrillionniumFoundation/Trillionnium-World"
WORLD_BRANCH = "fix/world-plan-v4-development-closure-20260831"
NEW_REQUEST_ID = "world-v13k-2026-09-08-r4"
WORKFLOW_ID = 345102133
WORKFLOW_NAME = "world-command-deployed-runtime-v1"
WORKFLOW_REPO_PATH = ".github/workflows/world-command-deployed-runtime-v1.yml"


class RefreshFailure(RuntimeError):
    pass


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RefreshFailure(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout={result.stdout.decode('utf-8', 'replace')[-2000:]}\n"
            f"stderr={result.stderr.decode('utf-8', 'replace')[-2000:]}"
        )
    return result.stdout.decode("utf-8").strip()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git_blob_sha(value: bytes) -> str:
    return hashlib.sha1(f"blob {len(value)}\0".encode("ascii") + value).hexdigest()


def replace_exact(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise RefreshFailure(f"{label}: expected {expected} matches, found {count}")
    return text.replace(old, new)


def current_world_head() -> str:
    output = run(
        "git",
        "ls-remote",
        f"https://github.com/{WORLD_REPOSITORY}.git",
        f"refs/heads/{WORLD_BRANCH}",
    )
    fields = output.split()
    if len(fields) != 2 or len(fields[0]) != 40:
        raise RefreshFailure(f"cannot resolve current World head: {output!r}")
    return fields[0]


def load_importer() -> Any:
    spec = importlib.util.spec_from_file_location("trusted_world_importer", IMPORTER_PATH)
    if spec is None or spec.loader is None:
        raise RefreshFailure("cannot load pinned World importer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def derive_overlay(old_head: str, observed_head: str, records: list[dict[str, Any]]) -> tuple[str, list[str], int]:
    with tempfile.TemporaryDirectory(prefix="trnm-world-cas-refresh-") as directory:
        repo = Path(directory)
        run("git", "init", "-q", str(repo))
        run("git", "-C", str(repo), "remote", "add", "origin", f"https://github.com/{WORLD_REPOSITORY}.git")
        run("git", "-C", str(repo), "fetch", "--no-tags", "origin", old_head, observed_head)
        run("git", "-C", str(repo), "cat-file", "-e", f"{old_head}^{{commit}}")
        run("git", "-C", str(repo), "cat-file", "-e", f"{observed_head}^{{commit}}")
        run("git", "-C", str(repo), "merge-base", "--is-ancestor", old_head, observed_head)

        changed = [
            line
            for line in run("git", "-C", str(repo), "diff", "--name-only", old_head, observed_head).splitlines()
            if line
        ]
        commit_count = int(run("git", "-C", str(repo), "rev-list", "--count", f"{old_head}..{observed_head}"))
        record_paths = {str(record["path"]) for record in records}
        overlap = sorted(record_paths.intersection(changed))
        if overlap:
            raise RefreshFailure(f"World target delta overlaps qualified overlay paths: {overlap}")

        index = repo / "overlay.index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        run("git", "-C", str(repo), "read-tree", f"{observed_head}^{{tree}}", env=env)
        for record in records:
            path = str(record["path"])
            blob = record["sha"]
            if blob is None:
                run("git", "-C", str(repo), "update-index", "--force-remove", "--", path, env=env)
                continue
            content = record["content"]
            if not isinstance(content, bytes):
                raise RefreshFailure(f"qualified record lacks bytes: {path}")
            observed_blob = run("git", "-C", str(repo), "hash-object", "-w", "--stdin", env=env, input_bytes=content)
            if observed_blob != blob:
                raise RefreshFailure(f"local blob identity mismatch: {path}")
            mode = str(record["mode"])
            run("git", "-C", str(repo), "update-index", "--add", "--cacheinfo", mode, blob, path, env=env)

        overlay_tree = run("git", "-C", str(repo), "write-tree", env=env)
        effective_paths = {
            line
            for line in run(
                "git",
                "-C",
                str(repo),
                "diff",
                "--name-only",
                f"{observed_head}^{{tree}}",
                overlay_tree,
            ).splitlines()
            if line
        }
        if effective_paths != record_paths:
            missing = sorted(record_paths - effective_paths)
            extra = sorted(effective_paths - record_paths)
            raise RefreshFailure(f"overlay path closure mismatch missing={missing} extra={extra}")

        for record in records:
            path = str(record["path"])
            line = run("git", "-C", str(repo), "ls-tree", overlay_tree, "--", path)
            if record["sha"] is None:
                if line:
                    raise RefreshFailure(f"required deletion remains in overlay tree: {path}")
                continue
            fields = line.split()
            if len(fields) < 4 or fields[0] != str(record["mode"]) or fields[2] != str(record["sha"]):
                raise RefreshFailure(f"overlay identity mismatch: {path}: {line!r}")

        return overlay_tree, changed, commit_count


def update_overlay(workflow_bytes: bytes) -> None:
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    replacements = overlay.get("replace_workflows")
    if not isinstance(replacements, list):
        raise RefreshFailure("workflow overlay replacements must be a list")
    matches = [
        item
        for item in replacements
        if isinstance(item, dict)
        and item.get("workflow_id") == WORKFLOW_ID
        and item.get("name") == WORKFLOW_NAME
        and item.get("path") == WORKFLOW_REPO_PATH
    ]
    if len(matches) != 1:
        raise RefreshFailure(f"registered World workflow match count is {len(matches)}")
    matches[0]["git_blob_sha1"] = git_blob_sha(workflow_bytes)
    payload = dict(overlay)
    payload.pop("overlay_sha256", None)
    overlay["overlay_sha256"] = sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    OVERLAY_PATH.write_text(
        json.dumps(overlay, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> int:
    request_bytes = REQUEST_PATH.read_bytes()
    request = json.loads(request_bytes)
    if request.get("schema") != "trillionnium.world-publication-request.v1":
        raise RefreshFailure("unexpected publication request schema")
    if request.get("request_id") != "world-v13k-2026-09-08":
        raise RefreshFailure("publication request identity drift")
    target = request.get("target")
    if not isinstance(target, dict):
        raise RefreshFailure("publication target must be an object")
    if target.get("repository") != WORLD_REPOSITORY or target.get("branch") != WORLD_BRANCH:
        raise RefreshFailure("publication target identity drift")
    old_head = str(target.get("expected_head", ""))
    if len(old_head) != 40:
        raise RefreshFailure("old expected World head is invalid")

    observed_head = current_world_head()
    importer = load_importer()
    with tempfile.TemporaryDirectory(prefix="trnm-world-artifact-expand-") as directory:
        _, records = importer.prepare(ARTIFACT_PATH, Path(directory))
    if len(records) != 75:
        raise RefreshFailure(f"qualified record count drift: {len(records)}")
    overlay_tree, changed_paths, commit_count = derive_overlay(old_head, observed_head, records)

    request["request_id"] = NEW_REQUEST_ID
    target["expected_head"] = observed_head
    target["expected_overlay_tree"] = overlay_tree
    target["preserved_delta"] = {
        "from_head": old_head,
        "to_head": observed_head,
        "commit_count": commit_count,
        "changed_path_count": len(changed_paths),
        "changed_paths_sha256": sha256_bytes(("\n".join(changed_paths) + "\n").encode("utf-8")),
        "qualified_path_overlap_count": 0,
    }
    new_request_bytes = (json.dumps(request, indent=2, sort_keys=False) + "\n").encode("utf-8")
    REQUEST_PATH.write_bytes(new_request_bytes)
    request_sha = sha256_bytes(new_request_bytes)
    old_request_sha = sha256_bytes(request_bytes)

    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = replace_exact(workflow, "world-v13k-2026-09-08", NEW_REQUEST_ID, 5, "request identifier")
    workflow = replace_exact(workflow, old_head, observed_head, 2, "World expected head")
    workflow = replace_exact(workflow, old_request_sha, request_sha, 2, "request SHA-256")
    head_line = f"      WORLD_EXPECTED_HEAD: {observed_head}\n"
    workflow = replace_exact(
        workflow,
        head_line,
        head_line + f"      WORLD_EXPECTED_OVERLAY_TREE: {overlay_tree}\n",
        2,
        "expected overlay env",
    )
    assertion = '          assert request["target"]["expected_head"] == os.environ["WORLD_EXPECTED_HEAD"]\n'
    workflow = replace_exact(
        workflow,
        assertion,
        assertion + '          assert request["target"]["expected_overlay_tree"] == os.environ["WORLD_EXPECTED_OVERLAY_TREE"]\n',
        1,
        "request overlay assertion",
    )
    dry_anchor = '''          assert value["deletion_count"] == 2
          PY

  publish-qualified-world-source:
'''
    dry_insert = '''          assert value["deletion_count"] == 2
          PY
          observed_head="$(git ls-remote "https://github.com/${WORLD_REPOSITORY}.git" "refs/heads/${WORLD_BRANCH}" | awk '{print $1}')"
          test "$observed_head" = "$WORLD_EXPECTED_HEAD"
          rm -rf /tmp/world-v13k-overlay-preflight
          git init -q /tmp/world-v13k-overlay-preflight
          git -C /tmp/world-v13k-overlay-preflight remote add origin "https://github.com/${WORLD_REPOSITORY}.git"
          git -C /tmp/world-v13k-overlay-preflight fetch --no-tags --depth=1 origin "$WORLD_EXPECTED_HEAD"
          python3 - <<'PY'
          import importlib.util
          import os
          from pathlib import Path
          import subprocess
          import sys
          import tempfile

          importer_path = "/tmp/import-qualified-world-v13k.py"
          spec = importlib.util.spec_from_file_location("trusted_world_importer_preflight", importer_path)
          assert spec is not None and spec.loader is not None
          importer = importlib.util.module_from_spec(spec)
          sys.modules[spec.name] = importer
          spec.loader.exec_module(importer)
          with tempfile.TemporaryDirectory(prefix="world-v13k-preflight-") as directory:
              _, records = importer.prepare(Path("/tmp/world-v13k-source-candidate.zip"), Path(directory))
          repo = Path("/tmp/world-v13k-overlay-preflight")
          env = dict(os.environ)
          env["GIT_INDEX_FILE"] = str(repo / "overlay.index")
          head = os.environ["WORLD_EXPECTED_HEAD"]
          subprocess.run(["git", "-C", str(repo), "read-tree", f"{head}^{{tree}}"], env=env, check=True)
          expected_paths = set()
          for record in records:
              path = record["path"]
              expected_paths.add(path)
              if record["sha"] is None:
                  subprocess.run(["git", "-C", str(repo), "update-index", "--force-remove", "--", path], env=env, check=True)
                  continue
              blob = subprocess.check_output(
                  ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                  env=env,
                  input=record["content"],
              ).decode().strip()
              assert blob == record["sha"]
              subprocess.run(
                  ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", record["mode"], blob, path],
                  env=env,
                  check=True,
              )
          overlay = subprocess.check_output(["git", "-C", str(repo), "write-tree"], env=env).decode().strip()
          assert overlay == os.environ["WORLD_EXPECTED_OVERLAY_TREE"]
          changed = set(
              subprocess.check_output(
                  ["git", "-C", str(repo), "diff", "--name-only", f"{head}^{{tree}}", overlay]
              ).decode().splitlines()
          )
          assert changed == expected_paths
          PY

  publish-qualified-world-source:
'''
    workflow = replace_exact(workflow, dry_anchor, dry_insert, 1, "secret-free overlay preflight")
    receipt_assertion = '          assert receipt["qualified_tree"] == qualified_tree\n'
    workflow = replace_exact(
        workflow,
        receipt_assertion,
        receipt_assertion + '          assert receipt["overlay_tree"] == os.environ["WORLD_EXPECTED_OVERLAY_TREE"]\n',
        1,
        "publication overlay assertion",
    )
    workflow_bytes = workflow.encode("utf-8")
    WORKFLOW_PATH.write_bytes(workflow_bytes)
    update_overlay(workflow_bytes)

    if current_world_head() != observed_head:
        raise RefreshFailure("World target moved during publication-request refresh")

    print(f"request_id={NEW_REQUEST_ID}")
    print(f"old_world_head={old_head}")
    print(f"world_head={observed_head}")
    print(f"world_overlay_tree={overlay_tree}")
    print(f"preserved_commit_count={commit_count}")
    print(f"preserved_changed_path_count={len(changed_paths)}")
    print(f"request_sha256={request_sha}")
    print(f"workflow_blob={git_blob_sha(workflow_bytes)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RefreshFailure, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"World CAS refresh failed closed: {error}", file=sys.stderr)
        raise SystemExit(1)
