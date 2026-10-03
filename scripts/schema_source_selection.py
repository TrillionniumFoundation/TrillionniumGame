"""Pure source-frontier/execution-prefix proof issuer; no native execution.
The complete unchanged migration validator runs before selected-prefix derivation.
An issued token is source-byte proof only, never exact Git HEAD or DB readiness.
"""
from __future__ import annotations

from enum import Enum
import hashlib
import types
import json
import os
import stat
from pathlib import Path
from typing import Any
import weakref

__all__ = ('SelectionError', 'SchemaTarget', 'verify_current_source_selection',
           'verify_historical_source4_selection', 'current_selection_document',
           'historical_selection_document', 'verify_current_selection_envelope',
           'verify_historical_selection_envelope')

CURRENT_SCHEMA = 'trillionnium.authoritative-schema-source-selection.v1'
HISTORICAL_SCHEMA = 'trillionnium.historical-schema4-source-selection.v1'
ALGORITHM = 'ordered-path-git-blob-sha256.v1'
PROFILES = ('postgresql', 'cockroachdb')
LOCK_PATH = 'migrations/MIGRATION_CHAIN.lock.json'
AUTHORITY_PATH = 'docs/development/SCHEMA_AUTHORITY.json'
VALIDATOR_PATH = 'scripts/check-migration-lock.py'
CURRENT_VALIDATOR_SHA256 = '1f9a589713c5b4e8f18b9dc47e635aed965c16816b2dbec88ae891ef88912a90'
HISTORICAL_VALIDATOR_SHA256 = '456d68d270435c14c0b98af2ccbf43eb6bdca60f515dcc011b05b359c99d57be'
HISTORICAL_AUTHORITY_SHA256 = '23f1333232e7a386e98b704802404daca08afa2582d9a5b9e47898f4c2f9e480'
ORIGINAL4_DIGESTS = types.MappingProxyType({
    'postgresql': '9b2dc61f2f8ecb87d152a043bf2648495df5bacd3b17d38f4cc5596418fdb391',
    'cockroachdb': 'f3c3340c7c1112553103a44060bf2350c244e061002cf8b8f7d7423e836bf261',
})
_EXPECTED_POLICY_BYTES = b'{"claims":{"accepted":false,"account_transfer_admitted":false,"compatibility_credit":false,"production_ready":false,"runtime_execution_verified":false},"default_target":"StorageV4","full_source_frontier_schema_version":5,"full_source_validation_required":true,"historical_source4":{"current_head_eligible":false,"envelope":"trillionnium.historical-schema4-source-selection.v1","must_use_genuine_historical_validator_and_source":true},"profiles_are_separate":true,"schema":"trillionnium.schema-execution-selection-policy.v1","selected_prefix_must_be_initial_contiguous":true,"targets":{"NakamaAccountsV5":{"blocked_before_io":"schema5_native_catalog_capture_pending","executed_migration_file_count":5,"selected_schema_version":5,"source_binding_allowed":false,"storage_writer_epoch":4,"table_count":14},"StorageV4":{"executed_migration_file_count":4,"selected_schema_version":4,"source_binding_allowed":true,"storage_writer_epoch":4,"table_count":12}}}'
AUTHORITY_TOP_KEYS = frozenset(('schema', 'project_id', 'plan_version', 'effective_date', 'migration_lock', 'authority', 'non_authoritative', 'adapter_abi', 'change_control', 'gap', 'claim_boundary'))
AUTHORITY_KEYS = frozenset(('migration_root', 'profiles', 'metadata_table', 'schema_version', 'identity_rule', 'postgresql_connection_fault_source_candidate', 'postgresql_pitr_source_candidate', 'postgresql_primary_failover_source_candidate', 'postgresql_recovery_barrier_source_candidate', 'postgresql_semantic_recovery_source_candidate', 'cockroachdb_node_failover_source_candidate', 'cockroachdb_semantic_recovery_source_candidate', 'storage_timestamp_upgrade_source_candidate', 'storage_jsonb_upgrade_source_candidate', 'storage_source_import_upgrade_source_candidate', 'default_runtime_schema_version', 'latest_supported_schema_version', 'accounts_v5_source_candidate', 'source_execution_selection_policy'))
_EXPECTED_ACCOUNT_CANDIDATE_BYTES = b'{"accepted":false,"account_transfer_implemented":false,"accounts_v5_backups_restores_qualified":false,"blocked_before_ddl":"schema5_native_catalog_capture_pending","boundary":"Default serve/migrate/import remains strict schema4 prefix. Schema5 source frontier is explicit and cannot borrow epoch4, storage packet or old catalog observations.","default_runtime_promotion_allowed":false,"full_current_two_table_fields_defaults_constraints":true,"migration":"0005_nakama_accounts_up.sql","native_activation_allowed":false,"native_catalog_observations_bound":false,"repository_service_routes_implemented":false,"storage_writer_epoch":4,"tables":["users","user_device"],"target":"NakamaAccountsV5"}'
_EXPECTED_MIGRATION_AUTHORITY_BYTES = b'{"identity":"ordered-path-git-blob-sha256.v1: each zero-based u64 big-endian position, UTF-8 repository path, NUL, and 20-byte Git blob SHA-1; hash the concatenation with SHA-256","path":"migrations/MIGRATION_CHAIN.lock.json","runtime_execution_credit":false,"validator":"python3 scripts/check-migration-lock.py"}'
MIGRATION_AUTHORITY_KEYS = frozenset(('path', 'validator', 'identity', 'runtime_execution_credit'))
ADAPTER_ABI_KEYS = frozenset(('crate', 'required_tables', 'required_semantics', 'forbidden_implicit_behavior', 'source_modules', 'source_coverage_candidate', 'storage_client_list_projection_source_candidate', 'explicit_profile_exceptions'))
_EXPECTED_CLAIM_BOUNDARY_BYTES = b'{"adapter_table_surface_source_candidate":true,"cockroachdb_node_failover_source_candidate":true,"cockroachdb_semantic_restore_source_candidate":true,"database_durable":false,"ha_proven":false,"migration_compatible":false,"migration_source_identity_locked":true,"pitr_proven":false,"postgresql_connection_fault_source_candidate":true,"postgresql_pitr_source_candidate":true,"postgresql_primary_failover_source_candidate":true,"postgresql_recovery_barrier_source_candidate":true,"postgresql_semantic_restore_source_candidate":true,"production_ready":false,"rollback_proven":false,"sg4_complete":false,"single_node_foundation_evidence_exists":true}'
CURRENT_LOCK_KEYS = frozenset(('schema','project_id','schema_version','generated_from_base','profiles','rules','claim_boundary','default_runtime_schema_version','accounts_v5_activation'))
HISTORICAL_LOCK_KEYS = frozenset(('schema','project_id','schema_version','generated_from_base','profiles','rules','claim_boundary'))
RULE_KEYS = frozenset(('ordered_files_are_complete','unlisted_sql_is_failure','duplicate_path_is_failure','profile_conclusions_are_separate','semantic_change_requires_adr_and_lock_update','drop_based_production_rollback_allowed'))
MAX_DOCUMENT_BYTES = 128 * 1024
MAX_SOURCE_BYTES = 512 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
REQUIRED_TABLES = ('trnm_schema_metadata', 'trnm_entity_heads', 'trnm_command_receipts', 'trnm_events', 'trnm_outbox', 'trnm_command_outbox', 'trnm_authority_leases', 'trnm_session_families', 'trnm_refresh_tokens', 'trnm_storage_objects', 'trnm_storage_import_jobs', 'trnm_storage_import_pages')

class SelectionError(RuntimeError):
    """A source proof is incomplete, forged, stale or unsupported."""

class SchemaTarget(Enum):
    StorageV4 = 'StorageV4'
    NakamaAccountsV5 = 'NakamaAccountsV5'


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SelectionError(reason)


def _strict_json(raw: bytes, label: str) -> Any:
    _require(type(raw) is bytes and len(raw) <= MAX_DOCUMENT_BYTES, label + '_byte_budget')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, label + '_duplicate_key')
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_JSON_number')))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise SelectionError(label + '_invalid_JSON') from error


def _keys(value: Any, expected: frozenset[str], label: str) -> None:
    _require(type(value) is dict and frozenset(value) == expected, label + '_closed_fields')


def _same(left: Any, right: Any) -> bool:
    # bool is a subtype of int; ordinary equality alone must never validate it.
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return left.keys() == right.keys() and all(_same(left[k], v) for k,v in right.items())
    if type(right) is list:
        return len(left) == len(right) and all(_same(a,b) for a,b in zip(left,right))
    return left == right


def _regular(root: Path, relative: str, maximum: int) -> bytes:
    # Anchor each relative component through a directory descriptor. Do not
    # follow symlinks or block on a replaced FIFO/device, and never read all
    # bytes based only on an earlier stat that a concurrent writer can change.
    parts = Path(relative).parts
    _require(type(relative) is str and parts and not Path(relative).is_absolute()
             and all(part not in ('', '.', '..') for part in parts), 'source_path_escape')
    _require(all(type(getattr(os, name, None)) is int for name in
                 ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')), 'source_safe_read_platform_unavailable')
    directory_fd = file_fd = None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        directory_fd = os.open(root, directory_flags)
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        metadata = os.fstat(file_fd)
        _require(stat.S_ISREG(metadata.st_mode) and 0 <= metadata.st_size <= maximum,
                 'source_not_regular_or_budget')
        with os.fdopen(file_fd, 'rb') as stream:
            file_fd = None  # Binary stream now owns the descriptor.
            data = stream.read(maximum + 1)
        _require(type(data) is bytes and len(data) <= maximum, 'source_read_budget')
        return data
    except OSError as error:
        raise SelectionError('source_open_regular_file_failed') from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _blob(data: bytes) -> str:
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_validator(source_root: Path, historical: bool):
    expected = HISTORICAL_VALIDATOR_SHA256 if historical else CURRENT_VALIDATOR_SHA256
    path = source_root / VALIDATOR_PATH
    raw = _regular(source_root, VALIDATOR_PATH, MAX_SOURCE_BYTES)
    _require(_sha(raw) == expected, 'genuine_original_validator_required')
    # Compile exactly the already-hash-verified bytes. A path-loader second
    # read would permit a validator replacement between hash and execution.
    # The reviewed original module's main is guarded and it has no native or
    # process/network calls; its source and validator functions stay unchanged.
    module = types.ModuleType('_source_selection_original_validator')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module, raw


def _snapshot(root: Path) -> tuple[dict, bytes, dict[str,bytes], dict[str,list[str]]]:
    lock_bytes = _regular(root, LOCK_PATH, MAX_DOCUMENT_BYTES)
    lock = _strict_json(lock_bytes, 'migration_lock')
    _require(type(lock) is dict, 'migration_lock_object_required')
    sources = {}
    inventory = {}
    total = len(lock_bytes)
    for profile in PROFILES:
        # Path.iterdir in CPython3.12 materializes os.listdir before yielding.
        # A real fd-anchored scandir iterator must enforce the directory budget
        # before reading any SQL body. Never use a listdir/iterdir fallback.
        descriptors = []
        paths = []
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            descriptors.append(os.open(root, flags))
            descriptors.append(os.open('migrations', flags, dir_fd=descriptors[-1]))
            descriptors.append(os.open(profile, flags, dir_fd=descriptors[-1]))
            with os.scandir(descriptors[-1]) as entries:
                for index, entry in enumerate(entries):
                    _require(index < 16, 'profile_inventory_budget')
                    _require(not entry.is_symlink() and not entry.is_dir(follow_symlinks=False),
                             'profile_indirect_or_nested_source')
                    if entry.name.endswith('.sql'):
                        paths.append('migrations/' + profile + '/' + entry.name)
        except OSError as error:
            raise SelectionError('profile_inventory_open_failed') from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        for relative in paths:
            data = _regular(root, relative, MAX_SOURCE_BYTES)
            total += len(data)
            _require(total <= MAX_SNAPSHOT_BYTES, 'source_snapshot_budget')
            sources[relative] = data
        inventory[profile] = sorted(paths)
    return lock, lock_bytes, sources, inventory


def _validate_full_source(source_root: Path, historical: bool):
    validator, validator_bytes = _load_validator(source_root, historical)
    lock, lock_bytes, sources, inventory = _snapshot(source_root)
    try:
        # Exact original checker, complete real lock and complete BOTH-profile
        # byte snapshot. The read callback is privately bound; callers cannot
        # pass a shortened report, mutated chains map or synthetic read function.
        report = validator.validate_source_document(lock, sources.__getitem__, inventory)
    except (validator.ValidationError, KeyError, ValueError, TypeError, OSError, AttributeError, IndexError, UnicodeError) as error:
        raise SelectionError('full_source_validation_failed') from error
    # Prefix selection has not happened yet. Harden the full lock object shape
    # without truncating the original validator's source denominator.
    _keys(lock, HISTORICAL_LOCK_KEYS if historical else CURRENT_LOCK_KEYS, 'migration_lock')
    _keys(lock['rules'], RULE_KEYS, 'migration_rules')
    _require(all(lock['rules'][key] is (key != 'drop_based_production_rollback_allowed')
                 for key in RULE_KEYS), 'migration_rule_type_or_value')
    _require(type(lock['claim_boundary']) is str and lock['claim_boundary'] ==
             'The lock proves source identity only. It does not prove migration execution, data compatibility, durability, backup, PITR or HA.',
             'migration_lock_claim_boundary_changed')
    for profile in PROFILES:
        _keys(lock['profiles'][profile], frozenset(('directory','ordered_files')), 'migration_profile')
        for row in lock['profiles'][profile]['ordered_files']:
            _keys(row, frozenset(('path','git_blob_sha1')), 'migration_file')
    expected = 4 if historical else 5
    _require(type(lock['schema_version']) is int and lock['schema_version'] == expected,
             'source_frontier_version_type_or_value')
    if not historical:
        _require(type(lock['default_runtime_schema_version']) is int and lock['default_runtime_schema_version'] == 4,
                 'default_runtime_version_type_or_value')
    if not historical:
        _require(lock['accounts_v5_activation'] == 'typed opt-in source candidate; native catalog bindings required before any schema5 DDL', 'source_accounts_activation_changed')
    _require(set(report['profiles']) == set(PROFILES), 'both_source_profiles_required')
    for profile in PROFILES:
        _require(type(report['profiles'][profile]['file_count']) is int and report['profiles'][profile]['file_count'] == expected,
                 'full_source_file_count_type_or_value')
    return validator, validator_bytes, lock, lock_bytes, sources, report


def _validate_authority(source_root: Path, historical: bool) -> bytes:
    raw = _regular(source_root, AUTHORITY_PATH, MAX_DOCUMENT_BYTES)
    authority = _strict_json(raw, 'schema_authority')
    if historical:
        _require(_sha(raw) == HISTORICAL_AUTHORITY_SHA256, 'genuine_historical_authority_required')
    else:
        _keys(authority, AUTHORITY_TOP_KEYS, 'authority')
        _require(authority['schema'] == 'trillionnium.schema-authority.v1'
                 and authority['project_id'] == 'trillionnium-game', 'authority_envelope_or_project_changed')
        _keys(authority['authority'], AUTHORITY_KEYS, 'authority_section')
        _require(_same(authority['authority']['source_execution_selection_policy'], _strict_json(_EXPECTED_POLICY_BYTES, 'reviewed_authority_constant')),
                 'source_selection_policy_type_shape_or_value')
        for key, expected in [('schema_version',4),('default_runtime_schema_version',4),('latest_supported_schema_version',5)]:
            _require(type(authority['authority'][key]) is int and authority['authority'][key] == expected,
                     'authority_version_type_or_value')
        _keys(authority['migration_lock'], MIGRATION_AUTHORITY_KEYS, 'migration_authority')
        _require(_same(authority['migration_lock'], _strict_json(_EXPECTED_MIGRATION_AUTHORITY_BYTES, 'reviewed_authority_constant')), 'migration_authority_changed')
        _require(authority['authority']['migration_root'] == 'migrations'
                 and authority['authority']['metadata_table'] == 'trnm_schema_metadata'
                 and _same(authority['authority']['profiles'], [{'id': 'postgresql', 'path': 'migrations/postgresql', 'runtime_adapter': 'crates/trnm-persistence-pg', 'status': 'production-authority-candidate', 'evidence_required': ['fresh-apply', 'catalog-introspection', 'negative-constraints', 'transaction-faults', 'backup-restore', 'pitr']}, {'id': 'cockroachdb', 'path': 'migrations/cockroachdb', 'runtime_adapter': 'crates/trnm-persistence-pg', 'status': 'production-authority-candidate', 'evidence_required': ['fresh-apply', 'catalog-introspection', 'negative-constraints', 'transaction-restarts', 'leaseholder-failover', 'backup-restore']}]), 'sole_authority_identity_changed')
        _keys(authority['adapter_abi'], ADAPTER_ABI_KEYS, 'adapter_abi')
        _require(_same(authority['claim_boundary'], _strict_json(_EXPECTED_CLAIM_BOUNDARY_BYTES, 'reviewed_authority_constant')), 'authority_claim_boundary_changed')
        account = authority['authority']['accounts_v5_source_candidate']
        _require(_same(account, _strict_json(_EXPECTED_ACCOUNT_CANDIDATE_BYTES, 'reviewed_authority_constant')), 'accounts_candidate_type_shape_or_value')
        for key in ('native_catalog_observations_bound','native_activation_allowed','default_runtime_promotion_allowed',
                    'repository_service_routes_implemented','account_transfer_implemented','accounts_v5_backups_restores_qualified','accepted'):
            _require(account.get(key) is False, 'accounts_native_or_transfer_credit_forbidden')
        _require(account.get('blocked_before_ddl') == 'schema5_native_catalog_capture_pending', 'accounts_pending_gate_changed')
    _require(_same(authority['adapter_abi']['required_tables'], list(REQUIRED_TABLES)), 'original12_table_ABI_changed')
    _require(authority['migration_lock']['path'] == LOCK_PATH and authority['migration_lock']['runtime_execution_credit'] is False,
             'sole_chain_or_source_credit_changed')
    return raw


def _proof(source_root: Path, profile: str, historical: bool) -> dict:
    validator, validator_bytes, lock, lock_bytes, sources, full_report = _validate_full_source(source_root, historical)
    authority_bytes = _validate_authority(source_root, historical)
    profiles = {}
    for source_profile in PROFILES:
        entries = lock['profiles'][source_profile]['ordered_files']
        files = []
        for row in entries:
            data = sources[row['path']]
            files.append({'path':row['path'],'git_blob_sha1':row['git_blob_sha1'],'sha256':_sha(data),'bytes':len(data)})
        original4 = validator.ordered_chain_digest([(r['path'],r['git_blob_sha1']) for r in entries[:4]])
        _require(original4 == ORIGINAL4_DIGESTS[source_profile], 'both_profiles_frozen_initial4_digest_changed')
        profiles[source_profile] = {'file_count':len(files),'chain_digest':full_report['profiles'][source_profile]['chain_sha256'],
                                    'digest_algorithm':ALGORITHM,'ordered_files':files}
    # Derive only the initial four AFTER complete original validation and strict
    # HEAD-owned source policy. No independent second migration chain exists.
    entries = lock['profiles'][profile]['ordered_files'][:4]
    prefix = validator.ordered_chain_digest([(row['path'],row['git_blob_sha1']) for row in entries])
    _require(prefix == ORIGINAL4_DIGESTS[profile], 'frozen_initial4_digest_changed')
    if not historical:
        prefix_report = full_report['profiles'][profile]['revision_prefixes']['4']
        _require(prefix_report['ordered_paths'] == [r['path'] for r in entries]
                 and prefix_report['file_count'] == 4 and type(prefix_report['file_count']) is int
                 and prefix_report['chain_sha256'] == prefix, 'validated_prefix_report_inconsistent')
    return {'schema':HISTORICAL_SCHEMA if historical else CURRENT_SCHEMA,
            'project_id':'trillionnium-game','profile':profile,
            'source':{'mode':'historical-source4-only' if historical else 'current-full-source-frontier5',
                      'frontier_schema_version':4 if historical else 5,
                      'lock_path':LOCK_PATH,'lock_sha256':_sha(lock_bytes),'lock_git_blob_sha1':_blob(lock_bytes),
                      'authority_path':AUTHORITY_PATH,'authority_sha256':_sha(authority_bytes),
                      'validator_path':VALIDATOR_PATH,'validator_sha256':_sha(validator_bytes),
                      'profiles':profiles,'complete_validation':full_report},
            'selection':{'target':'StorageV4','execution_schema_version':4,'storage_writer_epoch':4,'table_count':12,
                         'executed_migration_file_count':4,'execution_chain_digest':prefix,'digest_algorithm':ALGORITHM,
                         'ordered_files':profiles[profile]['ordered_files'][:4]},
            'claims':{'source_identity_verified':True,'current_head_eligible':not historical,
                      'exact_git_head_verified':False,'runtime_execution_verified':False,'native_catalog_verified':False,
                      'account_transfer_admitted':False,'accepted':False,'compatibility_credit':False,'production_ready':False}}


def _selection_api():
    # Registry membership, exact class identity and hidden immutable payload
    # reject direct construction, object.__new__ forgeries, subclass/pickle and
    # caller-dictionary substitution at the supported API boundary. Python
    # same-process reflection/monkeypatch attacks are outside this boundary.
    issued = weakref.WeakKeyDictionary()
    class VerifiedSelection:
        __slots__ = ('__weakref__',)
        def __new__(cls, *args, **kwargs):
            raise TypeError('verified_source_selection_requires_full_validation')
        def __init_subclass__(cls, **kwargs):
            raise TypeError('verified_source_selection_cannot_be_subclassed')
        def __reduce__(self):
            raise TypeError('verified_source_selection_not_serializable')
        def __repr__(self):
            return 'VerifiedSourceSelection(source-byte-proof-only)'
    def issue(payload: dict):
        token = object.__new__(VerifiedSelection)
        issued[token] = json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
        return token
    def document(token: Any, historical: bool = False) -> dict:
        _require(type(token) is VerifiedSelection and token in issued, 'unissued_or_forged_selection_token')
        payload = _strict_json(issued[token], 'issued_selection')
        _require(payload['schema'] == (HISTORICAL_SCHEMA if historical else CURRENT_SCHEMA), 'historical_current_token_scope_mismatch')
        return payload

    def verify_current_source_selection(source_root: Path, *, profile: str,
                                        target: SchemaTarget = SchemaTarget.StorageV4):
        # This gate runs before Path conversion, stat/read, validator import or any
        # external work. Accounts source and writer4 never authorize AccountsV5.
        _require(type(target) is SchemaTarget, 'typed_schema_target_required')
        if target is SchemaTarget.NakamaAccountsV5:
            raise SelectionError('schema5_native_catalog_capture_pending')
        _require(target is SchemaTarget.StorageV4, 'exact_schema_target_member_required')
        _require(type(profile) is str and profile in PROFILES, 'source_profile_required')
        root = Path(source_root).resolve()
        return issue(_proof(root, profile, historical=False))


    def verify_historical_source4_selection(source_root: Path, *, profile: str):
        _require(type(profile) is str and profile in PROFILES, 'source_profile_required')
        # Genuine source4 validator + authority + all original8 bodies required.
        # This entry can never issue a current-frontier5 token or pretend HEAD proof.
        root = Path(source_root).resolve()
        return issue(_proof(root, profile, historical=True))


    def current_selection_document(token) -> dict:
        return document(token, historical=False)


    def historical_selection_document(token) -> dict:
        return document(token, historical=True)


    def verify_current_selection_envelope(token, raw: bytes) -> None:
        actual = _strict_json(raw, 'current_selection_envelope')
        _require(_same(actual, current_selection_document(token)), 'current_selection_envelope_type_shape_or_value')


    def verify_historical_selection_envelope(token, raw: bytes) -> None:
        actual = _strict_json(raw, 'historical_selection_envelope')
        _require(_same(actual, historical_selection_document(token)), 'historical_selection_envelope_type_shape_or_value')
    return (verify_current_source_selection, verify_historical_source4_selection, current_selection_document, historical_selection_document, verify_current_selection_envelope, verify_historical_selection_envelope)

(verify_current_source_selection, verify_historical_source4_selection, current_selection_document, historical_selection_document, verify_current_selection_envelope, verify_historical_selection_envelope) = _selection_api()
del _selection_api
