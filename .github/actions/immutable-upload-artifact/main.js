'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const UPSTREAM_REPOSITORY = 'https://github.com/actions/upload-artifact.git';
const UPSTREAM_COMMIT = '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a';

function fail(message) {
  process.stderr.write(`immutable-upload-artifact: ${message}\n`);
  process.exit(1);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    ...options,
  });
  if (result.error) {
    fail(`${command} failed to start: ${result.error.message}`);
  }
  if (result.status !== 0) {
    if (options.capture) {
      process.stderr.write(result.stdout || '');
      process.stderr.write(result.stderr || '');
    }
    fail(`${command} exited with status ${result.status}`);
  }
  return result.stdout ? result.stdout.trim() : '';
}

if (!process.env.ACTIONS_RUNTIME_TOKEN) {
  fail('runner did not inject ACTIONS_RUNTIME_TOKEN into the local JavaScript action');
}
if (!process.env.ACTIONS_RESULTS_URL && !process.env.ACTIONS_RUNTIME_URL) {
  fail('runner did not inject an artifact service URL');
}
if (!process.env.RUNNER_TEMP) {
  fail('RUNNER_TEMP is missing');
}

const checkout = path.join(process.env.RUNNER_TEMP, `upload-artifact-${UPSTREAM_COMMIT}`);
fs.rmSync(checkout, { recursive: true, force: true });
fs.mkdirSync(checkout, { recursive: true });
run('git', ['init', checkout]);
run('git', ['-C', checkout, 'remote', 'add', 'origin', UPSTREAM_REPOSITORY]);
run('git', ['-C', checkout, 'fetch', '--no-tags', '--depth=1', 'origin', UPSTREAM_COMMIT]);
run('git', ['-C', checkout, 'checkout', '--detach', 'FETCH_HEAD']);
const observed = run('git', ['-C', checkout, 'rev-parse', 'HEAD'], { capture: true });
if (observed !== UPSTREAM_COMMIT) {
  fail(`upstream identity mismatch: expected ${UPSTREAM_COMMIT}, observed ${observed}`);
}

const entrypoint = path.join(checkout, 'dist', 'index.js');
const actionContract = path.join(checkout, 'action.yml');
if (!fs.statSync(entrypoint).isFile() || !fs.statSync(actionContract).isFile()) {
  fail('verified upstream commit lacks the expected action entrypoint or contract');
}

const environment = {
  ...process.env,
  GITHUB_ACTION_PATH: checkout,
};
const result = spawnSync(process.execPath, [entrypoint], {
  env: environment,
  stdio: 'inherit',
});
if (result.error) {
  fail(`upstream action failed to start: ${result.error.message}`);
}
if (result.status !== 0) {
  fail(`upstream action exited with status ${result.status}`);
}
