import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_admission_check", ROOT / "scripts/check-trnm-server.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class NativeAdmissionSourceTests(unittest.TestCase):
    def test_exact_full_transitive_source_and_closed_claims(self):
        M.validate_native_admission_source()

    def test_issuer_and_test_target_mutations_fail_closed(self):
        sources = {path: (ROOT/path).read_text() for path in M.native_admission_source_paths()}
        for path, old, new in [
            ("crates/trnm-persistence-pg/src/schema_parts/migrate.rs", "#[cfg(test)]\n        pub(crate) fn diagnostic", "pub fn diagnostic"),
            ("crates/trnm-persistence-pg/src/schema_parts/migrate.rs", "target: AuthoritativeSchemaTarget,", "pub target: AuthoritativeSchemaTarget,"),
            ("crates/trnm-persistence-pg/src/schema_parts/migrate.rs", "#[derive(Clone, Copy, Debug)]", "#[derive(Clone, Copy, Debug, Default)]"),
            ("crates/trnm-persistence-pg/src/schema_parts/migrate.rs", "#[derive(Clone, Copy, Debug)]", "#[derive(Clone, Copy, Debug, Deserialize)]"),
            ("crates/trnm-persistence-pg/src/schema_parts/migrate.rs", "require_account_catalog_capture(target)?;", "// omitted gate"),
            ("crates/trnm-persistence-pg/src/lib.rs", "#[cfg(test)]\nextern crate self", "extern crate self"),
            ("crates/trnm-persistence-pg/src/lib.rs", '#[cfg(test)]\n#[path = "../../trnm-server/src/runtime/mod.rs"]', '#[path = "../../trnm-server/src/runtime/mod.rs"]'),
            ("crates/trnm-persistence-pg/src/schema_parts/account_catalog.rs", "const ACCOUNT_CATALOG_CAPTURE_READY: bool = false;", "const ACCOUNT_CATALOG_CAPTURE_READY: bool = true;"),
            ("crates/trnm-persistence-pg/Cargo.toml", "session-test-hooks = []", "session-test-hooks = []\nnative-admission = []"),
        ]:
            with self.subTest(path=path, mutation=old):
                changed = copy.copy(sources)
                self.assertIn(old, changed[path])
                changed[path] = changed[path].replace(old, new)
                with self.assertRaises(SystemExit):
                    M.validate_native_admission_structure(changed)

    def test_actual_server_and_native_lease_bodies_are_in_inventory(self):
        paths = M.native_admission_source_paths()
        for path in ("crates/trnm-server/src/runtime/server.rs", "crates/trnm-server/src/runtime/http.rs",
                     "crates/trnm-server/src/runtime/app.rs", "crates/trnm-server/src/runtime/pool.rs",
                     "crates/trnm-persistence-pg/src/nakama_account/native.rs",
                     "crates/trnm-persistence-pg/src/pool_parts/cancellation.rs",
                     "crates/trnm-persistence-pg/schema_build.rs", "Cargo.lock"):
            self.assertIn(path, paths)
