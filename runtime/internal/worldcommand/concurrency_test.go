package worldcommand

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestConcurrentReservationsProduceOneCommitAndOneStale(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	firstRequest := testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"})
	secondRequest := testRequest("command-2", "participant-2", 1, map[string]any{"kind": "advance"})
	first, err := store.Prepare(context.Background(), firstRequest, time.Unix(1, 0))
	if err != nil {
		t.Fatal(err)
	}
	second, err := store.Prepare(context.Background(), secondRequest, time.Unix(2, 0))
	if err != nil {
		t.Fatal(err)
	}
	firstVerified, _ := fakeCodec{}.Verify(first.Reservation.Transition, first.Reservation.Context, []byte("accepted"))
	if _, err := store.Commit(context.Background(), *first.Reservation, firstVerified, time.Unix(3, 0)); err != nil {
		t.Fatal(err)
	}
	before := store.State()
	secondVerified, _ := fakeCodec{}.Verify(second.Reservation.Transition, second.Reservation.Context, []byte("accepted"))
	if _, err := store.Commit(context.Background(), *second.Reservation, secondVerified, time.Unix(4, 0)); !errors.Is(err, ErrStaleReservation) {
		t.Fatalf("expected stale second reservation, got %v", err)
	}
	after := store.State()
	if before.MatchVersion != after.MatchVersion || before.StateRevision != after.StateRevision || before.StateHash != after.StateHash || before.NextGlobalEventSequence != after.NextGlobalEventSequence {
		t.Fatalf("stale commit mutated authority state: before=%+v after=%+v", before, after)
	}
	status := store.Status(time.Unix(5, 0))
	if status.RetiredReservations != 1 || status.Receipts != 1 {
		t.Fatalf("stale generation not retired: %+v", status)
	}
}

func TestTwoWorkersConvergeOnOneReceipt(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := newBarrierExecutor()
	coordinator := Coordinator{
		Store:    store,
		Executor: executor,
		Clock:    func() time.Time { return time.Unix(1_800_000_000, 0).UTC() },
	}
	request := testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"})

	type result struct {
		receipt Receipt
		err     error
	}
	results := make(chan result, 2)
	for range 2 {
		go func() {
			receipt, err := coordinator.Execute(context.Background(), request)
			results <- result{receipt: receipt, err: err}
		}()
	}
	select {
	case <-executor.ready:
	case <-time.After(5 * time.Second):
		t.Fatal("workers did not both reach external execution")
	}
	close(executor.release)
	first := <-results
	second := <-results
	if first.err != nil || second.err != nil {
		t.Fatalf("workers failed to converge: first=%v second=%v", first.err, second.err)
	}
	if executor.requestCount() != 2 || first.receipt.ReservationID != second.receipt.ReservationID || first.receipt.EventSequence == nil || second.receipt.EventSequence == nil || *first.receipt.EventSequence != *second.receipt.EventSequence {
		t.Fatalf("workers did not converge on one canonical receipt: first=%+v second=%+v calls=%d", first.receipt, second.receipt, executor.requestCount())
	}
	state := store.State()
	if state.MatchVersion != testState().MatchVersion+1 || state.NextGlobalEventSequence != testState().NextGlobalEventSequence+1 || state.StateRevision != testState().StateRevision+1 {
		t.Fatalf("duplicate workers advanced state more than once: %+v", state)
	}
	stored, ok := store.Receipt("command-1")
	if !ok || len(stored.Attempts) != 2 {
		t.Fatalf("receipt did not retain both execution attempts: %+v", stored)
	}
	for _, attempt := range stored.Attempts {
		if attempt.FinishedAtUnix == nil {
			t.Fatalf("receipt contains unfinished attempt: %+v", attempt)
		}
	}
}

func TestSeparateStoreWritersFailClosedOnCASConflict(t *testing.T) {
	backend := NewMemoryBackend()
	firstStore := newTestStore(t, backend)
	secondStore, err := OpenStore(context.Background(), "match-1", backend, fakeCodec{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := firstStore.Prepare(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}), time.Unix(1, 0)); err != nil {
		t.Fatal(err)
	}
	if _, err := secondStore.Prepare(context.Background(), testRequest("command-2", "participant-2", 1, map[string]any{"kind": "advance"}), time.Unix(2, 0)); !errors.Is(err, ErrVersionConflict) {
		t.Fatalf("separate stale writer did not fail closed on CAS conflict: %v", err)
	}
	if _, ok := secondStore.Reservation("command-2"); ok {
		t.Fatal("stale writer installed an unpersisted reservation")
	}
}
