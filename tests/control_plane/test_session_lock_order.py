from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "crates/trnm-persistence-pg/src/session.rs"


class SessionMutationLockOrderTests(unittest.TestCase):
    def source(self) -> str:
        return SOURCE.read_text(encoding="utf-8")

    def function(self, start: str, end: str) -> str:
        text = self.source()
        left = text.index(start)
        right = text.index(end, left)
        return text[left:right]

    def test_rotation_discovers_identity_then_locks_family_before_token(self) -> None:
        body = self.function(
            "    pub fn rotate_refresh_token(",
            "    pub fn revoke_session_family(",
        )
        discovery = body.index("SELECT family_id FROM trnm_refresh_tokens")
        family_lock = body.index("FROM trnm_session_families")
        token_lock = body.index("WHERE family_id = $1 AND token_id = $2 AND token_digest = $3 \\\n                 FOR UPDATE")
        self.assertLess(discovery, family_lock)
        self.assertLess(family_lock, token_lock)
        self.assertNotIn(
            "WHERE token_id = $1 AND token_digest = $2 FOR UPDATE",
            body,
        )

    def test_rotation_revalidates_the_exact_credential_after_family_lock(self) -> None:
        body = self.function(
            "    pub fn rotate_refresh_token(",
            "    pub fn revoke_session_family(",
        )
        family_lock = body.index("FROM trnm_session_families")
        exact_locked_token = body.index(
            "WHERE family_id = $1 AND token_id = $2 AND token_digest = $3 \\\n                 FOR UPDATE"
        )
        validation = body.index("validate_refresh_token_snapshot(presented)?")
        self.assertLess(family_lock, exact_locked_token)
        self.assertLess(exact_locked_token, validation)

    def test_explicit_and_replay_revocation_keep_family_before_token(self) -> None:
        explicit = self.function(
            "    pub fn revoke_session_family(",
            "fn committed_rotation_retry_matches(",
        )
        self.assertLess(
            explicit.index("FROM trnm_session_families"),
            explicit.index("UPDATE trnm_refresh_tokens"),
        )

        replay = self.function("fn revoke_for_replay(", "fn validate_create(")
        self.assertIn("UPDATE trnm_refresh_tokens", replay)
        self.assertIn("UPDATE trnm_session_families", replay)
        # revoke_for_replay is private and only called from rotation after the
        # family lock; it must never acquire a family row lock on its own.
        self.assertNotIn("FOR UPDATE", replay)


if __name__ == "__main__":
    unittest.main()
