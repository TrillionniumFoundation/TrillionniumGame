#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

FILES: dict[str, str] = {
    "config/denominator-gates.json": r'''
{
  "schema": "trillionnium.denominator-gates.v1",
  "project_id": "trillionnium-game",
  "gates": [
    {
      "gate_id": "GATE-SCOPE",
      "stage_gate": "SG1",
      "title": "Complete, independently reviewed denominator lock",
      "claim_boundary": "Leaf proposals, candidate generation and remote execution do not close SG1 without accepted independent review."
    }
  ]
}
''',
    "config/denominator-review-routing.json": r'''
{
  "schema": "trillionnium.denominator-review-routing.v1",
  "project_id": "trillionnium-game",
  "default_task_id": "TG-W0-002",
  "default_gate_id": "GATE-SCOPE",
  "routes": {
    "DEN-SOURCE": {"candidate_filename":"source-denominator.candidate.json","owner_role":"governance","reviewer_roles":["governance","security"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/43"},
    "DEN-API": {"candidate_filename":"api-denominator.candidate.json","owner_role":"protocol","reviewer_roles":["protocol","security"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/43"},
    "DEN-RTAPI": {"candidate_filename":"rtapi-denominator.candidate.json","owner_role":"realtime","reviewer_roles":["realtime","protocol"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/43"},
    "DEN-CONSOLE": {"candidate_filename":"console-denominator.candidate.json","owner_role":"console","reviewer_roles":["console","legal"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/21"},
    "DEN-RUNTIME": {"candidate_filename":"runtime-denominator.candidate.json","owner_role":"runtime","reviewer_roles":["runtime","security"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/15"},
    "DEN-CONFIG": {"candidate_filename":"config-denominator.candidate.json","owner_role":"governance","reviewer_roles":["governance","sre"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/15"},
    "DEN-CLI": {"candidate_filename":"cli-denominator.candidate.json","owner_role":"governance","reviewer_roles":["governance","sre"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/15"},
    "DEN-DB": {"candidate_filename":"database-denominator.candidate.json","owner_role":"database","reviewer_roles":["database","security"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/15"},
    "DEN-DATA": {"candidate_filename":"data-denominator.candidate.json","owner_role":"database","reviewer_roles":["database","governance"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/15"},
    "DEN-METRICS": {"candidate_filename":"metrics-denominator.candidate.json","owner_role":"sre","reviewer_roles":["sre","security"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/21"},
    "DEN-OPS": {"candidate_filename":"operations-denominator.candidate.json","owner_role":"sre","reviewer_roles":["sre","governance"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/21"},
    "DEN-PROVIDERS": {"candidate_filename":"providers-denominator.candidate.json","owner_role":"security","reviewer_roles":["security","identity"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/21"},
    "DEN-IAP": {"candidate_filename":"iap-denominator.candidate.json","owner_role":"iap","reviewer_roles":["iap","security"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/21"},
    "DEN-SDK": {"candidate_filename":"sdk-denominator.candidate.json","owner_role":"sdk","reviewer_roles":["sdk","protocol"],"issue_url":"https://github.com/TrillionniumFoundation/TrillionniumGame/issues/21"}
  }
}
''',
    "tools/denominator/source_manifest.py": r'''
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}")


class SourceManifestError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SourceManifestError(f"{path}: root must be an object")
    return value


def _leaf(item_class: str, symbol: str, contract: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    seed = canonical_bytes({"class": item_class, "symbol": symbol, "contract": contract})
    leaf_id = "TG-D0-SOURCE-" + hashlib.sha256(seed).hexdigest()[:18].upper()
    return {
        "id": leaf_id,
        "layer": "D0-upstream-source-build",
        "class": item_class,
        "symbol": symbol,
        "source": source,
        "signature_hash": sha256_bytes(seed),
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": "governance",
        "workstream": "W0",
        "task_ids": ["TG-W0-002"],
        "test_ids": [f"TG-DIFF-{leaf_id}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def generate(root: Path = ROOT) -> dict[str, Any]:
    baseline_path = root / "docs/development/UPSTREAM_BASELINE.json"
    sdk_path = root / "config/sdk-source-snapshots.json"
    images_path = root / "config/database-test-images.json"
    baseline = _load(baseline_path)
    sdk = _load(sdk_path)
    images = _load(images_path)
    leaves: list[dict[str, Any]] = []

    for key in ("nakama", "nakama_common"):
        row = baseline[key]
        identity = {
            "repository": row["repository"],
            "tag": row.get("tag"),
            "commit": row["commit"],
            "tree": row["tree"],
        }
        leaves.append(_leaf("upstream_source_root", row["repository"], identity, identity))
        for group in ("source_roots", "protocol_contracts", "implementation_contracts"):
            for item in row.get(group, []):
                contract = {"repository": row["repository"], "group": group, **item}
                leaves.append(
                    _leaf(
                        "upstream_source_object",
                        f"{row['repository']}:{item['path']}",
                        contract,
                        identity,
                    )
                )

    profiles = sdk.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise SourceManifestError("SDK source registry is empty")
    for profile in profiles:
        contract = {
            key: profile.get(key)
            for key in ("id", "repository", "branch", "commit", "tree", "archive_tree", "language", "platform")
            if profile.get(key) is not None
        }
        contract["gitlinks"] = profile.get("gitlinks", [])
        contract["archive_repairs"] = profile.get("archive_repairs", [])
        leaves.append(
            _leaf(
                "sdk_source_root",
                f"{profile['id']}:{profile['repository']}",
                contract,
                {"path": "config/sdk-source-snapshots.json"},
            )
        )

    profiles_value = images.get("profiles")
    if not isinstance(profiles_value, dict) or not profiles_value:
        raise SourceManifestError("database image registry is empty")
    for profile, row in sorted(profiles_value.items()):
        leaves.append(
            _leaf(
                "database_test_image",
                f"{profile}:{row['image']}",
                {"profile": profile, **row},
                {"path": "config/database-test-images.json"},
            )
        )

    lock_paths = [
        "rust-toolchain.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "runtime/go.mod",
        "runtime/go.sum",
        "scripts/blackbox/package-lock.json",
        "config/database-test-images.json",
        "config/sdk-source-snapshots.json",
    ]
    for relative in lock_paths:
        path = root / relative
        if not path.is_file():
            continue
        payload = path.read_bytes()
        leaves.append(
            _leaf(
                "toolchain_or_dependency_lock",
                relative,
                {"path": relative, "size_bytes": len(payload), "sha256": sha256_bytes(payload)},
                {"path": relative},
            )
        )

    digest_paths = [
        "compose.yaml",
        "runtime/Dockerfile",
        "runtime/Dockerfile.faultlab",
        "config/database-test-images.json",
    ]
    found: set[str] = set()
    for relative in digest_paths:
        path = root / relative
        if not path.is_file():
            continue
        for reference in DIGEST.findall(path.read_text(encoding="utf-8")):
            if reference in found:
                continue
            found.add(reference)
            leaves.append(
                _leaf(
                    "oci_digest_reference",
                    reference,
                    {"reference": reference, "declared_in": relative},
                    {"path": relative},
                )
            )

    leaves.sort(key=lambda item: item["id"])
    ids = [item["id"] for item in leaves]
    if not leaves or len(ids) != len(set(ids)):
        raise SourceManifestError("source denominator is empty or contains duplicate IDs")
    registry_digest = sha256_bytes(
        canonical_bytes(
            {
                "baseline": baseline,
                "sdk": sdk,
                "images": images,
            }
        )
    )
    return {
        "schema": "trillionnium.source-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "denominator": "DEN-SOURCE",
        "layer": "D0-upstream-source-build",
        "status": "candidate-unclassified",
        "source_registry_sha256": registry_digest,
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "unreviewed_count": len(leaves),
        "manual_contract_count": 0,
        "manual_contracts": [],
        "leaves": leaves,
        "claims": {
            "complete_source_inventory": False,
            "independently_reviewed": False,
            "sg1_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
        "sg1_eligible": False,
        "compatibility_credit": False,
    }
''',
    "scripts/generate-source-denominator.py": r'''
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.denominator.source_manifest import generate


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the fail-closed DEN-SOURCE candidate")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-sg1", action="store_true")
    args = parser.parse_args()
    value = generate(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.require_sg1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "tools/denominator/review_request.py": r'''
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


class ReviewRequestError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewRequestError(f"{path}: {error}") from error


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewRequestError(f"{label} must be a non-empty string")
    return value


def _choice(leaf: dict[str, Any], plural: str, singular: str, fallback: str) -> str:
    values = leaf.get(plural)
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value:
                return value
    value = leaf.get(singular)
    if isinstance(value, str) and value:
        return value
    return fallback


def build_review_package(
    *,
    candidate_paths: Iterable[Path],
    head_sha: str,
    remote_index_path: Path,
    output_dir: Path,
    policy_path: Path,
    routing_path: Path,
) -> dict[str, Any]:
    if len(head_sha) != 40 or any(character not in "0123456789abcdef" for character in head_sha):
        raise ReviewRequestError("head_sha must be an exact lowercase Git SHA")
    policy = _load(policy_path)
    routing = _load(routing_path)
    remote_index = _load(remote_index_path)
    required = policy.get("required_denominators")
    routes = routing.get("routes")
    remotes = remote_index.get("denominators")
    if not isinstance(required, list) or not required:
        raise ReviewRequestError("review policy required_denominators is empty")
    if not isinstance(routes, dict) or not isinstance(remotes, dict):
        raise ReviewRequestError("routing or remote evidence index is invalid")
    if remote_index.get("candidate_head") != head_sha:
        raise ReviewRequestError("remote evidence index head does not match candidate head")

    candidates: dict[str, tuple[Path, bytes, dict[str, Any]]] = {}
    for path in candidate_paths:
        raw = path.read_bytes()
        value = json.loads(raw)
        denominator = _string(value.get("denominator"), f"{path}: denominator")
        if denominator in candidates:
            raise ReviewRequestError(f"duplicate candidate for {denominator}")
        candidates[denominator] = (path, raw, value)
    if set(candidates) != set(required):
        raise ReviewRequestError(
            f"candidate set mismatch: missing={sorted(set(required)-set(candidates))} "
            f"extra={sorted(set(candidates)-set(required))}"
        )

    candidate_dir = output_dir / "candidates"
    request_dir = output_dir / "review-requests"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    request_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    total_leaves = 0
    total_manual = 0

    for denominator in required:
        source_path, raw, candidate = candidates[denominator]
        route = routes.get(denominator)
        remote = remotes.get(denominator)
        if not isinstance(route, dict) or not isinstance(remote, dict):
            raise ReviewRequestError(f"missing route or remote evidence for {denominator}")
        filename = _string(route.get("candidate_filename"), f"{denominator}: candidate_filename")
        owner_role = _string(route.get("owner_role"), f"{denominator}: owner_role")
        reviewer_roles = route.get("reviewer_roles")
        if not isinstance(reviewer_roles, list) or len(set(reviewer_roles)) < 2:
            raise ReviewRequestError(f"{denominator}: two distinct reviewer roles are required")
        if owner_role not in set(policy.get("owner_roles", [])):
            raise ReviewRequestError(f"{denominator}: owner role is not allowed")
        if remote.get("head_sha") != head_sha or remote.get("conclusion") != "success":
            raise ReviewRequestError(f"{denominator}: remote evidence is not exact-head success")
        if remote.get("evidence_kind") != "immutable-job-log" or remote.get("log_sealed") is not True:
            raise ReviewRequestError(f"{denominator}: sealed immutable job-log evidence is required")

        leaves = candidate.get("leaves")
        manuals = candidate.get("manual_contracts", [])
        if not isinstance(leaves, list) or not leaves:
            raise ReviewRequestError(f"{denominator}: candidate leaves are empty")
        if not isinstance(manuals, list):
            raise ReviewRequestError(f"{denominator}: manual_contracts must be an array")
        leaf_ids: set[str] = set()
        decisions: list[dict[str, Any]] = []
        default_task = _string(routing.get("default_task_id"), "default_task_id")
        default_gate = _string(routing.get("default_gate_id"), "default_gate_id")
        for leaf in sorted(leaves, key=lambda item: item.get("id", "")):
            if not isinstance(leaf, dict):
                raise ReviewRequestError(f"{denominator}: leaf must be an object")
            leaf_id = _string(leaf.get("id"), f"{denominator}: leaf id")
            signature = _string(leaf.get("signature_hash"), f"{leaf_id}: signature_hash")
            if leaf_id in leaf_ids or not signature.startswith("sha256:") or len(signature) != 71:
                raise ReviewRequestError(f"{denominator}: duplicate leaf or invalid signature {leaf_id}")
            leaf_ids.add(leaf_id)
            task_id = _choice(leaf, "task_ids", "task_id", default_task)
            test_id = _choice(
                leaf,
                "test_ids",
                "test_id",
                "TG-DIFF-" + hashlib.sha256(leaf_id.encode("utf-8")).hexdigest()[:18].upper(),
            )
            decisions.append(
                {
                    "leaf_id": leaf_id,
                    "signature_hash": signature,
                    "classification": "mandatory",
                    "owner_role": owner_role,
                    "task_id": task_id,
                    "test_id": test_id,
                    "gate_id": default_gate,
                    "evidence_path": f"manifests/evidence/denominators/{denominator.lower()}/{leaf_id}.json",
                    "reviewer_ids": [],
                    "proposal_only": True,
                }
            )

        manual_rows: list[dict[str, Any]] = []
        for item in manuals:
            if not isinstance(item, dict):
                raise ReviewRequestError(f"{denominator}: manual contract must be an object")
            manual_rows.append(
                {
                    "identity": sha256_bytes(canonical_bytes(item)),
                    "disposition": "owned-blocker",
                    "owner_role": owner_role,
                    "issue_url": _string(route.get("issue_url"), f"{denominator}: issue_url"),
                    "gate_ids": [default_gate],
                    "reviewer_ids": [],
                    "proposal_only": True,
                    "source_contract": item,
                }
            )

        candidate_sha = sha256_bytes(raw)
        target = candidate_dir / filename
        target.write_bytes(raw)
        request = {
            "schema": "trillionnium.denominator-review-request.v1",
            "project_id": "trillionnium-game",
            "denominator": denominator,
            "status": "awaiting-independent-review",
            "candidate_path": target.as_posix(),
            "candidate_sha256": candidate_sha,
            "candidate_head": head_sha,
            "author_identity": "trillionnium-gap-closure-bot",
            "self_approval": False,
            "required_reviewer_count": policy.get("minimum_reviewers"),
            "required_reviewer_roles": reviewer_roles,
            "proposal_policy": "Every extracted leaf is conservatively proposed mandatory; only independent review may change classification.",
            "review_bundle_template": {
                "schema": "trillionnium.denominator-review.v1",
                "denominator": denominator,
                "candidate_sha256": candidate_sha,
                "candidate_head": head_sha,
                "author_identity": "trillionnium-gap-closure-bot",
                "self_approval": False,
                "reviewers": [],
                "leaf_decisions": decisions,
                "manual_contracts": manual_rows,
                "remote_evidence": remote,
            },
            "claims": {
                "classification_review_completed": False,
                "independent_review_completed": False,
                "reviewed_lock_written": False,
                "sg1_complete": False,
                "compatibility_credit": False,
            },
        }
        request_path = request_dir / f"{denominator.lower()}.review-request.json"
        request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total_leaves += len(decisions)
        total_manual += len(manual_rows)
        rows.append(
            {
                "denominator": denominator,
                "candidate_path": target.as_posix(),
                "candidate_sha256": candidate_sha,
                "review_request_path": request_path.as_posix(),
                "leaf_count": len(decisions),
                "manual_blocker_count": len(manual_rows),
                "issue_url": route["issue_url"],
                "remote_evidence": remote,
            }
        )

    worklist = {
        "schema": "trillionnium.denominator-review-worklist.v1",
        "project_id": "trillionnium-game",
        "candidate_head": head_sha,
        "status": "awaiting-independent-review",
        "required_denominator_count": len(required),
        "candidate_count": len(rows),
        "total_leaf_count": total_leaves,
        "manual_blocker_count": total_manual,
        "denominators": rows,
        "claims": {
            "all_candidates_materialized": True,
            "all_leaves_have_conservative_proposals": True,
            "independent_review_completed": False,
            "all_denominators_reviewed_locked": False,
            "sg1_complete": False,
            "compatibility_credit": False,
        },
    }
    (output_dir / "denominator-review-worklist.json").write_text(
        json.dumps(worklist, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return worklist
''',
    "scripts/build-denominator-review-request.py": r'''
#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from tools.denominator.review_request import build_review_package


def main() -> int:
    parser = argparse.ArgumentParser(description="Build exact-head denominator review requests")
    parser.add_argument("--candidate", action="append", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--remote-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("config/denominator-review-policy.json"))
    parser.add_argument("--routing", type=Path, default=Path("config/denominator-review-routing.json"))
    args = parser.parse_args()
    worklist = build_review_package(
        candidate_paths=args.candidate,
        head_sha=args.head_sha,
        remote_index_path=args.remote_index,
        output_dir=args.output_dir,
        policy_path=args.policy,
        routing_path=args.routing,
    )
    print(
        f"denominators={worklist['candidate_count']} leaves={worklist['total_leaf_count']} "
        f"manual_blockers={worklist['manual_blocker_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "tests/denominator/test_source_denominator.py": r'''
from __future__ import annotations

import unittest

from tools.denominator.source_manifest import ROOT, canonical_bytes, generate


class SourceDenominatorTests(unittest.TestCase):
    def test_repository_source_candidate_is_deterministic_and_fail_closed(self):
        first = generate(ROOT)
        second = generate(ROOT)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(first["denominator"], "DEN-SOURCE")
        self.assertGreaterEqual(first["leaf_count"], 30)
        self.assertEqual(first["unclassified_count"], first["leaf_count"])
        self.assertEqual(first["unreviewed_count"], first["leaf_count"])
        self.assertFalse(first["sg1_eligible"])
        self.assertFalse(first["compatibility_credit"])
        classes = {leaf["class"] for leaf in first["leaves"]}
        self.assertTrue(
            {
                "upstream_source_root",
                "upstream_source_object",
                "sdk_source_root",
                "database_test_image",
                "toolchain_or_dependency_lock",
            }.issubset(classes)
        )


if __name__ == "__main__":
    unittest.main()
''',
    "tests/denominator/test_review_request.py": r'''
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.denominator.review_request import build_review_package

ROOT = Path(__file__).resolve().parents[2]
HEAD = "1" * 40


class ReviewRequestTests(unittest.TestCase):
    def test_all_required_candidates_receive_conservative_review_templates(self):
        policy = json.loads((ROOT / "config/denominator-review-policy.json").read_text())
        routing = json.loads((ROOT / "config/denominator-review-routing.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            candidates = []
            remotes = {}
            for index, denominator in enumerate(policy["required_denominators"], start=1):
                candidate = {
                    "denominator": denominator,
                    "leaves": [
                        {
                            "id": f"TG-TEST-{index}",
                            "signature_hash": "sha256:" + f"{index:064x}"[-64:],
                            "task_ids": ["TG-W0-002"],
                            "test_ids": [f"TG-DIFF-TEST-{index}"],
                        }
                    ],
                    "manual_contracts": (
                        [{"class": "restricted", "symbol": "x"}]
                        if denominator == "DEN-CONSOLE"
                        else []
                    ),
                }
                path = base / routing["routes"][denominator]["candidate_filename"]
                path.write_text(json.dumps(candidate, sort_keys=True) + "\n")
                candidates.append(path)
                remotes[denominator] = {
                    "evidence_kind": "immutable-job-log",
                    "head_sha": HEAD,
                    "pull_request": 42,
                    "workflow_run_id": 100,
                    "job_id": 200,
                    "job_name": "generate-review-package",
                    "conclusion": "success",
                    "archive_sha256": "sha256:" + "a" * 64,
                    "assertion_count": 2,
                    "log_sealed": True,
                }
            remote_path = base / "remote.json"
            remote_path.write_text(
                json.dumps({"candidate_head": HEAD, "denominators": remotes}) + "\n"
            )
            output = base / "output"
            worklist = build_review_package(
                candidate_paths=candidates,
                head_sha=HEAD,
                remote_index_path=remote_path,
                output_dir=output,
                policy_path=ROOT / "config/denominator-review-policy.json",
                routing_path=ROOT / "config/denominator-review-routing.json",
            )
            self.assertEqual(worklist["candidate_count"], 14)
            self.assertEqual(worklist["total_leaf_count"], 14)
            self.assertEqual(worklist["manual_blocker_count"], 1)
            self.assertFalse(worklist["claims"]["sg1_complete"])
            for row in worklist["denominators"]:
                request = json.loads(Path(row["review_request_path"]).read_text())
                template = request["review_bundle_template"]
                self.assertEqual(template["reviewers"], [])
                self.assertTrue(
                    all(
                        decision["classification"] == "mandatory"
                        and decision["reviewer_ids"] == []
                        and decision["proposal_only"] is True
                        for decision in template["leaf_decisions"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
''',
    "docs/development/DENOMINATOR_REVIEW_REQUEST_PACKAGE.md": r'''
# Denominator review request package

Status: exact-source review input; no reviewed-lock, SG1, compatibility or production claim.

The package materializes all fourteen candidate denominator files and a one-to-one
review request for every extracted leaf. To avoid denominator shrinkage, every leaf
is conservatively proposed as `mandatory`. A proposal is not a classification
decision and contains no reviewer identity.

Each request includes:

- the exact candidate head and SHA-256;
- every stable leaf ID and signature hash;
- proposed owner, task, differential test, gate and evidence path;
- two required independent reviewer roles;
- exact remote workflow run/job identity and deterministic archive digest;
- every unresolved manual contract as an `owned-blocker` linked to an issue;
- a review-bundle template whose reviewer arrays are intentionally empty.

Independent reviewers may retain `mandatory`, select an approved optional profile,
or create a time-bounded versioned exclusion with an ADR and the required reviewer
count. They may not remove a leaf silently. Restricted Console ACL implementation
material remains a legal/manual blocker and must not be copied into this repository.

The package cannot close SG1. The reviewed-lock tool still requires exact leaf
coverage, author/reviewer separation, two real reviewers, accepted remote evidence,
manual-contract disposition and a separate global SG1 gate review.
''',
}


def main() -> None:
    for relative, content in FILES.items():
        path = Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
