package contract

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"sort"
)

const (
	EventSchema      = "trnm.match.event.v1"
	CompletionSchema = "trnm.match.completed.v1"
)

type MatchEvent struct {
	Schema          string `json:"schema"`
	EventID         string `json:"event_id"`
	EventType       string `json:"event_type"`
	MatchID         string `json:"match_id"`
	ChallengeID     string `json:"challenge_id"`
	Sequence        uint64 `json:"sequence"`
	CausationID     string `json:"causation_id"`
	OccurredAtUnix  int64  `json:"occurred_at_unix"`
	ParticipantSlot uint32 `json:"participant_slot"`
	MatchVersion    uint64 `json:"match_version"`
	PayloadType     string `json:"payload_type"`
	Payload         []byte `json:"payload"`
	PayloadHash     Digest `json:"payload_hash"`
	EventHash       Digest `json:"event_hash"`
}

func (e MatchEvent) factsBytes() ([]byte, error) {
	if e.Schema != EventSchema {
		return nil, fmt.Errorf("unsupported event schema %q", e.Schema)
	}
	for name, value := range map[string]string{
		"event_id": e.EventID, "event_type": e.EventType, "match_id": e.MatchID,
		"challenge_id": e.ChallengeID, "causation_id": e.CausationID,
		"payload_type": e.PayloadType,
	} {
		if err := validateText(name, value); err != nil {
			return nil, err
		}
	}
	if e.Sequence == 0 || e.MatchVersion == 0 {
		return nil, errors.New("event sequence and match version must be positive")
	}
	if err := ValidateLogicalMatchID(e.MatchID); err != nil {
		return nil, err
	}
	if e.ParticipantSlot > 2 {
		return nil, errors.New("event participant_slot must be 0, 1, or 2")
	}
	if e.OccurredAtUnix < 0 || len(e.Payload) == 0 || len(e.Payload) > MaxPayloadBytes {
		return nil, errors.New("event time or payload is invalid")
	}
	if e.PayloadHash != NewDigest(e.Payload) {
		return nil, errors.New("event payload_hash does not match payload")
	}
	f := newFrame("trnm_match_event_v1").
		string(e.Schema).
		string(e.EventID).
		string(e.EventType).
		string(e.MatchID).
		string(e.ChallengeID).
		u64(e.Sequence).
		string(e.CausationID).
		i64(e.OccurredAtUnix).
		u32(e.ParticipantSlot).
		u64(e.MatchVersion).
		string(e.PayloadType).
		bytes(e.Payload)
	var err error
	if f, err = f.digest(e.PayloadHash); err != nil {
		return nil, err
	}
	return f.result()
}

func ComputeEventHash(event MatchEvent) (Digest, error) {
	facts, err := event.factsBytes()
	if err != nil {
		return "", err
	}
	return NewDigest(facts), nil
}

func SealEvent(event MatchEvent) (MatchEvent, error) {
	event.PayloadHash = NewDigest(event.Payload)
	hash, err := ComputeEventHash(event)
	if err != nil {
		return MatchEvent{}, err
	}
	event.EventHash = hash
	return event, nil
}

func (e MatchEvent) Validate() error {
	hash, err := ComputeEventHash(e)
	if err != nil {
		return err
	}
	if err := e.EventHash.Validate(); err != nil {
		return fmt.Errorf("event_hash: %w", err)
	}
	if e.EventHash != hash {
		return errors.New("event_hash does not match event facts")
	}
	return nil
}

// CanonicalEventID derives the stable event identifier from the immutable
// archive key. The causation ID is part of the identifier so an event cannot be
// rebound to a different authorization, command, or completion transition.
func CanonicalEventID(matchID string, sequence uint64, causationID string) (string, error) {
	if err := ValidateLogicalMatchID(matchID); err != nil {
		return "", fmt.Errorf("match_id: %w", err)
	}
	if sequence == 0 {
		return "", errors.New("event sequence must be positive")
	}
	if err := validateText("causation_id", causationID); err != nil {
		return "", err
	}
	encoded, err := newFrame("trnm_match_event_id_v1").
		string(matchID).
		string(causationID).
		u64(sequence).
		result()
	if err != nil {
		return "", err
	}
	return string(NewDigest(encoded)), nil
}

// ValidateArchive validates the complete cross-component event contract. It
// accepts any valid prefix (including the admission and active phases) but a
// completion event, when present, must be the final event in the archive.
func ValidateArchive(events []MatchEvent) error {
	if len(events) == 0 {
		return errors.New("event archive is empty")
	}

	matchID := events[0].MatchID
	challengeID := events[0].ChallengeID
	joinedSlots := [2]bool{}
	seenEventIDs := make(map[string]struct{}, len(events))
	seenEventHashes := make(map[Digest]struct{}, len(events))
	commandCount := 0
	completionSeen := false
	var previousTime int64

	for index, event := range events {
		sequence := uint64(index + 1)
		if event.MatchID != matchID || event.ChallengeID != challengeID {
			return fmt.Errorf("event %d: archive match or challenge identity differs", index)
		}
		if event.Sequence != sequence {
			return fmt.Errorf("event %d: event sequence is not contiguous", index)
		}
		if event.MatchVersion != sequence+1 {
			return fmt.Errorf("event %d: match_version must equal sequence plus one", index)
		}
		if index > 0 && event.OccurredAtUnix < previousTime {
			return fmt.Errorf("event %d: event time moves backwards", index)
		}
		previousTime = event.OccurredAtUnix
		if err := event.Validate(); err != nil {
			return fmt.Errorf("event %d: %w", index, err)
		}
		expectedEventID, err := CanonicalEventID(event.MatchID, event.Sequence, event.CausationID)
		if err != nil {
			return fmt.Errorf("event %d: could not derive canonical event ID: %w", index, err)
		}
		if event.EventID != expectedEventID {
			return fmt.Errorf("event %d: event ID is not canonically derived", index)
		}
		if _, duplicate := seenEventIDs[event.EventID]; duplicate {
			return fmt.Errorf("event %d: duplicate event ID", index)
		}
		if _, duplicate := seenEventHashes[event.EventHash]; duplicate {
			return fmt.Errorf("event %d: duplicate event hash", index)
		}
		seenEventIDs[event.EventID] = struct{}{}
		seenEventHashes[event.EventHash] = struct{}{}

		switch event.EventType {
		case "participant_joined":
			if commandCount != 0 || completionSeen || event.ParticipantSlot < 1 || event.ParticipantSlot > 2 {
				return fmt.Errorf("event %d: join event occurs outside the admission phase", index)
			}
			participantIndex := int(event.ParticipantSlot - 1)
			if joinedSlots[participantIndex] {
				return fmt.Errorf("event %d: participant slot joins more than once", index)
			}
			if event.PayloadType != "trnm.participant.joined.v1" {
				return fmt.Errorf("event %d: join event has an unexpected payload type", index)
			}
			joinedSlots[participantIndex] = true

		case "agent_command_applied":
			if !joinedSlots[0] || !joinedSlots[1] || completionSeen || event.ParticipantSlot < 1 || event.ParticipantSlot > 2 {
				return fmt.Errorf("event %d: command event occurs outside the active phase", index)
			}
			commandCount++

		case "match_completed":
			if completionSeen || index != len(events)-1 || commandCount == 0 || event.ParticipantSlot != 0 {
				return fmt.Errorf("event %d: completion event occurs outside the completion transition", index)
			}
			if event.PayloadType != "trnm.match.terminal-facts.v1" {
				return fmt.Errorf("event %d: completion event has an unexpected payload type", index)
			}
			completionSeen = true

		default:
			return fmt.Errorf("event %d: unsupported event type %q", index, event.EventType)
		}
	}
	return nil
}

// EventRoot implements the audited binary Merkle algorithm. Odd nodes are
// duplicated. There is intentionally no root for an empty event archive.
func EventRoot(events []MatchEvent) (Digest, error) {
	if err := ValidateArchive(events); err != nil {
		return "", err
	}
	commitments := make([]EventCommitment, len(events))
	for index, event := range events {
		commitments[index] = EventCommitment{Sequence: event.Sequence, EventHash: event.EventHash}
	}
	return EventRootFromCommitments(commitments)
}

type EventCommitment struct {
	Sequence  uint64
	EventHash Digest
}

// EventRootFromCommitments exposes the consensus-facing root algorithm without
// requiring callers to reconstruct full event envelopes.
func EventRootFromCommitments(commitments []EventCommitment) (Digest, error) {
	if len(commitments) == 0 {
		return "", errors.New("cannot compute an event root for an empty archive")
	}
	level := make([][sha256.Size]byte, len(commitments))
	for index, commitment := range commitments {
		if commitment.Sequence != uint64(index+1) {
			return "", fmt.Errorf("event sequence is not contiguous at index %d", index)
		}
		eventHash, err := commitment.EventHash.Bytes()
		if err != nil {
			return "", fmt.Errorf("event hash %d: %w", index, err)
		}
		leafInput := append([]byte("trnm_match_event_leaf_v1\x00"), make([]byte, 8)...)
		binary.BigEndian.PutUint64(leafInput[len(leafInput)-8:], commitment.Sequence)
		leafInput = append(leafInput, eventHash[:]...)
		level[index] = sha256.Sum256(leafInput)
	}
	for len(level) > 1 {
		next := make([][sha256.Size]byte, 0, (len(level)+1)/2)
		for index := 0; index < len(level); index += 2 {
			right := level[index]
			if index+1 < len(level) {
				right = level[index+1]
			}
			input := append([]byte("trnm_binary_merkle_node_v1\x00"), level[index][:]...)
			input = append(input, right[:]...)
			next = append(next, sha256.Sum256(input))
		}
		level = next
	}
	return Digest(digestPrefix + fmt.Sprintf("%x", level[0][:])), nil
}

type RosterEntry struct {
	ParticipantSlot uint32 `json:"participant_slot"`
	SubjectUserID   string `json:"subject_user_id"`
	AgentID         string `json:"agent_id"`
	AgentDID        string `json:"agent_did"`
	AgentKeyID      string `json:"agent_key_id"`
	AgentKeyHash    Digest `json:"agent_key_hash"`
	Role            string `json:"role"`
}

func RosterRoot(roster []RosterEntry) (Digest, error) {
	if len(roster) != 2 {
		return "", errors.New("roster must contain exactly two participants")
	}
	ordered := append([]RosterEntry(nil), roster...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].ParticipantSlot < ordered[j].ParticipantSlot })
	if ordered[0].ParticipantSlot != 1 || ordered[1].ParticipantSlot != 2 {
		return "", errors.New("roster must contain slots 1 and 2")
	}
	if ordered[0].SubjectUserID == ordered[1].SubjectUserID || ordered[0].AgentID == ordered[1].AgentID ||
		ordered[0].AgentDID == ordered[1].AgentDID || ordered[0].AgentKeyID == ordered[1].AgentKeyID ||
		ordered[0].AgentKeyHash == ordered[1].AgentKeyHash {
		return "", errors.New("roster participants must have unique user, agent, DID, and key values")
	}
	f := newFrame("trnm_match_roster_v1").u32(uint32(len(ordered)))
	for _, entry := range ordered {
		for name, value := range map[string]string{
			"subject_user_id": entry.SubjectUserID, "agent_id": entry.AgentID,
			"agent_did": entry.AgentDID, "agent_key_id": entry.AgentKeyID, "role": entry.Role,
		} {
			if err := validateText(name, value); err != nil {
				return "", err
			}
		}
		if err := entry.AgentKeyHash.Validate(); err != nil {
			return "", fmt.Errorf("agent_key_hash: %w", err)
		}
		f = f.u32(entry.ParticipantSlot).
			string(entry.SubjectUserID).
			string(entry.AgentID).
			string(entry.AgentDID).
			string(entry.AgentKeyID)
		var err error
		if f, err = f.digest(entry.AgentKeyHash); err != nil {
			return "", err
		}
		f = f.string(entry.Role)
	}
	encoded, err := f.result()
	if err != nil {
		return "", err
	}
	return NewDigest(encoded), nil
}

func CanonicalArchive(events []MatchEvent) ([]byte, error) {
	if err := ValidateArchive(events); err != nil {
		return nil, err
	}
	f := newFrame("trnm_match_event_archive_v1").u64(uint64(len(events)))
	for _, event := range events {
		facts, err := event.factsBytes()
		if err != nil {
			return nil, err
		}
		eventHash, err := event.EventHash.Bytes()
		if err != nil {
			return nil, err
		}
		f = f.bytes(facts).bytes(eventHash[:])
	}
	return f.result()
}

func ArchiveHash(events []MatchEvent) (Digest, error) {
	archive, err := CanonicalArchive(events)
	if err != nil {
		return "", err
	}
	return NewDigest(archive), nil
}

// TerminalFacts are the only facts included in the terminal event. Derived
// roots are intentionally absent, avoiding an event-root self-reference.
type TerminalFacts struct {
	ResultCode  string `json:"result_code"`
	WinnerSlot  uint32 `json:"winner_slot"`
	OutcomeHash Digest `json:"outcome_hash"`
}

func (f TerminalFacts) CanonicalBytes() ([]byte, error) {
	if err := validateText("result_code", f.ResultCode); err != nil {
		return nil, err
	}
	if f.WinnerSlot > 2 {
		return nil, errors.New("winner_slot must be 0, 1, or 2")
	}
	if err := f.OutcomeHash.Validate(); err != nil {
		return nil, fmt.Errorf("outcome_hash: %w", err)
	}
	fr := newFrame("trnm_match_terminal_facts_v1").string(f.ResultCode).u32(f.WinnerSlot)
	var err error
	if fr, err = fr.digest(f.OutcomeHash); err != nil {
		return nil, err
	}
	return fr.result()
}

type MatchCompletedV1 struct {
	Schema                string        `json:"schema"`
	CommitmentID          Digest        `json:"commitment_id"`
	MatchID               string        `json:"match_id"`
	ChallengeID           string        `json:"challenge_id"`
	TerminalFacts         TerminalFacts `json:"terminal_facts"`
	EventCount            uint64        `json:"event_count"`
	EventRoot             Digest        `json:"event_root"`
	RosterRoot            Digest        `json:"roster_root"`
	RulesetHash           Digest        `json:"ruleset_hash"`
	DatasetHash           Digest        `json:"dataset_hash"`
	ChallengeSnapshotHash Digest        `json:"challenge_snapshot_hash"`
	ArchiveHash           Digest        `json:"archive_hash"`
	CompletedAtUnix       int64         `json:"completed_at_unix"`
	AuthorityKeyID        string        `json:"authority_key_id"`
	Signature             []byte        `json:"signature"`
}

func (c MatchCompletedV1) signingBytes() ([]byte, error) {
	if c.Schema != CompletionSchema || c.EventCount == 0 || c.CompletedAtUnix < 0 {
		return nil, errors.New("completion schema, event count, or time is invalid")
	}
	for name, value := range map[string]string{
		"match_id": c.MatchID, "challenge_id": c.ChallengeID,
		"authority_key_id": c.AuthorityKeyID,
	} {
		if err := validateText(name, value); err != nil {
			return nil, err
		}
	}
	if err := ValidateLogicalMatchID(c.MatchID); err != nil {
		return nil, err
	}
	if err := c.CommitmentID.Validate(); err != nil {
		return nil, fmt.Errorf("commitment_id: %w", err)
	}
	expectedCommitmentID, err := CommitmentID(c.MatchID, c.EventRoot, c.ArchiveHash)
	if err != nil {
		return nil, err
	}
	if c.CommitmentID != expectedCommitmentID {
		return nil, errors.New("commitment_id does not match match and archive roots")
	}
	terminalFacts, err := c.TerminalFacts.CanonicalBytes()
	if err != nil {
		return nil, fmt.Errorf("terminal_facts: %w", err)
	}
	f := newFrame("trnm_match_completed_signature_v1").string(c.Schema)
	if f, err = f.digest(c.CommitmentID); err != nil {
		return nil, err
	}
	f = f.
		string(c.MatchID).
		string(c.ChallengeID).
		bytes(terminalFacts).
		u64(c.EventCount)
	for _, digest := range []Digest{c.EventRoot, c.RosterRoot, c.RulesetHash, c.DatasetHash, c.ChallengeSnapshotHash, c.ArchiveHash} {
		if f, err = f.digest(digest); err != nil {
			return nil, err
		}
	}
	f = f.i64(c.CompletedAtUnix).string(c.AuthorityKeyID)
	return f.result()
}

func SignCompletion(completion MatchCompletedV1, privateKey ed25519.PrivateKey) (MatchCompletedV1, error) {
	if err := validateEd25519PrivateKey(privateKey, "authority"); err != nil {
		return MatchCompletedV1{}, err
	}
	completion.Signature = nil
	message, err := completion.signingBytes()
	if err != nil {
		return MatchCompletedV1{}, err
	}
	completion.Signature = ed25519.Sign(privateKey, message)
	return completion, nil
}

func VerifyCompletion(completion MatchCompletedV1, authorityPublicKey ed25519.PublicKey) error {
	if len(authorityPublicKey) != ed25519.PublicKeySize || len(completion.Signature) != ed25519.SignatureSize {
		return errors.New("authority key or completion signature has invalid length")
	}
	message, err := completion.signingBytes()
	if err != nil {
		return err
	}
	if !ed25519.Verify(authorityPublicKey, message, completion.Signature) {
		return errors.New("completion signature verification failed")
	}
	return nil
}

func CommitmentID(matchID string, eventRoot, archiveHash Digest) (Digest, error) {
	if err := ValidateLogicalMatchID(matchID); err != nil {
		return "", err
	}
	f := newFrame("trnm_match_commitment_id_v1").string(matchID)
	var err error
	if f, err = f.digest(eventRoot); err != nil {
		return "", err
	}
	if f, err = f.digest(archiveHash); err != nil {
		return "", err
	}
	encoded, err := f.result()
	if err != nil {
		return "", err
	}
	return NewDigest(encoded), nil
}
