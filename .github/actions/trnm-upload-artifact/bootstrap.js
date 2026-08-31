'use strict'

const crypto = require('crypto')
const fs = require('fs')
const https = require('https')
const path = require('path')

const PINNED_BUNDLE_URL =
  'https://raw.githubusercontent.com/actions/upload-artifact/ea165f8d65b6e75b540449e92b4886f43607fa02/dist/upload/index.js'
const PINNED_GIT_BLOB_SHA1 = '89238fa3eb49937ea82c5d82006ee1fc6c6abaae'
const MAX_REDIRECTS = 5

function download(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, {headers: {'user-agent': 'trnm-pinned-artifact-bootstrap'}}, response => {
      const status = response.statusCode || 0
      if (status >= 300 && status < 400 && response.headers.location) {
        response.resume()
        if (redirects >= MAX_REDIRECTS) {
          reject(new Error('pinned artifact bundle exceeded redirect limit'))
          return
        }
        resolve(download(new URL(response.headers.location, url).toString(), redirects + 1))
        return
      }
      if (status !== 200) {
        response.resume()
        reject(new Error(`pinned artifact bundle returned HTTP ${status}`))
        return
      }
      const chunks = []
      response.on('data', chunk => chunks.push(chunk))
      response.on('end', () => resolve(Buffer.concat(chunks)))
      response.on('error', reject)
    })
    request.setTimeout(30_000, () => request.destroy(new Error('pinned artifact bundle download timed out')))
    request.on('error', reject)
  })
}

function gitBlobSha1(content) {
  const hash = crypto.createHash('sha1')
  hash.update(Buffer.from(`blob ${content.length}\0`, 'utf8'))
  hash.update(content)
  return hash.digest('hex')
}

async function main() {
  if (!process.env.ACTIONS_RUNTIME_TOKEN) {
    throw new Error('runner did not provide ACTIONS_RUNTIME_TOKEN to the local JavaScript action')
  }
  const content = await download(PINNED_BUNDLE_URL)
  const actual = gitBlobSha1(content)
  if (actual !== PINNED_GIT_BLOB_SHA1) {
    throw new Error(`pinned artifact bundle identity mismatch: ${actual}`)
  }

  const runnerTemp = process.env.RUNNER_TEMP
  if (!runnerTemp) {
    throw new Error('RUNNER_TEMP is unavailable')
  }
  const bundlePath = path.join(runnerTemp, 'trnm-upload-artifact-v4.6.2.js')
  fs.writeFileSync(bundlePath, content, {mode: 0o600})
  console.log(`Pinned GitHub upload-artifact bundle verified: ${actual}`)
  require(bundlePath)
}

main().catch(error => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(`::error::${message.replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A')}`)
  process.exitCode = 1
})
