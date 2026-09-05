"""Real uploader exercised through offline replies; no live credentials or service."""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/upload-actions-artifact.py'
GOOD_CREATE = b'{"ok":true,"signed_upload_url":"https://blob.example.invalid/data"}'
GOOD_FINAL = b'{"ok":true,"artifact_id":"123"}'
TOKEN = 'synthetic.' + base64.urlsafe_b64encode(b'{"scp":"Actions.Results:run:job"}').decode().rstrip('=') + '.synthetic'


def subject():
    spec = importlib.util.spec_from_file_location('uploader_response_subject', SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError('uploader source is required')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Response:
    def __init__(self, body, status=200):
        self.stream = io.BytesIO(body)
        self.status = status
        self.requests = []
        self.closed = False

    def read(self, maximum):
        self.requests.append(maximum)
        return self.stream.read(maximum)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        self.stream.close()
        return False


class Service:
    def __init__(self, create=GOOD_CREATE, final=GOOD_FINAL, put=b''):
        self.create, self.final, self.put = create, final, put
        self.calls = []
        self.responses = []

    def __call__(self, request, timeout):
        assert timeout == 60
        self.calls.append(request)
        if request.full_url.endswith('/CreateArtifact'):
            response = Response(self.create)
        elif request.get_method() == 'PUT':
            response = Response(self.put, 201)
        elif request.full_url.endswith('/FinalizeArtifact'):
            response = Response(self.final)
        else:
            raise AssertionError('unexpected transport request')
        self.responses.append(response)
        return response


class ResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.m = subject()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.zip'
        self.path.write_bytes(b'synthetic nonsecret archive')
        self.sleeps = []

    def upload(self, service):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.m.upload_artifact('fixture-packet', self.path, 'application/zip',
                runtime_token=TOKEN, actions_results_url='https://results.example.invalid',
                opener=service, sleeper=self.sleeps.append)

    def reject(self, body, phase='final', expected_calls=None):
        if expected_calls is None:
            expected_calls = 1 if phase == 'create' else 3
        service = Service(**{phase: body})
        with self.assertRaises(self.m.ArtifactUploadError):
            self.upload(service)
        self.assertEqual(len(service.calls), expected_calls)
        self.assertEqual(self.sleeps, [])
        self.assertTrue(all(r.closed for r in service.responses))

    def reject_id(self, value):
        self.reject(json.dumps({'ok': True, 'artifact_id': value}).encode())

    def test_canonical_string_id_succeeds(self):
        self.assertEqual(self.upload(Service())['artifact_id'], '123')

    def test_positive_integer_id_succeeds(self):
        self.assertEqual(self.upload(Service(final=b'{"ok":true,"artifact_id":123}'))['artifact_id'], '123')

    def test_int64_maximum_succeeds_without_rounding(self):
        for value in (2**63-1, str(2**63-1)):
            with self.subTest(value=value):
                service = Service(final=json.dumps({'ok': True, 'artifact_id': value}).encode())
                self.assertEqual(self.upload(service)['artifact_id'], str(2**63-1))

    def test_camel_case_response_names_succeed(self):
        service = Service(create=b'{"ok":true,"signedUploadUrl":"https://blob.example.invalid/data"}',
                          final=b'{"ok":true,"artifactId":"123"}')
        self.assertEqual(self.upload(service)['artifact_id'], '123')

    def test_boolean_ids_rejected(self):
        for value in (True, False):
            with self.subTest(value=value): self.reject_id(value)

    def test_nonpositive_ids_rejected(self):
        for value in (0, -1, '0', '-1', '-0'):
            with self.subTest(value=value): self.reject_id(value)

    def test_overflow_ids_rejected(self):
        for value in (2**63, str(2**63), 2**64):
            with self.subTest(value=value): self.reject_id(value)

    def test_float_null_collection_ids_rejected(self):
        for value in (1.0, None, [], {}):
            with self.subTest(value=value): self.reject_id(value)

    def test_nonnumeric_and_noncanonical_string_ids_rejected(self):
        for value in ('not-an-id', '', ' 1', '1 ', '+1', '01', '1e3', '1.0', '\u0661'):
            with self.subTest(value=value): self.reject_id(value)

    def test_embedded_output_lines_rejected(self):
        for value in ('1\nother_output=synthetic', '1\rother_output=synthetic', '1\x00', '1\t'):
            with self.subTest(value=repr(value)): self.reject_id(value)

    def test_main_cannot_write_injected_output_line(self):
        output = Path(self.temp.name) / 'github-output'
        output.write_text('existing=preserved\n')
        service = Service(final=json.dumps({'ok':True,'artifact_id':'123\ninjected_output=SYNTHETIC'}).encode())
        args = SimpleNamespace(name='fixture',path=self.path,mime_type='application/zip')
        with patch.object(self.m,'parse_args',return_value=args), \
             patch.object(self.m,'_no_redirect_opener',return_value=service), \
             patch.dict(os.environ,{'ACTIONS_RUNTIME_TOKEN':TOKEN,'ACTIONS_RESULTS_URL':'https://results.example.invalid','GITHUB_OUTPUT':str(output)}), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(self.m.main(),1)
        self.assertEqual(output.read_text(),'existing=preserved\n')
        self.assertNotIn('injected_output',stdout.getvalue()+stderr.getvalue())
        self.assertNotIn(TOKEN,stdout.getvalue()+stderr.getvalue())

    def test_main_valid_output_remains_exactly_three_lines(self):
        output = Path(self.temp.name) / 'github-output'
        args = SimpleNamespace(name='fixture',path=self.path,mime_type='application/zip')
        with patch.object(self.m,'parse_args',return_value=args), \
             patch.object(self.m,'_no_redirect_opener',return_value=Service()), \
             patch.dict(os.environ,{'ACTIONS_RUNTIME_TOKEN':TOKEN,'ACTIONS_RESULTS_URL':'https://results.example.invalid','GITHUB_OUTPUT':str(output)}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(),0)
        lines=output.read_text().splitlines()
        self.assertEqual(len(lines),3)
        self.assertEqual(lines[0],'artifact_id=123')
        self.assertRegex(lines[1],r'^sha256:[0-9a-f]{64}$'.replace(':','='))
        self.assertEqual(lines[2],f'size_bytes={self.path.stat().st_size}')

    def test_duplicate_ok_cannot_override_refusal(self):
        self.reject(b'{"ok":false,"ok":true,"artifact_id":"123"}')

    def test_escaped_duplicate_keys_rejected(self):
        self.reject(b'{"ok":true,"artifact_id":"123","artifact_\\u0069d":"456"}')

    def test_duplicate_id_even_equal_rejected(self):
        self.reject(b'{"ok":true,"artifact_id":"123","artifact_id":"123"}')

    def test_conflicting_or_equal_aliases_rejected(self):
        for value in ('123','456'):
            with self.subTest(value=value):
                self.reject(json.dumps({'ok':True,'artifact_id':'123','artifactId':value}).encode())

    def test_duplicate_or_conflicting_create_url_rejected_before_put(self):
        for body in (
            b'{"ok":true,"signed_upload_url":"https://a.invalid/x","signed_upload_url":"https://b.invalid/y"}',
            b'{"ok":true,"signed_upload_url":"https://a.invalid/x","signedUploadUrl":"https://b.invalid/y"}',
            b'{"ok":false,"ok":true,"signed_upload_url":"https://a.invalid/x"}',
        ):
            with self.subTest(body=body): self.reject(body,'create')

    def test_nested_duplicate_metadata_rejected(self):
        self.reject(b'{"ok":true,"artifact_id":"123","future":{"a":1,"a":2}}')

    def test_nonfinite_literals_rejected(self):
        for value in ('NaN','Infinity','-Infinity'):
            with self.subTest(value=value):
                self.reject(('{"ok":true,"artifact_id":"123","future":'+value+'}').encode())

    def test_overflowing_json_float_rejected(self):
        self.reject(b'{"ok":true,"artifact_id":"123","future":1e999}')

    def test_finite_unknown_metadata_remains_supported(self):
        self.assertEqual(self.upload(Service(final=b'{"ok":true,"artifact_id":"123","future":{"n":1.25,"a":[1,2]}}'))['artifact_id'],'123')

    def test_excessive_integer_token_rejected(self):
        self.reject(b'{"ok":true,"artifact_id":"123","future":'+b'1'*129+b'}')

    def test_excessive_depth_rejected(self):
        self.reject(b'{"ok":true,"artifact_id":"123","future":'+b'['*64+b'0'+b']'*64+b'}')

    def test_exact_depth_boundary_succeeds(self):
        final=b'{"ok":true,"artifact_id":"123","future":'+b'['*63+b'0'+b']'*63+b'}'
        self.assertEqual(self.upload(Service(final=final))['artifact_id'],'123')

    def test_brackets_and_escaped_quotes_inside_strings_do_not_count(self):
        final=json.dumps({'ok':True,'artifact_id':'123','future':'['*80+'\\"'+']'*80}).encode()
        self.assertEqual(self.upload(Service(final=final))['artifact_id'],'123')

    def test_invalid_json_utf8_and_nonobject_rejected(self):
        for body in (b'{}junk',b'\xff',b'[]',b'null',b'"value"',b''):
            with self.subTest(body=body): self.reject(body)

    def test_response_limit_is_separate_from_artifact_bound(self):
        self.assertEqual(getattr(self.m,'MAX_SERVICE_RESPONSE_BYTES',None),1024*1024)
        self.assertEqual(self.m.MAX_ARTIFACT_BYTES,64*1024*1024)

    def test_oversized_create_stops_before_put_and_retry(self):
        with patch.object(self.m,'MAX_SERVICE_RESPONSE_BYTES',64,create=True):
            self.reject(GOOD_CREATE+b' '*65,'create')

    def test_oversized_finalize_never_returns_receipt(self):
        with patch.object(self.m,'MAX_SERVICE_RESPONSE_BYTES',128,create=True):
            self.reject(GOOD_FINAL+b' '*129)

    def test_oversized_put_ack_stops_before_finalize(self):
        with patch.object(self.m,'MAX_SERVICE_RESPONSE_BYTES',128,create=True):
            self.reject(b' '*129,'put',2)

    def test_exact_response_byte_boundary_succeeds(self):
        with patch.object(self.m,'MAX_SERVICE_RESPONSE_BYTES',128,create=True):
            service=Service(final=GOOD_FINAL+b' '*(128-len(GOOD_FINAL)))
            self.assertEqual(self.upload(service)['artifact_id'],'123')
            self.assertTrue(all(r.requests==[129] for r in service.responses))

    def test_body_reads_never_request_artifact_sized_allocation(self):
        service=Service()
        self.upload(service)
        self.assertTrue(all(r.requests==[1024*1024+1] for r in service.responses))

    def test_permanent_http_error_closes_response_without_body_disclosure(self):
        fp=io.BytesIO(b'SYNTHETIC-SECRET-ERROR-BODY')
        calls=[]
        def opener(request,timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url+'?sig=SYNTHETIC-SECRET',400,'SYNTHETIC-SECRET',None,fp)
        with self.assertRaises(self.m.ArtifactUploadError) as raised:
            self.upload(opener)
        self.assertTrue(fp.closed)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.sleeps,[])
        self.assertNotIn('SYNTHETIC',str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_transient_http_error_is_closed_before_retry(self):
        fp=io.BytesIO(b'SYNTHETIC-ERROR-BODY')
        service=Service()
        calls=[]
        def opener(request,timeout):
            calls.append(request)
            if len(calls)==1:
                raise urllib.error.HTTPError(request.full_url,503,'transient',None,fp)
            self.assertTrue(fp.closed)
            return service(request,timeout)
        self.assertEqual(self.upload(opener)['artifact_id'],'123')
        self.assertEqual(len(calls),4)
        self.assertEqual(len(self.sleeps),1)

    def test_error_status_does_not_read_irrelevant_body(self):
        response=Response(b'SYNTHETIC-SECRET-BODY',400)
        with self.assertRaises(self.m.ArtifactUploadError):
            self.upload(lambda request,timeout:response)
        self.assertEqual(response.requests,[])
        self.assertTrue(response.closed)

    def test_retry_budget_preserved(self):
        errors=[]
        def opener(request,timeout):
            stream=io.BytesIO(b'unused')
            errors.append(stream)
            raise urllib.error.HTTPError(request.full_url,503,'transient',None,stream)
        with self.assertRaises(self.m.ArtifactUploadError): self.upload(opener)
        self.assertEqual(len(errors),5)
        self.assertEqual(len(self.sleeps),4)
        self.assertTrue(all(e.closed for e in errors))


if __name__ == '__main__':
    unittest.main()
