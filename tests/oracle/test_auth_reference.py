"""Offline adversarial tests of the real collector/validator; not oracle evidence."""
from __future__ import annotations

import base64
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('auth_reference', ROOT / 'scripts/oracle/capture-auth-reference.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def token(uid, ttl, tid='33333333-3333-4333-8333-333333333333', username='trnm_reference_custom'):
    def enc(v):
        return base64.urlsafe_b64encode(mod.canonical(v)).decode().rstrip('=')
    return enc({'alg': 'HS256', 'typ': 'JWT'}) + '.' + enc(
        {'uid': uid, 'tid': tid, 'iat': 100, 'exp': 100 + ttl, 'usn': username}) + '.synthetic_signature'


def records_fixture():
    fixture = mod.load(mod.read(mod.FIXTURE))
    snapshot, records = {'users': [], 'user_device': []}, []
    for case in fixture['cases']:
        before = deepcopy(snapshot)
        if case['created'] is True:
            kind = case['id'].split('-')[0]
            uid = ('1' if kind == 'device' else '2') * 8 + '-1111-4111-8111-111111111111'
            snapshot['users'].append({'id': uid, 'username': 'trnm_reference_' + kind,
                'custom_id': 'trnm-reference-custom' if kind == 'custom' else None,
                'create_time': '2026-10-04T00:00:00+00:00', 'update_time': '2026-10-04T00:00:00+00:00',
                'disable_time': '1970-01-01T00:00:00+00:00', 'metadata': {}, 'wallet': {}, 'edge_count': 0})
            if kind == 'device':
                snapshot['user_device'] = [{'id': 'trnm-reference-device', 'user_id': uid}]
        if case['created'] is not None:
            kind = 'device' if case['id'].startswith('device') else 'custom'
            user = next(u for u in snapshot['users'] if u['username'] == 'trnm_reference_' + kind)
            body = {'token': token(user['id'], 3600, username=user['username']),
                    'refresh_token': token(user['id'], 86400, username=user['username'])}
            if case['created']:
                body['created'] = True
        elif case['code'] is not None:
            body = {'code': case['code'], 'message': case['message']}
        else:
            body = {}
        raw_body = mod.canonical(body)
        headers = f"HTTP/1.1 {case['status']} Test\r\nContent-Length: {len(raw_body)}\r\nContent-Type: application/json\r\n\r\n".encode()
        wire = headers + raw_body
        status, _, _ = mod.parse_wire(wire)
        records.append({'id': case['id'], 'before': before, 'after': deepcopy(snapshot),
            'started_at_epoch': 100, 'completed_at_epoch': 100,
            'status': status, 'body': mod.redact_body(raw_body), 'body_length': len(raw_body),
            'body_sha256': mod.digest(raw_body), 'body_base64': None if case['created'] is not None else mod.encode(raw_body),
            'headers_base64': mod.encode(headers), 'request_length': 50,
            'request_sha256': mod.digest(b'synthetic request'), 'response_sha256': mod.digest(wire),
            'response_length': len(wire), 'raw_tokens_retained': False})
    return fixture, records


class AuthReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.records = records_fixture()

    def test_real_validator_accepts_bounded_synthetic_profile(self):
        mod.validate(self.records, self.fixture)

    def test_missing_extra_duplicate_reordered_cases_fail(self):
        for rows in (self.records[:-1], self.records + self.records[:1], self.records[:1] * 15,
                     list(reversed(self.records))):
            with self.subTest(length=len(rows)), self.assertRaises(mod.CaptureError):
                mod.validate(rows, self.fixture)

    def test_missing_fabricated_or_unexpected_database_effects_fail(self):
        for mutate in (
            lambda r: r[3].update(after=r[3]['before']),
            lambda r: r[0]['after']['users'].append(deepcopy(r[3]['after']['users'][0])),
            lambda r: r[3]['after']['user_device'][0].update(user_id='44444444-4444-4444-8444-444444444444'),
            lambda r: r[8]['after']['users'][-1].update(custom_id=None),
            lambda r: r[-1]['after']['users'][0].update(wallet={'coins': 1}),
            lambda r: r[4]['before']['users'][0].update(username='changed'),
        ):
            rows = deepcopy(self.records)
            mutate(rows)
            with self.subTest(mutation=mutate), self.assertRaises(mod.CaptureError):
                mod.validate(rows, self.fixture)

    def test_error_identity_ttl_and_created_cannot_be_normalized(self):
        for mutate in (
            lambda r: r[0].update(status=200),
            lambda r: r[0]['body'].update(code=3),
            lambda r: r[3]['body'].update(created=False),
            lambda r: r[3]['body']['token']['claims'].update(uid='44444444-4444-4444-8444-444444444444'),
            lambda r: r[3]['body']['token']['claims'].update(exp=5000),
            lambda r: r[12]['body']['token']['claims'].update(tid='44444444-4444-4444-8444-444444444444'),
        ):
            rows = deepcopy(self.records)
            mutate(rows)
            with self.subTest(mutation=mutate), self.assertRaises(mod.CaptureError):
                mod.validate(rows, self.fixture)

    def test_duplicate_json_and_nonfinite_fail(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{'):
            with self.assertRaises(mod.CaptureError):
                mod.load(raw)

    def test_wire_framing_header_credential_and_limits_fail(self):
        for raw in (b'HTTP/1.1 200 OK\r\n\r\n{}',
                    b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n{}',
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}',
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nSet-Cookie: secret\r\n\r\n{}',
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Debug: aaaaaaaaaaaaa.bbbbbbbbbbbbb.ccccccccccccc\r\n\r\n{}',
                    b'x' * (mod.LIMIT + 1)):
            with self.subTest(raw=raw[:80]), self.assertRaises(mod.CaptureError):
                mod.parse_wire(raw)

    def test_no_raw_token_survives_projection(self):
        raw = token('22222222-1111-4111-8111-111111111111', 3600)
        view = mod.redact_body(mod.canonical({'token': raw}))
        self.assertNotIn(raw.encode(), mod.canonical(view))
        self.assertEqual(view['token']['sha256'], mod.digest(raw.encode()))
        self.assertFalse(view['token']['signature_verified'])

    def test_child_process_failure_and_timeout_are_not_success(self):
        with self.assertRaises(mod.CaptureError):
            mod.command([sys.executable, '-c', 'raise SystemExit(2)'])
        with patch.object(mod.subprocess, 'run', side_effect=subprocess.TimeoutExpired('test', 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                mod.command(['unused'])

    def packet(self, base):
        output = base / 'capture'
        output.mkdir()
        lock_bytes = mod.read(ROOT / 'oracle/immutable/oracle-lock.json')
        lock = mod.load(lock_bytes)
        facts = {'health_status': 'healthy', 'candidate_commit': 'a' * 40,
                 'oracle_lock_sha256': mod.digest(lock_bytes), 'nakama_image_id': 'sha256:' + 'b' * 64,
                 'postgres_image_id': 'sha256:' + 'c' * 64,
                 'nakama_config_image': mod.NAKAMA_IMAGE, 'postgres_config_image': mod.POSTGRES_IMAGE}
        facts_path = base / 'facts.json'
        mod.write(facts_path, facts)
        mod.write(base / 'lifecycle.json', {'process_status': 0, 'cleanup_status': 0, 'containers_removed': True})
        files = {}
        for record in self.records:
            name = record['id'] + '.json'
            mod.write(output / name, record)
            files[name] = mod.digest(mod.read(output / name))
        manifest = {'schema': 'trillionnium.immutable-auth-reference.v1', 'claims': deepcopy(mod.CLAIMS), 'status': 'reference-captured-unaccepted',
            'normalization_applied': False, 'corpus_id': self.fixture['corpus_id'],
            'oracle': lock['nakama'], 'database': lock['database'],
            'fixture_sha256': mod.digest(mod.read(mod.FIXTURE)),
            'collector_sha256': mod.digest(mod.read(Path(mod.__file__))),
            'retained_reader_sha256': mod.digest(mod.read(ROOT / 'scripts/evidence_admission.py')),
            'source_blobs': self.fixture['source_blobs'], 'runtime_facts': facts,
            'runtime_facts_sha256': mod.digest(mod.read(facts_path)), 'candidate_commit': 'a' * 40,
            'candidate_tree': 'b' * 40, 'case_count': 15, 'cases': files}
        mod.write(output / 'manifest.json', manifest)
        return output, facts_path

    def test_retained_packet_validator(self):
        with tempfile.TemporaryDirectory() as d:
            output, facts = self.packet(Path(d))
            self.assertEqual(mod.verify_capture(output, facts)['case_count'], 15)

    def test_retained_packet_wrong_pins_lifecycle_claims_or_digest_fail(self):
        for target, mutate in (
            ('manifest.json', lambda v: v.update(schema='unknown')),
            ('manifest.json', lambda v: v['oracle'].update(image='nakama:latest')),
            ('manifest.json', lambda v: v['oracle'].update(commit='f' * 40)),
            ('manifest.json', lambda v: v.update(case_count=0)),
            ('manifest.json', lambda v: v['claims'].update(compatibility_credit=True)),
            ('manifest.json', lambda v: v['cases'].update({'extra.json': 'sha256:' + 'a' * 64})),
            ('device-create.json', lambda v: v.update(body_base64=mod.encode(b'raw token'))),
            ('../lifecycle.json', lambda v: v.update(cleanup_status=1)),
            ('../lifecycle.json', lambda v: v.update(process_status=143)),
            ('../lifecycle.json', lambda v: v.update(process_status=False)),
            ('../lifecycle.json', lambda v: v.update(cleanup_status=False)),
            ('../lifecycle.json', lambda v: v.update(containers_removed=False)),
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                output, facts = self.packet(Path(d))
                path = output.parent / target[3:] if target.startswith('../') else output / target
                v = mod.load(mod.read(path)); mutate(v)
                path.write_bytes(mod.canonical(v))
                with self.assertRaises(mod.CaptureError):
                    mod.verify_capture(output, facts)

    def test_missing_cleanup_or_case_and_stale_failure_fail(self):
        for target in ('../lifecycle.json', 'device-create.json', 'failure.json'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                output, facts = self.packet(Path(d))
                if target == 'failure.json':
                    mod.write(output / target, {'status': 'failed'})
                else:
                    (output / target).unlink()
                with self.assertRaises((OSError, mod.CaptureError)):
                    mod.verify_capture(output, facts)

    def test_provenance_rejects_wrong_running_image_or_source(self):
        with tempfile.TemporaryDirectory() as d:
            _, path = self.packet(Path(d))
            facts = mod.load(mod.read(path))
            lock = mod.load(mod.read(ROOT / 'oracle/immutable/oracle-lock.json'))
            for mutate in (lambda f, l: f.update(nakama_config_image='nakama:latest'),
                           lambda f, l: f.update(postgres_image_id=''),
                           lambda f, l: l['nakama'].update(tree='f' * 40),
                           lambda f, l: l['database'].update(image='postgres:latest')):
                f, l = deepcopy(facts), deepcopy(lock); mutate(f, l)
                with self.assertRaises(mod.CaptureError):
                    mod.check_provenance(self.fixture, l, f)

    def test_resealed_raw_token_leak_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            output, facts = self.packet(Path(d))
            path = output / 'custom-create.json'
            value = mod.load(mod.read(path))
            value['body_base64'] = mod.encode(mod.canonical({'token': token('22222222-1111-4111-8111-111111111111', 3600)}))
            path.write_bytes(mod.canonical(value))
            manifest_path = output / 'manifest.json'
            manifest = mod.load(mod.read(manifest_path))
            manifest['cases'][path.name] = mod.digest(mod.read(path))
            manifest_path.write_bytes(mod.canonical(manifest))
            with self.assertRaisesRegex(mod.CaptureError, 'raw token body leaked'):
                mod.verify_capture(output, facts)

    def test_explicit_startup_and_cleanup_failures_from_real_runner(self):
        # A fake Docker executable tests shell lifecycle mechanics only. It never
        # claims an OCI image, API or DB ran, and exits before HTTP collection.
        for phase, expected_process, expected_cleanup in [('pull', 42, 0), ('cleanup', 42, 43), ('startup', 143, 0)]:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                docker = base / 'docker'
                docker.write_text('#!' + sys.executable + "\n" + r'''
import os, sys
args = sys.argv[1:]
phase = os.environ['STUB_PHASE']
if 'pull' in args:
    raise SystemExit(0 if phase == 'startup' else 42)
if 'up' in args:
    raise SystemExit(143)
if 'down' in args:
    raise SystemExit(43 if phase == 'cleanup' else 0)
if 'config' in args:
    print('services: {}')
if 'logs' in args:
    print('eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOiJzb21ldGhpbmcifQ.synthetic_signature')
''')
                docker.chmod(0o755)
                env = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                           STUB_PHASE=phase, TRNM_CANDIDATE_COMMIT='a' * 40, TRNM_KEEP_ORACLE='0')
                output = base / 'output'
                result = subprocess.run(['bash', str(ROOT / 'scripts/oracle/run-immutable-smoke.sh'), str(output)],
                                        env=env, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, expected_process, result.stderr)
                lifecycle = mod.load(mod.read(output / 'lifecycle.json'))
                self.assertEqual(lifecycle['process_status'], expected_process)
                self.assertEqual(lifecycle['cleanup_status'], expected_cleanup)
                self.assertEqual(lifecycle['containers_removed'], expected_cleanup == 0)
                self.assertFalse((output / 'SHA256SUMS').exists())
                self.assertFalse((output / 'compose-logs.raw.txt').exists())
                self.assertNotIn('synthetic_signature', (output / 'compose-logs.txt').read_text())

    def test_refresh_expiry_moves_without_rewriting_original_issuance(self):
        rows = deepcopy(self.records)
        rows[12]['started_at_epoch'] = rows[12]['completed_at_epoch'] = 102
        rows[12]['body']['token']['claims']['exp'] += 2
        rows[12]['body']['refresh_token']['claims']['exp'] += 2
        mod.validate(rows, self.fixture)

    def test_retained_inputs_reject_symlink_fifo_and_oversize(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            source = base / 'source.json'
            source.write_text('{}')
            link = base / 'link.json'
            link.symlink_to(source)
            fifo = base / 'fifo.json'
            os.mkfifo(fifo)
            large = base / 'large.json'
            large.write_bytes(b'x' * (mod.LIMIT + 1))
            for path in (link, fifo, large):
                with self.subTest(path=path.name), self.assertRaises(ValueError):
                    mod.read(path)


if __name__ == '__main__':
    unittest.main()
