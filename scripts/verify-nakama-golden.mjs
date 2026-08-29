#!/usr/bin/env node

import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign,
  verify,
} from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const fixturePath = process.argv[2] ?? join(root, "contracts", "golden-vectors.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));

function fail(message) {
  throw new Error(`golden fixture: ${message}`);
}

function assert(condition, message) {
  if (!condition) fail(message);
}

function exactKeys(value, expected, location) {
  assert(value && typeof value === "object" && !Array.isArray(value), `${location} must be an object`);
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  assert(JSON.stringify(actual) === JSON.stringify(wanted), `${location} keys: got ${actual}, want ${wanted}`);
}

function nonEmpty(value, location) {
  assert(typeof value === "string" && value.length > 0, `${location} must be a non-empty string`);
  return value;
}

function safeInteger(value, location, minimum = 0) {
  assert(Number.isSafeInteger(value) && value >= minimum, `${location} must be a safe integer >= ${minimum}`);
  return value;
}

function canonicalHex(value, bytes, location) {
  assert(typeof value === "string" && new RegExp(`^[0-9a-f]{${bytes * 2}}$`).test(value), `${location} is not canonical hex`);
  return Buffer.from(value, "hex");
}

function canonicalBase64(value, bytes, location) {
  nonEmpty(value, location);
  assert(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value), `${location} is not padded base64`);
  const decoded = Buffer.from(value, "base64");
  assert(decoded.length === bytes && decoded.toString("base64") === value, `${location} decoded length or canonical encoding is invalid`);
  return decoded;
}

function payloadBase64(value, location) {
  nonEmpty(value, location);
  assert(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value), `${location} is not padded base64`);
  const decoded = Buffer.from(value, "base64");
  assert(decoded.length >= 1 && decoded.length <= 65_536 && decoded.toString("base64") === value, `${location} decoded size or canonical encoding is invalid`);
  return decoded;
}

const concat = (...parts) => Buffer.concat(parts);
const domain = (value) => concat(Buffer.from(value, "ascii"), Buffer.from([0]));
const u32 = (value) => {
  safeInteger(value, "u32");
  assert(value <= 0xffff_ffff, "u32 overflow");
  const encoded = Buffer.alloc(4);
  encoded.writeUInt32BE(value);
  return encoded;
};
const u64 = (value) => {
  safeInteger(value, "u64");
  const encoded = Buffer.alloc(8);
  encoded.writeBigUInt64BE(BigInt(value));
  return encoded;
};
const i64 = (value) => {
  assert(Number.isSafeInteger(value), "i64 must be a safe integer");
  const encoded = Buffer.alloc(8);
  encoded.writeBigInt64BE(BigInt(value));
  return encoded;
};
const bytes = (value) => concat(u32(value.length), value);
const text = (value) => bytes(Buffer.from(nonEmpty(value, "canonical text"), "utf8"));
const sha256Raw = (value) => createHash("sha256").update(value).digest();
const sha256 = (value) => `sha256:${sha256Raw(value).toString("hex")}`;
const digestRaw = (value, location = "digest") => {
  assert(typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value), `${location} is not a canonical SHA-256 digest`);
  return Buffer.from(value.slice(7), "hex");
};

function assertHex(expected, actual, location) {
  assert(typeof expected === "string" && /^[0-9a-f]+$/.test(expected) && expected.length % 2 === 0, `${location} fixture hex is invalid`);
  assert(actual.toString("hex") === expected, `${location} mismatch`);
}

function keyFromSeed(record, location) {
  exactKeys(record, ["seed_hex", "public_key_base64"], location);
  const seed = canonicalHex(record.seed_hex, 32, `${location}.seed_hex`);
  const pkcs8 = concat(Buffer.from("302e020100300506032b657004220420", "hex"), seed);
  const privateKey = createPrivateKey({ key: pkcs8, format: "der", type: "pkcs8" });
  const publicKey = createPublicKey(privateKey);
  const spki = publicKey.export({ format: "der", type: "spki" });
  const publicRaw = spki.subarray(spki.length - 32);
  const expectedRaw = canonicalBase64(record.public_key_base64, 32, `${location}.public_key_base64`);
  assert(publicRaw.equals(expectedRaw), `${location} public key does not derive from seed`);
  return { privateKey, publicKey, publicRaw };
}

function verifyEventMerkle(record, location) {
  exactKeys(record, ["event_hashes", "leaf_hashes", "event_root"], location);
  assert(Array.isArray(record.event_hashes) && record.event_hashes.length > 0, `${location}.event_hashes must be non-empty`);
  assert(Array.isArray(record.leaf_hashes) && record.leaf_hashes.length === record.event_hashes.length, `${location}.leaf_hashes length mismatch`);
  let level = record.event_hashes.map((eventHash, index) =>
    sha256Raw(concat(domain("trnm_match_event_leaf_v1"), u64(index + 1), digestRaw(eventHash))),
  );
  assert(
    JSON.stringify(level.map((value) => `sha256:${value.toString("hex")}`)) === JSON.stringify(record.leaf_hashes),
    `${location} leaves mismatch`,
  );
  while (level.length > 1) {
    const next = [];
    for (let index = 0; index < level.length; index += 2) {
      next.push(sha256Raw(concat(domain("trnm_binary_merkle_node_v1"), level[index], level[index + 1] ?? level[index])));
    }
    level = next;
  }
  assert(`sha256:${level[0].toString("hex")}` === record.event_root, `${location} root mismatch`);
}

exactKeys(fixture, [
  "schema", "fixture_notice", "keys", "source_digests", "authorizations",
  "command", "sealed_events", "event_merkle", "odd_event_merkle", "roster", "archive",
  "terminal_facts", "commitment", "completion",
], "root");
assert(fixture.schema === "trnm.nakama.golden_vectors.v1", "schema is unsupported");
assert(/TEST ONLY/.test(fixture.fixture_notice) && /MUST NEVER.*production/i.test(fixture.fixture_notice), "fixture-only private key warning is missing");

exactKeys(fixture.keys, ["issuer", "agent_1", "agent_2", "authority"], "keys");
const keys = Object.fromEntries(Object.entries(fixture.keys).map(([name, record]) => [name, keyFromSeed(record, `keys.${name}`)]));
assert(new Set(Object.values(fixture.keys).map((record) => record.seed_hex)).size === 4, "fixture seeds must be unique");

exactKeys(fixture.source_digests, ["ruleset", "dataset", "challenge_snapshot", "outcome"], "source_digests");
for (const [name, record] of Object.entries(fixture.source_digests)) {
  exactKeys(record, ["utf8", "digest"], `source_digests.${name}`);
  assert(sha256(Buffer.from(nonEmpty(record.utf8, `source_digests.${name}.utf8`), "utf8")) === record.digest, `source_digests.${name}.digest mismatch`);
}

assert(Array.isArray(fixture.authorizations) && fixture.authorizations.length === 2, "authorizations must contain slots 1 and 2");
const claims = [];
for (const [index, authorization] of fixture.authorizations.entries()) {
  const location = `authorizations.${index}`;
  exactKeys(authorization, ["value", "claim_frame_hex", "signing_frame_hex"], location);
  exactKeys(authorization.value, ["claim", "issuer_key_id", "signature"], `${location}.value`);
  const claim = authorization.value.claim;
  exactKeys(claim, [
    "schema", "authorization_id", "match_id", "challenge_id", "agent_id",
    "agent_did", "agent_key_id", "agent_public_key", "subject_user_id",
    "participant_slot", "role", "ruleset_hash", "dataset_hash",
    "challenge_snapshot_hash", "issued_at_unix", "expires_at_unix",
  ], `${location}.value.claim`);
  const slot = index + 1;
  const agentKey = keys[`agent_${slot}`];
  assert(claim.schema === "trnm.match.authorization.v1", `${location} schema mismatch`);
  assert(claim.participant_slot === slot, `${location} slot/order mismatch`);
  assert(canonicalBase64(claim.agent_public_key, 32, `${location}.agent_public_key`).equals(agentKey.publicRaw), `${location} agent key mismatch`);
  assert(claim.ruleset_hash === fixture.source_digests.ruleset.digest, `${location} ruleset digest mismatch`);
  assert(claim.dataset_hash === fixture.source_digests.dataset.digest, `${location} dataset digest mismatch`);
  assert(claim.challenge_snapshot_hash === fixture.source_digests.challenge_snapshot.digest, `${location} challenge digest mismatch`);
  safeInteger(claim.issued_at_unix, `${location}.issued_at_unix`);
  safeInteger(claim.expires_at_unix, `${location}.expires_at_unix`, claim.issued_at_unix + 1);
  const claimFrame = concat(
    domain("trnm_match_authorization_claim_v1"), text(claim.schema), text(claim.authorization_id),
    text(claim.match_id), text(claim.challenge_id), text(claim.agent_id), text(claim.agent_did),
    text(claim.agent_key_id), bytes(agentKey.publicRaw), text(claim.subject_user_id),
    u32(claim.participant_slot), text(claim.role), digestRaw(claim.ruleset_hash),
    digestRaw(claim.dataset_hash), digestRaw(claim.challenge_snapshot_hash),
    i64(claim.issued_at_unix), i64(claim.expires_at_unix),
  );
  assertHex(authorization.claim_frame_hex, claimFrame, `${location}.claim_frame_hex`);
  const signingFrame = concat(
    domain("trnm_match_authorization_signature_v1"),
    text(authorization.value.issuer_key_id), bytes(claimFrame),
  );
  assertHex(authorization.signing_frame_hex, signingFrame, `${location}.signing_frame_hex`);
  const signature = canonicalBase64(authorization.value.signature, 64, `${location}.signature`);
  assert(verify(null, signingFrame, keys.issuer.publicKey, signature), `${location} signature verification failed`);
  assert(sign(null, signingFrame, keys.issuer.privateKey).equals(signature), `${location} signature is not the fixed seed result`);
  claims.push(claim);
}
const [claim, claimTwo] = claims;
for (const field of ["match_id", "challenge_id", "ruleset_hash", "dataset_hash", "challenge_snapshot_hash"]) {
  assert(claim[field] === claimTwo[field], `authorization snapshots disagree on ${field}`);
}
for (const field of ["authorization_id", "subject_user_id", "agent_id", "agent_did", "agent_key_id", "agent_public_key"]) {
  assert(claim[field] !== claimTwo[field], `authorization identities collide on ${field}`);
}

exactKeys(fixture.command, ["value", "signing_frame_hex", "fingerprint_frame_hex", "fingerprint"], "command");
const command = fixture.command.value;
exactKeys(command, [
  "schema", "command_id", "authorization_id", "match_id", "challenge_id",
  "agent_id", "participant_slot", "participant_sequence", "expected_match_version",
  "issued_at_unix", "payload_type", "payload", "payload_hash", "agent_key_id", "signature",
], "command.value");
assert(command.schema === "trnm.match.command.v1", "command schema mismatch");
for (const field of ["authorization_id", "match_id", "challenge_id", "agent_id", "agent_key_id", "participant_slot"]) {
  const claimField = field === "participant_slot" ? "participant_slot" : field;
  assert(command[field] === claim[claimField], `command ${field} is not bound to authorization`);
}
const commandPayload = payloadBase64(command.payload, "command.payload");
assert(sha256(commandPayload) === command.payload_hash, "command payload_hash mismatch");
const commandSigning = concat(
  domain("trnm_match_command_signature_v1"), text(command.schema), text(command.command_id),
  text(command.authorization_id), text(command.match_id), text(command.challenge_id), text(command.agent_id),
  u32(command.participant_slot), u64(command.participant_sequence), u64(command.expected_match_version),
  i64(command.issued_at_unix), text(command.payload_type), bytes(commandPayload),
  digestRaw(command.payload_hash), text(command.agent_key_id),
);
assertHex(fixture.command.signing_frame_hex, commandSigning, "command.signing_frame_hex");
const commandSignature = canonicalBase64(command.signature, 64, "command.signature");
assert(verify(null, commandSigning, keys.agent_1.publicKey, commandSignature), "command signature verification failed");
assert(sign(null, commandSigning, keys.agent_1.privateKey).equals(commandSignature), "command signature is not the fixed seed result");
const fingerprintFrame = concat(domain("trnm_match_command_fingerprint_v1"), bytes(commandSigning), bytes(commandSignature));
assertHex(fixture.command.fingerprint_frame_hex, fingerprintFrame, "command.fingerprint_frame_hex");
assert(sha256(fingerprintFrame) === fixture.command.fingerprint, "command fingerprint mismatch");

exactKeys(fixture.terminal_facts, ["value", "canonical_frame_hex"], "terminal_facts");
const terminal = fixture.terminal_facts.value;
exactKeys(terminal, ["result_code", "winner_slot", "outcome_hash"], "terminal_facts.value");
assert([0, 1, 2].includes(terminal.winner_slot), "terminal winner_slot is invalid");
assert(terminal.outcome_hash === fixture.source_digests.outcome.digest, "terminal outcome digest mismatch");
const terminalFrame = concat(
  domain("trnm_match_terminal_facts_v1"), text(terminal.result_code),
  u32(terminal.winner_slot), digestRaw(terminal.outcome_hash),
);
assertHex(fixture.terminal_facts.canonical_frame_hex, terminalFrame, "terminal_facts.canonical_frame_hex");

assert(Array.isArray(fixture.sealed_events) && fixture.sealed_events.length === 4, "sealed_events must contain two joins, one command, and one terminal event");
const expectedEventTypes = ["participant_joined", "participant_joined", "agent_command_applied", "match_completed"];
const eventFrames = [];
const eventHashes = [];
let previousEventTime = -1;
for (const [index, record] of fixture.sealed_events.entries()) {
  exactKeys(record, ["value", "facts_frame_hex"], `sealed_events.${index}`);
  const event = record.value;
  exactKeys(event, [
    "schema", "event_id", "event_type", "match_id", "challenge_id", "sequence",
    "causation_id", "occurred_at_unix", "participant_slot", "match_version",
    "payload_type", "payload", "payload_hash", "event_hash",
  ], `sealed_events.${index}.value`);
  assert(event.schema === "trnm.match.event.v1", `event ${index} schema mismatch`);
  assert(event.match_id === claim.match_id && event.challenge_id === claim.challenge_id, `event ${index} match binding mismatch`);
  assert(event.sequence === index + 1, `event ${index} sequence is not gapless`);
  assert(event.match_version === event.sequence + 1, `event ${index} match_version is not sequence + 1`);
  assert(event.event_type === expectedEventTypes[index], `event ${index} occurs outside the reachable state-machine order`);
  assert(event.occurred_at_unix >= previousEventTime, `event ${index} time moves backwards`);
  previousEventTime = event.occurred_at_unix;
  const expectedEventID = sha256(concat(
    domain("trnm_match_event_id_v1"), text(event.match_id),
    text(event.causation_id), u64(event.sequence),
  ));
  assert(event.event_id === expectedEventID, `event ${index} event_id is not canonically derived`);
  const eventPayload = payloadBase64(event.payload, `sealed_events.${index}.payload`);
  assert(sha256(eventPayload) === event.payload_hash, `event ${index} payload hash mismatch`);
  const facts = concat(
    domain("trnm_match_event_v1"), text(event.schema), text(event.event_id), text(event.event_type),
    text(event.match_id), text(event.challenge_id), u64(event.sequence), text(event.causation_id),
    i64(event.occurred_at_unix), u32(event.participant_slot), u64(event.match_version),
    text(event.payload_type), bytes(eventPayload), digestRaw(event.payload_hash),
  );
  assertHex(record.facts_frame_hex, facts, `sealed_events.${index}.facts_frame_hex`);
  assert(sha256(facts) === event.event_hash, `event ${index} hash mismatch`);
  eventFrames.push(facts);
  eventHashes.push(digestRaw(event.event_hash));
}
for (let index = 0; index < 2; index += 1) {
  const event = fixture.sealed_events[index].value;
  const admitted = claims[index];
  const expectedJoinPayload = concat(
    domain("trnm_participant_joined_v1"), u32(admitted.participant_slot),
    text(admitted.subject_user_id), text(admitted.authorization_id), text(admitted.agent_id),
  );
  assert(event.causation_id === admitted.authorization_id, `join event ${index} authorization causation mismatch`);
  assert(event.participant_slot === admitted.participant_slot, `join event ${index} slot mismatch`);
  assert(event.payload_type === "trnm.participant.joined.v1", `join event ${index} payload type mismatch`);
  assert(payloadBase64(event.payload, `join event ${index} payload`).equals(expectedJoinPayload), `join event ${index} canonical payload mismatch`);
  assert(event.occurred_at_unix >= admitted.issued_at_unix && event.occurred_at_unix < admitted.expires_at_unix, `join event ${index} is outside authorization validity`);
}
const commandEvent = fixture.sealed_events[2].value;
assert(commandEvent.causation_id === command.command_id, "command event causation mismatch");
assert(commandEvent.participant_slot === command.participant_slot, "command event slot mismatch");
assert(commandEvent.payload_type === command.payload_type, "command event payload type mismatch");
assert(payloadBase64(commandEvent.payload, "command event payload").equals(commandPayload), "command event payload mismatch");
assert(command.expected_match_version === commandEvent.sequence, "command expected_match_version does not target the pre-event version");
assert(command.issued_at_unix <= commandEvent.occurred_at_unix, "command event predates its signed command");
const terminalEvent = fixture.sealed_events.at(-1).value;
assert(terminalEvent.event_type === "match_completed" && terminalEvent.participant_slot === 0, "last event is not authoritative completion");
assert(payloadBase64(terminalEvent.payload, "terminal event payload").equals(terminalFrame), "terminal event does not embed canonical terminal facts");
assert(terminalEvent.causation_id === sha256(concat(Buffer.from("authority:complete:", "utf8"), terminalFrame)), "terminal event causation mismatch");

assert(JSON.stringify(fixture.event_merkle.event_hashes) === JSON.stringify(fixture.sealed_events.map((record) => record.value.event_hash)), "event_merkle hashes do not match sealed events");
verifyEventMerkle(fixture.event_merkle, "event_merkle");
assert(fixture.odd_event_merkle.event_hashes.length === 3, "odd_event_merkle must exercise three leaves");
assert(
  JSON.stringify(fixture.odd_event_merkle.event_hashes) === JSON.stringify(fixture.event_merkle.event_hashes.slice(0, 3)),
  "odd_event_merkle must reuse the first three independently sealed events",
);
verifyEventMerkle(fixture.odd_event_merkle, "odd_event_merkle");

exactKeys(fixture.roster, ["entries", "canonical_frame_hex", "roster_root"], "roster");
assert(Array.isArray(fixture.roster.entries) && fixture.roster.entries.length === 2, "roster must contain two entries");
const rosterEntries = [...fixture.roster.entries].sort((left, right) => left.participant_slot - right.participant_slot);
assert(rosterEntries[0].participant_slot === 1 && rosterEntries[1].participant_slot === 2, "roster slots must be 1 and 2");
const uniqueRosterFields = ["subject_user_id", "agent_id", "agent_did", "agent_key_id", "agent_key_hash"];
for (const field of uniqueRosterFields) assert(new Set(rosterEntries.map((entry) => entry[field])).size === 2, `roster ${field} is not unique`);
for (const [index, entry] of rosterEntries.entries()) {
  exactKeys(entry, ["participant_slot", "subject_user_id", "agent_id", "agent_did", "agent_key_id", "agent_key_hash", "role"], `roster.entries.${index}`);
  const key = index === 0 ? keys.agent_1 : keys.agent_2;
  const admitted = claims[index];
  assert(entry.agent_key_hash === sha256(key.publicRaw), `roster slot ${index + 1} key hash mismatch`);
  for (const field of ["participant_slot", "subject_user_id", "agent_id", "agent_did", "agent_key_id", "role"]) {
    assert(entry[field] === admitted[field], `roster slot ${index + 1} ${field} is not admission-derived`);
  }
}
const rosterParts = [domain("trnm_match_roster_v1"), u32(rosterEntries.length)];
for (const entry of rosterEntries) rosterParts.push(
  u32(entry.participant_slot), text(entry.subject_user_id), text(entry.agent_id),
  text(entry.agent_did), text(entry.agent_key_id), digestRaw(entry.agent_key_hash), text(entry.role),
);
const rosterFrame = concat(...rosterParts);
assertHex(fixture.roster.canonical_frame_hex, rosterFrame, "roster.canonical_frame_hex");
assert(sha256(rosterFrame) === fixture.roster.roster_root, "roster root mismatch");

exactKeys(fixture.archive, ["canonical_frame_hex", "archive_hash"], "archive");
const archiveParts = [domain("trnm_match_event_archive_v1"), u64(eventFrames.length)];
for (let index = 0; index < eventFrames.length; index += 1) archiveParts.push(bytes(eventFrames[index]), bytes(eventHashes[index]));
const archiveFrame = concat(...archiveParts);
assertHex(fixture.archive.canonical_frame_hex, archiveFrame, "archive.canonical_frame_hex");
assert(sha256(archiveFrame) === fixture.archive.archive_hash, "archive hash mismatch");

exactKeys(fixture.commitment, ["canonical_frame_hex", "commitment_id"], "commitment");
const commitmentFrame = concat(
  domain("trnm_match_commitment_id_v1"), text(claim.match_id),
  digestRaw(fixture.event_merkle.event_root), digestRaw(fixture.archive.archive_hash),
);
assertHex(fixture.commitment.canonical_frame_hex, commitmentFrame, "commitment.canonical_frame_hex");
assert(sha256(commitmentFrame) === fixture.commitment.commitment_id, "commitment ID mismatch");

exactKeys(fixture.completion, ["value", "signing_frame_hex"], "completion");
const completion = fixture.completion.value;
exactKeys(completion, [
  "schema", "commitment_id", "match_id", "challenge_id", "terminal_facts",
  "event_count", "event_root", "roster_root", "ruleset_hash", "dataset_hash",
  "challenge_snapshot_hash", "archive_hash", "completed_at_unix",
  "authority_key_id", "signature",
], "completion.value");
assert(completion.schema === "trnm.match.completed.v1", "completion schema mismatch");
assert(completion.commitment_id === fixture.commitment.commitment_id, "completion commitment mismatch");
assert(completion.match_id === claim.match_id && completion.challenge_id === claim.challenge_id, "completion identity mismatch");
assert(JSON.stringify(completion.terminal_facts) === JSON.stringify(terminal), "completion terminal_facts mismatch");
assert(completion.event_count === eventFrames.length, "completion event_count mismatch");
assert(completion.event_count === 4, "completion does not cover the full reachable four-event archive");
assert(completion.event_root === fixture.event_merkle.event_root, "completion event_root mismatch");
assert(completion.roster_root === fixture.roster.roster_root, "completion roster_root mismatch");
assert(completion.ruleset_hash === claim.ruleset_hash && completion.dataset_hash === claim.dataset_hash && completion.challenge_snapshot_hash === claim.challenge_snapshot_hash, "completion snapshot digest mismatch");
assert(completion.archive_hash === fixture.archive.archive_hash, "completion archive_hash mismatch");
const completionSigning = concat(
  domain("trnm_match_completed_signature_v1"), text(completion.schema),
  digestRaw(completion.commitment_id), text(completion.match_id), text(completion.challenge_id),
  bytes(terminalFrame), u64(completion.event_count), digestRaw(completion.event_root),
  digestRaw(completion.roster_root), digestRaw(completion.ruleset_hash),
  digestRaw(completion.dataset_hash), digestRaw(completion.challenge_snapshot_hash),
  digestRaw(completion.archive_hash), i64(completion.completed_at_unix), text(completion.authority_key_id),
);
assertHex(fixture.completion.signing_frame_hex, completionSigning, "completion.signing_frame_hex");
const completionSignature = canonicalBase64(completion.signature, 64, "completion.signature");
assert(verify(null, completionSigning, keys.authority.publicKey, completionSignature), "completion signature verification failed");
assert(sign(null, completionSigning, keys.authority.privateKey).equals(completionSignature), "completion signature is not the fixed seed result");

console.log("cross-language authorization/command/event/evidence golden fixture: ok");
