"""Pure POSIX supervisor models: virtual clock, fake pipes, kill and reap."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import schema_source_http as T

TOKEN = 'mock-secret-token-never-in-argv'
URL = 'https://api.github.com/repos/TrillionniumFoundation/TrillionniumGame/git/commits/' + 'a' * 40


class Harness:
    def __init__(self, *, stdout=None, stderr=b'', stage=None, trickle=False, exitcode=0,
                 wait_hang=False, kill_failure=False, reap_failure=False, launch_delay=0,
                 write_blocked=False, write_failure=False, partial_write=None, leader_exited=False):
        self.now = 0.0
        self.stdout_bytes = T.ok_envelope(b'{"ok":true}') if stdout is None else stdout
        self.stderr_bytes = stderr
        self.stage = stage
        self.trickle = trickle
        self.exitcode = exitcode
        self.wait_hang = wait_hang
        self.kill_failure = kill_failure
        self.reap_failure = reap_failure
        self.launch_delay = launch_delay
        self.write_blocked = write_blocked
        self.write_failure = write_failure
        self.partial_write = partial_write
        self.kills = []
        self.waits = []
        self.reads = []
        self.writes = []
        self.select_timeouts = []
        self.commands = []
        self.alive = not leader_exited
        self.group_alive = True
        self.killed = False
        self.reaped = False
        self.pipes = {}
        for fd, label in ((100001, 'stdin'), (100002, 'stdout'), (100003, 'stderr')):
            pipe = SimpleNamespace(fd=fd, label=label, closed=False)
            pipe.fileno = lambda p=pipe: p.fd
            pipe.close = lambda p=pipe: setattr(p, 'closed', True)
            self.pipes[fd] = pipe
        self.process = SimpleNamespace(pid=800001, stdin=self.pipes[100001], stdout=self.pipes[100002],
                                       stderr=self.pipes[100003], wait=self.wait)
        self.keys = {}
        self.selector_closed = False

    def popen(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        self.now += self.launch_delay
        return self.process

    def wait(self, *, timeout):
        self.waits.append(timeout)
        if self.killed:
            if self.reap_failure:
                self.now += timeout
                raise subprocess.TimeoutExpired('redacted', timeout)
            self.reaped = True
            return -signal.SIGKILL
        if self.wait_hang:
            self.now += timeout
            raise subprocess.TimeoutExpired('redacted', timeout)
        self.alive = False
        self.group_alive = False
        self.reaped = True
        return self.exitcode

    def killpg(self, pid, sig):
        self.kills.append((pid, sig))
        if self.kill_failure:
            raise PermissionError('OS secret must never be reported')
        self.killed = True
        self.alive = False
        self.group_alive = False

    def register(self, pipe, events, label):
        self.keys[pipe.fd] = SimpleNamespace(fileobj=pipe, events=events, data=label)

    def unregister(self, pipe):
        del self.keys[pipe.fd]

    def get_map(self):
        return self.keys

    def select(self, timeout):
        self.select_timeouts.append(timeout)
        if 'stdin' in [k.data for k in self.keys.values()]:
            if self.write_blocked:
                self.now += timeout
                return []
            return [(k, k.events) for k in self.keys.values() if k.data == 'stdin']
        if self.stage is not None:
            if self.trickle:
                self.now += 1
                self.stdout_bytes = b'x' + self.stdout_bytes
                return [(k, k.events) for k in self.keys.values() if k.data == 'stdout']
            self.now += timeout
            return []
        return [(k, k.events) for k in list(self.keys.values())]

    def read(self, fd, size):
        self.reads.append((fd, size))
        if fd == 100002:
            n = min(size, 1) if self.trickle else size
            part, self.stdout_bytes = self.stdout_bytes[:n], self.stdout_bytes[n:]
        elif fd == 100003:
            part, self.stderr_bytes = self.stderr_bytes[:size], self.stderr_bytes[size:]
        else:
            raise AssertionError('unexpected fake read fd')
        return part

    def write(self, fd, raw):
        self.writes.append(bytes(raw))
        if self.write_failure:
            raise BrokenPipeError('input secret must never be reported')
        return len(raw) if self.partial_write is None else min(self.partial_write, len(raw))

    def close(self):
        self.selector_closed = True

    @contextlib.contextmanager
    def installed(self):
        with mock.patch.object(T.subprocess, 'Popen', side_effect=self.popen), \
                mock.patch.object(T.selectors, 'DefaultSelector', return_value=self), \
                mock.patch.object(T.time, 'monotonic', side_effect=lambda: self.now), \
                mock.patch.object(T.os, 'read', side_effect=self.read), \
                mock.patch.object(T.os, 'write', side_effect=self.write), \
                mock.patch.object(T.os, 'set_blocking') as nonblocking, \
                mock.patch.object(T.os, 'killpg', side_effect=self.killpg):
            yield self
        self.nonblocking = nonblocking.call_args_list

    def fetch(self, **kwargs):
        with self.installed():
            return T.fetch(TOKEN, URL, budget='file', **kwargs)


class SourceHttpSupervisorTests(unittest.TestCase):
    def assert_clean(self, harness, *, killed=True):
        self.assertTrue(harness.selector_closed)
        self.assertTrue(all(p.closed for p in harness.pipes.values()))
        self.assertTrue(harness.reaped)
        self.assertFalse(harness.alive)
        self.assertFalse(harness.group_alive)
        self.assertEqual(harness.kills, [(800001, signal.SIGKILL)] if killed else [])
        self.assertTrue(all(0 < timeout <= T.ABSOLUTE_SECONDS for timeout in harness.select_timeouts))

    def test_success_uses_only_fixed_argv_and_bounded_secret_stdin(self):
        harness = Harness(partial_write=3)
        self.assertEqual(harness.fetch(), b'{"ok":true}')
        self.assert_clean(harness, killed=False)
        argv, kwargs = harness.commands[0]
        self.assertEqual(argv, [sys.executable, '-I', '-S', '-u', str(Path(T.__file__).resolve())])
        self.assertTrue(kwargs['start_new_session'])
        self.assertTrue(kwargs['close_fds'])
        self.assertEqual(kwargs['bufsize'], 0)
        self.assertNotIn(TOKEN, repr((argv, kwargs)))
        self.assertNotIn(URL, repr((argv, kwargs)))
        self.assertFalse(any('TOKEN' in k or 'PROXY' in k for k in kwargs['env']))
        self.assertTrue(all(len(write) <= 4096 for write in harness.writes))
        self.assertEqual(len(harness.nonblocking), 3)
        self.assertTrue(all(call.args[1] is False for call in harness.nonblocking))

    def test_dns_headers_redirect_body_and_error_body_stalls_share_absolute_deadline(self):
        for stage in ('DNS', 'headers', 'redirect', 'body-read', 'error-body-read'):
            harness = Harness(stage=stage)
            with self.subTest(stage=stage), self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
                harness.fetch()
            self.assertEqual(harness.now, T.ABSOLUTE_SECONDS)
            self.assert_clean(harness)
            self.assertEqual(harness.waits[-1], T.REAP_SECONDS)

    def test_trickle_progress_does_not_reset_absolute_deadline(self):
        harness = Harness(stage='body-read', trickle=True)
        with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
            harness.fetch()
        self.assertEqual(harness.now, T.ABSOLUTE_SECONDS)
        self.assertGreater(len(harness.reads), 1)
        self.assert_clean(harness)

    def test_stdin_backpressure_is_included_in_the_same_absolute_deadline(self):
        harness = Harness(write_blocked=True)
        with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
            harness.fetch()
        self.assertEqual(harness.now, T.ABSOLUTE_SECONDS)
        self.assert_clean(harness)

    def test_launch_elapsed_time_is_not_excluded_from_request_budget(self):
        harness = Harness(launch_delay=31)
        with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
            harness.fetch()
        self.assertEqual(harness.select_timeouts, [])
        self.assert_clean(harness)

    def test_worker_exit_wait_is_bounded_by_remaining_request_deadline(self):
        harness = Harness(wait_hang=True)
        with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
            harness.fetch()
        self.assertEqual(harness.waits, [T.ABSOLUTE_SECONDS, T.REAP_SECONDS])
        self.assert_clean(harness)

    def test_exited_group_leader_does_not_skip_descendant_group_cancellation(self):
        harness = Harness(stage='child-pipe-held-open', leader_exited=True)
        with self.assertRaises(T.SourceHttpError):
            harness.fetch()
        self.assert_clean(harness)

    def test_stdout_one_over_cap_is_killed_before_unbounded_parent_allocation(self):
        harness = Harness(stdout=b'x' * (T.FILE_JSON_BYTES + T.HEADER_BYTES + 1))
        with self.assertRaisesRegex(T.SourceHttpError, 'pipe byte budget'):
            harness.fetch()
        self.assert_clean(harness)
        stdout_reads = [size for fd, size in harness.reads if fd == 100002]
        self.assertTrue(all(size <= T.CHUNK_BYTES for size in stdout_reads))
        self.assertEqual(sum(stdout_reads), T.FILE_JSON_BYTES + T.HEADER_BYTES + 1)

    def test_stderr_one_over_cap_is_killed_and_secret_text_is_never_reported(self):
        harness = Harness(stderr=(TOKEN.encode() + b'x' * T.STDERR_BYTES)[:T.STDERR_BYTES + 1])
        with self.assertRaisesRegex(T.SourceHttpError, 'pipe byte budget') as raised:
            harness.fetch()
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assert_clean(harness)
        self.assertEqual([size for fd, size in harness.reads if fd == 100003], [T.STDERR_BYTES + 1])

    def test_bounded_nonempty_stderr_still_fails_closed_without_echo(self):
        harness = Harness(stderr=TOKEN.encode())
        with self.assertRaisesRegex(T.SourceHttpError, 'unexpected diagnostic') as raised:
            harness.fetch()
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assert_clean(harness, killed=False)

    def test_malformed_worker_protocol_is_not_acknowledged(self):
        for raw in (b'', b'not protocol', T.MAGIC + b'OK 9\n{}', T.MAGIC + b'ERR invented\n',
                    T.MAGIC + b'ERR network\nsecret', T.MAGIC + b'ERR http 999\n',
                    T.MAGIC + b'OK ' + b'9' * 200 + b'\n'):
            harness = Harness(stdout=raw)
            with self.subTest(raw=raw[:40]), self.assertRaises(T.SourceHttpError):
                harness.fetch()
            self.assert_clean(harness, killed=False)

    def test_worker_failure_returncode_fails_without_raw_os_diagnostic(self):
        harness = Harness(exitcode=1)
        with self.assertRaisesRegex(T.SourceHttpError, 'invalid source worker response'):
            harness.fetch()
        self.assert_clean(harness, killed=False)

    def test_pipe_write_failure_cancels_and_reaps_without_secret_echo(self):
        harness = Harness(write_failure=True)
        with self.assertRaisesRegex(T.SourceHttpError, 'pipe failed') as raised:
            harness.fetch()
        self.assertNotIn('secret', str(raised.exception))
        self.assert_clean(harness)

    def test_kill_failure_still_attempts_reap_and_is_explicit_failure(self):
        harness = Harness(stage='DNS', kill_failure=True)
        with self.assertRaisesRegex(T.SourceHttpError, 'could not be reaped') as raised:
            harness.fetch()
        self.assertNotIn('OS secret', str(raised.exception))
        self.assertEqual(harness.waits, [T.REAP_SECONDS])
        self.assertTrue(all(p.closed for p in harness.pipes.values()))
        self.assertTrue(harness.selector_closed)

    def test_reap_failure_is_bounded_explicit_and_never_returns_response(self):
        harness = Harness(stage='DNS', reap_failure=True)
        with self.assertRaisesRegex(T.SourceHttpError, 'could not be reaped'):
            harness.fetch()
        self.assertEqual(harness.now, T.ABSOLUTE_SECONDS + T.REAP_SECONDS)
        self.assertEqual(harness.waits, [T.REAP_SECONDS])
        self.assertTrue(all(p.closed for p in harness.pipes.values()))
        self.assertFalse(harness.reaped)

    def test_non_posix_fails_before_any_worker_launch(self):
        with mock.patch.object(T.os, 'name', 'nt'), mock.patch.object(T.subprocess, 'Popen', side_effect=AssertionError('no launch')):
            with self.assertRaisesRegex(T.SourceHttpError, 'requires POSIX'):
                T.fetch(TOKEN, URL, budget='file')

    def test_worker_launch_failure_contains_no_original_os_error_or_secret(self):
        with mock.patch.object(T.subprocess, 'Popen', side_effect=PermissionError(TOKEN)), \
                mock.patch.object(T.os, 'killpg', side_effect=AssertionError('no child was started')):
            with self.assertRaisesRegex(T.SourceHttpError, 'could not start') as raised:
                T.fetch(TOKEN, URL, budget='file')
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_unreviewed_budget_and_secret_input_bounds_fail_before_launch(self):
        with mock.patch.object(T.subprocess, 'Popen', side_effect=AssertionError('no launch')):
            for budget in (True, 1048576, 'large', None):
                with self.subTest(budget=budget), self.assertRaises(T.SourceHttpError):
                    T.fetch(TOKEN, URL, budget=budget)
            for token in ('', 'x' * (T.TOKEN_BYTES + 1), 'token\r\nAuthorization: bad', '非ASCII'):
                with self.subTest(size=len(token)), self.assertRaises(T.SourceHttpError):
                    T.fetch(token, URL, budget='file')

    def test_endpoint_host_scheme_port_credentials_path_and_budget_are_closed(self):
        bad = (URL.replace('api.github.com', 'api.github.com.evil'), URL.replace('https:', 'http:'),
               URL.replace('api.github.com', 'user@api.github.com'), URL.replace('api.github.com', 'api.github.com:443'),
               URL + '#fragment', URL + '?surprise=1', URL.replace('/git/commits/', '/actions/runs/'),
               URL.replace('/git/commits/', '/git/blobs/'), URL.replace('/repos/', '/repos/%2e%2e/'))
        with mock.patch.object(T.subprocess, 'Popen', side_effect=AssertionError('no launch')):
            for url in bad:
                with self.subTest(url=url), self.assertRaises(T.SourceHttpError):
                    T.fetch(TOKEN, url, budget='file')
            with self.assertRaises(T.SourceHttpError):
                T.fetch(TOKEN, URL, budget='tree')

    def test_worker_redirects_close_body_without_read_or_second_request(self):
        handler = T.RejectRedirect()
        for status in (301, 302, 303, 307, 308):
            body = mock.Mock()
            body.read.side_effect = AssertionError('redirect body must not be read')
            with self.subTest(status=status), self.assertRaisesRegex(T.SourceHttpError, 'redirect forbidden'):
                getattr(handler, 'http_error_' + str(status))(None, body, status, 'remote secret', {'Location': 'https://evil.invalid'})
            body.close.assert_called_once()
            body.read.assert_not_called()

    def test_worker_uses_local_opener_without_environment_proxy_or_redirect_follow(self):
        opener = mock.Mock()
        request = mock.Mock()
        with mock.patch.object(T.urllib.request, 'build_opener', return_value=opener) as build:
            T.open_source_url(request, timeout=30)
        args = build.call_args.args
        self.assertIsInstance(args[0], T.urllib.request.ProxyHandler)
        self.assertEqual(args[0].proxies, {})
        self.assertIsInstance(args[1], T.RejectRedirect)
        opener.open.assert_called_once_with(request, timeout=30)

    def test_worker_input_fields_versions_duplicates_and_byte_budget_are_closed(self):
        value = json.loads(T.make_input(TOKEN, URL, 'file'))
        for changed in (value | {'version': True}, value | {'unknown': 1}, value | {'budget': 'raw'}, value | {'token': None}):
            with self.assertRaises(T.SourceHttpError):
                T.parse_input(json.dumps(changed).encode())
        for raw in (b'x' * (T.INPUT_BYTES + 1), b'{"token":1,"token":2}', b'{"version":NaN}'):
            with self.assertRaises(T.SourceHttpError):
                T.parse_input(raw)

    def test_worker_error_protocol_contains_only_closed_enum_or_bounded_http_status(self):
        for error in (T.SourceHttpError('network'), T.SourceHttpError('http', 403), T.SourceHttpError(TOKEN)):
            raw = T.error_envelope(error)
            self.assertLessEqual(len(raw), T.HEADER_BYTES)
            self.assertNotIn(TOKEN.encode(), raw)
            with self.assertRaises(T.SourceHttpError):
                T.decode_envelope(raw, T.FILE_JSON_BYTES)

    def test_worker_main_input_read_and_output_writes_are_bounded_and_partial_safe(self):
        class Input(io.BytesIO):
            def read(self, size=-1):
                self.requested_size = size
                return super().read(size)
        class Output(io.BytesIO):
            def write(self, raw):
                self.maximum_write = max(getattr(self, 'maximum_write', 0), len(raw))
                return super().write(raw[:7])
        input_stream = Input(T.make_input(TOKEN, URL, 'file'))
        output_stream = Output()
        body = b'x' * 100000
        with mock.patch.object(T.sys, 'stdin', SimpleNamespace(buffer=input_stream)), \
                mock.patch.object(T.sys, 'stdout', SimpleNamespace(buffer=output_stream)), \
                mock.patch.object(T, 'perform_request', return_value=body):
            result = T.worker_main()
        self.assertEqual(result, 0)
        self.assertEqual(input_stream.requested_size, T.INPUT_BYTES + 1)
        self.assertLessEqual(output_stream.maximum_write, T.CHUNK_BYTES)
        self.assertEqual(T.decode_envelope(output_stream.getvalue(), T.FILE_JSON_BYTES), body)

    def test_worker_main_rejects_oversized_input_or_body_without_secret_diagnostics(self):
        for raw, body in ((b'x' * (T.INPUT_BYTES + 1), b''),
                          (T.make_input(TOKEN, URL, 'file'), b'x' * (T.FILE_JSON_BYTES + 1))):
            output = io.BytesIO()
            with mock.patch.object(T.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw))), \
                    mock.patch.object(T.sys, 'stdout', SimpleNamespace(buffer=output)), \
                    mock.patch.object(T, 'perform_request', return_value=body):
                self.assertEqual(T.worker_main(), 0)
            envelope = output.getvalue()
            self.assertLessEqual(len(envelope), T.HEADER_BYTES)
            self.assertNotIn(TOKEN.encode(), envelope)
            with self.assertRaises(T.SourceHttpError):
                T.decode_envelope(envelope, T.FILE_JSON_BYTES)

    def test_worker_broken_stdout_returns_failure_without_os_traceback(self):
        output = mock.Mock()
        output.write.side_effect = BrokenPipeError(TOKEN)
        with mock.patch.object(T.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(T.make_input(TOKEN, URL, 'file')))), \
                mock.patch.object(T.sys, 'stdout', SimpleNamespace(buffer=output)), \
                mock.patch.object(T.sys, 'stderr', mock.Mock()) as stderr, \
                mock.patch.object(T, 'perform_request', return_value=b'{}'):
            self.assertEqual(T.worker_main(), 1)
        stderr.write.assert_not_called()

    def test_approved_contents_paths_match_issuer_control_paths_exactly(self):
        spec = importlib.util.spec_from_file_location('supervisor_archive', ROOT / 'scripts/verify-actions-log-artifact.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(T.CONTROL_PATHS, frozenset(module.BINDING.CONTROL_PATHS))
        for path in T.CONTROL_PATHS:
            url = URL.split('/git/commits/', 1)[0] + '/contents/' + path + '?ref=' + 'a' * 40
            T.validate_url(url, 'file')

    def test_stdout_payload_declared_over_file_bound_cannot_use_envelope_allowance(self):
        payload = b'x' * (T.FILE_JSON_BYTES + 1)
        harness = Harness(stdout=T.ok_envelope(payload))
        with self.assertRaisesRegex(T.SourceHttpError, 'invalid source worker response'):
            harness.fetch()
        self.assert_clean(harness, killed=False)


if __name__ == '__main__':
    unittest.main()
