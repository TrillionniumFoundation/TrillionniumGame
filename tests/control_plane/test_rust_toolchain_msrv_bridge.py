"""Bound the Rust 1.99 atomic rename bridge without changing its MSRV or logic."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
REASON = 'fetch_update is the MSRV-compatible atomic API'
ATTRIBUTE = f'#[allow(deprecated, reason = "{REASON}")]'
COMMENT = '// `try_update` requires Rust 1.95; preserve the declared Rust 1.85 MSRV.'
BRIDGES = {
    'crates/trnm-token-crypto-provider/src/remote_unix.rs': (
        'fn reserve_connect_slot() -> Result<PendingConnectSlot, RemoteMacError> {',
        '0edcff0dbff0f6aba39fa382cc94bd35c6046f4d8a6bc64026558385badef23a',
    ),
    'crates/trnm-persistence-pg/src/pool_parts/cancellation.rs': (
        '        let previous = self',
        '59aa52af39e16d62e66ee5f8501a9a0d78ea6c444705fab94795be05cd5f9669',
    ),
    'crates/trnm-server/src/runtime/server.rs': (
        '        let previous = self',
        '36e7e7b67cf6bd2574b99f880dd8e7cfacbeddeb495c98a1b25d5711f95d0f45',
    ),
}


class AtomicMsrvBridgeTests(unittest.TestCase):
    def test_declared_msrv_and_build_compiler_remain_distinct(self) -> None:
        workspace = tomllib.loads((ROOT / 'Cargo.toml').read_text())
        toolchain = tomllib.loads((ROOT / 'rust-toolchain.toml').read_text())
        self.assertEqual(workspace['workspace']['package']['rust-version'], '1.85.1')
        self.assertEqual(toolchain['toolchain']['channel'], '1.99.0')

    def test_only_three_reviewed_deprecation_allowances_exist(self) -> None:
        paths = subprocess.check_output(
            ['git', 'ls-files', '-z', '*.rs'], cwd=ROOT
        ).decode().split('\0')
        observed = {}
        for relative in filter(None, paths):
            text = (ROOT / relative).read_text()
            allowances = re.findall(r'#\s*!?\[\s*allow\s*\([^]]*\bdeprecated\b[^]]*\)\]', text)
            if allowances:
                observed[relative] = allowances
        self.assertEqual(observed, {path: [ATTRIBUTE] for path in BRIDGES})

    def test_bridge_is_local_and_executable_source_is_unchanged(self) -> None:
        for relative, (next_line, original_sha256) in BRIDGES.items():
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text()
                self.assertEqual(text.count(ATTRIBUTE), 1)
                self.assertEqual(text.count(COMMENT), 1)
                self.assertIn(ATTRIBUTE + '\n' + next_line, text)
                original = ''.join(
                    line for line in text.splitlines(keepends=True)
                    if line.strip() not in {ATTRIBUTE, COMMENT}
                )
                self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), original_sha256)

    def test_strict_lint_commands_remain_required(self) -> None:
        for path in (
            '.github/workflows/trillionnium-game-merge-gate.yml',
            '.github/workflows/prospective-merge-gate.yml',
            '.github/workflows/jwt-crypto-provider-source.yml',
        ):
            text = (ROOT / path).read_text()
            self.assertIn('-- -D warnings', text)
            self.assertNotIn('-A deprecated', text)
            self.assertNotIn('continue-on-error:', text)


if __name__ == '__main__':
    unittest.main()
