"""Pure delivery-boundary deadline regressions; no processes, DNS or HTTP."""
from __future__ import annotations

import base64
import copy
import gc
import importlib.util
import io
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests/control_plane'))
import schema_source_http as T
from test_schema_source_http_supervisor import Harness, TOKEN, URL
from source_http_mock import mock_source_process
SPEC = importlib.util.spec_from_file_location('delivery_deadline_archive', ROOT / 'scripts/verify-actions-log-artifact.py')
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)
HEAD = 'a' * 40
TREE = 'b' * 40
REPOSITORY = 'TrillionniumFoundation/TrillionniumGame'


class SourceHttpDeliveryDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(T.active_deadline_count(), 0)

    def tearDown(self):
        self.assertEqual(T.active_deadline_count(), 0)

    def test_make_input_31_seconds_rejects_before_any_Popen(self):
        harness = Harness()
        original = T.make_input
        def delayed(*args):
            value = original(*args)
            harness.now += 31
            return value
        with harness.installed(), mock.patch.object(T, 'make_input', side_effect=delayed):
            with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
                T.fetch(TOKEN, URL, budget='file')
        self.assertEqual(harness.commands, [])
        self.assertEqual(harness.kills, [])
        self.assertEqual(harness.waits, [])

    def test_decode_envelope_31_seconds_cannot_deliver_successful_bytes(self):
        harness = Harness()
        original = T.decode_envelope
        def delayed(*args):
            value = original(*args)
            harness.now += 31
            return value
        with harness.installed(), mock.patch.object(T, 'decode_envelope', side_effect=delayed):
            with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
                T.fetch(TOKEN, URL, budget='file')
        self.assertTrue(harness.reaped)
        self.assertTrue(harness.selector_closed)
        self.assertTrue(all(p.closed for p in harness.pipes.values()))
        self.assertFalse(harness.group_alive)

    def test_outer_strict_json_31_seconds_cannot_deliver_successful_dict(self):
        harness = Harness()
        original = A.json.loads
        def delayed(*args, **kwargs):
            value = original(*args, **kwargs)
            harness.now += 31
            return value
        with harness.installed(), mock.patch.object(A.json, 'loads', side_effect=delayed):
            with self.assertRaisesRegex(A.VerificationError, 'absolute request deadline'):
                A.request_source_json(TOKEN, URL, maximum=A.MAX_SOURCE_FILE_JSON_BYTES)
        self.assertTrue(harness.reaped)
        self.assertTrue(all(p.closed for p in harness.pipes.values()))

    def test_final_selector_close_31_seconds_cannot_deliver_after_reaped_worker(self):
        harness = Harness()
        original = harness.close
        def delayed():
            self.assertTrue(harness.reaped)
            original()
            harness.now += 31
        with harness.installed(), mock.patch.object(harness, 'close', side_effect=delayed):
            with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
                T.fetch(TOKEN, URL, budget='file')
        self.assertTrue(harness.selector_closed)
        self.assertTrue(all(p.closed for p in harness.pipes.values()))
        self.assertEqual(harness.kills, [])

    def test_already_expired_owned_deadline_never_spawns(self):
        harness = Harness()
        with harness.installed():
            deadline = T.start_deadline()
            harness.now = 31
            try:
                with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
                    T.fetch(TOKEN, URL, budget='file', deadline=deadline)
            finally:
                T.release_deadline(deadline)
        self.assertEqual(harness.commands, [])

    def test_json_and_worker_share_request_expiration_without_renewal(self):
        harness = Harness(launch_delay=20)
        original = A.json.loads
        def delayed(*args, **kwargs):
            value = original(*args, **kwargs)
            harness.now += 11
            return value
        with harness.installed(), mock.patch.object(A.json, 'loads', side_effect=delayed):
            with self.assertRaisesRegex(A.VerificationError, 'absolute request deadline'):
                A.request_source_json(TOKEN, URL, maximum=A.MAX_SOURCE_FILE_JSON_BYTES)
        self.assertEqual(harness.now, 31)
        self.assertEqual(harness.waits, [10])

    def test_opaque_deadlines_reject_float_inf_TTL_and_unissued_tokens(self):
        harness = Harness()
        with harness.installed():
            token = T.start_deadline()
            try:
                with self.assertRaises(TypeError):
                    type(token)()
                with self.assertRaises(AttributeError):
                    token.expiration = float('inf')
                for forged in (float('inf'), -float('inf'), 600, True, {}, object.__new__(type(token))):
                    with self.subTest(kind=type(forged)), self.assertRaises(T.SourceHttpError):
                        T.fetch(TOKEN, URL, budget='file', deadline=forged)
                with self.assertRaises(TypeError):
                    T.start_deadline(ttl=600)
                for purpose in ('unbounded', float('inf'), True):
                    with self.assertRaises(T.SourceHttpError):
                        T.start_deadline(purpose=purpose)
                for copier in (copy.copy, copy.deepcopy):
                    with self.assertRaises(TypeError):
                        copier(token)
            finally:
                T.release_deadline(token)
        self.assertEqual(harness.commands, [])

    def test_collection_605_seconds_caps_new_request_without_renewing_parent(self):
        harness = Harness()
        with harness.installed():
            collection = T.start_deadline(purpose='collection')
            child = None
            try:
                self.assertEqual(T.MAX_COLLECTION_REQUESTS, 20)
                self.assertEqual(T.COLLECTION_SECONDS, 605)
                harness.now = 600
                child = T.start_deadline(parent=collection)
                self.assertEqual(T.remaining_deadline(child), 5)
                self.assertEqual(T.remaining_deadline(collection), 5)
                harness.now = 604
                late_child = T.start_deadline(parent=collection)
                self.assertEqual(T.remaining_deadline(late_child), 1)
                T.release_deadline(late_child)
                harness.now = 605
                with self.assertRaises(T.SourceHttpError):
                    T.start_deadline(parent=collection)
            finally:
                if child is not None:
                    T.release_deadline(child)
                T.release_deadline(collection)

    def test_direct_fetch_under_collection_still_has_30_second_request_cap(self):
        harness = Harness(stage='DNS')
        with harness.installed():
            collection = T.start_deadline(purpose='collection')
            try:
                with self.assertRaisesRegex(T.SourceHttpError, 'absolute request deadline'):
                    T.fetch(TOKEN, URL, budget='file', deadline=collection)
                self.assertEqual(harness.now, 30)
                self.assertEqual(T.remaining_deadline(collection), 575)
            finally:
                T.release_deadline(collection)
        self.assertTrue(harness.reaped)
        self.assertFalse(harness.group_alive)

    def test_deadline_registry_is_bounded_and_released_in_repeated_requests(self):
        harness = Harness()
        with harness.installed():
            tokens = [T.start_deadline() for _ in range(T.MAX_ACTIVE_DEADLINES)]
            try:
                self.assertEqual(T.active_deadline_count(), T.MAX_ACTIVE_DEADLINES)
                with self.assertRaisesRegex(T.SourceHttpError, 'ownership budget'):
                    T.start_deadline()
            finally:
                for token in tokens:
                    T.release_deadline(token)
        self.assertEqual(T.active_deadline_count(), 0)
        for _ in range(100):
            current = Harness()
            self.assertEqual(current.fetch(), b'{"ok":true}')
            self.assertEqual(T.active_deadline_count(), 0)

    def test_parent_release_invalidates_borrowed_child_and_weak_fallback_is_bounded(self):
        harness = Harness()
        with harness.installed():
            parent = T.start_deadline(purpose='collection')
            child = T.start_deadline(parent=parent)
            T.release_deadline(parent)
            try:
                with self.assertRaises(T.SourceHttpError):
                    T.remaining_deadline(child)
            finally:
                T.release_deadline(child)
            token = T.start_deadline()
            self.assertEqual(T.active_deadline_count(), 1)
            del token
            gc.collect()
            self.assertEqual(T.active_deadline_count(), 0)

    def collection_fixture(self, *, delay_write=False, delay_issuer=False, delay_cleanup=False):
        token = A.BINDING.verify_binding(ROOT, profile='postgresql')
        files = {row['path']: (ROOT / row['path']).read_bytes()
                 for row in A.BINDING.binding_document(token)['full_source_inventory']}
        tree = {'sha': TREE, 'truncated': False, 'tree': [
            {'path': path, 'size': len(raw), 'type': 'blob', 'mode': '100644', 'sha': A.git_blob_sha1(raw)}
            for path, raw in files.items()]}
        clock = SimpleNamespace(now=0)
        requests = []
        last_path = sorted(files)[-1]
        class Response(io.BytesIO):
            def __init__(self, raw):
                super().__init__(raw)
                self.headers = {'Content-Length': str(len(raw))}
            def read(self, size=-1):
                if size < 0:
                    raise AssertionError('unbounded read')
                clock.now += 29
                return super().read(size)
        def open_mock(request, *, timeout):
            requests.append(request.full_url)
            url = urllib.parse.urlsplit(request.full_url)
            if '/git/trees/' in url.path:
                document = tree
            else:
                path = urllib.parse.unquote(url.path.split('/contents/', 1)[1])
                raw = files[path]
                document = {'path': path, 'type': 'file', 'size': len(raw), 'encoding': 'base64',
                            'sha': A.git_blob_sha1(raw), 'content': base64.b64encode(raw).decode()}
            return Response(json.dumps(document).encode())
        original_write = Path.write_bytes
        def write(path, raw):
            result = original_write(path, raw)
            if delay_write and str(path).endswith(last_path):
                clock.now += 55
            return result
        original_issuer = A.BINDING.verify_binding
        def issue(*args, **kwargs):
            if delay_write:
                raise AssertionError('expired collection must not enter issuer')
            result = original_issuer(*args, **kwargs)
            if delay_issuer:
                clock.now += 55
            return result
        original_cleanup = A.tempfile.TemporaryDirectory.cleanup
        def cleanup(directory):
            original_cleanup(directory)
            if delay_cleanup:
                clock.now += 55
        with mock.patch.object(T.time, 'monotonic', side_effect=lambda: clock.now), \
                mock_source_process(T), mock.patch.object(T, 'open_source_url', side_effect=open_mock), \
                mock.patch.object(Path, 'write_bytes', write), \
                mock.patch.object(A.BINDING, 'verify_binding', side_effect=issue) as issuer, \
                mock.patch.object(A.tempfile.TemporaryDirectory, 'cleanup', cleanup):
            with self.assertRaises(A.VerificationError):
                A.fetch_profile_bindings(TOKEN, REPOSITORY, HEAD, head_tree=TREE)
        self.assertEqual(len(requests), 19)  # tree + all18, each completed in29s
        self.assertEqual(clock.now, 606)
        return issuer.call_count

    def test_expired_collection_after_all18_files_does_not_enter_issuer(self):
        self.assertEqual(self.collection_fixture(delay_write=True), 0)

    def test_expiration_during_issuer_never_delivers_tokens_or_starts_second_issuer(self):
        self.assertEqual(self.collection_fixture(delay_issuer=True), 1)

    def test_collection_cleanup_expiration_never_delivers_valid_tokens(self):
        self.assertEqual(self.collection_fixture(delay_cleanup=True), 2)

    def test_nonfinite_clock_fails_closed_without_spawning_or_leaking_ownership(self):
        for clock in (float('inf'), float('nan'), True):
            with mock.patch.object(T.time, 'monotonic', return_value=clock), \
                    mock.patch.object(T.subprocess, 'Popen', side_effect=AssertionError('no launch')):
                with self.assertRaises(T.SourceHttpError):
                    T.fetch(TOKEN, URL, budget='file')


if __name__ == '__main__':
    unittest.main()
