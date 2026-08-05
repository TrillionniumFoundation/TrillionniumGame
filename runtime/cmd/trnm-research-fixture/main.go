package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
)

type keyVector struct {
	SeedHex         string `json:"seed_hex"`
	PublicKeyBase64 string `json:"public_key_base64"`
}
type authorizationVector struct {
	Value           researchcontract.SignedAuthorization `json:"value"`
	ClaimFrameHex   string                               `json:"claim_frame_hex"`
	SigningFrameHex string                               `json:"signing_frame_hex"`
}
type actionVector struct {
	Value           researchcontract.ActionEnvelope `json:"value"`
	SigningFrameHex string                          `json:"signing_frame_hex"`
	Fingerprint     researchcontract.Digest         `json:"fingerprint"`
}
type negativeActionPairVector struct {
	ActionType    string `json:"action_type"`
	PayloadType   string `json:"payload_type"`
	ExpectedError string `json:"expected_error"`
}
type eventVector struct {
	Value         researchcontract.ResearchEvent `json:"value"`
	FactsFrameHex string                         `json:"facts_frame_hex"`
}
type digestSource struct {
	UTF8   string                  `json:"utf8"`
	Digest researchcontract.Digest `json:"digest"`
}

type fixture struct {
	Schema         string                  `json:"schema"`
	FixtureNotice  string                  `json:"fixture_notice"`
	Keys           map[string]keyVector    `json:"keys"`
	SourceDigests  map[string]digestSource `json:"source_digests"`
	Authorizations []authorizationVector   `json:"authorizations"`
	Roster         struct {
		Version  uint64                         `json:"version"`
		Entries  []researchcontract.RosterEntry `json:"entries"`
		FrameHex string                         `json:"frame_hex"`
		Root     researchcontract.Digest        `json:"root"`
	} `json:"roster"`
	Actions             []actionVector             `json:"actions"`
	NegativeActionPairs []negativeActionPairVector `json:"negative_action_pairs"`
	SealedEvents        []eventVector              `json:"sealed_events"`
	EventMerkle         struct {
		Commitments []researchcontract.EventCommitment `json:"commitments"`
		Root        researchcontract.Digest            `json:"root"`
	} `json:"event_merkle"`
	Archive struct {
		Hash researchcontract.Digest `json:"hash"`
	} `json:"archive"`
	TerminalFacts struct {
		Value    researchcontract.TerminalFacts `json:"value"`
		FrameHex string                         `json:"frame_hex"`
	} `json:"terminal_facts"`
	Completion struct {
		Value           researchcontract.SessionCompletedV1 `json:"value"`
		SigningFrameHex string                              `json:"signing_frame_hex"`
	} `json:"completion"`
}

func main() {
	issuerSeed := seed(0)
	authoritySeed := seed(96)
	agentSeeds := [][]byte{seed(32), seed(64), seed(128)}
	issuer := ed25519.NewKeyFromSeed(issuerSeed)
	authority := ed25519.NewKeyFromSeed(authoritySeed)
	agents := make([]ed25519.PrivateKey, 3)
	for i := range agents {
		agents[i] = ed25519.NewKeyFromSeed(agentSeeds[i])
	}
	now := time.Unix(1_800_000_000, 0).UTC()
	ruleset := researchcontract.NewDigest([]byte("paper-raid-ruleset:golden:v1"))
	challenge := researchcontract.NewDigest([]byte("paper-raid-challenge:golden:v1"))
	claims := make([]researchcontract.AuthorizationClaim, 3)
	for i := range claims {
		claims[i] = researchcontract.AuthorizationClaim{Schema: researchcontract.AuthorizationSchema, AuthorizationID: fmt.Sprintf("10000000-0000-4000-8000-%012d", i+1), SessionID: "research-session-golden-001", TeamID: "30000000-0000-4000-8000-000000000001", PaperProjectID: "40000000-0000-4000-8000-000000000001", ChallengeID: "50000000-0000-4000-8000-000000000001", AgentID: fmt.Sprintf("agent-golden-%d", i+1), AgentDID: fmt.Sprintf("did:trnm:research-agent-%d", i+1), AgentKeyID: fmt.Sprintf("agent-key-golden-%d", i+1), AgentPublicKey: agents[i].Public().(ed25519.PublicKey), SubjectUserID: fmt.Sprintf("00000000-0000-4000-8000-%012d", i+1), ParticipantSlot: uint32(i + 1), Role: []string{"captain-editor", "method-experiment", "evidence-integrity"}[i], RosterVersion: 1, RosterRoot: researchcontract.NewDigest([]byte("placeholder")), RulesetHash: ruleset, ChallengeSnapshotHash: challenge, IssuedAtUnix: now.Unix(), ExpiresAtUnix: now.Add(time.Hour).Unix()}
	}
	provisional := make([]researchcontract.SignedAuthorization, 3)
	for i := range claims {
		provisional[i].Claim = claims[i]
	}
	entries := researchcontract.RosterEntries(provisional)
	rosterRoot, mustErr := researchcontract.RosterRoot(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID, 1, entries)
	must(mustErr)
	auths := make([]researchcontract.SignedAuthorization, 3)
	for i := range claims {
		claims[i].RosterRoot = rosterRoot
		auths[i], mustErr = researchcontract.SignAuthorization(claims[i], "issuer-key-golden-001", issuer)
		must(mustErr)
	}
	engine, err := researchcore.NewSession(researchcore.NewSessionOptions{Authorizations: auths, TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-golden-001": issuer.Public().(ed25519.PublicKey)}, AuthorityKeyID: "nakama-research-authority-golden-001", AuthorityPrivateKey: authority, Now: now})
	must(err)
	for i, auth := range auths {
		_, err = engine.Join(auth.Claim.SubjectUserID, auth.Claim.AuthorizationID, now.Add(time.Duration(i+1)*time.Second))
		must(err)
	}
	actions := make([]researchcontract.ActionEnvelope, 0, 7)
	apply := func(slot int, actionType, payloadType string, reference researchcontract.Digest, at time.Time) {
		view := engine.View()
		p := view.Participants[slot-1]
		claim := auths[slot-1].Claim
		a := researchcontract.ActionEnvelope{Schema: researchcontract.ActionSchema, ActionID: fmt.Sprintf("research-action-golden-%02d", len(actions)+1), AuthorizationID: claim.AuthorizationID, SessionID: claim.SessionID, TeamID: claim.TeamID, PaperProjectID: claim.PaperProjectID, ChallengeID: claim.ChallengeID, RosterVersion: 1, ParticipantSlot: uint32(slot), ParticipantSequence: p.LastActionSequence + 1, ExpectedSessionVersion: view.Version, IssuedAtUnix: at.Unix(), ActionType: actionType, PayloadType: payloadType, Payload: []byte(fmt.Sprintf("golden:%s:slot:%d", actionType, slot)), ReferenceHash: reference, AgentKeyID: claim.AgentKeyID}
		a, err = researchcontract.SignAction(a, agents[slot-1])
		must(err)
		_, err = engine.ApplyAction(claim.SubjectUserID, a, at)
		must(err)
		actions = append(actions, a)
	}
	for i := 0; i < 3; i++ {
		apply(i+1, researchcontract.ActionParticipantReady, "trnm.research-session.ready.v1", rosterRoot, now.Add(time.Duration(10+i)*time.Second))
	}
	proposalHash := researchcontract.NewDigest([]byte("proposal-parent:golden:v1"))
	apply(1, researchcontract.ActionProposalSubmitted, "trnm.paper-raid.agent-proposal.v1", proposalHash, now.Add(20*time.Second))
	releaseHash := researchcontract.NewDigest([]byte("paper-release-candidate:golden:v1"))
	for i := 0; i < 3; i++ {
		apply(i+1, researchcontract.ActionPaperReleaseAcknowledged, researchcontract.PayloadPaperReleaseAcknowledged, releaseHash, now.Add(time.Duration(30+i)*time.Second))
	}
	facts := researchcontract.TerminalFacts{ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("paper-bundle:golden:v1")), PaperReleaseCandidateHash: releaseHash, ContributionLedgerHash: researchcontract.NewDigest([]byte("contribution-ledger:golden:v1"))}
	completion, err := engine.Complete(facts, now.Add(40*time.Second))
	must(err)
	events := engine.Events()
	must(researchcontract.VerifyCompletionAgainstArchive(completion, events, authority.Public().(ed25519.PublicKey)))
	eventRoot, err := researchcontract.EventRoot(events)
	must(err)
	archiveHash, err := researchcontract.ArchiveHash(events)
	must(err)
	commitmentID, err := researchcontract.CommitmentID(claims[0].SessionID, eventRoot, archiveHash)
	must(err)
	if completion.EventCount != uint64(len(events)) || completion.EventRoot != eventRoot ||
		completion.ArchiveHash != archiveHash || completion.CommitmentID != commitmentID {
		panic("runtime completion does not bind its full authoritative archive")
	}
	terminal, err := facts.CanonicalBytes()
	must(err)
	last := events[len(events)-1]
	if last.EventType != "research_session_completed" || last.ActionType != "server.complete" ||
		last.PayloadType != "trnm.research-session.terminal-facts.v1" ||
		!equalBytes(last.Payload, terminal) || last.ReferenceHash != facts.PaperReleaseCandidateHash ||
		last.OccurredAtUnix != completion.CompletedAtUnix {
		panic("runtime completion event does not match terminal facts")
	}
	out := fixture{Schema: "trnm.nakama.research_session.golden_vectors.v1", FixtureNotice: "TEST ONLY. Deterministic seeds and private keys MUST NEVER be used in production.", Keys: map[string]keyVector{}, SourceDigests: map[string]digestSource{"ruleset": {UTF8: "paper-raid-ruleset:golden:v1", Digest: ruleset}, "challenge_snapshot": {UTF8: "paper-raid-challenge:golden:v1", Digest: challenge}, "proposal_parent": {UTF8: "proposal-parent:golden:v1", Digest: proposalHash}, "release_candidate": {UTF8: "paper-release-candidate:golden:v1", Digest: releaseHash}, "paper_bundle": {UTF8: "paper-bundle:golden:v1", Digest: facts.PaperBundleHash}, "contribution_ledger": {UTF8: "contribution-ledger:golden:v1", Digest: facts.ContributionLedgerHash}}}
	out.Keys["issuer"] = key(issuerSeed, issuer)
	out.Keys["authority"] = key(authoritySeed, authority)
	for i := range agents {
		out.Keys[fmt.Sprintf("agent_%d", i+1)] = key(agentSeeds[i], agents[i])
	}
	for _, auth := range auths {
		claimFrame, err := auth.Claim.CanonicalBytes()
		must(err)
		signing, err := researchcontract.AuthorizationSigningBytes(auth.Claim, auth.IssuerKeyID)
		must(err)
		out.Authorizations = append(out.Authorizations, authorizationVector{Value: auth, ClaimFrameHex: hex.EncodeToString(claimFrame), SigningFrameHex: hex.EncodeToString(signing)})
	}
	rosterFrame, err := researchcontract.RosterCanonicalBytes(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID, 1, researchcontract.RosterEntries(auths))
	must(err)
	out.Roster.Version = 1
	out.Roster.Entries = researchcontract.RosterEntries(auths)
	out.Roster.FrameHex = hex.EncodeToString(rosterFrame)
	out.Roster.Root = rosterRoot
	for _, action := range actions[:1] {
		signing, err := action.SigningBytes()
		must(err)
		fingerprint, err := researchcontract.ActionFingerprint(action)
		must(err)
		out.Actions = append(out.Actions, actionVector{Value: action, SigningFrameHex: hex.EncodeToString(signing), Fingerprint: fingerprint})
	}
	out.NegativeActionPairs = []negativeActionPairVector{{
		ActionType:    researchcontract.ActionProposalSubmitted,
		PayloadType:   researchcontract.PayloadReviewSubmitted,
		ExpectedError: "action_payload_type_mismatch",
	}}
	for _, event := range events {
		factsFrame, err := event.FactsBytes()
		must(err)
		out.SealedEvents = append(out.SealedEvents, eventVector{Value: event, FactsFrameHex: hex.EncodeToString(factsFrame)})
		out.EventMerkle.Commitments = append(out.EventMerkle.Commitments, researchcontract.EventCommitment{Sequence: event.Sequence, EventHash: event.EventHash})
	}
	out.EventMerkle.Root, err = researchcontract.EventRoot(events)
	must(err)
	out.Archive.Hash = archiveHash
	out.TerminalFacts.Value = facts
	out.TerminalFacts.FrameHex = hex.EncodeToString(terminal)
	completionSigning, err := completion.SigningBytes()
	must(err)
	out.Completion.Value = completion
	out.Completion.SigningFrameHex = hex.EncodeToString(completionSigning)
	encoded, err := json.MarshalIndent(out, "", "  ")
	must(err)
	_, err = os.Stdout.Write(append(encoded, '\n'))
	must(err)
}

func seed(start byte) []byte {
	out := make([]byte, ed25519.SeedSize)
	for i := range out {
		out[i] = start + byte(i)
	}
	return out
}
func key(seed []byte, key ed25519.PrivateKey) keyVector {
	return keyVector{SeedHex: hex.EncodeToString(seed), PublicKeyBase64: base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey))}
}
func equalBytes(left, right []byte) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}
func must(err error) {
	if err != nil {
		panic(err)
	}
}
