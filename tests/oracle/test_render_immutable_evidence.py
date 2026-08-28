from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/oracle/render-immutable-evidence.py"
SPEC = importlib.util.spec_from_file_location("oracle_evidence", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class EvidenceTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        lock_path = root / "lock.json"
        compose_path = root / "compose.yml"
        normalizers_path = root / "normalizers.json"
        facts_path = root / "facts.json"
        normalizers = {
            "schema": "trillionnium.oracle-normalizer-registry.v1",
            "project_id": "trillionnium-game",
            "status": "candidate-reviewed-required",
            "allowed": [{"surface": "jwt-access", "path": "$.iat", "reason": "time"}],
            "forbidden_path_fragments": ["uid", "user_id", "code", "amount"],
            "policy": {
                "raw_access_token_may_be_stored": False,
                "raw_refresh_token_may_be_stored": False,
                "identity_divergence_may_be_normalized": False,
                "authorization_divergence_may_be_normalized": False,
                "error_code_divergence_may_be_normalized": False,
                "durable_effect_divergence_may_be_normalized": False,
            },
        }
        normalizers_path.write_text(json.dumps(normalizers), encoding="utf-8")
        lock = {
            "schema": "trillionnium.immutable-oracle-lock.v2",
            "nakama": {"image": "nakama@sha256:x"},
            "nakama_common": {"commit": "a" * 40},
            "database": {"image": "postgres@sha256:y"},
            "claims": {
                "instrumented_equivalence": False,
                "sg2_complete": False,
                "compatibility_credit": False,
                "production_ready": False,
                "public_online": False,
            },
            "required_evidence": [
                "candidate_commit", "oracle_lock_sha256", "compose_sha256",
                "normalizer_registry_sha256", "rendered_config_sha256",
                "nakama_image_id", "postgres_image_id", "container_runtime",
                "kernel", "architecture", "health_status", "database_table_count",
                "started_at_utc", "completed_at_utc",
            ],
        }
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        compose_path.write_text("services: {}\n", encoding="utf-8")
        facts = {
            "candidate_commit": "b" * 40,
            "oracle_lock_sha256": mod.sha256(lock_path.read_bytes()),
            "compose_sha256": mod.sha256(compose_path.read_bytes()),
            "normalizer_registry_sha256": mod.sha256(normalizers_path.read_bytes()),
            "rendered_config_sha256": "sha256:" + "c" * 64,
            "nakama_image_id": "sha256:nakama", "postgres_image_id": "sha256:postgres",
            "container_runtime": "test", "kernel": "test", "architecture": "amd64",
            "health_status": "healthy", "database_table_count": "42",
            "started_at_utc": "2026-08-28T00:00:00Z", "completed_at_utc": "2026-08-28T00:00:01Z",
        }
        facts_path.write_text(json.dumps(facts), encoding="utf-8")
        return lock_path, compose_path, normalizers_path, facts_path

    def test_evidence_is_deterministic_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock, compose, normalizers, facts = self.fixture(Path(temporary))
            first = mod.render(lock, compose, normalizers, facts)
            second = mod.render(lock, compose, normalizers, facts)
            self.assertEqual(mod.canonical(first), mod.canonical(second))
            self.assertEqual(first["credit"], "diagnostic-only")
            self.assertFalse(any(first["claims"].values()))
            self.assertEqual(first["oracle"]["normalizer_registry"]["allowed_rule_count"], 1)

    def test_unhealthy_oracle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock, compose, normalizers, facts = self.fixture(Path(temporary))
            value = json.loads(facts.read_text())
            value["health_status"] = "unhealthy"
            facts.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(mod.EvidenceError):
                mod.render(lock, compose, normalizers, facts)

    def test_digest_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock, compose, normalizers, facts = self.fixture(Path(temporary))
            value = json.loads(facts.read_text())
            value["normalizer_registry_sha256"] = "sha256:" + "0" * 64
            facts.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(mod.EvidenceError):
                mod.render(lock, compose, normalizers, facts)

    def test_positive_claim_in_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock, compose, normalizers, facts = self.fixture(Path(temporary))
            value = json.loads(lock.read_text())
            value["claims"]["sg2_complete"] = True
            lock.write_text(json.dumps(value), encoding="utf-8")
            facts_value = json.loads(facts.read_text())
            facts_value["oracle_lock_sha256"] = mod.sha256(lock.read_bytes())
            facts.write_text(json.dumps(facts_value), encoding="utf-8")
            with self.assertRaises(mod.EvidenceError):
                mod.render(lock, compose, normalizers, facts)

    def test_zero_candidate_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock, compose, normalizers, facts = self.fixture(Path(temporary))
            value = json.loads(facts.read_text())
            value["candidate_commit"] = "0" * 40
            facts.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(mod.EvidenceError):
                mod.render(lock, compose, normalizers, facts)


if __name__ == "__main__":
    unittest.main()
