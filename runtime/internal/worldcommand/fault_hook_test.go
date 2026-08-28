package worldcommand

import (
	"context"
	"testing"
)

func TestCoordinatorFaultHooksRunInStableOrderOutsideStoreLock(t *testing.T) {
	backend := NewMemoryBackend()
	store := newTestStore(t, backend)
	executor := &scriptedExecutor{steps: []scriptStep{{result: []byte("accepted")}}}
	order := []string{}
	coordinator := Coordinator{
		Store:    store,
		Executor: executor,
		Clock:    fixedClock(),
		AfterReservation: func(reservation Reservation) {
			order = append(order, "after_reservation")
			// Calling back into Store proves the coordinator released its mutex
			// before invoking the process-level fault hook.
			if current, exists := store.Reservation(reservation.ClientCommandID); !exists || current.Generation != reservation.Generation {
				t.Fatal("reservation hook did not observe the durable reservation")
			}
		},
		BeforeCommit: func(reservation Reservation, verified VerifiedTransition) {
			order = append(order, "before_commit")
			if executor.count() != 1 || verified.Disposition != DispositionAccepted {
				t.Fatal("before-commit hook ran before external execution and verification")
			}
			if _, exists := store.Receipt(reservation.ClientCommandID); exists {
				t.Fatal("before-commit hook observed a premature receipt")
			}
		},
		Persister: func(_ context.Context, _ string, _ string, _ []byte, _ Receipt, _ MatchState) (string, error) {
			order = append(order, "persist")
			return "4", nil
		},
	}
	if _, err := coordinator.Execute(context.Background(), testRequest("command-hooks", "participant-1", 1, map[string]any{"kind": "advance"})); err != nil {
		t.Fatal(err)
	}
	expected := []string{"after_reservation", "before_commit", "persist"}
	if len(order) != len(expected) {
		t.Fatalf("hook order length mismatch: %v", order)
	}
	for index := range expected {
		if order[index] != expected[index] {
			t.Fatalf("hook order mismatch: got=%v expected=%v", order, expected)
		}
	}
}
