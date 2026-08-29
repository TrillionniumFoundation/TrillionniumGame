package worldcommand

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

// CommitPersister atomically persists a candidate World-command snapshot and
// any owner-defined sidecar state. The callback receives the exact store key,
// expected CAS version, serialized candidate, receipt, and candidate authority
// state. It must return a new concrete version only after all sidecar writes
// have committed. A failure must leave every sidecar unchanged.
type CommitPersister func(
	ctx context.Context,
	key string,
	expectedVersion string,
	candidatePayload []byte,
	receipt Receipt,
	candidateState MatchState,
) (string, error)

func (s *Store) Commit(ctx context.Context, reservation Reservation, verified VerifiedTransition, now time.Time) (Receipt, error) {
	return s.CommitWith(ctx, reservation, verified, now, nil)
}

// CommitWith applies exact stale fencing and persists the resulting candidate
// through persister. When persister is nil, the Store's configured backend is
// used. External World execution must already be complete before this method is
// entered; the callback is reserved for local atomic persistence only.
func (s *Store) CommitWith(
	ctx context.Context,
	reservation Reservation,
	verified VerifiedTransition,
	now time.Time,
	persister CommitPersister,
) (Receipt, error) {
	if now.IsZero() || now.Unix() < 0 {
		return Receipt{}, fmt.Errorf("%w: commit time is invalid", ErrInvalidState)
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	if receipt, exists := s.receipts[reservation.ClientCommandID]; exists {
		if receipt.IntentFingerprint != reservation.IntentFingerprint {
			return Receipt{}, ErrConflict
		}
		return cloneReceipt(receipt), nil
	}
	current, err := s.requireReservationLocked(reservation)
	if err != nil {
		return Receipt{}, err
	}
	if err := validateVerifiedAgainstReservation(verified, current); err != nil {
		return Receipt{}, err
	}
	if !s.fenceMatchesLocked(current) {
		if err := s.retireLocked(ctx, current, FailureStale, "stale authority or deterministic state", now); err != nil {
			return Receipt{}, err
		}
		return Receipt{}, ErrStaleReservation
	}

	finishCommittedAttempts(&current, latestAttemptNumber(reservation), verified.Disposition, now)
	candidate := s.cloneDocumentLocked()
	receipt := Receipt{
		Schema:                receiptSchema,
		ClientCommandID:       current.ClientCommandID,
		IntentFingerprint:     current.IntentFingerprint,
		ReservationID:         current.ReservationID,
		Generation:            current.Generation,
		Disposition:           verified.Disposition,
		MatchVersion:          s.state.MatchVersion,
		StateRevision:         s.state.StateRevision,
		StateHash:             s.state.StateHash,
		Tick:                  s.state.Tick,
		RequestHash:           verified.RequestHash,
		TransitionID:          verified.TransitionID,
		ReplayHash:            cloneString(verified.ReplayHash),
		WorldOutcomeHash:      cloneString(verified.WorldOutcomeHash),
		WorldTransitionHash:   cloneString(verified.WorldTransitionHash),
		ErrorCode:             cloneString(verified.ErrorCode),
		Retryable:             cloneBool(verified.Retryable),
		CanonicalResultSHA256: verified.CanonicalResultSHA256,
		Attempts:              cloneAttempts(current.Attempts),
		CommittedAtUnix:       now.Unix(),
	}

	switch verified.Disposition {
	case DispositionAccepted:
		if verified.NextTick == nil || verified.NextStateHash == nil || len(verified.NextStateCanonicalJSON) == 0 || verified.NextStateSchemaID == "" {
			return Receipt{}, fmt.Errorf("accepted World transition is incomplete")
		}
		if *verified.NextTick < s.state.Tick {
			return Receipt{}, fmt.Errorf("accepted World transition regresses tick")
		}
		if hashBytes(verified.NextStateCanonicalJSON) != *verified.NextStateHash {
			return Receipt{}, fmt.Errorf("accepted World transition next-state hash mismatch")
		}
		if _, err := s.codec.DecodeCanonical(verified.NextStateCanonicalJSON, 2*1024*1024); err != nil {
			return Receipt{}, fmt.Errorf("accepted World next state is not canonical: %w", err)
		}
		eventSequence := s.state.NextGlobalEventSequence
		receipt.EventSequence = &eventSequence
		candidate.State.MatchVersion++
		candidate.State.NextGlobalEventSequence++
		candidate.State.StateRevision++
		candidate.State.StateSchemaID = verified.NextStateSchemaID
		candidate.State.StateCanonicalJSON = append([]byte(nil), verified.NextStateCanonicalJSON...)
		candidate.State.StateHash = *verified.NextStateHash
		candidate.State.Tick = *verified.NextTick
		candidate.State.ParticipantSequences[current.ParticipantID] = current.ParticipantSequence
		receipt.MatchVersion = candidate.State.MatchVersion
		receipt.StateRevision = candidate.State.StateRevision
		receipt.StateHash = candidate.State.StateHash
		receipt.Tick = candidate.State.Tick
	case DispositionRejected:
		if verified.Retryable == nil || verified.ErrorCode == nil {
			return Receipt{}, fmt.Errorf("rejected World transition is incomplete")
		}
		if *verified.Retryable {
			return Receipt{}, ErrRetryable
		}
	default:
		return Receipt{}, fmt.Errorf("unsupported verified disposition %q", verified.Disposition)
	}

	delete(candidate.Reservations, current.ClientCommandID)
	candidate.Receipts[current.ClientCommandID] = receipt
	payload, err := json.Marshal(candidate)
	if err != nil {
		return Receipt{}, fmt.Errorf("encode World command store: %w", err)
	}

	var nextVersion string
	if persister == nil {
		nextVersion, err = s.backend.CompareAndSwap(ctx, s.key, s.version, payload)
	} else {
		nextVersion, err = persister(
			ctx,
			s.key,
			s.version,
			append([]byte(nil), payload...),
			cloneReceipt(receipt),
			cloneMatchState(candidate.State),
		)
	}
	if err != nil {
		return Receipt{}, fmt.Errorf("persist World command commit: %w", err)
	}
	if nextVersion == "" {
		return Receipt{}, fmt.Errorf("persist World command commit returned no version")
	}
	s.version = nextVersion
	s.installDocumentLocked(candidate)
	return cloneReceipt(receipt), nil
}
