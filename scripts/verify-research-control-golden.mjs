import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as ed25519Sign,
  verify as ed25519Verify,
} from "node:crypto";
import { readFileSync } from "node:fs";

const fixture = JSON.parse(readFileSync(
  new URL("../contracts/research-control-golden-vectors.json", import.meta.url),
  "utf8",
));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest();
}

function digest(bytes) {
  return `sha256:${sha256(bytes).toString("hex")}`;
}

function digestBytes(value) {
  assert(/^sha256:[0-9a-f]{64}$/.test(value), `invalid digest ${value}`);
  return Buffer.from(value.slice("sha256:".length), "hex");
}

function canonicalBase64(value, label) {
  const decoded = Buffer.from(value, "base64");
  assert(decoded.toString("base64") === value, `${label} is not canonical padded base64`);
  return decoded;
}

class Frame {
  constructor(domain) {
    this.parts = [Buffer.from(domain, "utf8"), Buffer.from([0])];
  }

  bytes(value) {
    const raw = Buffer.from(value);
    assert(raw.length <= 0xffffffff, "canonical field is too long");
    const size = Buffer.alloc(4);
    size.writeUInt32BE(raw.length);
    this.parts.push(size, raw);
    return this;
  }

  string(value) {
    return this.bytes(Buffer.from(value, "utf8"));
  }

  u32(value) {
    const raw = Buffer.alloc(4);
    raw.writeUInt32BE(value);
    this.parts.push(raw);
    return this;
  }

  u64(value) {
    const raw = Buffer.alloc(8);
    raw.writeBigUInt64BE(BigInt(value));
    this.parts.push(raw);
    return this;
  }

  i64(value) {
    const raw = Buffer.alloc(8);
    raw.writeBigInt64BE(BigInt(value));
    this.parts.push(raw);
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

function privateKey(seedHex) {
  const seed = Buffer.from(seedHex, "hex");
  assert(seed.length === 32, "Ed25519 seed length differs");
  return createPrivateKey({
    key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), seed]),
    format: "der",
    type: "pkcs8",
  });
}

function publicKey(raw) {
  return createPublicKey({
    key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), raw]),
    format: "der",
    type: "spki",
  });
}

function rawPublic(key) {
  return createPublicKey(key).export({ type: "spki", format: "der" }).subarray(-32);
}

function authorizationClaimFrame(claim) {
  return new Frame("trnm_research_session_authorization_claim_v1")
    .string(claim.schema)
    .string(claim.authorization_id)
    .string(claim.session_id)
    .string(claim.team_id)
    .string(claim.paper_project_id)
    .string(claim.challenge_id)
    .string(claim.agent_id)
    .string(claim.agent_did)
    .string(claim.agent_key_id)
    .bytes(canonicalBase64(claim.agent_public_key, "agent public key"))
    .string(claim.subject_user_id)
    .u32(claim.participant_slot)
    .string(claim.role)
    .u64(claim.roster_version)
    .digest(claim.roster_root)
    .digest(claim.ruleset_hash)
    .digest(claim.challenge_snapshot_hash)
    .i64(claim.issued_at_unix)
    .i64(claim.expires_at_unix)
    .finish();
}

function authorizationSigningFrame(authorization) {
  return new Frame("trnm_research_session_authorization_signature_v1")
    .string(authorization.issuer_key_id)
    .bytes(authorizationClaimFrame(authorization.claim))
    .finish();
}

function authorizationEnvelopeFrame(authorization) {
  return new Frame("trnm_research_control_authorization_envelope_v2")
    .bytes(authorizationSigningFrame(authorization))
    .bytes(canonicalBase64(authorization.signature, "authorization signature"))
    .finish();
}

function authorizationSetBusinessFrame(domain, request) {
  const sessionID = request.logical_session_id ?? request.authorizations[0].claim.session_id;
  const rosterVersion = request.authorizations[0].claim.roster_version;
  const frame = new Frame(domain)
    .string(request.schema)
    .string(sessionID)
    .string(request.authorization_set_id)
    .u32(request.authorizations.length);
  for (const [index, authorization] of request.authorizations.entries()) {
    assert(authorization.claim.participant_slot === index + 1,
      "control authorization slots are not ordered and gapless");
    assert(authorization.claim.session_id === sessionID,
      "control authorization set mixes sessions");
    assert(authorization.claim.roster_version === rosterVersion,
      "control authorization set mixes roster epochs");
    frame.bytes(authorizationEnvelopeFrame(authorization));
  }
  return frame.finish();
}

function terminalFactsFrame(facts) {
  return new Frame("trnm_research_session_terminal_facts_v1")
    .string(facts.result_code)
    .digest(facts.paper_bundle_hash)
    .digest(facts.paper_release_candidate_hash)
    .digest(facts.contribution_ledger_hash)
    .finish();
}

function businessFrame(operation, request) {
  switch (operation) {
    case "create":
      return authorizationSetBusinessFrame("trnm_research_control_create_business_v2", request);
    case "resume":
      return new Frame("trnm_research_control_resume_business_v2")
        .string(request.schema)
        .string(request.logical_session_id)
        .string(request.authorization_set_id)
        .finish();
    case "replace_roster":
      return authorizationSetBusinessFrame("trnm_research_control_replace_business_v2", request);
    case "complete":
      return new Frame("trnm_research_control_complete_business_v2")
        .string(request.schema)
        .string(request.logical_session_id)
        .string(request.authorization_set_id)
        .bytes(terminalFactsFrame(request.facts))
        .finish();
    default:
      throw new Error(`unsupported operation ${operation}`);
  }
}

function controlClaimFrame(claim) {
  return new Frame("trnm_research_control_claim_v2")
    .string(claim.schema)
    .string(claim.command_id)
    .string(claim.operation)
    .string(claim.target_rpc)
    .string(claim.session_id)
    .u64(claim.session_roster_version)
    .string(claim.authorization_set_id)
    .digest(claim.payload_hash)
    .string(claim.audience)
    .i64(claim.issued_at_unix)
    .i64(claim.expires_at_unix)
    .string(claim.issuer_key_id)
    .finish();
}

function controlSigningFrame(claim) {
  return new Frame("trnm_research_control_signature_v2")
    .bytes(controlClaimFrame(claim))
    .finish();
}

assert(fixture.schema === "trnm.nakama.research_control.golden_vectors.v2", "fixture schema differs");
assert(fixture.vectors.length === 4, "fixture must publish exactly four operations");

const authorizationPrivate = privateKey(fixture.keys.authorization_issuer.seed_hex);
const authorizationPublic = publicKey(rawPublic(authorizationPrivate));
const controlPrivate = privateKey(fixture.keys.control_issuer.seed_hex);
const controlPublic = publicKey(rawPublic(controlPrivate));
assert(rawPublic(authorizationPrivate).toString("base64") === fixture.keys.authorization_issuer.public_key_base64,
  "authorization issuer seed/public key differ");
assert(rawPublic(controlPrivate).toString("base64") === fixture.keys.control_issuer.public_key_base64,
  "control issuer seed/public key differ");
assert(fixture.keys.authorization_issuer.public_key_base64 !== fixture.keys.control_issuer.public_key_base64,
  "authorization and control trust domains reuse one key");

const expectedTargets = new Map([
  ["create", "trnm_research_session_create_v2"],
  ["resume", "trnm_research_session_resume_v2"],
  ["replace_roster", "trnm_research_session_replace_roster_v2"],
  ["complete", "trnm_research_session_complete_v2"],
]);

for (const vector of fixture.vectors) {
  const request = vector.request;
  const control = request.control;
  const claim = control.claim;
  assert(vector.target_rpc === expectedTargets.get(vector.operation), `${vector.operation} target mapping differs`);
  assert(claim.operation === vector.operation && claim.target_rpc === vector.target_rpc,
    `${vector.operation} signed routing differs`);
  assert(claim.audience === "trnm:nakama:research-control:v2", `${vector.operation} audience differs`);
  assert(claim.expires_at_unix - claim.issued_at_unix === 120, `${vector.operation} validity lifetime differs`);

  if (request.authorizations) {
    assert(request.authorizations.length === 3, `${vector.operation} authorization count differs`);
    for (const authorization of request.authorizations) {
      const signing = authorizationSigningFrame(authorization);
      const signature = canonicalBase64(authorization.signature, "authorization signature");
      assert(ed25519Verify(null, signing, authorizationPublic, signature),
        `${vector.operation} authorization signature rejected`);
      assert(ed25519Sign(null, signing, authorizationPrivate).equals(signature),
        `${vector.operation} deterministic authorization signature differs`);
    }
  }

  const business = businessFrame(vector.operation, request);
  assert(business.toString("base64") === vector.business_frame_base64,
    `${vector.operation} published business frame differs`);
  assert(digest(business) === vector.payload_hash && claim.payload_hash === vector.payload_hash,
    `${vector.operation} business-frame payload hash differs`);

  const signing = controlSigningFrame(claim);
  assert(signing.toString("base64") === vector.control_signing_frame_base64,
    `${vector.operation} published control signing frame differs`);
  const signature = canonicalBase64(control.signature, "control signature");
  assert(ed25519Verify(null, signing, controlPublic, signature), `${vector.operation} control signature rejected`);
  assert(ed25519Sign(null, signing, controlPrivate).equals(signature),
    `${vector.operation} deterministic control signature differs`);

  const canonicalRequest = Buffer.from(JSON.stringify(request), "utf8");
  assert(canonicalRequest.toString("base64") === vector.canonical_request_body_base64,
    `${vector.operation} canonical request bytes differ`);
  const tampered = Buffer.from(signing);
  tampered[tampered.length - 1] ^= 1;
  assert(!ed25519Verify(null, tampered, controlPublic, signature),
    `${vector.operation} tampered control frame was accepted`);
}

const complete = fixture.vectors.find((vector) => vector.operation === "complete");
assert(complete.request.facts.result_code === "可复现✅", "Unicode terminal-facts vector is absent");
assert(Buffer.from(complete.business_frame_base64, "base64").includes(Buffer.from("可复现✅", "utf8")),
  "Unicode terminal result is not encoded as exact UTF-8 bytes");

const mixedEpoch = structuredClone(fixture.vectors.find((vector) => vector.operation === "create").request);
mixedEpoch.authorizations[1].claim.roster_version += 1;
let mixedEpochRejected = false;
try {
  businessFrame("create", mixedEpoch);
} catch {
  mixedEpochRejected = true;
}
assert(mixedEpochRejected, "independent control-frame encoder accepted a mixed roster epoch");

console.log("research-control v2 Go/Node golden: create/resume/replace/complete typed business frames, hashes, signatures PASS");
