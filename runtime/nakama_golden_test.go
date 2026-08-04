package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"os"
	"testing"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
)

func TestContractPublicGoldenVectors(t *testing.T) {
	type encodedKey struct {
		PublicKeyBase64 string `json:"public_key_base64"`
	}
	type valueAuthorization struct {
		Value contract.SignedAuthorization `json:"value"`
	}
	type valueEvent struct {
		Value contract.MatchEvent `json:"value"`
	}
	var fixture struct {
		Schema string `json:"schema"`
		Keys   struct {
			Issuer    encodedKey `json:"issuer"`
			Authority encodedKey `json:"authority"`
		} `json:"keys"`
		Authorizations []valueAuthorization `json:"authorizations"`
		Command        struct {
			Value       contract.CommandEnvelope `json:"value"`
			Fingerprint contract.Digest          `json:"fingerprint"`
		} `json:"command"`
		SealedEvents []valueEvent `json:"sealed_events"`
		EventMerkle  struct {
			EventHashes []contract.Digest `json:"event_hashes"`
			EventRoot   contract.Digest   `json:"event_root"`
		} `json:"event_merkle"`
		OddEventMerkle struct {
			EventHashes []contract.Digest `json:"event_hashes"`
			EventRoot   contract.Digest   `json:"event_root"`
		} `json:"odd_event_merkle"`
		Archive struct {
			ArchiveHash contract.Digest `json:"archive_hash"`
		} `json:"archive"`
		Commitment struct {
			CommitmentID contract.Digest `json:"commitment_id"`
		} `json:"commitment"`
		Completion struct {
			Value contract.MatchCompletedV1 `json:"value"`
		} `json:"completion"`
	}
	raw, err := os.ReadFile("../contracts/golden-vectors.json")
	if err != nil {
		t.Fatalf("read canonical public fixture: %v", err)
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("decode canonical public fixture: %v", err)
	}
	if fixture.Schema != "trnm.nakama.golden_vectors.v1" || len(fixture.Authorizations) != 2 || len(fixture.SealedEvents) != 4 {
		t.Fatal("canonical public fixture shape is invalid")
	}
	issuerPublic := decodeGoldenPublicKey(t, fixture.Keys.Issuer.PublicKeyBase64)
	trusted := map[string]ed25519.PublicKey{"issuer-key-golden-001": issuerPublic}
	for index, authorization := range fixture.Authorizations {
		if err := contract.VerifyAuthorization(authorization.Value, trusted, authorization.Value.Claim.IssuedAtUnix); err != nil {
			t.Fatalf("authorization %d does not match public fixture: %v", index, err)
		}
	}
	agentPublic := ed25519.PublicKey(fixture.Authorizations[0].Value.Claim.AgentPublicKey)
	if err := contract.VerifyCommand(fixture.Command.Value, agentPublic); err != nil {
		t.Fatalf("command signature does not match public fixture: %v", err)
	}
	fingerprint, err := contract.CommandFingerprint(fixture.Command.Value)
	if err != nil || fingerprint != fixture.Command.Fingerprint {
		t.Fatalf("command fingerprint mismatch: got %s want %s err=%v", fingerprint, fixture.Command.Fingerprint, err)
	}
	events := make([]contract.MatchEvent, len(fixture.SealedEvents))
	for index, sealed := range fixture.SealedEvents {
		if err := sealed.Value.Validate(); err != nil {
			t.Fatalf("event %d is invalid: %v", index, err)
		}
		if sealed.Value.EventHash != fixture.EventMerkle.EventHashes[index] {
			t.Fatalf("event %d hash differs from Merkle input", index)
		}
		events[index] = sealed.Value
	}
	eventRoot, err := contract.EventRoot(events)
	if err != nil || eventRoot != fixture.EventMerkle.EventRoot {
		t.Fatalf("event root mismatch: got %s want %s err=%v", eventRoot, fixture.EventMerkle.EventRoot, err)
	}
	if len(fixture.OddEventMerkle.EventHashes) != 3 {
		t.Fatal("odd public Merkle fixture must contain exactly three events")
	}
	for index := range fixture.OddEventMerkle.EventHashes {
		if fixture.OddEventMerkle.EventHashes[index] != events[index].EventHash {
			t.Fatalf("odd Merkle event %d is not sourced from the sealed fixture", index)
		}
	}
	oddRoot, err := contract.EventRoot(events[:3])
	if err != nil || oddRoot != fixture.OddEventMerkle.EventRoot {
		t.Fatalf("odd event root mismatch: got %s want %s err=%v", oddRoot, fixture.OddEventMerkle.EventRoot, err)
	}
	archiveHash, err := contract.ArchiveHash(events)
	if err != nil || archiveHash != fixture.Archive.ArchiveHash {
		t.Fatalf("archive hash mismatch: got %s want %s err=%v", archiveHash, fixture.Archive.ArchiveHash, err)
	}
	commitmentID, err := contract.CommitmentID(fixture.Completion.Value.MatchID, eventRoot, archiveHash)
	if err != nil || commitmentID != fixture.Commitment.CommitmentID || fixture.Completion.Value.CommitmentID != commitmentID {
		t.Fatalf("commitment mismatch: got %s fixture %s completion %s err=%v", commitmentID, fixture.Commitment.CommitmentID, fixture.Completion.Value.CommitmentID, err)
	}
	authorityPublic := decodeGoldenPublicKey(t, fixture.Keys.Authority.PublicKeyBase64)
	if err := contract.VerifyCompletion(fixture.Completion.Value, authorityPublic); err != nil {
		t.Fatalf("completion signature does not match public fixture: %v", err)
	}
}

func decodeGoldenPublicKey(t *testing.T, encoded string) ed25519.PublicKey {
	t.Helper()
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(decoded) != ed25519.PublicKeySize {
		t.Fatalf("invalid public fixture key: %v", err)
	}
	return ed25519.PublicKey(decoded)
}
