"""Tests for the read-only GitHub governance packet generator."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("governance_readback", ROOT / "scripts/capture-repository-governance-readback.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load governance readback")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeApi:
    def __init__(self, values, statuses=None, headers=None):
        self.values, self.statuses, self.headers, self.paths = values, statuses or {}, headers or {}, []

    def get(self, path):
        self.paths.append(path)
        key = {
            "/user": "actor",
            f"/repos/{MODULE.REPO}": "repository",
            f"/repos/{MODULE.REPO}/branches/main": "branch",
            f"/repos/{MODULE.REPO}/commits/{'a' * 40}/check-runs?per_page=100": "main_checks",
            f"/repos/{MODULE.REPO}/branches/main/protection": "protection",
            f"/repos/{MODULE.REPO}/rulesets?includes_parents=true&per_page=100": "rulesets",
            f"/repos/{MODULE.REPO}/rulesets/17": "ruleset_17",
            f"/repos/{MODULE.REPO}/actions/permissions": "actions",
            f"/repos/{MODULE.REPO}/actions/permissions/workflow": "workflow_permissions",
            f"/repos/{MODULE.REPO}/environments?per_page=100": "environments",
            f"/repos/{MODULE.REPO}/environments/governance-audit": "environment",
            f"/repos/{MODULE.REPO}/collaborators/independent-admin/permission": "actor_permission",
        }[path]
        headers = {"x-github-request-id": key, **self.headers.get(key, {})}
        return self.statuses.get(key, 200), headers, self.values.get(key, {})


def values():
    return {
        "actor": {"id": 42, "login": "independent-admin", "type": "User"},
        "actor_permission": {"permission": "admin"},
        "repository": {"id": MODULE.REPO_ID, "full_name": MODULE.REPO, "default_branch": "main", "archived": False},
        "branch": {"name": "main", "protected": True, "commit": {"sha": "a" * 40}},
        "main_checks": {"total_count": 1, "check_runs": [{"id": 1, "name": MODULE.REQUIRED_CHECK, "app": {"id": MODULE.REQUIRED_CHECK_APP_ID}, "status": "completed", "conclusion": "success"}]},
        "protection": {
            "required_status_checks": {"strict": True, "contexts": [MODULE.REQUIRED_CHECK], "checks": [{"context": MODULE.REQUIRED_CHECK, "app_id": MODULE.REQUIRED_CHECK_APP_ID}]},
            "enforce_admins": {"enabled": True},
            "required_pull_request_reviews": {"dismiss_stale_reviews": True, "require_code_owner_reviews": True,
                "require_last_push_approval": True, "required_approving_review_count": 1,
                "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}},
            "required_conversation_resolution": {"enabled": True},
            "required_linear_history": {"enabled": True},
            "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
        },
        "rulesets": [{"id": 17}], "ruleset_17": {"id": 17, "bypass_actors": []},
        "actions": {"enabled": True},
        "workflow_permissions": {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
        "environments": {"total_count": 1, "environments": [{"name": "governance-audit"}]},
        "environment": {"name": "governance-audit", "prevent_self_review": True, "protection_rules": [
            {"type": "required_reviewers", "reviewers": [
                {"type": "User", "reviewer": {"id": 99, "login": "independent-reviewer"}}
            ]}]},
    }


class Response:
    def __init__(self, body=b"{}"):
        self.body, self.headers, self.closed = io.BytesIO(body), Message(), False
    def read(self, size=-1): return self.body.read(size)
    def getcode(self): return 200
    def close(self): self.closed = True


class Opener:
    def __init__(self, response): self.response, self.request = response, None
    def open(self, request, timeout): self.request = request; return self.response


class Tests(unittest.TestCase):
    def test_complete_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakeApi(values())
            output = Path(directory) / "packet"
            result = MODULE.capture(api, output, "a" * 40, ["governance-audit"])
            self.assertTrue(result["all_required_assertions"])
            self.assertEqual(result["mutation_methods_used"], [])
            self.assertFalse(result["claims"]["negative_rehearsal_accepted"])
            self.assertEqual(result["authenticated_actor"]["repository_permission"], "admin")
            for line in (output / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            retained = b"".join(path.read_bytes() for path in output.iterdir())
            self.assertNotIn(b"Bearer", retained)

    def test_403_and_weak_policy_fail_closed(self):
        data = values()
        data["protection"]["enforce_admins"]["enabled"] = False
        data["workflow_permissions"]["default_workflow_permissions"] = "write"
        data["ruleset_17"]["bypass_actors"] = [{"actor_id": 1}]
        data["environment"]["protection_rules"] = []
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.capture(FakeApi(data, {"protection": 403}), Path(directory) / "packet",
                                    "a" * 40, ["governance-audit"])
            self.assertFalse(result["all_required_assertions"])
            self.assertEqual(result["http_status"]["protection"], 403)
            self.assertFalse(result["assertions"]["admins_enforced"])
            self.assertFalse(result["assertions"]["ruleset_bypass_empty"])
            self.assertFalse(result["assertions"]["required_environments_have_reviewers"])
        with tempfile.TemporaryDirectory() as directory:
            paged = MODULE.capture(
                FakeApi(values(), headers={"rulesets": {"link": '<next>; rel="next"'}}),
                Path(directory) / "packet", "a" * 40, ["governance-audit"],
            )
            self.assertFalse(paged["assertions"]["rulesets_read_back"])

    def test_actor_main_and_check_collection_are_exact(self):
        data = values()
        data["actor"]["type"] = "Bot"
        data["actor_permission"]["permission"] = "write"
        data["branch"]["commit"]["sha"] = "b" * 40
        data["main_checks"]["total_count"] = 2
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.capture(FakeApi(data), Path(directory) / "packet", "a" * 40, ["governance-audit"])
            for key in ("authenticated_human_admin", "main_identity", "main_check_collection_complete"):
                self.assertFalse(result["assertions"][key])

    def test_client_is_get_only_origin_bound_bounded_and_closes(self):
        response, opener = Response(), None
        opener = Opener(response)
        api = MODULE.GitHubApi("x" * 32, opener=opener)
        status, headers, body = api.get(f"/repos/{MODULE.REPO}")
        self.assertEqual((status, headers, body), (200, {}, {}))
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.request.full_url, MODULE.ORIGIN + f"/repos/{MODULE.REPO}")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer " + "x" * 32)
        self.assertTrue(response.closed)
        with self.assertRaises(MODULE.ReadbackError): MODULE.GitHubApi("x" * 32, opener=Opener(Response(b"x" * (MODULE.MAX_BYTES + 1)))).get(f"/repos/{MODULE.REPO}")
        with self.assertRaises(MODULE.ReadbackError): api.get("/orgs/TrillionniumFoundation")
        class FailedOpener:
            def open(self, request, timeout):
                raise __import__("urllib.error", fromlist=["URLError"]).URLError("offline")
        with self.assertRaises(MODULE.ReadbackError):
            MODULE.GitHubApi("x" * 32, opener=FailedOpener()).get(f"/repos/{MODULE.REPO}")

    def test_redirects_and_unsafe_inputs_are_rejected(self):
        handler = MODULE.NoRedirect()
        request = __import__("urllib.request", fromlist=["Request"]).Request(MODULE.ORIGIN + "/user")
        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(MODULE.ReadbackError):
                handler.redirect_request(request, None, code, "redirect", {}, "https://example.invalid")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.capture(FakeApi(values()), Path(directory) / "zero", "a" * 40, [])
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.capture(FakeApi(values()), Path(directory) / "one", "bad", ["governance-audit"])
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.capture(FakeApi(values()), Path(directory) / "two", "a" * 40, ["../bad"])

    def test_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"; output.mkdir(); (output / "existing").write_text("x")
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.capture(FakeApi(values()), output, "a" * 40, ["governance-audit"])


    def test_latest_trusted_merge_gate_attempt_controls_result(self):
        data = values()
        data["main_checks"] = {
            "total_count": 3,
            "check_runs": [
                {"id": 1, "name": MODULE.REQUIRED_CHECK,
                 "app": {"id": MODULE.REQUIRED_CHECK_APP_ID},
                 "status": "completed", "conclusion": "success"},
                {"id": 2, "name": MODULE.REQUIRED_CHECK,
                 "app": {"id": MODULE.REQUIRED_CHECK_APP_ID},
                 "status": "completed", "conclusion": "failure"},
                {"id": 3, "name": MODULE.REQUIRED_CHECK,
                 "app": {"id": 999},
                 "status": "completed", "conclusion": "success"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.capture(
                FakeApi(data), Path(directory) / "packet",
                "a" * 40, ["governance-audit"],
            )
            self.assertFalse(result["assertions"]["successful_exact_main_merge_gate"])

    def test_packet_writer_rejects_directory_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            output.rename(Path(directory) / "moved")
            output.mkdir()
            with self.assertRaises(MODULE.ReadbackError):
                writer.iterdir()
            writer.close()

    def test_packet_writer_rejects_member_mutation_and_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            (output / "one.json").write_bytes(b"evil\n")
            with self.assertRaises(MODULE.ReadbackError):
                writer.read_verified("one.json")
            writer.close()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            (output / "injected").write_text("x")
            with self.assertRaises(MODULE.ReadbackError):
                writer.iterdir()
            writer.close()

    def test_packet_writer_rejects_symlinked_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.prepare(alias / "packet")


    def test_packet_writer_rechecks_members_when_sealing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            manifest = "".join(
                f"{record['sha256']}  {name}\n"
                for name, record in sorted(writer.records.items())
            ).encode()
            (output / "one.json").write_bytes(b"evil\n")
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.write(writer / "SHA256SUMS", manifest)
            writer.close()


    def test_team_only_or_unstable_user_environment_reviewers_fail_closed(self):
        for reviewers in (
            [{"type": "Team", "reviewer": {"id": 77, "slug": "release-sre"}}],
            [{"type": "User", "reviewer": {"login": "missing-id"}}],
            [{"type": "User", "reviewer": {"id": 0, "login": "zero-id"}}],
            [{"type": "User", "reviewer": {"id": 99, "login": "   "}}],
        ):
            with self.subTest(reviewers=reviewers), tempfile.TemporaryDirectory() as directory:
                data = values()
                data["environment"]["protection_rules"][0]["reviewers"] = reviewers
                result = MODULE.capture(
                    FakeApi(data), Path(directory) / "packet",
                    "a" * 40, ["governance-audit"],
                )
                self.assertFalse(result["assertions"]["required_environments_have_reviewers"])


if __name__ == "__main__": unittest.main()