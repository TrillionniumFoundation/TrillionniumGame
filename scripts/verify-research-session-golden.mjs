import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as ed25519Sign,
  verify as ed25519Verify,
} from "node:crypto";
import { readFileSync } from "node:fs";

const fixture = JSON.parse(readFileSync(new URL("../contracts/research-session-golden-vectors.json", import.meta.url), "utf8"));

function assert(condition, message) { if (!condition) throw new Error(message); }
function sha256(bytes) { return createHash("sha256").update(bytes).digest(); }
function digest(bytes) { return `sha256:${sha256(bytes).toString("hex")}`; }
function digestBytes(value) {
  assert(/^sha256:[0-9a-f]{64}$/.test(value), `invalid digest ${value}`);
  return Buffer.from(value.slice(7), "hex");
}
function canonicalBase64(value, label) {
  const decoded = Buffer.from(value, "base64");
  assert(decoded.toString("base64") === value, `${label} is not canonical padded base64`);
  return decoded;
}

class Frame {
  constructor(domain) { this.parts = [Buffer.from(domain, "utf8"), Buffer.from([0])]; }
  bytes(value) { const raw=Buffer.from(value); assert(raw.length<=0xffffffff,"field too long"); const size=Buffer.alloc(4);size.writeUInt32BE(raw.length);this.parts.push(size,raw);return this; }
  string(value) { return this.bytes(Buffer.from(value,"utf8")); }
  u32(value) { const raw=Buffer.alloc(4);raw.writeUInt32BE(value);this.parts.push(raw);return this; }
  u64(value) { const raw=Buffer.alloc(8);raw.writeBigUInt64BE(BigInt(value));this.parts.push(raw);return this; }
  i64(value) { const raw=Buffer.alloc(8);raw.writeBigInt64BE(BigInt(value));this.parts.push(raw);return this; }
  digest(value) { this.parts.push(digestBytes(value));return this; }
  finish() { return Buffer.concat(this.parts); }
}

function privateKey(seedHex) {
  const seed=Buffer.from(seedHex,"hex");assert(seed.length===32,"seed length");
  return createPrivateKey({key:Buffer.concat([Buffer.from("302e020100300506032b657004220420","hex"),seed]),format:"der",type:"pkcs8"});
}
function rawPublic(key) { return createPublicKey(key).export({type:"spki",format:"der"}).subarray(-32); }
function publicKey(raw) { return createPublicKey({key:Buffer.concat([Buffer.from("302a300506032b6570032100","hex"),raw]),format:"der",type:"spki"}); }

function claimFrame(c) {
  return new Frame("trnm_research_session_authorization_claim_v1")
    .string(c.schema).string(c.authorization_id).string(c.session_id).string(c.team_id)
    .string(c.paper_project_id).string(c.challenge_id).string(c.agent_id).string(c.agent_did)
    .string(c.agent_key_id).bytes(canonicalBase64(c.agent_public_key,"agent key"))
    .string(c.subject_user_id).u32(c.participant_slot).string(c.role).u64(c.roster_version)
    .digest(c.roster_root).digest(c.ruleset_hash).digest(c.challenge_snapshot_hash)
    .i64(c.issued_at_unix).i64(c.expires_at_unix).finish();
}
function authorizationSigning(value) {
  return new Frame("trnm_research_session_authorization_signature_v1")
    .string(value.issuer_key_id).bytes(claimFrame(value.claim)).finish();
}
function rosterFrame(roster, firstClaim) {
  const frame=new Frame("trnm_research_session_roster_v1").string(firstClaim.session_id)
    .string(firstClaim.team_id).string(firstClaim.paper_project_id).u64(roster.version).u32(roster.entries.length);
  for (const entry of roster.entries) frame.u32(entry.participant_slot).string(entry.authorization_id)
    .string(entry.subject_user_id).string(entry.agent_id).string(entry.agent_did).string(entry.agent_key_id)
    .digest(entry.agent_key_hash).string(entry.role);
  return frame.finish();
}
function actionSigning(a) {
  return new Frame("trnm_research_session_action_signature_v1")
    .string(a.schema).string(a.action_id).string(a.authorization_id).string(a.session_id)
    .string(a.team_id).string(a.paper_project_id).string(a.challenge_id).u64(a.roster_version)
    .u32(a.participant_slot).u64(a.participant_sequence).u64(a.expected_session_version)
    .i64(a.issued_at_unix).string(a.action_type).string(a.payload_type)
    .bytes(canonicalBase64(a.payload,"action payload")).digest(a.payload_hash).digest(a.reference_hash)
    .string(a.agent_key_id).finish();
}
function actionFingerprint(a) {
  return digest(new Frame("trnm_research_session_action_fingerprint_v1").bytes(actionSigning(a)).bytes(canonicalBase64(a.signature,"action signature")).finish());
}
function eventID(e) { return digest(new Frame("trnm_research_session_event_id_v1").string(e.session_id).string(e.causation_id).u64(e.sequence).finish()); }
function eventFacts(e) {
  return new Frame("trnm_research_session_event_v1").string(e.schema).string(e.event_id).string(e.event_type)
    .string(e.session_id).string(e.team_id).string(e.paper_project_id).string(e.challenge_id)
    .u64(e.roster_version).u64(e.sequence).string(e.causation_id).i64(e.occurred_at_unix)
    .u32(e.participant_slot).u64(e.session_version).string(e.action_type).string(e.payload_type)
    .bytes(canonicalBase64(e.payload,"event payload")).digest(e.payload_hash).digest(e.reference_hash).finish();
}
function eventRoot(events) {
  let level=events.map((event,index)=>{
    assert(event.sequence===index+1,"event gap");
    const seq=Buffer.alloc(8);seq.writeBigUInt64BE(BigInt(event.sequence));
    return sha256(Buffer.concat([Buffer.from("trnm_research_session_event_leaf_v1\0"),seq,digestBytes(event.event_hash)]));
  });
  while(level.length>1){const next=[];for(let i=0;i<level.length;i+=2){next.push(sha256(Buffer.concat([Buffer.from("trnm_research_session_merkle_node_v1\0"),level[i],level[i+1]??level[i]])));}level=next;}
  return `sha256:${level[0].toString("hex")}`;
}
function archiveFrame(events) {
  const frame=new Frame("trnm_research_session_event_archive_v1").u64(events.length);
  for(const event of events) frame.bytes(eventFacts(event)).bytes(digestBytes(event.event_hash));
  return frame.finish();
}
function terminalFrame(facts) {
  return new Frame("trnm_research_session_terminal_facts_v1").string(facts.result_code)
    .digest(facts.paper_bundle_hash).digest(facts.paper_release_candidate_hash).digest(facts.contribution_ledger_hash).finish();
}
function commitmentID(c) { return digest(new Frame("trnm_research_session_commitment_id_v1").string(c.session_id).digest(c.event_root).digest(c.archive_hash).finish()); }
function completionSigning(c) {
  return new Frame("trnm_research_session_completed_signature_v1").string(c.schema).digest(c.commitment_id)
    .string(c.session_id).string(c.team_id).string(c.paper_project_id).string(c.challenge_id)
    .u64(c.roster_version).digest(c.roster_root).bytes(terminalFrame(c.terminal_facts)).u64(c.event_count)
    .digest(c.event_root).digest(c.archive_hash).digest(c.ruleset_hash).digest(c.challenge_snapshot_hash)
    .i64(c.completed_at_unix).string(c.authority_key_id).finish();
}

assert(fixture.schema==="trnm.nakama.research_session.golden_vectors.v1","fixture schema");
const actionPayloadTypes=new Map([
  ["participant.ready","trnm.research-session.ready.v1"],
  ["research.task.claimed","trnm.paper-raid.task-claim.v1"],
  ["agent.proposal.submitted","trnm.paper-raid.agent-proposal.v1"],
  ["artifact.manifest.published","trnm.paper-raid.artifact-manifest.v1"],
  ["review.submitted","trnm.paper-raid.review.v1"],
  ["checkpoint.recorded","trnm.paper-raid.checkpoint.v1"],
  ["paper.release.acknowledged","trnm.paper-raid.release-acknowledgement.v1"],
]);
for(const [name,value] of Object.entries(fixture.keys)){
  const key=privateKey(value.seed_hex);assert(rawPublic(key).toString("base64")===value.public_key_base64,`${name} public key`);
}
for(const [name,value] of Object.entries(fixture.source_digests)) assert(digest(Buffer.from(value.utf8,"utf8"))===value.digest,`${name} digest`);

const issuerPrivate=privateKey(fixture.keys.issuer.seed_hex);const issuerPublic=publicKey(rawPublic(issuerPrivate));
for(const vector of fixture.authorizations){
  const claim=claimFrame(vector.value.claim);const signing=authorizationSigning(vector.value);
  assert(claim.toString("hex")===vector.claim_frame_hex,"claim frame");assert(signing.toString("hex")===vector.signing_frame_hex,"authorization signing frame");
  const signature=canonicalBase64(vector.value.signature,"authorization signature");
  assert(ed25519Verify(null,signing,issuerPublic,signature),"authorization signature verification");
  assert(ed25519Sign(null,signing,issuerPrivate).equals(signature),"authorization deterministic signature");
}
const roster=rosterFrame(fixture.roster,fixture.authorizations[0].value.claim);
assert(roster.toString("hex")===fixture.roster.frame_hex,"roster frame");assert(digest(roster)===fixture.roster.root,"roster root");
const seenKeys=new Set();for(const entry of fixture.roster.entries){assert(!seenKeys.has(entry.agent_key_hash),"duplicate roster key hash");seenKeys.add(entry.agent_key_hash);}

for(const vector of fixture.actions){const action=vector.value;assert(actionPayloadTypes.get(action.action_type)===action.payload_type,"action/payload pair");const signing=actionSigning(action);assert(signing.toString("hex")===vector.signing_frame_hex,"action signing frame");assert(digest(canonicalBase64(action.payload,"payload"))===action.payload_hash,"action payload hash");assert(actionFingerprint(action)===vector.fingerprint,"action fingerprint");const slotKey=privateKey(fixture.keys[`agent_${action.participant_slot}`].seed_hex);assert(ed25519Verify(null,signing,publicKey(rawPublic(slotKey)),canonicalBase64(action.signature,"action signature")),"action signature");}
for(const vector of fixture.negative_action_pairs){assert(vector.expected_error==="action_payload_type_mismatch","negative action error");assert(actionPayloadTypes.has(vector.action_type),"negative action type is known");assert(actionPayloadTypes.get(vector.action_type)!==vector.payload_type,"negative action/payload pair must be rejected");}

const events=fixture.sealed_events.map(vector=>vector.value);
for(const vector of fixture.sealed_events){const event=vector.value;const facts=eventFacts(event);assert(facts.toString("hex")===vector.facts_frame_hex,"event facts frame");assert(eventID(event)===event.event_id,"event id");assert(digest(canonicalBase64(event.payload,"event payload"))===event.payload_hash,"event payload hash");assert(digest(facts)===event.event_hash,"event hash");}
assert(eventRoot(events)===fixture.event_merkle.root,"event root");assert(digest(archiveFrame(events))===fixture.archive.hash,"archive hash");
const terminal=terminalFrame(fixture.terminal_facts.value);assert(terminal.toString("hex")===fixture.terminal_facts.frame_hex,"terminal facts frame");
const terminalEvent=events.at(-1);assert(terminalEvent.event_type==="research_session_completed","completion event must be terminal");assert(terminalEvent.action_type==="server.complete"&&terminalEvent.payload_type==="trnm.research-session.terminal-facts.v1","completion event semantic types");assert(canonicalBase64(terminalEvent.payload,"terminal event payload").equals(terminal),"completion event payload binds terminal facts");assert(terminalEvent.reference_hash===fixture.terminal_facts.value.paper_release_candidate_hash,"completion event release reference");
const completion=fixture.completion.value;assert(completion.event_count===events.length,"completion event_count covers full archive");assert(commitmentID(completion)===completion.commitment_id,"commitment id");assert(completion.event_root===fixture.event_merkle.root&&completion.archive_hash===fixture.archive.hash,"completion roots");assert(completion.completed_at_unix===terminalEvent.occurred_at_unix,"completion time binds terminal event");assert(JSON.stringify(completion.terminal_facts)===JSON.stringify(fixture.terminal_facts.value),"completion terminal facts");assert(completion.roster_root===fixture.roster.root&&completion.roster_version===fixture.roster.version,"completion roster");
const completionFrame=completionSigning(completion);assert(completionFrame.toString("hex")===fixture.completion.signing_frame_hex,"completion signing frame");const authorityPrivate=privateKey(fixture.keys.authority.seed_hex);const completionSignature=canonicalBase64(completion.signature,"completion signature");assert(ed25519Verify(null,completionFrame,publicKey(rawPublic(authorityPrivate)),completionSignature),"completion signature verification");assert(ed25519Sign(null,completionFrame,authorityPrivate).equals(completionSignature),"completion deterministic signature");

console.log(`research-session golden vectors: ${fixture.authorizations.length} authorizations, ${fixture.actions.length} action, ${events.length} events, roots and completion ok`);
