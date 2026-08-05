package researchcore

import (
	"crypto/ed25519"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
)

func restoreOptionsFor(f *fixture) RestoreOptions {
	return RestoreOptions{
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-test": f.issuerPrivate.Public().(ed25519.PublicKey)},
		AuthorityKeyID:    "nakama-research-test", AuthorityPrivateKey: f.authority,
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
}
