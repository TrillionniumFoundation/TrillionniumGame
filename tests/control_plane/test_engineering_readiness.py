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
LIST_ROUTES = '''pub(crate) const STORAGE_LIST_ROUTES: [(&str, &str); 2] = [
    ("GET", "/v2/storage/{collection}"),
    ("GET", "/v2/storage/{collection}/{user_id}"),
];
'''
LIST_DOCUMENT_ROWS = '''| `GET` | `/v2/storage/{collection}` | Public client list |
| `GET` | `/v2/storage/{collection}/{user_id}` | Owner client list |
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

    def list_fixture(self) -> str:
        source = LIST_ROUTES + APP.replace(
            '            _ =>',
            '            ("GET", target) if storage_list_api::is_list_target(target) => list(),\n            _ =>',
        )
        put(self.root, "crates/trnm-server/src/runtime/mod.rs", '''pub(crate) mod app;
#[cfg(test)]
mod unrelated_tests;
pub(crate) mod storage_list_api;
''')
        put(self.root, "crates/trnm-server/src/runtime/app.rs", source)
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT.replace(
            "<!-- trnm-server-routes:end -->", LIST_DOCUMENT_ROWS + "<!-- trnm-server-routes:end -->"))
        return source

    def test_integrated_dynamic_list_routes_are_extracted_from_the_owned_constant(self) -> None:
        self.list_fixture()
        result = MODULE.inspect(self.root)
        self.assertEqual(result["server_interface"]["routes"], [
            ("GET", "/healthz"),
            ("GET", "/v2/storage/{collection}"),
            ("GET", "/v2/storage/{collection}/{user_id}"),
            ("POST", "/v1/authority/commit"),
        ])
        self.assertIs(result["claim_credit"], False)

    def test_early_test_only_import_and_helper_do_not_hide_production_list_routes(self) -> None:
        source = self.list_fixture()
        early = "#[cfg(test)]\nuse crate::TestVerifier;\n\n#[cfg(test)]\nfn test_only_helper() {}\n"
        put(self.root, "crates/trnm-server/src/runtime/app.rs", early + source)
        result = MODULE.inspect(self.root)
        self.assertEqual(set(result["server_interface"]["routes"]), {
            ("GET", "/healthz"), ("POST", "/v1/authority/commit"),
            ("GET", "/v2/storage/{collection}"),
            ("GET", "/v2/storage/{collection}/{user_id}"),
        })
        # The same early cfg markers cannot make a fixture-only constant real.
        changed = early + source.removeprefix(LIST_ROUTES) + "\n#[cfg(test)]\nmod tests {\n" + LIST_ROUTES + "}\n"
        put(self.root, "crates/trnm-server/src/runtime/app.rs", changed)
        self.rejected()

    def test_conditional_route_constant_cannot_advertise_production_templates(self) -> None:
        source = self.list_fixture()
        for attrs in ("#[cfg(test)]\n", "#[cfg(any(test, feature = \"fake\"))]\n",
                      "#[cfg(test)]\n#[allow(dead_code)]\n"):
            with self.subTest(attributes=attrs):
                put(self.root, "crates/trnm-server/src/runtime/app.rs", attrs + source)
                self.rejected()

    def test_integrated_list_routes_require_the_authoritative_constant(self) -> None:
        source = self.list_fixture()
        put(self.root, "crates/trnm-server/src/runtime/app.rs", source.removeprefix(LIST_ROUTES))
        self.rejected()

    def test_integrated_list_constant_requires_a_dispatcher_guard(self) -> None:
        self.list_fixture()
        put(self.root, "crates/trnm-server/src/runtime/app.rs", LIST_ROUTES + APP)
        self.rejected()

    def test_wrong_list_template_is_rejected(self) -> None:
        source = self.list_fixture()
        put(self.root, "crates/trnm-server/src/runtime/app.rs", source.replace(
            "/v2/storage/{collection}/{user_id}", "/v2/storage/{collection}/{owner}"))
        self.rejected()

    def test_duplicate_list_template_is_rejected(self) -> None:
        source = self.list_fixture()
        put(self.root, "crates/trnm-server/src/runtime/app.rs", source.replace(
            "/v2/storage/{collection}/{user_id}", "/v2/storage/{collection}"))
        self.rejected()

    def test_extra_list_constant_and_duplicate_literal_are_rejected(self) -> None:
        source = self.list_fixture()
        for changed in [LIST_ROUTES + source, source.replace(
            '            _ =>', '            ("GET", "/v2/storage/{collection}") => unreachable(),\n            _ =>')]:
            with self.subTest(source=changed):
                put(self.root, "crates/trnm-server/src/runtime/app.rs", changed)
                self.rejected()

    def test_list_constant_inside_tests_or_dispatcher_cannot_advertise_routes(self) -> None:
        source = self.list_fixture()
        for changed in [
            source.removeprefix(LIST_ROUTES) + "\n#[cfg(test)]\nmod tests {\n" + LIST_ROUTES + "}\n",
            source.removeprefix(LIST_ROUTES).replace("            _ =>", LIST_ROUTES + "            _ =>"),
        ]:
            with self.subTest(source=changed):
                put(self.root, "crates/trnm-server/src/runtime/app.rs", changed)
                self.rejected()

    def test_unintegrated_constant_does_not_add_inventory_credit(self) -> None:
        put(self.root, "crates/trnm-server/src/runtime/app.rs", LIST_ROUTES + APP)
        result = MODULE.inspect(self.root)
        self.assertEqual(result["server_interface"]["routes"], [
            ("GET", "/healthz"), ("POST", "/v1/authority/commit")])
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT.replace(
            "<!-- trnm-server-routes:end -->", LIST_DOCUMENT_ROWS + "<!-- trnm-server-routes:end -->"))
        self.rejected()

    def test_test_only_list_module_does_not_add_inventory_credit(self) -> None:
        self.list_fixture()
        put(self.root, "crates/trnm-server/src/runtime/mod.rs", "#[cfg(test)]\nmod storage_list_api;\n")
        self.rejected()

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



class SelectedLegacyInterfaceTest(unittest.TestCase):
    def test_actual_legacy_source_dispatch_and_query_routes_match_documented_interface(self):
        config = (ROOT / 'crates/trnm-server/src/runtime/config.rs').read_text()
        app = (ROOT / 'crates/trnm-server/src/runtime/app.rs').read_text()
        wire = (ROOT / 'crates/trnm-server/src/runtime/legacy_http_api.rs').read_text()
        interface = MODULE.source_interface(config, app, list_integrated=True, legacy_http_source=wire)
        MODULE.check_documented_interface(interface, (ROOT / 'docs/DEVELOPMENT.md').read_text())
        self.assertEqual(len(interface['routes']), 19)
        self.assertIn(('GET', '/healthcheck'), interface['routes'])

    def test_legacy_unused_recognizer_wrong_method_query_unknown_or_duplicate_path_rejects(self):
        config = (ROOT / 'crates/trnm-server/src/runtime/config.rs').read_text()
        app = (ROOT / 'crates/trnm-server/src/runtime/app.rs').read_text()
        wire = (ROOT / 'crates/trnm-server/src/runtime/legacy_http_api.rs').read_text()
        cases = [
            (app.replace('if legacy_auth_http_route("POST", target).is_some()', 'if false', 1), wire),
            (app.replace('.legacy_request(&mut self.repository, request, route)', '.session_request(&mut self.repository, request)', 1), wire),
            (app, wire.replace('if method != "POST"', 'if method != "GET"', 1)),
            (app, wire.replace("target.split_once('?').map_or(target, |(path, _)| path)", 'target', 1)),
            (app, wire.replace('/v2/session/logout', '/v2/session/unknown', 1)),
            (app, wire.replace('/v2/session/logout', '/v2/account/session/refresh', 1)),
        ]
        for altered_app, altered_wire in cases:
            with self.assertRaises(MODULE.ValidationError):
                MODULE.source_interface(config, altered_app, list_integrated=True, legacy_http_source=altered_wire)

class FiniteRustInterfaceTests(unittest.TestCase):
    """Pure source/document checks; these fixtures do not compile or execute Rust."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixture(self.root)
        put(self.root, "crates/trnm-server/src/runtime/mod.rs", "pub(crate) mod storage_list_api;\n")
        put(self.root, "docs/DEVELOPMENT.md", DOCUMENT.replace(
            "<!-- trnm-server-routes:end -->", LIST_DOCUMENT_ROWS + "<!-- trnm-server-routes:end -->"))
        self.dispatcher = APP.replace('            _ =>',
            '            ("GET", target) if storage_list_api::is_list_target(target) => list(),\n            _ =>')

    def check(self, source: str) -> dict:
        put(self.root, "crates/trnm-server/src/runtime/app.rs", source)
        return MODULE.inspect(self.root)

    def reject(self, source: str) -> None:
        with self.assertRaises(MODULE.ValidationError):
            self.check(source)

    def test_six_full_inventory_counterexamples_remain_rejected(self) -> None:
        cases = {
            "cfg_comment": "#[cfg(test)]\n// attribute still belongs to this item\n" + LIST_ROUTES,
            "cfg_multiline": "#[cfg(\n    test\n)]\n" + LIST_ROUTES,
            "pub_test_module": "#[cfg(test)]\npub mod tests {\n" + LIST_ROUTES + "}\n",
            "indented_test_module": "    #[cfg(test)]\n    mod tests {\n" + LIST_ROUTES + "    }\n",
            "raw_string": 'const FAKE: &str = r###"\n' + LIST_ROUTES + '"###;\n',
            "block_comment": "/*\n" + LIST_ROUTES + "*/\n",
        }
        for name, declaration in cases.items():
            with self.subTest(case=name):
                self.reject(declaration + self.dispatcher)

    def test_balanced_multiline_safe_attributes_comments_and_raw_templates(self) -> None:
        attributes = '#[allow(\n dead_code, /* nested /* comment */ still comment */ unused\n)]\n#[doc = r#"literal #[cfg(test)]"#]\n'
        raw = LIST_ROUTES.replace('"GET"', 'r"GET"').replace(
            '"/v2/storage/{collection}"', 'r###"/v2/storage/{collection}"###').replace(
            '"/v2/storage/{collection}/{user_id}"', 'r#"/v2/storage/{collection}/{user_id}"#')
        result = self.check(attributes + raw + self.dispatcher)
        self.assertEqual(len(result["server_interface"]["routes"]), 4)
        self.assertIs(result["claim_credit"], False)

    def test_conditional_attributes_remain_bound_across_all_trivia(self) -> None:
        for attribute in ('#[cfg(\nnot(test)\n)]', '#[cfg_attr(any(), allow(dead_code))]',
                          '#[allow(dead_code)]\n#[cfg(test)]', '#![cfg(test)]'):
            for trivia in ("\n", "\n// comment\n", " /* /* inner */ outer */ \n"):
                with self.subTest(attribute=attribute, trivia=trivia):
                    self.reject(attribute + trivia + LIST_ROUTES + self.dispatcher)

    def test_unknown_constant_attributes_fail_closed(self) -> None:
        for attribute in ('#[route_rewriter]', '#[custom::route_rewriter]', '#[derive(Clone)]'):
            with self.subTest(attribute=attribute):
                self.reject(attribute + "\n" + LIST_ROUTES + self.dispatcher)

    def test_nested_scope_constants_never_become_top_level_routes(self) -> None:
        for wrapper in ("pub mod nested { %s }", "mod nested { mod deeper { %s } }",
                        "fn helper() { %s }", "impl Holder { %s }",
                        "const NESTED: () = { %s };", "macro_rules! hidden { () => { %s } }"):
            with self.subTest(wrapper=wrapper):
                self.reject((wrapper % LIST_ROUTES) + self.dispatcher)

    def test_fake_constants_in_all_supported_opaque_literals_are_ignored(self) -> None:
        for prefix in ("r", "br", "cr"):
            with self.subTest(prefix=prefix):
                self.reject('const FAKE: &str = ' + prefix + '###"\n' + LIST_ROUTES + '"###;\n' + self.dispatcher)
        escaped = LIST_ROUTES.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        for prefix in ("", "b", "c"):
            with self.subTest(prefix=prefix):
                self.reject('const FAKE: &str = ' + prefix + '"' + escaped + '";\n' + self.dispatcher)

    def test_unicode_identifiers_lifetimes_characters_and_escapes_do_not_hide_routes(self) -> None:
        unrelated = "const Δείγμα: char = '{';\nconst 中文: char = '\\n';\nfn helper<'生命>(value: &'生命 str) {}\n"
        unrelated += "const RAW: &str = r###\"a \\\" { } #[cfg(test)]\"###;\n"
        escaped = LIST_ROUTES.replace('"GET"', '"G\\u{45}T"').replace('/v2/storage/', '/v2/sto\\x72age/')
        self.assertEqual(len(self.check(unrelated + escaped + self.dispatcher)["server_interface"]["routes"]), 4)

    def test_literal_type_and_expression_substitutes_are_rejected(self) -> None:
        changes = [LIST_ROUTES.replace('"GET"', "GET", 1),
                   LIST_ROUTES.replace('"GET"', 'b"GET"', 1),
                   LIST_ROUTES.replace('"GET"', 'c"GET"', 1),
                   LIST_ROUTES.replace('"GET"', '{ "GET" }', 1),
                   LIST_ROUTES.replace('"GET"', 'concat!("G", "ET")', 1),
                   LIST_ROUTES.replace("; 2]", "; COUNT]"),
                   LIST_ROUTES.replace("; 2]", "; 2 + 0]"),
                   LIST_ROUTES.replace("= [", "= &["),
                   LIST_ROUTES.replace("];", "].map(identity);"),
                   LIST_ROUTES.replace("(&str, &str)", "(RouteMethod, &str)")]
        for changed in changes:
            with self.subTest(source=changed):
                self.reject(changed + self.dispatcher)

    def test_fake_guard_in_comment_or_string_does_not_integrate(self) -> None:
        guard = '("GET", target) if storage_list_api::is_list_target(target) => list(),'
        for prefix in ("// " + guard + "\n", "/* " + guard + " */\n",
                       'const GUARD: &str = r#"' + guard + '"#;\n'):
            with self.subTest(prefix=prefix):
                self.reject(LIST_ROUTES + prefix + APP)

    def test_nested_or_conditional_dispatchers_do_not_integrate(self) -> None:
        for source in (LIST_ROUTES + "#[cfg(test)]\n" + self.dispatcher,
                       LIST_ROUTES + "#[route_rewriter]\n" + self.dispatcher,
                       LIST_ROUTES + "#[cfg_attr(any(), inline)]\n" + self.dispatcher,
                       LIST_ROUTES + "#[cfg(test)]\nimpl Holder {" + self.dispatcher + "}",
                       LIST_ROUTES + "mod production {" + self.dispatcher + "}",
                       LIST_ROUTES + self.dispatcher.replace("fn handle_inner() {", "fn handle_inner() { if false {").replace(
                           "\n}\n", "\n} }\n")):
            with self.subTest(source=source):
                self.reject(source)

    def test_test_module_before_dispatcher_and_early_cfg_helpers_preserve_real_constant(self) -> None:
        source = "#[cfg(\n test\n)] pub mod tests { " + LIST_ROUTES + " }\n"
        source += "#[cfg(test)] /* trivia */ use crate::Test;\n#[cfg(test)] fn helper() {}\n"
        source += LIST_ROUTES + "impl Holder {" + self.dispatcher + "}\n"
        self.assertEqual(len(self.check(source)["server_interface"]["routes"]), 4)

    def test_module_registration_uses_top_level_unconditional_external_items(self) -> None:
        self.check(LIST_ROUTES + self.dispatcher)
        put(self.root, "crates/trnm-server/src/runtime/mod.rs", '#[allow(\n dead_code\n)] /* trivia */ pub(crate) mod storage_list_api;')
        self.assertEqual(len(self.check(LIST_ROUTES + self.dispatcher)["server_interface"]["routes"]), 4)
        for source in ("#[cfg(\n test\n)] // trivia\nmod storage_list_api;",
                       "#[cfg_attr(any(), allow(dead_code))] mod storage_list_api;",
                       "mod nested {mod storage_list_api;}", "mod storage_list_api {}",
                       "/*mod storage_list_api;*/", 'const FAKE: &str = r#"mod storage_list_api;"#;',
                       '#[route_rewriter] mod storage_list_api;'):
            with self.subTest(source=source):
                put(self.root, "crates/trnm-server/src/runtime/mod.rs", source)
                self.reject(LIST_ROUTES + self.dispatcher)

    def test_nested_comments_and_delimiters_enforce_exact_depth_boundary(self) -> None:
        self.assertEqual(len(self.check("/*" * 64 + "body" + "*/" * 64 + LIST_ROUTES + self.dispatcher)["server_interface"]["routes"]), 4)
        self.reject("/*" * 65 + "body" + "*/" * 65 + LIST_ROUTES + self.dispatcher)
        MODULE._rust_tokens("(" * 64 + ")" * 64)
        with self.assertRaises(MODULE.ValidationError):
            MODULE._rust_tokens("(" * 65 + ")" * 65)

    def test_byte_token_and_raw_delimiter_caps_have_exact_boundaries(self) -> None:
        MODULE._rust_tokens(" " * MODULE.MAX_BYTES)
        with self.assertRaises(MODULE.ValidationError):
            MODULE._rust_tokens(" " * (MODULE.MAX_BYTES + 1))
        MODULE._rust_tokens("x " * MODULE.RUST_MAX_TOKENS)
        with self.assertRaises(MODULE.ValidationError):
            MODULE._rust_tokens("x " * (MODULE.RUST_MAX_TOKENS + 1))
        MODULE._rust_tokens('r' + "#" * 255 + '"{}"' + "#" * 255)
        with self.assertRaises(MODULE.ValidationError):
            MODULE._rust_tokens('r' + "#" * 256 + '"{}"' + "#" * 256)

    def test_malformed_lexical_inputs_fail_closed(self) -> None:
        for value in ('/* unclosed', '"unclosed', 'r##"unclosed"#', "'ab'", "b'é'", 'b"é"',
                      '"\\u{d800}"', '"\\xFF"', '"\\q"', 'c"nul\\0"', 'cr"nul\0"', "(]", "{", "\ud800"):
            with self.subTest(value=value), self.assertRaises(MODULE.ValidationError):
                MODULE._rust_tokens(value)

    def test_guard_expression_tuples_do_not_advertise_fixed_routes(self) -> None:
        dispatcher = self.dispatcher.replace('            _ =>',
            '            (_, path) if check(("GET", "/fake"), path) => error(),\n            _ =>')
        self.assertNotIn(("GET", "/fake"), self.check(LIST_ROUTES + dispatcher)["server_interface"]["routes"])


class FiniteRustAttributeRepairTests(unittest.TestCase):
    """Attribute policy probes; no Rust compiler or runtime semantics credit."""

    def setUp(self) -> None:
        self.case = FiniteRustInterfaceTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_five_peer_attribute_counterexamples_are_rejected(self) -> None:
        for attribute in ('#![cfg(test)]', '#![cfg_attr(not(test), cfg(any()))]', '#![route_rewriter]'):
            with self.subTest(inner=attribute):
                self.case.reject(LIST_ROUTES + self.case.dispatcher.replace(
                    'fn handle_inner() {', 'fn handle_inner() {\n' + attribute))
        self.case.reject('#[doc::route_rewriter]\n' + LIST_ROUTES + self.case.dispatcher)
        self.case.reject(LIST_ROUTES + '#[allow::route_rewriter]\n' + self.case.dispatcher)

    def test_supported_doc_and_lint_inner_attributes_preserve_routes(self) -> None:
        for attributes in ('#![doc = "fixed docs"]', '#![doc = r#"#[cfg(test)]"#]',
                           '#![doc(hidden)]', '#![doc(alias = "route")]', '#![allow(dead_code)]',
                           '#![allow(dead_code, clippy::needless_return, reason = "fixed lint reason",)]',
                           '#![warn(unused)]\n/* nested /* trivia */ done */\n#![doc = "fixed docs"]'):
            with self.subTest(attributes=attributes):
                result = self.case.check(LIST_ROUTES + self.case.dispatcher.replace(
                    'fn handle_inner() {', 'fn handle_inner() {\n' + attributes))
                self.assertEqual(len(result['server_interface']['routes']), 4)
                self.assertIs(result['claim_credit'], False)

    def test_controlled_attribute_paths_and_shapes_are_exact(self) -> None:
        attributes = ('#[doc::route_rewriter]', '#[allow::route_rewriter]', '#[cfg::route_rewriter]',
                      '#[doc]', '#[doc = 1]', '#[doc = b"docs"]', '#[doc = concat!("docs")]',
                      '#[allow = "dead_code"]', '#[allow()]', '#[allow("dead_code")]',
                      '#[allow(dead_code())]', '#[allow(dead_code,, unused)]',
                      '#[allow(dead_code) :: route_rewriter]')
        for attribute in attributes:
            with self.subTest(attribute=attribute, scope='constant'):
                self.case.reject(attribute + '\n' + LIST_ROUTES + self.case.dispatcher)
            with self.subTest(attribute=attribute, scope='function'):
                self.case.reject(LIST_ROUTES + attribute + '\n' + self.case.dispatcher)
            with self.subTest(attribute=attribute, scope='module'):
                put(self.case.root, 'crates/trnm-server/src/runtime/mod.rs', attribute + '\nmod storage_list_api;')
                self.case.reject(LIST_ROUTES + self.case.dispatcher)
                put(self.case.root, 'crates/trnm-server/src/runtime/mod.rs', 'mod storage_list_api;')

    def test_inner_conditional_unknown_and_qualified_attributes_fail_closed(self) -> None:
        for attributes in ('#![cfg(\n test\n)]', '#![cfg_attr(not(test), cfg(any()))]', '#![route_rewriter]',
                           '#![doc::route_rewriter]', '#![allow::route_rewriter]', '#![doc = concat!("docs")]',
                           '#![allow()]', '#![allow(dead_code)]\n// still attached\n#![cfg(test)]'):
            with self.subTest(attributes=attributes):
                self.case.reject(LIST_ROUTES + self.case.dispatcher.replace(
                    'fn handle_inner() {', 'fn handle_inner() {\n/* trivia */\n' + attributes))

    def test_helper_and_opaque_inner_attribute_text_do_not_change_dispatcher(self) -> None:
        for prefix in ('/* #![cfg(test)] */', 'let text = "#![cfg(test)]";',
                       'let text = r#"#![cfg_attr(not(test), cfg(any()))]"#;'):
            with self.subTest(prefix=prefix):
                result = self.case.check(LIST_ROUTES + self.case.dispatcher.replace(
                    'fn handle_inner() {', 'fn handle_inner() {\n' + prefix))
                self.assertEqual(len(result['server_interface']['routes']), 4)
        helper = 'fn helper() { #![cfg(test)] }\n'
        self.assertEqual(len(self.case.check(helper + LIST_ROUTES + self.case.dispatcher)['server_interface']['routes']), 4)

    def test_supported_outer_attribute_shapes_have_no_path_alias(self) -> None:
        for attribute in ('#[doc = "fixed docs"]', '#[allow(dead_code, clippy::needless_return)]',
                          '#[deprecated(since = "0.1", note = "fixed note")]', '#[inline(always)]',
                          '#[inline(never)]', '#[cold]', '#[must_use = "fixed message"]'):
            with self.subTest(attribute=attribute):
                result = self.case.check(LIST_ROUTES + attribute + '\n' + self.case.dispatcher)
                self.assertEqual(len(result['server_interface']['routes']), 4)
        for attribute in ('#[inline::always]', '#[cold()]', '#[inline(sometimes)]',
                          '#[deprecated(note = "a", note = "b")]', '#[must_use = c"message"]'):
            with self.subTest(attribute=attribute):
                self.case.reject(LIST_ROUTES + attribute + '\n' + self.case.dispatcher)


if __name__ == "__main__":
    unittest.main()


class CustomRouteInventoryTests(unittest.TestCase):
    def test_custom_closed_route_missing_or_email_substitution_rejects(self):
        config=(ROOT/'crates/trnm-server/src/runtime/config.rs').read_text()
        app=(ROOT/'crates/trnm-server/src/runtime/app.rs').read_text()
        wire=(ROOT/'crates/trnm-server/src/runtime/legacy_http_api.rs').read_text()
        for altered in (wire.replace('/v2/account/authenticate/custom','/v2/account/authenticate/email',1), wire.replace('/v2/account/authenticate/custom','/v2/account/authenticate/device',1)):
            with self.assertRaises(MODULE.ValidationError):
                MODULE.source_interface(config,app,list_integrated=True,legacy_http_source=altered)
