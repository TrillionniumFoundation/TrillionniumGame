"""Source wiring only; native TLS/SQL execution is independently mandatory."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[2]

class DatabaseQualificationPaths(unittest.TestCase):
    def text(self, path):
        return (ROOT / path).read_text(encoding='utf-8')

    def test_tls_negative_requires_typed_witness_and_bracket(self):
        source = self.text('crates/trnm-persistence-pg/src/bin/trnm-pg-tls-rotation-probe.rs')
        self.assertNotIn('Err(_) => Ok(())', source)
        self.assertIn('bracket(', source)
        self.assertIn('Observation::TrustChainRejected', source)
        self.assertIn('healthy.0', source)
        self.assertIn('unexpected_tls_acceptance', source)

    def test_witness_checks_x509_result_without_error_text_matching(self):
        source = self.text('crates/trnm-persistence-pg/src/bin/tls_probe_witness/mod.rs')
        self.assertIn('verify_result().as_raw()', source)
        self.assertIn('verify_hostname(true)', source)
        self.assertIn('SslVerifyMode::PEER', source)
        self.assertIn('struct DeadlineStream', source)
        self.assertNotIn('danger_accept_invalid', source)

    def test_live_entry_executes_real_atomicity_phase(self):
        entry = self.text('crates/trnm-persistence-pg/src/bin/trnm_server/retry_live_tests.rs')
        implementation = self.text('crates/trnm-persistence-pg/src/bin/trnm_server/retry_atomicity.rs')
        self.assertIn('atomicity::prove(&database_url);', entry)
        self.assertIn('self.inner.commit_command_with_budget(request, budget)', implementation)
        self.assertIn('inject_retry_errors_on_commit_enabled = true', implementation)
        self.assertIn('inject_retry_errors_on_commit_enabled = false', implementation)
        self.assertNotIn('CommitReceipt {', implementation)
        self.assertIn('CommitOutcome::Duplicate(applied)', implementation)
        self.assertIn('retry_exhausted', implementation)

    def test_live_jobs_require_new_assertions(self):
        tls = self.text('.github/workflows/pg-tls-rotation.yml')
        crdb = self.text('.github/workflows/cockroach-serialization-retry.yml')
        for assertion in ('all_negative_cases_have_fresh_healthy_controls',
                          'unrelated_failures_do_not_count_as_certificate_rejection'):
            self.assertIn("grep -qx 'assertion=" + assertion + "'", tls)
        for assertion in ('failed_commit_left_no_receipt_event_or_outbox',
                          'fresh_pool_replayed_exact_durable_receipt',
                          'retry_exhaustion_left_no_partial_durable_effect'):
            self.assertIn("grep -qx 'assertion=" + assertion + "'", crdb)

    def test_migration_and_added_modules_are_in_main_trigger_paths(self):
        tls = self.text('.github/workflows/pg-tls-rotation.yml')
        crdb = self.text('.github/workflows/cockroach-serialization-retry.yml')
        self.assertIn("'crates/trnm-persistence-pg/src/bin/tls_probe_witness/**'", tls)
        self.assertIn("'crates/trnm-persistence-pg/src/bin/trnm_server/retry_atomicity.rs'", crdb)
        self.assertIn("'migrations/cockroachdb/**'", crdb)

if __name__ == '__main__':
    unittest.main()
