#!/usr/bin/env python3
"""Compose GitHub governance desired state, read-back and admin application tooling."""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def capture_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Capture GitHub governance endpoints including explicit HTTP failures."""
        from __future__ import annotations

        import argparse
        import datetime as dt
        import hashlib
        import json
        import os
        import urllib.error
        import urllib.request
        from pathlib import Path
        from typing import Any

        API = "https://api.github.com"
        ENDPOINTS = {
            "repository": "/repos/{repo}",
            "main_branch": "/repos/{repo}/branches/main",
            "main_protection": "/repos/{repo}/branches/main/protection",
            "rulesets": "/repos/{repo}/rulesets?includes_parents=true&per_page=100",
            "actions_permissions": "/repos/{repo}/actions/permissions",
            "workflow_permissions": "/repos/{repo}/actions/permissions/workflow",
            "environments": "/repos/{repo}/environments?per_page=100",
            "integration_pr": "/repos/{repo}/pulls/63",
        }

        def request(url: str, token: str) -> dict[str, Any]:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "trillionnium-governance-readback",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    body = response.read()
                    status = response.status
                    headers = dict(response.headers.items())
            except urllib.error.HTTPError as error:
                body = error.read()
                status = error.code
                headers = dict(error.headers.items())
            parsed: Any
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"raw_utf8": body.decode("utf-8", errors="replace")}
            return {
                "http_status": status,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body": parsed,
                "selected_headers": {
                    key: value
                    for key, value in headers.items()
                    if key.lower() in {"etag", "x-github-request-id", "x-oauth-scopes", "x-accepted-oauth-scopes"}
                },
            }

        def capture(repository: str, token: str) -> dict[str, Any]:
            return {
                "schema": "trillionnium.github-governance-readback.v1",
                "repository": repository,
                "captured_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "actor": os.environ.get("GITHUB_ACTOR"),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "endpoints": {
                    name: request(API + template.format(repo=repository), token)
                    for name, template in ENDPOINTS.items()
                },
            }

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--repository", required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()
            token = os.environ.get("GH_TOKEN")
            if not token:
                parser.error("GH_TOKEN is required")
            value = capture(args.repository, token)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({name: row["http_status"] for name, row in value["endpoints"].items()}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def verify_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Validate GitHub governance read-back against the fail-closed contract."""
        from __future__ import annotations

        import argparse
        import json
        from pathlib import Path
        from typing import Any

        class GovernanceError(RuntimeError):
            pass

        def required_contexts(protection: dict[str, Any]) -> set[str]:
            status = protection.get("required_status_checks") or {}
            contexts = set(status.get("contexts") or [])
            for row in status.get("checks") or []:
                if isinstance(row, dict) and isinstance(row.get("context"), str):
                    contexts.add(row["context"])
            return contexts

        def evaluate(value: dict[str, Any]) -> dict[str, Any]:
            endpoints = value.get("endpoints", {})
            requirements: list[dict[str, Any]] = []
            def add(requirement: str, passed: bool, detail: str) -> None:
                requirements.append({"requirement": requirement, "passed": bool(passed), "detail": detail})

            protection_row = endpoints.get("main_protection", {})
            protection = protection_row.get("body") if protection_row.get("http_status") == 200 else {}
            protection = protection if isinstance(protection, dict) else {}
            status = protection.get("required_status_checks") or {}
            reviews = protection.get("required_pull_request_reviews") or {}
            add("main protection readable", protection_row.get("http_status") == 200, f"HTTP {protection_row.get('http_status')}")
            add("strict latest-base checks", status.get("strict") is True, repr(status.get("strict")))
            add("aggregate merge gate required", "trillionnium-game-merge-gate" in required_contexts(protection), repr(sorted(required_contexts(protection))))
            add("stale approvals dismissed", reviews.get("dismiss_stale_reviews") is True, repr(reviews.get("dismiss_stale_reviews")))
            add("CODEOWNER review required", reviews.get("require_code_owner_reviews") is True, repr(reviews.get("require_code_owner_reviews")))
            add("at least one approval", int(reviews.get("required_approving_review_count") or 0) >= 1, repr(reviews.get("required_approving_review_count")))
            add("latest push approval required", reviews.get("require_last_push_approval") is True, repr(reviews.get("require_last_push_approval")))
            add("empty PR bypass allowances", not (reviews.get("bypass_pull_request_allowances") or {}).get("users") and not (reviews.get("bypass_pull_request_allowances") or {}).get("teams") and not (reviews.get("bypass_pull_request_allowances") or {}).get("apps"), repr(reviews.get("bypass_pull_request_allowances")))
            add("administrators enforced", (protection.get("enforce_admins") or {}).get("enabled") is True, repr(protection.get("enforce_admins")))
            add("conversation resolution required", (protection.get("required_conversation_resolution") or {}).get("enabled") is True, repr(protection.get("required_conversation_resolution")))
            add("force pushes forbidden", (protection.get("allow_force_pushes") or {}).get("enabled") is False, repr(protection.get("allow_force_pushes")))
            add("branch deletion forbidden", (protection.get("allow_deletions") or {}).get("enabled") is False, repr(protection.get("allow_deletions")))
            add("linear history required", (protection.get("required_linear_history") or {}).get("enabled") is True, repr(protection.get("required_linear_history")))

            rulesets_row = endpoints.get("rulesets", {})
            rulesets = rulesets_row.get("body") if rulesets_row.get("http_status") == 200 else []
            add("rulesets readable", rulesets_row.get("http_status") == 200, f"HTTP {rulesets_row.get('http_status')}")
            bypass = []
            if isinstance(rulesets, list):
                for ruleset in rulesets:
                    for actor in ruleset.get("bypass_actors") or []:
                        bypass.append({"ruleset": ruleset.get("name"), "actor": actor})
            add("no ruleset bypass actors", not bypass, repr(bypass))

            actions_row = endpoints.get("actions_permissions", {})
            add("Actions permissions readable", actions_row.get("http_status") == 200, f"HTTP {actions_row.get('http_status')}")
            workflow_row = endpoints.get("workflow_permissions", {})
            workflow = workflow_row.get("body") if workflow_row.get("http_status") == 200 else {}
            add("workflow permission read-back", workflow_row.get("http_status") == 200, f"HTTP {workflow_row.get('http_status')}")
            add("default workflow token is read-only", workflow.get("default_workflow_permissions") == "read", repr(workflow.get("default_workflow_permissions")))
            add("PR approval by workflows forbidden", workflow.get("can_approve_pull_request_reviews") is False, repr(workflow.get("can_approve_pull_request_reviews")))

            environments_row = endpoints.get("environments", {})
            environments = environments_row.get("body") if environments_row.get("http_status") == 200 else {}
            names = {row.get("name") for row in environments.get("environments", [])} if isinstance(environments, dict) else set()
            add("environments readable", environments_row.get("http_status") == 200, f"HTTP {environments_row.get('http_status')}")
            add("production environment exists", "production" in names, repr(sorted(name for name in names if name)))

            pr_row = endpoints.get("integration_pr", {})
            pr = pr_row.get("body") if pr_row.get("http_status") == 200 else {}
            add("singular integration PR readable", pr_row.get("http_status") == 200, f"HTTP {pr_row.get('http_status')}")
            add("integration PR remains unmerged", pr.get("merged") is False, repr(pr.get("merged")))
            all_pass = all(row["passed"] for row in requirements)
            return {
                "schema": "trillionnium.github-governance-evaluation.v1",
                "repository": value.get("repository"),
                "captured_at": value.get("captured_at"),
                "requirements": requirements,
                "passed": sum(row["passed"] for row in requirements),
                "failed": sum(not row["passed"] for row in requirements),
                "all_required_pass": all_pass,
                "claim_boundary": {
                    "negative_merge_rehearsal_accepted": False,
                    "independent_governance_acceptance": False,
                    "all_gaps_closed": False,
                    "production_ready": False,
                },
            }

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--readback", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            parser.add_argument("--require-pass", action="store_true")
            args = parser.parse_args()
            value = evaluate(json.loads(args.readback.read_text(encoding="utf-8")))
            args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"passed": value["passed"], "failed": value["failed"], "all_required_pass": value["all_required_pass"]}, sort_keys=True))
            if args.require_pass and not value["all_required_pass"]:
                return 1
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def apply_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Attempt the authorized main branch-protection hardening and retain HTTP result."""
        from __future__ import annotations

        import argparse
        import datetime as dt
        import hashlib
        import json
        import os
        import urllib.error
        import urllib.request
        from pathlib import Path
        from typing import Any

        API = "https://api.github.com"

        def apply(repository: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
            body = json.dumps(payload, sort_keys=True).encode()
            request = urllib.request.Request(
                f"{API}/repos/{repository}/branches/main/protection",
                data=body,
                method="PUT",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "trillionnium-governance-admin-apply",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    response_body = response.read()
                    status = response.status
            except urllib.error.HTTPError as error:
                response_body = error.read()
                status = error.code
            try:
                parsed = json.loads(response_body)
            except json.JSONDecodeError:
                parsed = {"raw_utf8": response_body.decode("utf-8", errors="replace")}
            return {
                "schema": "trillionnium.github-governance-apply-result.v1",
                "repository": repository,
                "attempted_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "actor": os.environ.get("GITHUB_ACTOR"),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "payload_sha256": hashlib.sha256(body).hexdigest(),
                "http_status": status,
                "succeeded": status in {200, 201},
                "response_sha256": hashlib.sha256(response_body).hexdigest(),
                "response": parsed,
            }

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--repository", required=True)
            parser.add_argument("--payload", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()
            token = os.environ.get("GH_TOKEN")
            if not token:
                parser.error("GH_TOKEN is required")
            payload = json.loads(args.payload.read_text(encoding="utf-8"))
            result = apply(args.repository, payload, token)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"http_status": result["http_status"], "succeeded": result["succeeded"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Source contract for GitHub governance administration and read-back."""
        from __future__ import annotations
        import json,sys
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[1]
        PAYLOAD=ROOT/"docs/governance/MAIN_BRANCH_PROTECTION_API_PAYLOAD.json"
        REQUEST=ROOT/"docs/governance/GITHUB_ADMIN_READBACK_REQUEST.json"
        CAPTURE=ROOT/"scripts/capture-github-governance-readback.py"
        VERIFY=ROOT/"scripts/verify-github-governance-readback.py"
        APPLY=ROOT/"scripts/apply-github-governance-contract.py"
        REQUIRED_VERIFY=("strict latest-base checks","aggregate merge gate required","stale approvals dismissed","CODEOWNER review required","latest push approval required","administrators enforced","force pushes forbidden","branch deletion forbidden","no ruleset bypass actors","default workflow token is read-only","PR approval by workflows forbidden","production environment exists")
        class ValidationError(RuntimeError): pass
        def require(value:bool,message:str)->None:
          if not value: raise ValidationError(message)
        def validate(payload:dict,request:dict,capture:str,verify:str,apply:str)->None:
          status=payload.get("required_status_checks",{}); reviews=payload.get("required_pull_request_reviews",{})
          require(status.get("strict") is True,"strict status checks")
          require(status.get("contexts")==["trillionnium-game-merge-gate"],"required context")
          require(payload.get("enforce_admins") is True,"admin enforcement")
          require(reviews.get("dismiss_stale_reviews") is True,"dismiss stale")
          require(reviews.get("require_code_owner_reviews") is True,"CODEOWNER")
          require(reviews.get("require_last_push_approval") is True,"last push approval")
          require(reviews.get("required_approving_review_count",0)>=1,"approval count")
          require(reviews.get("bypass_pull_request_allowances")=={"users":[],"teams":[],"apps":[]},"review bypass")
          require(payload.get("allow_force_pushes") is False and payload.get("allow_deletions") is False,"force/delete")
          require(payload.get("required_conversation_resolution") is True,"conversation resolution")
          require(request.get("schema")=="trillionnium.github-admin-readback-request.v1","request schema")
          require(request.get("administrator_scope_required") is True,"admin scope boundary")
          require(request.get("self_approval_allowed") is False,"self approval boundary")
          for marker in REQUIRED_VERIFY: require(marker in verify,f"verifier missing {marker}")
          require("HTTPError" in capture and "HTTPError" in apply,"HTTP failures not retained")
          require("PUT" in apply,"admin apply method")
          require(not any(request.get("claim_boundary",{}).values()),"positive governance claim")
        def main()->int:
          try: validate(json.loads(PAYLOAD.read_text()),json.loads(REQUEST.read_text()),CAPTURE.read_text(),VERIFY.read_text(),APPLY.read_text())
          except (OSError,json.JSONDecodeError,ValidationError) as error:
            print(f"GitHub governance source contract failed: {error}",file=sys.stderr); return 1
          print("GitHub governance source contract: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,json,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]
        VERIFY=ROOT/"scripts/verify-github-governance-readback.py"
        CHECKER=ROOT/"scripts/check-github-governance-contract.py"
        def load(path,name):
          spec=importlib.util.spec_from_file_location(name,path)
          if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
          module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
        class GithubGovernanceContractTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.verify=load(VERIFY,"governance_verify_tests"); cls.checker=load(CHECKER,"governance_checker_tests")
          def test_unreadable_admin_endpoint_fails_closed(self):
            report=self.verify.evaluate({"repository":"x","endpoints":{}})
            self.assertFalse(report["all_required_pass"]); self.assertGreater(report["failed"],0)
          def test_complete_protection_passes_protection_requirements(self):
            protection={"required_status_checks":{"strict":True,"contexts":["trillionnium-game-merge-gate"]},"required_pull_request_reviews":{"dismiss_stale_reviews":True,"require_code_owner_reviews":True,"required_approving_review_count":1,"require_last_push_approval":True,"bypass_pull_request_allowances":{"users":[],"teams":[],"apps":[]}},"enforce_admins":{"enabled":True},"required_conversation_resolution":{"enabled":True},"allow_force_pushes":{"enabled":False},"allow_deletions":{"enabled":False},"required_linear_history":{"enabled":True}}
            endpoints={"main_protection":{"http_status":200,"body":protection},"rulesets":{"http_status":200,"body":[]},"actions_permissions":{"http_status":200,"body":{}},"workflow_permissions":{"http_status":200,"body":{"default_workflow_permissions":"read","can_approve_pull_request_reviews":False}},"environments":{"http_status":200,"body":{"environments":[{"name":"production"}]}},"integration_pr":{"http_status":200,"body":{"merged":False}}}
            report=self.verify.evaluate({"repository":"x","endpoints":endpoints})
            self.assertEqual(report["failed"],0)
          def test_source_contract_passes(self):
            self.checker.validate(json.loads(self.checker.PAYLOAD.read_text()),json.loads(self.checker.REQUEST.read_text()),self.checker.CAPTURE.read_text(),self.checker.VERIFY.read_text(),self.checker.APPLY.read_text())
        if __name__=="__main__": unittest.main()
        '''
    )


def update_docs(root: Path) -> None:
    payload = {
        "required_status_checks": {"strict": True, "contexts": ["trillionnium-game-merge-gate"]},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismissal_restrictions": {"users": [], "teams": []},
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 1,
            "require_last_push_approval": True,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": True,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }
    request = {
        "schema": "trillionnium.github-admin-readback-request.v1",
        "repository": "TrillionniumFoundation/TrillionniumGame",
        "target_branch": "main",
        "administrator_scope_required": True,
        "desired_branch_protection_payload": "docs/governance/MAIN_BRANCH_PROTECTION_API_PAYLOAD.json",
        "capture_command": "GH_TOKEN=<admin-token> python3 scripts/capture-github-governance-readback.py --repository TrillionniumFoundation/TrillionniumGame --output governance-readback.json",
        "apply_command": "GH_TOKEN=<admin-token> python3 scripts/apply-github-governance-contract.py --repository TrillionniumFoundation/TrillionniumGame --payload docs/governance/MAIN_BRANCH_PROTECTION_API_PAYLOAD.json --output governance-apply.json",
        "verify_command": "python3 scripts/verify-github-governance-readback.py --readback governance-readback.json --output governance-evaluation.json --require-pass",
        "additional_required_facts": [
            "repository and organization rulesets have no bypass actors",
            "default workflow token permissions are read-only",
            "workflows cannot approve pull-request reviews",
            "production environment exists with independently accepted reviewers and branch policy",
            "harmless no-bypass rehearsal is retained without creating a competing main-target integration line",
        ],
        "self_approval_allowed": False,
        "claim_boundary": {"applied": False, "readback_complete": False, "negative_rehearsal_accepted": False, "independently_accepted": False, "all_gaps_closed": False},
    }
    write(root / "docs/governance/MAIN_BRANCH_PROTECTION_API_PAYLOAD.json", json.dumps(payload, indent=2))
    write(root / "docs/governance/GITHUB_ADMIN_READBACK_REQUEST.json", json.dumps(request, indent=2))
    governance = root / "docs/GOVERNANCE.md"
    text = read(governance)
    section = textwrap.dedent(
        '''\

        ## GitHub administration read-back

        The exact REST payload for `main` is `docs/governance/MAIN_BRANCH_PROTECTION_API_PAYLOAD.json`. The capture tool retains successful and failed endpoint bodies with HTTP status and SHA-256; the verifier rejects unreadable protection, ruleset, Actions or environment state and requires strict latest-base checks, the aggregate gate, CODEOWNER/latest-push review, conversation resolution, administrator enforcement and zero bypass actors.

        The controller attempts the authorized hardening with its installed token and immediately recaptures state. A 403 or incomplete read-back remains an external administration blocker. Repository code cannot turn an unavailable scope, missing production-environment reviewer or absent no-bypass rehearsal into a passing fact.
        '''
    )
    if "## GitHub administration read-back" not in text:
        text += section
    write(governance, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    write(root / "scripts/capture-github-governance-readback.py", capture_source())
    write(root / "scripts/verify-github-governance-readback.py", verify_source())
    write(root / "scripts/apply-github-governance-contract.py", apply_source())
    write(root / "scripts/check-github-governance-contract.py", checker_source())
    write(root / "tests/control_plane/test_github_governance_contract.py", test_source())
    update_docs(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
