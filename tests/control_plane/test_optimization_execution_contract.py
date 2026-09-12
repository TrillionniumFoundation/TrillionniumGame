"""Regression coverage for optimization scope and source contracts, not acceptance.

The native App tests prove compiled behavior separately. Text checks here only
prevent omission and regression of the declared source boundary.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_REF = "docs/status/CURRENT_STATE.json#/authority"
SPEC_PATH = "contracts/development/optimization-execution-v1.json"
DIMENSIONS = {
    "scope-and-dependencies", "public-api-and-examples", "state-and-data",
    "errors-and-side-effects", "concurrency-and-recovery", "security-and-budgets",
    "tests-and-compatibility", "operations-and-change",
}
APP_PATHS = (
    "crates/trnm-server/src/runtime/app.rs",
    "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs",
)
NATIVE_TESTS = (
    "app_debug_redacts_normal_pretty_and_nested_output",
    "app_debug_does_not_require_repository_debug",
    "app_debug_does_not_traverse_repository",
    "drain_authentication_precedes_body_validation",
    "authenticated_invalid_drain_does_not_change_shared_state",
)


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_spec(document):
    require(document.get("schema") == "trillionnium.optimization-execution-contract.v1", "schema")
    require(document.get("project_id") == "trillionnium-game", "project")
    require(type(document.get("plan_version")) is int and document["plan_version"] == 3, "plan")
    require(document.get("candidate_authority_ref") == AUTHORITY_REF, "authority")
    require(document.get("claim_credit") is False, "credit")
    require(document.get("all_priorities_completed") is False, "completion")
    require(document.get("design_kind") == "proposed-engineering-contract", "proposal")
    require(document.get("preserved_denominator_families") == 14, "denominator")
    dimensions = document.get("required_design_dimensions", [])
    require(len(dimensions) == 8 and set(dimensions) == DIMENSIONS, "dimensions")
    priorities = document.get("priorities", [])
    require([row.get("priority") for row in priorities] == list(range(1, 6)), "priorities")
    all_tasks = set()
    for row in priorities:
        require(type(row["priority"]) is int, "priority type")
        for key in ("milestone_items", "owner_roles", "implementation_steps", "acceptance_outputs"):
            values = row.get(key)
            require(isinstance(values, list) and values and
                    all(isinstance(value, str) and value.strip() for value in values), key)
            require(len(values) == len(set(values)), "duplicate " + key)
        all_tasks.update(row["milestone_items"])
    require(all_tasks == {f"TG-V3-{n:03d}" for n in range(1, 13)}, "milestone coverage")
    domains = document.get("domain_work_packages", [])
    require(len(domains) == 11 and {row.get("issue") for row in domains} == set(range(137, 148)), "domains")
    for row in domains:
        require(type(row["issue"]) is int, "issue type")
        require(row.get("compatibility_credit") is False, "domain credit")
        require(row.get("delivery_kind") == "required-design-and-implementation", "delivery")
        require(bool(row.get("owner_role")), "owner")
        design = row.get("design_deliverables", {})
        require(set(design) == DIMENSIONS, "domain dimensions")
        require(all(isinstance(value, str) and value.strip() for value in design.values()), "empty design")
    for key, count in (("module_followups", 23), ("component_followups", 9), ("integration_seams", 7)):
        rows = document.get(key, [])
        require(len(rows) == count and len({row.get("id") for row in rows}) == count, key)
    require(document["capacity_and_recovery"].get("objective_status") == "requires-independent-approval", "objective approval")
    require(document["capacity_and_recovery"].get("endurance_seconds") == [86400, 259200, 604800], "endurance")


def validate_bindings(roadmap, gaps, index):
    require(roadmap.get("candidate_authority_ref") == AUTHORITY_REF, "roadmap authority")
    integration = [row for row in gaps["gaps"] if row["id"] == "GAP-P0-PR-001"]
    require(len(integration) == 1, "integration gap")
    require(integration[0].get("candidate_authority_ref") == AUTHORITY_REF, "gap authority")
    pending = [row for row in index["pending_requirements"] if row["id"] == "REQ-EV-CURRENT-HEAD-MERGE-GATE"]
    require(len(pending) == 1, "pending requirement")
    require(pending[0].get("candidate_authority_ref") == AUTHORITY_REF, "evidence authority")
    # Historical entries and issue_refs are deliberately not scanned or rewritten.
    active = json.dumps({
        "title": roadmap["title"], "exit_conditions": roadmap["exit_conditions"],
        "initial_tasks": roadmap["items"][:2],
        "integration_criteria": integration[0]["close_criteria"],
        "pending_description": pending[0]["description"],
    })
    require(re.search(r"\bPR\s*#\d+|\bpull request\s*#\d+", active, re.I) is None, "hardcoded current PR")


def roadmap_digest(roadmap):
    projection = {
        "root": {key: value for key, value in roadmap.items()
                 if key not in {"status", "updated_at", "items", "acceptance_target"}},
        "items": [{key: value for key, value in row.items()
                   if key not in {"status", "evidence_ids", "acceptance_target"}}
                  for row in roadmap["items"]],
    }
    return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def validate_app_source(source):
    require(re.search(r"#\[derive\([^\]]*Debug[^\]]*\)\]\s*pub struct App", source) is None,
            "derived App Debug")
    marker = "impl<R> std::fmt::Debug for App<R> {"
    require(source.count(marker) == 1, "manual App Debug")
    block = source.split(marker, 1)[1].split("impl<R: Repository> App<R> {", 1)[0]
    require("[REDACTED]" in block and "finish_non_exhaustive" in block, "redacted shape")
    require("self." not in block, "debug traverses internal state")
    drain = source.split("fn drain(&mut self, request: &Request) -> Response {", 1)[1]
    drain = drain.split("fn bootstrap(", 1)[0]
    require(drain.index("!self.authorized(request)") < drain.index("!request.body.is_empty()")
            < drain.index("self.drain.begin()"), "drain auth order")
    for name in NATIVE_TESTS:
        require(source.count(f"fn {name}(") == 1, "missing native regression")


class SpecificationTests(unittest.TestCase):
    def setUp(self):
        self.document = load(SPEC_PATH)

    def reject(self, mutate, message):
        mutate(self.document)
        with self.assertRaisesRegex(ValueError, message):
            validate_spec(self.document)

    def test_current_complete_proposal_shape(self):
        validate_spec(self.document)

    def test_credit_rejected(self):
        self.reject(lambda d: d.update(claim_credit=True), "credit")

    def test_completion_rejected(self):
        self.reject(lambda d: d.update(all_priorities_completed=True), "completion")

    def test_wrong_authority_rejected(self):
        self.reject(lambda d: d.update(candidate_authority_ref="main"), "authority")

    def test_missing_priority_rejected(self):
        self.reject(lambda d: d["priorities"].pop(), "priorities")

    def test_duplicate_priority_rejected(self):
        self.reject(lambda d: d["priorities"][1].update(priority=1), "priorities")

    def test_empty_acceptance_output_rejected(self):
        self.reject(lambda d: d["priorities"][0].update(acceptance_outputs=[]), "acceptance_outputs")

    def test_missing_domain_rejected(self):
        self.reject(lambda d: d["domain_work_packages"].pop(), "domains")

    def test_duplicate_domain_rejected(self):
        self.reject(lambda d: d["domain_work_packages"][1].update(issue=137), "domains")

    def test_missing_design_dimension_rejected(self):
        self.reject(lambda d: d["domain_work_packages"][0]["design_deliverables"].pop("state-and-data"), "dimensions")

    def test_blank_design_rejected(self):
        self.reject(lambda d: d["domain_work_packages"][0]["design_deliverables"].update({"state-and-data": " "}), "empty design")

    def test_domain_credit_rejected(self):
        self.reject(lambda d: d["domain_work_packages"][0].update(compatibility_credit=True), "domain credit")

    def test_missing_module_rejected(self):
        self.reject(lambda d: d["module_followups"].pop(), "module_followups")

    def test_duplicate_component_rejected(self):
        self.reject(lambda d: d["component_followups"].__setitem__(1, d["component_followups"][0]), "component_followups")

    def test_missing_integration_seam_rejected(self):
        self.reject(lambda d: d["integration_seams"].pop(), "integration_seams")

    def test_reduced_denominator_rejected(self):
        self.reject(lambda d: d.update(preserved_denominator_families=13), "denominator")

    def test_short_endurance_rejected(self):
        self.reject(lambda d: d["capacity_and_recovery"].update(endurance_seconds=[60]), "endurance")

    def test_claimed_objective_approval_rejected(self):
        self.reject(lambda d: d["capacity_and_recovery"].update(objective_status="approved"), "objective approval")


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.roadmap = load("docs/roadmap/NEXT_MILESTONE.json")
        self.gaps = load("docs/status/GAP_REGISTER.json")
        self.index = load("docs/evidence/index.json")

    def check(self):
        validate_bindings(self.roadmap, self.gaps, self.index)

    def test_current_bindings_agree(self):
        self.check()

    def test_stale_roadmap_number_rejected(self):
        self.roadmap["items"][0]["title"] = "Reconcile PR #63"
        with self.assertRaisesRegex(ValueError, "hardcoded current PR"):
            self.check()

    def test_stale_evidence_pending_rejected(self):
        self.index["pending_requirements"][0]["description"] = "Bind pull request #163"
        with self.assertRaisesRegex(ValueError, "hardcoded current PR"):
            self.check()

    def test_historical_evidence_not_reinterpreted(self):
        before = copy.deepcopy(self.index["entries"])
        self.check()
        self.assertEqual(before, self.index["entries"])

    def test_substituted_gap_authority_rejected(self):
        next(row for row in self.gaps["gaps"] if row["id"] == "GAP-P0-PR-001")["candidate_authority_ref"] = "other.json"
        with self.assertRaisesRegex(ValueError, "gap authority"):
            self.check()

    def test_roadmap_scope_pin_matches_current_requirements(self):
        tree = ast.parse((ROOT / "scripts/check-status-transitions.py").read_text())
        values = [ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and any(
                      isinstance(target, ast.Name) and target.id == "ROADMAP_SCOPE_SHA256"
                      for target in node.targets)]
        self.assertEqual(values, [roadmap_digest(self.roadmap)])

    def test_removing_a_required_gap_changes_pinned_scope(self):
        before = roadmap_digest(self.roadmap)
        self.roadmap["items"][0]["gap_ids"].pop()
        self.assertNotEqual(before, roadmap_digest(self.roadmap))

    def test_status_does_not_redefine_pinned_scope(self):
        before = roadmap_digest(self.roadmap)
        self.roadmap["items"][0]["status"] = "blocked"
        self.assertEqual(before, roadmap_digest(self.roadmap))


class SourceContractTests(unittest.TestCase):
    def test_both_app_sources_retain_native_regressions(self):
        for path in APP_PATHS:
            with self.subTest(path=path):
                validate_app_source((ROOT / path).read_text())

    def test_derived_debug_regression_rejected(self):
        source = (ROOT / APP_PATHS[0]).read_text()
        source = source.replace("pub struct App<R> {", "#[derive(Debug)]\npub struct App<R> {")
        with self.assertRaisesRegex(ValueError, "derived App Debug"):
            validate_app_source(source)

    def test_nested_debug_traversal_rejected(self):
        source = (ROOT / APP_PATHS[0]).read_text()
        source = source.replace('.finish_non_exhaustive()', '.field("repository", &self.repository).finish_non_exhaustive()', 1)
        with self.assertRaisesRegex(ValueError, "debug traverses"):
            validate_app_source(source)

    def test_removed_native_test_rejected(self):
        source = (ROOT / APP_PATHS[0]).read_text()
        source = source.replace("fn " + NATIVE_TESTS[0] + "(", "fn renamed_away(")
        with self.assertRaisesRegex(ValueError, "missing native regression"):
            validate_app_source(source)


class CompleteRepositoryIntegrationTests(unittest.TestCase):
    def test_registered_module_and_component_coverage(self):
        spec = load(SPEC_PATH)
        modules = load("docs/status/MODULE_DOCUMENTATION.json")["modules"]
        components = load("docs/status/COMPONENT_DOCUMENTATION.json")["components"]
        self.assertEqual({row["id"] for row in spec["module_followups"]},
                         {row["id"] for row in modules})
        self.assertEqual({row["id"] for row in spec["component_followups"]},
                         {row["id"] for row in components})
        authority = load("docs/status/CURRENT_STATE.json")["authority"]
        self.assertEqual(authority["repository"], "TrillionniumFoundation/TrillionniumGame")
        self.assertIs(type(authority["candidate_pull_request"]), int)
        self.assertGreater(authority["candidate_pull_request"], 0)
        for row in spec["module_followups"]:
            self.assertTrue((ROOT / row["owning_document"]).is_file())


if __name__ == "__main__":
    unittest.main()
