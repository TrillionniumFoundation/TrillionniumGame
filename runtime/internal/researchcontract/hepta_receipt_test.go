package researchcontract

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
)

func signedReceiptFixture(t *testing.T) (SignedHeptaCompletionReceiptV1, ed25519.PublicKey) {
	t.Helper()
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(index + 41)
	}
	privateKey := ed25519.NewKeyFromSeed(seed)
	receipt := SignedHeptaCompletionReceiptV1{
		Schema:       HeptaCompletionReceiptSchema,
		CommitmentID: NewDigest([]byte("commitment")), SessionID: "research-receipt-test",
		TeamID: "30000000-0000-4000-8000-000000000001", PaperProjectID: "40000000-0000-4000-8000-000000000001",
		ChallengeID: "50000000-0000-4000-8000-000000000001", RosterVersion: 2,
		RosterRoot: NewDigest([]byte("roster")), EventCount: 9, EventRoot: NewDigest([]byte("events")),
		ArchiveHash: NewDigest([]byte("archive")), RulesetHash: NewDigest([]byte("ruleset")),
		ChallengeSnapshotHash: NewDigest([]byte("challenge")), NakamaAuthorityKeyID: "nakama-authority-v1",
		TerminalFacts: TerminalFacts{ResultCode: "paper_bundle_ready", PaperBundleHash: NewDigest([]byte("bundle")),
			PaperReleaseCandidateHash: NewDigest([]byte("release")), ContributionLedgerHash: NewDigest([]byte("ledger"))},
		VerifiedAtUnix: 1_800_000_123, IssuerKeyID: "hepta-receipt-v1",
	}
	message, err := receipt.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	receipt.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, message))
	return receipt, privateKey.Public().(ed25519.PublicKey)
}

func TestHeptaCompletionReceiptUsesPinnedIssuerAndIndependentDomainFrame(t *testing.T) {
	receipt, publicKey := signedReceiptFixture(t)
	trusted := map[string]ed25519.PublicKey{receipt.IssuerKeyID: publicKey}
	if err := receipt.Verify(trusted); err != nil {
		t.Fatal(err)
	}
	if err := receipt.Verify(map[string]ed25519.PublicKey{}); err == nil {
		t.Fatal("response issuer_key_id was treated as a trust source")
	}

	mutations := map[string]func(*SignedHeptaCompletionReceiptV1){
		"commitment":         func(value *SignedHeptaCompletionReceiptV1) { value.CommitmentID = NewDigest([]byte("other")) },
		"ruleset":            func(value *SignedHeptaCompletionReceiptV1) { value.RulesetHash = NewDigest([]byte("other")) },
		"challenge snapshot": func(value *SignedHeptaCompletionReceiptV1) { value.ChallengeSnapshotHash = NewDigest([]byte("other")) },
		"authority":          func(value *SignedHeptaCompletionReceiptV1) { value.NakamaAuthorityKeyID = "other-authority" },
		"verified time":      func(value *SignedHeptaCompletionReceiptV1) { value.VerifiedAtUnix++ },
		"signature": func(value *SignedHeptaCompletionReceiptV1) {
			raw, _ := base64.StdEncoding.DecodeString(value.Signature)
			raw[0] ^= 1
			value.Signature = base64.StdEncoding.EncodeToString(raw)
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			changed := receipt
			mutate(&changed)
			if err := changed.Verify(trusted); err == nil {
				t.Fatal("mutated signed receipt was accepted")
			}
		})
	}
}
