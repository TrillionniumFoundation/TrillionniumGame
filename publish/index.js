'use strict';
const {spawnSync} = require('node:child_process');
const path = require('node:path');
for (const key of ['GITHUB_WORKSPACE', 'RUNNER_TEMP', 'ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL']) {
  if (!process.env[key]) throw new Error('Required artifact context missing: ' + key);
}
const env = {...process.env};
delete env.GITHUB_TOKEN;
delete env.GH_BLOB_TOKEN;
const result = spawnSync('/usr/bin/python3', [
  path.join(process.env.GITHUB_WORKSPACE, 'source/scripts/upload-actions-artifact.py'),
  'trnm-status-transition-preflight-00ca7f81',
  path.join(process.env.RUNNER_TEMP, 'status-transition-proof/status-transition-preflight.zip'),
  '--mime-type', 'application/zip'
], {env, stdio: 'inherit', timeout: 180000, shell: false});
if (result.error || result.status !== 0) process.exit(1);
