"""Fixed-source retained-I/O preflight; only store Git blobs after checks.

stage uses no repository credential; store has a separate Actions step token.
Neither mode changes refs, reviews, settings, gaps to closed, or release state.
"""
from __future__ import annotations
import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request
from unittest.mock import patch

REPO = 'TrillionniumFoundation/TrillionniumGame'
SOURCE = '5fe1ef9988b56025168b7942fbf4b02d1d3cfecd'
TREE = '88748ce7809c80dea0d4c8a291d75126c2c73e2f'
PAYLOAD = '583cd856650121636a6b8b0b3a8c7ed0b5889c23521bfd694a65b596d16ba6c9'
STATUS = 'docs/status/EVIDENCE_RETAINED_IO_STATUS.json'
EXISTING = {'scripts/evidence_admission.py', 'docs/TESTING_AND_EVIDENCE.md',
            'docs/status/CURRENT_STATE.json', 'docs/status/GAP_REGISTER.json',
            'docs/roadmap/NEXT_MILESTONE.json'}
NEW = {STATUS, 'tests/control_plane/test_evidence_retained_io.py'}
PATHS = EXISTING | NEW


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], text=True, timeout=30).strip()


def bind():
    require(git('rev-parse', 'HEAD') == SOURCE, 'unexpected source HEAD')
    require(git('rev-parse', 'HEAD^{tree}') == TREE, 'unexpected source tree')
    require(git('remote', 'get-url', 'origin') == 'https://github.com/' + REPO + '.git', 'unexpected origin')


def reproduce():
    path = Path('scripts/evidence_admission.py')
    require(blob(path.read_bytes()) == '854683b1c470d8870f1997fcadfa6ac08c0f561a', 'original admission changed')
    spec = importlib.util.spec_from_file_location('original_retained_admission', path.resolve())
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / 'root'
        root.mkdir()
        leaf = root / 'result.bin'
        data = b'synthetic retained bytes; not real evidence\n'
        leaf.write_bytes(data)
        outside = Path(temp) / 'outside.bin'
        outside.write_bytes(data)
        descriptor = {'name': 'fixture', 'path': 'result.bin', 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
        real_open = Path.open
        def swap(target, *args, **kwargs):
            if target == leaf:
                leaf.unlink()
                leaf.symlink_to(outside)
            return real_open(target, *args, **kwargs)
        with patch.object(Path, 'open', swap):
            original.verify_artifact(root, descriptor)
        require(leaf.is_symlink(), 'race fixture not exercised')
    print('BASELINE_REPRODUCED: checked path replaced by outside symlink was accepted', flush=True)


def stage(payload_path, receipt_path):
    bind()
    require(not git('status', '--porcelain', '--untracked-files=normal'), 'source not clean')
    compressed = payload_path.read_bytes()
    require(len(compressed) == 8257 and hashlib.sha256(compressed).hexdigest() == PAYLOAD, 'payload mismatch')
    with gzip.open(payload_path, 'rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024, 'payload exceeds bound')
    payload = json.loads(raw)
    require(payload['source'] == SOURCE and payload['tree'] == TREE, 'payload source mismatch')
    for name, expected in payload['original_blobs'].items():
        require(name in EXISTING and blob(Path(name).read_bytes()) == expected, 'original blob mismatch')
    require(set(payload['files']) == NEW, 'replacement path mismatch')
    for name in NEW:
        require(not Path(name).exists(), 'new path already exists')
    original = {name: Path(name).read_bytes() for name in EXISTING}
    reproduce()
    delta = payload['admission_patch'].encode()
    subprocess.run(['git', 'apply', '--check', '-'], input=delta, check=True, timeout=10)
    subprocess.run(['git', 'apply', '-'], input=delta, check=True, timeout=10)
    changed = dict(payload['files'])
    changed['scripts/evidence_admission.py'] = Path('scripts/evidence_admission.py').read_text()
    doc = original['docs/TESTING_AND_EVIDENCE.md'].decode()
    require('## 14. Database negative attribution and production retry proof' in doc, 'database documentation missing')
    require('## 15.' not in doc and 'Revision: 2026-09-05' in doc, 'documentation version drift')
    changed['docs/TESTING_AND_EVIDENCE.md'] = doc.rstrip() + payload['doc_append']
    state = json.loads(original['docs/status/CURRENT_STATE.json'])
    state['evidence']['shared_retained_admission_source_candidate']['retained_io_status'] = STATUS
    changed['docs/status/CURRENT_STATE.json'] = json.dumps(state, indent=2) + '\n'
    register = json.loads(original['docs/status/GAP_REGISTER.json'])
    wanted = {'GAP-P0-EVIDENCE-001', 'GAP-P0-PLAN-001', 'GAP-P1-TEST-001'}
    selected = [row for row in register['gaps'] if row['id'] in wanted]
    require(len(selected) == 3, 'missing linked gap')
    before_states = [(row['id'], row['status'], row['evidence_ids']) for row in register['gaps']]
    for row in selected:
        row['retained_io_status'] = STATUS
    require(before_states == [(row['id'], row['status'], row['evidence_ids']) for row in register['gaps']], 'gap status changed')
    changed['docs/status/GAP_REGISTER.json'] = json.dumps(register, separators=(',', ':')) + '\n'
    roadmap = json.loads(original['docs/roadmap/NEXT_MILESTONE.json'])
    items = [row for row in roadmap['items'] if row['id'] == 'TG-V3-002']
    require(len(items) == 1, 'missing task')
    items[0]['admission_source_candidate']['retained_io_status'] = STATUS
    changed['docs/roadmap/NEXT_MILESTONE.json'] = json.dumps(roadmap, separators=(',', ':')) + '\n'
    require(set(changed) == PATHS, 'unexpected change set')
    for name, text in changed.items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(text, encoding='utf-8')
    require(set(git('diff', '--name-only').splitlines()) == EXISTING, 'tracked changes drift')
    commands = [['python3', '-m', 'compileall', '-q', 'scripts', 'tools', 'tests'],
                ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v']]
    commands += [['python3', 'scripts/' + name + '.py'] for name in
                 ['check-documentation-authority', 'check-plan', 'check-evidence-index',
                  'check-gap-register', 'derive-gap-status', 'derive-gates', 'check-status-transitions']]
    results = []
    for i, command in enumerate(commands):
        run = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        output = run.stdout.decode('utf-8', errors='replace')
        receipt_path.with_name('check-' + str(i) + '.log').write_bytes(run.stdout)
        count = re.search(r'Ran (\d+) tests? in', output)
        result = {'command': command, 'exit_code': run.returncode, 'output_sha256': hashlib.sha256(run.stdout).hexdigest(),
                  'tests': int(count.group(1)) if count else None}
        print('CHECK=' + json.dumps(result), flush=True)
        require(run.returncode == 0, 'preflight failed: ' + output[-8000:])
        if i == 1:
            require(count and int(count.group(1)) == 370 and '\nOK\n' in output, 'unexpected full test result')
            require(output.count('(control_plane.test_evidence_retained_io.') == 34, 'retained I/O tests not collected')
            require(output.count('(control_plane.test_evidence_admission.ConsumerWiringTests.') == 6, 'consumer integration tests not collected')
        results.append(result)
    entries = []
    for name in sorted(changed):
        data = Path(name).read_bytes()
        require(data == changed[name].encode(), 'tests altered publication source')
        entries.append({'path': name, 'mode': '100644', 'type': 'blob', 'sha': blob(data),
                        'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                        'before_blob': blob(original[name]) if name in original else None})
    receipt = {'source': SOURCE, 'tree': TREE, 'entries': entries, 'checks': results,
               'full_python_tests': 370, 'new_io_tests': 34, 'consumer_integration_tests': 6,
               'gap_closed': False, 'independent_acceptance': False, 'ref_mutated': False}
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + '\n')
    print('PREFLIGHT_RECEIPT=' + json.dumps(receipt, sort_keys=True), flush=True)


def store(receipt_path):
    bind()
    receipt = json.loads(receipt_path.read_text())
    require(receipt['source'] == SOURCE and receipt['tree'] == TREE, 'receipt identity mismatch')
    require({r['path'] for r in receipt['entries']} == PATHS and len(receipt['entries']) == 7, 'receipt paths mismatch')
    require(len(receipt['checks']) == 9 and all(r['exit_code'] == 0 for r in receipt['checks']), 'preflight incomplete')
    token = os.environ.get('GITHUB_TOKEN')
    require(bool(token), 'blob-write credential missing')
    for row in receipt['entries']:
        data = Path(row['path']).read_bytes()
        require(blob(data) == row['sha'] and len(data) == row['size_bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'], 'source changed after preflight')
        request = urllib.request.Request('https://api.github.com/repos/' + REPO + '/git/blobs',
                    data=json.dumps({'content': base64.b64encode(data).decode(), 'encoding': 'base64'}).encode(),
                    headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read(1024 * 1024))
        require(result.get('sha') == row['sha'], 'GitHub stored a different blob')
    print('MATERIALIZATION_RECEIPT=' + json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'stage':
        stage(Path(sys.argv[2]), Path(sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == 'store':
        store(Path(sys.argv[2]))
    else:
        raise SystemExit('usage: stage.py stage PAYLOAD RECEIPT | stage.py store RECEIPT')
