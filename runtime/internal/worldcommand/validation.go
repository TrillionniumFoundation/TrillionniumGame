package worldcommand

import (
	"fmt"
	"strings"
	"time"
)

func (s *Store) validateRequestIdentity(request PrepareRequest) error {
	if strings.TrimSpace(request.ClientCommandID) == "" || request.ClientCommandID != request.Context.CommandIdempotencyKey {
		return fmt.Errorf("%w: client command ID must equal the Nakama idempotency key", ErrInvalidState)
	}
	if strings.TrimSpace(request.UserID) == "" || strings.TrimSpace(request.ParticipantID) == "" || request.ParticipantSequence == 0 ||
		request.ExpectedStateRevision == 0 || strings.TrimSpace(request.ExpectedStateHash) == "" || strings.TrimSpace(request.CommandSchemaID) == "" {
		return fmt.Errorf("%w: command identity is incomplete", ErrInvalidState)
	}
	return nil
}

func (s *Store) validateCurrentFence(request PrepareRequest) error {
	if request.Context.MatchID != s.state.MatchID || request.Context.ParticipantRosterHash != s.state.ParticipantRosterHash ||
		request.Context.MatchVersion < 0 || uint64(request.Context.MatchVersion) != s.state.MatchVersion ||
		request.Context.GlobalEventSequence < 0 || uint64(request.Context.GlobalEventSequence) != s.state.NextGlobalEventSequence ||
		request.Context.ExpectedTick != s.state.Tick || request.ExpectedStateRevision != s.state.StateRevision ||
		request.ExpectedStateHash != s.state.StateHash {
		return ErrStaleReservation
	}
	expectedParticipantSequence := s.state.ParticipantSequences[request.ParticipantID] + 1
	if request.ParticipantSequence != expectedParticipantSequence {
		return fmt.Errorf("%w: expected participant sequence %d", ErrStaleReservation, expectedParticipantSequence)
	}
	return nil
}

func (s *Store) validateState(state MatchState) error {
	if strings.TrimSpace(state.MatchID) == "" || strings.TrimSpace(state.ParticipantRosterHash) == "" ||
		state.MatchVersion == 0 || state.NextGlobalEventSequence == 0 || state.StateRevision == 0 ||
		strings.TrimSpace(state.StateSchemaID) == "" || len(state.StateCanonicalJSON) == 0 ||
		state.Tick < 0 || state.ParticipantSequences == nil {
		return fmt.Errorf("%w: deterministic match state is incomplete", ErrInvalidState)
	}
	if hashBytes(state.StateCanonicalJSON) != state.StateHash {
		return fmt.Errorf("%w: deterministic state hash mismatch", ErrInvalidState)
	}
	if _, err := s.codec.DecodeCanonical(state.StateCanonicalJSON, 2*1024*1024); err != nil {
		return fmt.Errorf("%w: deterministic state is not canonical: %v", ErrInvalidState, err)
	}
	return nil
}

func (s *Store) validateLocked(schema string) error {
	if schema != snapshotSchema {
		return fmt.Errorf("%w: unsupported snapshot schema %q", ErrInvalidState, schema)
	}
	if err := s.validateState(s.state); err != nil {
		return err
	}
	for commandID, reservation := range s.reservations {
		if commandID != reservation.ClientCommandID || reservation.Schema != reservationSchema || reservation.Status != ReservationPending || reservation.Generation == 0 {
			return fmt.Errorf("%w: invalid pending reservation %q", ErrInvalidState, commandID)
		}
		restored, err := s.codec.Restore(reservation.Context, reservation.Transition.CanonicalRequest)
		if err != nil || restored.RequestHash != reservation.Transition.RequestHash || restored.TransitionID != reservation.Transition.TransitionID || restored.WorldCommandID != reservation.Transition.WorldCommandID {
			return fmt.Errorf("%w: reservation %q request identity is invalid", ErrInvalidState, commandID)
		}
		if reservation.StateToken != reservationToken(reservation) || reservation.ReservationID != "wres-"+reservation.StateToken[:48] {
			return fmt.Errorf("%w: reservation %q state token is invalid", ErrInvalidState, commandID)
		}
		if err := validateAttempts(reservation.Attempts, false); err != nil {
			return fmt.Errorf("%w: invalid pending reservation %q attempts: %v", ErrInvalidState, commandID, err)
		}
	}
	for commandID, receipt := range s.receipts {
		if commandID != receipt.ClientCommandID || receipt.Schema != receiptSchema || receipt.Generation == 0 ||
			receipt.RequestHash == "" || receipt.TransitionID == "" || receipt.IntentFingerprint == "" ||
			receipt.ReservationID == "" || receipt.CanonicalResultSHA256 == "" || receipt.CommittedAtUnix < 0 {
			return fmt.Errorf("%w: invalid receipt %q", ErrInvalidState, commandID)
		}
		if err := validateAttempts(receipt.Attempts, true); err != nil {
			return fmt.Errorf("%w: invalid receipt %q attempts: %v", ErrInvalidState, commandID, err)
		}
	}
	for commandID, values := range s.retired {
		for _, reservation := range values {
			if reservation.ClientCommandID != commandID || reservation.Status != ReservationRetired || reservation.Generation == 0 || reservation.RetiredReason == "" ||
				reservation.StateToken != reservationToken(reservation) || reservation.ReservationID != "wres-"+reservation.StateToken[:48] {
				return fmt.Errorf("%w: invalid retired reservation %q", ErrInvalidState, commandID)
			}
			if err := validateAttempts(reservation.Attempts, true); err != nil {
				return fmt.Errorf("%w: invalid retired reservation %q attempts: %v", ErrInvalidState, commandID, err)
			}
		}
	}
	return nil
}

func (s *Store) requireReservationLocked(input Reservation) (Reservation, error) {
	current, exists := s.reservations[input.ClientCommandID]
	if !exists {
		if receipt, committed := s.receipts[input.ClientCommandID]; committed && receipt.IntentFingerprint == input.IntentFingerprint {
			return Reservation{}, ErrStaleReservation
		}
		return Reservation{}, ErrReservationAbsent
	}
	if current.Generation != input.Generation || current.ReservationID != input.ReservationID || current.StateToken != input.StateToken || current.IntentFingerprint != input.IntentFingerprint || current.Status != ReservationPending {
		return Reservation{}, ErrStaleReservation
	}
	return cloneReservation(current), nil
}

func (s *Store) fenceMatchesLocked(reservation Reservation) bool {
	return s.state.MatchVersion == reservation.Fence.MatchVersion &&
		s.state.NextGlobalEventSequence == reservation.Fence.NextGlobalEventSequence &&
		s.state.StateRevision == reservation.Fence.StateRevision &&
		s.state.StateHash == reservation.Fence.StateHash &&
		s.state.Tick == reservation.Fence.Tick &&
		s.state.ParticipantSequences[reservation.ParticipantID] == reservation.Fence.ParticipantSequence
}

func validateVerifiedAgainstReservation(verified VerifiedTransition, reservation Reservation) error {
	if verified.RequestHash != reservation.Transition.RequestHash || verified.TransitionID != reservation.Transition.TransitionID {
		return fmt.Errorf("verified World result is not bound to reservation")
	}
	if verified.PreviousStateHash != nil && *verified.PreviousStateHash != reservation.Transition.PreviousStateHash {
		return fmt.Errorf("verified World result previous state mismatch")
	}
	return nil
}

func latestAttemptNumber(reservation Reservation) uint64 {
	if len(reservation.Attempts) == 0 {
		return 0
	}
	return reservation.Attempts[len(reservation.Attempts)-1].Number
}

func findOpenAttempt(attempts []Attempt, number uint64) (int, error) {
	if number == 0 {
		return -1, fmt.Errorf("%w: attempt number is required", ErrStaleReservation)
	}
	for index := range attempts {
		if attempts[index].Number == number {
			if attempts[index].FinishedAtUnix != nil {
				return -1, fmt.Errorf("%w: attempt %d is already finished", ErrStaleReservation, number)
			}
			return index, nil
		}
	}
	return -1, fmt.Errorf("%w: attempt %d does not exist", ErrStaleReservation, number)
}

func finishCommittedAttempts(reservation *Reservation, activeAttempt uint64, disposition Disposition, now time.Time) {
	if len(reservation.Attempts) == 0 {
		reservation.Attempts = append(reservation.Attempts, Attempt{Number: 1, StartedAtUnix: now.Unix()})
		activeAttempt = 1
	}
	finished := now.Unix()
	for index := range reservation.Attempts {
		attempt := &reservation.Attempts[index]
		if attempt.FinishedAtUnix != nil {
			continue
		}
		attempt.FinishedAtUnix = &finished
		attempt.Retryable = false
		if attempt.Number == activeAttempt {
			attempt.Detail = boundedDetail("committed " + string(disposition))
		} else {
			attempt.FailureKind = FailureSuperseded
			attempt.Detail = "superseded by committed result"
		}
	}
}

func finishOpenAttempts(reservation *Reservation, kind FailureKind, retryable bool, detail string, now time.Time) {
	finished := now.Unix()
	for index := range reservation.Attempts {
		attempt := &reservation.Attempts[index]
		if attempt.FinishedAtUnix != nil {
			continue
		}
		attempt.FinishedAtUnix = &finished
		attempt.FailureKind = kind
		attempt.Retryable = retryable
		attempt.Detail = boundedDetail(detail)
	}
}

func validateAttempts(attempts []Attempt, requireFinished bool) error {
	for index, attempt := range attempts {
		expected := uint64(index + 1)
		if attempt.Number != expected || attempt.StartedAtUnix < 0 {
			return fmt.Errorf("attempt sequence is invalid")
		}
		if attempt.FinishedAtUnix == nil {
			if requireFinished {
				return fmt.Errorf("attempt %d is unfinished", attempt.Number)
			}
			continue
		}
		if *attempt.FinishedAtUnix < attempt.StartedAtUnix {
			return fmt.Errorf("attempt %d finishes before it starts", attempt.Number)
		}
		if attempt.Detail == "" {
			return fmt.Errorf("attempt %d has no completion detail", attempt.Number)
		}
	}
	return nil
}
