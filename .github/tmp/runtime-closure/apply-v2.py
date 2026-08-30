#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

ROOT = Path('.')
STAGED = ROOT / '.github/tmp/runtime-closure'


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def replace_function(source: str, signature: str, replacement: str) -> str:
    start = source.find(signature)
    require(start >= 0, f'missing function signature: {signature}')
    brace = source.find('{', start)
    require(brace >= 0, f'missing function brace: {signature}')
    depth = 0
    end = None
    for position in range(brace, len(source)):
        if source[position] == '{':
            depth += 1
        elif source[position] == '}':
            depth -= 1
            if depth == 0:
                end = position + 1
                break
    require(end is not None, f'unbalanced function: {signature}')
    return source[:start] + replacement.rstrip() + source[end:]


def replace_impl(source: str, signature: str, replacement: str) -> str:
    return replace_function(source, signature, replacement)


def copy_staged() -> None:
    mapping = {
        'outbox.rs': ROOT / 'crates/trnm-persistence-pg/src/outbox.rs',
        'cancel.rs': ROOT / 'crates/trnm-persistence-pg/src/cancel.rs',
        'pool.rs': ROOT / 'crates/trnm-persistence-pg/src/pool.rs',
        'server-pool.rs': ROOT / 'crates/trnm-persistence-pg/src/bin/trnm_server/pool.rs',
        'retry.rs': ROOT / 'crates/trnm-persistence-pg/src/bin/trnm_server/retry.rs',
        'schema.rs': ROOT / 'crates/trnm-persistence-pg/src/bin/trnm_server/schema.rs',
        'outbox-runtime.rs': ROOT / 'crates/trnm-persistence-pg/tests/outbox_runtime.rs',
    }
    missing = [name for name in mapping if not (STAGED / name).is_file()]
    require(not missing, f'missing staged files: {missing}')
    for source_name, destination in mapping.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STAGED / source_name, destination)

    cancel = mapping['cancel.rs']
    value = cancel.read_text(encoding='utf-8').replace(
        'pub(crate) fn cancellation_watchdog_panicked()',
        'pub fn cancellation_watchdog_panicked()',
    )
    cancel.write_text(value, encoding='utf-8')

    outbox = mapping['outbox.rs']
    value = outbox.read_text(encoding='utf-8')
    if 'const MAX_CLAIM_SCAN:' not in value:
        value = value.replace(
            'const MAX_CLAIM_BATCH: usize = 64;\n',
            'const MAX_CLAIM_BATCH: usize = 64;\nconst MAX_CLAIM_SCAN: usize = 256;\n',
            1,
        )
    value = value.replace(
        '        let mut leases = Vec::with_capacity(limit);\n        while leases.len() < limit {\n',
        '        let mut leases = Vec::with_capacity(limit);\n'
        '        let mut scanned = 0_usize;\n'
        '        while leases.len() < limit && scanned < MAX_CLAIM_SCAN {\n'
        '            scanned = scanned.saturating_add(1);\n',
        1,
    )
    outbox.write_text(value, encoding='utf-8')


def patch_manifest() -> None:
    path = ROOT / 'crates/trnm-persistence-pg/Cargo.toml'
    source = path.read_text(encoding='utf-8')
    require('[dependencies]\n' in source, 'persistence-pg manifest lacks dependencies')
    dependencies = [
        ('native-tls', 'native-tls = "=0.2.18"'),
        ('postgres-native-tls', 'postgres-native-tls = "=0.5.3"'),
        ('r2d2', 'r2d2 = "=0.8.10"'),
        ('r2d2_postgres', 'r2d2_postgres = "=0.18.2"'),
    ]
    missing = [line for name, line in dependencies if re.search(rf'(?m)^{re.escape(name)}\s*=', source) is None]
    if missing:
        source = source.replace(
            '[dependencies]\n',
            '[dependencies]\n' + '\n'.join(missing) + '\n',
            1,
        )
    path.write_text(source, encoding='utf-8')


def patch_library() -> None:
    path = ROOT / 'crates/trnm-persistence-pg/src/lib.rs'
    source = path.read_text(encoding='utf-8')
    header = '#![forbid(unsafe_code)]\n\n'
    require(source.startswith(header), 'persistence-pg crate header drifted')
    body = source[len(header):]
    body = re.sub(r'(?m)^mod (?:cancel|outbox|pool);\n', '', body)
    body = re.sub(r'(?ms)^pub use cancel::\{.*?\};\n', '', body)
    body = re.sub(r'(?ms)^pub use outbox::\{.*?^\};\n', '', body)
    body = re.sub(r'(?ms)^pub use pool::\{.*?\};\n', '', body)
    body = body.lstrip('\n')
    prefix = (
        'mod cancel;\nmod outbox;\nmod pool;\n\n'
        'pub use cancel::{cancellation_watchdog_panicked, PgCancelHandle};\n'
        'pub use outbox::{\n'
        '    OutboxLease, OutboxRecord, OutboxRetryOutcome, OutboxState,\n'
        '};\n'
        'pub use pool::{PgPool, PgPoolConfig, PgPoolSnapshot, PgTlsConfig};\n\n'
    )
    source = header + prefix + body

    pattern = re.compile(
        r'pub struct PgRepository \{\n\s*profile: DatabaseProfile,\n\s*client: [^\n]+,\n(?:\s*cancel_transport: [^\n]+,\n)?\}',
    )
    replacement = (
        'pub struct PgRepository {\n'
        '    profile: DatabaseProfile,\n'
        '    client: pool::ClientHandle,\n'
        '    cancel_transport: cancel::CancelTransport,\n'
        '}'
    )
    source, count = pattern.subn(replacement, source, count=1)
    require(count == 1, 'PgRepository structure anchor drifted')

    connect = re.compile(
        r'let client = Client::connect\(database_url, NoTls\)\.map_err\(map_postgres_error\)\?;\n\s*Ok\(Self \{.*?\}\)',
        re.S,
    )
    replacement = (
        'let client = Client::connect(database_url, NoTls).map_err(map_postgres_error)?;\n'
        '        Ok(Self {\n'
        '            profile,\n'
        '            client: pool::ClientHandle::direct(client),\n'
        '            cancel_transport: cancel::CancelTransport::Plain,\n'
        '        })'
    )
    source, count = connect.subn(replacement, source, count=1)
    require(count == 1, 'PgRepository::connect anchor drifted')

    intent_impl = '''impl IntentKind {
    const fn database_value(self) -> i16 {
        self as i16
    }

    pub(crate) const fn from_database_value(value: i16) -> Option<Self> {
        match value {
            0 => Some(Self::Broadcast),
            1 => Some(Self::SearchIndex),
            2 => Some(Self::Notification),
            3 => Some(Self::ExternalEffect),
            4 => Some(Self::Completion),
            _ => None,
        }
    }
}'''
    source = replace_impl(source, 'impl IntentKind {', intent_impl)
    path.write_text(source, encoding='utf-8')


def patch_server() -> None:
    app = ROOT / 'crates/trnm-persistence-pg/src/bin/trnm_server/app.rs'
    source = app.read_text(encoding='utf-8')
    derive = '#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]\npub struct RepositoryOperationalMetrics'
    start = source.find(derive)
    if start < 0:
        start = source.find('pub trait Repository:')
    end = source.find('impl Repository for PgRepository')
    require(start >= 0 and end > start, 'Repository trait section anchor drifted')
    trait_block = (STAGED / 'app-trait.rs').read_text(encoding='utf-8')
    source = source[:start] + trait_block + source[end:]
    metrics = (STAGED / 'app-metrics.rs').read_text(encoding='utf-8')
    source = replace_function(source, '    fn metrics_response(&self) -> Response {', metrics)
    app.write_text(source, encoding='utf-8')

    module = ROOT / 'crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs'
    source = module.read_text(encoding='utf-8')
    if 'pub(crate) mod pool;' not in source:
        require('pub(crate) mod json;\n' in source, 'server module anchor drifted')
        source = source.replace(
            'pub(crate) mod json;\n',
            'pub(crate) mod json;\npub(crate) mod pool;\n',
            1,
        )
    module.write_text(source, encoding='utf-8')

    server = ROOT / 'crates/trnm-persistence-pg/src/bin/trnm_server/server.rs'
    source = server.read_text(encoding='utf-8')
    source = source.replace('use trnm_persistence_pg::PgRepository;\n\n', '')
    source = source.replace('use super::app::App;', 'use super::app::{App, Repository};')
    source = source.replace(
        'pub fn serve(config: &ServerConfig, repository: PgRepository) -> Result<(), ServerError> {',
        'pub fn serve<R: Repository>(config: &ServerConfig, repository: R) -> Result<(), ServerError> {',
    )
    require('pub fn serve<R: Repository>' in source, 'generic server repository composition missing')
    server.write_text(source, encoding='utf-8')


def immutable_images() -> tuple[str, str]:
    workflow_text = '\n'.join(
        path.read_text(encoding='utf-8', errors='replace')
        for path in (ROOT / '.github/workflows').glob('*.yml')
        if not path.name.startswith('temporary-')
    )
    postgres = re.findall(
        r"postgres(?::[^@\s\"']+)?@sha256:[0-9a-f]{64}",
        workflow_text,
    )
    cockroach = re.findall(
        r"cockroachdb/cockroach(?::[^@\s\"']+)?@sha256:[0-9a-f]{64}",
        workflow_text,
    )
    require(bool(postgres), 'immutable PostgreSQL image not found')
    require(bool(cockroach), 'immutable CockroachDB image not found')
    return postgres[0], cockroach[0]


def write_workflow_and_docs() -> None:
    postgres, cockroach = immutable_images()
    template = (STAGED / 'pg-outbox-fault.yml.in').read_text(encoding='utf-8')
    require(template.count('__POSTGRES_IMAGE__') == 1, 'PostgreSQL image placeholder drifted')
    require(template.count('__COCKROACH_IMAGE__') == 1, 'CockroachDB image placeholder drifted')
    workflow = template.replace('__POSTGRES_IMAGE__', postgres).replace(
        '__COCKROACH_IMAGE__', cockroach
    )
    path = ROOT / '.github/workflows/pg-outbox-fault.yml'
    path.write_text(workflow, encoding='utf-8')
    with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as handle:
        handle.write(f'POSTGRES_IMAGE={postgres}\n')
        handle.write(f'COCKROACH_IMAGE={cockroach}\n')

    protocol = ROOT / 'docs/development/OUTBOX_DISPATCH_PROTOCOL.md'
    protocol.write_text(
        '''# Transactional outbox dispatch protocol

Status: source candidate. Compatibility, durability, production and retirement credit remain false until exact-head profile evidence and independent review are accepted.

The PG-wire repository exposes bounded claim, completion, retry and dead-letter transitions. Every mutation is fenced by intent identity, owner node, attempt, lease generation and exact lease expiry. A worker that has lost or exceeded its lease cannot complete or reschedule an intent. Claim increments both attempt and generation atomically. Pending or expired leased rows at the attempt ceiling are atomically converted to dead-letter state during a bounded claim scan, preventing a crash after the final delivery attempt from stranding an invisible lease.

Successful completion clears ownership and stores a non-zero receipt digest. Retry clears ownership and schedules an explicit next-available timestamp. Dead-letter clears ownership and stores a stable reason digest. State inspection exposes only typed pending, leased, delivered and dead-letter records.

The dual-profile fault test proves the declared subset on fresh PostgreSQL and CockroachDB databases: initial claim, explicit retry, generation fencing, simulated worker crash at the attempt ceiling, automatic dead-letter reclaim, successful receipt completion, and persistence across repository reconnection. The server pool holds the checked-out connection until its cancellation watchdog exits, preventing a late cancel request from targeting a later borrower.

Remaining closure requirements include a real external-effect dispatcher and reconciliation adapter, node-loss/endurance/load evidence, TLS rotation and expiry faults, independently accepted database/protocol/security review, and later SG4/SG8 production evidence.
''',
        encoding='utf-8',
    )


def clean_temporary_files() -> None:
    for path in (ROOT / '.github/workflows').glob('temporary-*.yml'):
        path.unlink()
    shutil.rmtree(STAGED, ignore_errors=True)
    parent = STAGED.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def main() -> None:
    copy_staged()
    patch_manifest()
    patch_library()
    patch_server()
    write_workflow_and_docs()
    clean_temporary_files()


if __name__ == '__main__':
    main()
