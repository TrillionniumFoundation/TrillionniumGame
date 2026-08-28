package worldcommand

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

func (s *Store) retireLocked(ctx context.Context, reservation Reservation, kind FailureKind, reason string, now time.Time) error {
	retired := cloneReservation(reservation)
	finishOpenAttempts(&retired, kind, false, reason, now)
	retired.Status = ReservationRetired
	retired.RetiredReason = boundedDetail(reason)
	retired.UpdatedAtUnix = now.Unix()
	candidate := s.cloneDocumentLocked()
	delete(candidate.Reservations, reservation.ClientCommandID)
	candidate.Retired[reservation.ClientCommandID] = append(candidate.Retired[reservation.ClientCommandID], retired)
	if err := s.persistDocumentLocked(ctx, candidate); err != nil {
		return err
	}
	s.installDocumentLocked(candidate)
	return nil
}

func (s *Store) marshalLocked() ([]byte, error) {
	document := s.cloneDocumentLocked()
	return json.Marshal(document)
}

func (s *Store) cloneDocumentLocked() snapshotDocument {
	return snapshotDocument{
		Schema:       snapshotSchema,
		State:        cloneMatchState(s.state),
		Reservations: cloneReservationMap(s.reservations),
		Receipts:     cloneReceiptMap(s.receipts),
		Retired:      cloneRetiredMap(s.retired),
	}
}

func (s *Store) persistDocumentLocked(ctx context.Context, document snapshotDocument) error {
	payload, err := json.Marshal(document)
	if err != nil {
		return fmt.Errorf("encode World command store: %w", err)
	}
	version, err := s.backend.CompareAndSwap(ctx, s.key, s.version, payload)
	if err != nil {
		return fmt.Errorf("persist World command store: %w", err)
	}
	s.version = version
	return nil
}

func (s *Store) installDocumentLocked(document snapshotDocument) {
	s.state = cloneMatchState(document.State)
	s.reservations = cloneReservationMap(document.Reservations)
	s.receipts = cloneReceiptMap(document.Receipts)
	s.retired = cloneRetiredMap(document.Retired)
}
