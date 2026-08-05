import {
  createPrivateKey,
  createPublicKey,
  sign as ed25519Sign,
  verify as ed25519Verify,
} from "node:crypto";
import { readFileSync } from "node:fs";

const fixture = JSON.parse(readFileSync(new URL("../contracts/hepta-callback-golden-vectors.json", import.meta.url), "utf8"));
function assert(condition, message) { if (!condition) throw new Error(message); }
function digestBytes(value) {
  assert(/^sha256:[0-9a-f]{64}$/.test(value), `invalid digest ${value}`);
  return Buffer.from(value.slice(7), "hex");
}
class Frame {
  constructor(domain) { this.parts = [Buffer.from(domain, "utf8"), Buffer.from([0])]; }
  bytes(value) { const raw = Buffer.from(value); const size = Buffer.alloc(4); size.writeUInt32BE(raw.length); this.parts.push(size, raw); return this; }
  string(value) { return this.bytes(Buffer.from(value, "utf8")); }
  u32(value) { const raw = Buffer.alloc(4); raw.writeUInt32BE(value); this.parts.push(raw); return this; }
  u64(value) { const raw = Buffer.alloc(8); raw.writeBigUInt64BE(BigInt(value)); this.parts.push(raw); return this; }
  i64(value) { const raw = Buffer.alloc(8); raw.writeBigInt64BE(BigInt(value)); this.parts.push(raw); return this; }
  digest(value) { this.parts.push(digestBytes(value)); return this; }
  finish() { return Buffer.concat(this.parts); }
}
function privateKey(seedHex) {
  const seed = Buffer.from(seedHex, "hex"); assert(seed.length === 32, "issuer seed length differs");
  return createPrivateKey({ key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), seed]), format: "der", type: "pkcs8" });
}
function rawPublic(key) { return createPublicKey(key).export({ type: "spki", format: "der" }).subarray(-32); }
function consumptionSigning(value) {
  const frame = new Frame("hepta_research_session_authorization_set_consumption_receipt_v1")
    .string(value.schema).string(value.session_id).string(value.team_id).string(value.paper_project_id)
    .string(value.challenge_id).u64(value.session_roster_version).digest(value.roster_root)
    .u32(value.authorization_ids.length);
  for (const authorizationID of value.authorization_ids) frame.string(authorizationID);
  return frame.i64(value.consumed_at_unix).string(value.issuer_key_id).finish();
}
function terminalFrame(value) {
  return new Frame("trnm_research_session_terminal_facts_v1").string(value.result_code)
    .digest(value.paper_bundle_hash).digest(value.paper_release_candidate_hash)
    .digest(value.contribution_ledger_hash).finish();
}
function completionSigning(value) {
  return new Frame("hepta_nakama_research_session_completion_receipt_v1")
    .string(value.schema).digest(value.commitment_id).string(value.session_id).string(value.team_id)
    .string(value.paper_project_id).string(value.challenge_id).u64(value.roster_version)
    .digest(value.roster_root).u64(value.event_count).digest(value.event_root).digest(value.archive_hash)
    .digest(value.ruleset_hash).digest(value.challenge_snapshot_hash).string(value.nakama_authority_key_id)
    .bytes(terminalFrame(value.terminal_facts)).i64(value.verified_at_unix).string(value.issuer_key_id).finish();
}

assert(fixture.schema === "trnm.nakama.hepta_callback.golden_vectors.v1", "callback fixture schema differs");
assert(fixture.source_fixture.schema === "hepta.paper_raid.golden_vectors.v2", "source fixture schema differs");
assert(fixture.source_fixture.sha256 === "309584cc21a7169473a7bd37b93528edce4a3b248b313238cd81f6a7c3cad19d", "source fixture SHA differs");
const issuerPrivate = privateKey(fixture.issuer.seed_hex);
const issuerPublic = createPublicKey(issuerPrivate);
assert(rawPublic(issuerPrivate).toString("base64") === fixture.issuer.public_key_base64, "issuer seed/public key differ");

for (const [label, section, encode] of [
  ["authorization consumption", fixture.authorization_consumption_receipt, consumptionSigning],
  ["completion", fixture.nakama_completion_receipt, completionSigning],
]) {
  const message = encode(section.value);
  assert(message.toString("hex") === section.signing_frame_hex, `${label} frame differs from Hepta`);
  const signature = Buffer.from(section.value.signature, "base64");
  assert(signature.length === 64 && signature.toString("base64") === section.value.signature, `${label} signature encoding differs`);
  assert(ed25519Verify(null, message, issuerPublic, signature), `${label} signature rejected`);
  assert(ed25519Sign(null, message, issuerPrivate).equals(signature), `${label} deterministic signature differs`);
  const tampered = Buffer.from(message); tampered[tampered.length - 1] ^= 1;
  assert(!ed25519Verify(null, tampered, issuerPublic, signature), `${label} tampered frame accepted`);
}

console.log(`Hepta signed ACK cross-language vectors: source=${fixture.source_fixture.sha256} consume/completion PASS`);
