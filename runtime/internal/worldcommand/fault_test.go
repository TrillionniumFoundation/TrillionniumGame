package worldcommand

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"testing"
	"time"
)

func TestResponseLossReusesExactRequestAcrossRestart(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := &scriptedExecutor{steps: []scriptStep{
		{err: &ExecutionError{Kind: FailureAmbiguousCommit, Retryable: true, Err: errors.New("response lost")}},
		{result: []byte("accepted")},
	}}
	coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
	request := testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"})

	if _, err := coordinator.Execute(context.Background(), request); err == nil {
		t.Fatal("ambiguous response loss unexpectedly succeeded")
	}
	pending, ok := store.Reservation("command-1")
	if !ok || len(pending.Attempts) != 1 || pending.Attempts[0].FailureKind != FailureAmbiguousCommit {
		t.Fatalf("ambiguous reservation was not retained: %+v", pending)
	}

	restored, err := OpenStore(context.Background(), "match-1", backend, fakeCodec{})
	if err != nil {
		t.Fatal(err)
	}
	coordinator.Store = restored
	receipt, err := coordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if receipt.Disposition != DispositionAccepted || executor.count() != 2 {
		t.Fatalf("unexpected retry result: %+v calls=%d", receipt, executor.count())
	}
	if !bytes.Equal(executor.requests[0], executor.requests[1]) {
		t.Fatal("ambiguous retry changed canonical World request bytes")
	}
}

func TestTakeoverRejectsPreviousGeneration(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	result, err := store.Prepare(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}), time.Unix(1, 0))
	if err != nil {
		t.Fatal(err)
	}
	old := *result.Reservation
	next, err := store.Takeover(context.Background(), old.ClientCommandID, old.Generation, "lease expired", time.Unix(2, 0))
	if err != nil {
		t.Fatal(err)
	}
	if next.Generation != old.Generation+1 || bytes.Equal(next.Transition.CanonicalRequest, nil) || !bytes.Equal(next.Transition.CanonicalRequest, old.Transition.CanonicalRequest) {
		t.Fatalf("takeover changed request identity: old=%+v next=%+v", old, next)
	}
	verified, _ := fakeCodec{}.Verify(old.Transition, old.Context, []byte("accepted"))
	if _, err := store.Commit(context.Background(), old, verified, time.Unix(3, 0)); !errors.Is(err, ErrStaleReservation) {
		t.Fatalf("old generation committed after takeover: %v", err)
	}
}

func TestPersistenceFailureLeavesStateAndReservationUnchanged(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	prepared, err := store.Prepare(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}), time.Unix(1, 0))
	if err != nil {
		t.Fatal(err)
	}
	before := store.State()
	backend.FailNext(errors.New("storage unavailable"))
	verified, _ := fakeCodec{}.Verify(prepared.Reservation.Transition, prepared.Reservation.Context, []byte("accepted"))
	if _, err := store.Commit(context.Background(), *prepared.Reservation, verified, time.Unix(2, 0)); err == nil {
		t.Fatal("persistence failure unexpectedly committed")
	}
	after := store.State()
	if before.MatchVersion != after.MatchVersion || before.StateRevision != after.StateRevision || before.StateHash != after.StateHash || before.NextGlobalEventSequence != after.NextGlobalEventSequence {
		t.Fatalf("failed persistence mutated state: before=%+v after=%+v", before, after)
	}
	if _, ok := store.Reservation("command-1"); !ok {
		t.Fatal("failed persistence removed pending reservation")
	}
	if _, ok := store.Receipt("command-1"); ok {
		t.Fatal("failed persistence created a receipt")
	}
}

func TestRetryableRejectionAndInvalidResultStayPending(t *testing.T) {
	for _, tc := range []struct {
		name string
		raw  []byte
		want FailureKind
	}{
		{"retryable", []byte("retryable"), FailureRemoteRetryable},
		{"invalid", []byte("tampered"), FailureInvalidResult},
	} {
		t.Run(tc.name, func(t *testing.T) {
			backend := NewMemoryBackend()
			store := newTestStore(t, backend)
			executor := &scriptedExecutor{steps: []scriptStep{{result: tc.raw}}}
			coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
			_, err := coordinator.Execute(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}))
			if err == nil {
				t.Fatal("failure scenario unexpectedly succeeded")
			}
			reservation, ok := store.Reservation("command-1")
			if !ok || len(reservation.Attempts) != 1 || reservation.Attempts[0].FailureKind != tc.want {
				t.Fatalf("pending failure evidence mismatch: %+v", reservation)
			}
			if _, ok := store.Receipt("command-1"); ok {
				t.Fatal("failure scenario created receipt")
			}
		})
	}
}

func TestCancellationBeforeExecutePreservesReservation(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := coordinator.Execute(ctx, testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}))
	if err == nil {
		t.Fatal("cancelled execution unexpectedly succeeded")
	}
	if executor.count() != 0 {
		t.Fatal("executor ran after pre-execution cancellation")
	}
	if _, ok := store.Reservation("command-1"); !ok {
		t.Fatal("cancelled command lost reservation")
	}
}

func TestNonretryableRejectionCreatesReceiptWithoutAdvancingState(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	before := store.State()
	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("rejected")}}}
	coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
	receipt, err := coordinator.Execute(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "invalid"}))
	if err != nil {
		t.Fatal(err)
	}
	after := store.State()
	if receipt.Disposition != DispositionRejected || receipt.EventSequence != nil || receipt.ErrorCode == nil || *receipt.ErrorCode != "domain_rejected" {
		t.Fatalf("unexpected rejection receipt: %+v", receipt)
	}
	if before.MatchVersion != after.MatchVersion || before.NextGlobalEventSequence != after.NextGlobalEventSequence || before.StateRevision != after.StateRevision || before.StateHash != after.StateHash {
		t.Fatalf("rejection advanced authoritative state: before=%+v after=%+v", before, after)
	}
}

func TestAbortIsGenerationBoundAndNeverCreatesReceipt(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	prepared, err := store.Prepare(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}), time.Unix(1, 0))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Abort(context.Background(), "command-1", prepared.Reservation.Generation+1, "wrong generation", time.Unix(2, 0)); !errors.Is(err, ErrStaleReservation) {
		t.Fatalf("wrong generation abort was not fenced: %v", err)
	}
	if err := store.Abort(context.Background(), "command-1", prepared.Reservation.Generation, "operator quarantine", time.Unix(3, 0)); err != nil {
		t.Fatal(err)
	}
	if _, ok := store.Reservation("command-1"); ok {
		t.Fatal("aborted reservation still pending")
	}
	if _, ok := store.Receipt("command-1"); ok {
		t.Fatal("abort generated canonical receipt")
	}
	if store.Status(time.Unix(4, 0)).RetiredReservations != 1 {
		t.Fatal("abort did not preserve retired audit record")
	}
}

func TestCorruptedSnapshotFailsClosed(t *testing.T) {
	backend := NewMemoryBackend()
	_ = newTestStore(t, backend)
	if err := backend.Corrupt("match-1", func(payload []byte) []byte {
		sum := sha256.Sum256(payload)
		return []byte(hex.EncodeToString(sum[:]))
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := OpenStore(context.Background(), "match-1", backend, fakeCodec{}); err == nil {
		t.Fatal("corrupted persisted store was accepted")
	}
}
