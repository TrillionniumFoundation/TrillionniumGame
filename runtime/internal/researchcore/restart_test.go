package researchcore

import (
	"crypto/ed25519"
	"encoding/binary"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
)

func decodeResearchSnapshotForTest(t *testing.T, snapshot []byte) (snapshotDocument, []byte) {
	t.Helper()
	start := len(snapshotMagic) + 8
	size := int(binary.BigEndian.Uint64(snapshot[len(snapshotMagic):start]))
	end := start + size
	var document snapshotDocument
	if err := json.Unmarshal(snapshot[start:end], &document); err != nil {
		t.Fatal(err)
	}
	return document, append([]byte(nil), snapshot[end+32:]...)
}

func restoreOptionsFor(f *fixture) RestoreOptions {
	return RestoreOptions{
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-test": f.issuerPrivate.Public().(ed25519.PublicKey)},
		AuthorityKeyID:    "nakama-research-test", AuthorityPrivateKey: f.authority,
		AuthorityPublicKeys: map[string]ed25519.PublicKey{"nakama-research-test": f.authority.Public().(ed25519.PublicKey)},
	}
}

func TestSignedSnapshotRestartFencesLiveConnectionsAndCatchesUp(t *testing.T) {
	f := newFixture(t, 3)
	f.joinAll()
	f.readyAll(f.now.Add(10 * time.Second))
	action := f.action(1, researchcontract.ActionCheckpointRecorded, "trnm.paper-raid.checkpoint.v1",
		researchcontract.NewDigest([]byte("checkpoint")), f.now.Add(20*time.Second))
	if _, err := f.engine.ApplyAction(fixtureUserID(1), action, f.now.Add(20*time.Second)); err != nil {
		t.Fatal(err)
	}
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	restored, err := Restore(snapshot, restoreOptionsFor(f))
	if err != nil {
		t.Fatal(err)
	}
	before := restored.View().EventCount
	events, err := restored.FenceAllConnections(f.now.Add(21 * time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 3 || restored.View().Status != StatusPaused || restored.View().EventCount != before+3 {
		t.Fatal("runtime restart did not durably fence all live connections")
	}
	second, err := restored.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	restoredAgain, err := Restore(second, restoreOptionsFor(f))
	if err != nil {
		t.Fatal(err)
	}
	for _, participant := range restoredAgain.View().Participants {
		if participant.Connected {
			t.Fatal("disconnected participant resurrected after restart")
		}
	}
}

func TestResearchRestartUsesPublicRegistryForHistoryAndActiveKeyForContinuation(t *testing.T) {
	f := newFixture(t, 3)
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	active := ed25519.NewKeyFromSeed(seed(221))
	options := RestoreOptions{
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-test": f.issuerPrivate.Public().(ed25519.PublicKey)},
		AuthorityKeyID:    "nakama-research-next", AuthorityPrivateKey: active,
		AuthorityPublicKeys: map[string]ed25519.PublicKey{
			"nakama-research-test": f.authority.Public().(ed25519.PublicKey),
			"nakama-research-next": active.Public().(ed25519.PublicKey),
		},
	}
	restored, err := Restore(snapshot, options)
	if err != nil {
		t.Fatalf("historical research snapshot was not restored without its private key: %v", err)
	}
	if restored.authorityKeyID != "nakama-research-next" || !restored.authorityPrivateKey.Equal(active) {
		t.Fatal("restored research session did not continue with the active signing key")
	}
	continued, err := restored.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	continuedDocument, _ := decodeResearchSnapshotForTest(t, continued)
	if continuedDocument.AuthorityKeyID != "nakama-research-next" ||
		!ed25519.PublicKey(continuedDocument.AuthorityPublicKey).Equal(active.Public().(ed25519.PublicKey)) {
		t.Fatal("continued research snapshot was not signed by the active authority")
	}
	delete(options.AuthorityPublicKeys, "nakama-research-test")
	if _, err := Restore(snapshot, options); err == nil {
		t.Fatal("historical research snapshot was restored after public-key removal")
	}
}

func TestSnapshotRejectsTamperAndWrongAuthority(t *testing.T) {
	f := newFixture(t, 4)
	f.joinAll()
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	tampered := append([]byte(nil), snapshot...)
	tampered[len(tampered)/2] ^= 1
	if _, err := Restore(tampered, restoreOptionsFor(f)); err == nil {
		t.Fatal("tampered snapshot accepted")
	}
	wrong := restoreOptionsFor(f)
	wrong.AuthorityPrivateKey = ed25519.NewKeyFromSeed(seed(220))
	if _, err := Restore(snapshot, wrong); err == nil {
		t.Fatal("snapshot accepted under wrong authority")
	}
}

func TestCompletedSnapshotPreservesIndependentRoots(t *testing.T) {
	f := newFixture(t, 3)
	f.joinAll()
	f.readyAll(f.now.Add(10 * time.Second))
	action := f.action(1, researchcontract.ActionArtifactPublished, "trnm.paper-raid.artifact-manifest.v1",
		researchcontract.NewDigest([]byte("manifest")), f.now.Add(20*time.Second))
	if _, err := f.engine.ApplyAction(fixtureUserID(1), action, f.now.Add(20*time.Second)); err != nil {
		t.Fatal(err)
	}
	release := researchcontract.NewDigest([]byte("release"))
	f.acknowledgeAll(release, f.now.Add(30*time.Second))
	facts := researchcontract.TerminalFacts{ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("bundle")), PaperReleaseCandidateHash: release, ContributionLedgerHash: researchcontract.NewDigest([]byte("ledger"))}
	if _, err := f.engine.Complete(facts, f.now.Add(40*time.Second)); err != nil {
		t.Fatal(err)
	}
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	restored, err := Restore(snapshot, restoreOptionsFor(f))
	if err != nil {
		t.Fatal(err)
	}
	completion, ok := restored.Completion()
	if !ok {
		t.Fatal("completion missing")
	}
	eventRoot, err := researchcontract.EventRoot(restored.Events())
	if err != nil {
		t.Fatal(err)
	}
	archiveHash, err := researchcontract.ArchiveHash(restored.Events())
	if err != nil {
		t.Fatal(err)
	}
	rosterRoot, err := researchcontract.RosterRoot(restored.View().SessionID, restored.View().TeamID,
		restored.View().PaperProjectID, restored.View().RosterVersion, restored.Roster())
	if err != nil {
		t.Fatal(err)
	}
	if completion.EventRoot != eventRoot || completion.ArchiveHash != archiveHash || completion.RosterRoot != rosterRoot {
		t.Fatal("restored completion roots do not independently recompute")
	}

	active := ed25519.NewKeyFromSeed(seed(222))
	rotatedOptions := RestoreOptions{
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-test": f.issuerPrivate.Public().(ed25519.PublicKey)},
		AuthorityKeyID:    "nakama-research-next", AuthorityPrivateKey: active,
		AuthorityPublicKeys: map[string]ed25519.PublicKey{
			"nakama-research-test": f.authority.Public().(ed25519.PublicKey),
			"nakama-research-next": active.Public().(ed25519.PublicKey),
		},
	}
	rotated, err := Restore(snapshot, rotatedOptions)
	if err != nil {
		t.Fatalf("completed historical snapshot was not verified from the public registry: %v", err)
	}
	completionPublic, ok := rotated.CompletionAuthorityPublicKey()
	if !ok || !completionPublic.Equal(f.authority.Public().(ed25519.PublicKey)) {
		t.Fatal("historical completion did not retain its K0 verification key")
	}
	resigned, err := rotated.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	resignedDocument, _ := decodeResearchSnapshotForTest(t, resigned)
	if resignedDocument.AuthorityKeyID != "nakama-research-next" ||
		!ed25519.PublicKey(resignedDocument.AuthorityPublicKey).Equal(active.Public().(ed25519.PublicKey)) {
		t.Fatal("restored completed snapshot was not re-signed with active K1")
	}
	if _, err := Restore(resigned, rotatedOptions); err != nil {
		t.Fatalf("K1 snapshot containing a K0 completion was not restorable: %v", err)
	}
	delete(rotatedOptions.AuthorityPublicKeys, "nakama-research-test")
	if _, err := Restore(resigned, rotatedOptions); err == nil ||
		!errors.Is(err, ErrAuthorityVerificationKeyUnavailable) {
		t.Fatalf("K1 snapshot containing a K0 completion did not fail with the explicit missing-key sentinel: %v", err)
	}
}
