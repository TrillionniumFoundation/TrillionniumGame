package worldcommand

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestCommitWithPersisterReceivesExactCandidate(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	request := testRequest("command-atomic", "participant-1", 1, map[string]any{"kind": "advance"})
	prepared, err := store.Prepare(context.Background(), request, time.Unix(1_800_000_001, 0))
	if err != nil {
		t.Fatal(err)
	}
	reservation, err := store.BeginAttempt(context.Background(), *prepared.Reservation, time.Unix(1_800_000_002, 0))
	if err != nil {
		t.Fatal(err)
	}
	verified, err := fakeCodec{}.Verify(reservation.Transition, reservation.Context, []byte("accepted"))
	if err != nil {
		t.Fatal(err)
	}

	called := false
	receipt, err := store.CommitWith(
		context.Background(),
		reservation,
		verified,
		time.Unix(1_800_000_003, 0),
		func(_ context.Context, key, expectedVersion string, payload []byte, candidate Receipt, state MatchState) (string, error) {
			called = true
			if key != "match-1" || expectedVersion != "3" {
				t.Fatalf("unexpected CAS identity: key=%q version=%q", key, expectedVersion)
			}
			if len(payload) == 0 || candidate.ClientCommandID != request.ClientCommandID {
				t.Fatal("atomic persister did not receive exact candidate material")
			}
			if state.MatchVersion != 4 || state.NextGlobalEventSequence != 4 || state.StateRevision != 2 {
				t.Fatalf("candidate authority state did not advance exactly once: %+v", state)
			}
			return "4", nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if !called || receipt.MatchVersion != 4 || receipt.EventSequence == nil || *receipt.EventSequence != 3 {
		t.Fatalf("unexpected atomic commit receipt: %+v", receipt)
	}
	if _, exists := store.Reservation(request.ClientCommandID); exists {
		t.Fatal("committed reservation remained pending")
	}
}

func TestCommitWithPersisterFailureLeavesStoreUnchanged(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	request := testRequest("command-rollback", "participant-1", 1, map[string]any{"kind": "advance"})
	prepared, err := store.Prepare(context.Background(), request, time.Unix(1_800_000_010, 0))
	if err != nil {
		t.Fatal(err)
	}
	reservation, err := store.BeginAttempt(context.Background(), *prepared.Reservation, time.Unix(1_800_000_011, 0))
	if err != nil {
		t.Fatal(err)
	}
	verified, err := fakeCodec{}.Verify(reservation.Transition, reservation.Context, []byte("accepted"))
	if err != nil {
		t.Fatal(err)
	}
	before := store.State()
	injected := errors.New("injected sidecar persistence failure")
	_, err = store.CommitWith(
		context.Background(),
		reservation,
		verified,
		time.Unix(1_800_000_012, 0),
		func(context.Context, string, string, []byte, Receipt, MatchState) (string, error) {
			return "", injected
		},
	)
	if !errors.Is(err, injected) {
		t.Fatalf("expected injected failure, got %v", err)
	}
	after := store.State()
	if after.MatchVersion != before.MatchVersion || after.NextGlobalEventSequence != before.NextGlobalEventSequence ||
		after.StateRevision != before.StateRevision || after.StateHash != before.StateHash || after.Tick != before.Tick {
		t.Fatalf("store changed after failed atomic persistence: before=%+v after=%+v", before, after)
	}
	if _, exists := store.Reservation(request.ClientCommandID); !exists {
		t.Fatal("failed atomic persistence removed the pending reservation")
	}
	if _, exists := store.Receipt(request.ClientCommandID); exists {
		t.Fatal("failed atomic persistence created a receipt")
	}
}
