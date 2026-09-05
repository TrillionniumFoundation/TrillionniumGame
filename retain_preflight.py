#!/usr/bin/env python3
"""Supplement a diagnostic export with exact producer-local bytes, never acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

HEAD = 'bdfa705c751be77aff1f7e35f14c76ed4e032ea4'
TREE = 'f2391bbf7210d155fc7e56a190253cbadc820802'
REPO = 'TrillionniumFoundation/TrillionniumGame'
MAX_TOTAL = 128 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_ZIP = 64 * 1024 * 1024
MAX_FILES = 4000

class RetentionError(ValueError):
    pass

def require(ok: bool, message: str) -> None:
    if not ok:
        raise RetentionError(message)

def load_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def nonfinite(_):
        raise RetentionError('nonfinite JSON')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)

def canonical(name: str) -> str:
    require(isinstance(name, str) and bool(name), 'invalid path type')
    p = PurePosixPath(name)
    require(not p.is_absolute() and not ({'..', '.git'} & set(p.parts))
            and '\\' not in name and '\x00' not in name and p.as_posix() == name,
            'unsafe path')
    return name

def validate_tests(data: bytes, expected: int = 411) -> list[str]:
    require(type(expected) is int and expected > 0, 'invalid expected test count')
    require(0 < len(data) <= 8 * 1024 * 1024, 'test log outside bound')
    text = data.decode('utf-8')
    lines = text.splitlines()
    summary = re.findall(r'^Ran ([0-9]+) tests in [0-9]+(?:\.[0-9]+)?s$', text, re.M)
    require(summary == [str(expected)] and lines.count('OK') == 1
            and lines[-1] == 'OK', 'test summary absent, duplicate or unsuccessful')
    candidates = [line for line in lines if line.startswith('test_')]
    pattern = re.compile(r'^(test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\) \.\.\. ok$')
    require(len(candidates) == expected, 'test line count mismatch')
    ids = []
    for line in candidates:
        match = pattern.fullmatch(line)
        require(match is not None, 'non-passing or malformed test line')
        ids.append(match[2] + ':' + match[1])
    require(len(set(ids)) == expected, 'duplicate test identity')
    return ids

def verified_packet(path: Path) -> dict[str, bytes]:
    require(path.is_file() and not path.is_symlink()
            and 0 < path.stat().st_size <= MAX_ZIP, 'packet outside bound')
    files = {}
    with zipfile.ZipFile(path) as z:
        info = z.infolist()
        require(0 < len(info) <= MAX_FILES and sum(i.file_size for i in info) <= MAX_TOTAL,
                'packet count or size exceeded')
        for i in info:
            name = canonical(i.filename)
            require(name not in files and not i.is_dir() and not i.flag_bits & 1
                    and (i.external_attr >> 16) & 0o170000 != 0o120000
                    and 0 <= i.file_size <= MAX_MEMBER, 'invalid ZIP member')
            files[name] = z.read(i)
    require('file-index.json' in files, 'byte index missing')
    rows = load_json(files.pop('file-index.json'))
    require(isinstance(rows, list), 'index must be a list')
    seen = set()
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'path', 'size_bytes', 'sha256'},
                'invalid index fields')
        name = canonical(row['path'])
        require(name in files and name not in seen, 'duplicate or absent indexed path')
        seen.add(name)
        data = files[name]
        require(type(row['size_bytes']) is int and row['size_bytes'] == len(data)
                and row['sha256'] == hashlib.sha256(data).hexdigest(), 'index byte mismatch')
    require(seen == set(files), 'unindexed content')
    obs = load_json(files['observation.json'])
    require(obs.get('source_head') == HEAD and obs.get('source_tree') == TREE,
            'wrong candidate')
    claims = obs.get('claims')
    require(isinstance(claims, dict) and claims and all(v is False for v in claims.values()),
            'diagnostic claims must remain false')
    return files

def augment(packet: Path, output: Path, preflight: bytes, controls: bytes,
            commit: bytes, producer: dict) -> dict:
    require(not output.exists() and not output.is_symlink(), 'output already exists')
    files = verified_packet(packet)
    ids = validate_tests(preflight)
    require(0 < len(controls) <= 8 * 1024 * 1024, 'control log outside bound')
    digest = hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest()
    require(digest == HEAD and commit.startswith(('tree ' + TREE + '\n').encode()),
            'raw commit mismatch')
    require(set(producer) == {'repository', 'workflow_commit', 'run_id', 'run_attempt', 'job'}
            and producer['repository'] == REPO
            and isinstance(producer['workflow_commit'], str)
            and re.fullmatch('[0-9a-f]{40}', producer['workflow_commit']) is not None
            and all(type(producer[k]) is int and producer[k] > 0 for k in ('run_id', 'run_attempt'))
            and isinstance(producer['job'], str)
            and re.fullmatch('[A-Za-z0-9_-]+', producer['job']) is not None,
            'invalid producer identity')
    additions = {
        'producer/product-preflight.log': preflight,
        'producer/source-controls.log': controls,
        'source-commit.raw': commit,
    }
    receipt = {
        'schema': 'trillionnium.producer-preflight-retention.v1',
        'source_head': HEAD, 'source_tree': TREE, 'producer': producer,
        'tests': {'collected': len(ids), 'passed': len(ids), 'identities': ids},
        'files': [{'path': n, 'size_bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()}
                  for n, v in sorted(additions.items())],
        'claims': {'accepted': False, 'gap_closed': False, 'independently_reviewed': False},
        'limitations': ['The named producer ran the preflight; it is not a replacement for ordinary product gates.',
                        'Native API provenance and all original incomplete observations remain separately required.',
                        'Raw commit identity verifies one object, not the complete Git history.'],
    }
    additions['producer/preflight-retention.json'] = (json.dumps(receipt, indent=2) + '\n').encode()
    require(not set(additions).intersection(files), 'supplement path collision')
    files.update(additions)
    require(len(files) + 1 <= MAX_FILES and sum(map(len, files.values())) <= MAX_TOTAL,
            'supplement exceeds packet bounds')
    index = [{'path': n, 'size_bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()}
             for n, v in sorted(files.items())]
    files['file-index.json'] = (json.dumps(index, indent=2) + '\n').encode()
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, data in sorted(files.items()):
            z.writestr(name, data)
    require(output.stat().st_size <= MAX_ZIP, 'supplement ZIP too large')
    return {'size_bytes': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'retained_members': len(files), 'producer_tests': len(ids), 'accepted': False, 'gap_closed': False}

def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('packet', 'output', 'preflight', 'controls', 'source'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    raw = subprocess.run(['git', '-C', str(args.source), 'cat-file', 'commit', HEAD],
                         check=True, capture_output=True, timeout=10).stdout
    def log(path):
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 8 * 1024 * 1024,
                'local log missing/outside bound')
        with path.open('rb') as stream:
            return stream.read(8 * 1024 * 1024 + 1)
    producer = {'repository': os.environ['GITHUB_REPOSITORY'],
                'workflow_commit': os.environ['GITHUB_SHA'],
                'run_id': int(os.environ['GITHUB_RUN_ID']),
                'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']), 'job': os.environ['GITHUB_JOB']}
    print(json.dumps(augment(args.packet, args.output, log(args.preflight), log(args.controls), raw, producer)))

if __name__ == '__main__':
    try:
        main()
    except (RetentionError, OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        raise SystemExit('Preflight retention failed: ' + type(error).__name__) from None
