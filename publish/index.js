'use strict';
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
for (const key of ['GITHUB_WORKSPACE', 'RUNNER_TEMP', 'ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL']) {
  if (!process.env[key]) throw new Error('Required artifact context missing: ' + key);
}
const packet = process.env.WORLD_DIAGNOSTIC_PACKET;
if (!['world-source-9a57222', 'world-native-9a57222'].includes(packet)) {
  throw new Error('Unsupported diagnostic packet');
}
const uploader = path.join(process.env.GITHUB_WORKSPACE, 'uploader/scripts/upload-actions-artifact.py');
const crypto = require('node:crypto');
const data = fs.readFileSync(uploader);
const objectId = crypto.createHash('sha1').update(Buffer.from('blob ' + data.length + '\0')).update(data).digest('hex');
if (objectId !== '8baac2cca878af822428831359ab00d15ab10b71') throw new Error('Uploader identity changed');
const env = {};
for (const key of ['PATH', 'HOME', 'LANG', 'ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL']) {
  if (process.env[key]) env[key] = process.env[key];
}
const result = spawnSync('/usr/bin/python3', [
  uploader, packet, path.join(process.env.RUNNER_TEMP, 'world-diagnostic', packet + '.zip'),
  '--mime-type', 'application/zip'
], {env, stdio: 'inherit', timeout: 180000, shell: false});
if (result.error || result.status !== 0) process.exit(1);
