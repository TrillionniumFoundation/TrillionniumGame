"""Issued full-source5 / selected StorageV4 evidence bindings.

Source bytes and selected runtime identity occupy separate closed domains. This
module performs no Git, network or native work. Producers/remote collectors own
exact Git custody; a source token alone never grants that observation.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import types
import weakref
from pathlib import Path
import schema_source_selection as SOURCE

SCHEMA = 'trillionnium.schema-source-execution-evidence.v1'
ANNEX = 'full-schema-source'
SIDECAR = 'schema-source-selection.json'
HEAD_PROOF = 'schema-source-head.json'
MAX_FILE_BYTES = 512 * 1024
# Composite receipts retain all 55 workflow rows plus both full-source proofs.
# This local ceiling does not change the standalone selection JSON limit.
MAX_COMPOSITE_RECEIPT_BYTES = 512 * 1024
MAX_COMPOSITE_JSON_DEPTH = 64
MAX_ANNEX_BYTES = 2 * 1024 * 1024
# Complete both-profile frontier: no second chain and no prefix lock generated.
CONTROL_PATHS = (SOURCE.LOCK_PATH, SOURCE.AUTHORITY_PATH, SOURCE.VALIDATOR_PATH,
                 'scripts/schema_source_selection.py', 'scripts/schema_evidence_binding.py',
                 'config/database-test-images.json', 'scripts/check-authoritative-schema-identity.py',
                 'scripts/capture-schema-source-selection.py')
PROFILES = SOURCE.PROFILES

class BindingError(RuntimeError):
    pass

def require(condition, reason):
    if not condition:
        raise BindingError(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'

def same(a, b):
    return SOURCE._same(a, b)

def strict(raw, label='evidence'):
    # Reuse bounded duplicate/nonfinite/closed-type parsing, never permissive JSON.
    return SOURCE._strict_json(raw, label)

def strict_composite_receipt(raw):
    """Bound the composite domain independently of standalone source documents."""
    require(type(raw) is bytes and len(raw) <= MAX_COMPOSITE_RECEIPT_BYTES,
            'composite_receipt_byte_budget')
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            require(depth <= MAX_COMPOSITE_JSON_DEPTH, 'composite_receipt_depth_budget')
        elif byte in (93, 125):
            depth -= 1
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'composite_receipt_duplicate_key')
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_JSON_number')))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BindingError('composite_receipt_invalid_JSON') from error

def blob(raw):
    return hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def source_paths(document):
    return sorted(CONTROL_PATHS + tuple(row['path'] for profile in PROFILES
                   for row in document['source']['profiles'][profile]['ordered_files']))

def _api():
    issued = weakref.WeakKeyDictionary()
    class VerifiedBinding:
        __slots__ = ('__weakref__',)
        def __new__(cls, *args, **kwargs):
            raise TypeError('verified_evidence_binding_requires_source_validation')
        def __init_subclass__(cls, **kwargs):
            raise TypeError('verified_evidence_binding_cannot_be_subclassed')
        def __reduce__(self):
            raise TypeError('verified_evidence_binding_not_serializable')
    def verify_binding(source_root, *, profile, target=SOURCE.SchemaTarget.StorageV4):
        # Accounts and wrong enum fail before Path conversion or any file read.
        selection = SOURCE.verify_current_source_selection(source_root, profile=profile, target=target)
        proof = SOURCE.current_selection_document(selection)
        root = Path(source_root)
        files = []; total = 0; payloads = {}
        expected_source = {
            SOURCE.LOCK_PATH: {'sha256':proof['source']['lock_sha256'], 'git_blob_sha1':proof['source']['lock_git_blob_sha1']},
            SOURCE.AUTHORITY_PATH: {'sha256':proof['source']['authority_sha256']},
            SOURCE.VALIDATOR_PATH: {'sha256':proof['source']['validator_sha256']},
        }
        for source_profile in PROFILES:
            for row in proof['source']['profiles'][source_profile]['ordered_files']:
                require(row['path'] not in expected_source, 'source_proof_path_duplicate')
                expected_source[row['path']] = {key:row[key] for key in ('bytes','sha256','git_blob_sha1')}
        require(len(expected_source)==13, 'full_verified_source_denominator')
        for path in source_paths(proof):
            raw = SOURCE._regular(root, path, MAX_FILE_BYTES)
            total += len(raw); require(total <= MAX_ANNEX_BYTES, 'full_source_annex_byte_budget')
            descriptor = {'bytes':len(raw),'sha256':sha(raw),'git_blob_sha1':blob(raw)}
            if path in expected_source:
                require(all(descriptor[key]==value for key,value in expected_source[path].items()),
                        'source_changed_between_validation_and_binding')
            payloads[path] = raw
            if path in ('scripts/schema_source_selection.py', 'scripts/schema_evidence_binding.py'):
                trusted = Path(__file__).with_name(Path(path).name).read_bytes()
                require(raw == trusted, 'reviewed_shared_source_implementation_required')
            files.append({'path':path,'archive_path':ANNEX+'/'+path,'bytes':len(raw),
                          'sha256':sha(raw),'git_blob_sha1':blob(raw)})
        images = strict(payloads['config/database-test-images.json'], 'image_lock')
        require(type(images) is dict and images.get('schema') == 'trillionnium.database-test-images.v1', 'image_lock_schema')
        image = images['profiles'][profile]['image']
        require(type(image) is str and re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}',image), 'digest_pinned_profile_image_required')
        payload = {'schema':SCHEMA,'profile':profile,'source_selection':proof,
                   'full_source_inventory':files,'full_source_inventory_count':18,
                   'image':image,'claims':{'exact_git_head_verified':False,
                   'runtime_execution_verified':False,'native_catalog_verified':False,
                   'account_transfer_admitted':False,'accepted':False,
                   'compatibility_credit':False,'production_ready':False}}
        require(len(files)==18, 'full_source_inventory_denominator')
        token = object.__new__(VerifiedBinding)
        issued[token] = (selection, canonical(payload))
        return token
    def owned(token):
        require(type(token) is VerifiedBinding and token in issued, 'unissued_or_forged_evidence_binding_token')
        return issued[token]
    def document(token):
        return strict(owned(token)[1], 'issued_evidence_binding')
    def selection_token(token):
        return owned(token)[0]
    return verify_binding, document, selection_token

verify_binding, binding_document, selection_token = _api()
del _api

def operational_binding(token):
    proof = binding_document(token); selected = proof['source_selection']['selection']
    return {'migration_lock':SOURCE.LOCK_PATH,
            'migration_lock_sha256':proof['source_selection']['source']['lock_sha256'],
            'schema_version':'4','storage_writer_epoch':'4',
            'chain_digest':selected['execution_chain_digest'],
            'digest_algorithm':selected['digest_algorithm'],
            'ordered_files':[{'path':r['path'],'git_blob_sha1':r['git_blob_sha1']} for r in selected['ordered_files']],
            'image':proof['image']}

def identity_fields(token):
    proof=binding_document(token); source=proof['source_selection']['source']; selected=proof['source_selection']['selection']
    return {'source_selection_schema':SCHEMA,'source_selection_sha256':sha(canonical(proof)),
            'source_frontier_schema_version':'5','source_profile_migration_file_count':'5',
            'source_profile_chain_digest':source['profiles'][proof['profile']]['chain_digest'],
            'execution_schema_version':'4','executed_migration_file_count':'4',
            'execution_chain_digest':selected['execution_chain_digest'],'execution_table_count':'12',
            'account_transfer_admitted':'false'}

def validate_composite_directory(tokens, root, *, prefix, receipt_filename, commit, tree):
    """Closed two-profile source custody for aggregate/prospective packets.

    This function consumes two independently issued tokens. Actual Git/run
    qualification remains with the producer or receiver that issued them.
    """
    require(type(tokens) is tuple and len(tokens)==2, 'issued_two_profile_token_tuple_required')
    documents={}
    for token in tokens:
        document=binding_document(token)
        require(document['profile'] not in documents, 'duplicate_composite_profile_token')
        documents[document['profile']]=(token,document)
    require(set(documents)==set(PROFILES), 'complete_two_profile_source_denominator')
    shapes={
        ('source-selection','external-workflow-collection.json'):
          ('trillionnium.required-workflow-retained-collection.v2',
           {'schema','repository','head','run_id','run_attempt','schema_source_selection','workflows','claims'}),
        ('source-selection','final-gate-receipt.json'):
          ('trillionnium.merge-gate-retained-receipt.v2',
           {'schema','repository','head','event','run_id','run_attempt','schema_source_selection','lanes','claims'}),
        ('final-source-selection','final-gate-receipt.json'):
          ('trillionnium.prospective-merge-retained-gate.v2',
           {'schema','repository','base_commit','source_head','prospective_merge','run_id','run_attempt','schema_source_selection','lanes','claims'}),
    }
    require((prefix,receipt_filename) in shapes, 'explicit_composite_packet_policy_required')
    root=Path(root)
    # Finish the closed two-entry namespace scan before reading either profile.
    directory=root/prefix
    require(not directory.is_symlink(), 'composite_source_directory_indirect')
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        names=set()
        with os.scandir(fd) as entries:
            for count,entry in enumerate(entries):
                require(count<2, 'composite_profile_inventory_budget')
                require(not entry.is_symlink() and entry.is_dir(follow_symlinks=False), 'composite_profile_directory_required')
                names.add(entry.name)
        require(names==set(PROFILES), 'composite_profile_inventory_closed')
    finally:
        os.close(fd)
    for profile,(token,_) in documents.items():
        validate_annex_directory(token,directory/profile,commit=commit,tree=tree)
    receipt=strict_composite_receipt(SOURCE._regular(root,receipt_filename,MAX_COMPOSITE_RECEIPT_BYTES))
    schema,keys=shapes[(prefix,receipt_filename)]
    require(type(receipt) is dict and set(receipt)==keys and receipt['schema']==schema,'composite_receipt_closed_schema')
    expected={profile:document for profile,(_,document) in documents.items()}
    require(same(receipt['schema_source_selection'],expected),'composite_source_selection_type_shape_or_value')
    require(same(receipt['claims'],{'accepted_evidence':False,'gap_closed':False,'production_ready':False}),'composite_claim_boundary')
    require(receipt.get('head',receipt.get('prospective_merge'))==commit,'composite_source_commit_context')
    require(type(receipt['repository']) is str and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',receipt['repository']) and len(receipt['repository'])<=200,'composite_repository_context')
    for field in ('run_id','run_attempt'):
        require(type(receipt[field]) is str and re.fullmatch(r'[1-9][0-9]{0,18}',receipt[field]),'composite_run_context')
    if 'workflows' in receipt:
        rows=receipt['workflows']
        require(type(rows) is list and len(rows)==55,'composite_required_workflow_denominator')
        fields={profile:identity_fields(token) for profile,(token,_) in documents.items()}
        for row in rows:
            require(type(row) is dict and same(row.get('schema_source_selection'),fields),'composite_workflow_source_binding')
    return {'schema':schema,'source_profiles':2,'authoritative_source_files_per_profile':18,
            'full_source_frontier_schema_version':5,'execution_schema_version':4,
            'account_transfer_admitted':False,'runtime_execution_verified':False,'accepted':False}

def head_document(token, *, commit, tree):
    require(type(commit) is str and type(tree) is str and all(re.fullmatch(r'[0-9a-f]{40}',v) for v in (commit,tree)), 'exact_candidate_context_required')
    # Caller must independently qualify every row against actual Git. Receiver
    # compares this document against a binding from its own exact-head custody.
    # This constructor alone is not Git proof and makes no kernel credit claim.
    proof=binding_document(token)
    return {'schema':'trillionnium.schema-source-exact-head-custody.v1',
            'commit':commit,'tree':tree,'source_selection_sha256':sha(canonical(proof)),
            'files':proof['full_source_inventory'],'producer_git_object_verification_required':True,
            'accepted':False,'compatibility_credit':False,'production_ready':False}

def write_annex(token, source_root, destination, *, commit, tree):
    proof=binding_document(token); root=Path(destination)
    # Check all source bytes before creating output; fifth omission cannot leave
    # a partially sealed source proof that is subsequently admitted.
    payloads={}; total=0
    for row in proof['full_source_inventory']:
        raw=SOURCE._regular(Path(source_root),row['path'],MAX_FILE_BYTES)
        require(len(raw)==row['bytes'] and sha(raw)==row['sha256'] and blob(raw)==row['git_blob_sha1'],'source_changed_since_token_issuance')
        total+=len(raw);require(total<=MAX_ANNEX_BYTES,'full_source_annex_byte_budget')
        payloads[row['archive_path']]=raw
    require(not (root/ANNEX).exists() and not (root/SIDECAR).exists() and not (root/HEAD_PROOF).exists(), 'source_annex_already_present')
    for name,raw in payloads.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    (root/SIDECAR).write_bytes(canonical(proof))
    (root/HEAD_PROOF).write_bytes(canonical(head_document(token,commit=commit,tree=tree)))

def validate_annex_files(token, files, *, commit, tree):
    expected=binding_document(token)
    require(SIDECAR in files and HEAD_PROOF in files, 'versioned_source_sidecars_required')
    require(same(strict(files[SIDECAR]),expected) and files[SIDECAR]==canonical(expected), 'source_sidecar_type_shape_or_value_or_exact_SHA_bytes')
    expected_head=head_document(token,commit=commit,tree=tree)
    require(same(strict(files[HEAD_PROOF]),expected_head) and files[HEAD_PROOF]==canonical(expected_head), 'source_head_custody_type_shape_or_value_or_exact_SHA_bytes')
    wanted={r['archive_path'] for r in expected['full_source_inventory']}
    actual={p for p in files if p==ANNEX or p.startswith(ANNEX+'/')}
    require(actual==wanted,'closed_full_source_annex_inventory')
    for row in expected['full_source_inventory']:
        raw=files[row['archive_path']]
        require(type(raw) is bytes and len(raw)==row['bytes'] and sha(raw)==row['sha256'] and blob(raw)==row['git_blob_sha1'], 'full_source_annex_byte_identity')
    return identity_fields(token)

def validate_annex_directory(token, root, *, commit, tree):
    proof=binding_document(token);root=Path(root);files={}
    for name in (SIDECAR,HEAD_PROOF):
        files[name]=SOURCE._regular(root,name,SOURCE.MAX_DOCUMENT_BYTES)
    annex=root/ANNEX
    require(annex.is_dir() and not annex.is_symlink(),'full_source_annex_directory_required')
    total=0
    pending=[ANNEX]; count=0
    while pending:
        relative=pending.pop()
        descriptors=[];flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        try:
            descriptors.append(os.open(root,flags))
            for part in Path(relative).parts:
                descriptors.append(os.open(part,flags,dir_fd=descriptors[-1]))
            with os.scandir(descriptors[-1]) as entries:
                for entry in entries:
                    count+=1;require(count<=64 and not entry.is_symlink(),'full_source_annex_inventory_budget_or_symlink')
                    name=relative+'/'+entry.name
                    if entry.is_dir(follow_symlinks=False):
                        require(len(Path(name).parts)<=6,'full_source_annex_depth_budget')
                        pending.append(name);continue
                    raw=SOURCE._regular(root,name,MAX_FILE_BYTES);total+=len(raw)
                    require(total<=MAX_ANNEX_BYTES,'full_source_annex_byte_budget');files[name]=raw
        except OSError as error:
            raise BindingError('full_source_annex_inventory_open_failed') from error
        finally:
            for descriptor in reversed(descriptors):os.close(descriptor)
    return validate_annex_files(token,files,commit=commit,tree=tree)
