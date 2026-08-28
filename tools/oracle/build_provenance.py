# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SHA40 = re.compile(r'^[0-9a-f]{40}$')
SHA256 = re.compile(r'^sha256:[0-9a-f]{64}$')


class ProvenanceError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ProvenanceError(f'{path}: root must be object')
    return value


def _sha40(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA40.fullmatch(value) or value == '0' * 40:
        raise ProvenanceError(f'{label} must be a non-zero lowercase Git SHA')
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value) or value == 'sha256:' + '0' * 64:
        raise ProvenanceError(f'{label} must be a non-zero sha256 digest')
    return value


def _safe_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ProvenanceError(f'{label} is unsafe')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
        raise ProvenanceError(f'{label} is unsafe')
    return value


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get('schema') != 'trillionnium.oracle-instrumentation-policy.v1' or policy.get('project_id') != 'trillionnium-game':
        raise ProvenanceError('invalid instrumentation policy identity')
    upstream = policy.get('upstream') or {}
    if upstream != {
        'repository': 'heroiclabs/nakama',
        'commit': 'd4d92f93f78bbbe62c7fc50a3f85c772ec121a09',
        'tree': 'f3c9cfc2726d5543da1564629170f35b98e3797d',
    }:
        raise ProvenanceError('instrumentation policy upstream mismatch')
    allowed = policy.get('allowed_capabilities')
    if not isinstance(allowed, list) or len(set(allowed)) != len(allowed) or not allowed:
        raise ProvenanceError('allowed capability set is invalid')
    controls = policy.get('policy') or {}
    expected_false = (
        'deletions_allowed',
        'semantic_behavior_change_allowed',
        'networked_build_allowed',
        'floating_image_or_dependency_allowed',
        'self_approval_allowed',
        'positive_equivalence_claim_allowed',
    )
    for field in expected_false:
        if controls.get(field) is not False:
            raise ProvenanceError(f'policy {field} must be false')


def validate_manifest(manifest: dict[str, Any], policy: dict[str, Any], *, require_approved: bool = False) -> dict[str, Any]:
    validate_policy(policy)
    if manifest.get('schema') != 'trillionnium.instrumented-oracle-build.v1' or manifest.get('project_id') != 'trillionnium-game':
        raise ProvenanceError('invalid build manifest identity')
    status = manifest.get('status')
    if status not in {'candidate-unreviewed', 'reviewed-approved'}:
        raise ProvenanceError('invalid build manifest status')
    if require_approved and status != 'reviewed-approved':
        raise ProvenanceError('approved instrumented build evidence is required')

    upstream = manifest.get('upstream') or {}
    if upstream != policy['upstream']:
        raise ProvenanceError('manifest upstream is not exact policy upstream')

    claims = manifest.get('claims')
    if not isinstance(claims, dict) or not claims or any(claims.values()):
        raise ProvenanceError('instrumented build manifest must contain only explicit false claims')

    patch = manifest.get('patch') or {}
    _sha256(patch.get('sha256'), 'patch.sha256')
    files = patch.get('files')
    if not isinstance(files, list) or not files:
        raise ProvenanceError('patch.files must be non-empty')
    allowed_capabilities = set(policy['allowed_capabilities'])
    allowed_added = tuple(policy.get('allowed_added_prefixes', []))
    allowed_modified = set(policy.get('allowed_modified_paths', []))
    forbidden_prefixes = tuple(policy.get('forbidden_prefixes', []))
    forbidden_exact = set(policy.get('forbidden_exact_paths', []))
    seen_paths: set[str] = set()
    capabilities_seen: set[str] = set()
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            raise ProvenanceError(f'patch file {index} must be object')
        path = _safe_path(row.get('path'), f'patch.files[{index}].path')
        if path in seen_paths:
            raise ProvenanceError(f'duplicate patch path: {path}')
        seen_paths.add(path)
        if path in forbidden_exact or path.startswith(forbidden_prefixes):
            raise ProvenanceError(f'forbidden instrumentation path: {path}')
        change = row.get('change_type')
        if change not in {'added', 'modified'}:
            raise ProvenanceError(f'{path}: deletions and unknown change types are forbidden')
        if change == 'added':
            if not path.startswith(allowed_added):
                raise ProvenanceError(f'{path}: added file is outside reviewed instrumentation prefixes')
            if row.get('old_blob') is not None:
                raise ProvenanceError(f'{path}: added file old_blob must be null')
        else:
            if path not in allowed_modified:
                raise ProvenanceError(f'{path}: modification requires explicit policy allowlist review')
            _sha40(row.get('old_blob'), f'{path}.old_blob')
        _sha40(row.get('new_blob'), f'{path}.new_blob')
        _sha256(row.get('diff_sha256'), f'{path}.diff_sha256')
        capabilities = row.get('capabilities')
        if not isinstance(capabilities, list) or not capabilities:
            raise ProvenanceError(f'{path}: capabilities are required')
        unknown = set(capabilities) - allowed_capabilities
        if unknown:
            raise ProvenanceError(f'{path}: unknown capabilities {sorted(unknown)}')
        capabilities_seen.update(capabilities)
        if row.get('semantics_impact') != 'none-claimed':
            raise ProvenanceError(f'{path}: semantics impact must remain none-claimed')
        if row.get('review_status') not in {'unreviewed', 'approved'}:
            raise ProvenanceError(f'{path}: invalid review status')
        if status == 'candidate-unreviewed' and row.get('review_status') != 'unreviewed':
            raise ProvenanceError(f'{path}: candidate file may not self-mark approved')
        if status == 'reviewed-approved' and row.get('review_status') != 'approved':
            raise ProvenanceError(f'{path}: reviewed build contains unapproved file')

    toolchain = manifest.get('toolchain') or {}
    for field in ('go_version', 'docker_version', 'buildkit_version', 'platform'):
        if not isinstance(toolchain.get(field), str) or not toolchain[field]:
            raise ProvenanceError(f'toolchain.{field} is required')
    for field in ('go_binary_sha256', 'dockerfile_sha256', 'base_image_digest'):
        _sha256(toolchain.get(field), f'toolchain.{field}')

    build = manifest.get('build') or {}
    command = build.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ProvenanceError('build.command must be a non-empty argv list')
    joined = ' '.join(command).lower()
    if any(marker in joined for marker in (' curl ', ' wget ', 'git clone', ':latest', 'http://', 'https://')):
        raise ProvenanceError('build command contains network or floating input')
    if build.get('network_mode') != 'none':
        raise ProvenanceError('instrumented build network_mode must be none')
    _sha256(build.get('context_sha256'), 'build.context_sha256')
    if not isinstance(build.get('source_date_epoch'), int) or build['source_date_epoch'] <= 0:
        raise ProvenanceError('build.source_date_epoch must be positive integer')

    image = manifest.get('image') or {}
    for field in ('image_id', 'oci_digest', 'sbom_sha256', 'provenance_sha256'):
        _sha256(image.get(field), f'image.{field}')

    review = manifest.get('review') or {}
    reviewers = review.get('reviewers')
    if not isinstance(reviewers, list):
        raise ProvenanceError('review.reviewers must be list')
    if status == 'candidate-unreviewed' and reviewers:
        raise ProvenanceError('candidate manifest may not contain approving reviewers')
    if status == 'reviewed-approved':
        if len(reviewers) < 2 or len(set(reviewers)) != len(reviewers):
            raise ProvenanceError('approved build requires at least two distinct reviewers')
        if review.get('self_approval') is not False:
            raise ProvenanceError('approved build must explicitly deny self approval')
        _sha256(review.get('review_evidence_sha256'), 'review.review_evidence_sha256')

    result = {
        'schema': 'trillionnium.instrumented-oracle-build-verification.v1',
        'status': 'approved' if status == 'reviewed-approved' else 'candidate-valid',
        'manifest_sha256': digest(manifest),
        'policy_sha256': digest(policy),
        'file_count': len(files),
        'capabilities': sorted(capabilities_seen),
        'approved': status == 'reviewed-approved',
        'instrumented_equivalence': False,
        'sg2_complete': False,
        'compatibility_credit': False,
        'production_ready': False,
        'public_online': False,
    }
    result['content_sha256'] = digest(result)
    return result
