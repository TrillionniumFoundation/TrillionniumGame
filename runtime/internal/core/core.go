package core

import (
	"bytes"
	"crypto/ed25519"
	"encoding/binary"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
)

type Status string

const (
	StatusCreated   Status = "created"
	StatusWaiting   Status = "waiting"
	StatusReady     Status = "ready"
	StatusActive    Status = "active"
	StatusCompleted Status = "completed"

	// MaxCommandsPerMatch and MaxCumulativeCommandPayloadBytes are explicit P0
	// authority limits. Snapshot headroom is checked independently before each
	// acceptance so these limits cannot strand a match that can no longer emit
	// durable completion evidence.
	MaxCommandsPerMatch              = 512
	MaxCumulativeCommandPayloadBytes = 2 * 1024 * 1024
	completionSnapshotReserveBytes   = 1024 * 1024
)

var (
	ErrAuthorization = errors.New("authorization rejected")
	ErrConflict      = errors.New("idempotency conflict")
	ErrSequence      = errors.New("command sequence rejected")
	ErrVersion       = errors.New("match version rejected")
	ErrState         = errors.New("match state rejected")
	ErrCapacity      = errors.New("match capacity rejected")
)

type NewMatchOptions struct {
	Authorizations      [2]contract.SignedAuthorization
	TrustedIssuerKeys   map[string]ed25519.PublicKey
	AuthorityKeyID      string
	AuthorityPrivateKey ed25519.PrivateKey
	Now                 time.Time
}

type RestoreOptions struct {
	TrustedIssuerKeys   map[string]ed25519.PublicKey
	AuthorityKeyID      string
	AuthorityPrivateKey ed25519.PrivateKey
}

type participantState struct {
	Authorization       contract.SignedAuthorization `json:"authorization"`
	Joined              bool                         `json:"joined"`
	LastCommandSequence uint64                       `json:"last_command_sequence"`
}

type commandRecord struct {
	Command     contract.CommandEnvelope `json:"command"`
	Fingerprint contract.Digest          `json:"fingerprint"`
	Event       contract.MatchEvent      `json:"event"`
}

type Engine struct {
	mu                  sync.Mutex
	matchID             string
	challengeID         string
	status              Status
	version             uint64
	participants        [2]participantState
	commands            map[string]commandRecord
	events              []contract.MatchEvent
	terminalFacts       *contract.TerminalFacts
	completion          *contract.MatchCompletedV1
	trustedIssuerKeys   map[string]ed25519.PublicKey
	authorityKeyID      string
	authorityPrivateKey ed25519.PrivateKey
	authorityPublicKey  ed25519.PublicKey
}

type ParticipantView struct {
	Slot                uint32
	AuthorizationID     string
	SubjectUserID       string
	AgentID             string
	Joined              bool
	LastCommandSequence uint64
}

type View struct {
	MatchID      string
	ChallengeID  string
	Status       Status
	Version      uint64
	EventCount   uint64
	Participants [2]ParticipantView
}

type JoinResult struct {
	Replay  bool
	Event   *contract.MatchEvent
	Status  Status
	Version uint64
}

type ApplyResult struct {
	Replay  bool
	Event   contract.MatchEvent
	Status  Status
	Version uint64
}

func NewMatch(options NewMatchOptions) (*Engine, error) {
	if options.Now.IsZero() || options.Now.Unix() < 0 {
		return nil, errors.New("a non-negative current time is required")
	}
	authorityPublicKey, err := validateAuthoritySigningConfiguration(options.AuthorityKeyID, options.AuthorityPrivateKey)
	if err != nil {
		return nil, err
	}
	engine := &Engine{
		status:              StatusCreated,
		version:             1,
		commands:            make(map[string]commandRecord),
		trustedIssuerKeys:   cloneKeyMap(options.TrustedIssuerKeys),
		authorityKeyID:      options.AuthorityKeyID,
		authorityPrivateKey: append(ed25519.PrivateKey(nil), options.AuthorityPrivateKey...),
	}
	engine.authorityPublicKey = authorityPublicKey

	for _, authorization := range options.Authorizations {
		if err := contract.VerifyAuthorization(authorization, engine.trustedIssuerKeys, options.Now.Unix()); err != nil {
			return nil, fmt.Errorf("%w: %v", ErrAuthorization, err)
		}
		claim := authorization.Claim
		index := int(claim.ParticipantSlot - 1)
		if engine.participants[index].Authorization.Claim.AuthorizationID != "" {
			return nil, fmt.Errorf("%w: duplicate participant slot", ErrAuthorization)
		}
		engine.participants[index].Authorization = cloneAuthorization(authorization)
	}
	if err := engine.validateConfiguration(); err != nil {
		return nil, err
	}
	engine.matchID = engine.participants[0].Authorization.Claim.MatchID
	engine.challengeID = engine.participants[0].Authorization.Claim.ChallengeID
	return engine, nil
}

func (e *Engine) validateConfiguration() error {
	one := e.participants[0].Authorization.Claim
	two := e.participants[1].Authorization.Claim
	if one.AuthorizationID == "" || two.AuthorizationID == "" {
		return fmt.Errorf("%w: roster must contain slots 1 and 2", ErrAuthorization)
	}
	if one.MatchID != two.MatchID || one.ChallengeID != two.ChallengeID ||
		one.RulesetHash != two.RulesetHash || one.DatasetHash != two.DatasetHash ||
		one.ChallengeSnapshotHash != two.ChallengeSnapshotHash {
		return fmt.Errorf("%w: authorization snapshots do not describe one match", ErrAuthorization)
	}
	if one.AuthorizationID == two.AuthorizationID || one.SubjectUserID == two.SubjectUserID ||
		one.AgentID == two.AgentID || one.AgentDID == two.AgentDID || one.AgentKeyID == two.AgentKeyID ||
		bytes.Equal(one.AgentPublicKey, two.AgentPublicKey) {
		return fmt.Errorf("%w: authorization identities and agent keys must be unique", ErrAuthorization)
	}
	return nil
}

func (e *Engine) Join(userID, authorizationID string, now time.Time) (JoinResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return JoinResult{}, fmt.Errorf("%w: completed matches accept no joins", ErrState)
	}
	participant, err := e.findParticipant(authorizationID)
	if err != nil {
		return JoinResult{}, err
	}
	claim := participant.Authorization.Claim
	if userID != claim.SubjectUserID {
		return JoinResult{}, fmt.Errorf("%w: subject_user_id does not match authenticated user", ErrAuthorization)
	}
	if participant.Joined {
		return JoinResult{Replay: true, Status: e.status, Version: e.version}, nil
	}
	if now.IsZero() || contract.VerifyAuthorization(participant.Authorization, e.trustedIssuerKeys, now.Unix()) != nil {
		return JoinResult{}, fmt.Errorf("%w: authorization is no longer valid for first use", ErrAuthorization)
	}
	joined := e.joinedCount() + 1
	var nextStatus Status
	switch joined {
	case 1:
		nextStatus = StatusWaiting
	case 2:
		nextStatus = StatusReady
	default:
		return JoinResult{}, fmt.Errorf("%w: impossible joined participant count", ErrState)
	}
	payload := encodeJoinPayload(claim.ParticipantSlot, claim.SubjectUserID, claim.AuthorizationID, claim.AgentID)
	event, err := e.appendEvent("participant_joined", claim.AuthorizationID, now, claim.ParticipantSlot, "trnm.participant.joined.v1", payload)
	if err != nil {
		return JoinResult{}, err
	}
	participant.Joined = true
	e.status = nextStatus
	copyEvent := cloneEvent(event)
	return JoinResult{Event: &copyEvent, Status: e.status, Version: e.version}, nil
}

func (e *Engine) findParticipant(authorizationID string) (*participantState, error) {
	for index := range e.participants {
		if e.participants[index].Authorization.Claim.AuthorizationID == authorizationID {
			return &e.participants[index], nil
		}
	}
	return nil, fmt.Errorf("%w: authorization_id is not in this match", ErrAuthorization)
}

func (e *Engine) ApplyCommand(userID string, command contract.CommandEnvelope, now time.Time) (ApplyResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return ApplyResult{}, fmt.Errorf("%w: completed matches reject commands", ErrState)
	}
	if e.status != StatusReady && e.status != StatusActive {
		return ApplyResult{}, fmt.Errorf("%w: both participants must join before commands", ErrState)
	}
	participant, err := e.findParticipant(command.AuthorizationID)
	if err != nil {
		return ApplyResult{}, err
	}
	claim := participant.Authorization.Claim
	if !participant.Joined || userID != claim.SubjectUserID || command.MatchID != e.matchID ||
		command.ChallengeID != e.challengeID || command.AgentID != claim.AgentID ||
		command.AgentKeyID != claim.AgentKeyID || command.ParticipantSlot != claim.ParticipantSlot {
		return ApplyResult{}, fmt.Errorf("%w: command identity does not match consumed authorization", ErrAuthorization)
	}
	if err := contract.VerifyCommand(command, ed25519.PublicKey(claim.AgentPublicKey)); err != nil {
		return ApplyResult{}, fmt.Errorf("%w: %v", ErrAuthorization, err)
	}
	fingerprint, err := contract.CommandFingerprint(command)
	if err != nil {
		return ApplyResult{}, err
	}
	if previous, ok := e.commands[command.CommandID]; ok {
		if previous.Fingerprint != fingerprint {
			return ApplyResult{}, fmt.Errorf("%w: command_id was reused with different signed bytes", ErrConflict)
		}
		return ApplyResult{Replay: true, Event: cloneEvent(previous.Event), Status: e.status, Version: e.version}, nil
	}
	if err := e.checkCommandQuota(len(command.Payload)); err != nil {
		return ApplyResult{}, err
	}
	expectedSequence := participant.LastCommandSequence + 1
	if command.ParticipantSequence != expectedSequence {
		return ApplyResult{}, fmt.Errorf("%w: expected participant sequence %d", ErrSequence, expectedSequence)
	}
	if command.ExpectedMatchVersion != e.version {
		return ApplyResult{}, fmt.Errorf("%w: expected match version %d", ErrVersion, e.version)
	}
	if now.IsZero() || command.IssuedAtUnix < claim.IssuedAtUnix || now.Unix() < command.IssuedAtUnix {
		return ApplyResult{}, fmt.Errorf("%w: command timestamp is outside the accepted interval", ErrAuthorization)
	}
	event, err := e.buildEvent("agent_command_applied", command.CommandID, now, claim.ParticipantSlot, command.PayloadType, command.Payload)
	if err != nil {
		return ApplyResult{}, err
	}
	record := commandRecord{
		Command: cloneCommand(command), Fingerprint: fingerprint, Event: cloneEvent(event),
	}
	candidateCommands := cloneCommandRecords(e.commands)
	candidateCommands[command.CommandID] = record
	candidateEvents := append(cloneEvents(e.events), event)
	candidateParticipants := e.participants
	candidateParticipants[int(claim.ParticipantSlot-1)].LastCommandSequence = command.ParticipantSequence
	if err := e.ensureSnapshotCapacity(StatusActive, event.MatchVersion, candidateParticipants, candidateCommands, candidateEvents, nil, nil, completionSnapshotReserveBytes); err != nil {
		return ApplyResult{}, err
	}
	e.events = candidateEvents
	e.version = event.MatchVersion
	participant.LastCommandSequence = command.ParticipantSequence
	e.status = StatusActive
	e.commands[command.CommandID] = record
	return ApplyResult{Event: cloneEvent(event), Status: e.status, Version: e.version}, nil
}

func (e *Engine) Complete(facts contract.TerminalFacts, now time.Time) (contract.MatchCompletedV1, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	factsBytes, err := facts.CanonicalBytes()
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	if e.completion != nil {
		oldFacts, _ := e.terminalFacts.CanonicalBytes()
		if string(oldFacts) != string(factsBytes) {
			return contract.MatchCompletedV1{}, fmt.Errorf("%w: completion facts differ from recorded evidence", ErrConflict)
		}
		return cloneCompletion(*e.completion), nil
	}
	if e.status != StatusActive {
		return contract.MatchCompletedV1{}, fmt.Errorf("%w: a match must be active before authoritative completion", ErrState)
	}
	if now.IsZero() || now.Unix() < 0 {
		return contract.MatchCompletedV1{}, errors.New("completion time is invalid")
	}
	causation := string(contract.NewDigest(append([]byte("authority:complete:"), factsBytes...)))
	terminalEvent, err := e.buildEvent("match_completed", causation, now, 0, "trnm.match.terminal-facts.v1", factsBytes)
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	candidateEvents := make([]contract.MatchEvent, len(e.events)+1)
	copy(candidateEvents, e.events)
	candidateEvents[len(e.events)] = terminalEvent
	eventRoot, err := contract.EventRoot(candidateEvents)
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	archiveHash, err := contract.ArchiveHash(candidateEvents)
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	rosterRoot, err := contract.RosterRoot(e.roster())
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	commitmentID, err := contract.CommitmentID(e.matchID, eventRoot, archiveHash)
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	claim := e.participants[0].Authorization.Claim
	completion, err := contract.SignCompletion(contract.MatchCompletedV1{
		Schema: contract.CompletionSchema, CommitmentID: commitmentID, MatchID: e.matchID,
		ChallengeID: e.challengeID, TerminalFacts: facts, EventCount: uint64(len(candidateEvents)), EventRoot: eventRoot,
		RosterRoot: rosterRoot, RulesetHash: claim.RulesetHash, DatasetHash: claim.DatasetHash,
		ChallengeSnapshotHash: claim.ChallengeSnapshotHash, ArchiveHash: archiveHash,
		CompletedAtUnix: now.Unix(), AuthorityKeyID: e.authorityKeyID,
	}, e.authorityPrivateKey)
	if err != nil {
		return contract.MatchCompletedV1{}, err
	}
	terminalFacts := facts
	if err := e.ensureSnapshotCapacity(StatusCompleted, terminalEvent.MatchVersion, e.participants, e.commands, candidateEvents, &terminalFacts, &completion, 0); err != nil {
		return contract.MatchCompletedV1{}, err
	}
	e.events = candidateEvents
	e.version = terminalEvent.MatchVersion
	e.status = StatusCompleted
	e.terminalFacts = &facts
	e.completion = &completion
	return cloneCompletion(completion), nil
}

func (e *Engine) checkCommandQuota(nextPayloadBytes int) error {
	if len(e.commands) >= MaxCommandsPerMatch {
		return fmt.Errorf("%w: at most %d commands are allowed", ErrCapacity, MaxCommandsPerMatch)
	}
	used := 0
	for _, record := range e.commands {
		if len(record.Command.Payload) > MaxCumulativeCommandPayloadBytes-used {
			return fmt.Errorf("%w: recorded payload accounting is invalid", ErrCapacity)
		}
		used += len(record.Command.Payload)
	}
	if nextPayloadBytes > MaxCumulativeCommandPayloadBytes-used {
		return fmt.Errorf("%w: cumulative command payload exceeds %d bytes", ErrCapacity, MaxCumulativeCommandPayloadBytes)
	}
	return nil
}

func (e *Engine) validateRecordedCommandQuota() error {
	if len(e.commands) > MaxCommandsPerMatch {
		return fmt.Errorf("%w: snapshot exceeds the command count limit", ErrCapacity)
	}
	used := 0
	for _, record := range e.commands {
		if len(record.Command.Payload) > MaxCumulativeCommandPayloadBytes-used {
			return fmt.Errorf("%w: snapshot exceeds the cumulative payload limit", ErrCapacity)
		}
		used += len(record.Command.Payload)
	}
	return nil
}

func (e *Engine) appendEvent(eventType, causationID string, now time.Time, slot uint32, payloadType string, payload []byte) (contract.MatchEvent, error) {
	event, err := e.buildEvent(eventType, causationID, now, slot, payloadType, payload)
	if err != nil {
		return contract.MatchEvent{}, err
	}
	e.events = append(e.events, event)
	e.version = event.MatchVersion
	return event, nil
}

func (e *Engine) buildEvent(eventType, causationID string, now time.Time, slot uint32, payloadType string, payload []byte) (contract.MatchEvent, error) {
	if now.IsZero() || now.Unix() < 0 {
		return contract.MatchEvent{}, errors.New("event time is invalid")
	}
	if len(e.events) > 0 && now.Unix() < e.events[len(e.events)-1].OccurredAtUnix {
		return contract.MatchEvent{}, errors.New("event time cannot move backwards")
	}
	sequence := uint64(len(e.events) + 1)
	eventIDInput := encodeEventIDInput(e.matchID, sequence, causationID)
	event, err := contract.SealEvent(contract.MatchEvent{
		Schema: contract.EventSchema, EventID: string(contract.NewDigest(eventIDInput)), EventType: eventType,
		MatchID: e.matchID, ChallengeID: e.challengeID, Sequence: sequence, CausationID: causationID,
		OccurredAtUnix: now.Unix(), ParticipantSlot: slot, MatchVersion: e.version + 1,
		PayloadType: payloadType, Payload: append([]byte(nil), payload...),
	})
	if err != nil {
		return contract.MatchEvent{}, err
	}
	return event, nil
}

func validateAuthoritySigningConfiguration(keyID string, privateKey ed25519.PrivateKey) (ed25519.PublicKey, error) {
	if err := contract.ValidateKeyID(keyID); err != nil {
		return nil, fmt.Errorf("authority key ID is invalid: %w", err)
	}
	if len(privateKey) != ed25519.PrivateKeySize {
		return nil, errors.New("authority private key has invalid length")
	}
	derived := ed25519.NewKeyFromSeed(privateKey[:ed25519.SeedSize])
	if !bytes.Equal(derived, privateKey) {
		return nil, errors.New("authority private key public suffix does not match its seed")
	}
	return append(ed25519.PublicKey(nil), derived[ed25519.SeedSize:]...), nil
}

func (e *Engine) roster() []contract.RosterEntry {
	roster := make([]contract.RosterEntry, 0, 2)
	for _, participant := range e.participants {
		claim := participant.Authorization.Claim
		roster = append(roster, contract.RosterEntry{
			ParticipantSlot: claim.ParticipantSlot, SubjectUserID: claim.SubjectUserID,
			AgentID: claim.AgentID, AgentDID: claim.AgentDID, AgentKeyID: claim.AgentKeyID,
			AgentKeyHash: contract.NewDigest(claim.AgentPublicKey), Role: claim.Role,
		})
	}
	return roster
}

func (e *Engine) View() View {
	e.mu.Lock()
	defer e.mu.Unlock()
	view := View{MatchID: e.matchID, ChallengeID: e.challengeID, Status: e.status, Version: e.version, EventCount: uint64(len(e.events))}
	for index, participant := range e.participants {
		claim := participant.Authorization.Claim
		view.Participants[index] = ParticipantView{Slot: claim.ParticipantSlot, AuthorizationID: claim.AuthorizationID,
			SubjectUserID: claim.SubjectUserID, AgentID: claim.AgentID, Joined: participant.Joined,
			LastCommandSequence: participant.LastCommandSequence}
	}
	return view
}

func (e *Engine) Events() []contract.MatchEvent {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]contract.MatchEvent, len(e.events))
	for index := range e.events {
		out[index] = cloneEvent(e.events[index])
	}
	return out
}

func (e *Engine) Completion() (*contract.MatchCompletedV1, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.completion == nil {
		return nil, false
	}
	copyCompletion := cloneCompletion(*e.completion)
	return &copyCompletion, true
}

func (e *Engine) AuthorityPublicKey() ed25519.PublicKey {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append(ed25519.PublicKey(nil), e.authorityPublicKey...)
}

func (e *Engine) joinedCount() int {
	count := 0
	for _, participant := range e.participants {
		if participant.Joined {
			count++
		}
	}
	return count
}

func encodeJoinPayload(slot uint32, userID, authorizationID, agentID string) []byte {
	out := append([]byte("trnm_participant_joined_v1\x00"), make([]byte, 4)...)
	binary.BigEndian.PutUint32(out[len(out)-4:], slot)
	for _, value := range []string{userID, authorizationID, agentID} {
		var size [4]byte
		binary.BigEndian.PutUint32(size[:], uint32(len(value)))
		out = append(out, size[:]...)
		out = append(out, value...)
	}
	return out
}

func encodeEventIDInput(matchID string, sequence uint64, causationID string) []byte {
	out := []byte("trnm_match_event_id_v1\x00")
	for _, value := range []string{matchID, causationID} {
		var size [4]byte
		binary.BigEndian.PutUint32(size[:], uint32(len(value)))
		out = append(out, size[:]...)
		out = append(out, value...)
	}
	var raw [8]byte
	binary.BigEndian.PutUint64(raw[:], sequence)
	return append(out, raw[:]...)
}

func cloneAuthorization(in contract.SignedAuthorization) contract.SignedAuthorization {
	in.Claim.AgentPublicKey = append([]byte(nil), in.Claim.AgentPublicKey...)
	in.Signature = append([]byte(nil), in.Signature...)
	return in
}

func cloneCommand(in contract.CommandEnvelope) contract.CommandEnvelope {
	in.Payload = append([]byte(nil), in.Payload...)
	in.Signature = append([]byte(nil), in.Signature...)
	return in
}

func cloneEvent(in contract.MatchEvent) contract.MatchEvent {
	in.Payload = append([]byte(nil), in.Payload...)
	return in
}

func cloneCompletion(in contract.MatchCompletedV1) contract.MatchCompletedV1 {
	in.Signature = append([]byte(nil), in.Signature...)
	return in
}

func cloneEvents(in []contract.MatchEvent) []contract.MatchEvent {
	out := make([]contract.MatchEvent, len(in))
	for index := range in {
		out[index] = cloneEvent(in[index])
	}
	return out
}

func cloneCommandRecords(in map[string]commandRecord) map[string]commandRecord {
	out := make(map[string]commandRecord, len(in)+1)
	for id, record := range in {
		out[id] = record
	}
	return out
}

func cloneKeyMap(in map[string]ed25519.PublicKey) map[string]ed25519.PublicKey {
	out := make(map[string]ed25519.PublicKey, len(in))
	for keyID, publicKey := range in {
		out[keyID] = append(ed25519.PublicKey(nil), publicKey...)
	}
	return out
}

func sortedCommandRecords(commands map[string]commandRecord) []commandRecord {
	ids := make([]string, 0, len(commands))
	for id := range commands {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]commandRecord, 0, len(ids))
	for _, id := range ids {
		out = append(out, commands[id])
	}
	return out
}
