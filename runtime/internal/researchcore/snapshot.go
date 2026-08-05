package researchcore

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
	"sort"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
)

const (
	snapshotSchema          = "trnm.research-session.snapshot.v1"
	maxSnapshotPayloadBytes = 64 * 1024 * 1024
)

var snapshotMagic = [8]byte{'T', 'R', 'N', 'M', 'R', 'S', 'P', '1'}

type snapshotDocument struct {
	Schema             string                               `json:"schema"`
	SessionID          string                               `json:"session_id"`
	TeamID             string                               `json:"team_id"`
	PaperProjectID     string                               `json:"paper_project_id"`
	ChallengeID        string                               `json:"challenge_id"`
	RulesetHash        researchcontract.Digest              `json:"ruleset_hash"`
	ChallengeHash      researchcontract.Digest              `json:"challenge_snapshot_hash"`
	Status             Status                               `json:"status"`
	Version            uint64                               `json:"version"`
	Epochs             []rosterEpoch                        `json:"epochs"`
	Participants       []participantState                   `json:"participants"`
	Actions            []actionRecord                       `json:"actions"`
	Events             []researchcontract.ResearchEvent     `json:"events"`
	Completion         *researchcontract.SessionCompletedV1 `json:"completion,omitempty"`
	AuthorityKeyID     string                               `json:"authority_key_id"`
	AuthorityPublicKey []byte                               `json:"authority_public_key"`
}

func (e *Engine) Snapshot() ([]byte, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	actions := make([]actionRecord, 0, len(e.actions))
	ids := make([]string, 0, len(e.actions))
	for id := range e.actions {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		actions = append(actions, cloneActionRecord(e.actions[id]))
	}
	document := snapshotDocument{
		Schema: snapshotSchema, SessionID: e.sessionID, TeamID: e.teamID,
		PaperProjectID: e.paperProjectID, ChallengeID: e.challengeID,
		RulesetHash: e.rulesetHash, ChallengeHash: e.challengeHash, Status: e.status,
		Version: e.version, Epochs: cloneEpochs(e.epochs), Participants: cloneParticipants(e.participants),
		Actions: actions, Events: cloneEvents(e.events), Completion: cloneCompletionPointer(e.completion),
		AuthorityKeyID: e.authorityKeyID, AuthorityPublicKey: append([]byte(nil), e.authorityPublicKey...),
	}
	payload, err := json.Marshal(document)
	if err != nil {
		return nil, err
	}
	if len(payload) > maxSnapshotPayloadBytes {
		return nil, errors.New("research snapshot exceeds 64 MiB")
	}
	checksum := sha256.Sum256(append([]byte("trnm_research_session_snapshot_checksum_v1\x00"), payload...))
	message, err := snapshotSigningBytes(e.authorityKeyID, payload, checksum)
	if err != nil {
		return nil, err
	}
	signature := ed25519.Sign(e.authorityPrivateKey, message)
	out := append([]byte(nil), snapshotMagic[:]...)
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(payload)))
	out = append(out, size[:]...)
	out = append(out, payload...)
	out = append(out, checksum[:]...)
	out = append(out, signature...)
	return out, nil
}

func Restore(snapshot []byte, options RestoreOptions) (*Engine, error) {
	const trailer = sha256.Size + ed25519.SignatureSize
	if len(snapshot) < len(snapshotMagic)+8+trailer || !bytes.Equal(snapshot[:len(snapshotMagic)], snapshotMagic[:]) {
		return nil, errors.New("research snapshot header is invalid")
	}
	size := binary.BigEndian.Uint64(snapshot[len(snapshotMagic) : len(snapshotMagic)+8])
	if size > maxSnapshotPayloadBytes || size != uint64(len(snapshot)-(len(snapshotMagic)+8+trailer)) {
		return nil, errors.New("research snapshot length is invalid")
	}
	start := len(snapshotMagic) + 8
	end := start + int(size)
	payload := snapshot[start:end]
	checksum := snapshot[end : end+sha256.Size]
	expected := sha256.Sum256(append([]byte("trnm_research_session_snapshot_checksum_v1\x00"), payload...))
	if subtle.ConstantTimeCompare(checksum, expected[:]) != 1 {
		return nil, errors.New("research snapshot checksum failed")
	}
	public, err := validateAuthority(options.AuthorityKeyID, options.AuthorityPrivateKey)
	if err != nil {
		return nil, err
	}
	var checksumArray [sha256.Size]byte
	copy(checksumArray[:], checksum)
	message, err := snapshotSigningBytes(options.AuthorityKeyID, payload, checksumArray)
	if err != nil {
		return nil, err
	}
	if !ed25519.Verify(public, message, snapshot[end+sha256.Size:]) {
		return nil, errors.New("research snapshot signature failed")
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var document snapshotDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("research snapshot JSON: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, errors.New("research snapshot has trailing JSON")
	}
	if document.Schema != snapshotSchema || document.AuthorityKeyID != options.AuthorityKeyID ||
		!bytes.Equal(document.AuthorityPublicKey, public) {
		return nil, errors.New("research snapshot schema or authority differs")
	}
	engine := &Engine{
		sessionID: document.SessionID, teamID: document.TeamID, paperProjectID: document.PaperProjectID,
		challengeID: document.ChallengeID, rulesetHash: document.RulesetHash, challengeHash: document.ChallengeHash,
		status: document.Status, version: document.Version, epochs: cloneEpochs(document.Epochs),
		participants: cloneParticipants(document.Participants), actions: make(map[string]actionRecord),
		events: cloneEvents(document.Events), completion: cloneCompletionPointer(document.Completion),
		trustedIssuerKeys: cloneKeys(options.TrustedIssuerKeys), authorityKeyID: options.AuthorityKeyID,
		authorityPrivateKey: append(ed25519.PrivateKey(nil), options.AuthorityPrivateKey...),
		authorityPublicKey:  append(ed25519.PublicKey(nil), public...),
	}
	for _, record := range document.Actions {
		if _, exists := engine.actions[record.Action.ActionID]; exists {
			return nil, errors.New("snapshot contains duplicate action_id")
		}
		engine.actions[record.Action.ActionID] = cloneActionRecord(record)
	}
	if err := engine.validateRestored(); err != nil {
		return nil, err
	}
	return engine, nil
}

func snapshotSigningBytes(keyID string, payload []byte, checksum [sha256.Size]byte) ([]byte, error) {
	if err := researchcontract.ValidateKeyID(keyID); err != nil {
		return nil, err
	}
	if len(payload) > maxSnapshotPayloadBytes {
		return nil, errors.New("research snapshot payload too large")
	}
	out := append([]byte("trnm_research_session_snapshot_signature_v1\x00"), make([]byte, 4)...)
	binary.BigEndian.PutUint32(out[len(out)-4:], uint32(len(keyID)))
	out = append(out, keyID...)
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(payload)))
	out = append(out, size[:]...)
	out = append(out, payload...)
	out = append(out, checksum[:]...)
	return out, nil
}

func (e *Engine) validateRestored() error {
	if researchcontract.ValidateSessionID(e.sessionID) != nil || e.teamID == "" || e.paperProjectID == "" || e.challengeID == "" ||
		e.version != uint64(len(e.events))+1 || len(e.epochs) == 0 {
		return errors.New("research snapshot identity or version is inconsistent")
	}
	allAuthIDs := make(map[string]struct{})
	for index, epoch := range e.epochs {
		snapshot, err := researchcontract.ValidateAuthorizationSet(epoch.Authorizations, e.trustedIssuerKeys, 0, false)
		if err != nil {
			return fmt.Errorf("research snapshot epoch %d: %w", index, err)
		}
		if epoch.Version != uint64(index+1) || epoch.Version != snapshot.RosterVersion || epoch.Root != snapshot.RosterRoot ||
			snapshot.SessionID != e.sessionID || snapshot.TeamID != e.teamID || snapshot.PaperProjectID != e.paperProjectID ||
			snapshot.ChallengeID != e.challengeID || snapshot.RulesetHash != e.rulesetHash || snapshot.ChallengeHash != e.challengeHash {
			return errors.New("research snapshot roster epoch differs from immutable identity")
		}
		for _, auth := range epoch.Authorizations {
			if _, exists := allAuthIDs[auth.Claim.AuthorizationID]; exists {
				return errors.New("authorization_id is reused across roster epochs")
			}
			allAuthIDs[auth.Claim.AuthorizationID] = struct{}{}
		}
	}
	if len(e.participants) != len(e.currentEpoch().Authorizations) {
		return errors.New("current participant state has wrong size")
	}
	for i := range e.participants {
		if !reflect.DeepEqual(e.participants[i].Authorization, e.currentEpoch().Authorizations[i]) {
			return errors.New("current participant authorization differs from current epoch")
		}
	}
	if len(e.events) > 0 {
		if err := researchcontract.ValidateArchive(e.events); err != nil {
			return fmt.Errorf("research snapshot archive: %w", err)
		}
	}
	if len(e.actions) > MaxDistinctActions || e.totalPayloadBytes() > MaxTotalPayloadBytes {
		return ErrCapacity
	}
	return e.replayAndValidate()
}

func (e *Engine) replayAndValidate() error {
	epochIndex := 0
	replay := freshParticipantStates(e.epochs[0].Authorizations)
	actionsBySequence := make(map[uint64]actionRecord, len(e.actions))
	for id, record := range e.actions {
		if id != record.Action.ActionID {
			return errors.New("action map key differs from action_id")
		}
		fingerprint, err := researchcontract.ActionFingerprint(record.Action)
		if err != nil || fingerprint != record.Fingerprint {
			return errors.New("action fingerprint is invalid")
		}
		if record.Event.Sequence == 0 || record.Event.Sequence > uint64(len(e.events)) ||
			!reflect.DeepEqual(record.Event, e.events[record.Event.Sequence-1]) {
			return errors.New("action record event differs from archive")
		}
		if _, exists := actionsBySequence[record.Event.Sequence]; exists {
			return errors.New("multiple actions map to one event")
		}
		actionsBySequence[record.Event.Sequence] = record
	}
	currentSubstantive := 0
	for index, event := range e.events {
		epoch := e.epochs[epochIndex]
		if event.SessionVersion != uint64(index+2) {
			return errors.New("event session version is inconsistent")
		}
		switch event.EventType {
		case "participant_joined":
			participant, claim, err := replayParticipant(replay, event.ParticipantSlot)
			if err != nil {
				return err
			}
			if event.RosterVersion != epoch.Version || participant.Joined || event.CausationID != claim.AuthorizationID ||
				event.ReferenceHash != epoch.Root || event.ActionType != "server.participant-joined" ||
				event.PayloadType != "trnm.research-session.participant-joined.v1" ||
				event.OccurredAtUnix < claim.IssuedAtUnix || event.OccurredAtUnix >= claim.ExpiresAtUnix {
				return errors.New("join event is inconsistent")
			}
			expected, _ := researchcontract.ParticipantJoinedPayload(epoch.Version, claim.ParticipantSlot, claim.SubjectUserID, claim.AuthorizationID, claim.AgentID)
			if !bytes.Equal(event.Payload, expected) {
				return errors.New("join payload is inconsistent")
			}
			participant.Joined, participant.Connected = true, true
		case "participant_disconnected":
			participant, claim, err := replayParticipant(replay, event.ParticipantSlot)
			if err != nil {
				return err
			}
			if event.RosterVersion != epoch.Version || !participant.Joined || !participant.Connected || event.ReferenceHash != epoch.Root ||
				event.PayloadType != "trnm.research-session.participant-disconnected.v1" {
				return errors.New("disconnect event is inconsistent")
			}
			expected, _ := researchcontract.ParticipantDisconnectedPayload(epoch.Version, claim.ParticipantSlot, claim.SubjectUserID, claim.AuthorizationID)
			if !bytes.Equal(event.Payload, expected) {
				return errors.New("disconnect payload is inconsistent")
			}
			participant.Connected = false
		case "participant_reconnected":
			participant, claim, err := replayParticipant(replay, event.ParticipantSlot)
			if err != nil {
				return err
			}
			if event.RosterVersion != epoch.Version || !participant.Joined || participant.Connected || event.ReferenceHash != epoch.Root ||
				event.PayloadType != "trnm.research-session.participant-reconnected.v1" {
				return errors.New("reconnect event is inconsistent")
			}
			expected, _ := researchcontract.ParticipantReconnectedPayload(epoch.Version, claim.ParticipantSlot, claim.SubjectUserID, claim.AuthorizationID)
			if !bytes.Equal(event.Payload, expected) {
				return errors.New("reconnect payload is inconsistent")
			}
			participant.Connected = true
		case "research_action_applied":
			record, ok := actionsBySequence[event.Sequence]
			if !ok {
				return errors.New("research action event has no action record")
			}
			action := record.Action
			participant, claim, err := replayParticipant(replay, action.ParticipantSlot)
			if err != nil {
				return err
			}
			if action.RosterVersion != epoch.Version || event.RosterVersion != epoch.Version || action.AuthorizationID != claim.AuthorizationID ||
				action.SessionID != e.sessionID || action.TeamID != e.teamID || action.PaperProjectID != e.paperProjectID ||
				action.ChallengeID != e.challengeID || action.AgentKeyID != claim.AgentKeyID ||
				action.ExpectedSessionVersion != event.Sequence || action.ParticipantSequence != participant.LastActionSequence+1 ||
				action.IssuedAtUnix < claim.IssuedAtUnix || action.IssuedAtUnix > event.OccurredAtUnix ||
				!participant.Joined || !participant.Connected || event.CausationID != action.ActionID ||
				event.ActionType != action.ActionType || event.PayloadType != action.PayloadType ||
				!bytes.Equal(event.Payload, action.Payload) || event.ReferenceHash != action.ReferenceHash {
				return errors.New("research action identity, time, or event mapping is inconsistent")
			}
			if expected := payloadTypeForAction[action.ActionType]; expected == "" || expected != action.PayloadType {
				return errors.New("action payload type is not allowed")
			}
			if err := researchcontract.VerifyAction(action, ed25519.PublicKey(claim.AgentPublicKey)); err != nil {
				return err
			}
			if action.ActionType == researchcontract.ActionParticipantReady {
				if participant.Ready || action.ReferenceHash != epoch.Root {
					return errors.New("ready action is inconsistent")
				}
				participant.Ready = true
			} else {
				if !allActive(replay) {
					return errors.New("ordinary action occurred before all-ready")
				}
				if action.ActionType == researchcontract.ActionPaperReleaseAcknowledged {
					participant.AcknowledgementHash = action.ReferenceHash
				} else {
					currentSubstantive++
				}
			}
			participant.LastActionSequence++
		case "roster_replaced":
			if _, mapped := actionsBySequence[event.Sequence]; mapped {
				return errors.New("action maps to roster replacement")
			}
			if epochIndex+1 >= len(e.epochs) {
				return errors.New("roster replacement has no next epoch")
			}
			next := e.epochs[epochIndex+1]
			changed, err := validateEpochReplacement(epoch, next, replay)
			if err != nil {
				return err
			}
			if event.ParticipantSlot != uint32(changed+1) || event.RosterVersion != next.Version || event.ReferenceHash != next.Root ||
				event.ActionType != "server.roster-replaced" || event.PayloadType != "trnm.research-session.roster-replaced.v1" {
				return errors.New("roster replacement event metadata is inconsistent")
			}
			oldClaim := epoch.Authorizations[changed].Claim
			newClaim := next.Authorizations[changed].Claim
			expected, _ := researchcontract.RosterReplacedPayload(epoch.Version, next.Version, uint32(changed+1), epoch.Root, next.Root, oldClaim.AuthorizationID, newClaim.AuthorizationID)
			if !bytes.Equal(event.Payload, expected) || event.CausationID != string(researchcontract.NewDigest(expected)) {
				return errors.New("roster replacement payload is inconsistent")
			}
			epochIndex++
			replay = freshParticipantStates(next.Authorizations)
			currentSubstantive = 0
		case "research_session_completed":
			if e.completion == nil || index != len(e.events)-1 || event.RosterVersion != epoch.Version || !allActive(replay) || currentSubstantive == 0 {
				return errors.New("completion event occurs outside cooperative completion")
			}
			facts := e.completion.TerminalFacts
			terminal, err := facts.CanonicalBytes()
			if err != nil {
				return err
			}
			for _, participant := range replay {
				if participant.AcknowledgementHash != facts.PaperReleaseCandidateHash {
					return errors.New("completion lacks unanimous Agent release acknowledgement")
				}
			}
			if !bytes.Equal(event.Payload, terminal) || event.PayloadType != "trnm.research-session.terminal-facts.v1" ||
				event.ReferenceHash != facts.PaperReleaseCandidateHash || event.ActionType != "server.complete" {
				return errors.New("completion terminal event is inconsistent")
			}
		}
	}
	if epochIndex != len(e.epochs)-1 {
		return errors.New("roster epoch has no replacement event")
	}
	if len(e.participants) != len(replay) {
		return errors.New("replayed participant count differs")
	}
	for i := range replay {
		if !reflect.DeepEqual(replay[i], e.participants[i]) {
			return errors.New("participant state differs from deterministic replay")
		}
	}
	if e.completion != nil {
		if e.status != StatusCompleted {
			return errors.New("completed snapshot has wrong status")
		}
		if err := e.validateCompletion(); err != nil {
			return err
		}
	} else {
		status := deriveReplayStatus(replay, e.currentEpoch().Version)
		if e.status != status {
			return errors.New("snapshot status differs from deterministic replay")
		}
	}
	return nil
}

func (e *Engine) validateCompletion() error {
	completion := *e.completion
	epoch := e.currentEpoch()
	if completion.SessionID != e.sessionID || completion.TeamID != e.teamID || completion.PaperProjectID != e.paperProjectID ||
		completion.ChallengeID != e.challengeID || completion.RosterVersion != epoch.Version || completion.RosterRoot != epoch.Root ||
		completion.EventCount != uint64(len(e.events)) || completion.RulesetHash != e.rulesetHash ||
		completion.ChallengeSnapshotHash != e.challengeHash || completion.AuthorityKeyID != e.authorityKeyID {
		return errors.New("completion immutable identity differs")
	}
	return researchcontract.VerifyCompletionAgainstArchive(completion, e.events, e.authorityPublicKey)
}

func validateEpochReplacement(oldEpoch, next rosterEpoch, states []participantState) (int, error) {
	if next.Version != oldEpoch.Version+1 || len(next.Authorizations) != len(oldEpoch.Authorizations) {
		return -1, errors.New("replacement epoch number or size is invalid")
	}
	oldIDs := make(map[string]struct{})
	for _, auth := range oldEpoch.Authorizations {
		oldIDs[auth.Claim.AuthorizationID] = struct{}{}
	}
	changed := -1
	for i := range next.Authorizations {
		old := oldEpoch.Authorizations[i].Claim
		fresh := next.Authorizations[i].Claim
		if _, reused := oldIDs[fresh.AuthorizationID]; reused {
			return -1, errors.New("replacement reuses authorization_id")
		}
		if fresh.Role != old.Role || fresh.SubjectUserID != old.SubjectUserID || fresh.AgentID != old.AgentID || fresh.AgentDID != old.AgentDID {
			return -1, errors.New("v1 roster epoch rotation changes human, Agent identity, or role")
		}
		keyIDChanged := fresh.AgentKeyID != old.AgentKeyID
		publicKeyChanged := !bytes.Equal(fresh.AgentPublicKey, old.AgentPublicKey)
		if keyIDChanged != publicKeyChanged {
			return -1, errors.New("v1 Agent key rotation changes only one key component")
		}
		if keyIDChanged {
			if changed != -1 {
				return -1, errors.New("v1 roster epoch rotation changes multiple Agent signing keys")
			}
			changed = i
		}
	}
	if changed == -1 || states[changed].Connected {
		return -1, errors.New("key-rotation target was not uniquely changed and disconnected")
	}
	return changed, nil
}

func replayParticipant(states []participantState, slot uint32) (*participantState, researchcontract.AuthorizationClaim, error) {
	if slot < 1 || int(slot) > len(states) {
		return nil, researchcontract.AuthorizationClaim{}, errors.New("event slot outside current roster")
	}
	return &states[slot-1], states[slot-1].Authorization.Claim, nil
}

func allActive(states []participantState) bool {
	for _, state := range states {
		if !state.Joined || !state.Connected || !state.Ready {
			return false
		}
	}
	return true
}

func deriveReplayStatus(states []participantState, version uint64) Status {
	joined, connected, ready := 0, 0, 0
	for _, state := range states {
		if state.Joined {
			joined++
		}
		if state.Connected {
			connected++
		}
		if state.Ready {
			ready++
		}
	}
	if joined > 0 && connected < joined {
		return StatusPaused
	}
	if ready == len(states) && connected == len(states) {
		return StatusActive
	}
	if version > 1 {
		return StatusPaused
	}
	if joined == len(states) {
		return StatusReady
	}
	if joined > 0 {
		return StatusWaiting
	}
	return StatusCreated
}

func freshParticipantStates(auths []researchcontract.SignedAuthorization) []participantState {
	out := make([]participantState, len(auths))
	for i := range auths {
		out[i].Authorization = cloneAuthorization(auths[i])
	}
	return out
}
func cloneActionRecord(in actionRecord) actionRecord {
	in.Action = cloneAction(in.Action)
	in.Event = cloneEvent(in.Event)
	return in
}
func cloneEpochs(in []rosterEpoch) []rosterEpoch {
	out := make([]rosterEpoch, len(in))
	for i := range in {
		out[i] = rosterEpoch{Version: in[i].Version, Root: in[i].Root, Authorizations: cloneAuthorizations(in[i].Authorizations)}
	}
	return out
}
func cloneParticipants(in []participantState) []participantState {
	out := make([]participantState, len(in))
	for i := range in {
		out[i] = in[i]
		out[i].Authorization = cloneAuthorization(in[i].Authorization)
	}
	return out
}
func cloneCompletionPointer(in *researchcontract.SessionCompletedV1) *researchcontract.SessionCompletedV1 {
	if in == nil {
		return nil
	}
	value := cloneCompletion(*in)
	return &value
}
