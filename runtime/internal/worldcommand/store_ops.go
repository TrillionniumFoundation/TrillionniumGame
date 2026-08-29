package worldcommand

import (
	"context"
	"fmt"
	"strings"
	"time"
)

func (s *Store) Takeover(ctx context.Context, clientCommandID string, generation uint64, reason string, now time.Time) (Reservation, error) {
	if now.IsZero() || now.Unix() < 0 || strings.TrimSpace(reason) == "" {
		return Reservation{}, fmt.Errorf("%w: takeover request is invalid", ErrInvalidState)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, exists := s.reservations[clientCommandID]
	if !exists {
		return Reservation{}, ErrReservationAbsent
	}
	if current.Generation != generation || current.Status != ReservationPending {
		return Reservation{}, ErrStaleReservation
	}
	retired := cloneReservation(current)
	finishOpenAttempts(&retired, FailureSuperseded, false, "superseded by reservation generation takeover", now)
	retired.Status = ReservationRetired
	retired.RetiredReason = boundedDetail("generation takeover: " + reason)
	retired.UpdatedAtUnix = now.Unix()

	next := cloneReservation(current)
	next.Generation++
	next.Attempts = []Attempt{}
	next.CreatedAtUnix = now.Unix()
	next.UpdatedAtUnix = now.Unix()
	next.StateToken = reservationToken(next)
	next.ReservationID = "wres-" + next.StateToken[:48]

	candidate := s.cloneDocumentLocked()
	candidate.Retired[clientCommandID] = append(candidate.Retired[clientCommandID], retired)
	candidate.Reservations[clientCommandID] = next
	if err := s.persistDocumentLocked(ctx, candidate); err != nil {
		return Reservation{}, err
	}
	s.installDocumentLocked(candidate)
	return cloneReservation(next), nil
}

func (s *Store) Abort(ctx context.Context, clientCommandID string, generation uint64, reason string, now time.Time) error {
	if now.IsZero() || now.Unix() < 0 || strings.TrimSpace(reason) == "" {
		return fmt.Errorf("%w: abort request is invalid", ErrInvalidState)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, exists := s.reservations[clientCommandID]
	if !exists {
		return ErrReservationAbsent
	}
	if current.Generation != generation || current.Status != ReservationPending {
		return ErrStaleReservation
	}
	return s.retireLocked(ctx, current, FailureOperatorAbort, "operator abort: "+boundedDetail(reason), now)
}

func (s *Store) Status(now time.Time) StatusReport {
	s.mu.Lock()
	defer s.mu.Unlock()
	report := StatusReport{
		Schema:                  "trnm.nakama.world-command-status.v1",
		PendingReservations:     len(s.reservations),
		Receipts:                len(s.receipts),
		StateRevision:           s.state.StateRevision,
		StateHash:               s.state.StateHash,
		Tick:                    s.state.Tick,
		MatchVersion:            s.state.MatchVersion,
		NextGlobalEventSequence: s.state.NextGlobalEventSequence,
	}
	oldest := int64(0)
	for _, reservation := range s.reservations {
		accumulateAttemptMetrics(&report, reservation.Attempts)
		age := now.Unix() - reservation.CreatedAtUnix
		if age > oldest {
			oldest = age
		}
	}
	for _, values := range s.retired {
		report.RetiredReservations += len(values)
		for _, reservation := range values {
			accumulateAttemptMetrics(&report, reservation.Attempts)
			if len(reservation.Attempts) == 0 {
				accumulateRetirementMetric(&report, reservation)
			}
		}
	}
	for _, receipt := range s.receipts {
		accumulateAttemptMetrics(&report, receipt.Attempts)
	}
	if oldest > 0 {
		report.OldestPendingAgeSeconds = oldest
	}
	return report
}

func accumulateAttemptMetrics(report *StatusReport, attempts []Attempt) {
	for _, attempt := range attempts {
		report.TotalAttempts++
		if attempt.Retryable {
			report.RetryableAttempts++
		}
		switch attempt.FailureKind {
		case FailureCancelled:
			report.CancelledAttempts++
		case FailureTransport:
			report.TransportFailures++
		case FailureAmbiguousCommit:
			report.AmbiguousCommitFailures++
		case FailureInvalidResult:
			report.InvalidResultFailures++
		case FailureRemoteRetryable:
			report.RemoteRetryableRejections++
		case FailurePersistence:
			report.PersistenceFailures++
		case FailureStale:
			report.StaleRejects++
		case FailureOperatorAbort:
			report.OperatorAborts++
		case FailureSuperseded:
			report.SupersededGenerations++
		}
	}
}

func accumulateRetirementMetric(report *StatusReport, reservation Reservation) {
	switch {
	case strings.HasPrefix(reservation.RetiredReason, "operator abort:"):
		report.OperatorAborts++
	case reservation.Generation > 0 && strings.Contains(reservation.RetiredReason, "takeover"):
		report.SupersededGenerations++
	case strings.Contains(reservation.RetiredReason, "stale"):
		report.StaleRejects++
	}
}

func (s *Store) State() MatchState {
	s.mu.Lock()
	defer s.mu.Unlock()
	return cloneMatchState(s.state)
}

func (s *Store) Receipt(clientCommandID string) (Receipt, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	receipt, ok := s.receipts[clientCommandID]
	return cloneReceipt(receipt), ok
}

func (s *Store) Reservation(clientCommandID string) (Reservation, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	reservation, ok := s.reservations[clientCommandID]
	return cloneReservation(reservation), ok
}
