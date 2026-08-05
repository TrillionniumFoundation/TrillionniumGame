import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as ed25519Sign,
  verify as ed25519Verify,
} from "node:crypto";
import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { Client } from "@heroiclabs/nakama-js";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
function assert(condition, message) { if (!condition) throw new Error(message); }
function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function equal(left, right) { return JSON.stringify(left) === JSON.stringify(right); }
function sha256(bytes) { return createHash("sha256").update(bytes).digest(); }
function digest(bytes) { return `sha256:${sha256(bytes).toString("hex")}`; }
function digestBytes(value) {
  assert(/^sha256:[0-9a-f]{64}$/.test(value), `invalid digest ${value}`);
  return Buffer.from(value.slice(7), "hex");
}
function canonicalBase64(value, label) {
  assert(typeof value === "string", `${label} is not base64 text`);
  const bytes = Buffer.from(value, "base64");
  assert(bytes.toString("base64") === value, `${label} is not canonical padded base64`);
  return bytes;
}

class Frame {
  constructor(domain) { this.parts = [Buffer.from(domain, "utf8"), Buffer.from([0])]; }
  bytes(value) {
    const bytes = Buffer.from(value);
    assert(bytes.length <= 0xffffffff, "canonical field is too large");
    const size = Buffer.alloc(4); size.writeUInt32BE(bytes.length);
    this.parts.push(size, bytes); return this;
  }
  string(value) { return this.bytes(Buffer.from(value, "utf8")); }
  u32(value) { const bytes = Buffer.alloc(4); bytes.writeUInt32BE(value); this.parts.push(bytes); return this; }
  u64(value) { const bytes = Buffer.alloc(8); bytes.writeBigUInt64BE(BigInt(value)); this.parts.push(bytes); return this; }
  i64(value) { const bytes = Buffer.alloc(8); bytes.writeBigInt64BE(BigInt(value)); this.parts.push(bytes); return this; }
  digest(value) { this.parts.push(digestBytes(value)); return this; }
  finish() { return Buffer.concat(this.parts); }
}

function privateKeyFromSeed(seedBase64) {
  const seed = canonicalBase64(seedBase64, "Ed25519 seed");
  assert(seed.length === 32, "Ed25519 seed length differs");
  return createPrivateKey({
    key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), seed]),
    format: "der", type: "pkcs8",
  });
}
function rawPublic(privateKey) {
  return createPublicKey(privateKey).export({ type: "spki", format: "der" }).subarray(-32);
}
function publicKeyFromRaw(rawBase64) {
  const raw = canonicalBase64(rawBase64, "Ed25519 public key");
  assert(raw.length === 32, "Ed25519 public key length differs");
  return createPublicKey({
    key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), raw]),
    format: "der", type: "spki",
  });
}

function authorizationClaimFrame(claim) {
  return new Frame("trnm_research_session_authorization_claim_v1")
    .string(claim.schema).string(claim.authorization_id).string(claim.session_id)
    .string(claim.team_id).string(claim.paper_project_id).string(claim.challenge_id)
    .string(claim.agent_id).string(claim.agent_did).string(claim.agent_key_id)
    .bytes(canonicalBase64(claim.agent_public_key, "Agent public key"))
    .string(claim.subject_user_id).u32(claim.participant_slot).string(claim.role)
    .u64(claim.roster_version).digest(claim.roster_root).digest(claim.ruleset_hash)
    .digest(claim.challenge_snapshot_hash).i64(claim.issued_at_unix).i64(claim.expires_at_unix)
    .finish();
}
function signAuthorization(claim, issuerKeyID, issuerPrivate) {
  const message = new Frame("trnm_research_session_authorization_signature_v1")
    .string(issuerKeyID).bytes(authorizationClaimFrame(claim)).finish();
  return { claim, issuer_key_id: issuerKeyID, signature: ed25519Sign(null, message, issuerPrivate).toString("base64") };
}
function rosterFrame(sessionID, teamID, paperProjectID, version, entries) {
  const frame = new Frame("trnm_research_session_roster_v1").string(sessionID).string(teamID)
    .string(paperProjectID).u64(version).u32(entries.length);
  for (const entry of entries) {
    frame.u32(entry.participant_slot).string(entry.authorization_id).string(entry.subject_user_id)
      .string(entry.agent_id).string(entry.agent_did).string(entry.agent_key_id)
      .digest(entry.agent_key_hash).string(entry.role);
  }
  return frame.finish();
}
function rosterRoot(sessionID, teamID, paperProjectID, version, entries) {
  const ordered = [...entries].sort((left, right) => left.participant_slot - right.participant_slot);
  assert(ordered.length >= 3 && ordered.length <= 5, "roster count differs");
  ordered.forEach((entry, index) => assert(entry.participant_slot === index + 1, "roster slots are not gapless"));
  return digest(rosterFrame(sessionID, teamID, paperProjectID, version, ordered));
}

function actionSigning(action) {
  return new Frame("trnm_research_session_action_signature_v1")
    .string(action.schema).string(action.action_id).string(action.authorization_id)
    .string(action.session_id).string(action.team_id).string(action.paper_project_id)
    .string(action.challenge_id).u64(action.roster_version).u32(action.participant_slot)
    .u64(action.participant_sequence).u64(action.expected_session_version).i64(action.issued_at_unix)
    .string(action.action_type).string(action.payload_type)
    .bytes(canonicalBase64(action.payload, "action payload")).digest(action.payload_hash)
    .digest(action.reference_hash).string(action.agent_key_id).finish();
}
function signAction(unsigned, key) {
  const action = { ...unsigned, payload_hash: digest(Buffer.from(unsigned.payload, "base64")) };
  action.signature = ed25519Sign(null, actionSigning(action), key).toString("base64");
  return action;
}

function eventID(event) {
  return digest(new Frame("trnm_research_session_event_id_v1").string(event.session_id)
    .string(event.causation_id).u64(event.sequence).finish());
}
function eventFacts(event) {
  return new Frame("trnm_research_session_event_v1")
    .string(event.schema).string(event.event_id).string(event.event_type).string(event.session_id)
    .string(event.team_id).string(event.paper_project_id).string(event.challenge_id)
    .u64(event.roster_version).u64(event.sequence).string(event.causation_id).i64(event.occurred_at_unix)
    .u32(event.participant_slot).u64(event.session_version).string(event.action_type)
    .string(event.payload_type).bytes(canonicalBase64(event.payload, "event payload"))
    .digest(event.payload_hash).digest(event.reference_hash).finish();
}
function eventRoot(events) {
  assert(events.length > 0, "event archive is empty");
  let level = events.map((event, index) => {
    assert(event.sequence === index + 1, "event sequence is not gapless");
    assert(event.event_id === eventID(event), `event ${event.sequence} id differs`);
    assert(event.payload_hash === digest(canonicalBase64(event.payload, `event ${event.sequence} payload`)), `event ${event.sequence} payload hash differs`);
    assert(event.event_hash === digest(eventFacts(event)), `event ${event.sequence} hash differs`);
    const sequence = Buffer.alloc(8); sequence.writeBigUInt64BE(BigInt(event.sequence));
    return sha256(Buffer.concat([Buffer.from("trnm_research_session_event_leaf_v1\0"), sequence, digestBytes(event.event_hash)]));
  });
  while (level.length > 1) {
    const next = [];
    for (let index = 0; index < level.length; index += 2) {
      next.push(sha256(Buffer.concat([Buffer.from("trnm_research_session_merkle_node_v1\0"), level[index], level[index + 1] ?? level[index]])));
    }
    level = next;
  }
  return `sha256:${level[0].toString("hex")}`;
}
function archiveFrame(events) {
  const frame = new Frame("trnm_research_session_event_archive_v1").u64(events.length);
  for (const event of events) frame.bytes(eventFacts(event)).bytes(digestBytes(event.event_hash));
  return frame.finish();
}
function terminalFrame(facts) {
  return new Frame("trnm_research_session_terminal_facts_v1").string(facts.result_code)
    .digest(facts.paper_bundle_hash).digest(facts.paper_release_candidate_hash)
    .digest(facts.contribution_ledger_hash).finish();
}
function completionSigning(completion) {
  return new Frame("trnm_research_session_completed_signature_v1").string(completion.schema)
    .digest(completion.commitment_id).string(completion.session_id).string(completion.team_id)
    .string(completion.paper_project_id).string(completion.challenge_id).u64(completion.roster_version)
    .digest(completion.roster_root).bytes(terminalFrame(completion.terminal_facts)).u64(completion.event_count)
    .digest(completion.event_root).digest(completion.archive_hash).digest(completion.ruleset_hash)
    .digest(completion.challenge_snapshot_hash).i64(completion.completed_at_unix)
    .string(completion.authority_key_id).finish();
}
function commitmentID(completion) {
  return digest(new Frame("trnm_research_session_commitment_id_v1").string(completion.session_id)
    .digest(completion.event_root).digest(completion.archive_hash).finish());
}

function verifyEvidence(evidence, archive, expectedCount) {
  const completion = evidence.completion;
  assert(completion.session_id === archive.logical_session_id, "completion session differs");
  assert(completion.event_count === archive.events.length && completion.event_count === archive.event_count, "completion event count differs");
  assert(completion.event_root === eventRoot(archive.events), "independent event_root differs");
  assert(completion.archive_hash === digest(archiveFrame(archive.events)), "independent archive_hash differs");
  assert(completion.roster_root === rosterRoot(completion.session_id, completion.team_id,
    completion.paper_project_id, completion.roster_version, archive.roster), "independent roster_root differs");
  assert(completion.commitment_id === commitmentID(completion), "independent commitment_id differs");
  assert(archive.roster.length === expectedCount && archive.participants.length === expectedCount, "final roster cardinality differs");
  assert(archive.events.at(-1).event_type === "research_session_completed", "completion is not terminal event");
  assert(canonicalBase64(archive.events.at(-1).payload, "terminal payload").equals(terminalFrame(completion.terminal_facts)), "terminal event facts differ");
  const authorityRaw = required("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY");
  assert(evidence.authority_public_key_base64 === authorityRaw, "evidence authority key differs from pinned key");
  assert(ed25519Verify(null, completionSigning(completion), publicKeyFromRaw(authorityRaw),
    canonicalBase64(completion.signature, "completion signature")), "completion signature rejected");

  const tampered = structuredClone(archive.events);
  tampered[0].payload = Buffer.from("tampered", "utf8").toString("base64");
  let rejected = false;
  try { eventRoot(tampered); } catch { rejected = true; }
  assert(rejected, "tampered archive was accepted");
  rejected = false;
  try { eventRoot(archive.events.slice(1)); } catch { rejected = true; }
  assert(rejected, "missing/reordered archive was accepted");
  return completion;
}

async function rpcHttpKey(client, httpKey, id, payload) {
  void client;
  const url = `http://${process.env.NAKAMA_HOST || "127.0.0.1"}:${process.env.NAKAMA_PORT || "7350"}/v2/rpc/${encodeURIComponent(id)}?http_key=${encodeURIComponent(httpKey)}`;
  const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(JSON.stringify(payload)), signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`RPC ${id} HTTP ${response.status}: ${await response.text()}`);
  const envelope = await response.json();
  return envelope.payload ? JSON.parse(envelope.payload) : undefined;
}
function inbox(socket) {
  const messages = [];
  socket.onmatchdata = (message) => {
    let payload;
    try { payload = JSON.parse(decoder.decode(message.data)); } catch (error) { payload = { error: String(error) }; }
    messages.push({ opcode: Number(message.op_code), payload });
  };
  return {
    async take(predicate, label, timeoutMs = 12_000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const index = messages.findIndex(predicate);
        if (index >= 0) return messages.splice(index, 1)[0];
        await delay(20);
      }
      throw new Error(`timed out waiting for ${label}: ${JSON.stringify(messages)}`);
    },
  };
}
async function player(client, customID) {
  const session = await client.authenticateCustom(customID, true);
  const socket = client.createSocket(false, false);
  const messages = inbox(socket);
  await socket.connect(session, true);
  return { session, socket, inbox: messages, customID };
}
async function closePlayers(players) {
  for (const value of players) { try { value.socket.disconnect(false); } catch {} }
  await delay(250);
}

const teamID = required("TRNM_RESEARCH_TEAM_ID");
const paperProjectID = required("TRNM_RESEARCH_PAPER_PROJECT_ID");
const challengeID = required("TRNM_RESEARCH_CHALLENGE_ID");
const rulesetHash = digest(Buffer.from("paper-raid-blackbox-ruleset-v1"));
const challengeHash = digest(Buffer.from("paper-raid-blackbox-challenge-v1"));

function authID(count, version, slot) {
  return `10000000-0000-4000-8000-${String(count * 10000 + version * 100 + slot).padStart(12, "0")}`;
}
function sessionID(count) { return `${required("TRNM_RESEARCH_SESSION_PREFIX")}-${count}`; }
function agentKeys() {
  return [1, 2, 3, 4, 5].map((slot) => privateKeyFromSeed(required(`TRNM_AGENT_${slot}_PRIVATE_SEED`)));
}
function createAuthorizations(count, version, users, keys, rotatedSlot = 0) {
  const now = Math.floor(Date.now() / 1000);
  const issuerID = required("TRNM_HEPTA_ISSUER_KEY_ID");
  const issuer = privateKeyFromSeed(required("TRNM_HEPTA_ISSUER_PRIVATE_SEED"));
  const claims = [];
  for (let index = 0; index < count; index += 1) {
    const slot = index + 1;
    const key = slot === rotatedSlot ? privateKeyFromSeed(required("TRNM_AGENT_ROTATION_PRIVATE_SEED")) : keys[index];
    claims.push({ schema: "trnm.research-session.authorization.v1", authorization_id: authID(count, version, slot),
      session_id: sessionID(count), team_id: teamID, paper_project_id: paperProjectID, challenge_id: challengeID,
      agent_id: `paper-agent-${slot}`, agent_did: `did:trnm:paper-agent-${slot}`,
      agent_key_id: slot === rotatedSlot ? `paper-agent-key-${slot}-v${version}` : `paper-agent-key-${slot}`,
      agent_public_key: rawPublic(key).toString("base64"), subject_user_id: users[index], participant_slot: slot,
      role: `paper-role-${slot}`, roster_version: version, roster_root: digest(Buffer.from("placeholder")),
      ruleset_hash: rulesetHash, challenge_snapshot_hash: challengeHash,
      issued_at_unix: now - 30, expires_at_unix: now + 3600 });
  }
  const entries = claims.map((claim) => ({ participant_slot: claim.participant_slot,
    authorization_id: claim.authorization_id, subject_user_id: claim.subject_user_id, agent_id: claim.agent_id,
    agent_did: claim.agent_did, agent_key_id: claim.agent_key_id,
    agent_key_hash: digest(Buffer.from(claim.agent_public_key, "base64")), role: claim.role }));
  const root = rosterRoot(sessionID(count), teamID, paperProjectID, version, entries);
  for (const claim of claims) claim.roster_root = root;
  return { claims, authorizations: claims.map((claim) => signAuthorization(claim, issuerID, issuer)), root };
}

async function archive(client, httpKey, count, afterSequence = 0) {
  return rpcHttpKey(client, httpKey, "trnm_research_session_archive_v1", {
    schema: "trnm.nakama.research-session.get-archive.v1", logical_session_id: sessionID(count),
    after_sequence: afterSequence, limit: 128, operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
  });
}
async function joinAll(players, externalMatchID, authorizations) {
  for (let index = 0; index < players.length; index += 1) {
    await players[index].socket.joinMatch(externalMatchID, undefined, { authorization_id: authorizations[index].claim.authorization_id });
    await players[index].inbox.take((message) => message.opcode === 12 && message.payload?.event_type === "participant_joined", `slot ${index + 1} join`);
  }
}
async function sendAction(playerValue, externalMatchID, action, label) {
  await playerValue.socket.sendMatchState(externalMatchID, 11, encoder.encode(JSON.stringify(action)));
  return (await playerValue.inbox.take((message) => message.opcode === 12 && message.payload?.causation_id === action.action_id, label)).payload;
}
async function actionRound(client, httpKey, count, externalMatchID, players, claims, keys, rotatedSlot = 0) {
  let current = await archive(client, httpKey, count, 0);
  let version = current.session_version;
  const sequences = current.participants.map((participant) => participant.last_action_sequence);
  const actionKeys = keys.slice(0, count);
  if (rotatedSlot > 0) actionKeys[rotatedSlot - 1] = privateKeyFromSeed(required("TRNM_AGENT_ROTATION_PRIVATE_SEED"));
  const send = async (slot, actionType, payloadType, reference, suffix) => {
    const index = slot - 1;
    const action = signAction({ schema: "trnm.research-session.action.v1",
      action_id: `${sessionID(count)}-v${claims[index].roster_version}-${suffix}-${slot}`,
      authorization_id: claims[index].authorization_id, session_id: sessionID(count), team_id: teamID,
      paper_project_id: paperProjectID, challenge_id: challengeID, roster_version: claims[index].roster_version,
      participant_slot: slot, participant_sequence: sequences[index] + 1, expected_session_version: version,
      issued_at_unix: Math.floor(Date.now() / 1000), action_type: actionType, payload_type: payloadType,
      payload: Buffer.from(JSON.stringify({ slot, suffix }), "utf8").toString("base64"),
      reference_hash: reference, agent_key_id: claims[index].agent_key_id }, actionKeys[index]);
    const event = await sendAction(players[index], externalMatchID, action, suffix);
    sequences[index] += 1; version = event.session_version;
    return event;
  };
  for (let slot = 1; slot <= count; slot += 1) {
    await send(slot, "participant.ready", "trnm.research-session.ready.v1", claims[0].roster_root, "ready");
  }
  await send(1, "agent.proposal.submitted", "trnm.paper-raid.agent-proposal.v1",
    digest(Buffer.from(`${sessionID(count)}-proposal-v${claims[0].roster_version}`)), "proposal");
  const release = digest(Buffer.from(`${sessionID(count)}-release-v${claims[0].roster_version}`));
  for (let slot = 1; slot <= count; slot += 1) {
    await send(slot, "paper.release.acknowledged", "trnm.paper-raid.release-acknowledgement.v1", release, "release-ack");
  }
  return release;
}
function facts(count, release) {
  return { result_code: "paper_bundle_ready", paper_bundle_hash: digest(Buffer.from(`${sessionID(count)}-bundle`)),
    paper_release_candidate_hash: release, contribution_ledger_hash: digest(Buffer.from(`${sessionID(count)}-ledger`)) };
}
async function complete(client, httpKey, count, release) {
  return rpcHttpKey(client, httpKey, "trnm_research_session_complete_v1", {
    schema: "trnm.nakama.research-session.complete.v1", operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
    logical_session_id: sessionID(count), facts: facts(count, release),
  });
}

function writeState(value) {
  const path = required("TRNM_BLACKBOX_STATE_FILE");
  writeFileSync(path, JSON.stringify(value, null, 2) + "\n", { mode: 0o600 }); chmodSync(path, 0o600);
}
function readState() { return JSON.parse(readFileSync(required("TRNM_BLACKBOX_STATE_FILE"), "utf8")); }
function setHeptaMode(mode) { writeFileSync(required("TRNM_HEPTA_MOCK_CONTROL_FILE"), mode + "\n", { mode: 0o666 }); }

async function runPrepare3(client, httpKey) {
  const customIDs = [1, 2, 3].map((slot) => `${required("TRNM_CUSTOM_ID_PREFIX")}-${slot}`);
  const players = await Promise.all(customIDs.map((id) => player(client, id)));
  try {
    const keys = agentKeys();
    const epoch = createAuthorizations(3, 1, players.map((value) => value.session.user_id), keys);
    const runtime = await rpcHttpKey(client, httpKey, "trnm_research_session_create_v1", {
      schema: "trnm.nakama.research-session.create.v1", operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
      authorizations: epoch.authorizations,
    });
    assert(runtime.runtime_generation === 1 && runtime.roster_root === epoch.root, "three-member create differs");
    await joinAll(players, runtime.external_match_id, epoch.authorizations);
    await actionRound(client, httpKey, 3, runtime.external_match_id, players, epoch.claims, keys);

    const beforeDisconnect = await archive(client, httpKey, 3, 0);
    players[2].socket.disconnect(false); await delay(500);
    const replacement = await player(client, customIDs[2]); players[2] = replacement;
    await replacement.socket.joinMatch(runtime.external_match_id, undefined, { authorization_id: epoch.claims[2].authorization_id });
    await replacement.inbox.take((message) => message.opcode === 12 && message.payload?.event_type === "participant_reconnected", "slot 3 reconnect");
    const caught = await archive(client, httpKey, 3, beforeDisconnect.event_count);
    assert(caught.events.some((event) => event.event_type === "participant_disconnected"), "cursor catch-up missed disconnect");
    assert(caught.events.some((event) => event.event_type === "participant_reconnected"), "cursor catch-up missed reconnect");
    writeState({ schema: "trnm.paper-raid.blackbox-state.v1", custom_ids: customIDs,
      subject_user_ids: players.map((value) => value.session.user_id), old_external_match_id: runtime.external_match_id,
      epoch_one_authorization_ids: epoch.claims.map((claim) => claim.authorization_id), cursor_after: beforeDisconnect.event_count });
    process.stdout.write(JSON.stringify({ phase: "prepare3", session_id: sessionID(3), hepta_down_local_progress: true,
      disconnect_reconnect_cursor: true }) + "\n");
  } finally { await closePlayers(players); }
}

async function runRotateComplete3(client, httpKey) {
  const state = readState();
  const players = await Promise.all(state.custom_ids.map((id) => player(client, id)));
  try {
    assert(equal(players.map((value) => value.session.user_id), state.subject_user_ids), "Nakama user identities changed after SIGKILL");
    const resumed = await rpcHttpKey(client, httpKey, "trnm_research_session_resume_v1", {
      schema: "trnm.nakama.research-session.resume.v1", operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
      logical_session_id: sessionID(3),
    });
    assert(resumed.runtime_generation === 2 && resumed.external_match_id !== state.old_external_match_id, "SIGKILL resume did not fence runtime");
    const keys = agentKeys();
    const epoch = createAuthorizations(3, 2, state.subject_user_ids, keys, 2);
    const rotated = await rpcHttpKey(client, httpKey, "trnm_research_session_replace_roster_v1", {
      schema: "trnm.nakama.research-session.replace-roster.v1", operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
      logical_session_id: sessionID(3), authorizations: epoch.authorizations,
    });
    assert(rotated.roster_version === 2 && rotated.roster_root === epoch.root, "key rotation did not install epoch two");
    let oldRejected = false;
    try { await players[0].socket.joinMatch(rotated.external_match_id, undefined, { authorization_id: state.epoch_one_authorization_ids[0] }); }
    catch { oldRejected = true; }
    assert(oldRejected, "old epoch authorization joined after rotation");
    await joinAll(players, rotated.external_match_id, epoch.authorizations);
    const release = await actionRound(client, httpKey, 3, rotated.external_match_id, players, epoch.claims, keys, 2);
    setHeptaMode("down");
    const evidence = await complete(client, httpKey, 3, release);
    assert(evidence.completion.roster_version === 2 && evidence.completion.roster_root === epoch.root, "completion did not bind epoch two");
    state.epoch_two_authorization_ids = epoch.claims.map((claim) => claim.authorization_id);
    state.epoch_two_external_match_id = rotated.external_match_id;
    state.completion_commitment_id = evidence.completion.commitment_id;
    writeState(state);
    process.stdout.write(JSON.stringify({ phase: "rotate-complete3", runtime_generation: 2,
      old_epoch_rejected: true, epoch_two_completion_local_before_callback: true }) + "\n");
  } finally { await closePlayers(players); }
}

function callbackLogs() {
  const path = required("TRNM_HEPTA_MOCK_LOG_FILE");
  const raw = readFileSync(path, "utf8").trim();
  return raw ? raw.split("\n").map((line) => JSON.parse(line)) : [];
}
function logBody(entry) { return JSON.parse(Buffer.from(entry.body_base64, "base64").toString("utf8")); }
async function waitFor(predicate, label, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await predicate()) return; await delay(200); }
  throw new Error(`timed out waiting for ${label}`);
}

async function runRecover3(client, httpKey) {
  const state = readState();
  const customPlayer = await player(client, state.custom_ids[0]);
  try {
    const evidence = await rpcHttpKey(client, httpKey, "trnm_research_session_evidence_v1", {
      schema: "trnm.nakama.research-session.get-evidence.v1", logical_session_id: sessionID(3),
      operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
    });
    assert(evidence.completion.commitment_id === state.completion_commitment_id, "post-SIGKILL evidence changed");
    await waitFor(() => {
      const entries = callbackLogs().filter((entry) => entry.path.endsWith("research-session-completions") &&
        logBody(entry).completion.session_id === sessionID(3));
      return entries.some((entry) => entry.response === "tampered_completion") && entries.some((entry) => entry.response === "valid_completion");
    }, "tampered receipt rejection followed by valid completion ACK");
    const entries = callbackLogs().filter((entry) => entry.path.endsWith("research-session-completions") &&
      logBody(entry).completion.session_id === sessionID(3));
    assert(entries.some((entry) => entry.response === "down"), "completion was not attempted while Hepta was down");
    assert(new Set(entries.map((entry) => entry.body_base64)).size === 1, "completion retry body bytes changed");
    assert(new Set(entries.map((entry) => entry.idempotency_key)).size === 1, "completion retry idempotency changed");
    const consumptions = callbackLogs().filter((entry) => entry.path.endsWith("research-session-authorizations/consumed") &&
      logBody(entry).session_id === sessionID(3));
    const epochOne = consumptions.filter((entry) => logBody(entry).roster_version === 1);
    assert(epochOne.some((entry) => entry.response === "down") && epochOne.some((entry) => entry.response === "valid_consumption"), "epoch-one consumption did not survive outage/restart");
    assert(new Set(epochOne.map((entry) => entry.body_base64)).size === 1, "consumption retry body bytes changed");

    const finalArchive = await archive(client, httpKey, 3, 0);
    const completion = verifyEvidence(evidence, finalArchive, 3);
    assert(completion.roster_version === 2, "final three-member completion lost rotation epoch");
    await waitFor(async () => {
      const listed = await client.listMatches(customPlayer.session, 100, true);
      return !listed.matches?.some((match) => match.match_id === evidence.external_match_id);
    }, "delivery-only runtime termination");
    process.stdout.write(JSON.stringify({ phase: "recover3", signed_ack_after_sigkill: true,
      tampered_receipt_rejected: true, exact_body_retry: true, roots_recomputed: true,
      commitment_id: completion.commitment_id }) + "\n");
  } finally { await closePlayers([customPlayer]); }
}

async function runCardinality(client, httpKey) {
  const keys = agentKeys();
  const results = [];
  for (const count of [4, 5]) {
    const customIDs = Array.from({ length: count }, (_, index) => `${required("TRNM_CUSTOM_ID_PREFIX")}-${index + 1}`);
    const players = await Promise.all(customIDs.map((id) => player(client, id)));
    try {
      const epoch = createAuthorizations(count, 1, players.map((value) => value.session.user_id), keys);
      const runtime = await rpcHttpKey(client, httpKey, "trnm_research_session_create_v1", {
        schema: "trnm.nakama.research-session.create.v1", operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
        authorizations: epoch.authorizations,
      });
      await joinAll(players, runtime.external_match_id, epoch.authorizations);
      const release = await actionRound(client, httpKey, count, runtime.external_match_id, players, epoch.claims, keys);
      const evidence = await complete(client, httpKey, count, release);
      const finalArchive = await archive(client, httpKey, count, 0);
      const completion = verifyEvidence(evidence, finalArchive, count);
      results.push({ participants: count, event_count: completion.event_count, commitment_id: completion.commitment_id });
    } finally { await closePlayers(players); }
  }
  process.stdout.write(JSON.stringify({ phase: "cardinality", results }) + "\n");
}

async function runHealth(client, httpKey) {
  const health = await rpcHttpKey(client, httpKey, "trnm_health_v1", {});
  const ready = await rpcHttpKey(client, httpKey, "trnm_ready_v1", {});
  assert(health.healthy === true && ready.ready === true, "Nakama plugin is not ready");
  process.stdout.write(JSON.stringify({ phase: "health", ready: true }) + "\n");
}

const client = new Client(required("NAKAMA_SERVER_KEY"), process.env.NAKAMA_HOST || "127.0.0.1", process.env.NAKAMA_PORT || "7350", false);
const httpKey = required("NAKAMA_RUNTIME_HTTP_KEY");
switch (required("BLACKBOX_PHASE")) {
  case "health": await runHealth(client, httpKey); break;
  case "prepare3": await runPrepare3(client, httpKey); break;
  case "rotate-complete3": await runRotateComplete3(client, httpKey); break;
  case "recover3": await runRecover3(client, httpKey); break;
  case "cardinality": await runCardinality(client, httpKey); break;
  default: throw new Error(`unsupported BLACKBOX_PHASE ${process.env.BLACKBOX_PHASE}`);
}
