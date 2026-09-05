'use strict';
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const env = {...process.env};
delete env.GITHUB_TOKEN;
const r = spawnSync('/usr/bin/python3', [
  path.join(env.GITHUB_WORKSPACE, 'source/scripts/upload-actions-artifact.py'),
  'pr63-stale-queue-cleanup', path.join(env.RUNNER_TEMP, 'queue-cleanup.zip'),
  '--mime-type', 'application/zip'
], {env, stdio:'inherit', timeout:180000, shell:false});
if (r.error || r.status !== 0) process.exit(1);
