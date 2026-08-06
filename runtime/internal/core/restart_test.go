package core

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"reflect"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
)

func restoreOptions(f *coreFixture) RestoreOptions {
	return RestoreOptions{TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
		AuthorityKeyID: "nakama-authority-1", AuthorityPrivateKey: f.authorityPrivate}
}

func TestRestartPreservesIdempotencySequencesAndEvidence(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	command := f.command(t, 1, "cmd-1", 1, 3, "move-a")
	result, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	restored, err := Restore(snapshot, restoreOptions(f))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(f.engine.View(), restored.View()) || !reflect.DeepEqual(f.engine.Events(), restored.Events()) {
		t.Fatal("restored state differs from original")
	}
	replay, err := restored.ApplyCommand("user-1", command, f.now.Add(2*time.Hour))
	if err != nil || !replay.Replay || !reflect.DeepEqual(result.Event, replay.Event) {
		t.Fatalf("idempotent command was not preserved: %+v %v", replay, err)
	}
	commandTwo := f.command(t, 1, "cmd-2", 2, restored.View().Version, "move-b")
	if _, err := restored.ApplyCommand("user-1", commandTwo, f.now.Add(2*time.Hour)); err != nil {
		t.Fatalf("continuous command after restart failed: %v", err)
	}
	facts := contract.TerminalFacts{ResultCode: "decisive", WinnerSlot: 1, OutcomeHash: contract.NewDigest([]byte("outcome"))}
	completion, err := restored.Complete(facts, f.now.Add(2*time.Hour+time.Second))
	if err != nil {
		t.Fatal(err)
	}
	completedSnapshot, err := restored.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	restoredAgain, err := Restore(completedSnapshot, restoreOptions(f))
	if err != nil {
		t.Fatal(err)
	}
	restoredCompletion, ok := restoredAgain.Completion()
	if !ok || !reflect.DeepEqual(completion, *restoredCompletion) {
		t.Fatal("signed completion changed across restart")
	}
	retry, err := restoredAgain.Complete(facts, f.now.Add(3*time.Hour))
	if err != nil || !reflect.DeepEqual(completion, retry) {
		t.Fatal("completion retry after restart was not byte-identical")
	}
}

func TestRestartAcceptsRetiringAuthorityFromOverlapRing(t *testing.T) {
	f := newCoreFixture(t)
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	_, active := fixtureKey("next-authority")
	options := RestoreOptions{
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
		AuthorityKeyID:    "nakama-authority-2", AuthorityPrivateKey: active,
		AuthorityPrivateKeys: map[string]ed25519.PrivateKey{"nakama-authority-1": f.authorityPrivate, "nakama-authority-2": active},
	}
	restored, err := Restore(snapshot, options)
	if err != nil {
		t.Fatalf("retiring authority snapshot was not restored: %v", err)
	}
	if restored.authorityKeyID != "nakama-authority-1" {
		t.Fatal("restored match was silently rekeyed")
	}
	delete(options.AuthorityPrivateKeys, "nakama-authority-1")
	if _, err := Restore(snapshot, options); err == nil {
		t.Fatal("retiring authority snapshot was restored after key removal")
	}
}

func TestRestartFailsClosedOnCorruptTruncatedOrWrongKey(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	corrupt := append([]byte(nil), snapshot...)
	corrupt[len(corrupt)/2] ^= 0x01
	if _, err := Restore(corrupt, restoreOptions(f)); err == nil {
		t.Fatal("checksum-corrupt snapshot was accepted")
	}
	if _, err := Restore(snapshot[:len(snapshot)-1], restoreOptions(f)); err == nil {
		t.Fatal("truncated snapshot was accepted")
	}
	legacy := append([]byte(nil), snapshot...)
	legacy[len(snapshotMagic)-1] = '1'
	if _, err := Restore(legacy, restoreOptions(f)); err == nil {
		t.Fatal("legacy unauthenticated snapshot format was accepted")
	}
	_, wrongPrivate := fixtureKey("wrong-authority")
	wrong := restoreOptions(f)
	wrong.AuthorityPrivateKey = wrongPrivate
	if _, err := Restore(snapshot, wrong); err == nil {
		t.Fatal("snapshot was restored with the wrong authority key")
	}

	document, originalSignature := decodeSnapshotDocumentForTest(t, snapshot)
	document.Version++
	semanticCorruption := encodeSnapshotDocumentForTest(t, document, "nakama-authority-1", f.authorityPrivate, true, nil)
	if _, err := Restore(semanticCorruption, restoreOptions(f)); err == nil {
		// The exact error category is deliberately not part of the persistence contract.
		t.Fatal("semantically inconsistent snapshot was accepted")
	}

	// A checksum is only a corruption detector. Recomputing it after changing
	// the payload must not bypass the authority signature.
	document.Version++
	forgedChecksum := encodeSnapshotDocumentForTest(t, document, "nakama-authority-1", f.authorityPrivate, false, originalSignature)
	if _, err := Restore(forgedChecksum, restoreOptions(f)); err == nil {
		t.Fatal("payload tampering with a recomputed checksum bypassed snapshot authentication")
	}
}

func TestRestartRejectsNonCanonicalReplaySemantics(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	command := f.command(t, 1, "cmd-1", 1, 3, "move-a")
	if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second)); err != nil {
		t.Fatal(err)
	}
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}

	tests := map[string]func(*snapshotDocument){
		"record event differs from archive": func(document *snapshotDocument) {
			document.Commands[0].Event.Payload = []byte("record-only-tamper")
			document.Commands[0].Event, _ = contract.SealEvent(document.Commands[0].Event)
		},
		"event match version is not sequence plus one": func(document *snapshotDocument) {
			document.Events[2].MatchVersion = 99
			document.Events[2], _ = contract.SealEvent(document.Events[2])
			document.Commands[0].Event = document.Events[2]
		},
		"archive identity differs from snapshot": func(document *snapshotDocument) {
			for index := range document.Events {
				document.Events[index].MatchID = "match-2"
				document.Events[index].EventID, _ = contract.CanonicalEventID("match-2", document.Events[index].Sequence, document.Events[index].CausationID)
				document.Events[index], _ = contract.SealEvent(document.Events[index])
			}
			document.Commands[0].Event = document.Events[2]
		},
		"join type is forged": func(document *snapshotDocument) {
			document.Events[0].EventType = "agent_command_applied"
			document.Events[0], _ = contract.SealEvent(document.Events[0])
		},
		"join payload is forged": func(document *snapshotDocument) {
			document.Events[0].Payload = []byte("forged-join")
			document.Events[0], _ = contract.SealEvent(document.Events[0])
		},
		"join causation is forged": func(document *snapshotDocument) {
			document.Events[0].CausationID = "auth-2"
			document.Events[0].EventID, _ = contract.CanonicalEventID("match-1", 1, "auth-2")
			document.Events[0], _ = contract.SealEvent(document.Events[0])
		},
		"command expected version is not global version": func(document *snapshotDocument) {
			document.Commands[0].Command.ExpectedMatchVersion = 2
			document.Commands[0].Command, _ = contract.SignCommand(document.Commands[0].Command, f.agentPrivate[0])
			document.Commands[0].Fingerprint, _ = contract.CommandFingerprint(document.Commands[0].Command)
		},
		"participant command sequence has a gap": func(document *snapshotDocument) {
			document.Commands[0].Command.ParticipantSequence = 2
			document.Commands[0].Command, _ = contract.SignCommand(document.Commands[0].Command, f.agentPrivate[0])
			document.Commands[0].Fingerprint, _ = contract.CommandFingerprint(document.Commands[0].Command)
		},
		"event time regresses": func(document *snapshotDocument) {
			document.Events[2].OccurredAtUnix = document.Events[0].OccurredAtUnix - 1
			document.Events[2], _ = contract.SealEvent(document.Events[2])
			document.Commands[0].Event = document.Events[2]
		},
		"command event payload differs from signed command": func(document *snapshotDocument) {
			document.Events[2].Payload = []byte("forged-command-payload")
			document.Events[2], _ = contract.SealEvent(document.Events[2])
			document.Commands[0].Event = document.Events[2]
		},
		"one command is mapped twice": func(document *snapshotDocument) {
			document.Commands = append(document.Commands, document.Commands[0])
		},
	}

	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			document, _ := decodeSnapshotDocumentForTest(t, snapshot)
			mutate(&document)
			forged := encodeSnapshotDocumentForTest(t, document, "nakama-authority-1", f.authorityPrivate, true, nil)
			if _, err := Restore(forged, restoreOptions(f)); err == nil {
				t.Fatal("semantically forged snapshot was accepted")
			}
		})
	}
}

func TestRestartAcceptsDeterministicReverseJoinOrder(t *testing.T) {
	f := newCoreFixture(t)
	if _, err := f.engine.Join("user-2", "auth-2", f.now.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, err := f.engine.Join("user-1", "auth-1", f.now.Add(2*time.Second)); err != nil {
		t.Fatal(err)
	}
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Restore(snapshot, restoreOptions(f)); err != nil {
		t.Fatalf("valid reverse join order was rejected: %v", err)
	}
}

func TestRestartRejectsSignedCompletionFactsThatDifferFromTerminalEvent(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	command := f.command(t, 1, "cmd-1", 1, 3, "move-a")
	if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second)); err != nil {
		t.Fatal(err)
	}
	facts := contract.TerminalFacts{ResultCode: "decisive", WinnerSlot: 1, OutcomeHash: contract.NewDigest([]byte("outcome"))}
	if _, err := f.engine.Complete(facts, f.now.Add(4*time.Second)); err != nil {
		t.Fatal(err)
	}
	snapshot, err := f.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	document, _ := decodeSnapshotDocumentForTest(t, snapshot)
	document.Completion.TerminalFacts.WinnerSlot = 2
	resigned, err := contract.SignCompletion(*document.Completion, f.authorityPrivate)
	if err != nil {
		t.Fatal(err)
	}
	document.Completion = &resigned
	forged := encodeSnapshotDocumentForTest(t, document, "nakama-authority-1", f.authorityPrivate, true, nil)
	if _, err := Restore(forged, restoreOptions(f)); err == nil {
		t.Fatal("completion facts that differ from the terminal event were accepted")
	}
}

func decodeSnapshotDocumentForTest(t *testing.T, snapshot []byte) (snapshotDocument, []byte) {
	t.Helper()
	var document snapshotDocument
	payloadStart := len(snapshotMagic) + 8
	payloadLength := int(binary.BigEndian.Uint64(snapshot[len(snapshotMagic):payloadStart]))
	if err := json.Unmarshal(snapshot[payloadStart:payloadStart+payloadLength], &document); err != nil {
		t.Fatal(err)
	}
	signatureStart := payloadStart + payloadLength + sha256.Size
	return document, append([]byte(nil), snapshot[signatureStart:]...)
}

func encodeSnapshotDocumentForTest(t *testing.T, document snapshotDocument, authorityKeyID string, privateKey ed25519.PrivateKey, resign bool, signature []byte) []byte {
	t.Helper()
	payload, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	checksum := sha256.Sum256(append([]byte("trnm_match_snapshot_checksum_v2\x00"), payload...))
	if resign {
		message, err := snapshotSigningBytes(authorityKeyID, payload, checksum)
		if err != nil {
			t.Fatal(err)
		}
		signature = ed25519.Sign(privateKey, message)
	}
	if len(signature) != ed25519.SignatureSize {
		t.Fatal("test snapshot signature has invalid length")
	}
	out := append([]byte(nil), snapshotMagic[:]...)
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(payload)))
	out = append(out, size[:]...)
	out = append(out, payload...)
	out = append(out, checksum[:]...)
	return append(out, signature...)
}
