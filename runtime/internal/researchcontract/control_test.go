package researchcontract

import (
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
	"testing"
)

func validResearchControlAuthorizations(t *testing.T) []SignedAuthorization {
	t.Helper()
	_, issuer, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	claims := make([]AuthorizationClaim, 3)
	for index := range claims {
		publicKey, _, keyErr := ed25519.GenerateKey(rand.Reader)
		if keyErr != nil {
			t.Fatal(keyErr)
		}
		claims[index] = AuthorizationClaim{
			Schema: AuthorizationSchema, AuthorizationID: fmt.Sprintf("10000000-0000-4000-8000-%012d", index+1),
			SessionID: "paper-raid-control-auth-set", TeamID: "30000000-0000-4000-8000-000000000001",
			PaperProjectID: "40000000-0000-4000-8000-000000000001", ChallengeID: "50000000-0000-4000-8000-000000000001",
			AgentID: fmt.Sprintf("agent-%d", index+1), AgentDID: fmt.Sprintf("did:trnm:agent-%d", index+1),
			AgentKeyID: fmt.Sprintf("agent-key-%d", index+1), AgentPublicKey: publicKey,
			SubjectUserID: fmt.Sprintf("60000000-0000-4000-8000-%012d", index+1), ParticipantSlot: uint32(index + 1),
			Role: fmt.Sprintf("role-%d", index+1), RosterVersion: 1, RosterRoot: NewDigest([]byte("placeholder")),
			RulesetHash: NewDigest([]byte("ruleset")), ChallengeSnapshotHash: NewDigest([]byte("challenge")),
			IssuedAtUnix: 1_700_000_000, ExpiresAtUnix: 1_700_003_600,
		}
	}
	provisional := make([]SignedAuthorization, len(claims))
	for index := range claims {
		provisional[index].Claim = claims[index]
	}
	root, err := RosterRoot(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID, 1, RosterEntries(provisional))
	if err != nil {
		t.Fatal(err)
	}
	result := make([]SignedAuthorization, len(claims))
	for index := range claims {
		claims[index].RosterRoot = root
		result[index], err = SignAuthorization(claims[index], "authorization-issuer", issuer)
		if err != nil {
			t.Fatal(err)
		}
	}
	return result
}

func validResearchControl(t *testing.T) (SignedResearchControlV2, ed25519.PublicKey, ed25519.PrivateKey) {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	control, err := SignResearchControlV2(ResearchControlClaimV2{
		Schema: ResearchControlClaimSchemaV2, CommandID: "10000000-0000-4000-8000-000000000001",
		Operation: ResearchControlOperationCreate, TargetRPC: ResearchControlRPCCreateV2,
		SessionID: "paper-raid-control-test", SessionRosterVersion: 1,
		AuthorizationSetID: "20000000-0000-4000-8000-000000000001",
		PayloadHash:        NewDigest([]byte("business payload")), Audience: ResearchControlAudienceV2,
		IssuedAtUnix: 1_700_000_000, ExpiresAtUnix: 1_700_000_120, IssuerKeyID: "hepta-control-v2",
	}, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return control, publicKey, privateKey
}

func TestResearchControlV2VerifiesBoundClaimAndClockWindow(t *testing.T) {
	control, publicKey, _ := validResearchControl(t)
	trusted := map[string]ed25519.PublicKey{"hepta-control-v2": publicKey}
	for _, at := range []int64{control.Claim.IssuedAtUnix - ResearchControlClockSkewSeconds, control.Claim.IssuedAtUnix, control.Claim.ExpiresAtUnix, control.Claim.ExpiresAtUnix + ResearchControlClockSkewSeconds} {
		if err := VerifyResearchControlV2(control, trusted, at); err != nil {
			t.Fatalf("valid control rejected at %d: %v", at, err)
		}
	}
	if err := VerifyResearchControlV2(control, trusted, control.Claim.IssuedAtUnix-ResearchControlClockSkewSeconds-1); err == nil {
		t.Fatal("future control outside skew was accepted")
	}
	if err := VerifyResearchControlV2(control, trusted, control.Claim.ExpiresAtUnix+ResearchControlClockSkewSeconds+1); err == nil {
		t.Fatal("expired control outside skew was accepted")
	}
}

func TestResearchControlV2RejectsEverySignedBindingMutation(t *testing.T) {
	control, publicKey, privateKey := validResearchControl(t)
	trusted := map[string]ed25519.PublicKey{"hepta-control-v2": publicKey}
	tests := map[string]func(*ResearchControlClaimV2){
		"command":   func(value *ResearchControlClaimV2) { value.CommandID = "10000000-0000-4000-8000-000000000002" },
		"operation": func(value *ResearchControlClaimV2) { value.Operation = ResearchControlOperationComplete },
		"target":    func(value *ResearchControlClaimV2) { value.TargetRPC = ResearchControlRPCReplaceV2 },
		"session":   func(value *ResearchControlClaimV2) { value.SessionID = "another-session" },
		"roster":    func(value *ResearchControlClaimV2) { value.SessionRosterVersion++ },
		"set":       func(value *ResearchControlClaimV2) { value.AuthorizationSetID = "20000000-0000-4000-8000-000000000002" },
		"payload":   func(value *ResearchControlClaimV2) { value.PayloadHash = NewDigest([]byte("different")) },
		"audience":  func(value *ResearchControlClaimV2) { value.Audience = "another-audience" },
		"issued":    func(value *ResearchControlClaimV2) { value.IssuedAtUnix++ },
		"expires":   func(value *ResearchControlClaimV2) { value.ExpiresAtUnix-- },
		"key":       func(value *ResearchControlClaimV2) { value.IssuerKeyID = "another-key" },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			changed := control
			mutate(&changed.Claim)
			if err := VerifyResearchControlSignatureV2(changed, trusted); err == nil {
				t.Fatal("mutated signed binding was accepted")
			}
		})
	}

	tooLong := control.Claim
	tooLong.ExpiresAtUnix = tooLong.IssuedAtUnix + ResearchControlMaximumLifetimeSeconds + 1
	if _, err := SignResearchControlV2(tooLong, privateKey); err == nil {
		t.Fatal("oversized validity window was signed")
	}

	tampered := control
	tampered.Signature = append([]byte(nil), control.Signature...)
	tampered.Signature[0] ^= 1
	if err := VerifyResearchControlSignatureV2(tampered, trusted); err == nil {
		t.Fatal("tampered control signature was accepted")
	}
}

func TestResearchControlBusinessFrameRejectsMixedRosterEpochBeforeEncoding(t *testing.T) {
	authorizations := validResearchControlAuthorizations(t)
	authorizations[1].Claim.RosterVersion = 2
	if _, err := ResearchControlCreateBusinessBytesV2(ResearchControlCreateRequestSchemaV2,
		"20000000-0000-4000-8000-000000000001", authorizations); err == nil {
		t.Fatal("create business frame accepted a mixed authorization epoch")
	}
	if _, err := ResearchControlReplaceBusinessBytesV2(ResearchControlReplaceRequestSchemaV2,
		"paper-raid-control-auth-set", "20000000-0000-4000-8000-000000000002", authorizations); err == nil {
		t.Fatal("replacement business frame accepted a mixed authorization epoch")
	}
}
