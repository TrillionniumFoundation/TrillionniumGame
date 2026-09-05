package worldcommand

import (
	"bytes"
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type recoveryOutcome struct {
	receipt Receipt
	err     error
}

type cancelUntilDoneExecutor struct {
	mu      sync.Mutex
	request []byte
	started chan struct{}
	once    sync.Once
}

func newCancelUntilDoneExecutor() *cancelUntilDoneExecutor {
	return &cancelUntilDoneExecutor{started: make(chan struct{})}
}

func (e *cancelUntilDoneExecutor) Execute(ctx context.Context, request []byte) ([]byte, error) {
	e.mu.Lock()
	e.request = append([]byte(nil), request...)
	e.mu.Unlock()
	e.once.Do(func() { close(e.started) })
	<-ctx.Done()
	return nil, ctx.Err()
}

func (e *cancelUntilDoneExecutor) requestBytes() []byte {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]byte(nil), e.request...)
}

type cancelOnSuccessExecutor struct {
	mu      sync.Mutex
	request []byte
	cancel  context.CancelFunc
}

func (e *cancelOnSuccessExecutor) Execute(_ context.Context, request []byte) ([]byte, error) {
	e.mu.Lock()
	e.request = append([]byte(nil), request...)
	e.mu.Unlock()
	e.cancel()
	return []byte("accepted"), nil
}

type blockingResultExecutor struct {
	mu      sync.Mutex
	request []byte
	result  []byte
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func newBlockingResultExecutor(result []byte) *blockingResultExecutor {
	return &blockingResultExecutor{
		result:  append([]byte(nil), result...),
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
}

func (e *blockingResultExecutor) Execute(_ context.Context, request []byte) ([]byte, error) {
	e.mu.Lock()
	e.request = append([]byte(nil), request...)
	e.mu.Unlock()
	e.once.Do(func() { close(e.started) })
	<-e.release
	return append([]byte(nil), e.result...), nil
}

func recoveryClock(start int64) func() time.Time {
	var mu sync.Mutex
	current := time.Unix(start, 0).UTC()
	return func() time.Time {
		mu.Lock()
		defer mu.Unlock()
		current = current.Add(time.Second)
		return current
	}
}

func TestCancellationDuringWorldExecutionPreservesExactRequestForRetry(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := newCancelUntilDoneExecutor()
	coordinator := Coordinator{
		Store:    store,
		Executor: executor,
		Clock:    recoveryClock(1_800_000_000),
	}
	request := testRequest("command-cancel-during", "participant-1", 1, map[string]any{"kind": "hold"})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan recoveryOutcome, 1)
	go func() {
		receipt, err := coordinator.Execute(ctx, request)
		done <- recoveryOutcome{receipt: receipt, err: err}
	}()

	select {
	case <-executor.started:
	case <-time.After(5 * time.Second):
		t.Fatal("World executor did not start")
	}
	cancel()

	var first recoveryOutcome
	select {
	case first = <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("cancelled World execution did not terminate")
	}
	var failure *ExecutionError
	if !errors.As(first.err, &failure) || failure.Kind != FailureCancelled || !failure.Retryable {
		t.Fatalf("execution cancellation was not classified retryably: %v", first.err)
	}
	pending, ok := store.Reservation(request.ClientCommandID)
	if !ok || len(pending.Attempts) != 1 {
		t.Fatalf("cancelled execution did not retain one pending attempt: %+v", pending)
	}
	if pending.Attempts[0].FailureKind != FailureCancelled || !pending.Attempts[0].Retryable || pending.Attempts[0].FinishedAtUnix == nil {
		t.Fatalf("cancelled attempt evidence is incomplete: %+v", pending.Attempts[0])
	}
	firstRequest := executor.requestBytes()
	if len(firstRequest) == 0 || !bytes.Equal(firstRequest, pending.Transition.CanonicalRequest) {
		t.Fatal("cancelled execution did not retain the exact canonical World request")
	}

	retryExecutor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	retryCoordinator := Coordinator{
		Store:    store,
		Executor: retryExecutor,
		Clock:    recoveryClock(1_800_000_100),
	}
	receipt, err := retryCoordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	retryExecutor.mu.Lock()
	if len(retryExecutor.requests) != 1 {
		retryExecutor.mu.Unlock()
		t.Fatalf("retry executed an unexpected request count: %d", len(retryExecutor.requests))
	}
	retryRequest := append([]byte(nil), retryExecutor.requests[0]...)
	retryExecutor.mu.Unlock()
	if !bytes.Equal(firstRequest, retryRequest) {
		t.Fatal("retry changed canonical World request identity")
	}
	if receipt.Generation != pending.Generation || len(receipt.Attempts) != 2 {
		t.Fatalf("retry receipt lost generation or attempt history: %+v", receipt)
	}
	if receipt.Attempts[0].FailureKind != FailureCancelled || receipt.Attempts[1].FailureKind != "" || receipt.Attempts[1].FinishedAtUnix == nil {
		t.Fatalf("retry receipt did not preserve cancellation then commit evidence: %+v", receipt.Attempts)
	}
	if _, exists := store.Reservation(request.ClientCommandID); exists {
		t.Fatal("successful retry left a pending reservation")
	}
}

func TestVerifiedWorldSuccessCommitsAfterCallerCancellation(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	ctx, cancel := context.WithCancel(context.Background())
	executor := &cancelOnSuccessExecutor{cancel: cancel}
	coordinator := Coordinator{
		Store:    store,
		Executor: executor,
		Clock:    recoveryClock(1_800_000_200),
	}
	request := testRequest("command-cancel-after-success", "participant-1", 1, map[string]any{"kind": "advance"})
	receipt, err := coordinator.Execute(ctx, request)
	if err != nil {
		t.Fatalf("verified World success was lost after caller cancellation: %v", err)
	}
	if !errors.Is(ctx.Err(), context.Canceled) {
		t.Fatalf("test executor did not cancel the caller context: %v", ctx.Err())
	}
	if receipt.Disposition != DispositionAccepted || receipt.EventSequence == nil {
		t.Fatalf("verified success did not produce an accepted receipt: %+v", receipt)
	}
	if _, exists := store.Reservation(request.ClientCommandID); exists {
		t.Fatal("verified success left a pending reservation after caller cancellation")
	}
	stored, exists := store.Receipt(request.ClientCommandID)
	if !exists || stored.ReservationID != receipt.ReservationID || stored.CanonicalResultSHA256 != receipt.CanonicalResultSHA256 {
		t.Fatalf("verified success was not durably replayable: %+v", stored)
	}

	duplicateExecutor := &scriptedExecutor{}
	duplicateCoordinator := Coordinator{
		Store:    store,
		Executor: duplicateExecutor,
		Clock:    recoveryClock(1_800_000_300),
	}
	duplicate, err := duplicateCoordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if duplicateExecutor.count() != 0 || duplicate.ReservationID != receipt.ReservationID || duplicate.CanonicalResultSHA256 != receipt.CanonicalResultSHA256 {
		t.Fatalf("post-cancellation duplicate did not replay the exact receipt: first=%+v duplicate=%+v calls=%d", receipt, duplicate, duplicateExecutor.count())
	}
}

func TestTakeoverGenerationAndStateTokenSurviveRestart(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	request := testRequest("command-takeover-restart", "participant-1", 1, map[string]any{"kind": "hold"})
	prepared, err := store.Prepare(context.Background(), request, time.Unix(1_800_000_400, 0))
	if err != nil {
		t.Fatal(err)
	}
	old := *prepared.Reservation
	next, err := store.Takeover(context.Background(), old.ClientCommandID, old.Generation, "worker lease expired", time.Unix(1_800_000_401, 0))
	if err != nil {
		t.Fatal(err)
	}
	if next.Generation != old.Generation+1 || next.StateToken == old.StateToken || next.ReservationID == old.ReservationID {
		t.Fatalf("takeover did not rotate generation identity: old=%+v next=%+v", old, next)
	}
	if !bytes.Equal(next.Transition.CanonicalRequest, old.Transition.CanonicalRequest) || next.Transition.RequestHash != old.Transition.RequestHash {
		t.Fatal("takeover changed the immutable World request")
	}

	restored, err := OpenStore(context.Background(), "match-1", backend, fakeCodec{})
	if err != nil {
		t.Fatal(err)
	}
	loaded, ok := restored.Reservation(request.ClientCommandID)
	if !ok || loaded.Generation != next.Generation || loaded.StateToken != next.StateToken || loaded.ReservationID != next.ReservationID {
		t.Fatalf("takeover identity did not survive restart: expected=%+v loaded=%+v", next, loaded)
	}
	if !bytes.Equal(loaded.Transition.CanonicalRequest, next.Transition.CanonicalRequest) {
		t.Fatal("restored takeover changed canonical request bytes")
	}
	oldVerified, err := fakeCodec{}.Verify(old.Transition, old.Context, []byte("accepted"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := restored.Commit(context.Background(), old, oldVerified, time.Unix(1_800_000_402, 0)); !errors.Is(err, ErrStaleReservation) {
		t.Fatalf("pre-takeover generation committed after restart: %v", err)
	}

	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	coordinator := Coordinator{
		Store:    restored,
		Executor: executor,
		Clock:    recoveryClock(1_800_000_500),
	}
	receipt, err := coordinator.Execute(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if receipt.Generation != next.Generation || receipt.ReservationID != next.ReservationID {
		t.Fatalf("restored takeover did not commit the active generation: %+v", receipt)
	}
}

func TestPoisonedReservationDoesNotBlockUnrelatedCommand(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	poisonExecutor := newBlockingResultExecutor([]byte("tampered"))
	poisonCoordinator := Coordinator{
		Store:    store,
		Executor: poisonExecutor,
		Clock:    recoveryClock(1_800_000_600),
	}
	poisonRequest := testRequest("command-poison", "participant-1", 1, map[string]any{"kind": "hold"})
	poisonDone := make(chan recoveryOutcome, 1)
	go func() {
		receipt, err := poisonCoordinator.Execute(context.Background(), poisonRequest)
		poisonDone <- recoveryOutcome{receipt: receipt, err: err}
	}()
	select {
	case <-poisonExecutor.started:
	case <-time.After(5 * time.Second):
		t.Fatal("poisoned reservation did not reach external execution")
	}

	unrelatedExecutor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	unrelatedCoordinator := Coordinator{
		Store:    store,
		Executor: unrelatedExecutor,
		Clock:    recoveryClock(1_800_000_700),
	}
	unrelatedRequest := testRequest("command-unrelated", "participant-2", 1, map[string]any{"kind": "advance"})
	unrelatedDone := make(chan recoveryOutcome, 1)
	go func() {
		receipt, err := unrelatedCoordinator.Execute(context.Background(), unrelatedRequest)
		unrelatedDone <- recoveryOutcome{receipt: receipt, err: err}
	}()

	var unrelated recoveryOutcome
	select {
	case unrelated = <-unrelatedDone:
	case <-time.After(5 * time.Second):
		t.Fatal("blocked World execution held the store lock and stalled an unrelated command")
	}
	if unrelated.err != nil || unrelated.receipt.Disposition != DispositionAccepted {
		t.Fatalf("unrelated command did not commit while poison execution was blocked: receipt=%+v err=%v", unrelated.receipt, unrelated.err)
	}
	close(poisonExecutor.release)

	var poison recoveryOutcome
	select {
	case poison = <-poisonDone:
	case <-time.After(5 * time.Second):
		t.Fatal("poisoned execution did not terminate after release")
	}
	var failure *ExecutionError
	if !errors.As(poison.err, &failure) || failure.Kind != FailureInvalidResult || failure.Retryable {
		t.Fatalf("poisoned result was not rejected non-retryably: %v", poison.err)
	}
	pending, ok := store.Reservation(poisonRequest.ClientCommandID)
	if !ok || len(pending.Attempts) != 1 || pending.Attempts[0].FailureKind != FailureInvalidResult {
		t.Fatalf("poisoned reservation evidence was not isolated: %+v", pending)
	}
	if _, ok := store.Receipt(unrelatedRequest.ClientCommandID); !ok {
		t.Fatal("unrelated receipt was lost after poison failure recording")
	}
	state := store.State()
	initial := testState()
	if state.MatchVersion != initial.MatchVersion+1 || state.StateRevision != initial.StateRevision+1 || state.NextGlobalEventSequence != initial.NextGlobalEventSequence+1 {
		t.Fatalf("unrelated command did not advance authority exactly once: %+v", state)
	}
	if err := store.Abort(context.Background(), pending.ClientCommandID, pending.Generation, "quarantine invalid World result", time.Unix(1_800_000_800, 0)); err != nil {
		t.Fatal(err)
	}
	status := store.Status(time.Unix(1_800_000_801, 0))
	if status.PendingReservations != 0 || status.RetiredReservations != 1 || status.Receipts != 1 {
		t.Fatalf("poison quarantine affected unrelated authority state: %+v", status)
	}
}
