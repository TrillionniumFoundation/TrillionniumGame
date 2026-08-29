package worldcommand

import (
	"context"
	"errors"
	"fmt"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldtransition"
)

const (
	snapshotSchema    = "trnm.nakama.world-command-store.v1"
	reservationSchema = "trnm.nakama.world-command-reservation.v1"
	receiptSchema     = "trnm.nakama.world-command-receipt.v1"
)

var (
	ErrConflict          = errors.New("world command idempotency conflict")
	ErrStaleReservation  = errors.New("world command reservation is stale")
	ErrRetryable         = errors.New("world command remains retryable")
	ErrReservationAbsent = errors.New("world command reservation not found")
	ErrVersionConflict   = errors.New("world command snapshot version conflict")
	ErrInvalidState      = errors.New("world command store state is invalid")
)

type FailureKind string

const (
	FailureCancelled       FailureKind = "cancelled"
	FailureTransport       FailureKind = "transport"
	FailureAmbiguousCommit FailureKind = "ambiguous_remote_commit"
	FailureInvalidResult   FailureKind = "invalid_world_result"
	FailureRemoteRetryable FailureKind = "remote_retryable_rejection"
	FailurePersistence     FailureKind = "persistence"
	FailureStale           FailureKind = "stale_authority_state"
	FailureOperatorAbort   FailureKind = "operator_abort"
	FailureSuperseded      FailureKind = "superseded_generation"
)

type ExecutionError struct {
	Kind      FailureKind
	Retryable bool
	Err       error
}

func (e *ExecutionError) Error() string {
	if e == nil {
		return ""
	}
	if e.Err == nil {
		return string(e.Kind)
	}
	return fmt.Sprintf("%s: %v", e.Kind, e.Err)
}

func (e *ExecutionError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

type PrepareRequest struct {
	ClientCommandID       string
	UserID                string
	ParticipantID         string
	ParticipantSequence   uint64
	ExpectedStateRevision uint64
	ExpectedStateHash     string
	CommandSchemaID       string
	Command               any
	Context               worldtransition.AuthorityContext
}

type MatchState struct {
	MatchID                 string            `json:"match_id"`
	ParticipantRosterHash   string            `json:"participant_roster_hash"`
	MatchVersion            uint64            `json:"match_version"`
	NextGlobalEventSequence uint64            `json:"next_global_event_sequence"`
	StateRevision           uint64            `json:"state_revision"`
	StateSchemaID           string            `json:"state_schema_id"`
	StateCanonicalJSON      []byte            `json:"state_canonical_json"`
	StateHash               string            `json:"state_hash"`
	Tick                    int64             `json:"tick"`
	ParticipantSequences    map[string]uint64 `json:"participant_sequences"`
}

type Fence struct {
	MatchVersion            uint64 `json:"match_version"`
	NextGlobalEventSequence uint64 `json:"next_global_event_sequence"`
	StateRevision           uint64 `json:"state_revision"`
	StateHash               string `json:"state_hash"`
	Tick                    int64  `json:"tick"`
	ParticipantSequence     uint64 `json:"participant_sequence"`
}

type PreparedTransition struct {
	CanonicalRequest  []byte `json:"canonical_request"`
	RequestHash       string `json:"request_hash"`
	TransitionID      string `json:"transition_id"`
	WorldCommandID    string `json:"world_command_id"`
	PreviousStateHash string `json:"previous_state_hash"`
}

type Attempt struct {
	Number         uint64      `json:"number"`
	StartedAtUnix  int64       `json:"started_at_unix"`
	FinishedAtUnix *int64      `json:"finished_at_unix,omitempty"`
	FailureKind    FailureKind `json:"failure_kind,omitempty"`
	Detail         string      `json:"detail,omitempty"`
	Retryable      bool        `json:"retryable"`
}

type ReservationStatus string

const (
	ReservationPending ReservationStatus = "pending"
	ReservationRetired ReservationStatus = "retired"
)

type Reservation struct {
	Schema               string                           `json:"schema"`
	ReservationID        string                           `json:"reservation_id"`
	ClientCommandID      string                           `json:"client_command_id"`
	IntentFingerprint    string                           `json:"intent_fingerprint"`
	Generation           uint64                           `json:"generation"`
	StateToken           string                           `json:"state_token"`
	UserID               string                           `json:"user_id"`
	ParticipantID        string                           `json:"participant_id"`
	ParticipantSequence  uint64                           `json:"participant_sequence"`
	Context              worldtransition.AuthorityContext `json:"context"`
	CommandSchemaID      string                           `json:"command_schema_id"`
	CommandCanonicalJSON []byte                           `json:"command_canonical_json"`
	Transition           PreparedTransition               `json:"transition"`
	Fence                Fence                            `json:"fence"`
	Status               ReservationStatus                `json:"status"`
	CreatedAtUnix        int64                            `json:"created_at_unix"`
	UpdatedAtUnix        int64                            `json:"updated_at_unix"`
	Attempts             []Attempt                        `json:"attempts"`
	RetiredReason        string                           `json:"retired_reason,omitempty"`
}

type Disposition string

const (
	DispositionAccepted Disposition = "accepted"
	DispositionRejected Disposition = "rejected"
)

type VerifiedTransition struct {
	Disposition            Disposition `json:"disposition"`
	RequestHash            string      `json:"request_hash"`
	TransitionID           string      `json:"transition_id"`
	NextTick               *int64      `json:"next_tick,omitempty"`
	PreviousStateHash      *string     `json:"previous_state_hash,omitempty"`
	NextStateSchemaID      string      `json:"next_state_schema_id,omitempty"`
	NextStateCanonicalJSON []byte      `json:"next_state_canonical_json,omitempty"`
	NextStateHash          *string     `json:"next_state_hash,omitempty"`
	ReplayHash             *string     `json:"replay_hash,omitempty"`
	WorldOutcomeHash       *string     `json:"world_outcome_hash,omitempty"`
	WorldTransitionHash    *string     `json:"world_transition_hash,omitempty"`
	ErrorCode              *string     `json:"error_code,omitempty"`
	Retryable              *bool       `json:"retryable,omitempty"`
	CanonicalResultSHA256  string      `json:"canonical_result_sha256"`
}

type Receipt struct {
	Schema                 string       `json:"schema"`
	ClientCommandID       string      `json:"client_command_id"`
	IntentFingerprint     string      `json:"intent_fingerprint"`
	ReservationID         string      `json:"reservation_id"`
	Generation            uint64      `json:"generation"`
	Disposition            Disposition `json:"disposition"`
	EventSequence         *uint64     `json:"event_sequence,omitempty"`
	MatchVersion          uint64      `json:"match_version"`
	StateRevision         uint64      `json:"state_revision"`
	StateHash             string      `json:"state_hash"`
	Tick                  int64       `json:"tick"`
	RequestHash           string      `json:"request_hash"`
	TransitionID          string      `json:"transition_id"`
	ReplayHash            *string     `json:"replay_hash,omitempty"`
	WorldOutcomeHash      *string     `json:"world_outcome_hash,omitempty"`
	WorldTransitionHash   *string     `json:"world_transition_hash,omitempty"`
	ErrorCode             *string     `json:"error_code,omitempty"`
	Retryable             *bool       `json:"retryable,omitempty"`
	CanonicalResultSHA256 string      `json:"canonical_result_sha256"`
	Attempts              []Attempt   `json:"attempts"`
	CommittedAtUnix       int64       `json:"committed_at_unix"`
}

type PrepareResult struct {
	Reservation *Reservation
	Receipt     *Receipt
}

type StatusReport struct {
	Schema                  string `json:"schema"`
	PendingReservations     int    `json:"pending_reservations"`
	RetiredReservations     int    `json:"retired_reservations"`
	Receipts                int    `json:"receipts"`
	TotalAttempts           uint64 `json:"total_attempts"`
	OldestPendingAgeSeconds int64  `json:"oldest_pending_age_seconds"`
	StateRevision           uint64 `json:"state_revision"`
	StateHash               string `json:"state_hash"`
	Tick                    int64  `json:"tick"`
	MatchVersion            uint64 `json:"match_version"`
	NextGlobalEventSequence uint64 `json:"next_global_event_sequence"`
}

type Codec interface {
	DecodeCanonical(raw []byte, maximumBytes int) (any, error)
	EncodeCanonical(value any) ([]byte, error)
	Prepare(context worldtransition.AuthorityContext, previousStateSchemaID string, previousState any, commandSchemaID string, command any) (PreparedTransition, error)
	Restore(context worldtransition.AuthorityContext, canonicalRequest []byte) (PreparedTransition, error)
	Verify(prepared PreparedTransition, context worldtransition.AuthorityContext, rawResult []byte) (VerifiedTransition, error)
}

type Executor interface {
	Execute(context.Context, []byte) ([]byte, error)
}

type SnapshotBackend interface {
	Load(context.Context, string) ([]byte, string, error)
	CompareAndSwap(context.Context, string, string, []byte) (string, error)
}
