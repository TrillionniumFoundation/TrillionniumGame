"""Source qualification fences, separate from actual gRPC/native execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_grpc_check", ROOT / "scripts/check-trnm-server.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class NativeGrpcSourceTests(unittest.TestCase):
    def test_workflow_preserves_diagnostic_and_canonical_grpc_execution(self):
        source = (ROOT / ".github/workflows/grpc-health-source.yml").read_text()
        self.assertNotIn("represents only the pinned Nakama", source)
        for required in ("contracts/grpc/nakama-healthcheck-v1.json",
                         "python3 scripts/check-trnm-server.py",
                         "python3 scripts/check-rust-server-source-candidate.py",
                         "cargo test --package trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server --locked",
                         "cargo test --package trnm-server --lib --locked runtime::grpc",
                         "cargo clippy --package trnm-server --all-targets --locked -- -D warnings"):
            self.assertIn(required, source)

    def test_actual_bound_source_and_closed_claims(self):
        M.validate_native_grpc_auth_source()

    def fixture(self, directory):
        root = Path(directory)
        for relative in M.NATIVE_GRPC_AUTH_SOURCES | {M.NATIVE_GRPC_AUTH_CONTRACT, "docs/status/TRNM_SERVER_STATUS.json"}:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        return root

    def test_no_qualification_or_denominator_promotion(self):
        for field, key, value in [("claims", "native_grpc_executed", True), ("claims", "compatibility_credit", True), ("authority", "global_cancel_for_rpc", True), ("authority", "public_v5_gate_change", True), ("bounds", "accepted_connections_global", 64), ("bounds", "accepted_connection_lifetime_seconds", 0)]:
            with self.subTest(field=field, key=key), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                path = root / M.NATIVE_GRPC_AUTH_CONTRACT
                document = json.loads(path.read_text())
                document[field][key] = value
                path.write_text(json.dumps(document))
                with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                    M.validate_native_grpc_auth_source()
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / M.NATIVE_GRPC_AUTH_CONTRACT
            document = json.loads(path.read_text())
            document["full_rpc_denominator"] = 5
            path.write_text(json.dumps(document))
            with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                M.validate_native_grpc_auth_source()

    def test_unbound_byte_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / "crates/trnm-server/src/runtime/grpc_auth.rs"
            path.write_text(path.read_text() + "\n// unexpected byte drift\n")
            with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                M.validate_native_grpc_auth_source()

    def test_rebound_source_cannot_remove_ownership_fences(self):
        for relative, old, new in [("grpc_auth.rs", "let _permit = permit;", "drop(permit);"), ("grpc_transport.rs", "registry.close_all();", "// missing actual socket close")]:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                source = "crates/trnm-server/src/runtime/" + relative
                path = root / source
                self.assertIn(old, path.read_text())
                path.write_text(path.read_text().replace(old, new))
                contract = root / M.NATIVE_GRPC_AUTH_CONTRACT
                document = json.loads(contract.read_text())
                document["candidate_source_sha256"][source] = hashlib.sha256(path.read_bytes()).hexdigest()
                contract.write_text(json.dumps(document))
                with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                    M.validate_native_grpc_auth_source()

    def test_storage_rebound_projection_and_authority_drift_is_rejected(self):
        cases = [
            ("grpc_storage.rs", "StorageActor::User(user)", "StorageActor::Server"),
            ("grpc_storage.rs", "allows_batch_read(object.key.user_id() == user)", "allows_batch_read(true)"),
            ("grpc_storage.rs", "*total <= MAX_RESPONSE_BYTES", "*total <= usize::MAX"),
            ("grpc_storage.rs", ".verify_integrity()", ".unreviewed_integrity()"),
            ("grpc_storage.rs", "nanos: 0", "nanos: value.nanos as i32"),
            ("grpc_auth.rs", '"/nakama.api.Nakama/ReadStorageObjects" => true', '"/nakama.api.Nakama/ReadStorageObjects" => false'),
        ]
        for name, old, new in cases:
            with self.subTest(name=name, old=old), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                relative = "crates/trnm-server/src/runtime/" + name
                path = root / relative
                self.assertIn(old, path.read_text())
                path.write_text(path.read_text().replace(old, new))
                contract = root / M.NATIVE_GRPC_AUTH_CONTRACT
                document = json.loads(contract.read_text())
                document["candidate_source_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
                contract.write_text(json.dumps(document))
                with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                    M.validate_native_grpc_auth_source()

    def test_storage_claims_bounds_and_six_method_inventory_remain_fail_closed(self):
        for path, value in [("claims.native_database_qualified", True), ("claims.accepted", True), ("max_encoded_response_bytes", 4194304), ("max_batch", 101), ("empty_read_queries", False), ("new_store", True), ("public_v5_gate_change", True)]:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                contract = root / M.NATIVE_GRPC_AUTH_CONTRACT
                document = json.loads(contract.read_text())
                policy = document["storage_read"]
                parts = path.split(".")
                for name in parts[:-1]:
                    policy = policy[name]
                policy[parts[-1]] = value
                contract.write_text(json.dumps(document))
                with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                    M.validate_native_grpc_auth_source()
        document = json.loads((ROOT / M.NATIVE_GRPC_AUTH_CONTRACT).read_text())
        self.assertEqual(len(document["methods"]), 6)
        self.assertEqual(document["full_rpc_denominator"], 85)
        self.assertEqual(document["remaining_non_healthcheck_rpc_obligations"], 79)

    def test_storage_component_status_cannot_claim_acceptance(self):
        for field, value in [("accepted", True), ("represented_signatures", 85), ("production_gate", True)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                path = root / "docs/status/TRNM_SERVER_STATUS.json"
                document = json.loads(path.read_text())
                document["native_grpc_storage_read_source_candidate"][field] = value
                path.write_text(json.dumps(document))
                with patch.object(M, "ROOT", root), self.assertRaises(SystemExit):
                    M.validate_native_grpc_auth_source()
