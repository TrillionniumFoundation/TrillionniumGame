package worldcommand

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
)

func intentFingerprint(request PrepareRequest, commandCanonical []byte) string {
	// Idempotency is bound to immutable signed intent, not to the mutable
	// authority cursor captured when the first reservation was created. This
	// lets an exact duplicate replay its committed receipt after match/state
	// cursors advance, while a changed participant, command, ruleset, content,
	// roster or authorization still fails closed.
	material := struct {
		ClientCommandID       string `json:"client_command_id"`
		UserID                string `json:"user_id"`
		ParticipantID         string `json:"participant_id"`
		ParticipantSequence   uint64 `json:"participant_sequence"`
		CommandSchemaID       string `json:"command_schema_id"`
		CommandCanonical      []byte `json:"command_canonical"`
		MatchID               string `json:"match_id"`
		AuthorizationID       string `json:"authorization_id"`
		ParticipantRosterHash string `json:"participant_roster_hash"`
		RulesetRevision       string `json:"ruleset_revision"`
		ContentRevision       string `json:"content_revision"`
	}{
		request.ClientCommandID,
		request.UserID,
		request.ParticipantID,
		request.ParticipantSequence,
		request.CommandSchemaID,
		commandCanonical,
		request.Context.MatchID,
		request.Context.AuthorizationID,
		request.Context.ParticipantRosterHash,
		request.Context.RulesetRevision,
		request.Context.ContentRevision,
	}
	encoded, _ := json.Marshal(material)
	return domainHash("trnm.nakama.world-command.intent.v1", encoded)
}

func reservationToken(reservation Reservation) string {
	material := struct {
		ClientCommandID   string `json:"client_command_id"`
		IntentFingerprint string `json:"intent_fingerprint"`
		Generation        uint64 `json:"generation"`
		Fence             Fence  `json:"fence"`
		RequestHash       string `json:"request_hash"`
		TransitionID      string `json:"transition_id"`
		WorldCommandID    string `json:"world_command_id"`
	}{
		reservation.ClientCommandID,
		reservation.IntentFingerprint,
		reservation.Generation,
		reservation.Fence,
		reservation.Transition.RequestHash,
		reservation.Transition.TransitionID,
		reservation.Transition.WorldCommandID,
	}
	encoded, _ := json.Marshal(material)
	return domainHash("trnm.nakama.world-command.reservation.v1", encoded)
}

func domainHash(domain string, payload []byte) string {
	material := make([]byte, 0, len(domain)+1+len(payload))
	material = append(material, domain...)
	material = append(material, '\n')
	material = append(material, payload...)
	sum := sha256.Sum256(material)
	return hex.EncodeToString(sum[:])
}

func hashBytes(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

func boundedDetail(value string) string {
	value = strings.Map(func(r rune) rune {
		if r < 0x20 || r == 0x7f {
			return ' '
		}
		return r
	}, value)
	value = strings.TrimSpace(value)
	runes := []rune(value)
	if len(runes) > 256 {
		value = string(runes[:256])
	}
	if value == "" {
		return "unspecified failure"
	}
	return value
}

func cloneAttempts(input []Attempt) []Attempt {
	output := append([]Attempt(nil), input...)
	for index := range output {
		if output[index].FinishedAtUnix != nil {
			value := *output[index].FinishedAtUnix
			output[index].FinishedAtUnix = &value
		}
	}
	return output
}

func cloneMatchState(input MatchState) MatchState {
	output := input
	output.StateCanonicalJSON = append([]byte(nil), input.StateCanonicalJSON...)
	output.ParticipantSequences = make(map[string]uint64, len(input.ParticipantSequences))
	for key, value := range input.ParticipantSequences {
		output.ParticipantSequences[key] = value
	}
	return output
}

func clonePrepared(input PreparedTransition) PreparedTransition {
	input.CanonicalRequest = append([]byte(nil), input.CanonicalRequest...)
	return input
}

func cloneReservation(input Reservation) Reservation {
	input.CommandCanonicalJSON = append([]byte(nil), input.CommandCanonicalJSON...)
	input.Transition = clonePrepared(input.Transition)
	input.Attempts = cloneAttempts(input.Attempts)
	return input
}

func cloneReceipt(input Receipt) Receipt {
	input.EventSequence = cloneUint64(input.EventSequence)
	input.ReplayHash = cloneString(input.ReplayHash)
	input.WorldOutcomeHash = cloneString(input.WorldOutcomeHash)
	input.WorldTransitionHash = cloneString(input.WorldTransitionHash)
	input.ErrorCode = cloneString(input.ErrorCode)
	input.Retryable = cloneBool(input.Retryable)
	input.Attempts = cloneAttempts(input.Attempts)
	return input
}

func cloneUint64(value *uint64) *uint64 {
	if value == nil {
		return nil
	}
	copyValue := *value
	return &copyValue
}

func cloneReservationMap(input map[string]Reservation) map[string]Reservation {
	output := make(map[string]Reservation, len(input))
	for key, value := range input {
		output[key] = cloneReservation(value)
	}
	return output
}

func cloneReceiptMap(input map[string]Receipt) map[string]Receipt {
	output := make(map[string]Receipt, len(input))
	for key, value := range input {
		output[key] = cloneReceipt(value)
	}
	return output
}

func cloneRetiredMap(input map[string][]Reservation) map[string][]Reservation {
	output := make(map[string][]Reservation, len(input))
	for key, values := range input {
		cloned := make([]Reservation, len(values))
		for index, value := range values {
			cloned[index] = cloneReservation(value)
		}
		output[key] = cloned
	}
	return output
}
