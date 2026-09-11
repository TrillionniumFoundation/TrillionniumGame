"""Source/document regression and hostile inventory fixtures; no acceptance credit."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/engineering_readiness.py"
SPEC = importlib.util.spec_from_file_location("engineering_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CONFIG = '''fn from_lookup() {
        let command = match arguments {
            [_, value] if value == "check-config" => Command::CheckConfig,
            [_, value] if value == "migrate" => Command::Migrate,
            [_, value] if value == "serve" => Command::Serve,
            _ => return Err(error),
        };
        let bind = lookup("TRNM_SERVER_BIND");
}
#[cfg(test)]
mod tests { const FAKE: &str = "TRNM_SERVER_TEST_ONLY"; }
'''
APP = '''fn handle_inner() {
        let response = match (request.method.as_str(), request.target.as_str()) {
            ("GET", "/healthz") => health(),
            ("POST", "/v1/authority/commit") => commit(),
            _ => error(),
        };
        if response.status < 400 { record(); }
}
'''
DOCUMENT = '''<!-- trnm-server-cli:start -->
`check-config`
`migrate`
`serve`
<!-- trnm-server-cli:end -->
<!-- trnm-server-routes:start -->
| `GET` | `/healthz` | Liveness |
| `POST` | `/v1/authority/commit` | Command |
<!-- trnm-server-routes:end -->
<!-- trnm-server-config:start -->
| `TRNM_SERVER_BIND` | Loopback bind |
<!-- trnm-server-config:end -->
'''


def put(root: Path, path: str, value: object) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def fixture(root: Path) -> None:
    put(root, "crates/trnm-server/Cargo.toml", '[package]\nname = "trnm-server"\n')
    put(root, "crates/trnm-server/README.md", "# trnm-server\nSynthetic fixture only.\n")
    put(root, "crates/trnm-server/src/runtime/config.rs", CONFIG)
    put(root, "crates/trnm-server/src/runtime/app.rs", APP)
    put(root, "docs/DEVELOPMENT.md", DOCUMENT)
    put(root, "docs/status/MODULE_DOCUMENTATION.json", {"modules": [{
        "id": "trnm-server", "documentation": "crates/trnm-server/README.md"}]})
    put(root, "docs/status/COMPONENT_DOCUMENTATION.json", {"components": [{"id": "COMPONENT-RUST-PACKAGES"}]})
    put(root, "docs/status/DOCUMENTATION_DEPTH.json", {
        "schema": "trillionnium.documentation-depth.v1", "project_id": "trillionnium-game",
        "assessment_kind": "engineering-review-proposal", "claim_credit": False,
        "required_design_dimensions": list(MODULE.DIMENSIONS),
        "modules": [{"id": "trnm-server", "depth": "partial-design",
                     "remaining_design_work": ["Typed service boundary"]}],
        "components": [{"id": "COMPONENT-RUST-PACKAGES", "remaining_design_work": ["Independent design review"]}],
    })
    put(root, "docs/status/GAP_REGISTER.json", {"gaps": [{
        "id": "GAP-P0-SERVER-001", "status": "source-candidate", "owner_role": "platform",
        "close_criteria": ["Execute and independently review the declared slice"],
        "required_evidence_types": ["unit", "wire-differential"], "evidence_ids": [],
    }]})

    put(root, "docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json", {
        "schema": "trillionnium.engineering-exit-detail.v1", "claim_credit": False,
        "gap_details": [{"id": "GAP-P0-SERVER-001", "priority_band": 1,
                         "implementation_steps": ["Service integration"],
                         "required_checks": ["Wire differential"],
                         "external_obligations": ["Independent review"]}],
        "domain_details": [{"issue": issue} for issue in range(137, 148)],
    })


class ReadinessUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        fixture(self.root)

    def change_json(self, path: str, change) -> None:
        value = json.loads((self.root / path).read_text(encoding="utf-8"))
        change(value)
        put(self.root, path, value)

    def rejected(self) -> None:
        with self.assertRaises(MODULE.ValidationError):
            MODULE.inspect(self.root)

    def test_positive_inventory_is_read_only_and_has_no_credit(self) -> None:
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = MODULE.inspect(self.root)
        self.assertEqual(result["module_count"], 1)
        self.assertEqual(result["gap_count"], 1)
        self.assertIs(result["claim_credit"], False)
        self.assertIs(result["closure_validated"], False)
        self.assertEqual(result["server_interface"]["environment_names"], ["TRNM_SERVER_BIND"])
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_old_cli_version_is_rejected(self) -> None:
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT.replace("`migrate`", "`version`"))
        self.rejected()

    def test_old_authority_route_is_rejected(self) -> None:
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT.replace("/v1/authority/commit", "/v1/command"))
        self.rejected()

    def test_new_source_command_requires_documentation(self) -> None:
        put(self.root, "crates/trnm-server/src/runtime/config.rs", CONFIG.replace(
            '            _ =>', '            [_, value] if value == "version" => Command::Version,\n            _ =>'))
        self.rejected()

    def test_new_route_requires_documentation(self) -> None:
        put(self.root, "crates/trnm-server/src/runtime/app.rs", APP.replace(
            '            _ =>', '            ("GET", "/new") => new(),\n            _ =>'))
        self.rejected()

    def test_new_configuration_name_requires_documentation(self) -> None:
        put(self.root, "crates/trnm-server/src/runtime/config.rs", CONFIG.replace(
            'let bind =', 'let bind = lookup("TRNM_SERVER_NEW");\n        let ignored ='))
        self.rejected()

    def test_duplicate_document_block_rejected(self) -> None:
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT + "\n<!-- trnm-server-cli:start -->")
        self.rejected()

    def test_duplicate_document_row_rejected(self) -> None:
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT.replace("`serve`", "`serve`\n`serve`"))
        self.rejected()

    def test_duplicate_source_route_rejected(self) -> None:
        put(self.root, "crates/trnm-server/src/runtime/app.rs", APP.replace(
            '("GET", "/healthz") => health(),', '("GET", "/healthz") => health(),\n            ("GET", "/healthz") => health(),'))
        self.rejected()

    def test_empty_source_extraction_rejected(self) -> None:
        put(self.root, "crates/trnm-server/src/runtime/app.rs", APP.replace('("GET", "/healthz")', "first").replace(
            '("POST", "/v1/authority/commit")', "second"))
        self.rejected()

    def test_removed_readme_rejected(self) -> None:
        (self.root / "crates/trnm-server/README.md").unlink()
        self.rejected()

    def test_unregistered_package_rejected(self) -> None:
        put(self.root, "crates/trnm-unregistered/Cargo.toml", "[package]\n")
        self.rejected()

    def test_missing_depth_assessment_rejected(self) -> None:
        self.change_json("docs/status/DOCUMENTATION_DEPTH.json", lambda obj: obj.update(modules=[]))
        self.rejected()

    def test_duplicate_depth_assessment_rejected(self) -> None:
        self.change_json("docs/status/DOCUMENTATION_DEPTH.json", lambda obj: obj["modules"].append(obj["modules"][0].copy()))
        self.rejected()

    def test_self_promoted_depth_credit_rejected(self) -> None:
        self.change_json("docs/status/DOCUMENTATION_DEPTH.json", lambda obj: obj.update(claim_credit=True))
        self.rejected()

    def test_new_component_requires_assessment(self) -> None:
        self.change_json("docs/status/COMPONENT_DOCUMENTATION.json", lambda obj: obj["components"].append({"id": "COMPONENT-NEW"}))
        self.rejected()

    def test_empty_remaining_work_rejected(self) -> None:
        self.change_json("docs/status/DOCUMENTATION_DEPTH.json", lambda obj: obj["modules"][0].update(remaining_design_work=[]))
        self.rejected()

    def test_closed_snapshot_is_not_validated_closure(self) -> None:
        self.change_json("docs/status/GAP_REGISTER.json", lambda obj: obj["gaps"][0].update(status="closed"))
        result = MODULE.inspect(self.root)
        self.assertEqual(result["recorded_gap_status_counts"], {"closed": 1})
        self.assertIs(result["closure_validated"], False)
        self.assertIs(result["claim_credit"], False)

    def test_empty_evidence_obligation_rejected(self) -> None:
        self.change_json("docs/status/GAP_REGISTER.json", lambda obj: obj["gaps"][0].update(required_evidence_types=[]))
        self.rejected()

    def test_duplicate_json_keys_rejected(self) -> None:
        put(self.root, "docs/status/DOCUMENTATION_DEPTH.json", '{"schema": "first", "schema": "second"}')
        self.rejected()

    def test_non_finite_json_rejected(self) -> None:
        put(self.root, "docs/status/DOCUMENTATION_DEPTH.json", '{"x": NaN}')
        self.rejected()

    def test_missing_gap_detail_rejected(self) -> None:
        self.change_json("docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json", lambda obj: obj.update(gap_details=[]))
        self.rejected()

    def test_omitted_full_surface_domain_rejected(self) -> None:
        self.change_json("docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json", lambda obj: obj["domain_details"].pop())
        self.rejected()

    def test_exit_detail_cannot_mint_credit(self) -> None:
        self.change_json("docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json", lambda obj: obj.update(claim_credit=True))
        self.rejected()

    def test_boolean_priority_is_not_an_integer_band(self) -> None:
        self.change_json("docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json", lambda obj: obj["gap_details"][0].update(priority_band=True))
        self.rejected()

    def test_missing_file_diagnostic_excludes_os_path(self) -> None:
        with self.assertRaises(MODULE.ValidationError) as error:
            MODULE.text(self.root, "missing.json")
        self.assertNotIn(str(self.root), str(error.exception))

    def test_cli_fails_with_missing_inputs(self) -> None:
        completed = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root / "missing")],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertNotIn("Traceback", completed.stderr)

    def test_cli_positive_emits_no_acceptance(self) -> None:
        completed = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root)],
                                   capture_output=True, text=True, timeout=10, check=True)
        result = json.loads(completed.stdout)
        self.assertIs(result["claim_credit"], False)
        self.assertIs(result["closure_validated"], False)


class RepositoryReadinessTests(unittest.TestCase):
    def test_real_repository_interface_depth_and_gap_inventory(self) -> None:
        result = MODULE.inspect(ROOT)
        self.assertGreater(result["module_count"], 0)
        self.assertGreater(result["gap_count"], 0)
        self.assertIs(result["claim_credit"], False)
        self.assertIs(result["closure_validated"], False)


if __name__ == "__main__":
    unittest.main()
