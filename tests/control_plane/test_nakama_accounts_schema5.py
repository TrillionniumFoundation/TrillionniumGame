from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mutable_test_copy(source: str, destination: str) -> str:
    """Only owned mutation-fixture copies gain write permission; donors stay sealed."""
    copied = shutil.copy2(source, destination)
    path = Path(copied)
    path.chmod(path.stat().st_mode | 0o200)
    return copied


class AccountsSourceFrontierTests(unittest.TestCase):
    def root_copy(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / 'migrations', root / 'migrations', copy_function=mutable_test_copy)
        return root

    def relock(self, root: Path, profile: str, revision: int, data: bytes) -> None:
        path = root / 'migrations/MIGRATION_CHAIN.lock.json'
        lock = json.loads(path.read_text())
        row = lock['profiles'][profile]['ordered_files'][revision - 1]
        (root / row['path']).write_bytes(data)
        row['git_blob_sha1'] = load_script('check-migration-lock.py').git_blob_sha1(data)
        path.write_text(json.dumps(lock))

    def test_relocked_accounts_cannot_reduce_native_fields_constraints_seed_or_scope(self) -> None:
        module = load_script('check-migration-lock.py')
        mutations = [
            (b'facebook_instant_game_id VARCHAR(128),', b''),
            (b'apple_id VARCHAR(128),', b''),
            (b'preferences JSONB NOT NULL DEFAULT', b'preferences JSONB DEFAULT'),
            (b"push_token_huawei VARCHAR(512) NOT NULL DEFAULT ''", b'push_token_huawei VARCHAR(512) NOT NULL'),
            (b'CONSTRAINT users_edge_count_check CHECK (edge_count >= 0)', b'CONSTRAINT users_edge_count_check CHECK (edge_count > 0)'),
            (b'ON DELETE CASCADE', b'ON DELETE NO ACTION'),
            (b'UNIQUE (user_id, id)', b'UNIQUE (user_id)'),
            (b"VALUES ('00000000-0000-0000-0000-000000000000', '')", b"VALUES ('00000000-0000-0000-0000-000000000001', '')"),
            (b'CREATE TABLE public.users', b'CREATE TABLE users'),
            (b'-- trnm:action nakama_users', b'-- trnm:action nakama_user_device'),
            (b'schema_version < 5', b'schema_version < 4'),
            (b'COMMIT;', b'DROP TABLE public.trnm_storage_objects;\nCOMMIT;'),
        ]
        for profile in ('postgresql', 'cockroachdb'):
            for old, replacement in mutations:
                root = self.root_copy()
                lock = module.load_lock(root)
                path = root / lock['profiles'][profile]['ordered_files'][4]['path']
                data = path.read_bytes()
                self.assertIn(old, data)
                self.relock(root, profile, 5, data.replace(old, replacement))
                with self.subTest(profile=profile, mutation=old), self.assertRaises(module.ValidationError):
                    module.validate(root)

    def test_default_profile_cannot_promote_and_relocking_cannot_rewrite_revision4(self) -> None:
        module = load_script('check-migration-lock.py')
        root = self.root_copy()
        path = root / 'migrations/MIGRATION_CHAIN.lock.json'
        lock = json.loads(path.read_text())
        lock['default_runtime_schema_version'] = 5
        path.write_text(json.dumps(lock))
        with self.assertRaises(module.ValidationError):
            module.validate(root)
        for profile in ('postgresql', 'cockroachdb'):
            root = self.root_copy()
            path = root / module.load_lock(root)['profiles'][profile]['ordered_files'][3]['path']
            self.relock(root, profile, 4, path.read_bytes() + b'-- rewritten historical publisher\n')
            with self.subTest(profile=profile), self.assertRaises(module.ValidationError):
                module.validate(root)

    def test_full_frontier_and_storage_identity_have_distinct_framed_digests(self) -> None:
        module = load_script('check-migration-lock.py')
        lock = module.load_lock()
        result = module.validate()
        for profile in ('postgresql', 'cockroachdb'):
            row = result['profiles'][profile]
            entries = lock['profiles'][profile]['ordered_files']
            framed = b''.join(index.to_bytes(8, 'big') + item['path'].encode() + b'\0' + bytes.fromhex(item['git_blob_sha1']) for index, item in enumerate(entries[:4]))
            self.assertEqual(hashlib.sha256(framed).hexdigest(), row['revision_prefixes']['4']['chain_sha256'])
            self.assertNotEqual(row['chain_sha256'], row['revision_prefixes']['4']['chain_sha256'])
            self.assertEqual(row['revision_prefixes']['4']['file_count'], 4)

    def test_capture_enablement_io_before_gate_and_claim_drift_are_rejected(self) -> None:
        module = load_script('check-trnm-server.py')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in ('crates/trnm-persistence-pg/src', 'migrations', 'docs/status'):
            shutil.copytree(ROOT / relative, root / relative, copy_function=mutable_test_copy)
        module.ROOT = root
        module.PERSISTENCE_ROOT = root / 'crates/trnm-persistence-pg/src'
        module.validate_accounts_schema_source()
        gate = module.PERSISTENCE_ROOT / 'schema_parts/account_catalog.rs'
        original = gate.read_text()
        gate.write_text(original.replace('ACCOUNT_CATALOG_CAPTURE_READY: bool = false', 'ACCOUNT_CATALOG_CAPTURE_READY: bool = true'))
        with self.assertRaises(SystemExit):
            module.validate_accounts_schema_source()
        gate.write_text(original)
        migrate = module.PERSISTENCE_ROOT / 'schema_parts/migrate.rs'
        original = migrate.read_text()
        migrate.write_text(original.replace('        require_account_catalog_capture(target)?;', '        let profile = self.profile;\n        require_account_catalog_capture(target)?;', 1))
        with self.assertRaises(SystemExit):
            module.validate_accounts_schema_source()
        migrate.write_text(original)
        status = root / 'docs/status/TRNM_SERVER_STATUS.json'
        original = status.read_text()
        data = json.loads(original)
        data['nakama_accounts_schema5_source_candidate']['schema5_storage_transfer_admitted'] = True
        status.write_text(json.dumps(data))
        with self.assertRaises(SystemExit):
            module.validate_accounts_schema_source()
        status.write_text(original)
        module.validate_accounts_schema_source()


class LegacyAuthSourceCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script('check-trnm-server.py')
        self.sources = {
            path: (ROOT / path).read_text()
            for path in self.module.LEGACY_AUTH_REQUIRED_FILES
        }
        self.sources[Path('crates/trnm-server/src/runtime/app.rs')] = (
            ROOT / 'crates/trnm-server/src/runtime/app.rs'
        ).read_text()
        self.status = json.loads((ROOT / 'docs/status/TRNM_SERVER_STATUS.json').read_text())
        self.lock = json.loads((ROOT / 'contracts/session/nakama-v340-token-source-lock.json').read_text())

    def validate(self) -> None:
        self.module.validate_legacy_auth_source(self.sources, self.status, self.lock)

    def mutate_source(self, path: str, old: str, new: str) -> None:
        key = Path(path)
        original = self.sources[key]
        self.assertIn(old, original)
        self.sources[key] = original.replace(old, new, 1)
        try:
            with self.assertRaises(SystemExit):
                self.validate()
        finally:
            self.sources[key] = original

    def test_current_source_composition_retains_closed_runtime_and_commit_truth(self) -> None:
        self.validate()
        state = self.status['nakama_legacy_auth_source_candidate']
        self.assertEqual(state['device_error_variants'], ['Unconfirmed','CommittedCreation','UnconfirmedCleanup'])
        self.assertIs(state['unconfirmed_means_no_commit'], False)
        self.assertIs(state['caller_replay_permitted'], False)
        self.assertIs(state['caller_compensation_permitted'], False)
        self.assertEqual(len(self.lock['verified_files']), 9)

    def test_Ban_clock_and_postcommit_confirmation_mutations_fail_closed(self) -> None:
        path = 'crates/trnm-server/src/runtime/legacy_auth.rs'
        self.mutate_source(path, 'let cutoff = now()?;\n        if users.len()',
                           'if users.len()')
        self.mutate_source(path, 'if account.created {\n                LegacyDeviceAuthError::CommittedCreation',
                           'if true {\n                LegacyDeviceAuthError::CommittedCreation')
        self.mutate_source(path, 'return Err(LegacyDeviceAuthError::UnconfirmedCleanup {',
                           'return Err(LegacyDeviceAuthError::CommittedCreation {')
        self.mutate_source(path, 'matches!(self, Self::CommittedCreation(_))', 'true')
        self.mutate_source(path, 'pub const fn retry_permitted(&self) -> bool {\n        false',
                           'pub const fn retry_permitted(&self) -> bool {\n        true')

    def test_mac_bytes_key_purpose_and_blacklist_profiles_cannot_drift(self) -> None:
        self.mutate_source('crates/trnm-token-jwt-adapter/src/nakama_legacy_verify.rs',
                           '&token[..second],', '&signature,')
        self.mutate_source('crates/trnm-token-crypto-provider/src/nakama_legacy.rs',
                           '(KeyDomain::RefreshToken, REFRESH_HANDLE, None)',
                           '(KeyDomain::AccessToken, REFRESH_HANDLE, None)')
        self.mutate_source('crates/trnm-session-core/src/nakama_legacy_blacklist.rs',
                           'wrapping_add(1)', 'saturating_add(1)')
        self.mutate_source('crates/trnm-token-jwt-adapter/src/nakama_legacy_header.rs',
                           'HS256', 'hs256')

    def test_deferred_UUID_lookup_order_and_native_error_diagnostic_mapping_are_bound(self) -> None:
        self.mutate_source('crates/trnm-persistence-pg/src/nakama_account.rs',
                           'let generated = new_user_id().map_err',
                           'let generated = new_user_id().and_then(|_| new_user_id()).map_err')
        self.mutate_source('crates/trnm-server/src/runtime/legacy_repository.rs',
                           'NakamaAccountError::Internal(_) => LegacyRepositoryError::Internal',
                           'NakamaAccountError::Internal(_) => LegacyRepositoryError::DataLoss')
        self.mutate_source('crates/trnm-server/src/runtime/legacy_repository.rs',
                           'committed_cleanup_failure: outcome.committed_cleanup_failure.map(map_cleanup)',
                           'committed_cleanup_failure: None')
        self.mutate_source('crates/trnm-server/src/runtime/legacy_repository.rs',
                           '.authenticate_nakama_device_with_id_source(request, || {',
                           '.authenticate_nakama_device_with_id_source(request, || { loop {}')

    def test_missing_registration_source_and_unimplemented_transport_are_rejected(self) -> None:
        for path, marker in (
            ('crates/trnm-server/src/runtime/mod.rs','mod legacy_auth;'),
            ('crates/trnm-token-jwt-adapter/src/lib.rs','mod nakama_legacy_verify;'),
            ('crates/trnm-session-core/src/lib.rs','mod nakama_legacy_blacklist;'),
        ):
            with self.subTest(path=path):
                self.mutate_source(path, marker, '// removed')
        path = Path('crates/trnm-server/src/runtime/legacy_uuid.rs')
        original = self.sources.pop(path)
        with self.assertRaises(SystemExit):
            self.validate()
        self.sources[path] = original
        for path in ('crates/trnm-server/src/runtime/app.rs','crates/trnm-server/src/runtime/config.rs'):
            original = self.sources[Path(path)]
            self.sources[Path(path)] += '\nconst ROUTE: &str = "/v2/session/refresh";\n'
            with self.subTest(path=path), self.assertRaises(SystemExit):
                self.validate()
            self.sources[Path(path)] = original

    def test_false_claims_integer_bounds_and_observation_scope_are_not_coerced(self) -> None:
        state = self.status['nakama_legacy_auth_source_candidate']
        for key in state['acceptance_flags']:
            state['acceptance_flags'][key] = True
            with self.subTest(key=key), self.assertRaises(SystemExit):
                self.validate()
            state['acceptance_flags'][key] = False
        for key in ('default_runtime_schema_version','default_storage_writer_epoch',
                    'default_runtime_table_count','supported_source_schema_version','key_budget_bytes'):
            original = state[key]
            for replacement in (True, str(original), original+1):
                state[key] = replacement
                with self.subTest(key=key, value=replacement), self.assertRaises(SystemExit):
                    self.validate()
            state[key] = original
        for scope, key, value in (
            ('relation_observation_scope','qualifies_account_migration_or_device_transaction',0),
            ('relation_observation_scope','cockroachdb_negative_cases',False),
            ('source_fetch_policy','checks_before_IO_and_after_delivery',1),
            ('source_fetch_policy','global_nonreturning_IO_bound_qualified',0),
        ):
            original = state[scope][key]
            state[scope][key] = value
            with self.subTest(scope=scope, key=key), self.assertRaises(SystemExit):
                self.validate()
            state[scope][key] = original
        state['relation_observation_scope']['cockroachdb_negative_cases'] = 5
        with self.assertRaises(SystemExit):
            self.validate()
        state['relation_observation_scope']['cockroachdb_negative_cases'] = 0
        state['source_fetch_policy']['global_nonreturning_IO_bound_qualified'] = True
        with self.assertRaises(SystemExit):
            self.validate()

    def test_official_file_identity_and_historical_claim_scope_are_closed(self) -> None:
        for path in self.lock['verified_files']:
            original = path['sha256']
            path['sha256'] = '0'*64
            with self.subTest(source=path['path']), self.assertRaises(SystemExit):
                self.validate()
            path['sha256'] = original
        for row in self.lock['sources']:
            original = row['blob']
            row['blob'] = '0'*40
            with self.subTest(source=row['path']), self.assertRaises(SystemExit):
                self.validate()
            row['blob'] = original
        region = self.lock['implementation_attribution']['regions'][0]
        original = region['region_sha256']
        region['region_sha256'] = '0'*64
        with self.assertRaises(SystemExit):
            self.validate()
        region['region_sha256'] = original
        self.lock['implementation_attribution']['Go_code_compiled_into_Rust_target'] = True
        with self.assertRaises(SystemExit):
            self.validate()


if __name__ == '__main__':
    unittest.main()
