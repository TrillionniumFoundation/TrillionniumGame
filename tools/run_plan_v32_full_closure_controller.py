#!/usr/bin/env python3
"""Drive Plan v3.2 candidates through exact-object, non-bypass closure gates.

This controller may repair source/checker defects, publish verified candidates,
request independent review, and perform an ordinary protected merge only when
all current-head checks are non-empty terminal success and a current non-author
approval is bound to the exact head.  It never fabricates evidence, approvals,
acceptance, compatibility, production, cutover, or retirement claims.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
GH_TOKEN = os.environ["GH_TOKEN"]
OWNER, REPO = REPOSITORY.split("/", 1)
REMOTE = f"https://github.com/{REPOSITORY}.git"
ROOT = Path(tempfile.mkdtemp(prefix="plan-v32-full-closure-"))
CONTROLLER_COMMIT = "c78f20a02a475c2ddc1a376fd24b79930d3c7e29"
SURFACE3_PR = 185
SURFACE3_BRANCH = "feature/plan-v32-storage-api-core-20260910"
REVIEWERS = ("Franksudoman", "Tomasrgbsf")
REPORT: dict[str, Any] = {
    "schema": "trillionnium.plan-v32-full-closure-controller.v1",
    "repository": REPOSITORY,
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    "history_rewritten": False,
    "administrator_bypass": False,
    "self_approval": False,
    "accepted_evidence_promoted": False,
    "production_claim_promoted": False,
    "actions": [],
    "errors": [],
}


def redacted(value: str) -> str:
    return value.replace(GH_TOKEN, "<redacted>") if GH_TOKEN else value


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    process = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=merged,
    )
    if check and process.returncode != 0:
        command = " ".join(args)
        raise RuntimeError(
            redacted(
                f"command failed ({process.returncode}): {command}\n"
                f"stdout:\n{process.stdout[-12000:]}\n"
                f"stderr:\n{process.stderr[-12000:]}"
            )
        )
    return process


def gh_json(endpoint: str, *, method: str = "GET", payload: Any | None = None) -> Any:
    args = ["gh", "api", endpoint, "-X", method]
    if payload is None:
        process = run(args, timeout=120)
    else:
        args.extend(["--input", "-"])
        process = subprocess.run(
            args,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=120,
            env=os.environ.copy(),
        )
        if process.returncode != 0:
            raise RuntimeError(
                redacted(
                    f"gh api failed ({process.returncode}): {endpoint}\n"
                    f"stdout:\n{process.stdout[-8000:]}\n"
                    f"stderr:\n{process.stderr[-8000:]}"
                )
            )
    if not process.stdout.strip():
        return None
    return json.loads(process.stdout)


def gh_paginated(endpoint: str) -> list[Any]:
    process = run(["gh", "api", "--paginate", "--slurp", endpoint], timeout=180)
    pages = json.loads(process.stdout or "[]")
    flattened: list[Any] = []
    for page in pages:
        if isinstance(page, list):
            flattened.extend(page)
        elif isinstance(page, dict):
            flattened.append(page)
    return flattened


def clone_ref(ref: str, destination: Path) -> Path:
    run(["git", "init", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", REMOTE])
    run(["git", "-C", str(destination), "fetch", "--no-tags", "origin", ref], timeout=300)
    run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    return destination


def checkout_branch(branch: str, destination: Path) -> Path:
    run(["git", "init", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", REMOTE])
    run(
        ["git", "-C", str(destination), "fetch", "--no-tags", "origin", f"refs/heads/{branch}"],
        timeout=300,
    )
    run(["git", "-C", str(destination), "checkout", "-B", branch, "FETCH_HEAD"])
    return destination


def rev(path: Path, expression: str = "HEAD") -> str:
    return run(["git", "rev-parse", expression], cwd=path).stdout.strip()


def configure_author(path: Path, label: str) -> None:
    run(["git", "config", "user.name", label], cwd=path)
    run(
        ["git", "config", "user.email", "102159240+ProfHepta@users.noreply.github.com"],
        cwd=path,
    )


def recursively_collect_statuses(value: Any, path: str = "$") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if isinstance(value, dict):
        identity = None
        for key in ("gap_id", "id", "key"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("GAP-"):
                identity = candidate
                break
        if identity:
            status = value.get("status")
            if not isinstance(status, str):
                status = value.get("state")
            found.append(
                {
                    "id": identity,
                    "status": status if isinstance(status, str) else "unspecified",
                    "path": path,
                }
            )
        for key, child in value.items():
            found.extend(recursively_collect_statuses(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(recursively_collect_statuses(child, f"{path}[{index}]"))
    return found


def authoritative_snapshot() -> tuple[Path, str, str]:
    main = clone_ref("refs/heads/main", ROOT / "main")
    main_sha = rev(main)
    main_tree = rev(main, "HEAD^{tree}")
    required = ("CURRENT_PLAN.md", "GAP_REGISTER.json", "PRODUCT_GATES.json")
    missing = [name for name in required if not (main / name).is_file()]
    if missing:
        raise RuntimeError("missing main authority files: " + ", ".join(missing))
    gap_data = json.loads((main / "GAP_REGISTER.json").read_text(encoding="utf-8"))
    gaps = recursively_collect_statuses(gap_data)
    REPORT["main"] = {"commit": main_sha, "tree": main_tree}
    REPORT["gap_register"] = {
        "count": len(gaps),
        "status_counts": dict(Counter(item["status"] for item in gaps)),
        "gaps": gaps,
    }
    REPORT["actions"].append("read latest main Plan, Gap Register and Product Gates")
    return main, main_sha, main_tree


SPLIT_AUTHORITY_TEST = '''#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-trnm-server.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("trnm_server_split_checker", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("checker loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SplitAuthorityStorageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()

    def test_exact_ordered_include_authority_is_required(self):
        expected = "\\n".join(
            f'include!("authority_storage_parts/{part.name}");'
            for part in self.checker.AUTHORITY_STORAGE_PARTS
        ) + "\\n"
        self.assertEqual(
            self.checker.AUTHORITY_STORAGE_ROOT.read_text(encoding="utf-8"), expected
        )
        self.assertTrue(
            set(self.checker.AUTHORITY_STORAGE_PARTS) <= self.checker.REQUIRED_FILES
        )

    def test_omission_reordering_and_substitution_fail_closed(self):
        expected = self.checker.AUTHORITY_STORAGE_ROOT.read_text(encoding="utf-8")
        mutations = (
            expected.replace(
                'include!("authority_storage_parts/03_storage_list.rs");\\n', ""
            ),
            expected.replace(
                'include!("authority_storage_parts/01_authority.rs");\\n'
                'include!("authority_storage_parts/02_storage_batch.rs");\\n',
                'include!("authority_storage_parts/02_storage_batch.rs");\\n'
                'include!("authority_storage_parts/01_authority.rs");\\n',
            ),
            expected + 'include!("authority_storage_parts/99_unreviewed.rs");\\n',
        )
        original = Path.read_text
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                def replaced(path, *args, **kwargs):
                    if path == self.checker.AUTHORITY_STORAGE_ROOT:
                        return mutation
                    return original(path, *args, **kwargs)

                with patch.object(Path, "read_text", replaced):
                    with self.assertRaisesRegex(
                        SystemExit, "exact four-part include authority"
                    ):
                        self.checker.main()


if __name__ == "__main__":
    unittest.main()
'''


def repair_surface3_checker(surface: Path) -> bool:
    checker = surface / "scripts/check-trnm-server.py"
    parts_dir = surface / "crates/trnm-persistence-pg/tests/authority_storage_parts"
    authority_root = surface / "crates/trnm-persistence-pg/tests/authority_storage.rs"
    expected_parts = (
        "00_helpers.rs",
        "01_authority.rs",
        "02_storage_batch.rs",
        "03_storage_list.rs",
    )
    if not checker.is_file() or not authority_root.is_file():
        raise RuntimeError("Surface 3 server checker or authority test root is missing")
    if not all((parts_dir / name).is_file() for name in expected_parts):
        raise RuntimeError("Surface 3 split authority-storage test set is incomplete")

    changed = False
    text = checker.read_text(encoding="utf-8")
    if "AUTHORITY_STORAGE_PARTS" not in text:
        anchor = 'MODULE_ROOT = SERVER_ROOT / "trnm_server"\n'
        constants = (
            'AUTHORITY_STORAGE_ROOT = ROOT / "crates/trnm-persistence-pg/tests/authority_storage.rs"\n'
            'AUTHORITY_STORAGE_PARTS = tuple(\n'
            '    AUTHORITY_STORAGE_ROOT.parent / "authority_storage_parts" / name\n'
            '    for name in ("00_helpers.rs", "01_authority.rs", "02_storage_batch.rs", "03_storage_list.rs")\n'
            ')\n'
        )
        if text.count(anchor) != 1:
            raise RuntimeError("checker module-root insertion point is not exact")
        text = text.replace(anchor, anchor + constants, 1)

        old_required = '    ROOT / "crates/trnm-persistence-pg/tests/authority_storage.rs",\n'
        new_required = '    AUTHORITY_STORAGE_ROOT,\n    *AUTHORITY_STORAGE_PARTS,\n'
        if text.count(old_required) != 1:
            raise RuntimeError("checker authority-storage required-file entry is not exact")
        text = text.replace(old_required, new_required, 1)

        combined_anchor = '    combined = "\\n".join(sources.values())\n'
        validation = (
            '    authority_storage_root_key = AUTHORITY_STORAGE_ROOT.relative_to(ROOT)\n'
            '    expected_authority_storage_root = "\\n".join(\n'
            '        f\'include!("authority_storage_parts/{part.name}");\'\n'
            '        for part in AUTHORITY_STORAGE_PARTS\n'
            '    ) + "\\n"\n'
            '    if sources[authority_storage_root_key] != expected_authority_storage_root:\n'
            '        fail("authority_storage.rs must remain the exact four-part include authority")\n\n'
        )
        if text.count(combined_anchor) != 1:
            raise RuntimeError("checker combined-source insertion point is not exact")
        text = text.replace(combined_anchor, validation + combined_anchor, 1)
        checker.write_text(text, encoding="utf-8")
        changed = True

    hostile = surface / "tests/control_plane/test_trnm_server_split_authority_storage_contract.py"
    if not hostile.is_file() or hostile.read_text(encoding="utf-8") != SPLIT_AUTHORITY_TEST:
        hostile.write_text(SPLIT_AUTHORITY_TEST, encoding="utf-8")
        changed = True
    return changed


def validate_candidate(path: Path, *, full: bool) -> None:
    run(["git", "diff", "--check"], cwd=path, timeout=120)
    run(["python3", "-m", "compileall", "-q", "scripts", "tests"], cwd=path, timeout=600)
    run(["python3", "scripts/check-trnm-server.py"], cwd=path, timeout=300)
    run(
        [
            "python3",
            "-m",
            "unittest",
            "tests.control_plane.test_trnm_server_split_authority_storage_contract",
            "-v",
        ],
        cwd=path,
        timeout=300,
    )
    run(["cargo", "fmt", "--all"], cwd=path, timeout=300)
    run(["cargo", "fmt", "--all", "--", "--check"], cwd=path, timeout=300)
    if full:
        run(
            ["cargo", "test", "--workspace", "--all-targets", "--locked"],
            cwd=path,
            timeout=5400,
        )
        run(
            [
                "cargo",
                "clippy",
                "--workspace",
                "--all-targets",
                "--locked",
                "--",
                "-D",
                "warnings",
            ],
            cwd=path,
            timeout=5400,
        )
        run(
            [
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/control_plane",
                "-p",
                "test_*.py",
                "-v",
            ],
            cwd=path,
            timeout=3600,
        )
        for script in (
            "scripts/check-plan.py",
            "scripts/derive-gates.py",
            "scripts/check-status-transitions.py",
            "scripts/check-schema-authority.py",
        ):
            if (path / script).is_file():
                run(["python3", script], cwd=path, timeout=600)
    run(["git", "diff", "--check"], cwd=path, timeout=120)


def commit_if_changed(path: Path, branch: str, subject: str, body: str) -> str:
    status = run(["git", "status", "--porcelain"], cwd=path).stdout
    if not status.strip():
        return rev(path)
    configure_author(path, "Plan v3.2 full closure controller")
    run(["git", "add", "--all"], cwd=path)
    run(["git", "diff", "--cached", "--check"], cwd=path)
    run(["git", "commit", "-m", subject, "-m", body], cwd=path)
    new_head = rev(path)
    run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=path, timeout=600)
    return new_head


def request_reviewers(number: int) -> None:
    try:
        gh_json(
            f"repos/{REPOSITORY}/pulls/{number}/requested_reviewers",
            method="POST",
            payload={"reviewers": list(REVIEWERS)},
        )
    except Exception as error:  # Reviewer absence must not mutate admission truth.
        REPORT["errors"].append(f"review request for PR #{number}: {error}")


def update_surface3_binding(path: Path, number: int) -> dict[str, Any]:
    pull = gh_json(f"repos/{REPOSITORY}/pulls/{number}")
    if pull.get("state") != "open":
        return {"state": pull.get("state"), "merged": bool(pull.get("merged_at"))}
    head = pull["head"]["sha"]
    base = pull["base"]["sha"]
    run(["git", "fetch", "--no-tags", "origin", head, base], cwd=path, timeout=300)
    head_tree = run(["git", "rev-parse", f"{head}^{{tree}}"], cwd=path).stdout.strip()
    base_tree = run(["git", "rev-parse", f"{base}^{{tree}}"], cwd=path).stdout.strip()
    merge = run(["git", "merge-tree", "--write-tree", base, head], cwd=path, check=False)
    merge_tree = merge.stdout.strip().splitlines()[0] if merge.returncode == 0 else "conflict"
    marker_start = "<!-- plan-v32-current-binding:start -->"
    marker_end = "<!-- plan-v32-current-binding:end -->"
    binding = f'''{marker_start}
## Current exact-object binding

```text
repository         {REPOSITORY}
base branch        {pull["base"]["ref"]}
base commit        {base}
base tree          {base_tree}
head branch        {pull["head"]["ref"]}
head commit        {head}
head tree          {head_tree}
prospective tree   {merge_tree}
draft              {str(bool(pull.get("draft"))).lower()}
history_rewritten  false
administrator_bypass false
```

This binding supersedes older tuples in the historical narrative.  Source and
execution credit remain bounded.  Independent exact-head approval, retained
accepted evidence, complete public API/SDK/oracle coverage, migration proof,
production authorization, public-online cutover, replacement and retirement
remain false until their own contracts are actually satisfied.
{marker_end}'''
    body = pull.get("body") or ""
    pattern = re.compile(
        re.escape(marker_start) + r".*?" + re.escape(marker_end), re.DOTALL
    )
    if pattern.search(body):
        body = pattern.sub(binding, body, count=1)
    else:
        body = binding + "\n\n" + body
    gh_json(
        f"repos/{REPOSITORY}/pulls/{number}",
        method="PATCH",
        payload={"body": body},
    )
    request_reviewers(number)
    return {
        "state": "open",
        "draft": bool(pull.get("draft")),
        "head": head,
        "head_tree": head_tree,
        "base": base,
        "base_tree": base_tree,
        "prospective_tree": merge_tree,
    }


def latest_reviews(number: int) -> dict[str, dict[str, Any]]:
    reviews = gh_paginated(f"repos/{REPOSITORY}/pulls/{number}/reviews?per_page=100")
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        login = ((review.get("user") or {}).get("login") or "").lower()
        if not login:
            continue
        current = latest.get(login)
        current_key = (current or {}).get("submitted_at") or ""
        candidate_key = review.get("submitted_at") or ""
        if current is None or candidate_key >= current_key:
            latest[login] = review
    return latest


def check_runs(head: str) -> list[dict[str, Any]]:
    response = gh_json(
        f"repos/{REPOSITORY}/commits/{head}/check-runs?per_page=100"
    )
    return list((response or {}).get("check_runs") or [])


def exact_head_gate(number: int, *, poll_seconds: int = 2700) -> dict[str, Any]:
    started = time.monotonic()
    last: list[dict[str, Any]] = []
    while time.monotonic() - started <= poll_seconds:
        pull = gh_json(f"repos/{REPOSITORY}/pulls/{number}")
        if pull.get("state") != "open":
            return {"state": pull.get("state"), "merged": bool(pull.get("merged_at"))}
        head = pull["head"]["sha"]
        last = check_runs(head)
        if last and all(item.get("status") == "completed" for item in last):
            break
        time.sleep(30)
    pull = gh_json(f"repos/{REPOSITORY}/pulls/{number}")
    head = pull["head"]["sha"]
    current = [item for item in last if item.get("head_sha") in (None, head)]
    conclusions = Counter((item.get("conclusion") or "pending") for item in current)
    names = sorted(
        {
            str(item.get("name"))
            for item in current
            if item.get("conclusion") != "success"
        }
    )
    latest = latest_reviews(number)
    author = ((pull.get("user") or {}).get("login") or "").lower()
    approvals = sorted(
        login
        for login, review in latest.items()
        if login != author
        and review.get("state") == "APPROVED"
        and review.get("commit_id") == head
    )
    all_success = bool(current) and all(
        item.get("status") == "completed" and item.get("conclusion") == "success"
        for item in current
    )
    return {
        "state": pull.get("state"),
        "draft": bool(pull.get("draft")),
        "head": head,
        "check_count": len(current),
        "conclusion_counts": dict(conclusions),
        "non_success_checks": names,
        "current_exact_head_approvals": approvals,
        "all_checks_terminal_success": all_success,
        "mergeable": pull.get("mergeable"),
        "mergeable_state": pull.get("mergeable_state"),
    }


def maybe_ordinary_merge(number: int, gate: dict[str, Any]) -> bool:
    if gate.get("state") != "open":
        return bool(gate.get("merged"))
    if not gate.get("all_checks_terminal_success"):
        return False
    if not gate.get("current_exact_head_approvals"):
        return False
    if gate.get("mergeable") is not True:
        return False
    pull = gh_json(f"repos/{REPOSITORY}/pulls/{number}")
    if pull.get("draft"):
        ready = run(
            ["gh", "pr", "ready", str(number), "--repo", REPOSITORY],
            check=False,
            timeout=120,
        )
        if ready.returncode != 0:
            REPORT["errors"].append(
                f"PR #{number} could not leave Draft through ordinary API: "
                + redacted(ready.stderr[-2000:])
            )
            return False
    merged = run(
        [
            "gh",
            "pr",
            "merge",
            str(number),
            "--repo",
            REPOSITORY,
            "--squash",
            "--delete-branch=false",
        ],
        check=False,
        timeout=300,
    )
    if merged.returncode != 0:
        REPORT["errors"].append(
            f"ordinary protected merge for PR #{number} was rejected: "
            + redacted(merged.stderr[-3000:])
        )
        return False
    reread = gh_json(f"repos/{REPOSITORY}/pulls/{number}")
    return bool(reread.get("merged_at"))


def repair_and_qualify_surface3() -> dict[str, Any]:
    pull = gh_json(f"repos/{REPOSITORY}/pulls/{SURFACE3_PR}")
    if pull.get("state") != "open":
        return {"state": pull.get("state"), "merged": bool(pull.get("merged_at"))}
    surface = checkout_branch(SURFACE3_BRANCH, ROOT / "surface3")
    original_head = rev(surface)
    if original_head != pull["head"]["sha"]:
        raise RuntimeError("Surface 3 branch head differs from PR head")
    changed = repair_surface3_checker(surface)
    validate_candidate(surface, full=True)
    new_head = commit_if_changed(
        surface,
        SURFACE3_BRANCH,
        "fix(server): bind checker to split authority-storage tests",
        "Require the exact ordered four-part authority_storage include authority, scan every retained split test part, and add hostile omission, reordering and substitution regressions. No source, evidence, compatibility, production, cutover or retirement gate is weakened.",
    )
    REPORT["actions"].append(
        "validated and fast-forwarded Surface 3 checker repair"
        if changed
        else "revalidated existing Surface 3 checker repair"
    )
    binding = update_surface3_binding(surface, SURFACE3_PR)
    binding["original_head"] = original_head
    binding["current_head"] = new_head
    gate = exact_head_gate(SURFACE3_PR)
    binding["gate"] = gate
    binding["ordinary_merge_completed"] = maybe_ordinary_merge(SURFACE3_PR, gate)
    return binding


def materialize_ops_security(main_sha: str) -> dict[str, Any]:
    branch = f"feature/plan-v32-ops-security-closure-{main_sha[:12]}"
    exists = run(
        ["git", "ls-remote", "--exit-code", "--heads", REMOTE, f"refs/heads/{branch}"],
        check=False,
        timeout=120,
    ).returncode == 0
    if exists:
        candidates = gh_json(
            f"repos/{REPOSITORY}/pulls?state=open&head={OWNER}:{branch}&per_page=100"
        ) or []
        if candidates:
            request_reviewers(int(candidates[0]["number"]))
            return {
                "branch": branch,
                "status": "existing-open-candidate",
                "pull_request": int(candidates[0]["number"]),
            }
        return {"branch": branch, "status": "existing-branch-without-open-pr"}

    driver = checkout_branch(
        "ops/game-exact-source-snapshot-20260910", ROOT / "ops-driver"
    )
    controller = clone_ref(CONTROLLER_COMMIT, ROOT / "immutable-controller")
    target = clone_ref(main_sha, ROOT / "ops-target")
    run(["git", "checkout", "-B", branch], cwd=target)
    materializer = driver / "tools/materialize_plan_v32_closure.py"
    if not materializer.is_file():
        raise RuntimeError("current ops driver lacks Plan v3.2 materializer")
    run(
        [
            "python3",
            str(materializer),
            "--root",
            str(target),
            "--controller",
            str(controller),
        ],
        cwd=target,
        timeout=1800,
    )
    if not run(["git", "status", "--porcelain"], cwd=target).stdout.strip():
        return {"branch": branch, "status": "no-delta-from-current-main"}
    validate_candidate(target, full=True)
    configure_author(target, "Plan v3.2 operations materializer")
    run(["git", "add", "--all"], cwd=target)
    changed = run(["git", "diff", "--cached", "--name-only"], cwd=target).stdout.splitlines()
    if any(name.startswith(".github/workflows/") for name in changed):
        raise RuntimeError("materialized candidate unexpectedly changes workflow definitions")
    run(["git", "diff", "--cached", "--check"], cwd=target)
    run(
        [
            "git",
            "commit",
            "-m",
            "plan-v3.2: materialize security durability and operations candidates",
            "-m",
            "Add fail-closed remote MAC provider and Unix transport boundaries, exhaustive durability state modeling, reproducible denominator review packets, PostgreSQL and CockroachDB fault and recovery harnesses, capacity/endurance segmentation, and a bounded cutover state machine. Preserve all acceptance, compatibility, production, public-online, cutover, replacement and retirement claims as false pending exact execution and independent acceptance.",
        ],
        cwd=target,
    )
    head = rev(target)
    tree = rev(target, "HEAD^{tree}")
    run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=target, timeout=600)
    body_file = ROOT / "ops-pr-body.md"
    body_file.write_text(
        f'''## Plan v3.2 bounded operations/security source candidate

```text
repository          {REPOSITORY}
base                main@{main_sha}
head branch         {branch}
head commit         {head}
head tree           {tree}
history_rewritten   false
administrator_bypass false
```

This candidate materializes fail-closed security, durability, database-fault,
recovery, capacity/endurance, denominator-review and cutover-control source
contracts.  It is deliberately a Draft.  Source presence does not constitute
retained accepted evidence or close any gap.

```text
accepted evidence = false
independently accepted = false
complete Nakama compatibility = false
production-ready = false
public-online = false
cutover authorized = false
replacement authorized = false
Nakama retired = false
```
''',
        encoding="utf-8",
    )
    created = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            REPOSITORY,
            "--base",
            "main",
            "--head",
            branch,
            "--draft",
            "--title",
            "[Plan v3.2][Ops/Security] Materialize bounded durability and recovery candidates",
            "--body-file",
            str(body_file),
        ],
        timeout=180,
    )
    match = re.search(r"/(\d+)\s*$", created.stdout.strip())
    number = int(match.group(1)) if match else None
    if number is not None:
        request_reviewers(number)
    REPORT["actions"].append("published bounded operations/security source candidate")
    return {
        "branch": branch,
        "status": "published-draft-candidate",
        "head": head,
        "tree": tree,
        "pull_request": number,
        "changed_files": changed,
    }


def qualify_existing_branch(branch: str, title: str) -> dict[str, Any]:
    branch_probe = run(
        ["git", "ls-remote", "--exit-code", "--heads", REMOTE, f"refs/heads/{branch}"],
        check=False,
        timeout=120,
    )
    if branch_probe.returncode != 0:
        return {"branch": branch, "status": "absent"}
    pulls = gh_json(
        f"repos/{REPOSITORY}/pulls?state=open&head={OWNER}:{branch}&per_page=100"
    ) or []
    if pulls:
        number = int(pulls[0]["number"])
        request_reviewers(number)
        return {"branch": branch, "status": "existing-open-pr", "pull_request": number}
    probe = clone_ref(f"refs/heads/{branch}", ROOT / ("candidate-" + branch.rsplit("/", 1)[-1]))
    run(["git", "fetch", "--no-tags", "origin", "refs/heads/main"], cwd=probe, timeout=300)
    head = rev(probe)
    main = rev(probe, "FETCH_HEAD")
    if run(["git", "merge-base", "--is-ancestor", head, main], cwd=probe, check=False).returncode == 0:
        return {"branch": branch, "status": "already-contained-in-main"}
    merge = run(["git", "merge-tree", "--write-tree", main, head], cwd=probe, check=False)
    if merge.returncode != 0:
        return {"branch": branch, "status": "conflicts-with-current-main", "head": head}
    run(["git", "diff", "--check", main, head], cwd=probe, timeout=120)
    body_file = ROOT / (branch.rsplit("/", 1)[-1] + ".md")
    body_file.write_text(
        f'''## Existing Plan v3.2 bounded candidate inventory

```text
branch               {branch}
head                 {head}
current main         {main}
prospective tree     {merge.stdout.strip().splitlines()[0]}
history_rewritten    false
administrator_bypass false
```

Opened as Draft for exact qualification and independent review.  No gap,
compatibility, accepted-evidence, production, public-online, cutover,
replacement or retirement credit is granted by opening this pull request.
''',
        encoding="utf-8",
    )
    created = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            REPOSITORY,
            "--base",
            "main",
            "--head",
            branch,
            "--draft",
            "--title",
            title,
            "--body-file",
            str(body_file),
        ],
        timeout=180,
    )
    match = re.search(r"/(\d+)\s*$", created.stdout.strip())
    number = int(match.group(1)) if match else None
    if number is not None:
        request_reviewers(number)
    REPORT["actions"].append(f"opened existing branch {branch} for bounded review")
    return {"branch": branch, "status": "opened-draft-pr", "pull_request": number, "head": head}


def update_parent_issue() -> None:
    marker = "<!-- plan-v32-full-closure-controller -->"
    gap = REPORT.get("gap_register") or {}
    surface = REPORT.get("surface3") or {}
    ops = REPORT.get("ops_security") or {}
    lines = [
        marker,
        "## Plan v3.2 full-closure controller readback",
        "",
        f"- run: `{REPORT.get('run_id')}` attempt `{REPORT.get('run_attempt')}`",
        f"- main: `{(REPORT.get('main') or {}).get('commit')}`",
        f"- gap records discovered: `{gap.get('count', 0)}`",
        f"- gap status counts: `{json.dumps(gap.get('status_counts', {}), sort_keys=True)}`",
        f"- Surface 3: `{json.dumps(surface, sort_keys=True)}`",
        f"- operations/security candidate: `{json.dumps(ops, sort_keys=True)}`",
        "- administrator bypass: `false`",
        "- history rewritten: `false`",
        "- accepted evidence promoted by controller: `false`",
        "- production/cutover/retirement claims promoted by controller: `false`",
        "",
        "The controller only records facts bound to current remote objects. Remaining",
        "gaps stay open until their own implementation, execution, retention and",
        "independent-acceptance contracts are actually satisfied.",
    ]
    body = "\n".join(lines)
    comments = gh_paginated(f"repos/{REPOSITORY}/issues/129/comments?per_page=100")
    current_user = (gh_json("user") or {}).get("login", "").lower()
    existing = next(
        (
            item
            for item in comments
            if marker in (item.get("body") or "")
            and ((item.get("user") or {}).get("login") or "").lower() == current_user
        ),
        None,
    )
    if existing:
        gh_json(
            f"repos/{REPOSITORY}/issues/comments/{existing['id']}",
            method="PATCH",
            payload={"body": body},
        )
    else:
        gh_json(
            f"repos/{REPOSITORY}/issues/129/comments",
            method="POST",
            payload={"body": body},
        )


def emit_report() -> None:
    REPORT["finished_at_epoch"] = int(time.time())
    output = json.dumps(REPORT, indent=2, sort_keys=True)
    print(output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Plan v3.2 full-closure controller\n\n```json\n")
            handle.write(output)
            handle.write("\n```\n")


def main() -> int:
    if not GH_TOKEN:
        raise RuntimeError("GH_TOKEN is required")
    run(["gh", "auth", "setup-git"], timeout=120)
    _, main_sha, _ = authoritative_snapshot()

    for number in (163, 164):
        try:
            pull = gh_json(f"repos/{REPOSITORY}/pulls/{number}")
            if pull.get("state") == "open":
                gate = exact_head_gate(number, poll_seconds=60)
                REPORT[f"surface_pr_{number}"] = gate
                REPORT[f"surface_pr_{number}"]["ordinary_merge_completed"] = (
                    maybe_ordinary_merge(number, gate)
                )
        except Exception as error:
            REPORT["errors"].append(f"PR #{number} admission check: {error}")

    try:
        REPORT["surface3"] = repair_and_qualify_surface3()
    except Exception as error:
        REPORT["surface3"] = {"status": "failed", "error": str(error)}
        REPORT["errors"].append(f"Surface 3: {error}")

    # Refresh main after any ordinary protected admission.
    shutil.rmtree(ROOT / "main-refresh", ignore_errors=True)
    refreshed = clone_ref("refs/heads/main", ROOT / "main-refresh")
    refreshed_main = rev(refreshed)
    REPORT["main_after_surface_admission"] = refreshed_main

    try:
        REPORT["ops_security"] = materialize_ops_security(refreshed_main)
    except Exception as error:
        REPORT["ops_security"] = {"status": "failed", "error": str(error)}
        REPORT["errors"].append(f"operations/security materialization: {error}")

    known = (
        (
            "feature/plan-v32-storage-public-api-20260909",
            "[Plan v3.2][Storage API] Qualify existing public API candidate",
        ),
        (
            "feature/plan-v32-competition-core-20260909",
            "[Plan v3.2][Competition] Qualify existing competition core candidate",
        ),
    )
    REPORT["existing_candidates"] = []
    for branch, title in known:
        try:
            REPORT["existing_candidates"].append(qualify_existing_branch(branch, title))
        except Exception as error:
            REPORT["existing_candidates"].append(
                {"branch": branch, "status": "failed", "error": str(error)}
            )
            REPORT["errors"].append(f"candidate {branch}: {error}")

    try:
        update_parent_issue()
        REPORT["actions"].append("updated parent issue #129 with exact readback")
    except Exception as error:
        REPORT["errors"].append(f"parent issue readback: {error}")

    emit_report()
    # Candidate failures remain visible but do not erase successful bounded work.
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        REPORT["errors"].append(str(exc))
        emit_report()
        raise
