#!/usr/bin/env python3
"""Trusted bounded proposal generator; publishes Git blobs only, never refs."""
from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def trusted_template(name, expected):
    path = Path(__file__).resolve().parent / name
    data = path.read_bytes()
    if len(data) > 65536 or hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != expected:
        raise ValueError("trusted source template identity mismatch")
    return data.decode("utf-8")


HELPER = trusted_template("workflow_trigger_contract.py", "c3f10682c46583e2f0c2b97e38285741ff938eaa")
TESTS = trusted_template("test_required_workflow_source_contract.py", "7ed522b26a3eabeba6d18a16f590ef61b311e7b0")
REPOSITORY = 'TrillionniumFoundation/TrillionniumGame'
BASE = '395d80cfb5582f1e8dbf07184412a56bfa4fb680'
BASE_TREE = '32c9dbef0cd653f30a557e157cada4b37a810e19'
MANIFEST = 'docs/governance/REQUIRED_WORKFLOWS_V1.json'
MANIFEST_BLOB = '686662868d8a88abcafaedb3ac1d81ecb72c0935'
OVERLAY = 'docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json'
CRDB = '.github/workflows/cockroach-serialization-retry.yml'
OLD_CRDB = '612b41c49637b68fa133ec64f40e0a14f85c9349'
ACTUAL_CRDB = '881bbfc9eaffb179fac9f150e17bf21579cbeb17'
EXTRA_PATHS = {'scripts/workflow_trigger_contract.py', 'tests/control_plane/test_required_workflow_source_contract.py', OVERLAY, 'docs/GOVERNANCE.md', 'docs/DOCUMENTATION_AUTHORITY.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def load_json(data):
    return json.loads(data, object_pairs_hook=unique)


def git_blob(payload):
    return hashlib.sha1(f'blob {len(payload)}\0'.encode() + payload).hexdigest()


def digest(value):
    payload = copy.deepcopy(value)
    payload.pop('overlay_sha256', None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def regular(root, path):
    require(path in EXTRA_PATHS or re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', path) is not None or path == MANIFEST,
            'path is outside source-materialization allowlist')
    target = root
    for part in Path(path).parts:
        target = target / part
        require(not target.is_symlink(), 'symlink is not a source authority')
    require(target.is_file(), 'expected regular source file is absent: ' + path)
    with target.open('rb') as stream:
        payload = stream.read(1024 * 1024 + 1)
    require(len(payload) <= 1024 * 1024, 'source file exceeds size bound')
    payload.decode('utf-8')
    return payload


def revision(text):
    matches = list(re.finditer(r'^Revision: [0-9]{4}-[0-9]{2}-[0-9]{2}[ \t]*$', text, re.M))
    require(len(matches) == 1, 'governance revision must be singular')
    return text[:matches[0].start()] + 'Revision: 2026-09-05' + text[matches[0].end():]


def generate(root, output):
    head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    tree = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD^{tree}'], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    require(head == BASE and tree == BASE_TREE, 'source checkout identity mismatch')
    payload = regular(root, MANIFEST)
    require(git_blob(payload) == MANIFEST_BLOB, 'immutable base manifest drift')
    base = load_json(payload)
    overlay = load_json(regular(root, OVERLAY))
    require(base['repository'] == REPOSITORY and base['event'] == 'pull_request', 'base authority mismatch')
    require(overlay['schema'] == 'trnm_required_workflow_overlay_v1', 'overlay schema mismatch')
    require(overlay['repository'] == REPOSITORY and overlay['event'] == 'pull_request', 'overlay authority mismatch')
    require(overlay['base_manifest_path'] == MANIFEST and overlay['base_manifest_blob_sha1'] == MANIFEST_BLOB, 'base binding mismatch')
    require(overlay['overlay_sha256'] == digest(overlay), 'existing overlay digest mismatch')
    require(overlay['remove_workflow_ids'] == [], 'workflow removals are not authorized')
    rows = copy.deepcopy(base['workflows'])
    by_id = {row['workflow_id']: row for row in rows}
    require(len(by_id) == len(rows), 'duplicate base workflow IDs')
    for replacement in overlay['replace_workflows']:
        old = by_id.get(replacement['workflow_id'])
        require(old is not None and old['name'] == replacement['name'] and old['path'] == replacement['path'], 'replacement identity drift')
        by_id[replacement['workflow_id']] = copy.deepcopy(replacement)
    for addition in overlay['add_workflows']:
        require(addition['workflow_id'] not in by_id, 'duplicate addition')
        by_id[addition['workflow_id']] = copy.deepcopy(addition)
    require(len(by_id) == 55 == overlay['composed_external_workflow_count'], 'closed workflow denominator changed')
    require(len({row['path'] for row in by_id.values()}) == 55 and len({row['name'] for row in by_id.values()}) == 55, 'ambiguous workflow identity')

    # Execute only the embedded, locally reviewed helper, never source checkout code.
    module = {'__name__': 'trusted_trigger_contract'}
    exec(compile(HELPER, '<trusted_trigger_contract>', 'exec'), module)
    convert = module['remove_required_pr_selectors']
    validate = module['validate_required_pr_trigger']
    changes = {}
    report = []
    for row in sorted(by_id.values(), key=lambda value: value['path']):
        path = row['path']
        original = regular(root, path)
        observed = git_blob(original)
        expected = row['git_blob_sha1']
        require(observed == expected or (path == CRDB and observed == ACTUAL_CRDB and expected == OLD_CRDB), 'unreviewed workflow definition drift: ' + path)
        text = original.decode('utf-8')
        require(re.search(r'^\s+(?:contents|actions|id-token|pull-requests|issues|packages|deployments):\s*write\s*$', text, re.M) is None, 'required workflow has write permission: ' + path)
        require(not re.search(r'secrets\.(?!GITHUB_TOKEN\b)[A-Za-z_]', text), 'unexpected secret access in required workflow: ' + path)
        transformed, selectors = convert(text)
        validate(transformed)
        encoded = transformed.encode('utf-8')
        if encoded != original:
            changes[path] = encoded
        row['git_blob_sha1'] = git_blob(encoded)
        if row['name'] in {'pg-tls-rotation', 'cockroach-serialization-retry'}:
            row['minimum_successful_execution_jobs'] = max(2, row.get('minimum_successful_execution_jobs', 1))
        if selectors or observed != expected:
            report.append({'path': path, 'removed_pr_selectors': list(selectors), 'old_blob': observed, 'new_blob': row['git_blob_sha1'], 'stale_binding_reconciled': observed != expected})

    aggregate = base['aggregate_workflow']
    aggregate_bytes = regular(root, aggregate['path'])
    require(git_blob(aggregate_bytes) == aggregate['git_blob_sha1'], 'aggregate definition drift')
    validate(aggregate_bytes.decode())
    base_ids = {row['workflow_id']: row for row in base['workflows']}
    overlay['replace_workflows'] = [row for wid, row in sorted(by_id.items()) if wid in base_ids and row != base_ids[wid]]
    overlay['add_workflows'] = [row for wid, row in sorted(by_id.items()) if wid not in base_ids]
    overlay['overlay_sha256'] = digest(overlay)
    changes[OVERLAY] = (json.dumps(overlay, sort_keys=True, separators=(',', ':')) + '\n').encode()
    changes['scripts/workflow_trigger_contract.py'] = HELPER.encode()
    changes['tests/control_plane/test_required_workflow_source_contract.py'] = TESTS.encode()

    doc_path = 'docs/GOVERNANCE.md'
    text = regular(root, doc_path).decode()
    require('## 3. Pull request state' in text and '### Required workflow trigger contract' not in text, 'unexpected governance document layout')
    paragraph = '''### Required workflow trigger contract

The closed required-workflow set applies to every candidate head, not only to changed paths. A mandatory `pull_request` workflow must not have `paths`, `paths-ignore`, `branches`, or `branches-ignore` selectors. Explicit activity types must include `opened`, `synchronize`, and `reopened`. Main-push selectors and all job bodies, permissions, test assertions and evidence requirements remain separate and unchanged. This intentionally trades additional CI execution for complete exact-head qualification; optimization requires an independently reviewed scope-aware evidence contract, never silently treating absent execution as success.

`python3 -m unittest tests.control_plane.test_required_workflow_source_contract -v` checks the real composed manifest against current workflow bytes, verifies every required PR trigger, and requires both source/unit and live jobs for the PostgreSQL TLS and CockroachDB retry lanes. The bounded trigger helper rejects ambiguous forms rather than acting as a general YAML parser; the existing workflow syntax policy remains mandatory. The immutable base manifest is not rewritten. Definition changes are bound through the existing digest-verified overlay, without changing workflow identity, removing required workflows, or granting old-head evidence credit.

GitHub's repository workflow catalog may temporarily expose the full registered path as its display name. The catalog identity helper permits only that exact form, and only when active ID/path, a successful current-head PR run, its canonical name, and the current regular source definition's Git blob all agree. Other renamed, disabled, missing, stale or substituted workflows reject. Receipts preserve the original observed catalog name. This does not relax job/assertion verification, rerun freshness, independent review or production gates.

'''
    text = revision(text).replace('## 3. Pull request state', paragraph + '## 3. Pull request state', 1)
    changes[doc_path] = text.encode()
    authority = load_json(regular(root, 'docs/DOCUMENTATION_AUTHORITY.json'))
    require(doc_path in authority['current_human_documents'], 'governance topic not registered')
    authority.setdefault('document_revisions', {})[doc_path] = '2026-09-05'
    changes['docs/DOCUMENTATION_AUTHORITY.json'] = (json.dumps(authority, indent=2) + '\n').encode()

    # Pure tests use only reviewed templates and stdlib; no repository imports.
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        (fixture / 'scripts').mkdir()
        (fixture / 'tests/control_plane').mkdir(parents=True)
        (fixture / 'scripts/workflow_trigger_contract.py').write_text(HELPER)
        test_file = fixture / 'tests/control_plane/test_required_workflow_source_contract.py'
        test_file.write_text(TESTS)
        done = subprocess.run([sys.executable, '-I', str(test_file), 'RequiredWorkflowTriggerPureTests', '-v'], capture_output=True, text=True, timeout=30)
        require(done.returncode == 0 and 'Ran 12 tests' in done.stderr and '\nOK\n' in done.stderr, 'trusted pure regression suite failed: ' + done.stderr[-3000:])
    require(5 <= len(changes) <= 65, 'unexpected source patch cardinality')
    require(sum(map(len, changes.values())) < 16 * 1024 * 1024, 'patch size bound')
    entries = []
    for path, data in sorted(changes.items()):
        if path.endswith('.py'):
            ast.parse(data)
        target = root / path
        old = git_blob(regular(root, path)) if target.exists() else None
        entries.append({'path': path, 'old_blob': old, 'sha': git_blob(data), 'content': data.decode(), 'mode': '100644'})
    proposal = {'schema': 'trnm_trusted_source_materialization_v1', 'repository': REPOSITORY, 'base_commit': BASE, 'base_tree': BASE_TREE,
                'tooling_commit': os.environ.get('GITHUB_SHA'), 'run_id': os.environ.get('GITHUB_RUN_ID'), 'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                'required_external_count': 55, 'pure_tests_passed': 12, 'live_evidence': False, 'accepted': False,
                'workflow_transformations': report, 'entries': entries}
    output.write_text(json.dumps(proposal, sort_keys=True, separators=(',', ':')))
    print(json.dumps({'generated_files': len(entries), 'workflow_transformations': len(report), 'pure_tests_passed': 12, 'base_commit': BASE, 'refs_written': False}))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('authenticated blob API redirects are forbidden')


def publish(path):
    require(path.stat().st_size < 16 * 1024 * 1024, 'proposal size bound')
    proposal = load_json(path.read_text())
    require(proposal['schema'] == 'trnm_trusted_source_materialization_v1' and proposal['repository'] == REPOSITORY, 'proposal authority mismatch')
    require(proposal['base_commit'] == BASE and proposal['base_tree'] == BASE_TREE, 'proposal base mismatch')
    require(proposal['tooling_commit'] == os.environ.get('GITHUB_SHA') and proposal['run_id'] == os.environ.get('GITHUB_RUN_ID'), 'proposal producer mismatch')
    require(0 < len(proposal['entries']) <= 65, 'entry cardinality bound')
    require(len({row['path'] for row in proposal['entries']}) == len(proposal['entries']), 'duplicate proposal paths')
    token = os.environ.get('GITHUB_TOKEN', '')
    require(bool(token), 'blob publisher token is required')
    opener = urllib.request.build_opener(NoRedirect())
    elements = []
    for row in proposal['entries']:
        file_path = row['path']
        require(file_path in EXTRA_PATHS or re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', file_path) is not None, 'publisher path is not allowed')
        data = row['content'].encode()
        require(row['mode'] == '100644', 'unsupported file mode')
        require(len(data) <= 1024 * 1024 and git_blob(data) == row['sha'], 'proposal bytes do not match blob identity')
        payload = json.dumps({'content': base64.b64encode(data).decode(), 'encoding': 'base64'}).encode()
        request = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + '/git/blobs', data=payload,
                    headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'}, method='POST')
        # Idempotent object publication only. No tree, commit, ref, review or deployment writes.
        for attempt in range(3):
            try:
                with opener.open(request, timeout=30) as response:
                    result = load_json(response.read(65537))
                require(result.get('sha') == row['sha'], 'remote Git blob identity mismatch')
                break
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise ValueError('blob publication HTTP ' + str(error.code)) from None
                time.sleep(1 + attempt)
        elements.append({'path': file_path, 'mode': row['mode'], 'type': 'blob', 'sha': row['sha']})
    result = {key: value for key, value in proposal.items() if key not in {'entries', 'workflow_transformations'}}
    result['transformed_workflow_count'] = len(proposal['workflow_transformations'])
    result['tree_elements'] = elements
    result['refs_written'] = False
    result['production_claim'] = False
    print('MATERIALIZED_BLOBS_BEGIN')
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    print('MATERIALIZED_BLOBS_END')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['generate', 'publish'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--proposal', required=True, type=Path)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY, 'workflow repository mismatch')
    require(os.environ.get('GITHUB_REPOSITORY_ID') == '1323087470', 'workflow repository ID mismatch')
    if args.mode == 'generate':
        require(not os.environ.get('GITHUB_TOKEN'), 'generation must not have a write credential')
        require(args.source is not None, 'source checkout is required')
        generate(args.source.resolve(), args.proposal)
    else:
        publish(args.proposal)


if __name__ == '__main__':
    main()
