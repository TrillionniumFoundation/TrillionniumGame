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
function authorizationEnvelopeFrame(authorization) {
  const signing = new Frame("trnm_research_session_authorization_signature_v1")
    .string(authorization.issuer_key_id).bytes(authorizationClaimFrame(authorization.claim)).finish();
  return new Frame("trnm_research_control_authorization_envelope_v2")
    .bytes(signing).bytes(canonicalBase64(authorization.signature, "authorization signature")).finish();
}
function authorizationSetControlFrame(domain, request) {
  const session = request.logical_session_id ?? request.authorizations[0].claim.session_id;
  const rosterVersion = request.authorizations[0].claim.roster_version;
  const frame = new Frame(domain).string(request.schema).string(session)
    .string(request.authorization_set_id).u32(request.authorizations.length);
  request.authorizations.forEach((authorization, index) => {
    assert(authorization.claim.participant_slot === index + 1, "control authorization slots differ");
    assert(authorization.claim.session_id === session, "control authorization sessions differ");
    assert(authorization.claim.roster_version === rosterVersion, "control authorization epochs differ");
    frame.bytes(authorizationEnvelopeFrame(authorization));
  });
  return frame.finish();
}
function researchControlBusinessFrame(operation, request) {
  switch (operation) {
    case "create":
      return authorizationSetControlFrame("trnm_research_control_create_business_v2", request);
    case "resume":
      return new Frame("trnm_research_control_resume_business_v2").string(request.schema)
        .string(request.logical_session_id).string(request.authorization_set_id).finish();
    case "replace_roster":
      return authorizationSetControlFrame("trnm_research_control_replace_business_v2", request);
    case "complete":
      return new Frame("trnm_research_control_complete_business_v2").string(request.schema)
        .string(request.logical_session_id).string(request.authorization_set_id)
        .bytes(terminalFrame(request.facts)).finish();
    default:
      throw new Error(`unsupported research control operation ${operation}`);
  }
}
const controlTargets = new Map([
  ["create", "trnm_research_session_create_v2"],
  ["resume", "trnm_research_session_resume_v2"],
  ["replace_roster", "trnm_research_session_replace_roster_v2"],
  ["complete", "trnm_research_session_complete_v2"],
]);
function signResearchControl(operation, commandID, session, rosterVersion, authorizationSetID, business) {
  const now = Math.floor(Date.now() / 1000);
  const claim = { schema: "trnm.nakama.research-control.claim.v2", command_id: commandID,
    operation, target_rpc: controlTargets.get(operation), session_id: session,
    session_roster_version: rosterVersion, authorization_set_id: authorizationSetID,
    payload_hash: digest(business), audience: "trnm:nakama:research-control:v2",
    issued_at_unix: now, expires_at_unix: now + 120,
    issuer_key_id: required("TRNM_HEPTA_CONTROL_ISSUER_KEY_ID") };
  const claimFrame = new Frame("trnm_research_control_claim_v2")
    .string(claim.schema).string(claim.command_id).string(claim.operation).string(claim.target_rpc)
    .string(claim.session_id).u64(claim.session_roster_version).string(claim.authorization_set_id)
    .digest(claim.payload_hash).string(claim.audience).i64(claim.issued_at_unix)
    .i64(claim.expires_at_unix).string(claim.issuer_key_id).finish();
  const signing = new Frame("trnm_research_control_signature_v2").bytes(claimFrame).finish();
  const key = privateKeyFromSeed(required("TRNM_HEPTA_CONTROL_PRIVATE_SEED"));
  return { claim, signature: ed25519Sign(null, signing, key).toString("base64") };
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

async function rpcHttpKeyRaw(client, httpKey, id, payload) {
  void client;
  const url = `http://${process.env.NAKAMA_HOST || "127.0.0.1"}:${process.env.NAKAMA_PORT || "7350"}/v2/rpc/${encodeURIComponent(id)}?http_key=${encodeURIComponent(httpKey)}`;
  const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(JSON.stringify(payload)), signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`RPC ${id} HTTP ${response.status}: ${await response.text()}`);
  const envelope = await response.json();
  return { raw: envelope.payload, payload: envelope.payload ? JSON.parse(envelope.payload) : undefined };
}
async function rpcHttpKey(client, httpKey, id, payload) {
  return (await rpcHttpKeyRaw(client, httpKey, id, payload)).payload;
}
async function rpcHttpKeyRejected(httpKey, id, payload) {
  const url = `http://${process.env.NAKAMA_HOST || "127.0.0.1"}:${process.env.NAKAMA_PORT || "7350"}/v2/rpc/${encodeURIComponent(id)}?http_key=${encodeURIComponent(httpKey)}`;
  const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(JSON.stringify(payload)), signal: AbortSignal.timeout(20_000) });
  const body = await response.text();
  assert(!response.ok, `RPC ${id} unexpectedly accepted a negative request`);
  return { status: response.status, body };
}
async function playerRPC(playerValue, id, payload) {
  const raw = await playerValue.socket.rpc(id, JSON.stringify(payload));
  assert(typeof raw?.payload === "string", `player RPC ${id} returned no payload`);
  return { raw: raw.payload, payload: JSON.parse(raw.payload) };
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
function commandID(count, ordinal) {
  return `90000000-0000-4000-8000-${String(count * 100 + ordinal).padStart(12, "0")}`;
}
function authorizationSetID(count, version) {
  return `20000000-0000-4000-8000-${String(count * 100 + version).padStart(12, "0")}`;
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

function signedCreateRequest(count, epoch, ordinal = 1) {
  const request = { schema: "trnm.nakama.research-session.create.v2",
    authorization_set_id: authorizationSetID(count, 1), authorizations: epoch.authorizations };
  request.control = signResearchControl("create", commandID(count, ordinal), sessionID(count), 1,
    request.authorization_set_id, researchControlBusinessFrame("create", request));
  return request;
}
function signedResumeRequest(count, version, setID, ordinal) {
  const request = { schema: "trnm.nakama.research-session.resume.v2",
    logical_session_id: sessionID(count), authorization_set_id: setID };
  request.control = signResearchControl("resume", commandID(count, ordinal), sessionID(count), version,
    setID, researchControlBusinessFrame("resume", request));
  return request;
}
function signedReplaceRequest(count, epoch, ordinal = 3) {
  const request = { schema: "trnm.nakama.research-session.replace-roster.v2",
    logical_session_id: sessionID(count), authorization_set_id: authorizationSetID(count, 2),
    authorizations: epoch.authorizations };
  request.control = signResearchControl("replace_roster", commandID(count, ordinal), sessionID(count), 2,
    request.authorization_set_id, researchControlBusinessFrame("replace_roster", request));
  return request;
}
function signedCompleteRequest(count, version, setID, release, ordinal = 4) {
  const request = { schema: "trnm.nakama.research-session.complete.v2",
    logical_session_id: sessionID(count), authorization_set_id: setID, facts: facts(count, release) };
  request.control = signResearchControl("complete", commandID(count, ordinal), sessionID(count), version,
    setID, researchControlBusinessFrame("complete", request));
  return request;
}

async function participantArchive(playerValue, count, authorizationID, afterSequence = 0) {
  return (await playerRPC(playerValue, "trnm_research_session_archive_v1", {
    schema: "trnm.nakama.research-session.get-archive.v1", logical_session_id: sessionID(count),
    after_sequence: afterSequence, limit: 128, authorization_id: authorizationID,
  })).payload;
}
async function participantEvidence(playerValue, count, authorizationID) {
  return playerRPC(playerValue, "trnm_research_session_evidence_v1", {
    schema: "trnm.nakama.research-session.get-evidence.v1", logical_session_id: sessionID(count),
    authorization_id: authorizationID,
  });
}
async function joinAll(players, externalMatchID, authorizations) {
  for (let index = 0; index < players.length; index += 1) {
    await players[index].socket.joinMatch(externalMatchID, undefined, { authorization_id: authorizations[index].claim.authorization_id });
    await players[index].inbox.take((message) => message.opcode === 12 &&
      ["participant_joined", "participant_reconnected"].includes(message.payload?.event_type) &&
      message.payload?.participant_slot === index + 1, `slot ${index + 1} join`);
  }
}
async function sendAction(playerValue, externalMatchID, action, label) {
  await playerValue.socket.sendMatchState(externalMatchID, 11, encoder.encode(JSON.stringify(action)));
  return (await playerValue.inbox.take((message) => message.opcode === 12 && message.payload?.causation_id === action.action_id, label)).payload;
}
async function actionRound(client, httpKey, count, externalMatchID, players, claims, keys, rotatedSlot = 0) {
  void client; void httpKey;
  let current = await participantArchive(players[0], count, claims[0].authorization_id, 0);
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
function writeState(value) {
  const path = required("TRNM_BLACKBOX_STATE_FILE");
  writeFileSync(path, JSON.stringify(value, null, 2) + "\n", { mode: 0o600 }); chmodSync(path, 0o600);
}
function readState() { return JSON.parse(readFileSync(required("TRNM_BLACKBOX_STATE_FILE"), "utf8")); }
function setHeptaMode(mode) { writeFileSync(required("TRNM_HEPTA_MOCK_CONTROL_FILE"), mode + "\n", { mode: 0o666 }); }

async function runCreatePending3(client, httpKey) {
  const customIDs = [1, 2, 3].map((slot) => `${required("TRNM_CUSTOM_ID_PREFIX")}-${slot}`);
  const players = await Promise.all(customIDs.map((id) => player(client, id)));
  try {
    const keys = agentKeys();
    const epoch = createAuthorizations(3, 1, players.map((value) => value.session.user_id), keys);
    const request = signedCreateRequest(3, epoch);
    writeState({ schema: "trnm.paper-raid.control-blackbox-state.v2", custom_ids: customIDs,
      subject_user_ids: players.map((value) => value.session.user_id), create_request: request,
      epoch_one_claims: epoch.claims, epoch_one_authorizations: epoch.authorizations });
    await rpcHttpKey(client, httpKey, "trnm_research_session_create_v2", request);
    throw new Error("create_after_runtime failpoint did not block");
  } finally { await closePlayers(players); }
}

async function runCreateRecover3(client, httpKey) {
  const state = readState();
  const players = await Promise.all(state.custom_ids.map((id) => player(client, id)));
  try {
    assert(equal(players.map((value) => value.session.user_id), state.subject_user_ids), "Nakama user identities changed during create SIGKILL");
    const first = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_create_v2", state.create_request);
    const second = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_create_v2", state.create_request);
    assert(first.raw === second.raw, "signed create exact replay changed response bytes");
    assert(first.payload.schema === "trnm.nakama.research-control.result.v2" && first.payload.operation === "create", "signed create wrapper differs");
    const runtime = first.payload.result;
    assert(runtime.runtime_generation >= 1 && runtime.roster_root === state.epoch_one_claims[0].roster_root,
      "create recovery did not bind epoch one");

    const conflict = structuredClone(state.create_request);
    conflict.authorization_set_id = "20000000-0000-4000-8000-000000000399";
    conflict.control = signResearchControl("create", state.create_request.control.claim.command_id, sessionID(3), 1,
      conflict.authorization_set_id, researchControlBusinessFrame("create", conflict));
    const rejected = await rpcHttpKeyRejected(httpKey, "trnm_research_session_create_v2", conflict);
    assert(rejected.status === 409, `different-body command replay returned HTTP ${rejected.status}, want 409`);

    const keys = agentKeys();
    await joinAll(players, runtime.external_match_id, state.epoch_one_authorizations);
    await actionRound(client, httpKey, 3, runtime.external_match_id, players, state.epoch_one_claims, keys);

    const beforeDisconnect = await participantArchive(players[0], 3, state.epoch_one_claims[0].authorization_id, 0);
    players[2].socket.disconnect(false); await delay(500);
    const replacement = await player(client, state.custom_ids[2]); players[2] = replacement;
    await replacement.socket.joinMatch(runtime.external_match_id, undefined,
      { authorization_id: state.epoch_one_claims[2].authorization_id });
    await replacement.inbox.take((message) => message.opcode === 12 && message.payload?.event_type === "participant_reconnected", "slot 3 reconnect");
    const caught = await participantArchive(players[0], 3, state.epoch_one_claims[0].authorization_id, beforeDisconnect.event_count);
    assert(caught.events.some((event) => event.event_type === "participant_disconnected"), "cursor catch-up missed disconnect");
    assert(caught.events.some((event) => event.event_type === "participant_reconnected"), "cursor catch-up missed reconnect");
    state.old_external_match_id = runtime.external_match_id;
    state.epoch_one_authorization_ids = state.epoch_one_claims.map((claim) => claim.authorization_id);
    state.cursor_after = beforeDisconnect.event_count;
    writeState(state);
    process.stdout.write(JSON.stringify({ phase: "create-recover3", create_exact_replay: true,
      different_body_conflict: true, create_sigkill_recovered: true, disconnect_reconnect_cursor: true }) + "\n");
  } finally { await closePlayers(players); }
}

async function runResumePending3(client, httpKey) {
  const state = readState();
  const request = signedResumeRequest(3, 1, authorizationSetID(3, 1), 2);
  state.resume_request = request;
  writeState(state);
  await rpcHttpKey(client, httpKey, "trnm_research_session_resume_v2", request);
  throw new Error("resume_after_runtime failpoint did not block");
}

async function runResumeRecoverReplacePending3(client, httpKey) {
  const state = readState();
  const first = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_resume_v2", state.resume_request);
  const second = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_resume_v2", state.resume_request);
  assert(first.raw === second.raw, "signed resume exact replay changed response bytes");
  const runtime = first.payload.result;
  assert(first.payload.operation === "resume" && runtime.runtime_generation >= 2 &&
    runtime.external_match_id !== state.old_external_match_id, "resume SIGKILL recovery did not fence generation two");
  const keys = agentKeys();
  const epoch = createAuthorizations(3, 2, state.subject_user_ids, keys, 2);
  const request = signedReplaceRequest(3, epoch);
  state.epoch_two_claims = epoch.claims;
  state.epoch_two_authorizations = epoch.authorizations;
  state.replace_request = request;
  state.resume_external_match_id = runtime.external_match_id;
  state.resume_runtime_generation = runtime.runtime_generation;
  writeState(state);
  await rpcHttpKey(client, httpKey, "trnm_research_session_replace_roster_v2", request);
  throw new Error("replace_before_signal failpoint did not block");
}

async function runReplaceRecoverCompletePending3(client, httpKey) {
  const state = readState();
  const players = await Promise.all(state.custom_ids.map((id) => player(client, id)));
  try {
    const resumed = await rpcHttpKey(client, httpKey, "trnm_research_session_resume_v2",
      signedResumeRequest(3, 1, authorizationSetID(3, 1), 5));
    assert(resumed.result.runtime_generation > state.resume_runtime_generation,
      "post-replacement-window resume did not advance the fenced runtime generation");
    const first = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_replace_roster_v2", state.replace_request);
    const second = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_replace_roster_v2", state.replace_request);
    assert(first.raw === second.raw, "signed replacement exact replay changed response bytes");
    const rotated = first.payload.result;
    assert(first.payload.operation === "replace_roster" && rotated.roster_version === 2 &&
      rotated.roster_root === state.epoch_two_claims[0].roster_root, "replacement recovery did not install epoch two");
    let oldRejected = false;
    try {
      await players[0].socket.joinMatch(rotated.external_match_id, undefined,
        { authorization_id: state.epoch_one_authorization_ids[0] });
    } catch { oldRejected = true; }
    assert(oldRejected, "old epoch authorization joined after signed replacement");
    await joinAll(players, rotated.external_match_id, state.epoch_two_authorizations);
    const release = await actionRound(client, httpKey, 3, rotated.external_match_id, players,
      state.epoch_two_claims, agentKeys(), 2);
    setHeptaMode("down");
    const request = signedCompleteRequest(3, 2, authorizationSetID(3, 2), release, 4);
    state.complete_request = request;
    state.epoch_two_external_match_id = rotated.external_match_id;
    state.replacement_runtime_generation = rotated.runtime_generation;
    state.epoch_two_authorization_ids = state.epoch_two_claims.map((claim) => claim.authorization_id);
    writeState(state);
    await rpcHttpKey(client, httpKey, "trnm_research_session_complete_v2", request);
    throw new Error("complete_before_signal failpoint did not block");
  } finally { await closePlayers(players); }
}

async function runCompleteRecover3(client, httpKey) {
  const state = readState();
  const resumed = await rpcHttpKey(client, httpKey, "trnm_research_session_resume_v2",
    signedResumeRequest(3, 2, authorizationSetID(3, 2), 6));
  assert(resumed.result.runtime_generation > state.replacement_runtime_generation,
    "post-completion-window resume did not advance the fenced runtime generation");
  const players = await Promise.all(state.custom_ids.map((id) => player(client, id)));
  try {
    // MatchInit durably fences every pre-crash socket. Re-establish the current
    // epoch presence before applying the already accepted completion command;
    // readiness and release acknowledgements remain durable research facts.
    await joinAll(players, resumed.result.external_match_id, state.epoch_two_authorizations);
    const first = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_complete_v2", state.complete_request);
    const second = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_complete_v2", state.complete_request);
    assert(first.raw === second.raw, "signed completion exact replay changed response bytes");
    const evidence = first.payload.result;
    assert(first.payload.operation === "complete" && evidence.completion.roster_version === 2 &&
      evidence.completion.roster_root === state.epoch_two_claims[0].roster_root,
    "completion recovery did not bind epoch two");
    state.completion_commitment_id = evidence.completion.commitment_id;
    state.completion_external_match_id = evidence.external_match_id;
    state.complete_response_raw_base64 = Buffer.from(first.raw, "utf8").toString("base64");
    state.complete_response_sha256 = sha256(Buffer.from(first.raw, "utf8")).toString("hex");
    writeState(state);
    process.stdout.write(JSON.stringify({ phase: "complete-recover3", create_resume_replace_complete_v2: true,
      replacement_signal_sigkill_recovered: true, completion_signal_sigkill_recovered: true,
      exact_applied_receipts: true, old_epoch_rejected: true }) + "\n");
  } finally { await closePlayers(players); }
}

async function runReplayK0ControlUnderK1(client, httpKey) {
  const state = readState();
  const expectedRaw = canonicalBase64(state.complete_response_raw_base64,
    "stored K0 applied control response").toString("utf8");
  assert(sha256(Buffer.from(expectedRaw, "utf8")).toString("hex") === state.complete_response_sha256,
    "stored K0 applied control response hash differs");
  const first = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_complete_v2", state.complete_request);
  const second = await rpcHttpKeyRaw(client, httpKey, "trnm_research_session_complete_v2", state.complete_request);
  assert(first.raw === expectedRaw && second.raw === expectedRaw,
    "K1-active exact replay changed the K0-applied control response bytes");
  assert(first.payload.operation === "complete" &&
    first.payload.result.completion.authority_key_id === "paper-raid-nakama-k0",
  "K1-active replay did not preserve the embedded K0 completion epoch");
  process.stdout.write(JSON.stringify({ phase: "replay-k0-control-under-k1",
    exact_k0_applied_control_replay: true, completion_authority_key_id: "paper-raid-nakama-k0",
    response_sha256: state.complete_response_sha256 }) + "\n");
}

async function runRetiredK0Rejected(client, httpKey) {
  void client;
  const state = readState();
  const archiveFailure = await rpcHttpKeyRejected(httpKey, "trnm_research_session_archive_v1", {
    schema: "trnm.nakama.research-session.get-archive.v1", logical_session_id: sessionID(3),
    after_sequence: 0, limit: 128, authorization_id: state.epoch_two_authorization_ids[0],
  });
  const evidenceFailure = await rpcHttpKeyRejected(httpKey, "trnm_research_session_evidence_v1", {
    schema: "trnm.nakama.research-session.get-evidence.v1", logical_session_id: sessionID(3),
    authorization_id: state.epoch_two_authorization_ids[0],
  });
  assert(/research runtime is not ready/.test(archiveFailure.body),
    `retired K0 archive did not inherit the activation fence: ${archiveFailure.body}`);
  assert(/research runtime is not ready/.test(evidenceFailure.body),
    `retired K0 evidence did not inherit the activation fence: ${evidenceFailure.body}`);
  const controlFailure = await rpcHttpKeyRejected(httpKey, "trnm_research_session_complete_v2",
    state.complete_request);
  assert(/research runtime is not ready/.test(controlFailure.body),
    `retired K0 applied control replay did not inherit the activation fence: ${controlFailure.body}`);
  process.stdout.write(JSON.stringify({ phase: "retired-k0-rejected",
    removed_k0_public_failed_closed: true,
    rejected_paths: ["snapshot_archive", "completion_evidence", "applied_control_replay"],
    activation_fence_observed: true,
    database_mutation_requested: false }) + "\n");
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
  void httpKey;
  const state = readState();
  const customPlayer = await player(client, state.custom_ids[0]);
  try {
    const evidenceRPC = await participantEvidence(customPlayer, 3, state.epoch_two_authorization_ids[0]);
    const evidenceAgain = await participantEvidence(customPlayer, 3, state.epoch_two_authorization_ids[0]);
    assert(evidenceRPC.raw === evidenceAgain.raw, "participant evidence replay changed bytes");
    const evidence = evidenceRPC.payload;
    assert(evidence.completion.commitment_id === state.completion_commitment_id, "post-SIGKILL evidence changed");
    await waitFor(() => {
      const entries = callbackLogs().filter((entry) => entry.path.endsWith("research-session-completions") &&
        logBody(entry).completion.session_id === sessionID(3));
      return entries.some((entry) => entry.response === "tampered_completion") && entries.some((entry) => entry.response === "valid_completion");
    }, "tampered receipt rejection followed by valid completion ACK");
    const entries = callbackLogs().filter((entry) => entry.path.endsWith("research-session-completions") &&
      logBody(entry).completion.session_id === sessionID(3));
    assert(new Set(entries.map((entry) => entry.body_base64)).size === 1, "completion retry body bytes changed");
    assert(new Set(entries.map((entry) => entry.idempotency_key)).size === 1, "completion retry idempotency changed");
    const consumptions = callbackLogs().filter((entry) => entry.path.endsWith("research-session-authorizations/consumed") &&
      logBody(entry).session_id === sessionID(3));
    const epochOne = consumptions.filter((entry) => logBody(entry).roster_version === 1);
    assert(epochOne.some((entry) => entry.response === "down") && epochOne.some((entry) => entry.response === "valid_consumption"), "epoch-one consumption did not survive outage/restart");
    assert(new Set(epochOne.map((entry) => entry.body_base64)).size === 1, "consumption retry body bytes changed");

    const finalArchive = await participantArchive(customPlayer, 3, state.epoch_two_authorization_ids[0], 0);
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
      const created = await rpcHttpKey(client, httpKey, "trnm_research_session_create_v2", signedCreateRequest(count, epoch));
      const runtime = created.result;
      assert(created.operation === "create" && runtime.roster_version === 1, `${count}-member signed create differs`);
      await joinAll(players, runtime.external_match_id, epoch.authorizations);
      const release = await actionRound(client, httpKey, count, runtime.external_match_id, players, epoch.claims, keys);
      const completed = await rpcHttpKey(client, httpKey, "trnm_research_session_complete_v2",
        signedCompleteRequest(count, 1, authorizationSetID(count, 1), release, 4));
      const evidence = completed.result;
      assert(completed.operation === "complete", `${count}-member signed completion differs`);
      const finalArchive = await participantArchive(players[0], count, epoch.claims[0].authorization_id, 0);
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
  case "create-pending3": await runCreatePending3(client, httpKey); break;
  case "create-recover3": await runCreateRecover3(client, httpKey); break;
  case "resume-pending3": await runResumePending3(client, httpKey); break;
  case "resume-recover-replace-pending3": await runResumeRecoverReplacePending3(client, httpKey); break;
  case "replace-recover-complete-pending3": await runReplaceRecoverCompletePending3(client, httpKey); break;
  case "complete-recover3": await runCompleteRecover3(client, httpKey); break;
  case "replay-k0-control-under-k1": await runReplayK0ControlUnderK1(client, httpKey); break;
  case "retired-k0-rejected": await runRetiredK0Rejected(client, httpKey); break;
  case "recover3": await runRecover3(client, httpKey); break;
  case "cardinality": await runCardinality(client, httpKey); break;
  default: throw new Error(`unsupported BLACKBOX_PHASE ${process.env.BLACKBOX_PHASE}`);
}
