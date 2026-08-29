# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tools.upstream.pinned_archive import git_blob_sha1_bytes, verify_source_lock

TEXT_EXTENSIONS = {'.ts','.tsx','.js','.jsx','.cs','.java','.cpp','.cc','.cxx','.h','.hpp','.swift','.gd','.lua','.dart','.proto'}
MAX_FILE_BYTES = 4 * 1024 * 1024
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
RPC = re.compile(r'\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(')
MESSAGE = re.compile(r'\bmessage\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{')


class SDKMatrixError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha256(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def stable_id(item_class: str, symbol: str, contract: Any) -> str:
    seed = canonical({'class': item_class, 'symbol': symbol, 'contract': contract})
    return 'TG-D1D2-SDK-' + hashlib.sha256(seed).hexdigest()[:18].upper()


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or value == '0' * 40 or any(c not in '0123456789abcdef' for c in value):
        raise SDKMatrixError(f'{label} must be non-zero lowercase SHA')
    return value


def load_registry(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if value.get('schema') != 'trillionnium.sdk-source-snapshots.v1' or value.get('project_id') != 'trillionnium-game':
        raise SDKMatrixError('invalid SDK registry identity')
    profiles = value.get('profiles')
    if not isinstance(profiles, list) or len(profiles) < 10:
        raise SDKMatrixError('at least ten SDK profiles are required')
    ids: list[str] = []
    repos: list[str] = []
    for row in profiles:
        if not isinstance(row, dict):
            raise SDKMatrixError('SDK profile must be object')
        for field in ('id', 'repository', 'branch', 'language', 'platform'):
            if not isinstance(row.get(field), str) or not row[field]:
                raise SDKMatrixError(f'SDK profile missing {field}')
        if not row['repository'].startswith('heroiclabs/nakama-'):
            raise SDKMatrixError('only reviewed Heroic Labs Nakama SDK repositories are allowed')
        _require_sha(row.get('commit'), f"{row['id']} commit")
        _require_sha(row.get('tree'), f"{row['id']} tree")
        ids.append(row['id'])
        repos.append(row['repository'])
    if len(ids) != len(set(ids)) or len(repos) != len(set(repos)):
        raise SDKMatrixError('duplicate SDK id or repository')
    claims = value.get('claims') or {}
    if not claims or any(claims.values()):
        raise SDKMatrixError('candidate SDK registry must contain only false claims')
    return value


def _normal(value: str) -> str:
    result = re.sub(r'[^a-z0-9]', '', value.lower())
    for suffix in ('async', 'request', 'response', 'message', 'result', 'list'):
        if result.endswith(suffix) and len(result) > len(suffix) + 3:
            result = result[:-len(suffix)]
    return result


def _line(text: str, offset: int) -> int:
    return text.count('\n', 0, offset) + 1


def source_targets(nakama_root: Path, common_root: Path) -> tuple[list[str], list[str]]:
    api = (nakama_root / 'apigrpc/apigrpc.proto').read_text()
    realtime = (common_root / 'rtapi/realtime.proto').read_text()
    rpcs = sorted(set(RPC.findall(api)))
    messages = sorted(set(MESSAGE.findall(realtime)))
    if len(rpcs) < 50 or len(messages) < 25:
        raise SDKMatrixError(f'implausible source denominator sizes: rpc={len(rpcs)} rt={len(messages)}')
    return rpcs, messages


def scan_sdk(root: Path, profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    verify_source_lock(root, repository=profile['repository'], revision=profile['commit'], tree=profile['tree'])
    index: dict[str, list[dict[str, Any]]] = {}
    file_count = 0
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name == '.trillionnium-source-lock.json' or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        data = path.read_bytes()
        file_count += 1
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
        blob = git_blob_sha1_bytes(data)
        for match in IDENTIFIER.finditer(text):
            key = _normal(match.group(0))
            if len(key) < 4:
                continue
            index.setdefault(key, []).append({
                'path': path.relative_to(root).as_posix(),
                'line': _line(text, match.start()),
                'identifier': match.group(0),
                'blob': blob,
                'sha256': sha256(data),
            })
    if file_count < 1:
        raise SDKMatrixError(f"{profile['id']}: no scannable source files")
    return index


def _matches(index: dict[str, list[dict[str, Any]]], target: str) -> list[dict[str, Any]]:
    wanted = _normal(target)
    found: list[dict[str, Any]] = []
    for key, locations in index.items():
        if key == wanted or key.startswith(wanted) or (wanted.startswith(key) and len(key) >= 6):
            found.extend(locations)
    unique = {(item['path'], item['line'], item['identifier']): item for item in found}
    return [unique[key] for key in sorted(unique)][:20]


def make_leaf(profile: dict[str, Any], kind: str, target: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
    symbol = f"{profile['id']}:{kind}:{target}"
    contract = {
        'sdk_id': profile['id'],
        'repository': profile['repository'],
        'commit': profile['commit'],
        'tree': profile['tree'],
        'language': profile['language'],
        'platform': profile['platform'],
        'target': target,
        'match_count': len(matches),
        'matched_locations': matches,
        'candidate_presence': 'candidate-present' if matches else 'candidate-missing',
        'transport_profile': None,
        'support_window': None,
    }
    identifier = stable_id(f'sdk_{kind}_candidate', symbol, contract)
    return {
        'id': identifier,
        'layer': 'D1/D2-consumer',
        'class': f'sdk_{kind}_candidate',
        'symbol': symbol,
        'source': {
            'repository': profile['repository'],
            'commit': profile['commit'],
            'tree': profile['tree'],
            'branch_snapshot': profile['branch'],
        },
        'signature_hash': sha256(canonical(contract)),
        'classification': 'unclassified',
        'mandatory': None,
        'owner_role': 'sdk-compatibility',
        'workstream': 'W2',
        'task_ids': ['TG-W0-002'],
        'test_ids': [f'TG-DIFF-{identifier}'],
        'status': 'planned',
        'evidence_refs': [],
        'waiver': None,
        'contract': contract,
    }


def generate(registry: dict[str, Any], sdk_root: Path, nakama_root: Path, common_root: Path) -> dict[str, Any]:
    verify_source_lock(
        nakama_root,
        repository='heroiclabs/nakama',
        revision='d4d92f93f78bbbe62c7fc50a3f85c772ec121a09',
        tree='f3c9cfc2726d5543da1564629170f35b98e3797d',
    )
    verify_source_lock(
        common_root,
        repository='heroiclabs/nakama-common',
        revision='449b77ecc8789aa466c36b67f6e498033dfcd9c5',
        tree='c6a7b9796b9c2a6b5118c74e5f213963a5001f14',
    )
    rpcs, messages = source_targets(nakama_root, common_root)
    leaves: list[dict[str, Any]] = []
    for profile in registry['profiles']:
        index = scan_sdk(sdk_root / profile['id'], profile)
        for target in rpcs:
            leaves.append(make_leaf(profile, 'api_operation', target, _matches(index, target)))
        for target in messages:
            leaves.append(make_leaf(profile, 'realtime_message', target, _matches(index, target)))
    leaves.sort(key=lambda item: item['id'])
    if len({item['id'] for item in leaves}) != len(leaves):
        raise SDKMatrixError('duplicate SDK leaf IDs')
    manual = [
        {'class': 'sdk_release_line_review', 'symbol': 'all-sdk-profiles', 'reason': 'Default-branch snapshots are discovery inputs, not approved release/support baselines.'},
        {'class': 'sdk_transport_profile_review', 'symbol': 'all-sdk-profiles', 'reason': 'HTTP/JSON, WebSocket JSON/Protobuf, engine and platform transport behavior require black-box classification.'},
        {'class': 'sdk_operation_match_review', 'symbol': 'all-sdk-profiles', 'reason': 'Identifier matches are candidates; generated aliases, overloads, serialization and error semantics require independent review.'},
        {'class': 'sdk_repository_discovery_review', 'symbol': 'heroiclabs-nakama-sdk-set', 'reason': 'Archived, engine-specific and newly published official SDK repositories require explicit include/exclude decisions.'},
    ]
    value = {
        'schema': 'trillionnium.sdk-denominator-candidate.v1',
        'project_id': 'trillionnium-game',
        'denominator': 'DEN-SDK',
        'layer': 'D1/D2-consumer-matrix',
        'status': 'candidate-unclassified',
        'source_registry_sha256': sha256(canonical(registry)),
        'profile_count': len(registry['profiles']),
        'api_target_count': len(rpcs),
        'realtime_target_count': len(messages),
        'leaf_count': len(leaves),
        'unclassified_count': len(leaves),
        'manual_contract_count': len(manual),
        'sg1_eligible': False,
        'operation_coverage_verified': False,
        'transport_profiles_verified': False,
        'support_windows_verified': False,
        'compatibility_credit': False,
        'production_ready': False,
        'leaves': leaves,
        'manual_contracts': manual,
    }
    value['content_sha256'] = sha256(canonical(value))
    return value
