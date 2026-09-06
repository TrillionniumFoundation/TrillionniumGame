'use strict';
const {spawnSync} = require('node:child_process');
const path = require('node:path');

for (const key of [
  'GITHUB_WORKSPACE',
  'ACTIONS_RUNTIME_TOKEN',
  'ACTIONS_RESULTS_URL',
  'INPUT_NAME',
  'INPUT_PATH',
  'INPUT_MIME_TYPE',
]) {
  if (!process.env[key]) throw new Error('Required artifact context missing: ' + key);
}

const artifactPath = path.isAbsolute(process.env.INPUT_PATH)
  ? path.normalize(process.env.INPUT_PATH)
  : path.resolve(process.env.GITHUB_WORKSPACE, process.env.INPUT_PATH);
const uploader = path.join(
  process.env.GITHUB_WORKSPACE,
  'source/scripts/upload-actions-artifact.py',
);
const env = {...process.env};
delete env.GITHUB_TOKEN;
delete env.GH_TOKEN;
delete env.GH_BLOB_TOKEN;

const result = spawnSync('/usr/bin/python3', [
  uploader,
  process.env.INPUT_NAME,
  artifactPath,
  '--mime-type',
  process.env.INPUT_MIME_TYPE,
], {
  env,
  stdio: 'inherit',
  timeout: 180000,
  shell: false,
});
if (result.error || result.status !== 0) process.exit(1);
