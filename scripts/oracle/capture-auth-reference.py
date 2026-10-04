#!/usr/bin/env python3
"""Bounded, reference-only HTTP capture inside the disposable immutable lane.

No raw credential/token is persisted. Wire commitments are not reversible wire
proof. This new corpus cannot replace historical evidence or qualify Rust parity.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
READ_SPEC = importlib.util.spec_from_file_location('auth_reference_retained_io', ROOT / 'scripts/evidence_admission.py')
if READ_SPEC is None or READ_SPEC.loader is None:
    raise RuntimeError('bounded retained reader unavailable')
RETAINED_IO = importlib.util.module_from_spec(READ_SPEC)
READ_SPEC.loader.exec_module(RETAINED_IO)
FIXTURE = ROOT / 'oracle/immutable/auth-reference-cases.json'
LIMIT = 1024 * 1024
TIMEOUT = 10
NAKAMA_COMMIT = 'd4d92f93f78bbbe62c7fc50a3f85c772ec121a09'
NAKAMA_TREE = 'f3c9cfc2726d5543da1564629170f35b98e3797d'
NAKAMA_IMAGE = 'heroiclabs/nakama:3.40.0@sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd'
POSTGRES_IMAGE = 'postgres:17.6-alpine3.22@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
CLAIMS = dict(compatibility_credit=False, production_ready=False, accounts_v5_enabled=False,
              historical_corpus_recovered=False, sg2_complete=False)
# Explicit durable projection; credentials never enter captured DB rows.
# LIMIT is a contamination/oversize tripwire, not a whole-schema comparison.
SQL = """SELECT json_build_object(
 'users', (SELECT coalesce(json_agg(t ORDER BY id), '[]') FROM
   (SELECT id, username, custom_id, create_time, update_time, disable_time, metadata, wallet, edge_count FROM users WHERE id <> '00000000-0000-0000-0000-000000000000' LIMIT 3) t),
 'user_device', (SELECT coalesce(json_agg(t ORDER BY id), '[]') FROM
   (SELECT id, user_id FROM user_device LIMIT 3) t));"""


class CaptureError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise CaptureError(message)


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def encode(data):
    return base64.b64encode(data).decode('ascii')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def load(data):
    require(len(data) <= LIMIT, 'JSON byte bound')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate JSON key')
            value[key] = item
        return value
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_float=lambda _: (_ for _ in ()).throw(CaptureError('floating-point JSON unsupported')),
                          parse_constant=lambda _: (_ for _ in ()).throw(CaptureError('nonfinite JSON')))
    except (ValueError, RecursionError) as exc:
        raise CaptureError('invalid JSON') from exc


def read(path):
    # Reuse the repository's descriptor-relative no-follow reader. This also
    # rejects FIFOs without blocking and detects file changes during the read.
    try:
        return b''.join(RETAINED_IO._regular_chunks(path, LIMIT))
    except RETAINED_IO.AdmissionError as exc:
        raise CaptureError('retained input rejected') from exc


def write(path, value):
    data = canonical(value) + b'\n'
    require(len(data) <= LIMIT, 'output byte bound')
    with path.open('xb') as stream:
        stream.write(data)


def token_view(token):
    require(isinstance(token, str) and 0 < len(token) <= 16384, 'token bound')
    parts = token.split('.')
    require(len(parts) == 3, 'token format')
    try:
        header, claims = [load(base64.b64decode(p + '=' * (-len(p) % 4), altchars=b'-_', validate=True))
                          for p in parts[:2]]
    except ValueError as exc:
        raise CaptureError('token encoding') from exc
    require(isinstance(header, dict) and isinstance(claims, dict), 'token objects')
    require(header == {'alg': 'HS256', 'typ': 'JWT'}, 'unexpected token header')
    require(claims.get('vrs', {}) == {}, 'unexpected token vars')
    require(claims.get('usn') in ('trnm_reference_device', 'trnm_reference_custom'), 'unexpected token username')
    require(set(claims) <= {'tid', 'uid', 'usn', 'vrs', 'exp', 'iat'}, 'unexpected token claims')
    for key in ('uid', 'tid'):
        try:
            require(str(uuid.UUID(claims[key])) == claims[key], 'token UUID')
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise CaptureError('token UUID') from exc
    require(type(claims.get('iat')) is int and type(claims.get('exp')) is int, 'token times')
    return {'sha256': digest(token.encode()), 'length': len(token), 'header': header,
            'claims': claims, 'signature_verified': False}


def redact_body(body):
    value = load(body)
    require(isinstance(value, dict), 'response object')
    result = dict(value)
    for key in ('token', 'refresh_token'):
        if key in result:
            result[key] = token_view(result[key])
    return result


def parse_wire(wire):
    require(len(wire) <= LIMIT, 'wire byte bound')
    head, sep, body = wire.partition(b'\r\n\r\n')
    require(bool(sep) and len(head) <= 32768, 'HTTP header bound/framing')
    require(re.search(rb'[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', head) is None, 'token-bearing response header')
    lines = head.split(b'\r\n')
    match = re.fullmatch(rb'HTTP/1\.[01] ([0-9]{3}) [^\r\n]*', lines[0])
    require(match is not None, 'HTTP status line')
    headers = []
    for line in lines[1:]:
        name, colon, value = line.partition(b':')
        require(colon and re.fullmatch(rb'[!#$%&\x27*+.^_`|~0-9A-Za-z-]+', name), 'HTTP header')
        require(name.lower() not in (b'set-cookie', b'authorization', b'proxy-authenticate'),
                'credential-bearing response header')
        headers.append((name.lower(), value.strip()))
    lengths = [v for k, v in headers if k == b'content-length']
    require(len(lengths) == 1 and lengths[0].isdigit(), 'single Content-Length required')
    require(not any(k == b'transfer-encoding' for k, _ in headers), 'chunked capture unsupported')
    require(int(lengths[0]) == len(body), 'truncated/trailing HTTP response')
    return int(match[1]), head + sep, body


def exchange(port, path, body, authorization):
    require(type(port) is int and 1 <= port <= 65535, 'invalid loopback port')
    payload = canonical(body)
    request = (f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
               'Connection: close\r\nContent-Type: application/json\r\n'
               f'Authorization: {authorization}\r\nContent-Length: {len(payload)}\r\n\r\n').encode() + payload
    started = int(time.time())
    deadline = time.monotonic() + TIMEOUT
    chunks, size = [], 0
    with socket.create_connection(('127.0.0.1', port), timeout=TIMEOUT) as connection:
        connection.sendall(request)
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'HTTP deadline')
            connection.settimeout(remaining)
            data = connection.recv(min(65536, LIMIT + 1 - size))
            if not data:
                break
            chunks.append(data)
            size += len(data)
            require(size <= LIMIT, 'HTTP response byte bound')
    wire = b''.join(chunks)
    status, headers, raw_body = parse_wire(wire)
    require(authorization.encode() not in headers, 'authorization echoed in response')
    view = redact_body(raw_body)
    sensitive = 'token' in view or 'refresh_token' in view
    return view, load(raw_body), {
        'request_sha256': digest(request), 'request_length': len(request),
        'response_sha256': digest(wire), 'response_length': len(wire),
        'status': status, 'headers_base64': encode(headers),
        'started_at_epoch': started, 'completed_at_epoch': int(time.time()),
        'body_sha256': digest(raw_body), 'body_length': len(raw_body),
        'body_base64': None if sensitive else encode(raw_body), 'body': view,
        'raw_tokens_retained': False,
    }


def check_case(case, record):
    require(record['id'] == case['id'], 'case identity/order')
    require(record['status'] == case['status'], 'unexpected HTTP status')
    require(record['raw_tokens_retained'] is False, 'raw token retention')
    body, before, after = record['body'], record['before'], record['after']
    started, completed = record['started_at_epoch'], record['completed_at_epoch']
    require(type(started) is int and type(completed) is int and 0 <= completed - started <= TIMEOUT + 1, 'observation clock window')
    for snapshot in (before, after):
        require(set(snapshot) == {'users', 'user_device'}, 'database projection')
        require(isinstance(snapshot['users'], list) and len(snapshot['users']) <= 2, 'database user count')
        require(isinstance(snapshot['user_device'], list) and len(snapshot['user_device']) <= 1, 'database device count')
        for row in snapshot['users']:
            require(set(row) == {'id', 'username', 'custom_id', 'create_time', 'update_time', 'disable_time', 'metadata', 'wallet', 'edge_count'}, 'user projection')
            require(row['metadata'] == {} and row['wallet'] == {} and row['edge_count'] == 0, 'unexpected user state')
            require(row['username'] in ('trnm_reference_device', 'trnm_reference_custom'), 'unexpected username')
            require(row['custom_id'] in (None, 'trnm-reference-custom'), 'unexpected custom identity')
            for key in ('create_time', 'update_time', 'disable_time'):
                require(isinstance(row[key], str) and re.fullmatch(r'[0-9T:+. -]{19,40}', row[key]), 'database timestamp')
            require(str(uuid.UUID(row['id'])) == row['id'], 'database UUID')
        for row in snapshot['user_device']:
            require(set(row) == {'id', 'user_id'} and row['id'] == 'trnm-reference-device', 'device projection')
            require(str(uuid.UUID(row['user_id'])) == row['user_id'], 'device UUID')
    if case['code'] is not None:
        require(set(body) <= {'code', 'message', 'details'} and body.get('details', []) == [], 'error fields')
        require(body.get('code') == case['code'] and body.get('message') == case['message'], 'error contract')
    elif case['created'] is not None:
        # ProtoJSON omits default false; it must not be changed into a true value.
        require(body.get('created', False) is case['created'], 'created flag')
        require(set(body) <= {'created', 'token', 'refresh_token'}, 'session fields')
        access, refresh = body.get('token'), body.get('refresh_token')
        require(isinstance(access, dict) and isinstance(refresh, dict), 'session tokens')
        for token, ttl in ((access, 3600), (refresh, 86400)):
            claims = token['claims']
            require(token['header'] == {'alg': 'HS256', 'typ': 'JWT'}, 'token header')
            require(set(claims) <= {'uid', 'tid', 'iat', 'exp', 'usn', 'vrs'} and claims.get('vrs', {}) == {}, 'token claims')
            require(str(uuid.UUID(claims['tid'])) == claims['tid'], 'token session UUID')
            require(type(claims['iat']) is int and type(claims['exp']) is int, 'token integer times')
            require(token['signature_verified'] is False, 'signature verification overclaim')
            require(started + ttl <= claims['exp'] <= completed + ttl, 'token expiry window')
            if case['id'] != 'refresh-valid':
                require(started <= claims['iat'] <= completed, 'token issuance window')
            users = [user for user in after['users'] if user['id'] == claims['uid']]
            require(len(users) == 1 and users[0]['username'] == claims['usn'], 'token/database identity')
        require(all(access['claims'][key] == refresh['claims'][key] for key in ('uid', 'tid', 'iat', 'usn')), 'token pair identity')
    else:
        require(body == {}, 'logout body')
    if case['created'] is True:
        require(len(after['users']) == len(before['users']) + 1, 'creation count')
        new = [row for row in after['users'] if row not in before['users']]
        require(len(new) == 1 and all(row in after['users'] for row in before['users']), 'creation changed existing row')
        kind = case['id'].split('-')[0]
        require(new[0]['username'] == 'trnm_reference_' + kind, 'created username')
        if kind == 'device':
            require(before['user_device'] == [] and after['user_device'] == [
                {'id': 'trnm-reference-device', 'user_id': new[0]['id']}], 'device linkage')
            require(new[0]['custom_id'] is None, 'unexpected custom link')
        else:
            require(new[0]['custom_id'] == 'trnm-reference-custom', 'custom linkage')
            require(before['user_device'] == after['user_device'], 'custom changed devices')
    else:
        require(before == after, 'unexpected durable effect')


def validate(records, fixture):
    cases = fixture['cases']
    require(len(records) == len(cases) == 15, 'missing/extra cases')
    require(records[0]['before'] == {'users': [], 'user_device': []}, 'nonempty initial database')
    for i, (case, record) in enumerate(zip(cases, records)):
        check_case(case, record)
        if i:
            require(records[i - 1]['after'] == record['before'], 'database continuity')
    # The valid refresh must preserve the original immutable token identity.
    source = next(r for r in records if r['id'] == 'custom-existing')['body']['token']['claims']
    refreshed = next(r for r in records if r['id'] == 'refresh-valid')['body']['token']['claims']
    require(all(source[k] == refreshed[k] for k in ('uid', 'tid', 'iat', 'usn')), 'refresh identity changed')


def command(args):
    # Bounded stdout/stderr files avoid unbounded communicate() buffers. External
    # timeout plus RLIMIT_FSIZE bounds a malfunctioning child, including docker.
    import resource
    import tempfile
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(args, stdout=out, stderr=err, timeout=TIMEOUT,
                                check=False, preexec_fn=limits)
        require(result.returncode == 0, 'subprocess failed')
        out.seek(0)
        data = out.read(LIMIT + 1)
        require(len(data) < LIMIT, 'subprocess output bound')
        return data


def check_provenance(fixture, lock, facts):
    require(fixture['oracle_commit'] == lock['nakama']['commit'] == NAKAMA_COMMIT, 'oracle source pin')
    require(fixture['oracle_tree'] == lock['nakama']['tree'] == NAKAMA_TREE, 'oracle tree pin')
    require(lock['nakama']['image'] == NAKAMA_IMAGE, 'Nakama image pin')
    require(lock['database']['image'] == POSTGRES_IMAGE, 'PostgreSQL image pin')
    require(facts['health_status'] == 'healthy', 'oracle unhealthy')
    for key in ('nakama_image_id', 'postgres_image_id'):
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', facts[key]) is not None, 'runtime image ID')
    require(facts['nakama_config_image'] == NAKAMA_IMAGE, 'running Nakama image pin')
    require(facts['postgres_config_image'] == POSTGRES_IMAGE, 'running PostgreSQL image pin')
    require(re.fullmatch('[0-9a-f]{40}', facts['candidate_commit']) is not None
            and facts['candidate_commit'] != '0' * 40, 'candidate commit')


def verify_capture(output, facts_path):
    fixture_bytes, lock_bytes = read(FIXTURE), read(ROOT / 'oracle/immutable/oracle-lock.json')
    fixture, lock = load(fixture_bytes), load(lock_bytes)
    manifest = load(read(output / 'manifest.json'))
    facts_bytes = read(facts_path)
    facts = load(facts_bytes)
    require(facts['oracle_lock_sha256'] == digest(lock_bytes), 'oracle lock binding')
    check_provenance(fixture, lock, facts)
    require(manifest['schema'] == 'trillionnium.immutable-auth-reference.v1', 'manifest schema')
    require(set(manifest['claims']) == set(CLAIMS) and all(value is False for value in manifest['claims'].values()), 'claims changed')
    require(manifest['status'] == 'reference-captured-unaccepted', 'capture failed/incomplete')
    require(manifest['normalization_applied'] is False, 'normalization applied')
    require(manifest['corpus_id'] == fixture['corpus_id'], 'corpus identity')
    require(manifest['oracle'] == lock['nakama'] and manifest['database'] == lock['database'], 'manifest image/source')
    require(manifest['fixture_sha256'] == digest(fixture_bytes), 'fixture hash')
    require(manifest['collector_sha256'] == digest(read(Path(__file__))), 'collector hash')
    require(manifest['retained_reader_sha256'] == digest(read(ROOT / 'scripts/evidence_admission.py')), 'retained reader hash')
    require(manifest['source_blobs'] == fixture['source_blobs'], 'source blobs')
    require(manifest['runtime_facts_sha256'] == digest(facts_bytes) and manifest['runtime_facts'] == facts, 'runtime facts')
    require(manifest['candidate_commit'] == facts['candidate_commit'], 'candidate binding')
    require(re.fullmatch('[0-9a-f]{40}', manifest['candidate_tree']) is not None, 'candidate tree')
    lifecycle = load(read(output.parent / 'lifecycle.json'))
    require(set(lifecycle) == {'process_status', 'cleanup_status', 'containers_removed'}
            and type(lifecycle['process_status']) is int and lifecycle['process_status'] == 0
            and type(lifecycle['cleanup_status']) is int and lifecycle['cleanup_status'] == 0
            and lifecycle['containers_removed'] is True, 'lifecycle/cleanup failed')
    expected = {case['id'] + '.json' for case in fixture['cases']}
    require(manifest['case_count'] == 15 and set(manifest['cases']) == expected, 'missing/extra cases')
    require({p.name for p in output.iterdir()} == expected | {'manifest.json'}, 'unexpected capture file')
    records = []
    for case in fixture['cases']:
        name = case['id'] + '.json'
        data = read(output / name)
        require(digest(data) == manifest['cases'][name], 'case digest')
        record = load(data)
        headers = base64.b64decode(record['headers_base64'], validate=True)
        require(len(headers) <= 32768 and headers.endswith(b'\r\n\r\n'), 'retained header bound')
        require(type(record['body_length']) is int and 0 <= record['body_length'] < LIMIT, 'retained body bound')
        require(type(record['request_length']) is int and 0 < record['request_length'] < 32768, 'retained request bound')
        # Validate status/header framing without manufacturing token-bearing body bytes.
        parsed_status, _, _ = parse_wire(headers + b' ' * record['body_length'])
        require(parsed_status == record['status'], 'retained status mismatch')
        require(record['response_length'] == len(headers) + record['body_length'], 'wire length')
        if case['created'] is not None:
            require(record['body_base64'] is None, 'raw token body leaked')
            for key in ('token', 'refresh_token'):
                token = record['body'][key]
                require(set(token) == {'sha256', 'length', 'header', 'claims', 'signature_verified'}, 'token view fields')
                require(re.fullmatch(r'sha256:[0-9a-f]{64}', token['sha256']) is not None, 'token digest')
                require(type(token['length']) is int and 0 < token['length'] <= 16384, 'token length')
        else:
            body = base64.b64decode(record['body_base64'], validate=True)
            require(load(body) == record['body'] and digest(body) == record['body_sha256'], 'raw body binding')
            require(len(body) == record['body_length'], 'raw body length')
            require(digest(headers + body) == record['response_sha256'], 'raw response binding')
        for key in ('request_sha256', 'response_sha256', 'body_sha256'):
            require(re.fullmatch(r'sha256:[0-9a-f]{64}', record[key]) is not None, 'wire commitment')
        # Tokens/credentials have no permitted free-text retention location.
        require(set(record) == {'id', 'before', 'after', 'request_sha256', 'request_length',
                'response_sha256', 'response_length', 'status', 'headers_base64', 'body_sha256',
                'body_length', 'body_base64', 'body', 'raw_tokens_retained', 'started_at_epoch',
                'completed_at_epoch'}, 'unexpected record fields')
        records.append(record)
    validate(records, fixture)
    return manifest


def capture(port, env_file, output, facts_path):
    fixture_bytes = read(FIXTURE)
    fixture = load(fixture_bytes)
    require(fixture['schema'] == 'trillionnium.immutable-auth-reference-fixture.v1'
            and fixture['historical_51_pair_corpus'] is False, 'fixture schema/scope')
    facts_bytes = read(facts_path)
    facts = load(facts_bytes)
    lock_bytes = read(ROOT / 'oracle/immutable/oracle-lock.json')
    lock = load(lock_bytes)
    check_provenance(fixture, lock, facts)
    require(facts['oracle_lock_sha256'] == digest(lock_bytes), 'oracle lock binding')
    require(facts['health_status'] == 'healthy', 'oracle unhealthy')
    require(re.fullmatch('[0-9a-f]{40}', facts['candidate_commit']) is not None, 'candidate commit')
    head = command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
    require(head == facts['candidate_commit'], 'candidate is not checked out')
    tree = command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD^{tree}']).decode().strip()
    command(['git', '-C', str(ROOT), 'diff', '--quiet', 'HEAD', '--', 'scripts/oracle',
             'scripts/evidence_admission.py', 'oracle/immutable', '.github/workflows/oracle-immutable-smoke.yml'])
    # Deliberately accept only the existing disposable profile's fixed synthetic
    # secret. Never accept a user account credential or a remote endpoint.
    env = dict(line.split('=', 1) for line in read(env_file).decode().splitlines() if '=' in line)
    server = hashlib.sha256(b'trillionnium-oracle-server-v1').hexdigest()
    require(env.get('TRNM_ORACLE_SERVER_KEY') == server, 'not disposable synthetic profile')
    compose = ['docker', 'compose', '--env-file', str(env_file), '-f', str(ROOT / 'oracle/immutable/compose.yml')]
    def snapshot():
        return load(command(compose + ['exec', '-T', 'postgres', 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                                     '-U', 'postgres', '-d', 'nakama', '-Atqc', SQL]))
    sessions, records = {}, []
    baseline = snapshot()
    require(baseline == {'users': [], 'user_device': []}, 'nonempty initial database')
    def resolve(value):
        if not isinstance(value, str) or not value.startswith('$'):
            return value
        case_id, field = value[1:].split('.')
        return sessions[case_id][field]
    for case in fixture['cases']:
        before = snapshot()
        body = {key: resolve(value) for key, value in case['body'].items()}
        auth = ('Basic ' + encode((server + ':').encode()) if case['auth'] == 'server'
                else 'Bearer ' + resolve(case['auth']))
        _, session, record = exchange(port, case['path'], body, auth)
        sessions[case['id']] = session
        record.update(id=case['id'], before=before, after=snapshot())
        # Validate the safe retained projection before writing any unexpected server data.
        # Failed outcomes produce failure.json; never a success or unsafe raw dump.
        check_case(case, record)
        write(output / (case['id'] + '.json'), record)
        records.append(record)
    validate(records, fixture)
    files = {p.name: digest(read(p)) for p in sorted(output.glob('*.json'))}
    manifest = {'schema': 'trillionnium.immutable-auth-reference.v1',
                'corpus_id': fixture['corpus_id'], 'status': 'reference-captured-unaccepted',
                'claims': CLAIMS, 'candidate_commit': head, 'candidate_tree': tree,
                'oracle': lock['nakama'], 'database': lock['database'],
                'runtime_facts': facts, 'runtime_facts_sha256': digest(facts_bytes),
                'fixture_sha256': digest(fixture_bytes), 'source_blobs': fixture['source_blobs'],
                'collector_sha256': digest(read(Path(__file__))), 'cases': files,
                'retained_reader_sha256': digest(read(ROOT / 'scripts/evidence_admission.py')),
                'producer': {key: os.environ.get(key) for key in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID',
                    'GITHUB_RUN_ATTEMPT', 'GITHUB_JOB', 'GITHUB_WORKFLOW_REF', 'GITHUB_WORKFLOW_SHA')},
                'case_count': len(records), 'normalization_applied': False,
                'limitations': ['reference only; no Rust execution or differential',
                                'not the historical private 51-pair corpus',
                                'token-bearing wire retained only as digest and length; not raw replayable proof',
                                'JWT claims decoded but signatures not independently verified',
                                'single process; no restart, race, native Rust catalog, migration or restore proof',
                                'database effects cover declared users/user_device projections only; credentials excluded',
                                'independent acceptance and retained artifact custody absent']}
    write(output / 'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--port', type=int)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--facts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.verify:
        try:
            verify_capture(args.output, args.facts)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print('immutable auth reference validation failed: ' + type(exc).__name__, file=sys.stderr)
            return 1
        print('immutable auth reference validation passed; compatibility_credit=false')
        return 0
    require(args.port is not None and args.env_file is not None, 'capture requires port/env-file')
    # Exclusive new directory prevents stale success from surviving a failed rerun.
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        result = capture(args.port, args.env_file, args.output, args.facts)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        write(args.output / 'failure.json', {'status': 'failed', 'error_class': type(exc).__name__,
                                           'claims': CLAIMS})
        print('immutable auth reference capture failed; see failure.json', file=sys.stderr)
        return 1
    print(f"immutable auth reference cases={result['case_count']}; compatibility_credit=false")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
