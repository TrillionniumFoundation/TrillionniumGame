from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SecretZeroizationContractTests(unittest.TestCase):
    def test_software_provider_uses_pinned_zeroize(self) -> None:
        source = (ROOT / "crates/trnm-token-crypto-provider/src/software.rs").read_text(encoding="utf-8")
        manifest = (ROOT / "crates/trnm-token-crypto-provider/Cargo.toml").read_text(encoding="utf-8")
        policy = (ROOT / "scripts/check-rust-foundation.py").read_text(encoding="utf-8")
        self.assertIn("use zeroize::Zeroize;", source)
        self.assertIn("self.0.zeroize();", source)
        self.assertNotIn("self.0.fill(0);", source)
        self.assertIn('zeroize = "=1.8.2"', manifest)
        self.assertIn("'zeroize': '=1.8.2'", policy)

    def test_security_document_does_not_overclaim_memory_custody(self) -> None:
        security = (ROOT / "docs/SECURITY_AND_PRIVACY.md").read_text(encoding="utf-8")
        self.assertIn("does not imply register clearing, memory locking, core-dump protection", security)
        self.assertIn("production KMS/HSM custody", security)


if __name__ == "__main__":
    unittest.main()
