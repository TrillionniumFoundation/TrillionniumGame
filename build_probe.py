#!/usr/bin/env python3
"""Supplementary ARM Rust build on fixed source and actual merge objects.
Not the repository's required x64 gates, live database evidence, or acceptance.
"""
from __future__ import annotations
import argparse, hashlib, json, os, platform, re, selectors, signal, subprocess, time, tomllib, zipfile
from pathlib import Path

HEAD = 'be2be89efa3984e82b4e8584e2b8d6e7e315c9ca'
BASE = 'd66c1b5b614a2a7b682c233fe2e7a19939b6976b'
MERGE = 'a630c3fab4113f5e7a8fd86252cf0f586be3aca7'
TREE = '7aaa04dd376c6beade753d610f0649fd9db526bf'
REPO = 'TrillionniumFoundation/TrillionniumGame'
MAX_LOG = 8 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
SUMMARY = re.compile(r'^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;', re.M)

class ProbeError(RuntimeError): pass

def need(ok, message):
    if not ok: raise ProbeError(message)

def command(argv, cwd, log, env, budget=600, limit=MAX_LOG):
    """Bound stdout/stderr retention and kill the process group on overflow/timeout."""
    need(budget > 0 and limit > 0, 'positive command budgets required')
    started = time.monotonic()
    count, reason = 0, None
    with log.open('xb') as output:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(proc.stdout, selectors.EVENT_READ)
                while sel.get_map():
                    remaining = budget - (time.monotonic() - started)
                    if remaining <= 0:
                        reason = 'command-timeout'; break
                    for key, _ in sel.select(min(remaining, 0.5)):
                        block = os.read(key.fileobj.fileno(), min(65536, limit + 1 - count))
                        if not block:
                            sel.unregister(key.fileobj); continue
                        allowed = min(len(block), limit - count)
                        output.write(block[:allowed]); count += len(block)
                        if count > limit:
                            reason = 'log-byte-budget'; break
                    if reason: break
                if reason:
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                try: rc = proc.wait(timeout=max(0.1, budget - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    reason = 'command-timeout'
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    rc = proc.wait(timeout=5)
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait(timeout=5)
    data = log.read_bytes()
    return {'argv': argv, 'returncode': rc, 'failure': reason,
            'elapsed_seconds': round(time.monotonic()-started, 3),
            'log': log.name, 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

def test_summary(text):
    matches = SUMMARY.findall(text)
    need(bool(matches), 'Rust test summaries absent')
    counts = [tuple(map(int, x)) for x in matches]
    need(sum(c[0] for c in counts) > 0, 'zero effective Rust test count')
    need(all(c[1] == 0 and c[2] == 0 for c in counts), 'failed or ignored Rust tests')
    return {'reported_passed': sum(c[0] for c in counts), 'failed': 0, 'ignored': 0,
            'test_binary_summaries': len(counts), 'live_database_credit': False}

def scopes(source):
    cargo = tomllib.loads((source/'Cargo.toml').read_text())['workspace']
    registry = json.loads((source/'docs/development/RUST_PACKAGE_AUTHORITY.json').read_text())
    members = cargo['members']; excludes = cargo['exclude']
    isolated = [x['manifest'] for x in registry['isolated_workspaces']]
    need(set(members) == set(registry['workspace']['members']), 'root authority mismatch')
    need(set(isolated) == {x+'/Cargo.toml' for x in excludes}, 'isolated authority mismatch')
    need(len(members)==11 and len(isolated)==10 and len(set(isolated))==10, 'package count changed')
    found = {str(p.relative_to(source)) for p in (source/'crates').glob('*/Cargo.toml')}
    need(found == {x+'/Cargo.toml' for x in members} | set(isolated), 'unmapped package')
    return [('root-workspace', ['--workspace'], ['--all'])] + [
        (Path(p).parent.name, ['--manifest-path', p], ['--manifest-path', p]) for p in isolated]

def git(source, *args):
    return subprocess.run(['git','-C',str(source),*args], check=True, capture_output=True, timeout=20).stdout

def verify_object(source, sha):
    raw = git(source, 'cat-file', 'commit', sha)
    need(hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==sha, 'commit digest mismatch')
    need(raw.startswith(('tree '+TREE+'\n').encode()), 'object tree mismatch')
    if sha == MERGE:
        parents = re.findall(rb'^parent ([0-9a-f]{40})$', raw.split(b'\n\n',1)[0], re.M)
        need(parents == [BASE.encode(), HEAD.encode()], 'merge parent order mismatch')
    return raw

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args(); source=args.source.resolve(); out=args.output.resolve(); out.mkdir(exist_ok=False)
    env={k:os.environ[k] for k in ('PATH','HOME','USER','TMPDIR','RUSTUP_HOME','CARGO_HOME') if k in os.environ}
    env.update(CARGO_TERM_COLOR='never', RUST_BACKTRACE='1', CARGO_BUILD_JOBS='2',
               CARGO_TARGET_DIR=str(out/'cargo-target'), GOTOOLCHAIN='auto')
    receipt={'schema':'trillionnium.supplementary-rust-matrix.v1','repository':REPO,
             'source_head':HEAD,'base':BASE,'prospective_merge':MERGE,'tree':TREE,
             'platform':platform.platform(), 'architecture':platform.machine(),
             'producer':{k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_JOB')},
             'commands':[], 'claims':{'accepted':False,'gap_closed':False,'ordinary_gate_passed':False,'live_database_verified':False},
             'limitations':['Supplementary ARM profile, not required x64 source/prospective workflow IDs.',
              'Cargo unit tests may contain explicit developer opt-outs for live fixtures; no database credit.',
              'Repeated execution of the same tests on two objects is not two sets of unique tests.',
              'Independent acceptance, database, oracle, performance and governance obligations remain.'],
             'complete':False}
    rows=receipt['commands']; began=time.monotonic(); passed=True
    try:
        for sha in (HEAD,MERGE): (out/(sha+'.commit')).write_bytes(verify_object(source,sha))
        for label,sha in (('source',HEAD),('prospective',MERGE)):
            need(not git(source,'status','--porcelain').strip(),'dirty source')
            git(source,'checkout','--detach',sha)
            need(git(source,'rev-parse','HEAD').decode().strip()==sha,'wrong checkout')
            plan=scopes(source)
            receipt.setdefault('packages',{'root_count':11,'isolated_manifests':[r[1][1] for r in plan[1:]]})
            schedule=[('inventory',['python3','scripts/check-rust-package-inventory.py'],False)]
            # Obtain the existing full-width regression on each object explicitly.
            schedule.append(('length-regression',['cargo','+1.85.1','test','--manifest-path','crates/trnm-token-jwt-adapter/Cargo.toml','--locked','--lib','sha256::tests::constant_time_comparison_handles_lengths_and_values','--','--exact','--nocapture'],True))
            for name,test_args,fmt_args in plan:
                schedule.extend([(name+'-format',['cargo','+1.85.1','fmt',*fmt_args,'--','--check'],False),
                    (name+'-test',['cargo','+1.85.1','test',*test_args,'--all-targets','--locked','--','--nocapture'],True),
                    (name+'-lint',['cargo','+1.85.1','clippy',*test_args,'--all-targets','--locked','--','-D','warnings'],False)])
            for name,argv,is_test in schedule:
                remaining=2400-(time.monotonic()-began); need(remaining>0,'total command deadline')
                log=out/f'{len(rows):03d}-{label}-{name}.log'
                print('EXECUTE',label,name,flush=True)
                row=command(argv,source,log,env,min(900,remaining)); row.update(object=sha,scope=label,name=name)
                rows.append(row)
                need(row['returncode']==0 and row['failure'] is None,'command failed: '+name)
                if is_test:
                    row['tests']=test_summary(log.read_text(errors='replace'))
                    if name=='length-regression': need(row['tests']['reported_passed']==1,'exact regression was not one test')
                need(sum(r['size_bytes'] for r in rows)<=MAX_TOTAL,'total logs budget')
            need(not git(source,'status','--porcelain').strip(),'test mutated tracked source')
        receipt['complete']=True
    except Exception as exc:
        passed=False;receipt['failure_type']=type(exc).__name__;receipt['failure']=str(exc)[:500]
    finally:
        for exe in (['rustc','+1.85.1','--version','--verbose'], ['cargo','+1.85.1','--version','--verbose']):
            try:
                s=subprocess.run(exe,env=env,capture_output=True,text=True,timeout=10)
                receipt.setdefault('toolchain',[]).append({'command':exe,'exit':s.returncode,'stdout':s.stdout[:4000]})
            except Exception as exc:receipt.setdefault('toolchain_errors',[]).append(type(exc).__name__)
        (out/'matrix.json').write_text(json.dumps(receipt,indent=2)+'\n')
        files={p.name:p.read_bytes() for p in out.iterdir() if p.is_file()}
        need(sum(map(len,files.values()))<=MAX_TOTAL+1024*1024,'packet limit')
        files['file-index.json']=(json.dumps([{'path':n,'size_bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()} for n,d in sorted(files.items())],indent=2)+'\n').encode()
        with zipfile.ZipFile(out/'rust-matrix.zip','x',zipfile.ZIP_DEFLATED) as z:
            for name,data in sorted(files.items()):z.writestr(name,data)
        print(json.dumps({'complete':receipt['complete'],'commands':len(rows),'packet_sha256':hashlib.sha256((out/'rust-matrix.zip').read_bytes()).hexdigest()}),flush=True)
    # Always retain failed diagnostics; the workflow's final explicit check fails
    # the job when complete=false. Upload success never overrides matrix failure.
    return 0

if __name__=='__main__': raise SystemExit(main())
