import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as ed25519Sign,
  verify as ed25519Verify,
} from "node:crypto";
import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { Client } from "@heroiclabs/nakama-js";

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

function required(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function equalJSON(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function rpcHttpKey(client, runtimeHttpKey, id, payload) {
  // nakama-js rpcHttpKey uses GET and puts the serialized input in the query
  // string. A two-participant signed authorization snapshot legitimately
  // exceeds conservative HTTP request-line limits, so exercise Nakama's
  // official POST RPC endpoint instead. The REST body is a JSON string whose
  // contents are the runtime payload.
  void client;
  const host = process.env.NAKAMA_HOST || "127.0.0.1";
  const port = process.env.NAKAMA_PORT || "7350";
  const url = `http://${host}:${port}/v2/rpc/${encodeURIComponent(id)}?http_key=${encodeURIComponent(runtimeHttpKey)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(JSON.stringify(payload)),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`HTTP-key RPC ${id} failed with ${response.status}: ${body}`);
  }
  const envelope = await response.json();
  return {
    id: envelope.id,
    payload: envelope.payload ? JSON.parse(envelope.payload) : undefined,
  };
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

// This encoder is deliberately independent of the Go implementation. A value
// accepted by the live runtime therefore proves that the documented framing is
// cross-language, not merely self-consistent inside one package.
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

  string(value) {
    return this.bytes(Buffer.from(value, "utf8"));
  }

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

  finish() {
    return Buffer.concat(this.parts);
  }
}

function privateKeyFromSeed(seedBase64) {
  const seed = Buffer.from(seedBase64, "base64");
  assert(seed.length === 32, "Ed25519 seed must contain 32 bytes");
  const prefix = Buffer.from("302e020100300506032b657004220420", "hex");
  return createPrivateKey({ key: Buffer.concat([prefix, seed]), format: "der", type: "pkcs8" });
}

function publicKeyFromRaw(rawBase64) {
  const raw = Buffer.from(rawBase64, "base64");
  assert(raw.length === 32, "Ed25519 public key must contain 32 bytes");
  const prefix = Buffer.from("302a300506032b6570032100", "hex");
  return createPublicKey({ key: Buffer.concat([prefix, raw]), format: "der", type: "spki" });
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

function terminalFactsBytes(facts) {
  return new Frame("trnm_match_terminal_facts_v1")
    .string(facts.result_code)
    .u32(facts.winner_slot)
    .digest(facts.outcome_hash)
    .finish();
}

function completionSigningBytes(completion) {
  return new Frame("trnm_match_completed_signature_v1")
    .string(completion.schema)
    .digest(completion.commitment_id)
    .string(completion.match_id)
    .string(completion.challenge_id)
    .bytes(terminalFactsBytes(completion.terminal_facts))
    .u64(completion.event_count)
    .digest(completion.event_root)
    .digest(completion.roster_root)
    .digest(completion.ruleset_hash)
    .digest(completion.dataset_hash)
    .digest(completion.challenge_snapshot_hash)
    .digest(completion.archive_hash)
    .i64(completion.completed_at_unix)
    .string(completion.authority_key_id)
    .finish();
}

function commitmentID(matchID, eventRoot, archiveHash) {
  return digest(
    new Frame("trnm_match_commitment_id_v1")
      .string(matchID)
      .digest(eventRoot)
      .digest(archiveHash)
      .finish(),
  );
}

function canonicalBase64(value, label) {
  assert(typeof value === "string", `${label} is not a base64 string`);
  const decoded = Buffer.from(value, "base64");
  assert(decoded.toString("base64") === value, `${label} is not canonical padded base64`);
  return decoded;
}

function eventFactsBytes(event) {
  const payload = canonicalBase64(event.payload, `event ${event.sequence} payload`);
  assert(event.payload_hash === digest(payload), `event ${event.sequence} payload hash mismatch`);
  return new Frame("trnm_match_event_v1")
    .string(event.schema)
    .string(event.event_id)
    .string(event.event_type)
    .string(event.match_id)
    .string(event.challenge_id)
    .u64(event.sequence)
    .string(event.causation_id)
    .i64(event.occurred_at_unix)
    .u32(event.participant_slot)
    .u64(event.match_version)
    .string(event.payload_type)
    .bytes(payload)
    .digest(event.payload_hash)
    .finish();
}

function canonicalEventID(event) {
  return digest(
    new Frame("trnm_match_event_id_v1")
      .string(event.match_id)
      .string(event.causation_id)
      .u64(event.sequence)
      .finish(),
  );
}

function validateEvent(event, expectedSequence, matchID, challengeID) {
  assert(event.schema === "trnm.match.event.v1", `event ${expectedSequence} schema mismatch`);
  assert(event.sequence === expectedSequence, `event sequence ${event.sequence} is not contiguous at ${expectedSequence}`);
  assert(event.match_version === event.sequence + 1, `event ${expectedSequence} match version mismatch`);
  assert(event.match_id === matchID, `event ${expectedSequence} match identity mismatch`);
  assert(event.challenge_id === challengeID, `event ${expectedSequence} challenge identity mismatch`);
  assert(event.event_id === canonicalEventID(event), `event ${expectedSequence} canonical ID mismatch`);
  assert(event.event_hash === digest(eventFactsBytes(event)), `event ${expectedSequence} hash mismatch`);
}

function eventRoot(events) {
  let level = events.map((event) => {
    const sequence = Buffer.alloc(8);
    sequence.writeBigUInt64BE(BigInt(event.sequence));
    return sha256(Buffer.concat([Buffer.from("trnm_match_event_leaf_v1\0", "utf8"), sequence, digestBytes(event.event_hash)]));
  });
  assert(level.length > 0, "cannot compute an event root for an empty archive");
  while (level.length > 1) {
    const next = [];
    for (let index = 0; index < level.length; index += 2) {
      const right = index + 1 < level.length ? level[index + 1] : level[index];
      next.push(sha256(Buffer.concat([Buffer.from("trnm_binary_merkle_node_v1\0", "utf8"), level[index], right])));
    }
    level = next;
  }
  return `sha256:${level[0].toString("hex")}`;
}

function rosterRoot(roster) {
  assert(Array.isArray(roster) && roster.length === 2, "archive roster must contain exactly two participants");
  const ordered = [...roster].sort((left, right) => left.participant_slot - right.participant_slot);
  assert(ordered[0].participant_slot === 1 && ordered[1].participant_slot === 2, "archive roster must contain slots 1 and 2");
  for (const field of ["subject_user_id", "agent_id", "agent_did", "agent_key_id", "agent_key_hash"]) {
    assert(ordered[0][field] !== ordered[1][field], `archive roster ${field} values are not unique`);
  }
  let frame = new Frame("trnm_match_roster_v1").u32(ordered.length);
  for (const entry of ordered) {
    frame = frame
      .u32(entry.participant_slot)
      .string(entry.subject_user_id)
      .string(entry.agent_id)
      .string(entry.agent_did)
      .string(entry.agent_key_id)
      .digest(entry.agent_key_hash)
      .string(entry.role);
  }
  return digest(frame.finish());
}

function canonicalArchive(events) {
  let frame = new Frame("trnm_match_event_archive_v1").u64(events.length);
  for (const event of events) {
    frame = frame.bytes(eventFactsBytes(event)).bytes(digestBytes(event.event_hash));
  }
  return frame.finish();
}

function verifyArchive(events, roster, expectedMatchID) {
  assert(Array.isArray(events) && events.length > 0, "archive events are empty");
  const matchID = expectedMatchID || events[0].match_id;
  const challengeID = events[0].challenge_id;
  const joinedSlots = new Set();
  let sawCommand = false;
  let completed = false;
  let previousTime = -1;
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index];
    validateEvent(event, index + 1, matchID, challengeID);
    assert(event.occurred_at_unix >= previousTime, `event ${event.sequence} time moved backwards`);
    previousTime = event.occurred_at_unix;
    if (event.event_type === "participant_joined") {
      assert(!sawCommand && !completed, "participant join appeared after match activity");
      assert(event.payload_type === "trnm.participant.joined.v1", "participant join payload type mismatch");
      assert((event.participant_slot === 1 || event.participant_slot === 2) && !joinedSlots.has(event.participant_slot), "participant join slot is invalid or duplicated");
      joinedSlots.add(event.participant_slot);
    } else if (event.event_type === "agent_command_applied") {
      assert(joinedSlots.size === 2 && !completed, "command appeared outside the active phase");
      assert(event.participant_slot === 1 || event.participant_slot === 2, "command participant slot is invalid");
      sawCommand = true;
    } else if (event.event_type === "match_completed") {
      assert(joinedSlots.size === 2 && sawCommand && !completed, "completion appeared outside the terminal phase");
      assert(index === events.length - 1, "completion is not the final archive event");
      assert(event.participant_slot === 0, "completion participant slot is not zero");
      assert(event.payload_type === "trnm.match.terminal-facts.v1", "completion payload type mismatch");
      completed = true;
    } else {
      throw new Error(`unsupported archive event type ${event.event_type}`);
    }
  }
  assert(joinedSlots.size === 2, "archive does not contain both admissions");
  assert(completed, "archive does not end in completion");
  return {
    event_root: eventRoot(events),
    roster_root: rosterRoot(roster),
    archive_hash: digest(canonicalArchive(events)),
  };
}

function assertThrows(operation, label) {
  let rejected = false;
  try {
    operation();
  } catch {
    rejected = true;
  }
  assert(rejected, `${label} was accepted by the independent archive verifier`);
}

async function archiveRPC(player, request) {
  const raw = await player.socket.rpc("trnm_match_archive_v1", JSON.stringify(request));
  assert(typeof raw?.payload === "string", "realtime archive RPC returned no raw payload");
  return { raw: raw.payload, payload: JSON.parse(raw.payload) };
}

function matchInbox(socket) {
  const messages = [];
  socket.onmatchdata = (message) => {
    let payload;
    try {
      payload = JSON.parse(textDecoder.decode(message.data));
    } catch (error) {
      payload = { decode_error: String(error) };
    }
    messages.push({ opcode: Number(message.op_code), payload });
  };
  return {
    async take(predicate, label, timeoutMilliseconds = 10_000) {
      const deadline = Date.now() + timeoutMilliseconds;
      while (Date.now() < deadline) {
        const index = messages.findIndex(predicate);
        if (index >= 0) {
          return messages.splice(index, 1)[0];
        }
        await delay(20);
      }
      throw new Error(`timed out waiting for ${label}; queued=${JSON.stringify(messages)}`);
    },
    async expectNone(predicate, label, timeoutMilliseconds = 500) {
      const deadline = Date.now() + timeoutMilliseconds;
      while (Date.now() < deadline) {
        const unexpected = messages.find(predicate);
        if (unexpected) {
          throw new Error(`unexpected ${label}: ${JSON.stringify(unexpected)}`);
        }
        await delay(20);
      }
    },
  };
}

function assertEnvironmentScope(phase) {
  const serviceOnly = [
    "TRNM_NAKAMA_DB_PASSWORD",
    "TRNM_HEPTA_ISSUER_KEYS",
    "TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY",
    "NAKAMA_SESSION_ENCRYPTION_KEY",
    "NAKAMA_SESSION_REFRESH_ENCRYPTION_KEY",
    "NAKAMA_CONSOLE_PASSWORD",
    "NAKAMA_CONSOLE_SIGNING_KEY",
  ];
  for (const name of serviceOnly) {
    assert(process.env[name] === undefined, `black-box client unexpectedly inherited ${name}`);
  }
  if (phase === "health") {
    for (const name of [
      "TRNM_NAKAMA_OPERATOR_TOKEN",
      "TRNM_HEPTA_ISSUER_PRIVATE_SEED",
      "TRNM_AGENT_ONE_PRIVATE_SEED",
      "TRNM_AGENT_TWO_PRIVATE_SEED",
      "TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY",
    ]) {
      assert(process.env[name] === undefined, `health phase unexpectedly inherited ${name}`);
    }
  }
  if (phase === "resume") {
    for (const name of [
      "TRNM_HEPTA_ISSUER_KEY_ID",
      "TRNM_HEPTA_ISSUER_PRIVATE_SEED",
      "TRNM_AGENT_ONE_PRIVATE_SEED",
      "TRNM_AGENT_ONE_PUBLIC_KEY",
      "TRNM_AGENT_TWO_PUBLIC_KEY",
      "TRNM_BLACKBOX_CUSTOM_ID_ONE",
      "TRNM_BLACKBOX_CUSTOM_ID_TWO",
      "TRNM_BLACKBOX_LOGICAL_MATCH_ID",
    ]) {
      assert(process.env[name] === undefined, `resume phase unexpectedly inherited ${name}`);
    }
  }
}

async function authenticatedPlayer(client, customID) {
  const session = await client.authenticateCustom(customID, true);
  assert(session.user_id, `custom authentication returned no user_id for ${customID}`);
  const socket = client.createSocket(false, false);
  const inbox = matchInbox(socket);
  await socket.connect(session, true);
  return { session, socket, inbox };
}

async function disconnectPlayers(players) {
  for (const player of players) {
    try {
      player.socket.disconnect(false);
    } catch {
      // Cleanup must not hide the black-box assertion that already ran.
    }
  }
  await delay(50);
}

async function runHealth(client, runtimeHttpKey) {
  const expectedReady = (process.env.EXPECT_READY || "true") === "true";
  const health = await rpcHttpKey(client, runtimeHttpKey, "trnm_health_v1", {});
  assert(health?.payload?.schema === "trnm.nakama.health.v1", "runtime health schema mismatch");
  assert(health.payload.healthy === true, "runtime plugin is not healthy");

  const readiness = await rpcHttpKey(client, runtimeHttpKey, "trnm_ready_v1", {});
  assert(readiness?.payload?.schema === "trnm.nakama.readiness.v1", "readiness schema mismatch");
  assert(readiness.payload.ready === expectedReady, `expected ready=${expectedReady}, got ${readiness.payload.ready}`);
  if (expectedReady) {
    assert(readiness.payload.checks?.configuration === "ok", "configuration readiness failed");
    assert(readiness.payload.checks?.database === "ok", "database readiness failed");
    assert(readiness.payload.checks?.storage === "ok", "storage readiness failed");
  } else {
    assert(
      readiness.payload.checks?.database === "error" || readiness.payload.checks?.storage === "error",
      "unready response did not identify database or storage failure",
    );
  }
  process.stdout.write(JSON.stringify({ phase: "health", health: health.payload, readiness: readiness.payload }) + "\n");
}

function immutableHashes() {
  return {
    ruleset_hash: digest(Buffer.from("blackbox-ruleset-v1", "utf8")),
    dataset_hash: digest(Buffer.from("blackbox-dataset-v1", "utf8")),
    challenge_snapshot_hash: digest(Buffer.from("blackbox-challenge-snapshot-v1", "utf8")),
  };
}

async function runPrepare(client, runtimeHttpKey, stateFile) {
  const operatorToken = required("TRNM_NAKAMA_OPERATOR_TOKEN");
  const issuerKeyID = required("TRNM_HEPTA_ISSUER_KEY_ID");
  const issuerKey = privateKeyFromSeed(required("TRNM_HEPTA_ISSUER_PRIVATE_SEED"));
  const agentOneKey = privateKeyFromSeed(required("TRNM_AGENT_ONE_PRIVATE_SEED"));
  const agentTwoKey = privateKeyFromSeed(required("TRNM_AGENT_TWO_PRIVATE_SEED"));
  assert(
    rawPublicKey(agentOneKey).toString("base64") === required("TRNM_AGENT_ONE_PUBLIC_KEY"),
    "agent-one fixture seed/public-key mismatch",
  );
  assert(
    rawPublicKey(agentTwoKey).toString("base64") === required("TRNM_AGENT_TWO_PUBLIC_KEY"),
    "agent-two fixture seed/public-key mismatch",
  );
  const customIDs = [required("TRNM_BLACKBOX_CUSTOM_ID_ONE"), required("TRNM_BLACKBOX_CUSTOM_ID_TWO")];
  const logicalMatchID = required("TRNM_BLACKBOX_LOGICAL_MATCH_ID");
  const challengeID = "blackbox-challenge-v1";
  const players = await Promise.all(customIDs.map((id) => authenticatedPlayer(client, id)));
  try {
    const now = Math.floor(Date.now() / 1000);
    const common = immutableHashes();
    const claims = players.map((player, index) => ({
      schema: "trnm.match.authorization.v1",
      authorization_id: `blackbox-auth-${index + 1}`,
      match_id: logicalMatchID,
      challenge_id: challengeID,
      agent_id: `blackbox-agent-${index + 1}`,
      agent_did: `did:trnm:blackbox-agent-${index + 1}`,
      agent_key_id: `blackbox-agent-key-${index + 1}`,
      agent_public_key: rawPublicKey(index === 0 ? agentOneKey : agentTwoKey).toString("base64"),
      subject_user_id: player.session.user_id,
      participant_slot: index + 1,
      role: index === 0 ? "challenger" : "defender",
      ...common,
      issued_at_unix: now - 10,
      expires_at_unix: now + 3600,
    }));
    const authorizations = claims.map((claim) => signAuthorization(claim, issuerKeyID, issuerKey));

    const created = await rpcHttpKey(client, runtimeHttpKey, "trnm_match_create_v1", {
      schema: "trnm.nakama.create-match.v1",
      operator_token: operatorToken,
      authorizations,
    });
    assert(created?.payload?.schema === "trnm.nakama.match-runtime.v1", "create RPC schema mismatch");
    assert(created.payload.logical_match_id === logicalMatchID, "create RPC logical match mismatch");
    assert(created.payload.runtime_generation === 1, "first runtime generation must be 1");
    assert(created.payload.match_version === 1, "new match version must be 1");
    const externalMatchID = created.payload.external_match_id;
    assert(externalMatchID, "create RPC returned no external match ID");

    let duplicateCreateRejected = false;
    try {
      await rpcHttpKey(client, runtimeHttpKey, "trnm_match_create_v1", {
        schema: "trnm.nakama.create-match.v1",
        operator_token: operatorToken,
        authorizations,
      });
    } catch (error) {
      duplicateCreateRejected = /logical match already exists|create-match request/.test(String(error));
    }
    assert(duplicateCreateRejected, "same logical match and authorizations were accepted twice");

    let wrongSubjectRejected = false;
    try {
      await players[1].socket.joinMatch(externalMatchID, undefined, { authorization_id: claims[0].authorization_id });
    } catch {
      wrongSubjectRejected = true;
    }
    assert(wrongSubjectRejected, "another user consumed slot 1's authorization");
    await Promise.all([
      players[0].inbox.expectNone((message) => message.opcode === 2, "event after wrong-subject admission at slot 1"),
      players[1].inbox.expectNone((message) => message.opcode === 2, "event after wrong-subject admission at slot 2"),
    ]);

    await players[0].socket.joinMatch(externalMatchID, undefined, { authorization_id: claims[0].authorization_id });
    const joinOne = await players[0].inbox.take(
      (message) => message.opcode === 2 && message.payload?.event_type === "participant_joined",
      "slot-1 durable join event",
    );
    assert(joinOne.payload.sequence === 1, "rejected admission or duplicate create appended a hidden event");
    assert(joinOne.payload.match_version === 2, "rejected admission or duplicate create advanced match version");
    await players[1].socket.joinMatch(externalMatchID, undefined, { authorization_id: claims[1].authorization_id });
    const joinTwoAtOne = await players[0].inbox.take(
      (message) => message.opcode === 2 && message.payload?.event_type === "participant_joined" && message.payload?.sequence === 2,
      "slot-2 join broadcast at slot 1",
    );
    const joinTwoAtTwo = await players[1].inbox.take(
      (message) => message.opcode === 2 && message.payload?.event_type === "participant_joined",
      "slot-2 join broadcast at slot 2",
    );
    assert(equalJSON(joinTwoAtOne.payload, joinTwoAtTwo.payload), "slot-2 join broadcast differed by recipient");

    const commandOne = signCommand(
      {
        schema: "trnm.match.command.v1",
        command_id: "blackbox-command-a-1",
        authorization_id: claims[0].authorization_id,
        match_id: logicalMatchID,
        challenge_id: challengeID,
        agent_id: claims[0].agent_id,
        participant_slot: 1,
        participant_sequence: 1,
        expected_match_version: 3,
        issued_at_unix: Math.floor(Date.now() / 1000),
        payload_type: "trnm.blackbox.move.v1",
        payload: Buffer.from('{"move":"alpha"}', "utf8").toString("base64"),
        agent_key_id: claims[0].agent_key_id,
      },
      agentOneKey,
    );
    await players[0].socket.sendMatchState(externalMatchID, 1, textEncoder.encode(JSON.stringify(commandOne)));
    const firstApplied = await players[0].inbox.take(
      (message) => message.opcode === 2 && message.payload?.causation_id === commandOne.command_id,
      "first authoritative command event at sender",
    );
    const firstAppliedAtPeer = await players[1].inbox.take(
      (message) => message.opcode === 2 && message.payload?.causation_id === commandOne.command_id,
      "first authoritative command event at peer",
    );
    assert(equalJSON(firstApplied.payload, firstAppliedAtPeer.payload), "new command broadcast differed by recipient");
    assert(firstApplied.payload.sequence === 3, "first command event sequence must be 3");
    assert(firstApplied.payload.match_version === 4, "first command must advance match version to 4");

    await players[0].socket.sendMatchState(externalMatchID, 1, textEncoder.encode(JSON.stringify(commandOne)));
    const exactReplay = await players[0].inbox.take(
      (message) => message.opcode === 2 && message.payload?.causation_id === commandOne.command_id,
      "exact command replay",
    );
    assert(equalJSON(firstApplied.payload, exactReplay.payload), "exact command replay changed its event bytes");
    await players[1].inbox.expectNone(
      (message) => message.opcode === 2 && message.payload?.causation_id === commandOne.command_id,
      "exact replay broadcast at peer",
    );

    const sameIDTamper = signCommand(
      {
        ...commandOne,
        payload: Buffer.from('{"move":"tampered"}', "utf8").toString("base64"),
        signature: undefined,
      },
      agentOneKey,
    );
    await players[0].socket.sendMatchState(externalMatchID, 1, textEncoder.encode(JSON.stringify(sameIDTamper)));
    const conflict = await players[0].inbox.take(
      (message) => message.opcode === 3 && message.payload?.command_id === commandOne.command_id,
      "same-ID tamper rejection",
    );
    assert(/idempotency conflict/.test(conflict.payload.reason), "same-ID tamper did not fail as an idempotency conflict");
    await players[1].inbox.expectNone(
      (message) => message.opcode === 3 && message.payload?.command_id === commandOne.command_id,
      "same-ID rejection at peer",
    );

    const outOfOrder = signCommand(
      {
        schema: "trnm.match.command.v1",
        command_id: "blackbox-command-b-gap",
        authorization_id: claims[1].authorization_id,
        match_id: logicalMatchID,
        challenge_id: challengeID,
        agent_id: claims[1].agent_id,
        participant_slot: 2,
        participant_sequence: 2,
        expected_match_version: 4,
        issued_at_unix: Math.floor(Date.now() / 1000),
        payload_type: "trnm.blackbox.move.v1",
        payload: Buffer.from('{"move":"gap"}', "utf8").toString("base64"),
        agent_key_id: claims[1].agent_key_id,
      },
      agentTwoKey,
    );
    await players[1].socket.sendMatchState(externalMatchID, 1, textEncoder.encode(JSON.stringify(outOfOrder)));
    const sequenceRejection = await players[1].inbox.take(
      (message) => message.opcode === 3 && message.payload?.command_id === outOfOrder.command_id,
      "out-of-order command rejection",
    );
    assert(/expected participant sequence 1/.test(sequenceRejection.payload.reason), "out-of-order command was not rejected at sequence 1");
    await players[0].inbox.expectNone(
      (message) => message.opcode === 3 && message.payload?.command_id === outOfOrder.command_id,
      "out-of-order rejection at peer",
    );

    const state = {
      schema: "trnm.nakama.blackbox-state.v1",
      logical_match_id: logicalMatchID,
      old_external_match_id: externalMatchID,
      old_runtime_generation: 1,
      custom_ids: customIDs,
      subject_user_ids: players.map((player) => player.session.user_id),
      authorization_ids: claims.map((claim) => claim.authorization_id),
      challenge_id: challengeID,
      claims,
      command_one: commandOne,
      command_one_event: firstApplied.payload,
    };
    writeFileSync(stateFile, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
    chmodSync(stateFile, 0o600);
    process.stdout.write(
      JSON.stringify({
        phase: "prepare",
        logical_match_id: logicalMatchID,
        external_match_id: externalMatchID,
        first_event_hash: firstApplied.payload.event_hash,
        exact_replay: true,
        tamper_rejected: true,
        out_of_order_rejected: true,
        duplicate_create_rejected: true,
        wrong_subject_authorization_rejected_without_mutation: true,
        broadcast_scopes_verified: true,
      }) + "\n",
    );
  } finally {
    await disconnectPlayers(players);
  }
}

async function runResume(client, runtimeHttpKey, stateFile) {
  const operatorToken = required("TRNM_NAKAMA_OPERATOR_TOKEN");
  const agentTwoKey = privateKeyFromSeed(required("TRNM_AGENT_TWO_PRIVATE_SEED"));
  const state = JSON.parse(readFileSync(stateFile, "utf8"));
  assert(state.schema === "trnm.nakama.blackbox-state.v1", "black-box state schema mismatch");
  const players = await Promise.all(state.custom_ids.map((id) => authenticatedPlayer(client, id)));
  try {
    assert(
      equalJSON(players.map((player) => player.session.user_id), state.subject_user_ids),
      "custom reauthentication changed participant user IDs",
    );
    const resumed = await rpcHttpKey(client, runtimeHttpKey, "trnm_match_resume_v1", {
      schema: "trnm.nakama.resume-match.v1",
      operator_token: operatorToken,
      logical_match_id: state.logical_match_id,
    });
    assert(resumed?.payload?.schema === "trnm.nakama.match-runtime.v1", "resume RPC schema mismatch");
    assert(resumed.payload.external_match_id, "resume RPC returned no external match ID");
    assert(resumed.payload.external_match_id !== state.old_external_match_id, "crash resume reused the dead external match ID");
    assert(resumed.payload.runtime_generation === 2, "crash resume did not advance runtime generation to 2");
    assert(resumed.payload.status === "active", "resumed logical match must remain active");
    assert(resumed.payload.match_version === 4, "resumed logical match version must remain 4");
    const externalMatchID = resumed.payload.external_match_id;

    await players[0].socket.joinMatch(externalMatchID, undefined, { authorization_id: state.authorization_ids[0] });
    await players[1].socket.joinMatch(externalMatchID, undefined, { authorization_id: state.authorization_ids[1] });
    await Promise.all([
      players[0].inbox.expectNone((message) => message.opcode === 2 && message.payload?.event_type === "participant_joined", "reconnect join event at slot 1"),
      players[1].inbox.expectNone((message) => message.opcode === 2 && message.payload?.event_type === "participant_joined", "reconnect join event at slot 2"),
    ]);

    await players[0].socket.sendMatchState(externalMatchID, 1, textEncoder.encode(JSON.stringify(state.command_one)));
    const replayAfterCrash = await players[0].inbox.take(
      (message) => message.opcode === 2 && message.payload?.causation_id === state.command_one.command_id,
      "post-crash exact command replay",
    );
    assert(equalJSON(replayAfterCrash.payload, state.command_one_event), "post-crash command replay did not return the original event");
    await players[1].inbox.expectNone(
      (message) => message.opcode === 2 && message.payload?.causation_id === state.command_one.command_id,
      "post-crash replay broadcast at peer",
    );

    const claimTwo = state.claims[1];
    const commandTwo = signCommand(
      {
        schema: "trnm.match.command.v1",
        command_id: "blackbox-command-b-1",
        authorization_id: state.authorization_ids[1],
        match_id: state.logical_match_id,
        challenge_id: state.challenge_id,
        agent_id: claimTwo.agent_id,
        participant_slot: 2,
        participant_sequence: 1,
        expected_match_version: 4,
        issued_at_unix: Math.floor(Date.now() / 1000),
        payload_type: "trnm.blackbox.move.v1",
        payload: Buffer.from('{"move":"beta"}', "utf8").toString("base64"),
        agent_key_id: claimTwo.agent_key_id,
      },
      agentTwoKey,
    );
    await disconnectPlayers([players[0]]);
    await players[1].socket.sendMatchState(externalMatchID, 1, textEncoder.encode(JSON.stringify(commandTwo)));
    const secondApplied = await players[1].inbox.take(
      (message) => message.opcode === 2 && message.payload?.causation_id === commandTwo.command_id,
      "post-crash new command at sender",
    );
    assert(secondApplied.payload.sequence === 4, "post-crash replay appended a hidden event before command two");
    assert(secondApplied.payload.match_version === 5, "post-crash command did not advance version from 4 to 5");

    const recoveredPlayer = await authenticatedPlayer(client, state.custom_ids[0]);
    assert(recoveredPlayer.session.user_id === state.subject_user_ids[0], "disconnect recovery changed slot 1's user ID");
    players[0] = recoveredPlayer;
    await players[0].socket.joinMatch(externalMatchID, undefined, { authorization_id: state.authorization_ids[0] });
    await players[0].inbox.expectNone(
      (message) => message.opcode === 2 && message.payload?.event_type === "participant_joined",
      "disconnect recovery join event",
    );
    const catchUpRequest = {
      schema: "trnm.nakama.get-archive.v1",
      logical_match_id: state.logical_match_id,
      after_sequence: 3,
      limit: 1,
      authorization_id: state.authorization_ids[0],
    };
    const catchUp = (await archiveRPC(players[0], catchUpRequest)).payload;
    assert(catchUp.schema === "trnm.nakama.archive.v1", "catch-up archive schema mismatch");
    assert(catchUp.logical_match_id === state.logical_match_id, "catch-up archive logical match mismatch");
    assert(catchUp.external_match_id === externalMatchID, "catch-up archive external match mismatch");
    assert(catchUp.runtime_generation === 2, "catch-up archive runtime generation mismatch");
    assert(catchUp.status === "active", "catch-up archive did not report an active match");
    assert(catchUp.match_version === 5 && catchUp.event_count === 4, "catch-up archive state cursor is stale");
    assert(catchUp.after_sequence === 3 && catchUp.next_after_sequence === 4 && catchUp.has_more === false, "catch-up archive cursor metadata mismatch");
    assert(Array.isArray(catchUp.events) && catchUp.events.length === 1, "catch-up archive did not return exactly one missed event");
    assert(equalJSON(catchUp.events[0], secondApplied.payload), "catch-up archive event differed from the authoritative sender event");
    validateEvent(catchUp.events[0], 4, state.logical_match_id, state.challenge_id);
    assert(Array.isArray(catchUp.roster) && catchUp.roster.length === 2, "catch-up archive omitted the roster snapshot");
    assert(Array.isArray(catchUp.participants) && catchUp.participants.length === 2, "catch-up archive omitted participant cursors");
    const catchUpParticipants = [...catchUp.participants].sort((left, right) => left.participant_slot - right.participant_slot);
    assert(catchUpParticipants[0].joined === true && catchUpParticipants[1].joined === true, "catch-up archive lost consumed admissions");
    assert(catchUpParticipants[0].last_command_sequence === 1 && catchUpParticipants[1].last_command_sequence === 1, "catch-up archive participant command cursors are stale");

    const completeRequest = {
      schema: "trnm.nakama.complete-match.v1",
      operator_token: operatorToken,
      logical_match_id: state.logical_match_id,
      facts: {
        // The literal word "error" is valid contract data and protects the
        // adapter from regressing to substring-based response classification.
        result_code: "error",
        winner_slot: 1,
        outcome_hash: digest(Buffer.from("blackbox-outcome-v1", "utf8")),
      },
    };
    const beforeCompletion = await client.listMatches(players[0].session, 100, true);
    assert(
      beforeCompletion.matches?.some((match) => match.match_id === externalMatchID),
      "active authoritative runtime was absent before completion",
    );
    const completionOne = await rpcHttpKey(client, runtimeHttpKey, "trnm_match_complete_v1", completeRequest);
    const completionAtOne = await players[0].inbox.take(
      (message) => message.opcode === 4 && message.payload?.match_id === state.logical_match_id,
      "completion broadcast at slot 1",
    );
    const completionAtTwo = await players[1].inbox.take(
      (message) => message.opcode === 4 && message.payload?.match_id === state.logical_match_id,
      "completion broadcast at slot 2",
    );
    assert(equalJSON(completionAtOne.payload, completionAtTwo.payload), "completion broadcast differed by recipient");
    assert(equalJSON(completionAtOne.payload, completionOne.payload?.completion), "completion broadcast differed from durable evidence");
    const completionTwo = await rpcHttpKey(client, runtimeHttpKey, "trnm_match_complete_v1", completeRequest);
    let conflictRejected = false;
    try {
      await rpcHttpKey(client, runtimeHttpKey, "trnm_match_complete_v1", {
        ...completeRequest,
        facts: {
          result_code: "blackbox-conflicting-completion",
          winner_slot: 2,
          outcome_hash: digest(Buffer.from("blackbox-conflicting-outcome", "utf8")),
        },
      });
    } catch (error) {
      conflictRejected = /completion facts differ|authoritative completion rejected/.test(String(error));
    }
    assert(conflictRejected, "conflicting completion retry was accepted");

    let runtimeTerminated = false;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const listed = await client.listMatches(players[0].session, 100, true);
      if (!listed.matches?.some((match) => match.match_id === externalMatchID)) {
        runtimeTerminated = true;
        break;
      }
      await delay(100);
    }
    assert(runtimeTerminated, "completed authoritative runtime remained active");

    const evidenceRequest = {
      schema: "trnm.nakama.get-evidence.v1",
      logical_match_id: state.logical_match_id,
      authorization_id: state.authorization_ids[0],
    };
    const rawEvidenceOne = await players[0].socket.rpc("trnm_match_evidence_v1", JSON.stringify(evidenceRequest));
    const rawEvidenceTwo = await players[0].socket.rpc("trnm_match_evidence_v1", JSON.stringify(evidenceRequest));
    assert(typeof rawEvidenceOne?.payload === "string", "realtime evidence RPC returned no raw payload");
    assert(rawEvidenceOne.payload === rawEvidenceTwo?.payload, "repeated evidence RPC payload bytes differed");
    const evidence = JSON.parse(rawEvidenceOne.payload);
    assert(completionOne?.payload?.schema === "trnm.nakama.evidence.v1", "completion evidence schema mismatch");
    assert(equalJSON(completionOne.payload, completionTwo.payload), "completion retry was not byte-identical JSON");
    assert(equalJSON(completionOne.payload, evidence), "evidence retrieval differed from completion response");
    const completedResume = await rpcHttpKey(client, runtimeHttpKey, "trnm_match_resume_v1", {
      schema: "trnm.nakama.resume-match.v1",
      operator_token: operatorToken,
      logical_match_id: state.logical_match_id,
    });
    assert(equalJSON(completedResume.payload, evidence), "completed resume did not return immutable evidence");
    assert(completionOne.payload.runtime_generation === 2, "completion evidence lost resumed runtime generation");
    const completion = completionOne.payload.completion;
    assert(completion.event_count === 5, "exact replay changed the persisted event count");
    assert(completion.match_id === state.logical_match_id, "completion logical match mismatch");
    assert(equalJSON(completion.terminal_facts, completeRequest.facts), "signed completion terminal facts mismatch");
    assert(completion.authority_key_id === required("TRNM_NAKAMA_AUTHORITY_KEY_ID"), "completion authority key ID mismatch");
    assert(
      completion.commitment_id === commitmentID(completion.match_id, completion.event_root, completion.archive_hash),
      "independent Node verifier rejected the commitment ID",
    );

    const fullArchiveRequest = {
      schema: "trnm.nakama.get-archive.v1",
      logical_match_id: state.logical_match_id,
      after_sequence: 0,
      limit: 128,
      authorization_id: state.authorization_ids[0],
    };
    const fullArchiveOne = await archiveRPC(players[0], fullArchiveRequest);
    const fullArchiveTwo = await archiveRPC(players[0], fullArchiveRequest);
    assert(fullArchiveOne.raw === fullArchiveTwo.raw, "repeated full archive RPC payload bytes differed");
    const archive = fullArchiveOne.payload;
    assert(archive.schema === "trnm.nakama.archive.v1", "full archive schema mismatch");
    assert(archive.logical_match_id === state.logical_match_id, "full archive logical match mismatch");
    assert(archive.external_match_id === externalMatchID, "full archive lost the completed runtime identity");
    assert(archive.runtime_generation === 2, "full archive runtime generation mismatch");
    assert(archive.status === "completed", "full archive did not report completed status");
    assert(archive.match_version === 6 && archive.event_count === 5, "full archive terminal state is inconsistent");
    assert(archive.after_sequence === 0 && archive.next_after_sequence === 5 && archive.has_more === false, "full archive cursor metadata mismatch");
    assert(Array.isArray(archive.events) && archive.events.length === completion.event_count, "full archive event count mismatch");
    assert(Array.isArray(archive.roster) && archive.roster.length === 2, "full archive roster is incomplete");
    assert(Array.isArray(archive.participants) && archive.participants.length === 2, "full archive participant cursors are incomplete");
    const archiveParticipants = [...archive.participants].sort((left, right) => left.participant_slot - right.participant_slot);
    assert(archiveParticipants.every((participant) => participant.joined === true), "full archive participant admission state is incomplete");
    assert(archiveParticipants.every((participant) => participant.last_command_sequence === 1), "full archive participant command cursors are incorrect");

    const reconstructed = verifyArchive(archive.events, archive.roster, state.logical_match_id);
    assert(reconstructed.event_root === completion.event_root, "independent archive event_root does not match signed completion");
    assert(reconstructed.roster_root === completion.roster_root, "independent archive roster_root does not match signed completion");
    assert(reconstructed.archive_hash === completion.archive_hash, "independent canonical archive_hash does not match signed completion");
    assert(
      canonicalBase64(archive.events.at(-1).payload, "terminal event payload").equals(terminalFactsBytes(completion.terminal_facts)),
      "terminal archive event does not encode the signed completion facts",
    );

    const reorderedEvents = JSON.parse(JSON.stringify(archive.events));
    [reorderedEvents[0], reorderedEvents[1]] = [reorderedEvents[1], reorderedEvents[0]];
    assertThrows(() => verifyArchive(reorderedEvents, archive.roster, state.logical_match_id), "reordered archive");
    const missingEvents = archive.events.filter((event) => event.sequence !== 3);
    assertThrows(() => verifyArchive(missingEvents, archive.roster, state.logical_match_id), "archive with a missing event");
    const tamperedEvents = JSON.parse(JSON.stringify(archive.events));
    tamperedEvents[2].payload = Buffer.from('{"move":"tampered-archive"}', "utf8").toString("base64");
    assertThrows(() => verifyArchive(tamperedEvents, archive.roster, state.logical_match_id), "tampered archive");

    const authorityRaw = completionOne.payload.authority_public_key_base64;
    assert(authorityRaw === required("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY"), "runtime exposed an unexpected authority public key");
    const authorityKey = publicKeyFromRaw(authorityRaw);
    const signature = Buffer.from(completion.signature, "base64");
    assert(signature.length === 64, "completion signature does not contain 64 bytes");
    assert(
      ed25519Verify(null, completionSigningBytes(completion), authorityKey, signature),
      "independent Node verifier rejected the authority signature",
    );
    const tamperedCompletion = { ...completion, event_count: completion.event_count + 1 };
    assert(
      !ed25519Verify(null, completionSigningBytes(tamperedCompletion), authorityKey, signature),
      "independent verifier accepted tampered completion evidence",
    );

    process.stdout.write(
      JSON.stringify({
        phase: "resume",
        logical_match_id: state.logical_match_id,
        old_external_match_id: state.old_external_match_id,
        external_match_id: externalMatchID,
        runtime_generation: 2,
        post_crash_replay_exact: true,
        event_count: completion.event_count,
        disconnect_cursor_catch_up_verified: true,
        archive_payload_byte_identical: true,
        live_event_hashes_reconstructed: true,
        live_event_root_reconstructed: true,
        live_roster_root_reconstructed: true,
        live_archive_hash_reconstructed: true,
        reordered_missing_tampered_archives_rejected: true,
        evidence_byte_identical: true,
        conflicting_completion_rejected: true,
        completed_runtime_terminated: true,
        broadcast_scopes_verified: true,
        authority_signature_verified: true,
        commitment_id: completion.commitment_id,
      }) + "\n",
    );
  } finally {
    await disconnectPlayers(players);
  }
}

const host = process.env.NAKAMA_HOST || "127.0.0.1";
const port = process.env.NAKAMA_PORT || "7350";
const serverKey = required("NAKAMA_SERVER_KEY");
const runtimeHttpKey = required("NAKAMA_RUNTIME_HTTP_KEY");
const client = new Client(serverKey, host, port, false);
const phase = process.env.BLACKBOX_PHASE || "health";
assertEnvironmentScope(phase);

switch (phase) {
  case "health":
    await runHealth(client, runtimeHttpKey);
    break;
  case "prepare":
    await runPrepare(client, runtimeHttpKey, required("TRNM_BLACKBOX_STATE_FILE"));
    break;
  case "resume":
    await runResume(client, runtimeHttpKey, required("TRNM_BLACKBOX_STATE_FILE"));
    break;
  default:
    throw new Error(`unsupported BLACKBOX_PHASE: ${phase}`);
}
