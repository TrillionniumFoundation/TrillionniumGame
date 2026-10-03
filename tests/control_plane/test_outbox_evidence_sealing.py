"""Regression coverage for final-attempt outbox evidence sealing."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import textwrap
from unittest import mock
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci-outbox-final-attempt-reaper.sh"
WORKFLOW = ROOT / ".github/workflows/prospective-merge-gate.yml"


class OutboxEvidenceSealingTests(unittest.TestCase):
    def test_logger_is_joined_before_manifest_generation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        ordered = (
            "exec 3>&1 4>&2",
            'exec > >(tee "$evidence/logs/run.log" >&3) 2>&1',
            "tee_pid=$!",
            'cat "$evidence/result.env"',
            "exec 1>&3 2>&4",
            "exec 3>&- 4>&-",
            'wait "$tee_pid"',
            "find . -type f ! -path './files.sha256' -print0",
            "sha256sum --check files.sha256",
        )
        positions = []
        for marker in ordered:
            self.assertEqual(source.count(marker), 1, marker)
            positions.append(source.index(marker))
        self.assertEqual(positions, sorted(positions))

    def test_prospective_packet_checks_outbox_member_manifest(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        required = 'test -s "$outbox/files.sha256"'
        verified = '(cd "$outbox" && sha256sum --check files.sha256)'
        archive = 'archive="run/prospective-merge-${PROFILE}-${PROSPECTIVE_MERGE_SHA}.tar.gz"'
        self.assertEqual(source.count(required), 1)
        self.assertEqual(source.count(verified), 1)
        self.assertLess(source.index(required), source.index(verified))
        self.assertLess(source.index(verified), source.index(archive))

    def spool_identity_body(self):
        workflow=(ROOT/".github/workflows/outbox-spool-worker.yml").read_text()
        start=workflow.index("          import hashlib,importlib.util,json,math,os,re,stat,subprocess,sys")
        end=workflow.index("          PY_SPOOL_SOURCE",start)
        return textwrap.dedent(workflow[start:end])

    def test_spool_seal_binds_explicit_leaf_and_same_run_id_as_execution(self):
        workflow=(ROOT/".github/workflows/outbox-spool-worker.yml").read_text()
        self.assertEqual(workflow.count("EVIDENCE_RUN_ID: ${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.profile }}"),1)
        self.assertIn('evidence_root="run/outbox-spool-worker/${PROFILE}/${EVIDENCE_RUN_ID}"',workflow)
        self.assertNotIn('evidence_root="run/outbox-spool-worker/${PROFILE}"',workflow)
        self.assertNotIn('mkdir -p "$evidence_root"',workflow)
        self.assertIn('TRNM_RUN_ID: ${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.profile }}',workflow)
        self.assertIn('binding.validate_annex_directory(token,root,commit=commit,tree=tree)',workflow)
        self.assertIn("schemas.validate_identity(report,profile=profile,selection=binding.selection_token(token),mode='fresh',source_commit=commit)",workflow)
        for profile in ("postgresql","cockroachdb"):
            self.exercise_spool_identity(profile)

    def exercise_spool_identity(self,profile,mutation=None,prepare=None,leaf_mutation=None):
        # Actual source issuer, annex verifier, identity validator and producer;
        # only the current Git tree read is mocked. No native execution credit.
        head='a'*40;tree='b'*40;run_id='123-2-'+profile
        import_paths=list(sys.path);sys.path.insert(0,str(ROOT/'scripts'))
        try:
            import schema_evidence_binding as binding
            token=binding.verify_binding(ROOT,profile=profile);proof=binding.binding_document(token)
            selected=proof['source_selection']['selection'];first=selected['ordered_files'][0]
            producer=(ROOT/'scripts/ci-outbox-spool-worker.sh').read_text()
            marker='python3 - "$evidence" <<' + "'PY'" + '\n'
            begin=producer.index(marker)+len(marker);producer_body=producer[begin:producer.index('\nPY\n',begin)]
            with TemporaryDirectory() as directory:
                old=os.getcwd();os.chdir(directory)
                try:
                    for row in proof['full_source_inventory']:
                        destination=Path(row['path']);destination.parent.mkdir(parents=True,exist_ok=True)
                        destination.write_bytes((ROOT/row['path']).read_bytes())
                    root=Path('run/outbox-spool-worker')/profile/run_id;root.mkdir(parents=True)
                    binding.write_annex(token,ROOT,root,commit=head,tree=tree)
                    identity={'schema':'trillionnium.authoritative-schema-report.v1','profile':profile,'schema_version':4,'storage_writer_epoch':4,'chain_digest':selected['execution_chain_digest'],'digest_algorithm':binding.SOURCE.ALGORITHM,'table_count':12,'source_commit':head,'upgrade_source_commit':head,'v2_apply_source_commit':head,'v3_apply_source_commit':head,'migration_applied':True,'applied_steps':4,'compatibility_credit':False}
                    (root/'schema-identity.json').write_bytes(binding.canonical(identity))
                    (root/'migration-chain-validation.json').write_bytes(binding.canonical(proof['source_selection']['source']['complete_validation']))
                    source={'repository':'TrillionniumFoundation/TrillionniumGame','commit':head,'tree':tree,'profile':profile,'image':proof['image'],'foundation_migration':first['path'],'foundation_migration_sha256':hashlib.sha256((ROOT/first['path']).read_bytes()).hexdigest(),'run_id':run_id}
                    (root/'source.txt').write_text(''.join(key+'='+value+'\n' for key,value in source.items()))
                    if prepare:prepare(root)
                    with mock.patch.object(sys,'argv',['<actual-pure-spool-manifest-producer>',str(root)]):
                        exec(compile(producer_body,'<actual-pure-spool-manifest-producer>','exec'),{})
                    manifest=json.loads((root/'manifest.json').read_bytes())
                    env={'EVIDENCE_ROOT':str(root),'PROFILE':profile,'CANDIDATE_REPOSITORY':source['repository'],'CANDIDATE_SHA':head,'EVIDENCE_RUN_ID':run_id,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2'}
                    if mutation:mutation(source,manifest,env)
                    (root/'source.txt').write_text(''.join(key+'='+value+'\n' for key,value in source.items()))
                    (root/'manifest.json').write_text(json.dumps(manifest))
                    if leaf_mutation:leaf_mutation(root,manifest,env)
                    with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(subprocess,'check_output',return_value=tree+'\n') as current_tree:
                        exec(compile(self.spool_identity_body(),'<actual-full-spool-sealer>','exec'),{})
                    current_tree.assert_called_once_with(['git','rev-parse','HEAD^{tree}'],text=True)
                finally:os.chdir(old)
        finally:sys.path[:]=import_paths

    def test_spool_parent_sibling_stale_profile_and_object_identity_rejected(self):
        mutations=(
            lambda s,m,e:e.update(EVIDENCE_ROOT='run/outbox-spool-worker/'+e['PROFILE']),
            lambda s,m,e:e.update(EVIDENCE_ROOT='run/outbox-spool-worker/'+e['PROFILE']+'/999-2-'+e['PROFILE']),
            lambda s,m,e:e.update(EVIDENCE_RUN_ID='123-1-'+e['PROFILE']),
            lambda s,m,e:s.update(commit='c'*40),
            lambda s,m,e:s.update(tree='c'*40),
            lambda s,m,e:s.update(profile='wrong'),
            lambda s,m,e:m.update(run_id='999-2-'+e['PROFILE']),
            lambda s,m,e:m.update(target_commit='c'*40),
            lambda s,m,e:m.update(target_tree='c'*40),
            lambda s,m,e:e.update(CANDIDATE_SHA='not-a-git-object'),
            lambda s,m,e:m.update(schema='wrong'),
            lambda s,m,e:m['claims'].update(compatibility_credit=True),
            lambda s,m,e:m['claims'].update(production_ready=0),
        )
        for profile in ('postgresql','cockroachdb'):
            for index,mutation in enumerate(mutations):
                with self.subTest(profile=profile,index=index),self.assertRaises((SystemExit,FileNotFoundError)):
                    self.exercise_spool_identity(profile,mutation)

    def test_spool_complete_manifest_rejects_embedded_binding_and_extra_claims(self):
        mutations=(
            lambda s,m,e:m['schema_identity'].update(schema_version=5,table_count=14),
            lambda s,m,e:m['schema_identity'].update(schema_version=True),
            lambda s,m,e:m.update(migration_chain_validation={'schema_version':1}),
            lambda s,m,e:m.update(image='wrong@sha256:'+'0'*64),
            lambda s,m,e:s.update(image='wrong@sha256:'+'0'*64),
            lambda s,m,e:m['claims'].update(accepted=True),
            lambda s,m,e:m['claims'].update(single_node_dual_profile_source_slice_executed=1),
            lambda s,m,e:m['claims'].pop('external_effect_provider_executed'),
            lambda s,m,e:m.update(foundation_migration='other.sql'),
            lambda s,m,e:s.update(foundation_migration='other.sql'),
            lambda s,m,e:m.update(foundation_migration_sha256='0'*64),
            lambda s,m,e:s.update(foundation_migration_sha256='0'*64),
            lambda s,m,e:m['assertions'].update(completed_count=True),
            lambda s,m,e:m['assertions'].update(accepted=True),
            lambda s,m,e:m.update(limitations=[]),
            lambda s,m,e:m.update(accepted=True),
            lambda s,m,e:m.pop('limitations'),
            lambda s,m,e:s.update(accepted='true'),
            lambda s,m,e:s.pop('foundation_migration'),
        )
        for profile in ('postgresql','cockroachdb'):
            for index,mutation in enumerate(mutations):
                with self.subTest(profile=profile,index=index),self.assertRaises(SystemExit):
                    self.exercise_spool_identity(profile,mutation)

    def test_spool_artifacts_bind_complete_actual_members_digest_size_and_row_types(self):
        mutations=(
            lambda s,m,e:m.update(artifacts=[{'path':'schema-identity.json','sha256':'0'*64,'size_bytes':1}]),
            lambda s,m,e:m['artifacts'].pop(),
            lambda s,m,e:m['artifacts'].append({'path':'absent.log','sha256':'0'*64,'size_bytes':0}),
            lambda s,m,e:m['artifacts'].append(dict(m['artifacts'][0])),
            lambda s,m,e:m['artifacts'][0].update(sha256='0'*64),
            lambda s,m,e:m['artifacts'][0].update(size_bytes=m['artifacts'][0]['size_bytes']+1),
            lambda s,m,e:m['artifacts'][0].update(size_bytes=True),
            lambda s,m,e:m['artifacts'][0].update(sha256=None),
            lambda s,m,e:m['artifacts'][0].update(size_bytes=None),
            lambda s,m,e:m['artifacts'][0].update(path='../source.txt'),
            lambda s,m,e:m['artifacts'][0].update(extra=True),
            lambda s,m,e:m.update(artifacts=list(reversed(m['artifacts']))),
        )
        for profile in ('postgresql','cockroachdb'):
            for index,mutation in enumerate(mutations):
                with self.subTest(profile=profile,index=index),self.assertRaises(SystemExit):
                    self.exercise_spool_identity(profile,mutation)
        for profile in ('postgresql','cockroachdb'):
            with self.subTest(profile=profile),self.assertRaises(SystemExit):
                self.exercise_spool_identity(profile,leaf_mutation=lambda r,m,e:(r/'new-unlisted.log').write_bytes(b'new member'))

    def test_spool_dynamic_empty_members_and_exact_producer_basename_exclusions(self):
        def prepare(root):
            nested=root/'logs'/'arbitrary-node-17';nested.mkdir(parents=True)
            (nested/'empty.stderr').write_bytes(b'')
            (nested/'generated-receipt.bin').write_bytes(bytes(range(256)))
            (nested/'manifest.json').write_bytes(b'excluded by actual producer basename')
            (nested/'SHA256SUMS').write_bytes(b'excluded by actual producer basename')
        for profile in ('postgresql','cockroachdb'):
            with self.subTest(profile=profile):self.exercise_spool_identity(profile,prepare=prepare)

    def test_spool_artifact_order_matches_producer_path_components_for_prefix_siblings(self):
        # Actual producer uses sorted(Path.rglob), where a directory component
        # precedes its same-prefix sibling file; string ordering differs here.
        paths=('logs/a/child.log','logs/a.log','spool/node/empty.stderr','spool/node.log')
        self.assertNotEqual(sorted(paths),[str(path) for path in sorted(map(Path,paths))])
        def prepare(root):
            for relative in paths:
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b'' if path.name=='empty.stderr' else b'dynamic member')
        for profile in ('postgresql','cockroachdb'):
            with self.subTest(profile=profile):self.exercise_spool_identity(profile,prepare=prepare)
            with self.subTest(profile=profile,reversed=True),self.assertRaises(SystemExit):
                self.exercise_spool_identity(profile,prepare=prepare,mutation=lambda s,m,e:m.update(artifacts=list(reversed(m['artifacts']))))

    def test_spool_duplicate_nonfinite_json_and_unsafe_member_are_rejected(self):
        def duplicate(root,manifest,env):
            raw=(root/'manifest.json').read_text()
            (root/'manifest.json').write_text(raw[:-1]+',"run_id":'+json.dumps(env['EVIDENCE_RUN_ID'])+'}')
        def nonfinite(root,manifest,env):
            manifest['assertions']['completed_count']=float('inf')
            (root/'manifest.json').write_text(json.dumps(manifest))
        def duplicate_source(root,manifest,env):
            with (root/'source.txt').open('a') as stream:stream.write('profile='+env['PROFILE']+'\n')
        def symlink(root,manifest,env):
            (root/'symlink-member').symlink_to('source.txt')
        for profile in ('postgresql','cockroachdb'):
            for mutation in (duplicate,nonfinite,duplicate_source,symlink):
                with self.subTest(profile=profile,mutation=mutation.__name__),self.assertRaises(SystemExit):
                    self.exercise_spool_identity(profile,leaf_mutation=mutation)

    def test_spool_bounded_inventory_rejects_oversize_exclusions_and_excess_entries(self):
        def oversize(root,manifest,env):
            with (root/'oversize.log').open('wb') as stream:stream.truncate(67108865)
        def oversize_excluded(root,manifest,env):
            with (root/'SHA256SUMS').open('wb') as stream:stream.truncate(67108865)
        def excess_entries(root,manifest,env):
            directory=root/'many-members';directory.mkdir()
            for index in range(1025):(directory/str(index)).write_bytes(b'')
        for profile in ('postgresql','cockroachdb'):
            for mutation in (oversize,oversize_excluded,excess_entries):
                with self.subTest(profile=profile,mutation=mutation.__name__),self.assertRaises(SystemExit):
                    self.exercise_spool_identity(profile,leaf_mutation=mutation)

    @unittest.skipUnless(
        shutil.which("bash") and shutil.which("sha256sum"),
        "requires bash and GNU sha256sum",
    )
    def test_source_derived_seal_is_stable_and_fail_closed(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        setup_start = source.index("exec 3>&1 4>&2")
        setup_end = source.index("\n\ncontainer=", setup_start)
        setup = source[setup_start:setup_end]
        tail_start = source.index("printf 'status=passed\\nprofile=%s\\ncommit=%s\\n'")
        tail = source[tail_start:]

        with TemporaryDirectory() as directory:
            evidence = Path(directory) / "packet"
            evidence.mkdir()
            (evidence / "logs").mkdir()
            harness = "\n".join(
                (
                    "set -Eeuo pipefail",
                    "profile=postgresql",
                    f"commit={'a' * 40}",
                    f"evidence={str(evidence)!r}",
                    setup,
                    "printf 'fixture-log\\n'",
                    tail,
                )
            )
            completed = subprocess.run(
                ["bash", "-c", harness],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            manifest = evidence / "files.sha256"
            lines = manifest.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines)
            names = [line.split("  ", 1)[1] for line in lines]
            self.assertEqual(names, sorted(names))
            self.assertNotIn("./files.sha256", names)
            self.assertEqual(names, ["./logs/run.log", "./result.env"])

            for line in lines:
                expected, relative = line.split("  ", 1)
                actual = hashlib.sha256((evidence / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)

            with (evidence / "logs/run.log").open("ab") as stream:
                stream.write(b"post-seal mutation\n")
            rejected = subprocess.run(
                ["sha256sum", "--check", "files.sha256"],
                cwd=evidence,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("FAILED", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
