package researchcore

import (
	"crypto/ed25519"
	"fmt"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
)

type fixture struct {
	t              *testing.T
	now            time.Time
	issuerPrivate  ed25519.PrivateKey
	authority      ed25519.PrivateKey
	agentPrivate   []ed25519.PrivateKey
	authorizations []researchcontract.SignedAuthorization
	engine         *Engine
}

const (
	fixtureTeamID      = "30000000-0000-4000-8000-000000000001"
	fixturePaperID     = "40000000-0000-4000-8000-000000000001"
	fixtureChallengeID = "50000000-0000-4000-8000-000000000001"
)

func newFixture(t *testing.T, participants int) *fixture {
	t.Helper()
	f := &fixture{t: t, now: time.Unix(1_800_000_000, 0).UTC()}
	f.issuerPrivate = ed25519.NewKeyFromSeed(seed(0))
	f.authority = ed25519.NewKeyFromSeed(seed(64))
	f.agentPrivate = make([]ed25519.PrivateKey, participants)
	for i := range f.agentPrivate {
		f.agentPrivate[i] = ed25519.NewKeyFromSeed(seed(byte(96 + i*7)))
	}
	f.authorizations = f.makeAuthorizations(1, -1)
	engine, err := NewSession(NewSessionOptions{
		Authorizations:    f.authorizations,
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-test": f.issuerPrivate.Public().(ed25519.PublicKey)},
		AuthorityKeyID:    "nakama-research-test", AuthorityPrivateKey: f.authority, Now: f.now,
	})
	if err != nil {
		t.Fatal(err)
	}
	f.engine = engine
	return f
}

func seed(start byte) []byte {
	out := make([]byte, ed25519.SeedSize)
	for i := range out {
		out[i] = start + byte(i)
	}
	return out
}

func fixtureAuthorizationID(version uint64, slot int) string {
	return fmt.Sprintf("10000000-0000-4000-8000-%012d", version*100+uint64(slot))
}

func fixtureUserID(suffix int) string {
	return fmt.Sprintf("20000000-0000-4000-8000-%012d", suffix)
}

func (f *fixture) makeAuthorizations(version uint64, replacementSlot int) []researchcontract.SignedAuthorization {
	count := len(f.agentPrivate)
	claims := make([]researchcontract.AuthorizationClaim, count)
	for i := 0; i < count; i++ {
		key := f.agentPrivate[i]
		userSuffix, agentSuffix := i+1, i+1
		keyID := fmt.Sprintf("agent-key-%d", agentSuffix)
		if replacementSlot == i {
			key = ed25519.NewKeyFromSeed(seed(byte(170 + i)))
			keyID = fmt.Sprintf("agent-key-%d-v%d", agentSuffix, version)
		}
		claims[i] = researchcontract.AuthorizationClaim{
			Schema:          researchcontract.AuthorizationSchema,
			AuthorizationID: fixtureAuthorizationID(version, i+1), SessionID: "research-session-test",
			TeamID: fixtureTeamID, PaperProjectID: fixturePaperID, ChallengeID: fixtureChallengeID,
			AgentID: fmt.Sprintf("agent-%d", agentSuffix), AgentDID: fmt.Sprintf("did:trnm:agent-%d", agentSuffix),
			AgentKeyID: keyID, AgentPublicKey: key.Public().(ed25519.PublicKey),
			SubjectUserID: fixtureUserID(userSuffix), ParticipantSlot: uint32(i + 1), Role: fmt.Sprintf("role-%d", i+1),
			RosterVersion: version, RosterRoot: researchcontract.NewDigest([]byte("placeholder")),
			RulesetHash: researchcontract.NewDigest([]byte("ruleset")), ChallengeSnapshotHash: researchcontract.NewDigest([]byte("challenge")),
			IssuedAtUnix: f.now.Unix(), ExpiresAtUnix: f.now.Add(time.Hour).Unix(),
		}
		if replacementSlot == i {
			f.agentPrivate[i] = key
		}
	}
	provisional := make([]researchcontract.SignedAuthorization, count)
	for i := range claims {
		provisional[i].Claim = claims[i]
	}
	root, err := researchcontract.RosterRoot(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID, version, researchcontract.RosterEntries(provisional))
	if err != nil {
		f.t.Fatal(err)
	}
	auths := make([]researchcontract.SignedAuthorization, count)
	for i := range claims {
		claims[i].RosterRoot = root
		auths[i], err = researchcontract.SignAuthorization(claims[i], "issuer-test", f.issuerPrivate)
		if err != nil {
			f.t.Fatal(err)
		}
	}
	return auths
}

func (f *fixture) reissueClaims(claims []researchcontract.AuthorizationClaim) []researchcontract.SignedAuthorization {
	f.t.Helper()
	provisional := make([]researchcontract.SignedAuthorization, len(claims))
	for i := range claims {
		provisional[i].Claim = claims[i]
	}
	root, err := researchcontract.RosterRoot(claims[0].SessionID, claims[0].TeamID,
		claims[0].PaperProjectID, claims[0].RosterVersion, researchcontract.RosterEntries(provisional))
	if err != nil {
		f.t.Fatal(err)
	}
	auths := make([]researchcontract.SignedAuthorization, len(claims))
	for i := range claims {
		claims[i].RosterRoot = root
		auths[i], err = researchcontract.SignAuthorization(claims[i], "issuer-test", f.issuerPrivate)
		if err != nil {
			f.t.Fatal(err)
		}
	}
	return auths
}

func (f *fixture) resignPreservingClaims(auths []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
	f.t.Helper()
	result := cloneAuthorizations(auths)
	for i := range result {
		var err error
		result[i], err = researchcontract.SignAuthorization(result[i].Claim, "issuer-test", f.issuerPrivate)
		if err != nil {
			f.t.Fatal(err)
		}
	}
	return result
}

func (f *fixture) joinAll() {
	f.joinAllAt(f.now.Add(time.Second))
}

func (f *fixture) joinAllAt(start time.Time) {
	for i, auth := range f.currentAuthorizations() {
		if _, err := f.engine.Join(auth.Claim.SubjectUserID, auth.Claim.AuthorizationID, start.Add(time.Duration(i)*time.Second)); err != nil {
			f.t.Fatal(err)
		}
	}
}

func (f *fixture) currentAuthorizations() []researchcontract.SignedAuthorization {
	return f.engine.currentEpoch().Authorizations
}

func (f *fixture) action(slot int, actionType, payloadType string, reference researchcontract.Digest, at time.Time) researchcontract.ActionEnvelope {
	view := f.engine.View()
	participant := view.Participants[slot-1]
	claim := f.currentAuthorizations()[slot-1].Claim
	action, err := researchcontract.SignAction(researchcontract.ActionEnvelope{
		Schema: researchcontract.ActionSchema, ActionID: fmt.Sprintf("action-v%d-%d-%d", view.RosterVersion, slot, participant.LastActionSequence+1),
		AuthorizationID: claim.AuthorizationID, SessionID: view.SessionID, TeamID: view.TeamID,
		PaperProjectID: view.PaperProjectID, ChallengeID: view.ChallengeID, RosterVersion: view.RosterVersion,
		ParticipantSlot: uint32(slot), ParticipantSequence: participant.LastActionSequence + 1,
		ExpectedSessionVersion: view.Version, IssuedAtUnix: at.Unix(), ActionType: actionType,
		PayloadType: payloadType, Payload: []byte(fmt.Sprintf("payload-%s-%d", actionType, slot)),
		ReferenceHash: reference, AgentKeyID: claim.AgentKeyID,
	}, f.agentPrivate[slot-1])
	if err != nil {
		f.t.Fatal(err)
	}
	return action
}

func (f *fixture) readyAll(start time.Time) {
	root := f.engine.View().RosterRoot
	for i := range f.currentAuthorizations() {
		action := f.action(i+1, researchcontract.ActionParticipantReady, "trnm.research-session.ready.v1", root, start.Add(time.Duration(i)*time.Second))
		if _, err := f.engine.ApplyAction(f.currentAuthorizations()[i].Claim.SubjectUserID, action, start.Add(time.Duration(i)*time.Second)); err != nil {
			f.t.Fatal(err)
		}
	}
}

func (f *fixture) acknowledgeAll(hash researchcontract.Digest, start time.Time) {
	for i := range f.currentAuthorizations() {
		action := f.action(i+1, researchcontract.ActionPaperReleaseAcknowledged, researchcontract.PayloadPaperReleaseAcknowledged, hash, start.Add(time.Duration(i)*time.Second))
		if _, err := f.engine.ApplyAction(f.currentAuthorizations()[i].Claim.SubjectUserID, action, start.Add(time.Duration(i)*time.Second)); err != nil {
			f.t.Fatal(err)
		}
	}
}

func TestThreeFourFiveParticipantCooperativeCompletion(t *testing.T) {
	for _, count := range []int{3, 4, 5} {
		t.Run(fmt.Sprintf("%d", count), func(t *testing.T) {
			f := newFixture(t, count)
			f.joinAll()
			f.readyAll(f.now.Add(10 * time.Second))
			reference := researchcontract.NewDigest([]byte("work-item"))
			action := f.action(1, researchcontract.ActionProposalSubmitted, "trnm.paper-raid.agent-proposal.v1", reference, f.now.Add(20*time.Second))
			if _, err := f.engine.ApplyAction(fixtureUserID(1), action, f.now.Add(20*time.Second)); err != nil {
				t.Fatal(err)
			}
			release := researchcontract.NewDigest([]byte("release"))
			f.acknowledgeAll(release, f.now.Add(30*time.Second))
			facts := researchcontract.TerminalFacts{ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("bundle")), PaperReleaseCandidateHash: release, ContributionLedgerHash: researchcontract.NewDigest([]byte("ledger"))}
			completion, err := f.engine.Complete(facts, f.now.Add(40*time.Second))
			if err != nil {
				t.Fatal(err)
			}
			if completion.RosterVersion != 1 || completion.EventCount != uint64(len(f.engine.Events())) {
				t.Fatal("completion count or roster version differs")
			}
			if err := researchcontract.VerifyCompletion(completion, f.authority.Public().(ed25519.PublicKey)); err != nil {
				t.Fatal(err)
			}
			if got, _ := researchcontract.EventRoot(f.engine.Events()); got != completion.EventRoot {
				t.Fatal("event root differs")
			}
			if got, _ := researchcontract.ArchiveHash(f.engine.Events()); got != completion.ArchiveHash {
				t.Fatal("archive hash differs")
			}
		})
	}
}

func TestAdmissionExpiryDoesNotBecomePaperRaidWorkDeadline(t *testing.T) {
	f := newFixture(t, 3)
	f.joinAll()
	afterAdmissionExpiry := f.now.Add(2 * time.Hour)
	f.readyAll(afterAdmissionExpiry)
	action := f.action(1, researchcontract.ActionProposalSubmitted, researchcontract.PayloadProposalSubmitted,
		researchcontract.NewDigest([]byte("post-admission-expiry-work")), afterAdmissionExpiry.Add(time.Minute))
	if _, err := f.engine.ApplyAction(fixtureUserID(1), action, afterAdmissionExpiry.Add(time.Minute)); err != nil {
		t.Fatalf("joined member could not act after admission expiry: %v", err)
	}
	auth := f.currentAuthorizations()[0].Claim
	if _, err := f.engine.Disconnect(auth.SubjectUserID, auth.AuthorizationID, afterAdmissionExpiry.Add(2*time.Minute)); err != nil {
		t.Fatal(err)
	}
	if _, err := f.engine.Join(auth.SubjectUserID, auth.AuthorizationID, afterAdmissionExpiry.Add(3*time.Minute)); err != nil {
		t.Fatalf("joined member could not reconnect after admission expiry: %v", err)
	}

	neverJoined := newFixture(t, 3)
	claim := neverJoined.currentAuthorizations()[0].Claim
	if _, err := neverJoined.engine.Join(claim.SubjectUserID, claim.AuthorizationID, afterAdmissionExpiry); err == nil {
		t.Fatal("first admission after expires_at_unix was accepted")
	}
}

func TestReplacementRequiresDisconnectedTargetAndFullFreshEpoch(t *testing.T) {
	f := newFixture(t, 3)
	f.joinAll()
	f.readyAll(f.now.Add(10 * time.Second))
	fresh := f.makeAuthorizations(2, 2)
	if _, err := f.engine.ReplaceRoster(fresh, f.now.Add(20*time.Second)); err == nil {
		t.Fatal("connected replacement accepted")
	}
	old := f.currentAuthorizations()[2].Claim
	if _, err := f.engine.Disconnect(old.SubjectUserID, old.AuthorizationID, f.now.Add(21*time.Second)); err != nil {
		t.Fatal(err)
	}
	partialFresh := append([]researchcontract.SignedAuthorization(nil), fresh...)
	partialFresh[0] = f.currentAuthorizations()[0]
	if _, err := f.engine.ReplaceRoster(partialFresh, f.now.Add(22*time.Second)); err == nil {
		t.Fatal("mixed old/new epoch accepted")
	}
	if _, err := f.engine.ReplaceRoster(fresh, f.now.Add(22*time.Second)); err != nil {
		t.Fatal(err)
	}
	if f.engine.View().Status != StatusPaused {
		t.Fatal("replacement did not pause")
	}
	oldAction := f.action(1, researchcontract.ActionTaskClaimed, "trnm.paper-raid.task-claim.v1", researchcontract.NewDigest([]byte("task")), f.now.Add(23*time.Second))
	oldAction.AuthorizationID = f.authorizations[0].Claim.AuthorizationID
	if _, err := f.engine.ApplyAction(f.authorizations[0].Claim.SubjectUserID, oldAction, f.now.Add(23*time.Second)); err == nil {
		t.Fatal("old authorization acted in fresh epoch")
	}
	f.joinAllAt(f.now.Add(24 * time.Second))
	f.readyAll(f.now.Add(30 * time.Second))
	if f.engine.View().Status != StatusActive {
		t.Fatal("fresh epoch did not require/achieve all-ready")
	}
}

func TestInitialAuthorizationSetRejectsBoundsDuplicatesAndTamper(t *testing.T) {
	f := newFixture(t, 3)
	trusted := map[string]ed25519.PublicKey{"issuer-test": f.issuerPrivate.Public().(ed25519.PublicKey)}
	if _, err := researchcontract.ValidateAuthorizationSet(f.authorizations[:2], trusted, f.now.Unix(), true); err == nil {
		t.Fatal("two-participant roster was accepted")
	}
	six := append(cloneAuthorizations(f.authorizations), cloneAuthorizations(f.authorizations)...)
	if _, err := researchcontract.ValidateAuthorizationSet(six, trusted, f.now.Unix(), true); err == nil {
		t.Fatal("six-participant roster was accepted")
	}

	duplicateAuthorizationID := cloneAuthorizations(f.authorizations)
	duplicateAuthorizationID[1].Claim.AuthorizationID = duplicateAuthorizationID[0].Claim.AuthorizationID
	duplicateAuthorizationID = f.resignPreservingClaims(duplicateAuthorizationID)
	if _, err := researchcontract.ValidateAuthorizationSet(duplicateAuthorizationID, trusted, f.now.Unix(), true); err == nil {
		t.Fatal("duplicate authorization_id was accepted")
	}

	tamperedRoot := cloneAuthorizations(f.authorizations)
	for i := range tamperedRoot {
		tamperedRoot[i].Claim.RosterRoot = researchcontract.NewDigest([]byte("tampered-roster"))
	}
	tamperedRoot = f.resignPreservingClaims(tamperedRoot)
	if _, err := researchcontract.ValidateAuthorizationSet(tamperedRoot, trusted, f.now.Unix(), true); err == nil {
		t.Fatal("issuer-signed but noncanonical roster_root was accepted")
	}

	tamperedSignature := cloneAuthorizations(f.authorizations)
	tamperedSignature[0].Signature[0] ^= 1
	if _, err := researchcontract.ValidateAuthorizationSet(tamperedSignature, trusted, f.now.Unix(), true); err == nil {
		t.Fatal("tampered authorization signature was accepted")
	}
}

func TestReplacementRejectsIdentitySubstitutionAndInvalidKeyRotation(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*fixture, []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization
	}{
		{
			name: "human substitution",
			mutate: func(f *fixture, fresh []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				claims := authorizationClaims(fresh)
				claims[1].SubjectUserID = fixtureUserID(99)
				return f.reissueClaims(claims)
			},
		},
		{
			name: "Agent identity substitution",
			mutate: func(f *fixture, fresh []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				claims := authorizationClaims(fresh)
				claims[1].AgentID = "agent-substitute"
				return f.reissueClaims(claims)
			},
		},
		{
			name: "role substitution",
			mutate: func(f *fixture, fresh []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				claims := authorizationClaims(fresh)
				claims[1].Role = "role-substitute"
				return f.reissueClaims(claims)
			},
		},
		{
			name: "key id only",
			mutate: func(f *fixture, fresh []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				claims := authorizationClaims(fresh)
				claims[1].AgentPublicKey = append([]byte(nil), f.authorizations[1].Claim.AgentPublicKey...)
				return f.reissueClaims(claims)
			},
		},
		{
			name: "public key only",
			mutate: func(f *fixture, fresh []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				claims := authorizationClaims(fresh)
				claims[1].AgentKeyID = f.authorizations[1].Claim.AgentKeyID
				return f.reissueClaims(claims)
			},
		},
		{
			name: "no key changed",
			mutate: func(f *fixture, _ []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				return f.makeAuthorizations(2, -1)
			},
		},
		{
			name: "two keys changed",
			mutate: func(f *fixture, fresh []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
				claims := authorizationClaims(fresh)
				secondKey := ed25519.NewKeyFromSeed(seed(230))
				claims[0].AgentKeyID = "agent-key-1-v2"
				claims[0].AgentPublicKey = secondKey.Public().(ed25519.PublicKey)
				return f.reissueClaims(claims)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			f := newFixture(t, 3)
			f.joinAll()
			fresh := f.makeAuthorizations(2, 1)
			old := f.currentAuthorizations()[1].Claim
			if _, err := f.engine.Disconnect(old.SubjectUserID, old.AuthorizationID, f.now.Add(10*time.Second)); err != nil {
				t.Fatal(err)
			}
			if _, err := f.engine.ReplaceRoster(test.mutate(f, fresh), f.now.Add(11*time.Second)); err == nil {
				t.Fatal("invalid roster replacement was accepted")
			}
		})
	}
}

func TestCompletionAfterRotationRequiresFreshEpochWorkAndAgentAcknowledgements(t *testing.T) {
	f := newFixture(t, 3)
	f.joinAll()
	f.readyAll(f.now.Add(10 * time.Second))
	epochOneWork := f.action(1, researchcontract.ActionProposalSubmitted, researchcontract.PayloadProposalSubmitted,
		researchcontract.NewDigest([]byte("epoch-one-work")), f.now.Add(20*time.Second))
	if _, err := f.engine.ApplyAction(fixtureUserID(1), epochOneWork, f.now.Add(20*time.Second)); err != nil {
		t.Fatal(err)
	}
	release := researchcontract.NewDigest([]byte("release-after-rotation"))
	f.acknowledgeAll(release, f.now.Add(30*time.Second))
	target := f.currentAuthorizations()[1].Claim
	if _, err := f.engine.Disconnect(target.SubjectUserID, target.AuthorizationID, f.now.Add(40*time.Second)); err != nil {
		t.Fatal(err)
	}
	fresh := f.makeAuthorizations(2, 1)
	if _, err := f.engine.ReplaceRoster(fresh, f.now.Add(41*time.Second)); err != nil {
		t.Fatal(err)
	}
	facts := researchcontract.TerminalFacts{ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("bundle")), PaperReleaseCandidateHash: release, ContributionLedgerHash: researchcontract.NewDigest([]byte("ledger"))}
	if _, err := f.engine.Complete(facts, f.now.Add(42*time.Second)); err == nil {
		t.Fatal("completion reused epoch-one ready/work/acknowledgement state")
	}
	f.joinAllAt(f.now.Add(50 * time.Second))
	f.readyAll(f.now.Add(60 * time.Second))
	if _, err := f.engine.Complete(facts, f.now.Add(70*time.Second)); err == nil {
		t.Fatal("completion succeeded without current-epoch acknowledgement or work")
	}
	f.acknowledgeAll(release, f.now.Add(80*time.Second))
	if _, err := f.engine.Complete(facts, f.now.Add(90*time.Second)); err == nil {
		t.Fatal("completion counted epoch-one substantive work in epoch two")
	}
	epochTwoWork := f.action(3, researchcontract.ActionReviewSubmitted, researchcontract.PayloadReviewSubmitted,
		researchcontract.NewDigest([]byte("epoch-two-review")), f.now.Add(100*time.Second))
	if _, err := f.engine.ApplyAction(fixtureUserID(3), epochTwoWork, f.now.Add(100*time.Second)); err != nil {
		t.Fatal(err)
	}
	completion, err := f.engine.Complete(facts, f.now.Add(110*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if completion.RosterVersion != 2 || completion.RosterRoot != f.engine.View().RosterRoot {
		t.Fatal("completion did not bind the fresh session authorization epoch")
	}
	if err := researchcontract.VerifyCompletion(completion, f.authority.Public().(ed25519.PublicKey)); err != nil {
		t.Fatal(err)
	}
	if got, err := researchcontract.EventRoot(f.engine.Events()); err != nil || got != completion.EventRoot {
		t.Fatal("rotated completion event_root did not independently recompute")
	}
	if got, err := researchcontract.ArchiveHash(f.engine.Events()); err != nil || got != completion.ArchiveHash {
		t.Fatal("rotated completion archive_hash did not independently recompute")
	}
}

func authorizationClaims(authorizations []researchcontract.SignedAuthorization) []researchcontract.AuthorizationClaim {
	claims := make([]researchcontract.AuthorizationClaim, len(authorizations))
	for i := range authorizations {
		claims[i] = authorizations[i].Claim
		claims[i].AgentPublicKey = append([]byte(nil), authorizations[i].Claim.AgentPublicKey...)
	}
	return claims
}

func TestActionSemanticAndRosterBounds(t *testing.T) {
	f := newFixture(t, 3)
	f.joinAll()
	f.readyAll(f.now.Add(10 * time.Second))
	ref := researchcontract.NewDigest([]byte("x"))
	wrong := f.action(1, researchcontract.ActionProposalSubmitted, "trnm.paper-raid.agent-proposal.v1", ref, f.now.Add(20*time.Second))
	wrong.PayloadType = "trnm.paper-raid.review.v1"
	if err := wrong.Validate(); err == nil {
		t.Fatal("contract accepted wrong action/payload pair")
	}
	if _, err := f.engine.ApplyAction(fixtureUserID(1), wrong, f.now.Add(20*time.Second)); err == nil {
		t.Fatal("wrong action/payload pair accepted")
	}
	outside := f.action(1, researchcontract.ActionProposalSubmitted, "trnm.paper-raid.agent-proposal.v1", ref, f.now.Add(20*time.Second))
	outside.ParticipantSlot = 4
	outside, _ = researchcontract.SignAction(outside, f.agentPrivate[0])
	if _, err := f.engine.ApplyAction(fixtureUserID(1), outside, f.now.Add(20*time.Second)); err == nil {
		t.Fatal("slot beyond current roster accepted")
	}
}

func TestRosterRejectsDuplicatePublicKeyAcrossDifferentIdentity(t *testing.T) {
	f := newFixture(t, 3)
	claims := f.authorizations
	claims[1].Claim.AgentPublicKey = append([]byte(nil), claims[0].Claim.AgentPublicKey...)
	provisional := cloneAuthorizations(claims)
	if _, err := researchcontract.RosterRoot(provisional[0].Claim.SessionID, provisional[0].Claim.TeamID,
		provisional[0].Claim.PaperProjectID, 1, researchcontract.RosterEntries(provisional)); err == nil {
		t.Fatal("duplicate public key accepted")
	}
}
