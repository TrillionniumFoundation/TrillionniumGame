package worldcommand

import (
	"context"
	"fmt"
	"time"
)

func (s *Store) Prepare(ctx context.Context, request PrepareRequest, now time.Time) (PrepareResult, error) {
	if now.IsZero() || now.Unix() < 0 {
		return PrepareResult{}, fmt.Errorf("%w: prepare time is invalid", ErrInvalidState)
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	if err := s.validateRequestIdentity(request); err != nil {
		return PrepareResult{}, err
	}
	commandCanonical, err := s.codec.EncodeCanonical(request.Command)
	if err != nil {
		return PrepareResult{}, fmt.Errorf("command is not canonical: %w", err)
	}
	fingerprint := intentFingerprint(request, commandCanonical)

	if receipt, exists := s.receipts[request.ClientCommandID]; exists {
		if receipt.IntentFingerprint != fingerprint {
			return PrepareResult{}, ErrConflict
		}
		copyReceipt := cloneReceipt(receipt)
		return PrepareResult{Receipt: &copyReceipt}, nil
	}
	if existing, exists := s.reservations[request.ClientCommandID]; exists {
		if existing.IntentFingerprint != fingerprint {
			return PrepareResult{}, ErrConflict
		}
		copyReservation := cloneReservation(existing)
		return PrepareResult{Reservation: &copyReservation}, nil
	}
	if err := s.validateCurrentFence(request); err != nil {
		return PrepareResult{}, err
	}
	stateValue, err := s.codec.DecodeCanonical(s.state.StateCanonicalJSON, 2*1024*1024)
	if err != nil {
		return PrepareResult{}, fmt.Errorf("%w: stored deterministic state is not canonical: %v", ErrInvalidState, err)
	}
	prepared, err := s.codec.Prepare(
		request.Context,
		s.state.StateSchemaID,
		stateValue,
		request.CommandSchemaID,
		request.Command,
	)
	if err != nil {
		return PrepareResult{}, err
	}

	generation := uint64(1)
	for _, retired := range s.retired[request.ClientCommandID] {
		if retired.Generation >= generation {
			generation = retired.Generation + 1
		}
	}
	fence := Fence{
		MatchVersion:            s.state.MatchVersion,
		NextGlobalEventSequence: s.state.NextGlobalEventSequence,
		StateRevision:           s.state.StateRevision,
		StateHash:               s.state.StateHash,
		Tick:                    s.state.Tick,
		ParticipantSequence:     s.state.ParticipantSequences[request.ParticipantID],
	}
	reservation := Reservation{
		Schema:               reservationSchema,
		ClientCommandID:      request.ClientCommandID,
		IntentFingerprint:    fingerprint,
		Generation:           generation,
		UserID:               request.UserID,
		ParticipantID:        request.ParticipantID,
		ParticipantSequence:  request.ParticipantSequence,
		Context:              request.Context,
		CommandSchemaID:      request.CommandSchemaID,
		CommandCanonicalJSON: append([]byte(nil), commandCanonical...),
		Transition:           clonePrepared(prepared),
		Fence:                fence,
		Status:               ReservationPending,
		CreatedAtUnix:        now.Unix(),
		UpdatedAtUnix:        now.Unix(),
		Attempts:             []Attempt{},
	}
	reservation.StateToken = reservationToken(reservation)
	reservation.ReservationID = "wres-" + reservation.StateToken[:48]

	candidate := s.cloneDocumentLocked()
	candidate.Reservations[request.ClientCommandID] = reservation
	if err := s.persistDocumentLocked(ctx, candidate); err != nil {
		return PrepareResult{}, err
	}
	s.installDocumentLocked(candidate)
	copyReservation := cloneReservation(reservation)
	return PrepareResult{Reservation: &copyReservation}, nil
}

func (s *Store) BeginAttempt(ctx context.Context, reservation Reservation, now time.Time) (Reservation, error) {
	if now.IsZero() || now.Unix() < 0 {
		return Reservation{}, fmt.Errorf("%w: attempt time is invalid", ErrInvalidState)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, err := s.requireReservationLocked(reservation)
	if err != nil {
		return Reservation{}, err
	}
	current.Attempts = append(current.Attempts, Attempt{
		Number:        uint64(len(current.Attempts) + 1),
		StartedAtUnix: now.Unix(),
	})
	current.UpdatedAtUnix = now.Unix()
	candidate := s.cloneDocumentLocked()
	candidate.Reservations[current.ClientCommandID] = current
	if err := s.persistDocumentLocked(ctx, candidate); err != nil {
		return Reservation{}, err
	}
	s.installDocumentLocked(candidate)
	return cloneReservation(current), nil
}

func (s *Store) RecordFailure(ctx context.Context, reservation Reservation, failure *ExecutionError, now time.Time) error {
	if now.IsZero() || now.Unix() < 0 || failure == nil {
		return fmt.Errorf("%w: failure record is invalid", ErrInvalidState)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, err := s.requireReservationLocked(reservation)
	if err != nil {
		return err
	}
	attemptNumber := latestAttemptNumber(reservation)
	if attemptNumber == 0 {
		if len(current.Attempts) == 0 || current.Attempts[len(current.Attempts)-1].FinishedAtUnix != nil {
			attemptNumber = uint64(len(current.Attempts) + 1)
			current.Attempts = append(current.Attempts, Attempt{Number: attemptNumber, StartedAtUnix: now.Unix()})
		} else {
			attemptNumber = current.Attempts[len(current.Attempts)-1].Number
		}
	}
	index, err := findOpenAttempt(current.Attempts, attemptNumber)
	if err != nil {
		return err
	}
	finished := now.Unix()
	current.Attempts[index].FinishedAtUnix = &finished
	current.Attempts[index].FailureKind = failure.Kind
	current.Attempts[index].Retryable = failure.Retryable
	current.Attempts[index].Detail = boundedDetail(failure.Error())
	current.UpdatedAtUnix = now.Unix()
	candidate := s.cloneDocumentLocked()
	candidate.Reservations[current.ClientCommandID] = current
	if err := s.persistDocumentLocked(ctx, candidate); err != nil {
		return err
	}
	s.installDocumentLocked(candidate)
	return nil
}
