#!/usr/bin/env python3
"""Create a read-only, content-addressed GitHub governance read-back packet."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ORIGIN = "https://api.github.com"
REPO = "TrillionniumFoundation/TrillionniumGame"
REPO_ID = 1323087470
BRANCH = "main"
REQUIRED_CHECK = "trillionnium-game-merge-gate"
REQUIRED_CHECK_APP_ID = 15368
TOKEN_ENV = "TRNM_GITHUB_ADMIN_AUDIT_TOKEN"
MAX_BYTES = 2 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_HEADERS = {
    "content-type", "date", "etag", "last-modified", "link",
    "x-github-request-id", "x-github-api-version-selected",
    "x-oauth-scopes", "x-accepted-oauth-scopes",
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
}


class ReadbackError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise ReadbackError(f"redirect rejected: HTTP {code}")


class GitHubApi:
    def __init__(self, token: str, opener: Any | None = None, timeout: float = 30.0):
        if len(token) < 20 or any(char.isspace() for char in token):
            raise ReadbackError(f"{TOKEN_ENV} is absent or malformed")
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    def get(self, path: str) -> tuple[int, dict[str, str], Any]:
        allowed = path == "/user" or path == f"/repos/{REPO}" or path.startswith(f"/repos/{REPO}/")
        if not allowed:
            raise ReadbackError(f"unapproved API path: {path}")
        url = ORIGIN + path
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise ReadbackError("API origin drift")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "TrillionniumGame-governance-readback/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ReadbackError(f"GitHub API transport failed for {path}: {error}") from error
        try:
            status = int(response.getcode())
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ReadbackError(f"response exceeds {MAX_BYTES} bytes")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ReadbackError(f"non-JSON response for {path}") from error
            safe = {
                str(name).lower(): str(value)
                for name, value in response.headers.items()
                if str(name).lower() in SAFE_HEADERS
            }
            return status, safe, value
        finally:
            response.close()


def enabled(value: Any, key: str, default: bool = False) -> bool:
    if not isinstance(value, dict) or key not in value:
        return default
    item = value[key]
    if isinstance(item, dict):
        item = item.get("enabled")
    return item is True


def nested(value: Any, *keys: str, default: Any = None) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def has_next_page(headers: dict[str, str]) -> bool:
    return 'rel="next"' in headers.get("link", "").lower()


def has_stable_human_environment_reviewer(rule: Any) -> bool:
    if not isinstance(rule, dict) or rule.get("type") != "required_reviewers":
        return False
    reviewers = rule.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers:
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "User"
        and isinstance(item.get("reviewer"), dict)
        and isinstance(item["reviewer"].get("id"), int)
        and item["reviewer"]["id"] > 0
        and isinstance(item["reviewer"].get("login"), str)
        and bool(item["reviewer"]["login"].strip())
        for item in reviewers
    )


class PacketTarget:
    def __init__(self, writer: "PacketWriter", name: str):
        self.writer = writer
        self.name = name


class PacketMember:
    def __init__(self, writer: "PacketWriter", name: str):
        self.writer = writer
        self.name = name

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, PacketMember):
            return NotImplemented
        return self.name < other.name

    def read_bytes(self) -> bytes:
        return self.writer.read_verified(self.name)


class PacketWriter:
    NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    CHUNK = 64 * 1024

    def __init__(self, path: Path):
        required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
        if os.name != "posix" or any(not hasattr(os, name) for name in required):
            raise ReadbackError("descriptor-relative packet custody is unavailable")
        absolute = path.expanduser()
        if not absolute.is_absolute():
            absolute = Path.cwd() / absolute
        absolute = absolute.absolute()
        parts = absolute.parts
        if not parts or parts[0] != "/" or len(parts) == 1:
            raise ReadbackError("output must name a non-root directory")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        try:
            for component in parts[1:]:
                if component in {"", ".", ".."}:
                    raise ReadbackError("output path contains a noncanonical component")
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            if os.listdir(descriptor):
                raise ReadbackError("output must be a real empty directory")
            self.path = absolute
            self.descriptor = descriptor
            self.directory_identity = os.fstat(descriptor)
            self.records: dict[str, dict[str, int | str]] = {}
            self.closed = False
        except OSError as error:
            os.close(descriptor)
            raise ReadbackError("output directory could not be securely opened") from error
        except BaseException:
            os.close(descriptor)
            raise

    def __truediv__(self, name: str) -> PacketTarget:
        return PacketTarget(self, name)

    def _require_open(self) -> None:
        if self.closed:
            raise ReadbackError("packet writer is closed")

    def _check_directory_identity(self) -> None:
        self._require_open()
        current = os.fstat(self.descriptor)
        try:
            named = os.stat(self.path, follow_symlinks=False)
        except OSError as error:
            raise ReadbackError("output directory identity is unavailable") from error
        expected = self.directory_identity
        if not os.path.isdir(self.path) or (
            current.st_dev, current.st_ino, named.st_dev, named.st_ino
        ) != (expected.st_dev, expected.st_ino, expected.st_dev, expected.st_ino):
            raise ReadbackError("output directory identity changed")

    @staticmethod
    def _identity(stat_result: os.stat_result) -> tuple[int, ...]:
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_mode,
            stat_result.st_nlink,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )

    def write(self, name: str, data: bytes) -> None:
        self._check_directory_identity()
        if not self.NAME.fullmatch(name) or name in self.records:
            raise ReadbackError("packet member name is invalid or duplicated")
        if name == "SHA256SUMS":
            for member in sorted(self.records):
                self._read_verified(member, allow_manifest=False)
            expected = "".join(
                f"{record['sha256']}  {member}\n"
                for member, record in sorted(self.records.items())
            ).encode()
            if data != expected:
                raise ReadbackError("SHA256SUMS does not match verified packet members")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(name, flags, 0o600, dir_fd=self.descriptor)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise ReadbackError("packet member write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            stat_result = os.fstat(descriptor)
            if stat_result.st_size != len(data):
                raise ReadbackError("packet member size changed during write")
            self.records[name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "dev": stat_result.st_dev,
                "ino": stat_result.st_ino,
                "mode": stat_result.st_mode,
                "nlink": stat_result.st_nlink,
                "mtime_ns": stat_result.st_mtime_ns,
                "ctime_ns": stat_result.st_ctime_ns,
            }
        finally:
            os.close(descriptor)
        if name == "SHA256SUMS":
            self._check_exact_member_set()
            for member in sorted(self.records):
                self._read_verified(member, allow_manifest=True)
            os.fsync(self.descriptor)
            self.close()

    def _check_exact_member_set(self) -> None:
        self._check_directory_identity()
        if set(os.listdir(self.descriptor)) != set(self.records):
            raise ReadbackError("packet directory contains an untracked member")

    def iterdir(self) -> list[PacketMember]:
        self._check_exact_member_set()
        return [PacketMember(self, name) for name in self.records]

    def read_verified(self, name: str) -> bytes:
        return self._read_verified(name, allow_manifest=False)

    def _read_verified(self, name: str, *, allow_manifest: bool) -> bytes:
        self._check_exact_member_set()
        record = self.records.get(name)
        if record is None or (name == "SHA256SUMS" and not allow_manifest):
            raise ReadbackError("packet member is not available for sealing")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(name, flags, dir_fd=self.descriptor)
        try:
            before = os.fstat(descriptor)
            expected_identity = (
                record["dev"], record["ino"], record["mode"], record["nlink"],
                record["size"], record["mtime_ns"], record["ctime_ns"],
            )
            if self._identity(before) != expected_identity:
                raise ReadbackError("packet member identity changed before sealing")
            remaining = int(record["size"]) + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, min(self.CHUNK, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
            if self._identity(after) != expected_identity:
                raise ReadbackError("packet member identity changed during sealing")
            if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ReadbackError("packet member bytes changed before sealing")
            return data
        finally:
            os.close(descriptor)

    def close(self) -> None:
        if not getattr(self, "closed", True):
            os.close(self.descriptor)
            self.closed = True

    def __del__(self) -> None:
        self.close()


def write(path: PacketTarget, data: bytes) -> None:
    if not isinstance(path, PacketTarget):
        raise ReadbackError("packet writes must use a pinned directory descriptor")
    path.writer.write(path.name, data)


def prepare(path: Path) -> PacketWriter:
    return PacketWriter(path)


def retain(output: PacketWriter, key: str, path: str, result: tuple[int, dict[str, str], Any]) -> Any:
    status, headers, value = result
    body = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    write(output / f"{key}.json", body)
    metadata = {
        "schema": "trillionnium.github-api-read.v1",
        "method": "GET", "origin": ORIGIN, "path": path, "status": status,
        "headers": headers, "body_file": f"{key}.json",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_size_bytes": len(body), "authorization_retained": False,
    }
    write(output / f"{key}.http.json", json.dumps(metadata, sort_keys=True, indent=2).encode() + b"\n")
    return value


def capture(api: Any, output_path: Path, expected_main: str, environments: list[str]) -> dict[str, Any]:
    if not SHA.fullmatch(expected_main):
        raise ReadbackError("expected main must be a lowercase 40-character SHA")
    if not environments:
        raise ReadbackError("at least one protected environment must be named")
    if any(not name or "/" in name or name in {".", ".."} for name in environments):
        raise ReadbackError("environment names must be canonical single components")
    environments = list(dict.fromkeys(environments))
    output = prepare(output_path)
    reads: dict[str, tuple[int, dict[str, str], Any]] = {}

    paths = {
        "actor": "/user",
        "repository": f"/repos/{REPO}",
        "branch": f"/repos/{REPO}/branches/{BRANCH}",
        "main_checks": f"/repos/{REPO}/commits/{expected_main}/check-runs?per_page=100",
        "protection": f"/repos/{REPO}/branches/{BRANCH}/protection",
        "rulesets": f"/repos/{REPO}/rulesets?includes_parents=true&per_page=100",
        "actions": f"/repos/{REPO}/actions/permissions",
        "workflow_permissions": f"/repos/{REPO}/actions/permissions/workflow",
        "environments": f"/repos/{REPO}/environments?per_page=100",
    }
    values: dict[str, Any] = {}
    for key, path in paths.items():
        reads[key] = api.get(path)
        values[key] = retain(output, key, path, reads[key])

    actor = values["actor"]
    login = actor.get("login") if isinstance(actor, dict) else None
    if not isinstance(login, str) or not login:
        raise ReadbackError("authenticated actor has no stable login")
    permission_path = f"/repos/{REPO}/collaborators/{urllib.parse.quote(login, safe='')}/permission"
    reads["actor_permission"] = api.get(permission_path)
    values["actor_permission"] = retain(output, "actor_permission", permission_path, reads["actor_permission"])

    ruleset_keys: dict[int, str] = {}
    rulesets = values["rulesets"] if isinstance(values["rulesets"], list) else []
    for row in rulesets:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        ruleset_id = row["id"]
        key = f"ruleset_{ruleset_id}"
        path = f"/repos/{REPO}/rulesets/{ruleset_id}"
        reads[key] = api.get(path)
        values[key] = retain(output, key, path, reads[key])
        ruleset_keys[ruleset_id] = key

    environment_keys: dict[str, str] = {}
    for name in environments:
        key = "environment_" + hashlib.sha256(name.encode()).hexdigest()[:16]
        path = f"/repos/{REPO}/environments/{urllib.parse.quote(name, safe='')}"
        reads[key] = api.get(path)
        values[key] = retain(output, key, path, reads[key])
        environment_keys[name] = key

    repository, branch, checks = values["repository"], values["branch"], values["main_checks"]
    protection, actions, workflow = values["protection"], values["actions"], values["workflow_permissions"]
    actor_permission = values["actor_permission"] if isinstance(values["actor_permission"], dict) else {}
    review = nested(protection, "required_pull_request_reviews", default={})
    required = nested(protection, "required_status_checks", default={})
    context_present = any(
        isinstance(item, dict)
        and item.get("context") == REQUIRED_CHECK
        and item.get("app_id") == REQUIRED_CHECK_APP_ID
        for item in (required.get("checks", []) if isinstance(required, dict) else [])
    )
    check_rows = checks.get("check_runs", []) if isinstance(checks, dict) else []
    merge_gate_rows = [
        row for row in check_rows
        if isinstance(row, dict)
        and row.get("name") == REQUIRED_CHECK
        and nested(row, "app", "id") == REQUIRED_CHECK_APP_ID
        and isinstance(row.get("id"), int)
    ]
    latest_merge_gate = max(merge_gate_rows, key=lambda row: row["id"], default=None)
    environment_names = {
        row.get("name") for row in nested(values["environments"], "environments", default=[])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    bypass = review.get("bypass_pull_request_allowances", {}) if isinstance(review, dict) else None
    branch_bypass_empty = isinstance(bypass, dict) and all(value in (None, [], {}) for value in bypass.values())
    ruleset_details_valid = all(
        isinstance(values[key], dict) and values[key].get("id") == ruleset_id
        for ruleset_id, key in ruleset_keys.items()
    )
    ruleset_bypass_empty = all(
        isinstance(values[key], dict) and values[key].get("bypass_actors") in (None, [])
        for key in ruleset_keys.values()
    )
    protected_environments = all(
        isinstance(values[key], dict)
        and values[key].get("name") == name
        and values[key].get("prevent_self_review") is True
        and any(
            has_stable_human_environment_reviewer(rule)
            for rule in values[key].get("protection_rules", [])
        )
        for name, key in environment_keys.items()
    )

    assertions = {
        "all_reads_http_200": all(status == 200 for status, _, _ in reads.values()),
        "authenticated_human_admin": (
            isinstance(actor, dict) and actor.get("type") == "User"
            and isinstance(actor.get("id"), int)
            and actor_permission.get("permission") == "admin"
        ),
        "repository_identity": (
            isinstance(repository, dict) and repository.get("id") == REPO_ID
            and repository.get("full_name") == REPO and repository.get("default_branch") == BRANCH
            and repository.get("archived") is False
        ),
        "main_identity": nested(branch, "commit", "sha") == expected_main,
        "main_protected": isinstance(branch, dict) and branch.get("protected") is True,
        "main_check_collection_complete": (
            isinstance(checks, dict) and isinstance(checks.get("total_count"), int)
            and checks.get("total_count") == len(check_rows)
            and not has_next_page(reads["main_checks"][1])
        ),
        "successful_exact_main_merge_gate": (
            isinstance(latest_merge_gate, dict)
            and latest_merge_gate.get("status") == "completed"
            and latest_merge_gate.get("conclusion") == "success"
        ),
        "strict_required_check": isinstance(required, dict) and required.get("strict") is True and context_present,
        "admins_enforced": enabled(protection, "enforce_admins"),
        "stale_reviews_dismissed": isinstance(review, dict) and review.get("dismiss_stale_reviews") is True,
        "code_owner_review_required": isinstance(review, dict) and review.get("require_code_owner_reviews") is True,
        "latest_push_approval_required": isinstance(review, dict) and review.get("require_last_push_approval") is True,
        "approval_required": isinstance(review, dict) and review.get("required_approving_review_count", 0) >= 1,
        "conversation_resolution_required": enabled(protection, "required_conversation_resolution"),
        "linear_history_required": enabled(protection, "required_linear_history"),
        "force_push_forbidden": not enabled(protection, "allow_force_pushes", True),
        "deletion_forbidden": not enabled(protection, "allow_deletions", True),
        "branch_bypass_empty": branch_bypass_empty,
        "actions_enabled": isinstance(actions, dict) and actions.get("enabled") is True,
        "workflow_permissions_read_only": isinstance(workflow, dict) and workflow.get("default_workflow_permissions") == "read",
        "actions_cannot_approve": isinstance(workflow, dict) and workflow.get("can_approve_pull_request_reviews") is False,
        "rulesets_read_back": (
            reads["rulesets"][0] == 200
            and isinstance(values["rulesets"], list)
            and not has_next_page(reads["rulesets"][1])
            and all(reads[key][0] == 200 for key in ruleset_keys.values())
            and ruleset_details_valid
        ),
        "ruleset_bypass_empty": ruleset_bypass_empty,
        "environments_read_back": (
            reads["environments"][0] == 200
            and isinstance(values["environments"], dict)
            and isinstance(values["environments"].get("environments"), list)
            and values["environments"].get("total_count") == len(values["environments"]["environments"])
            and not has_next_page(reads["environments"][1])
            and all(reads[key][0] == 200 for key in environment_keys.values())
        ),
        "required_environments_present": set(environments).issubset(environment_names),
        "required_environments_have_reviewers": protected_environments,
    }
    complete = all(assertions.values())
    result = {
        "schema": "trillionnium.repository-governance-readback.v1",
        "repository": REPO, "repository_id": REPO_ID, "branch": BRANCH,
        "expected_main": expected_main, "required_check": REQUIRED_CHECK,
        "authenticated_actor": {"id": actor.get("id"), "login": login, "type": actor.get("type"),
                                "repository_permission": actor_permission.get("permission")},
        "read_only": True, "mutation_methods_used": [],
        "required_environments": environments,
        "observed_environment_names": sorted(environment_names),
        "ruleset_ids": sorted(ruleset_keys),
        "http_status": {key: status for key, (status, _, _) in reads.items()},
        "assertions": assertions, "all_required_assertions": complete,
        "claims": {"governance_readback_complete": complete, "negative_rehearsal_accepted": False,
                   "independent_acceptance": False, "accepted_evidence": False,
                   "gap_closed": False, "production_ready": False},
        "limitations": [
            "Read-only observation; it does not perform or accept the negative no-bypass rehearsal.",
            "The policy administrator cannot independently accept their own evidence.",
        ],
    }
    write(output / "result.json", json.dumps(result, sort_keys=True, indent=2).encode() + b"\n")
    members = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    write(output / "SHA256SUMS", "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in members
    ).encode())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-environment", action="append", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        api = GitHubApi(os.environ.get(TOKEN_ENV, ""), timeout=args.timeout_seconds)
        result = capture(api, args.output, args.expected_main, args.required_environment)
    except ReadbackError as error:
        print(f"governance readback failed closed: {error}", file=sys.stderr)
        return 2
    if not result["all_required_assertions"]:
        print("governance readback retained but required assertions are false", file=sys.stderr)
        return 3
    print(f"governance readback complete for {REPO}@{args.expected_main}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())