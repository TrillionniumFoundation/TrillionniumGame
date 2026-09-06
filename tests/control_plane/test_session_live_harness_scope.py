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
        self.assertGreaterEqual(source.count("#[test]"), 3)
        self.assertIn("session_live_fixture_digests_are_globally_unique", source)
        self.assertIn(
            "committed_refresh_response_loss_is_idempotent_and_changed_successor_revokes",
            source,
        )
        self.assertIn(
            "concurrent_refresh_and_logout_never_surface_a_database_deadlock",
            source,
        )
        self.assertIn("fn concurrency_credentials(iteration: u8)", source)
        self.assertIn("assert_eq!(digests.len(), 35);", source)

    def test_harnesses_accept_nonempty_growth_without_weakening_fixture_checks(self) -> None:
        forbidden = (
            "test result: ok[.] 1 passed; 0 failed; 0 ignored;",
            "test \"$(db_scalar 'SELECT count(*) FROM trnm_session_families')\" = 1",
            "test \"$(query 'SELECT count(*) FROM trnm_session_families;')\" = 1",
        )
        for path in HARNESSES:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                self.assertIn("session_test_count=", source)
                # Two tests require the live database. The third is the pure
                # uniqueness guard above and must not be misreported as database
                # coverage merely by increasing this lower bound.
                self.assertIn('test "$session_test_count" -ge 2', source)
                self.assertIn("response_loss_family_hex", source)
                self.assertIn(
                    "for family_byte in 90 91 92 93 94 95 96 97 98 99 9a 9b 9c 9d 9e 9f",
                    source,
                )
                self.assertIn('test "$concurrency_logout_families" = 16', source)
                self.assertIn("printf 'concurrency_logout_families=%s\\n'", source)
                for text in forbidden:
                    self.assertNotIn(text, source)


if __name__ == "__main__":
    unittest.main()
