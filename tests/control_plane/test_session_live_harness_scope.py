#!/usr/bin/env python3
"""Prevent session live harnesses from assuming one test or global fixture totals."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESSES = (
    ROOT / "scripts/check-pg-server-response-loss.sh",
    ROOT / "scripts/ci-trnm-server-live.sh",
)
SESSION_TEST = ROOT / "crates/trnm-persistence-pg/tests/session_response_loss.rs"


class SessionLiveHarnessContractTests(unittest.TestCase):
    def test_live_target_contains_unique_fixtures_and_both_database_contracts(self) -> None:
        source = SESSION_TEST.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("#[test]"), 8)
        self.assertIn("session_live_fixture_digests_are_globally_unique", source)
        self.assertIn(
            "committed_refresh_response_loss_is_idempotent_and_changed_successor_revokes",
            source,
        )
        self.assertIn(
            "concurrent_refresh_and_logout_never_surface_a_database_deadlock",
            source,
        )
        for name in (
            "concurrent_exact_committed_retries_return_same_durable_result",
            "exact_retry_then_successor_rotation_is_one_linear_history",
            "successor_rotation_then_old_retry_revokes_one_linear_history",
            "exact_retry_then_changed_replay_revokes_one_linear_history",
            "changed_replay_then_exact_retry_is_one_linear_history",
        ):
            self.assertIn(name, source)
        self.assertIn("SessionMutationPoint::BeforeCommit", source)
        self.assertIn("SessionMutationPoint::CredentialResolved", source)
        self.assertIn("for attempt in 0..4", source)
        self.assertIn('error.reason() == "database_serialization_failure"', source)
        self.assertIn("fn concurrency_credentials(iteration: u8)", source)
        self.assertIn("assert_eq!(digests.len(), 49);", source)

    def test_harnesses_accept_nonempty_growth_without_weakening_fixture_checks(self) -> None:
        forbidden = (
            "test result: ok[.] 1 passed; 0 failed; 0 ignored;",
            "test \"$(db_scalar 'SELECT count(*) FROM trnm_session_families')\" = 1",
            "test \"$(query 'SELECT count(*) FROM trnm_session_families;')\" = 1",
        )
        for path in HARNESSES:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                self.assertIn("CARGO_TERM_COLOR=never", source)
                self.assertIn("session_test_count=", source)
                # Seven tests require a live database and one is a pure fixture
                # uniqueness guard. The harness must execute the feature-gated
                # deterministic coordination paths, not merely compile them.
                self.assertIn("--features session-test-hooks", source)
                self.assertIn("--test-threads=1", source)
                self.assertIn('test "$session_test_count" -ge 8', source)
                self.assertIn("response_loss_family_hex", source)
                self.assertIn(
                    "for family_byte in 90 91 92 93 94 95 96 97 98 99 9a 9b 9c 9d 9e 9f",
                    source,
                )
                self.assertIn('test "$concurrency_logout_families" = 16', source)
                self.assertIn("printf 'concurrency_logout_families=%s\\n'", source)
                self.assertIn("assert_interleaving_family 40 1 44 none 2 1", source)
                self.assertIn("assert_interleaving_family 46 2 4c none 3 1", source)
                self.assertIn("assert_interleaving_family 4e 2 none 2 3 0", source)
                self.assertIn("assert_interleaving_family 56 1 none 2 2 0", source)
                self.assertIn("assert_interleaving_family 5e 1 none 2 2 0", source)
                self.assertIn("deterministic_interleaving_families=%s", source)
                for text in forbidden:
                    self.assertNotIn(text, source)


if __name__ == "__main__":
    unittest.main()
