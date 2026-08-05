package researchcontract

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
)

func signedConsumptionReceiptFixture(t *testing.T) (SignedAuthorizationSetConsumptionReceiptV1, ed25519.PublicKey) {
	t.Helper()
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(index + 73)
	}
	privateKey := ed25519.NewKeyFromSeed(seed)
	receipt := SignedAuthorizationSetConsumptionReceiptV1{
		Schema: HeptaAuthorizationConsumptionReceiptSchema, SessionID: "research-consumption-test",
		TeamID: "30000000-0000-4000-8000-000000000001", PaperProjectID: "40000000-0000-4000-8000-000000000001",
		ChallengeID: "50000000-0000-4000-8000-000000000001", SessionRosterVersion: 2,
		RosterRoot: NewDigest([]byte("roster-v2")), AuthorizationIDs: []string{
			"10000000-0000-4000-8000-000000000101", "10000000-0000-4000-8000-000000000102", "10000000-0000-4000-8000-000000000103",
		}, ConsumedAtUnix: 1_800_000_321, IssuerKeyID: "hepta-consumption-v1",
	}
	message, err := receipt.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	receipt.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, message))
	return receipt, privateKey.Public().(ed25519.PublicKey)
}

func TestHeptaAuthorizationConsumptionReceiptBindsStateTransition(t *testing.T) {
	receipt, publicKey := signedConsumptionReceiptFixture(t)
	trusted := map[string]ed25519.PublicKey{receipt.IssuerKeyID: publicKey}
	if err := receipt.Verify(trusted); err != nil {
		t.Fatal(err)
	}
	mutations := map[string]func(*SignedAuthorizationSetConsumptionReceiptV1){
		"epoch":  func(value *SignedAuthorizationSetConsumptionReceiptV1) { value.SessionRosterVersion++ },
		"roster": func(value *SignedAuthorizationSetConsumptionReceiptV1) { value.RosterRoot = NewDigest([]byte("other")) },
		"order": func(value *SignedAuthorizationSetConsumptionReceiptV1) {
			value.AuthorizationIDs[0], value.AuthorizationIDs[1] = value.AuthorizationIDs[1], value.AuthorizationIDs[0]
		},
		"consumed time": func(value *SignedAuthorizationSetConsumptionReceiptV1) { value.ConsumedAtUnix++ },
		"signature": func(value *SignedAuthorizationSetConsumptionReceiptV1) {
			raw, _ := base64.StdEncoding.DecodeString(value.Signature)
			raw[0] ^= 1
			value.Signature = base64.StdEncoding.EncodeToString(raw)
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			changed := receipt
			changed.AuthorizationIDs = append([]string(nil), receipt.AuthorizationIDs...)
			mutate(&changed)
			if err := changed.Verify(trusted); err == nil {
				t.Fatal("mutated consumption receipt was accepted")
			}
		})
	}
}
