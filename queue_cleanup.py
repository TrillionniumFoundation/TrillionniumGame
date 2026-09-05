#!/usr/bin/env python3
"""Cancel exactly one superseded, unassigned PR-63 run; never current checks.

One-shot operational cleanup, not an evidence gate or approval. Dry-run by
 default. Uses only the repository-scoped Actions token and a fixed cancel URL.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

REPO = 'TrillionniumFoundation/TrillionniumGame'
REPO_ID = 1323087470
BRANCH = 'codex/branch-evidence-closure-2026-09-02'
HEAD = 'be2be89efa3984e82b4e8584e2b8d6e7e315c9ca'
BASE = 'd66c1b5b614a2a7b682c233fe2e7a19939b6976b'
STALE_HEAD = 'c2944df15702de77e8bbe549158019f84a53a38e'
RUN = 33951787286
WORKFLOW = 347178559
RUN_PATH = f'/actions/runs/{RUN}'

class CleanupError(RuntimeError):
    pass

def need(condition, message):
    if not condition:
        raise CleanupError(message)

def current(pr):
    need(pr['number'] == 63 and pr['state'] == 'open' and pr['draft'] is True
         and pr['merged'] is False, 'PR state changed')
    need(pr['head']['sha'] == HEAD and pr['base']['sha'] == BASE
         and pr['head']['ref'] == BRANCH and pr['base']['ref'] == 'main', 'candidate moved')
    need(pr['head']['repo']['id'] == REPO_ID and pr['base']['repo']['id'] == REPO_ID,
         'repository identity changed')

def eligible(run, jobs):
    need(run['id'] == RUN and run['head_sha'] == STALE_HEAD
         and run['head_branch'] == BRANCH and run['event'] == 'pull_request'
         and run['workflow_id'] == WORKFLOW
         and run['path'] == '.github/workflows/prospective-merge-gate.yml'
         and run['repository']['id'] == REPO_ID and run['head_repository']['id'] == REPO_ID,
         'not the fixed superseded run')
    need(run['run_attempt'] == 1 and type(run['run_attempt']) is int, 'run attempt changed')
    need([p['number'] for p in run['pull_requests']] == [63], 'different PR association')
    need(run['status'] == 'queued' and run['conclusion'] is None, 'old run no longer queued')
    rows = jobs['jobs']
    need(type(jobs['total_count']) is int and jobs['total_count'] == len(rows)
         and 0 < len(rows) <= 100, 'empty or incomplete job collection')
    need(len({j['id'] for j in rows}) == len(rows), 'duplicate job IDs')
    for j in rows:
        need(j['run_id'] == RUN and j['run_attempt'] == 1 and j['head_sha'] == STALE_HEAD
             and j['status'] == 'queued' and j['conclusion'] is None and j['runner_id'] == 0
             and j['steps'] == [], 'a job has execution or changed identity')
    return [j['id'] for j in rows]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CleanupError('GitHub API redirect refused')

class Client:
    def __init__(self, token, output):
        self.token, self.output, self.sequence = token, output, 0
        self.opener = urllib.request.build_opener(NoRedirect())
    def request(self, path, method='GET'):
        allowed_get = {'/pulls/63', f'/git/commits/{HEAD}', RUN_PATH,
                       RUN_PATH + '/jobs?per_page=100'}
        need((method == 'GET' and path in allowed_get) or
             (method == 'POST' and path == RUN_PATH + '/cancel'), 'API operation not allowlisted')
        req = urllib.request.Request('https://api.github.com/repos/' + REPO + path,
            data=b'' if method == 'POST' else None, method=method,
            headers={'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28',
                     'User-Agent':'trnm-fixed-stale-queue-cleanup'})
        req.add_unredirected_header('Authorization', 'Bearer ' + self.token)
        try:
            with self.opener.open(req, timeout=20) as response:
                status = response.status
                body = response.read(1024*1024+1)
                need(len(body) <= 1024*1024, 'API response exceeds bound')
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise CleanupError(f'GitHub HTTP {status}; no retry or force-cancel') from None
        value = json.loads(body) if body.strip() else {}
        self.sequence += 1
        (self.output/f'{self.sequence:02d}-{method}.json').write_text(
            json.dumps({'method':method,'path':path,'status':status,'response':value},indent=2)+'\n')
        need(status == (202 if method == 'POST' else 200), 'unexpected API status')
        return value

def perform(client, apply=False, sleeper=time.sleep):
    current(client.request('/pulls/63'))
    commit = client.request(f'/git/commits/{HEAD}')
    need(commit['sha'] == HEAD and [p['sha'] for p in commit['parents']] == [STALE_HEAD],
         'superseded source is not the fixed direct parent')
    run = client.request(RUN_PATH)
    jobs = client.request(RUN_PATH+'/jobs?per_page=100')
    ids = eligible(run,jobs)
    result={'repository':REPO,'current_head':HEAD,'stale_head':STALE_HEAD,'run_id':RUN,
            'run_attempt':1,'queued_job_ids':ids,'cancel_requested':False,
            'cancel_confirmed':False,'current_candidate_modified':False,'gap_closed':False,
            'limitation':'Removing obsolete queued work does not prove the cause or resolution of current runner allocation delay.'}
    if not apply:
        result['mode']='dry-run'
        return result
    current(client.request('/pulls/63'))
    again = eligible(client.request(RUN_PATH),client.request(RUN_PATH+'/jobs?per_page=100'))
    need(again == ids, 'job collection changed before cancellation')
    client.request(RUN_PATH+'/cancel', 'POST')
    result['mode']='apply'
    result['cancel_requested']=True
    for _ in range(3):
        after=client.request(RUN_PATH)
        need(after['id']==RUN and after['head_sha']==STALE_HEAD and after['run_attempt']==1,
             'after-state identity changed')
        result['after_status']=after['status'];result['after_conclusion']=after['conclusion']
        if after['status']=='completed':
            result['cancel_confirmed']=after['conclusion']=='cancelled'
            break
        sleeper(1)
    current(client.request('/pulls/63'))
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(exist_ok=False)
    try:
        token=os.environ['GITHUB_TOKEN']
        result=perform(Client(token,args.output),args.apply)
    except (CleanupError,KeyError,OSError,ValueError) as error:
        result={'cancel_confirmed':False,'gap_closed':False,'failure_type':type(error).__name__}
        if isinstance(error,CleanupError):result['failure']=str(error)
        (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result));return 1
    (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));return 0

if __name__=='__main__':raise SystemExit(main())
