package researchcontract

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
)

const (
	EventSchema      = "trnm.research-session.event.v1"
	CompletionSchema = "trnm.research-session.completed.v1"
)

type ResearchEvent struct {
	Schema          string `json:"schema"`
	EventID         string `json:"event_id"`
	EventType       string `json:"event_type"`
	SessionID       string `json:"session_id"`
	TeamID          string `json:"team_id"`
	PaperProjectID  string `json:"paper_project_id"`
	ChallengeID     string `json:"challenge_id"`
	RosterVersion   uint64 `json:"roster_version"`
	Sequence        uint64 `json:"sequence"`
	CausationID     string `json:"causation_id"`
	OccurredAtUnix  int64  `json:"occurred_at_unix"`
	ParticipantSlot uint32 `json:"participant_slot"`
	SessionVersion  uint64 `json:"session_version"`
	ActionType      string `json:"action_type"`
	PayloadType     string `json:"payload_type"`
	Payload         []byte `json:"payload"`
	PayloadHash     Digest `json:"payload_hash"`
	ReferenceHash   Digest `json:"reference_hash"`
	EventHash       Digest `json:"event_hash"`
}

func (e ResearchEvent) FactsBytes() ([]byte, error) {
	if e.Schema != EventSchema {
		return nil, fmt.Errorf("unsupported event schema %q", e.Schema)
	}
	for name, value := range map[string]string{
		"event_id": e.EventID, "event_type": e.EventType,
		"causation_id": e.CausationID, "action_type": e.ActionType, "payload_type": e.PayloadType,
	} {
		if err := validateText(name, value); err != nil {
			return nil, err
		}
	}
	if err := ValidateSessionID(e.SessionID); err != nil {
		return nil, err
	}
	if err := ValidateTeamID(e.TeamID); err != nil {
		return nil, err
	}
	if err := ValidatePaperProjectID(e.PaperProjectID); err != nil {
		return nil, err
	}
	if err := ValidateChallengeID(e.ChallengeID); err != nil {
		return nil, err
	}
	if e.RosterVersion == 0 || e.Sequence == 0 || e.SessionVersion == 0 {
		return nil, errors.New("roster, event, and session versions must be positive")
	}
	if e.ParticipantSlot > MaxParticipants {
		return nil, errors.New("event participant_slot is outside 0..5")
	}
	if e.OccurredAtUnix < 0 || len(e.Payload) == 0 || len(e.Payload) > MaxPayloadBytes {
		return nil, errors.New("event time or payload is invalid")
	}
	if e.PayloadHash != NewDigest(e.Payload) {
		return nil, errors.New("event payload_hash does not match payload")
	}
	if err := e.ReferenceHash.Validate(); err != nil {
		return nil, fmt.Errorf("reference_hash: %w", err)
	}
	return newFrame("trnm_research_session_event_v1").
		string(e.Schema).string(e.EventID).string(e.EventType).string(e.SessionID).
		string(e.TeamID).string(e.PaperProjectID).string(e.ChallengeID).
		u64(e.RosterVersion).u64(e.Sequence).string(e.CausationID).i64(e.OccurredAtUnix).
		u32(e.ParticipantSlot).u64(e.SessionVersion).string(e.ActionType).
		string(e.PayloadType).bytes(e.Payload).digest(e.PayloadHash).digest(e.ReferenceHash).result()
}

func ComputeEventHash(event ResearchEvent) (Digest, error) {
	facts, err := event.FactsBytes()
	if err != nil {
		return "", err
	}
	return NewDigest(facts), nil
}

func SealEvent(event ResearchEvent) (ResearchEvent, error) {
	event.PayloadHash = NewDigest(event.Payload)
	hash, err := ComputeEventHash(event)
	if err != nil {
		return ResearchEvent{}, err
	}
	event.EventHash = hash
	return event, nil
}

func (e ResearchEvent) Validate() error {
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

func CanonicalEventID(sessionID string, sequence uint64, causationID string) (string, error) {
	if err := ValidateSessionID(sessionID); err != nil {
		return "", err
	}
	if sequence == 0 {
		return "", errors.New("event sequence must be positive")
	}
	if err := validateText("causation_id", causationID); err != nil {
		return "", err
	}
	encoded, err := newFrame("trnm_research_session_event_id_v1").
		string(sessionID).string(causationID).u64(sequence).result()
	if err != nil {
		return "", err
	}
	return string(NewDigest(encoded)), nil
}

var eventTypes = map[string]struct{}{
	"participant_joined": {}, "participant_disconnected": {}, "participant_reconnected": {},
	"research_action_applied": {}, "roster_replaced": {}, "research_session_completed": {},
}

func ValidateArchive(events []ResearchEvent) error {
	if len(events) == 0 {
		return errors.New("event archive is empty")
	}
	first := events[0]
	seenID := make(map[string]struct{}, len(events))
	seenHash := make(map[Digest]struct{}, len(events))
	var previousTime int64
	for index, event := range events {
		if event.SessionID != first.SessionID || event.TeamID != first.TeamID ||
			event.PaperProjectID != first.PaperProjectID || event.ChallengeID != first.ChallengeID {
			return fmt.Errorf("event %d has a different session identity", index)
		}
		if event.Sequence != uint64(index+1) || event.SessionVersion != event.Sequence+1 {
			return fmt.Errorf("event %d has a non-contiguous sequence or version", index)
		}
		if index > 0 && event.OccurredAtUnix < previousTime {
			return fmt.Errorf("event %d time moves backwards", index)
		}
		previousTime = event.OccurredAtUnix
		if _, ok := eventTypes[event.EventType]; !ok {
			return fmt.Errorf("event %d has unsupported type %q", index, event.EventType)
		}
		if event.EventType == "research_session_completed" && index != len(events)-1 {
			return fmt.Errorf("event %d completion is not terminal", index)
		}
		if event.EventType == "research_session_completed" && event.ParticipantSlot != 0 {
			return fmt.Errorf("event %d completion has a participant slot", index)
		}
		if event.EventType != "research_session_completed" && event.ParticipantSlot == 0 {
			return fmt.Errorf("event %d non-completion has no participant slot", index)
		}
		if err := event.Validate(); err != nil {
			return fmt.Errorf("event %d: %w", index, err)
		}
		expected, err := CanonicalEventID(event.SessionID, event.Sequence, event.CausationID)
		if err != nil || expected != event.EventID {
			return fmt.Errorf("event %d event_id is not canonical", index)
		}
		if _, ok := seenID[event.EventID]; ok {
			return fmt.Errorf("event %d duplicates event_id", index)
		}
		if _, ok := seenHash[event.EventHash]; ok {
			return fmt.Errorf("event %d duplicates event_hash", index)
		}
		seenID[event.EventID] = struct{}{}
		seenHash[event.EventHash] = struct{}{}
	}
	return nil
}

type EventCommitment struct {
	Sequence  uint64 `json:"sequence"`
	EventHash Digest `json:"event_hash"`
}

func EventRoot(events []ResearchEvent) (Digest, error) {
	if err := ValidateArchive(events); err != nil {
		return "", err
	}
	commitments := make([]EventCommitment, len(events))
	for i := range events {
		commitments[i] = EventCommitment{events[i].Sequence, events[i].EventHash}
	}
	return EventRootFromCommitments(commitments)
}

func EventRootFromCommitments(commitments []EventCommitment) (Digest, error) {
	if len(commitments) == 0 {
		return "", errors.New("cannot compute event root for empty archive")
	}
	level := make([][sha256.Size]byte, len(commitments))
	for index, commitment := range commitments {
		if commitment.Sequence != uint64(index+1) {
			return "", fmt.Errorf("event sequence gap at %d", index)
		}
		hash, err := commitment.EventHash.Bytes()
		if err != nil {
			return "", err
		}
		input := append([]byte("trnm_research_session_event_leaf_v1\x00"), make([]byte, 8)...)
		binary.BigEndian.PutUint64(input[len(input)-8:], commitment.Sequence)
		input = append(input, hash[:]...)
		level[index] = sha256.Sum256(input)
	}
	for len(level) > 1 {
		next := make([][sha256.Size]byte, 0, (len(level)+1)/2)
		for i := 0; i < len(level); i += 2 {
			right := level[i]
			if i+1 < len(level) {
				right = level[i+1]
			}
			input := append([]byte("trnm_research_session_merkle_node_v1\x00"), level[i][:]...)
			input = append(input, right[:]...)
			next = append(next, sha256.Sum256(input))
		}
		level = next
	}
	return Digest(digestPrefix + fmt.Sprintf("%x", level[0][:])), nil
}

func CanonicalArchive(events []ResearchEvent) ([]byte, error) {
	if err := ValidateArchive(events); err != nil {
		return nil, err
	}
	f := newFrame("trnm_research_session_event_archive_v1").u64(uint64(len(events)))
	for _, event := range events {
		facts, err := event.FactsBytes()
		if err != nil {
			return nil, err
		}
		hash, err := event.EventHash.Bytes()
		if err != nil {
			return nil, err
		}
		f = f.bytes(facts).bytes(hash[:])
	}
	return f.result()
}

func ArchiveHash(events []ResearchEvent) (Digest, error) {
	archive, err := CanonicalArchive(events)
	if err != nil {
		return "", err
	}
	return NewDigest(archive), nil
}

type TerminalFacts struct {
	ResultCode                string `json:"result_code"`
	PaperBundleHash           Digest `json:"paper_bundle_hash"`
	PaperReleaseCandidateHash Digest `json:"paper_release_candidate_hash"`
	ContributionLedgerHash    Digest `json:"contribution_ledger_hash"`
}

func (facts TerminalFacts) CanonicalBytes() ([]byte, error) {
	if err := validateText("result_code", facts.ResultCode); err != nil {
		return nil, err
	}
	for name, value := range map[string]Digest{
		"paper_bundle_hash":            facts.PaperBundleHash,
		"paper_release_candidate_hash": facts.PaperReleaseCandidateHash,
		"contribution_ledger_hash":     facts.ContributionLedgerHash,
	} {
		if err := value.Validate(); err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
	}
	return newFrame("trnm_research_session_terminal_facts_v1").string(facts.ResultCode).
		digest(facts.PaperBundleHash).digest(facts.PaperReleaseCandidateHash).
		digest(facts.ContributionLedgerHash).result()
}

type SessionCompletedV1 struct {
	Schema                string        `json:"schema"`
	CommitmentID          Digest        `json:"commitment_id"`
	SessionID             string        `json:"session_id"`
	TeamID                string        `json:"team_id"`
	PaperProjectID        string        `json:"paper_project_id"`
	ChallengeID           string        `json:"challenge_id"`
	RosterVersion         uint64        `json:"roster_version"`
	RosterRoot            Digest        `json:"roster_root"`
	TerminalFacts         TerminalFacts `json:"terminal_facts"`
	EventCount            uint64        `json:"event_count"`
	EventRoot             Digest        `json:"event_root"`
	ArchiveHash           Digest        `json:"archive_hash"`
	RulesetHash           Digest        `json:"ruleset_hash"`
	ChallengeSnapshotHash Digest        `json:"challenge_snapshot_hash"`
	CompletedAtUnix       int64         `json:"completed_at_unix"`
	AuthorityKeyID        string        `json:"authority_key_id"`
	Signature             []byte        `json:"signature"`
}

func CommitmentID(sessionID string, eventRoot, archiveHash Digest) (Digest, error) {
	if err := ValidateSessionID(sessionID); err != nil {
		return "", err
	}
	encoded, err := newFrame("trnm_research_session_commitment_id_v1").string(sessionID).
		digest(eventRoot).digest(archiveHash).result()
	if err != nil {
		return "", err
	}
	return NewDigest(encoded), nil
}

func (completion SessionCompletedV1) SigningBytes() ([]byte, error) {
	if completion.Schema != CompletionSchema || completion.RosterVersion == 0 ||
		completion.EventCount == 0 || completion.CompletedAtUnix < 0 {
		return nil, errors.New("completion schema, version, count, or time is invalid")
	}
	if err := ValidateSessionID(completion.SessionID); err != nil {
		return nil, err
	}
	for name, value := range map[string]string{"authority_key_id": completion.AuthorityKeyID} {
		if err := validateText(name, value); err != nil {
			return nil, err
		}
	}
	if err := ValidateTeamID(completion.TeamID); err != nil {
		return nil, err
	}
	if err := ValidatePaperProjectID(completion.PaperProjectID); err != nil {
		return nil, err
	}
	if err := ValidateChallengeID(completion.ChallengeID); err != nil {
		return nil, err
	}
	for name, value := range map[string]Digest{
		"commitment_id": completion.CommitmentID, "roster_root": completion.RosterRoot,
		"event_root": completion.EventRoot, "archive_hash": completion.ArchiveHash,
		"ruleset_hash": completion.RulesetHash, "challenge_snapshot_hash": completion.ChallengeSnapshotHash,
	} {
		if err := value.Validate(); err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
	}
	expected, err := CommitmentID(completion.SessionID, completion.EventRoot, completion.ArchiveHash)
	if err != nil || expected != completion.CommitmentID {
		return nil, errors.New("completion commitment_id is invalid")
	}
	terminal, err := completion.TerminalFacts.CanonicalBytes()
	if err != nil {
		return nil, err
	}
	return newFrame("trnm_research_session_completed_signature_v1").string(completion.Schema).
		digest(completion.CommitmentID).string(completion.SessionID).string(completion.TeamID).
		string(completion.PaperProjectID).string(completion.ChallengeID).u64(completion.RosterVersion).
		digest(completion.RosterRoot).bytes(terminal).u64(completion.EventCount).
		digest(completion.EventRoot).digest(completion.ArchiveHash).digest(completion.RulesetHash).
		digest(completion.ChallengeSnapshotHash).i64(completion.CompletedAtUnix).
		string(completion.AuthorityKeyID).result()
}

func SignCompletion(completion SessionCompletedV1, key ed25519.PrivateKey) (SessionCompletedV1, error) {
	if err := validatePrivateKey(key, "authority"); err != nil {
		return SessionCompletedV1{}, err
	}
	completion.Signature = nil
	message, err := completion.SigningBytes()
	if err != nil {
		return SessionCompletedV1{}, err
	}
	completion.Signature = ed25519.Sign(key, message)
	return completion, nil
}

func VerifyCompletion(completion SessionCompletedV1, key ed25519.PublicKey) error {
	if len(key) != ed25519.PublicKeySize || len(completion.Signature) != ed25519.SignatureSize {
		return errors.New("authority key or completion signature has invalid length")
	}
	message, err := completion.SigningBytes()
	if err != nil {
		return err
	}
	if !ed25519.Verify(key, message, completion.Signature) {
		return errors.New("completion signature verification failed")
	}
	return nil
}

// VerifyCompletionAgainstArchive proves that a signed completion is not only
// structurally valid, but was produced by the authoritative cooperative
// session lifecycle. In particular, its roots cover the complete archive and
// the last event commits the exact terminal facts carried by the credential.
func VerifyCompletionAgainstArchive(completion SessionCompletedV1, events []ResearchEvent, key ed25519.PublicKey) error {
	if err := VerifyCompletion(completion, key); err != nil {
		return err
	}
	if err := ValidateArchive(events); err != nil {
		return err
	}
	if completion.EventCount != uint64(len(events)) {
		return errors.New("completion event_count does not cover the archive")
	}
	last := events[len(events)-1]
	if last.EventType != "research_session_completed" || last.ParticipantSlot != 0 ||
		last.ActionType != "server.complete" || last.PayloadType != "trnm.research-session.terminal-facts.v1" {
		return errors.New("archive has no canonical terminal completion event")
	}
	if last.SessionID != completion.SessionID || last.TeamID != completion.TeamID ||
		last.PaperProjectID != completion.PaperProjectID || last.ChallengeID != completion.ChallengeID ||
		last.RosterVersion != completion.RosterVersion || last.ReferenceHash != completion.TerminalFacts.PaperReleaseCandidateHash ||
		last.OccurredAtUnix != completion.CompletedAtUnix {
		return errors.New("terminal completion event identity differs from credential")
	}
	terminal, err := completion.TerminalFacts.CanonicalBytes()
	if err != nil {
		return err
	}
	if !bytes.Equal(last.Payload, terminal) || last.CausationID != string(NewDigest(terminal)) {
		return errors.New("terminal completion event payload differs from credential")
	}
	eventRoot, err := EventRoot(events)
	if err != nil || eventRoot != completion.EventRoot {
		return errors.New("completion event_root does not cover the archive")
	}
	archiveHash, err := ArchiveHash(events)
	if err != nil || archiveHash != completion.ArchiveHash {
		return errors.New("completion archive_hash does not cover the archive")
	}
	commitmentID, err := CommitmentID(completion.SessionID, eventRoot, archiveHash)
	if err != nil || commitmentID != completion.CommitmentID {
		return errors.New("completion commitment_id does not bind the archive")
	}
	return nil
}
