package worldcommand

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestCommittedDuplicateDoesNotCallWorld(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
	request := testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"})
	first, err := coordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	second, err := coordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if executor.count() != 1 || first.ReservationID != second.ReservationID || first.RequestHash != second.RequestHash {
		t.Fatalf("duplicate was not short-circuited: first=%+v second=%+v calls=%d", first, second, executor.count())
	}
}

func TestSameCommandIDDifferentIntentFailsClosed(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	request := testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"})
	if _, err := store.Prepare(context.Background(), request, time.Unix(1, 0)); err != nil {
		t.Fatal(err)
	}
	request.Command = map[string]any{"kind": "attack"}
	if _, err := store.Prepare(context.Background(), request, time.Unix(2, 0)); !errors.Is(err, ErrConflict) {
		t.Fatalf("expected idempotency conflict, got %v", err)
	}
}

func TestCommittedDuplicateSurvivesAdvancedAuthorityCursor(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
	request := testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"})
	first, err := coordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}

	advanced := store.State()
	request.ExpectedStateRevision = advanced.StateRevision
	request.ExpectedStateHash = advanced.StateHash
	request.Context.MatchVersion = int64(advanced.MatchVersion)
	request.Context.GlobalEventSequence = int64(advanced.NextGlobalEventSequence)
	request.Context.ExpectedTick = advanced.Tick
	second, err := coordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if executor.count() != 1 || first.ReservationID != second.ReservationID || first.IntentFingerprint != second.IntentFingerprint {
		t.Fatalf("advanced duplicate did not replay committed receipt: first=%+v second=%+v calls=%d", first, second, executor.count())
	}
}

func TestCommittedReceiptClosesAttemptEvidence(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	coordinator := Coordinator{Store: store, Executor: executor, Clock: fixedClock()}
	receipt, err := coordinator.Execute(context.Background(), testRequest("command-1", "participant-1", 1, map[string]any{"kind": "hold"}))
	if err != nil {
		t.Fatal(err)
	}
	if receipt.CanonicalResultSHA256 == "" || receipt.ReplayHash == nil || len(receipt.Attempts) != 1 || receipt.Attempts[0].FinishedAtUnix == nil {
		t.Fatalf("receipt lost result or attempt evidence: %+v", receipt)
	}
	if receipt.Attempts[0].FailureKind != "" || receipt.Attempts[0].Retryable || receipt.Attempts[0].Detail != "committed accepted" {
		t.Fatalf("committed attempt evidence is ambiguous: %+v", receipt.Attempts[0])
	}
	if store.Status(time.Unix(1_900_000_000, 0)).TotalAttempts != 1 {
		t.Fatal("status did not retain committed attempt count")
	}
}
