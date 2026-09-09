"""Deterministic retained-file and credential-redirect regressions.

Uses temporary synthetic files and real urllib handler dispatch with an offline
transport. No live credential, socket, external file or acceptance decision.
"""
from __future__ import annotations

import base64
import contextlib
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.request
import urllib.response

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/upload-actions-artifact.py'
REAL_OPEN, REAL_READ, REAL_CLOSE = os.open, os.read, os.close
REAL_PATH_READ = Path.read_bytes
REAL_BUILD = urllib.request.build_opener


def module():
    spec = importlib.util.spec_from_file_location('artifact_security_subject', SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError('uploader source is required')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def token():
    payload = base64.urlsafe_b64encode(b'{"scp":"Actions.Results:test-run:test-job"}').decode().rstrip('=')
    return 'fixture.' + payload + '.not-a-live-signature'


class FileCustodyTests(unittest.TestCase):
    def setUp(self):
        self.m = module()
        self.assertTrue(self.m._SECURE_FILE_IO_SUPPORTED, 'secure POSIX file I/O is required')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'input'
        self.directory.mkdir()
        self.file = self.directory / 'artifact.zip'
        self.original = b'ORIGINAL'
        self.file.write_bytes(self.original)
        self.other = self.root / 'other.bin'
        self.other.write_bytes(b'DIFFERNT')

    def read(self):
        return self.m.validate_artifact('fixture-packet', self.file)

    def replace_at_open(self, replacement):
        seen = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            if path == self.file.name and not seen:
                seen.append(flags)
                self.file.unlink()
                replacement()
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        with patch.object(self.m.os, 'open', side_effect=opener), \
             patch.object(self.m.os, 'read', side_effect=AssertionError('changed leaf must not be read')):
            with self.assertRaises(self.m.ArtifactUploadError):
                self.read()
        self.assertEqual(len(seen), 1)
        for flag in (os.O_NOFOLLOW, os.O_NONBLOCK, os.O_CLOEXEC):
            self.assertTrue(seen[0] & flag)

    def test_regular_file_preserves_bytes(self):
        self.assertEqual(self.read(), self.original)

    def test_symlink_substitution_before_open_rejects_without_read(self):
        self.replace_at_open(lambda: self.file.symlink_to(self.other))

    def test_same_size_inode_substitution_rejects_without_read(self):
        self.replace_at_open(lambda: self.other.rename(self.file))

    def test_directory_substitution_rejects_without_read(self):
        self.replace_at_open(self.file.mkdir)

    def test_fifo_substitution_rejects_without_read(self):
        self.replace_at_open(lambda: os.mkfifo(self.file))

    def test_empty_or_oversize_leaf_rejects_before_read(self):
        for data in (b'', b'123456789'):
            with self.subTest(size=len(data)):
                self.file.write_bytes(data)
                with patch.object(self.m, 'MAX_ARTIFACT_BYTES', 8), \
                     patch.object(self.m.os, 'read', side_effect=AssertionError('must not read')):
                    with self.assertRaises(self.m.ArtifactUploadError):
                        self.read()

    def test_already_linked_or_special_leaf_is_rejected(self):
        for kind in ('symlink', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if self.file.exists() or self.file.is_symlink():
                    self.file.unlink()
                if kind == 'symlink':
                    self.file.symlink_to(self.other)
                elif kind == 'fifo':
                    os.mkfifo(self.file)
                else:
                    self.file.mkdir()
                with patch.object(self.m.os, 'read', side_effect=AssertionError('must not read')):
                    with self.assertRaises(self.m.ArtifactUploadError):
                        self.read()
                if kind == 'directory':
                    self.file.rmdir()

    def test_fifo_is_bounded_in_a_separate_process(self):
        self.file.unlink()
        os.mkfifo(self.file)
        program = (
            'import importlib.util,pathlib;'
            f's=importlib.util.spec_from_file_location("u", {str(SCRIPT)!r});'
            'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
            f'm.validate_artifact("fixture", pathlib.Path({str(self.file)!r}))'
        )
        result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('regular non-symlink', result.stderr)

    def test_growth_reads_at_most_inspected_size_plus_one(self):
        requests = []
        def reader(fd, maximum):
            if not requests:
                with self.file.open('ab') as f:
                    f.write(b'x' * 4096)
            requests.append(maximum)
            return REAL_READ(fd, maximum)
        with patch.object(self.m.os, 'read', side_effect=reader):
            with self.assertRaises(self.m.ArtifactUploadError):
                self.read()
        self.assertEqual(sum(requests), len(self.original) + 1)

    def test_truncation_before_read_rejects(self):
        seen = []
        def reader(fd, maximum):
            if not seen:
                seen.append(True)
                self.file.write_bytes(b'X')
            return REAL_READ(fd, maximum)
        with patch.object(self.m.os, 'read', side_effect=reader):
            with self.assertRaises(self.m.ArtifactUploadError):
                self.read()
        self.assertEqual(seen, [True])

    def mutate_after_bytes(self, callback):
        fired = []
        def reader(fd, maximum):
            block = REAL_READ(fd, maximum)
            if block and not fired:
                fired.append(True)
                callback()
            return block
        with patch.object(self.m.os, 'read', side_effect=reader):
            with self.assertRaises(self.m.ArtifactUploadError):
                self.read()
        self.assertEqual(fired, [True])

    def test_equal_length_inplace_change_after_bytes_rejects(self):
        def mutate():
            stamp = self.file.stat().st_mtime_ns
            self.file.write_bytes(b'DIFFERNT')
            os.utime(self.file, ns=(stamp + 1000000000, stamp + 1000000000))
        self.mutate_after_bytes(mutate)

    def test_path_replacement_after_bytes_rejects(self):
        def mutate():
            self.file.rename(self.directory / 'held.zip')
            self.other.rename(self.file)
        self.mutate_after_bytes(mutate)

    def test_unlink_after_bytes_rejects(self):
        self.mutate_after_bytes(self.file.unlink)

    def test_metadata_change_after_bytes_rejects(self):
        self.mutate_after_bytes(lambda: self.file.chmod(0o600 if self.file.stat().st_mode & 0o077 else 0o644))

    def test_no_path_based_read_bytes_fallback(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('path reopen is forbidden')):
            self.assertEqual(self.read(), self.original)

    def test_chunks_and_extra_byte_probe_are_bounded(self):
        requests = []
        def reader(fd, maximum):
            requests.append(maximum)
            return REAL_READ(fd, maximum)
        with patch.object(self.m, 'READ_CHUNK_BYTES', 3), patch.object(self.m.os, 'read', side_effect=reader):
            self.assertEqual(self.read(), self.original)
        self.assertEqual(requests, [3, 3, 2, 1])

    def test_parent_symlink_swap_before_open_rejects(self):
        seen = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            if path == 'input' and not seen:
                seen.append(True)
                self.directory.rename(self.root / 'held')
                self.directory.symlink_to(self.root, target_is_directory=True)
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        with patch.object(self.m.os, 'open', side_effect=opener):
            with self.assertRaises(self.m.ArtifactUploadError):
                self.read()

    def test_opened_parent_stays_pinned(self):
        seen = []
        def opener(path, flags, mode=0o777, *, dir_fd=None):
            fd = REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
            if path == 'input' and not seen:
                seen.append(True)
                self.directory.rename(self.root / 'held')
                self.directory.symlink_to(self.root, target_is_directory=True)
                (self.root / 'artifact.zip').write_bytes(b'DIFFERNT')
            return fd
        with patch.object(self.m.os, 'open', side_effect=opener):
            self.assertEqual(self.read(), self.original)
        self.assertEqual(seen, [True])

    def check_cleanup(self, fail):
        active = set()
        def opener(*args, **kwargs):
            fd = REAL_OPEN(*args, **kwargs)
            active.add(fd)
            return fd
        def closer(fd):
            active.remove(fd)
            return REAL_CLOSE(fd)
        def reader(fd, maximum):
            if fail:
                raise OSError('SYNTHETIC-PATH-AND-SECRET')
            return REAL_READ(fd, maximum)
        with patch.object(self.m.os, 'open', side_effect=opener), \
             patch.object(self.m.os, 'close', side_effect=closer), \
             patch.object(self.m.os, 'read', side_effect=reader):
            if fail:
                with self.assertRaises(self.m.ArtifactUploadError) as raised:
                    self.read()
                self.assertNotIn('SYNTHETIC', str(raised.exception))
                self.assertNotIn(str(self.file), str(raised.exception))
            else:
                self.assertEqual(self.read(), self.original)
        self.assertEqual(active, set())

    def test_descriptors_closed_on_success(self):
        self.check_cleanup(False)

    def test_descriptors_closed_and_errors_redacted_on_failure(self):
        self.check_cleanup(True)

    def test_unsupported_platform_fails_without_fallback(self):
        with patch.object(self.m, '_SECURE_FILE_IO_SUPPORTED', False), \
             patch.object(self.m.os, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaises(self.m.ArtifactUploadError):
                self.read()

    def test_invalid_name_or_traversal_rejects_before_io(self):
        with patch.object(self.m.os, 'open', side_effect=AssertionError('must not open')):
            for name, path in ((None, self.file), ('bad/name', self.file), ('good', self.directory / '..' / 'other.bin')):
                with self.subTest(name=name), self.assertRaises(self.m.ArtifactUploadError):
                    self.m.validate_artifact(name, path)

    def test_missing_leaf_has_no_path_diagnostic(self):
        self.file.unlink()
        with self.assertRaises(self.m.ArtifactUploadError) as raised:
            self.read()
        self.assertEqual(str(raised.exception), 'secure artifact file I/O failed')


class OfflineTransport(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Replace only network I/O; use real OpenerDirector redirect dispatch."""
    def __init__(self, phase=None, code=302, target='https://other.example.invalid/target'):
        super().__init__()
        self.phase, self.code, self.target = phase, code, target
        self.requests, self.responses = [], []

    def http_open(self, request):
        self.requests.append(request)
        phase = ('create' if request.full_url.endswith('/CreateArtifact') else
                 'finalize' if request.full_url.endswith('/FinalizeArtifact') else 'put')
        headers = Message()
        if phase == self.phase:
            headers['Location'] = self.target
            status, data = self.code, b'redirect-body-must-not-be-consumed'
        elif phase == 'create':
            status, data = 200, b'{"ok":true,"signed_upload_url":"https://blob.example.invalid/data?sig=synthetic"}'
        elif phase == 'finalize':
            status, data = 200, b'{"ok":true,"artifact_id":"123"}'
        else:
            status, data = 201, b''
        response = urllib.response.addinfourl(io.BytesIO(data), headers, request.full_url, status)
        response.msg = 'offline fixture'
        self.responses.append(response)
        return response

    https_open = http_open


class RedirectBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.m = module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'artifact.bin'
        self.path.write_bytes(b'synthetic archive')

    def upload_default(self, transport, sleeps):
        def build(*handlers):
            self.assertTrue(any(isinstance(h, self.m._RejectArtifactRedirects) for h in handlers))
            return REAL_BUILD(*handlers, urllib.request.ProxyHandler({}), transport)
        # urlopen must not be used, even if a global opener has been installed.
        with patch.object(self.m.urllib.request, 'build_opener', side_effect=build), \
             patch.object(self.m.urllib.request, 'urlopen', side_effect=AssertionError('unsafe global opener')), \
             contextlib.redirect_stdout(io.StringIO()):
            return self.m.upload_artifact('fixture-packet', self.path, 'application/zip',
                runtime_token=token(), actions_results_url='https://results.example.invalid',
                sleeper=sleeps.append)

    def redirects(self, phase, calls):
        origin = 'blob.example.invalid' if phase == 'put' else 'results.example.invalid'
        targets = (
            ('cross-origin', 'https://other.example.invalid/target'),
            ('downgrade', 'http://other.example.invalid/target'),
            ('same-origin', f'https://{origin}/same-origin'),
            ('relative', '/relative'),
        )
        for code in (301, 302, 303, 307, 308):
            for kind, target in targets:
                with self.subTest(phase=phase, code=code, target=target, kind=kind):
                    transport = OfflineTransport(phase, code, target)
                    sleeps = []
                    with self.assertRaises(self.m.ArtifactUploadError) as raised:
                        self.upload_default(transport, sleeps)
                    self.assertEqual(len(transport.requests), calls)
                    # Verify the label against the actual outgoing request, not
                    # a shared Results origin that would mislabel signed PUT.
                    requested = urllib.parse.urlsplit(transport.requests[-1].full_url)
                    destination = urllib.parse.urlsplit(target)
                    self.assertEqual(requested.netloc, origin)
                    if kind == 'same-origin':
                        self.assertEqual((requested.scheme, requested.netloc),
                                         (destination.scheme, destination.netloc))
                    elif kind == 'cross-origin':
                        self.assertEqual(requested.scheme, destination.scheme)
                        self.assertNotEqual(requested.netloc, destination.netloc)
                    elif kind == 'downgrade':
                        self.assertEqual((requested.scheme, destination.scheme), ('https', 'http'))
                    else:
                        self.assertFalse(destination.scheme or destination.netloc)
                    self.assertEqual(sleeps, [])
                    self.assertTrue(transport.responses[-1].closed)
                    self.assertEqual(str(raised.exception), 'artifact service redirects are forbidden')
                    self.assertNotIn(token(), str(raised.exception))
                    for request in transport.requests:
                        if request.get_method() == 'PUT':
                            self.assertFalse(request.has_header('Authorization'))

    def test_all_create_redirect_codes_and_origins_rejected(self):
        self.redirects('create', 1)

    def test_all_finalize_redirect_codes_and_origins_rejected(self):
        self.redirects('finalize', 3)

    def test_signed_put_redirects_do_not_replay_bytes(self):
        self.redirects('put', 2)

    def test_real_default_opener_success_keeps_credentials_origin_scoped(self):
        transport = OfflineTransport()
        result = self.upload_default(transport, [])
        self.assertEqual(result['artifact_id'], '123')
        self.assertEqual(len(transport.requests), 3)
        for index in (0, 2):
            request = transport.requests[index]
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + token())
            self.assertNotIn('Authorization', request.headers)
            self.assertIn('Authorization', request.unredirected_hdrs)
        self.assertFalse(transport.requests[1].has_header('Authorization'))

    def test_token_header_is_not_copied_even_by_standard_redirect_handler(self):
        transport = OfflineTransport()
        self.upload_default(transport, [])
        request = transport.requests[0]
        for target in ('https://other.example.invalid/x', 'http://other.example.invalid/x'):
            redirected = urllib.request.HTTPRedirectHandler().redirect_request(request, None, 302, 'Found', {}, target)
            self.assertFalse(redirected.has_header('Authorization'))

    def test_default_opener_does_not_change_global_urlopen(self):
        previous = urllib.request._opener
        self.upload_default(OfflineTransport(), [])
        self.assertIs(urllib.request._opener, previous)

    def test_redirect_request_override_also_fails_closed(self):
        request = urllib.request.Request('https://results.example.invalid', data=b'{}')
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code), self.assertRaises(self.m.ArtifactUploadError):
                self.m._RejectArtifactRedirects().redirect_request(request, None, code, 'Found', {}, 'http://other.example.invalid')


if __name__ == '__main__':
    unittest.main()
