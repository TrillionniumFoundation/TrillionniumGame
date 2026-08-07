package core

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
)

const (
	snapshotSchema          = "trnm.match.snapshot.v2"
	maxSnapshotPayloadBytes = 64 * 1024 * 1024
)

var (
	snapshotMagic = [8]byte{'T', 'R', 'N', 'M', 'S', 'N', 'P', '2'}

	// ErrAuthorityVerificationKeyUnavailable distinguishes an intentionally
	// retired public key from corruption or signature failure. Callers still
	// fail closed, but can expose an operator-actionable rotation error.
	ErrAuthorityVerificationKeyUnavailable = errors.New("authority verification key is unavailable")
)

type snapshotDocument struct {
	Schema             string                     `json:"schema"`
	MatchID            string                     `json:"match_id"`
	ChallengeID        string                     `json:"challenge_id"`
	Status             Status                     `json:"status"`
	Version            uint64                     `json:"version"`
	Participants       [2]participantState        `json:"participants"`
	Commands           []commandRecord            `json:"commands"`
	Events             []contract.MatchEvent      `json:"events"`
	TerminalFacts      *contract.TerminalFacts    `json:"terminal_facts,omitempty"`
	Completion         *contract.MatchCompletedV1 `json:"completion,omitempty"`
	AuthorityKeyID     string                     `json:"authority_key_id"`
	AuthorityPublicKey []byte                     `json:"authority_public_key"`
}

func (e *Engine) Snapshot() ([]byte, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	payload, err := e.marshalSnapshotState(e.status, e.version, e.participants, e.commands, e.events, e.terminalFacts, e.completion)
	if err != nil {
		return nil, err
	}
	if len(payload) > maxSnapshotPayloadBytes {
		return nil, errors.New("snapshot payload exceeds the durable v2 limit")
	}
	checksumInput := append([]byte("trnm_match_snapshot_checksum_v2\x00"), payload...)
	checksum := sha256.Sum256(checksumInput)
	signingBytes, err := snapshotSigningBytes(e.authorityKeyID, payload, checksum)
	if err != nil {
		return nil, err
	}
	signature := ed25519.Sign(e.authorityPrivateKey, signingBytes)
	out := append([]byte(nil), snapshotMagic[:]...)
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(payload)))
	out = append(out, size[:]...)
	out = append(out, payload...)
	out = append(out, checksum[:]...)
	out = append(out, signature...)
	return out, nil
}

func (e *Engine) marshalSnapshotState(status Status, version uint64, participants [2]participantState, commands map[string]commandRecord, events []contract.MatchEvent, terminalFacts *contract.TerminalFacts, completion *contract.MatchCompletedV1) ([]byte, error) {
	document := snapshotDocument{
		Schema: snapshotSchema, MatchID: e.matchID, ChallengeID: e.challengeID, Status: status,
		Version: version, Participants: participants, Commands: sortedCommandRecords(commands),
		Events: events, TerminalFacts: terminalFacts, Completion: completion,
		AuthorityKeyID: e.authorityKeyID, AuthorityPublicKey: e.authorityPublicKey,
	}
	return json.Marshal(document)
}

func (e *Engine) ensureSnapshotCapacity(status Status, version uint64, participants [2]participantState, commands map[string]commandRecord, events []contract.MatchEvent, terminalFacts *contract.TerminalFacts, completion *contract.MatchCompletedV1, reserve int) error {
	if reserve < 0 || reserve > maxSnapshotPayloadBytes {
		return fmt.Errorf("%w: snapshot reserve is invalid", ErrCapacity)
	}
	payload, err := e.marshalSnapshotState(status, version, participants, commands, events, terminalFacts, completion)
	if err != nil {
		return fmt.Errorf("%w: could not size candidate snapshot: %v", ErrCapacity, err)
	}
	if len(payload) > maxSnapshotPayloadBytes-reserve {
		return fmt.Errorf("%w: candidate snapshot leaves insufficient completion reserve", ErrCapacity)
	}
	return nil
}

func Restore(snapshot []byte, options RestoreOptions) (*Engine, error) {
	const trailerSize = sha256.Size + ed25519.SignatureSize
	if len(snapshot) < len(snapshotMagic)+8+trailerSize || !bytes.Equal(snapshot[:len(snapshotMagic)], snapshotMagic[:]) {
		return nil, errors.New("snapshot header is invalid")
	}
	payloadSize := binary.BigEndian.Uint64(snapshot[len(snapshotMagic) : len(snapshotMagic)+8])
	if payloadSize > maxSnapshotPayloadBytes || payloadSize != uint64(len(snapshot)-(len(snapshotMagic)+8+trailerSize)) {
		return nil, errors.New("snapshot length is invalid")
	}
	payloadStart := len(snapshotMagic) + 8
	payloadEnd := payloadStart + int(payloadSize)
	payload := snapshot[payloadStart:payloadEnd]
	checksum := snapshot[payloadEnd : payloadEnd+sha256.Size]
	expected := sha256.Sum256(append([]byte("trnm_match_snapshot_checksum_v2\x00"), payload...))
	if subtle.ConstantTimeCompare(expected[:], checksum) != 1 {
		return nil, errors.New("snapshot checksum verification failed")
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var document snapshotDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("snapshot payload is invalid: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, errors.New("snapshot payload contains trailing data")
	}
	if document.Schema != snapshotSchema {
		return nil, fmt.Errorf("unsupported snapshot schema %q", document.Schema)
	}
	activePublic, err := validateAuthoritySigningConfiguration(options.AuthorityKeyID, options.AuthorityPrivateKey)
	if err != nil {
		return nil, errors.New("active snapshot signing key is not configured")
	}
	registeredActive := options.AuthorityPublicKeys[options.AuthorityKeyID]
	if len(registeredActive) != ed25519.PublicKeySize || !bytes.Equal(registeredActive, activePublic) {
		return nil, errors.New("active snapshot signing key does not match the public verification registry")
	}
	verificationPublic := options.AuthorityPublicKeys[document.AuthorityKeyID]
	if len(verificationPublic) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("%w: snapshot authority key %q is not in the public verification registry",
			ErrAuthorityVerificationKeyUnavailable, document.AuthorityKeyID)
	}
	var checksumArray [sha256.Size]byte
	copy(checksumArray[:], checksum)
	signingBytes, err := snapshotSigningBytes(document.AuthorityKeyID, payload, checksumArray)
	if err != nil {
		return nil, err
	}
	if !ed25519.Verify(verificationPublic, signingBytes, snapshot[payloadEnd+sha256.Size:]) {
		return nil, errors.New("snapshot authority signature verification failed")
	}
	if !bytes.Equal(verificationPublic, document.AuthorityPublicKey) {
		return nil, errors.New("snapshot authority does not match the public verification registry")
	}
	engine := &Engine{
		matchID: document.MatchID, challengeID: document.ChallengeID, status: document.Status,
		version: document.Version, participants: document.Participants, commands: make(map[string]commandRecord),
		events: document.Events, terminalFacts: document.TerminalFacts, completion: document.Completion,
		trustedIssuerKeys: cloneKeyMap(options.TrustedIssuerKeys), authorityKeyID: options.AuthorityKeyID,
		authorityPrivateKey: append(ed25519.PrivateKey(nil), options.AuthorityPrivateKey...),
		authorityPublicKey:  append(ed25519.PublicKey(nil), activePublic...),
		authorityPublicKeys: cloneKeyMap(options.AuthorityPublicKeys),
	}
	for _, record := range document.Commands {
		if _, exists := engine.commands[record.Command.CommandID]; exists {
			return nil, errors.New("snapshot contains duplicate command IDs")
		}
		engine.commands[record.Command.CommandID] = record
	}
	if err := engine.validateRecordedCommandQuota(); err != nil {
		return nil, err
	}
	if err := engine.validateRestoredState(); err != nil {
		return nil, err
	}
	return engine, nil
}

func snapshotSigningBytes(authorityKeyID string, payload []byte, checksum [sha256.Size]byte) ([]byte, error) {
	if err := contract.ValidateKeyID(authorityKeyID); err != nil {
		return nil, fmt.Errorf("snapshot authority key ID is invalid: %w", err)
	}
	if len(payload) > maxSnapshotPayloadBytes {
		return nil, errors.New("snapshot payload exceeds the durable v2 limit")
	}
	out := append([]byte("trnm_match_snapshot_signature_v2\x00"), make([]byte, 4)...)
	binary.BigEndian.PutUint32(out[len(out)-4:], uint32(len(authorityKeyID)))
	out = append(out, authorityKeyID...)
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(payload)))
	out = append(out, size[:]...)
	out = append(out, payload...)
	out = append(out, checksum[:]...)
	return out, nil
}

func (e *Engine) validateRestoredState() error {
	if contract.ValidateLogicalMatchID(e.matchID) != nil || e.challengeID == "" || e.version != uint64(len(e.events))+1 {
		return errors.New("snapshot match identity or version is inconsistent")
	}
	for index := range e.participants {
		auth := e.participants[index].Authorization
		if auth.Claim.ParticipantSlot != uint32(index+1) {
			return errors.New("snapshot participant slots are inconsistent")
		}
		if err := contract.VerifyAuthorizationSignature(auth, e.trustedIssuerKeys); err != nil {
			return fmt.Errorf("snapshot authorization is invalid: %w", err)
		}
	}
	if err := e.validateConfiguration(); err != nil {
		return err
	}
	if e.matchID != e.participants[0].Authorization.Claim.MatchID || e.challengeID != e.participants[0].Authorization.Claim.ChallengeID {
		return errors.New("snapshot identities differ from authorization snapshot")
	}
	if len(e.events) > 0 {
		if e.events[0].MatchID != e.matchID || e.events[0].ChallengeID != e.challengeID {
			return errors.New("snapshot archive identity differs from the match")
		}
		if err := contract.ValidateArchive(e.events); err != nil {
			return fmt.Errorf("snapshot archive is invalid: %w", err)
		}
	}

	// First bind every command record to exactly one complete event envelope.
	// A hash-only lookup is insufficient because a forged record could retain a
	// valid archive hash while changing non-hashed in-memory fields or payload
	// slice identity. Sequence is the canonical archive key and DeepEqual binds
	// the entire decoded envelope.
	commandsByEventSequence := make(map[uint64]commandRecord, len(e.commands))
	for commandID, record := range e.commands {
		if record.Command.CommandID != commandID {
			return errors.New("snapshot command map key is inconsistent")
		}
		claimIndex := int(record.Command.ParticipantSlot) - 1
		if claimIndex < 0 || claimIndex >= len(e.participants) {
			return errors.New("snapshot command has invalid participant slot")
		}
		claim := e.participants[claimIndex].Authorization.Claim
		if record.Command.AuthorizationID != claim.AuthorizationID || record.Command.MatchID != e.matchID ||
			record.Command.ChallengeID != e.challengeID || record.Command.AgentID != claim.AgentID ||
			record.Command.AgentKeyID != claim.AgentKeyID || record.Command.ParticipantSlot != claim.ParticipantSlot {
			return errors.New("snapshot command identity or version is inconsistent")
		}
		if err := contract.VerifyCommand(record.Command, ed25519.PublicKey(claim.AgentPublicKey)); err != nil {
			return fmt.Errorf("snapshot command signature is invalid: %w", err)
		}
		fingerprint, err := contract.CommandFingerprint(record.Command)
		if err != nil || fingerprint != record.Fingerprint {
			return errors.New("snapshot command fingerprint is invalid")
		}
		if record.Event.Sequence == 0 || record.Event.Sequence > uint64(len(e.events)) {
			return errors.New("snapshot command event sequence is outside the event archive")
		}
		archiveEvent := e.events[record.Event.Sequence-1]
		if !reflect.DeepEqual(record.Event, archiveEvent) {
			return errors.New("snapshot command event does not exactly match its archive envelope")
		}
		if _, exists := commandsByEventSequence[record.Event.Sequence]; exists {
			return errors.New("snapshot maps multiple commands to one archive event")
		}
		commandsByEventSequence[record.Event.Sequence] = record
	}

	joinedSlots := [2]bool{}
	participantSequences := [2]uint64{}
	commandEvents := 0
	terminalSeen := false
	for index, event := range e.events {
		sequence := uint64(index + 1)

		switch event.EventType {
		case "participant_joined":
			if commandEvents != 0 || terminalSeen || event.ParticipantSlot < 1 || event.ParticipantSlot > 2 {
				return errors.New("snapshot join event occurs outside the admission phase")
			}
			participantIndex := int(event.ParticipantSlot - 1)
			if joinedSlots[participantIndex] || !e.participants[participantIndex].Joined {
				return errors.New("snapshot join event does not match participant state")
			}
			claim := e.participants[participantIndex].Authorization.Claim
			expectedPayload := encodeJoinPayload(claim.ParticipantSlot, claim.SubjectUserID, claim.AuthorizationID, claim.AgentID)
			if event.CausationID != claim.AuthorizationID || event.PayloadType != "trnm.participant.joined.v1" ||
				!bytes.Equal(event.Payload, expectedPayload) || event.OccurredAtUnix < claim.IssuedAtUnix ||
				event.OccurredAtUnix >= claim.ExpiresAtUnix {
				return errors.New("snapshot join event facts are inconsistent")
			}
			if _, mapped := commandsByEventSequence[sequence]; mapped {
				return errors.New("snapshot command record maps to a join event")
			}
			joinedSlots[participantIndex] = true

		case "agent_command_applied":
			if !joinedSlots[0] || !joinedSlots[1] || terminalSeen {
				return errors.New("snapshot command event occurs outside the active phase")
			}
			record, ok := commandsByEventSequence[sequence]
			if !ok {
				return errors.New("snapshot command event has no unique command record")
			}
			participantIndex := int(record.Command.ParticipantSlot - 1)
			claim := e.participants[participantIndex].Authorization.Claim
			participantSequences[participantIndex]++
			if record.Command.ParticipantSequence != participantSequences[participantIndex] ||
				record.Command.ExpectedMatchVersion != sequence || record.Event.MatchVersion != sequence+1 ||
				event.CausationID != record.Command.CommandID || event.ParticipantSlot != claim.ParticipantSlot ||
				event.PayloadType != record.Command.PayloadType || !bytes.Equal(event.Payload, record.Command.Payload) ||
				record.Command.IssuedAtUnix < claim.IssuedAtUnix || record.Command.IssuedAtUnix > event.OccurredAtUnix {
				return errors.New("snapshot command event facts or replay order are inconsistent")
			}
			commandEvents++

		case "match_completed":
			if index != len(e.events)-1 || !joinedSlots[0] || !joinedSlots[1] || commandEvents == 0 || terminalSeen ||
				event.ParticipantSlot != 0 || e.terminalFacts == nil || e.completion == nil {
				return errors.New("snapshot terminal event occurs outside the completion transition")
			}
			terminalBytes, err := e.terminalFacts.CanonicalBytes()
			if err != nil {
				return errors.New("snapshot terminal facts are invalid")
			}
			expectedCausation := string(contract.NewDigest(append([]byte("authority:complete:"), terminalBytes...)))
			if event.CausationID != expectedCausation || event.PayloadType != "trnm.match.terminal-facts.v1" ||
				!bytes.Equal(event.Payload, terminalBytes) || event.OccurredAtUnix != e.completion.CompletedAtUnix {
				return errors.New("snapshot terminal event facts are inconsistent")
			}
			if _, mapped := commandsByEventSequence[sequence]; mapped {
				return errors.New("snapshot command record maps to the terminal event")
			}
			terminalSeen = true

		default:
			return fmt.Errorf("snapshot contains unsupported event type %q", event.EventType)
		}
	}
	if commandEvents != len(e.commands) {
		return errors.New("snapshot command records and archive events are not one-to-one")
	}
	for index, participant := range e.participants {
		if participant.Joined != joinedSlots[index] || participant.LastCommandSequence != participantSequences[index] {
			return errors.New("snapshot participant state does not match deterministic event replay")
		}
	}

	joined := 0
	for _, value := range joinedSlots {
		if value {
			joined++
		}
	}
	var replayedStatus Status
	switch {
	case terminalSeen:
		replayedStatus = StatusCompleted
	case commandEvents > 0:
		replayedStatus = StatusActive
	case joined == 2:
		replayedStatus = StatusReady
	case joined == 1:
		replayedStatus = StatusWaiting
	default:
		replayedStatus = StatusCreated
	}
	if e.status != replayedStatus {
		return errors.New("snapshot status does not match deterministic event replay")
	}
	if terminalSeen {
		if err := e.validateCompletion(); err != nil {
			return err
		}
	} else if e.completion != nil || e.terminalFacts != nil {
		return errors.New("incomplete snapshot contains terminal evidence")
	}
	reserve := completionSnapshotReserveBytes
	if terminalSeen {
		reserve = 0
	}
	if err := e.ensureSnapshotCapacity(e.status, e.version, e.participants, e.commands, e.events, e.terminalFacts, e.completion, reserve); err != nil {
		return err
	}
	return nil
}

func (e *Engine) validateCompletion() error {
	completion := *e.completion
	if completion.MatchID != e.matchID || completion.ChallengeID != e.challengeID ||
		completion.EventCount != uint64(len(e.events)) {
		return errors.New("snapshot completion identity is inconsistent")
	}
	eventRoot, err := contract.EventRoot(e.events)
	if err != nil || completion.EventRoot != eventRoot {
		return errors.New("snapshot completion event root is invalid")
	}
	archiveHash, err := contract.ArchiveHash(e.events)
	if err != nil || completion.ArchiveHash != archiveHash {
		return errors.New("snapshot completion archive hash is invalid")
	}
	rosterRoot, err := contract.RosterRoot(e.roster())
	if err != nil || completion.RosterRoot != rosterRoot {
		return errors.New("snapshot completion roster root is invalid")
	}
	claim := e.participants[0].Authorization.Claim
	if completion.RulesetHash != claim.RulesetHash || completion.DatasetHash != claim.DatasetHash ||
		completion.ChallengeSnapshotHash != claim.ChallengeSnapshotHash {
		return errors.New("snapshot completion immutable hashes are invalid")
	}
	completionPublic := e.authorityPublicKeys[completion.AuthorityKeyID]
	if len(completionPublic) != ed25519.PublicKeySize {
		return fmt.Errorf("%w: snapshot completion authority key %q is not in the public verification registry",
			ErrAuthorityVerificationKeyUnavailable, completion.AuthorityKeyID)
	}
	if err := contract.VerifyCompletion(completion, completionPublic); err != nil {
		return fmt.Errorf("snapshot completion signature is invalid: %w", err)
	}
	commitmentID, err := contract.CommitmentID(e.matchID, completion.EventRoot, completion.ArchiveHash)
	if err != nil || completion.CommitmentID != commitmentID {
		return errors.New("snapshot completion commitment ID is invalid")
	}
	terminalBytes, err := e.terminalFacts.CanonicalBytes()
	lastEvent := e.events[len(e.events)-1]
	completionTerminalBytes, completionTerminalErr := completion.TerminalFacts.CanonicalBytes()
	if err != nil || completionTerminalErr != nil || !bytes.Equal(terminalBytes, completionTerminalBytes) ||
		lastEvent.EventType != "match_completed" || lastEvent.PayloadType != "trnm.match.terminal-facts.v1" ||
		!bytes.Equal(lastEvent.Payload, terminalBytes) {
		return errors.New("snapshot terminal event is inconsistent")
	}
	return nil
}
