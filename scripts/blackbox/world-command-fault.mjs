import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as ed25519Sign,
} from "node:crypto";
import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { Client } from "@heroiclabs/nakama-js";

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest();
}

function digest(bytes) {
  return `sha256:${sha256(bytes).toString("hex")}`;
}

function digestBytes(value) {
  assert(/^sha256:[0-9a-f]{64}$/.test(value), `invalid digest: ${value}`);
  return Buffer.from(value.slice("sha256:".length), "hex");
}

class Frame {
  constructor(domain) {
    this.parts = [Buffer.from(domain, "utf8"), Buffer.from([0])];
  }
  bytes(value) {
    const bytes = Buffer.from(value);
    assert(bytes.length <= 0xffffffff, "canonical field exceeds uint32 length");
    const size = Buffer.alloc(4);
    size.writeUInt32BE(bytes.length);
    this.parts.push(size, bytes);
    return this;
  }
  string(value) { return this.bytes(Buffer.from(value, "utf8")); }
  u32(value) {
    const bytes = Buffer.alloc(4);
    bytes.writeUInt32BE(value);
    this.parts.push(bytes);
    return this;
  }
  u64(value) {
    const bytes = Buffer.alloc(8);
    bytes.writeBigUInt64BE(BigInt(value));
    this.parts.push(bytes);
    return this;
  }
  i64(value) {
    const bytes = Buffer.alloc(8);
    bytes.writeBigInt64BE(BigInt(value));
    this.parts.push(bytes);
    return this;
  }
  digest(value) {
    this.parts.push(digestBytes(value));
    return this;
  }
  finish() { return Buffer.concat(this.parts); }
}

function privateKeyFromSeed(seedBase64) {
  const seed = Buffer.from(seedBase64, "base64");
  assert(seed.length === 32, "Ed25519 seed must contain 32 bytes");
  return createPrivateKey({
    key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), seed]),
    format: "der",
    type: "pkcs8",
  });
}

function rawPublicKey(privateKey) {
  return createPublicKey(privateKey).export({ type: "spki", format: "der" }).subarray(-32);
}

function authorizationClaimBytes(claim) {
  return new Frame("trnm_match_authorization_claim_v1")
    .string(claim.schema)
    .string(claim.authorization_id)
    .string(claim.match_id)
    .string(claim.challenge_id)
    .string(claim.agent_id)
    .string(claim.agent_did)
    .string(claim.agent_key_id)
    .bytes(Buffer.from(claim.agent_public_key, "base64"))
    .string(claim.subject_user_id)
    .u32(claim.participant_slot)
    .string(claim.role)
    .digest(claim.ruleset_hash)
    .digest(claim.dataset_hash)
    .digest(claim.challenge_snapshot_hash)
    .i64(claim.issued_at_unix)
    .i64(claim.expires_at_unix)
    .finish();
}

function signAuthorization(claim, issuerKeyID, issuerPrivateKey) {
  const signingBytes = new Frame("trnm_match_authorization_signature_v1")
    .string(issuerKeyID)
    .bytes(authorizationClaimBytes(claim))
    .finish();
  return {
    claim,
    issuer_key_id: issuerKeyID,
    signature: ed25519Sign(null, signingBytes, issuerPrivateKey).toString("base64"),
  };
}

function commandSigningBytes(command) {
  return new Frame("trnm_match_command_signature_v1")
    .string(command.schema)
    .string(command.command_id)
    .string(command.authorization_id)
    .string(command.match_id)
    .string(command.challenge_id)
    .string(command.agent_id)
    .u32(command.participant_slot)
    .u64(command.participant_sequence)
    .u64(command.expected_match_version)
    .i64(command.issued_at_unix)
    .string(command.payload_type)
    .bytes(Buffer.from(command.payload, "base64"))
    .digest(command.payload_hash)
    .string(command.agent_key_id)
    .finish();
}

function signCommand(unsigned, privateKey) {
  const payload = Buffer.from(unsigned.payload, "base64");
  const command = { ...unsigned, payload_hash: digest(payload) };
  command.signature = ed25519Sign(null, commandSigningBytes(command), privateKey).toString("base64");
  return command;
}

async function rpcHttpKey(runtimeHttpKey, id, payload) {
  const host = process.env.NAKAMA_HOST || "127.0.0.1";
  const port = process.env.NAKAMA_PORT || "7350";
  const response = await fetch(
    `http://${host}:${port}/v2/rpc/${encodeURIComponent(id)}?http_key=${encodeURIComponent(runtimeHttpKey)}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(JSON.stringify(payload)),
      signal: AbortSignal.timeout(Number(process.env.TRNM_FAULT_RPC_TIMEOUT_MS || 20_000)),
    },
  );
  if (!response.ok) {
    throw new Error(`HTTP-key RPC ${id} failed with ${response.status}: ${await response.text()}`);
  }
  const envelope = await response.json();
  return envelope.payload ? JSON.parse(envelope.payload) : undefined;
}

function matchInbox(socket) {
  const messages = [];
  let disconnected = false;
  socket.onmatchdata = (message) => {
    let payload;
    try {
      payload = JSON.parse(textDecoder.decode(message.data));
    } catch (error) {
      payload = { decode_error: String(error) };
    }
    messages.push({ opcode: Number(message.op_code), payload });
  };
  socket.ondisconnect = () => { disconnected = true; };
  return {
    async outcome(commandID, timeoutMilliseconds) {
      const deadline = Date.now() + timeoutMilliseconds;
      while (Date.now() < deadline) {
        const index = messages.findIndex((message) =>
          (message.opcode === 2 && message.payload?.causation_id === commandID) ||
          (message.opcode === 3 && message.payload?.command_id === commandID));
        if (index >= 0) return messages.splice(index, 1)[0];
        if (disconnected) return { opcode: -1, payload: { disconnected: true } };
        await delay(25);
      }
      return { opcode: -2, payload: { timeout: true, queued: messages } };
    },
  };
}

async function authenticatedPlayer(client, customID) {
  const session = await client.authenticateCustom(customID, true);
  assert(session.user_id, `authentication returned no user_id for ${customID}`);
  const socket = client.createSocket(false, false);
  const inbox = matchInbox(socket);
  await socket.connect(session, true);
  return { session, socket, inbox };
}

async function disconnectPlayers(players) {
  for (const player of players) {
    try { player.socket.disconnect(false); } catch { /* cleanup only */ }
  }
  await delay(50);
}

function immutableHashes() {
  return {
    ruleset_hash: digest(Buffer.from("blackbox-ruleset-v1", "utf8")),
    dataset_hash: digest(Buffer.from("blackbox-dataset-v1", "utf8")),
    challenge_snapshot_hash: digest(Buffer.from("blackbox-challenge-snapshot-v1", "utf8")),
  };
}

function saveState(path, state) {
  writeFileSync(path, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
  chmodSync(path, 0o600);
}

async function seed(client, runtimeHttpKey, stateFile) {
  const operatorToken = required("TRNM_NAKAMA_OPERATOR_TOKEN");
  const issuerKeyID = required("TRNM_HEPTA_ISSUER_KEY_ID");
  const issuerKey = privateKeyFromSeed(required("TRNM_HEPTA_ISSUER_PRIVATE_SEED"));
  const agentKeys = [
    privateKeyFromSeed(required("TRNM_AGENT_ONE_PRIVATE_SEED")),
    privateKeyFromSeed(required("TRNM_AGENT_TWO_PRIVATE_SEED")),
  ];
  const customIDs = [required("TRNM_BLACKBOX_CUSTOM_ID_ONE"), required("TRNM_BLACKBOX_CUSTOM_ID_TWO")];
  const logicalMatchID = required("TRNM_BLACKBOX_LOGICAL_MATCH_ID");
  const challengeID = "blackbox-challenge-v1";
  const players = await Promise.all(customIDs.map((id) => authenticatedPlayer(client, id)));
  try {
    const now = Math.floor(Date.now() / 1000);
    const hashes = immutableHashes();
    const claims = players.map((player, index) => ({
      schema: "trnm.match.authorization.v1",
      authorization_id: `blackbox-auth-${index + 1}`,
      match_id: logicalMatchID,
      challenge_id: challengeID,
      agent_id: `blackbox-agent-${index + 1}`,
      agent_did: `did:trnm:blackbox-agent-${index + 1}`,
      agent_key_id: `blackbox-agent-key-${index + 1}`,
      agent_public_key: rawPublicKey(agentKeys[index]).toString("base64"),
      subject_user_id: player.session.user_id,
      participant_slot: index + 1,
      role: index === 0 ? "challenger" : "defender",
      ...hashes,
      issued_at_unix: now - 10,
      expires_at_unix: now + 7200,
    }));
    const authorizations = claims.map((claim) => signAuthorization(claim, issuerKeyID, issuerKey));
    const created = await rpcHttpKey(runtimeHttpKey, "trnm_match_create_v1", {
      schema: "trnm.nakama.create-match.v1",
      operator_token: operatorToken,
      authorizations,
    });
    assert(created?.schema === "trnm.nakama.match-runtime.v1", "create response schema mismatch");
    const externalMatchID = created.external_match_id;
    assert(externalMatchID && created.match_version === 1 && created.runtime_generation === 1, "create response is inconsistent");
    await players[0].socket.joinMatch(externalMatchID, undefined, { authorization_id: claims[0].authorization_id });
    await players[1].socket.joinMatch(externalMatchID, undefined, { authorization_id: claims[1].authorization_id });
    await delay(250);
    const state = {
      schema: "trnm.game.world-command-fault-client-state.v1",
      logical_match_id: logicalMatchID,
      challenge_id: challengeID,
      custom_ids: customIDs,
      subject_user_ids: players.map((player) => player.session.user_id),
      authorization_ids: claims.map((claim) => claim.authorization_id),
      claims,
      external_match_id: externalMatchID,
      runtime_generation: 1,
      match_version: 3,
      participant_sequences: [0, 0],
      commands: {},
      events: {},
    };
    saveState(stateFile, state);
    process.stdout.write(JSON.stringify({ phase: "seed", logical_match_id: logicalMatchID, external_match_id: externalMatchID, match_version: 3 }) + "\n");
  } finally {
    await disconnectPlayers(players);
  }
}

async function sendCommand(client, runtimeHttpKey, stateFile) {
  const operatorToken = required("TRNM_NAKAMA_OPERATOR_TOKEN");
  const state = JSON.parse(readFileSync(stateFile, "utf8"));
  assert(state.schema === "trnm.game.world-command-fault-client-state.v1", "fault client state schema mismatch");
  if ((process.env.TRNM_FAULT_RESUME || "0") === "1") {
    const resumed = await rpcHttpKey(runtimeHttpKey, "trnm_match_resume_v1", {
      schema: "trnm.nakama.resume-match.v1",
      operator_token: operatorToken,
      logical_match_id: state.logical_match_id,
    });
    assert(resumed?.schema === "trnm.nakama.match-runtime.v1", "resume response schema mismatch");
    state.external_match_id = resumed.external_match_id;
    state.runtime_generation = resumed.runtime_generation;
    state.match_version = resumed.match_version;
  }
  const players = await Promise.all(state.custom_ids.map((id) => authenticatedPlayer(client, id)));
  try {
    assert(players.every((player, index) => player.session.user_id === state.subject_user_ids[index]), "participant identity changed across restart");
    await players[0].socket.joinMatch(state.external_match_id, undefined, { authorization_id: state.authorization_ids[0] });
    await players[1].socket.joinMatch(state.external_match_id, undefined, { authorization_id: state.authorization_ids[1] });
    await delay(200);

    const slot = Number(process.env.TRNM_FAULT_PARTICIPANT_SLOT || "1");
    assert(slot === 1 || slot === 2, "TRNM_FAULT_PARTICIPANT_SLOT must be 1 or 2");
    const index = slot - 1;
    const commandID = required("TRNM_FAULT_COMMAND_ID");
    const payloadText = process.env.TRNM_FAULT_COMMAND_JSON || '{"delta":1,"kind":"advance"}';
    assert(JSON.stringify(JSON.parse(payloadText)) === payloadText, "TRNM_FAULT_COMMAND_JSON must be compact canonical JSON for the fixture");
    const claim = state.claims[index];
    const key = privateKeyFromSeed(required(index === 0 ? "TRNM_AGENT_ONE_PRIVATE_SEED" : "TRNM_AGENT_TWO_PRIVATE_SEED"));
    const command = signCommand({
      schema: "trnm.match.command.v1",
      command_id: commandID,
      authorization_id: claim.authorization_id,
      match_id: state.logical_match_id,
      challenge_id: state.challenge_id,
      agent_id: claim.agent_id,
      participant_slot: slot,
      participant_sequence: state.participant_sequences[index] + 1,
      expected_match_version: state.match_version,
      issued_at_unix: Math.floor(Date.now() / 1000),
      payload_type: process.env.TRNM_WORLD_COMMAND_SCHEMA_ID || "trnm.blackbox.move.v1",
      payload: Buffer.from(payloadText, "utf8").toString("base64"),
      agent_key_id: claim.agent_key_id,
    }, key);
    state.commands[commandID] = command;
    saveState(stateFile, state);
    try {
      await players[index].socket.sendMatchState(state.external_match_id, 1, textEncoder.encode(JSON.stringify(command)));
    } catch (error) {
      const expected = process.env.EXPECT_COMMAND_OUTCOME || "event";
      if (expected !== "disconnect") throw error;
      process.stdout.write(JSON.stringify({ phase: "send", command_id: commandID, outcome: "disconnect", error: String(error) }) + "\n");
      return;
    }
    const outcome = await players[index].inbox.outcome(commandID, Number(process.env.TRNM_FAULT_COMMAND_TIMEOUT_MS || 20_000));
    const expected = process.env.EXPECT_COMMAND_OUTCOME || "event";
    const actual = outcome.opcode === 2 ? "event" : outcome.opcode === 3 ? "error" : "disconnect";
    assert(actual === expected, `expected command outcome ${expected}, received ${actual}: ${JSON.stringify(outcome)}`);
    if (actual === "event") {
      assert(outcome.payload.sequence === state.match_version, "command event sequence did not consume the captured global cursor");
      assert(outcome.payload.match_version === state.match_version + 1, "command event match version did not advance exactly once");
      state.match_version += 1;
      state.participant_sequences[index] += 1;
      state.events[commandID] = outcome.payload;
      saveState(stateFile, state);
    }
    process.stdout.write(JSON.stringify({ phase: "send", command_id: commandID, outcome: actual, payload: outcome.payload }) + "\n");
  } finally {
    await disconnectPlayers(players);
  }
}

async function status(runtimeHttpKey, stateFile) {
  const state = JSON.parse(readFileSync(stateFile, "utf8"));
  const response = await rpcHttpKey(runtimeHttpKey, "trnm_world_command_status_v1", {
    schema: "trnm.game.world-command-status-request.v1",
    operator_token: required("TRNM_NAKAMA_OPERATOR_TOKEN"),
    logical_match_id: state.logical_match_id,
  });
  assert(response?.schema === "trnm.game.world-command-status-response.v1", "status response schema mismatch");
  assert(response.cutover_authorized === false && response.public_online_enabled === false && response.public_player_market_enabled === false, "status response overclaimed release authority");
  process.stdout.write(JSON.stringify({ phase: "status", response }) + "\n");
}

async function ready(runtimeHttpKey) {
  const response = await rpcHttpKey(runtimeHttpKey, "trnm_world_command_ready_v1", {});
  assert(response?.schema === "trnm.game.world-command-ready.v1", "World readiness schema mismatch");
  assert(response.profile === "world_transition_v1" && response.ready === true, `World target profile is not ready: ${JSON.stringify(response)}`);
  assert(response.external_execution_under_lock === false && response.cutover_authorized === false && response.public_online_enabled === false, "World readiness overclaimed authority");
  process.stdout.write(JSON.stringify({ phase: "ready", response }) + "\n");
}

const client = new Client(
  required("NAKAMA_SERVER_KEY"),
  process.env.NAKAMA_HOST || "127.0.0.1",
  process.env.NAKAMA_PORT || "7350",
  false,
);
const runtimeHttpKey = required("NAKAMA_RUNTIME_HTTP_KEY");
const stateFile = process.env.TRNM_BLACKBOX_STATE_FILE || "/run/trnm/world-command-fault-state.json";
const phase = process.env.BLACKBOX_PHASE || "ready";

switch (phase) {
  case "ready":
    await ready(runtimeHttpKey);
    break;
  case "seed":
    await seed(client, runtimeHttpKey, stateFile);
    break;
  case "send":
    await sendCommand(client, runtimeHttpKey, stateFile);
    break;
  case "status":
    await status(runtimeHttpKey, stateFile);
    break;
  default:
    throw new Error(`unsupported BLACKBOX_PHASE: ${phase}`);
}
