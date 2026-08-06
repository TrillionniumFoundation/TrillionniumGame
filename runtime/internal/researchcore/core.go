package researchcore

import (
	"bytes"
	"crypto/ed25519"
	"errors"
	"fmt"
	"reflect"
	"sync"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
)

const (
	MaxDistinctActions   = 2048
	MaxTotalPayloadBytes = 16 * 1024 * 1024
)

type Status string

const (
	StatusCreated   Status = "created"
	StatusWaiting   Status = "waiting"
	StatusReady     Status = "ready"
	StatusActive    Status = "active"
	StatusPaused    Status = "paused"
	StatusCompleted Status = "completed"
)

var (
	ErrConflict     = errors.New("research session version or idempotency conflict")
	ErrUnauthorized = errors.New("research session authorization rejected")
	ErrInvalidState = errors.New("research session transition is invalid")
	ErrCapacity     = errors.New("research session capacity exceeded")
)

type participantState struct {
	Authorization       researchcontract.SignedAuthorization `json:"authorization"`
	Joined              bool                                 `json:"joined"`
	Connected           bool                                 `json:"connected"`
	Ready               bool                                 `json:"ready"`
	LastActionSequence  uint64                               `json:"last_action_sequence"`
	AcknowledgementHash researchcontract.Digest              `json:"acknowledgement_hash,omitempty"`
}

type rosterEpoch struct {
	Version        uint64                                 `json:"version"`
	Root           researchcontract.Digest                `json:"root"`
	Authorizations []researchcontract.SignedAuthorization `json:"authorizations"`
}

type actionRecord struct {
	Action      researchcontract.ActionEnvelope `json:"action"`
	Fingerprint researchcontract.Digest         `json:"fingerprint"`
	Event       researchcontract.ResearchEvent  `json:"event"`
}

type NewSessionOptions struct {
	Authorizations      []researchcontract.SignedAuthorization
	TrustedIssuerKeys   map[string]ed25519.PublicKey
	AuthorityKeyID      string
	AuthorityPrivateKey ed25519.PrivateKey
	Now                 time.Time
}

type RestoreOptions struct {
	TrustedIssuerKeys    map[string]ed25519.PublicKey
	AuthorityKeyID       string
	AuthorityPrivateKey  ed25519.PrivateKey
	AuthorityPrivateKeys map[string]ed25519.PrivateKey
}

type Engine struct {
	mu sync.Mutex

	sessionID      string
	teamID         string
	paperProjectID string
	challengeID    string
	rulesetHash    researchcontract.Digest
	challengeHash  researchcontract.Digest
	status         Status
	version        uint64
	epochs         []rosterEpoch
	participants   []participantState
	actions        map[string]actionRecord
	events         []researchcontract.ResearchEvent
	completion     *researchcontract.SessionCompletedV1

	trustedIssuerKeys   map[string]ed25519.PublicKey
	authorityKeyID      string
	authorityPrivateKey ed25519.PrivateKey
	authorityPublicKey  ed25519.PublicKey
}

type ParticipantView struct {
	ParticipantSlot              uint32                  `json:"participant_slot"`
	AuthorizationID              string                  `json:"authorization_id"`
	SubjectUserID                string                  `json:"subject_user_id"`
	AgentID                      string                  `json:"agent_id"`
	Role                         string                  `json:"role"`
	Joined                       bool                    `json:"joined"`
	Connected                    bool                    `json:"connected"`
	Ready                        bool                    `json:"ready"`
	LastActionSequence           uint64                  `json:"last_action_sequence"`
	AcknowledgementReferenceHash researchcontract.Digest `json:"acknowledgement_reference_hash,omitempty"`
}

type View struct {
	SessionID      string                  `json:"session_id"`
	TeamID         string                  `json:"team_id"`
	PaperProjectID string                  `json:"paper_project_id"`
	ChallengeID    string                  `json:"challenge_id"`
	RosterVersion  uint64                  `json:"roster_version"`
	RosterRoot     researchcontract.Digest `json:"roster_root"`
	Status         Status                  `json:"status"`
	Version        uint64                  `json:"version"`
	EventCount     uint64                  `json:"event_count"`
	Participants   []ParticipantView       `json:"participants"`
}

type MutationResult struct {
	Event  *researchcontract.ResearchEvent
	Replay bool
}

func NewSession(options NewSessionOptions) (*Engine, error) {
	if options.Now.IsZero() {
		return nil, errors.New("session creation time is required")
	}
	public, err := validateAuthority(options.AuthorityKeyID, options.AuthorityPrivateKey)
	if err != nil {
		return nil, err
	}
	snapshot, err := researchcontract.ValidateAuthorizationSet(
		options.Authorizations, options.TrustedIssuerKeys, options.Now.UTC().Unix(), true,
	)
	if err != nil {
		return nil, err
	}
	if snapshot.RosterVersion != 1 {
		return nil, errors.New("initial roster_version must be 1")
	}
	auths := cloneAuthorizations(options.Authorizations)
	participants := make([]participantState, len(auths))
	for i := range auths {
		participants[i].Authorization = cloneAuthorization(auths[i])
	}
	return &Engine{
		sessionID: snapshot.SessionID, teamID: snapshot.TeamID, paperProjectID: snapshot.PaperProjectID,
		challengeID: snapshot.ChallengeID, rulesetHash: snapshot.RulesetHash,
		challengeHash: snapshot.ChallengeHash, status: StatusCreated, version: 1,
		epochs:       []rosterEpoch{{Version: 1, Root: snapshot.RosterRoot, Authorizations: auths}},
		participants: participants, actions: make(map[string]actionRecord),
		trustedIssuerKeys: cloneKeys(options.TrustedIssuerKeys), authorityKeyID: options.AuthorityKeyID,
		authorityPrivateKey: append(ed25519.PrivateKey(nil), options.AuthorityPrivateKey...),
		authorityPublicKey:  append(ed25519.PublicKey(nil), public...),
	}, nil
}

func validateAuthority(keyID string, private ed25519.PrivateKey) (ed25519.PublicKey, error) {
	if err := researchcontract.ValidateKeyID(keyID); err != nil {
		return nil, err
	}
	if len(private) != ed25519.PrivateKeySize {
		return nil, errors.New("authority private key has invalid length")
	}
	derived := ed25519.NewKeyFromSeed(private.Seed())
	if !bytes.Equal(private, derived) {
		return nil, errors.New("authority private key has inconsistent public suffix")
	}
	return append(ed25519.PublicKey(nil), private[32:]...), nil
}

func (e *Engine) View() View {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.viewLocked()
}

func (e *Engine) viewLocked() View {
	participants := make([]ParticipantView, len(e.participants))
	for i, state := range e.participants {
		claim := state.Authorization.Claim
		participants[i] = ParticipantView{
			ParticipantSlot: claim.ParticipantSlot, AuthorizationID: claim.AuthorizationID,
			SubjectUserID: claim.SubjectUserID, AgentID: claim.AgentID, Role: claim.Role,
			Joined: state.Joined, Connected: state.Connected, Ready: state.Ready,
			LastActionSequence: state.LastActionSequence, AcknowledgementReferenceHash: state.AcknowledgementHash,
		}
	}
	epoch := e.currentEpoch()
	return View{SessionID: e.sessionID, TeamID: e.teamID, PaperProjectID: e.paperProjectID,
		ChallengeID: e.challengeID, RosterVersion: epoch.Version, RosterRoot: epoch.Root,
		Status: e.status, Version: e.version, EventCount: uint64(len(e.events)), Participants: participants}
}

func (e *Engine) Events() []researchcontract.ResearchEvent {
	e.mu.Lock()
	defer e.mu.Unlock()
	return cloneEvents(e.events)
}

func (e *Engine) Roster() []researchcontract.RosterEntry {
	e.mu.Lock()
	defer e.mu.Unlock()
	return researchcontract.RosterEntries(e.currentEpoch().Authorizations)
}

// RosterRootForVersion returns the root of an authenticated historical roster
// epoch. Signed-control responses are replayed long after a later replacement
// may have advanced the current epoch, so response validation cannot compare
// only with View().RosterRoot.
func (e *Engine) RosterRootForVersion(version uint64) (researchcontract.Digest, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	for _, epoch := range e.epochs {
		if epoch.Version == version {
			return epoch.Root, true
		}
	}
	return "", false
}

func (e *Engine) AuthorityPublicKey() ed25519.PublicKey {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append(ed25519.PublicKey(nil), e.authorityPublicKey...)
}

func (e *Engine) AuthorityKeyID() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.authorityKeyID
}

func (e *Engine) Completion() (*researchcontract.SessionCompletedV1, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.completion == nil {
		return nil, false
	}
	copy := cloneCompletion(*e.completion)
	return &copy, true
}

// ValidateJoin performs the admission checks used by MatchJoinAttempt without
// consuming the authorization or mutating durable presence state. Admission is
// consumed only from MatchJoin, after Nakama has actually attached a presence.
func (e *Engine) ValidateJoin(userID, authorizationID string, at time.Time) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return ErrInvalidState
	}
	index, err := e.participantIndex(userID, authorizationID)
	if err != nil {
		return err
	}
	state := e.participants[index]
	claim := state.Authorization.Claim
	now := at.UTC().Unix()
	if !state.Joined && (now < claim.IssuedAtUnix || now >= claim.ExpiresAtUnix) {
		return fmt.Errorf("%w: authorization is outside its first-use interval", ErrUnauthorized)
	}
	return nil
}

func (e *Engine) Join(userID, authorizationID string, at time.Time) (MutationResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return MutationResult{}, ErrInvalidState
	}
	index, err := e.participantIndex(userID, authorizationID)
	if err != nil {
		return MutationResult{}, err
	}
	state := &e.participants[index]
	claim := state.Authorization.Claim
	now := at.UTC().Unix()
	if !state.Joined && (now < claim.IssuedAtUnix || now >= claim.ExpiresAtUnix) {
		return MutationResult{}, fmt.Errorf("%w: authorization is outside its first-use interval", ErrUnauthorized)
	}
	if state.Joined && state.Connected {
		return MutationResult{Replay: true}, nil
	}
	var eventType, actionType, payloadType, causation string
	var payload []byte
	if !state.Joined {
		payload, err = researchcontract.ParticipantJoinedPayload(claim.RosterVersion, claim.ParticipantSlot, userID, authorizationID, claim.AgentID)
		eventType, actionType, payloadType, causation = "participant_joined", "server.participant-joined", "trnm.research-session.participant-joined.v1", authorizationID
	} else {
		payload, err = researchcontract.ParticipantReconnectedPayload(claim.RosterVersion, claim.ParticipantSlot, userID, authorizationID)
		eventType, actionType, payloadType = "participant_reconnected", "server.participant-reconnected", "trnm.research-session.participant-reconnected.v1"
		causation = fmt.Sprintf("reconnect:%s:%d", authorizationID, e.version)
	}
	if err != nil {
		return MutationResult{}, err
	}
	event, err := e.appendEvent(claim.RosterVersion, claim.ParticipantSlot, eventType, causation, actionType, payloadType, payload, e.currentEpoch().Root, at)
	if err != nil {
		return MutationResult{}, err
	}
	state.Joined, state.Connected = true, true
	e.rederiveStatus()
	return MutationResult{Event: &event}, nil
}

func (e *Engine) Disconnect(userID, authorizationID string, at time.Time) (MutationResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return MutationResult{}, ErrInvalidState
	}
	index, err := e.participantIndex(userID, authorizationID)
	if err != nil {
		return MutationResult{}, err
	}
	state := &e.participants[index]
	if !state.Joined {
		return MutationResult{}, fmt.Errorf("%w: participant has not joined", ErrInvalidState)
	}
	if !state.Connected {
		return MutationResult{Replay: true}, nil
	}
	claim := state.Authorization.Claim
	payload, err := researchcontract.ParticipantDisconnectedPayload(claim.RosterVersion, claim.ParticipantSlot, userID, authorizationID)
	if err != nil {
		return MutationResult{}, err
	}
	event, err := e.appendEvent(claim.RosterVersion, claim.ParticipantSlot, "participant_disconnected",
		fmt.Sprintf("disconnect:%s:%d", authorizationID, e.version), "server.participant-disconnected",
		"trnm.research-session.participant-disconnected.v1", payload, e.currentEpoch().Root, at)
	if err != nil {
		return MutationResult{}, err
	}
	state.Connected = false
	e.rederiveStatus()
	return MutationResult{Event: &event}, nil
}

func (e *Engine) FenceAllConnections(at time.Time) ([]researchcontract.ResearchEvent, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return nil, nil
	}
	var added []researchcontract.ResearchEvent
	for index := range e.participants {
		state := &e.participants[index]
		if !state.Joined || !state.Connected {
			continue
		}
		claim := state.Authorization.Claim
		payload, err := researchcontract.ParticipantDisconnectedPayload(claim.RosterVersion, claim.ParticipantSlot, claim.SubjectUserID, claim.AuthorizationID)
		if err != nil {
			return nil, err
		}
		event, err := e.appendEvent(claim.RosterVersion, claim.ParticipantSlot, "participant_disconnected",
			fmt.Sprintf("runtime-fence:%s:%d", claim.AuthorizationID, e.version), "server.runtime-fenced",
			"trnm.research-session.participant-disconnected.v1", payload, e.currentEpoch().Root, at)
		if err != nil {
			return nil, err
		}
		state.Connected = false
		added = append(added, event)
	}
	e.rederiveStatus()
	return added, nil
}

var payloadTypeForAction = map[string]string{
	researchcontract.ActionParticipantReady:         "trnm.research-session.ready.v1",
	researchcontract.ActionTaskClaimed:              "trnm.paper-raid.task-claim.v1",
	researchcontract.ActionProposalSubmitted:        "trnm.paper-raid.agent-proposal.v1",
	researchcontract.ActionArtifactPublished:        "trnm.paper-raid.artifact-manifest.v1",
	researchcontract.ActionReviewSubmitted:          "trnm.paper-raid.review.v1",
	researchcontract.ActionCheckpointRecorded:       "trnm.paper-raid.checkpoint.v1",
	researchcontract.ActionPaperReleaseAcknowledged: researchcontract.PayloadPaperReleaseAcknowledged,
}

func (e *Engine) ApplyAction(userID string, action researchcontract.ActionEnvelope, at time.Time) (MutationResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return MutationResult{}, ErrInvalidState
	}
	if action.ParticipantSlot < 1 || int(action.ParticipantSlot) > len(e.participants) {
		return MutationResult{}, fmt.Errorf("%w: participant_slot is outside current roster", ErrUnauthorized)
	}
	state := &e.participants[action.ParticipantSlot-1]
	claim := state.Authorization.Claim
	if userID != claim.SubjectUserID || action.AuthorizationID != claim.AuthorizationID ||
		action.SessionID != e.sessionID || action.TeamID != e.teamID || action.PaperProjectID != e.paperProjectID ||
		action.ChallengeID != e.challengeID || action.RosterVersion != e.currentEpoch().Version ||
		action.ParticipantSlot != claim.ParticipantSlot || action.AgentKeyID != claim.AgentKeyID {
		return MutationResult{}, fmt.Errorf("%w: action identity differs from current authorization", ErrUnauthorized)
	}
	if err := researchcontract.VerifyAction(action, ed25519.PublicKey(claim.AgentPublicKey)); err != nil {
		return MutationResult{}, fmt.Errorf("%w: %v", ErrUnauthorized, err)
	}
	fingerprint, err := researchcontract.ActionFingerprint(action)
	if err != nil {
		return MutationResult{}, err
	}
	if previous, exists := e.actions[action.ActionID]; exists {
		if previous.Fingerprint != fingerprint {
			return MutationResult{}, fmt.Errorf("%w: action_id fingerprint differs", ErrConflict)
		}
		event := previous.Event
		return MutationResult{Event: &event, Replay: true}, nil
	}
	now := at.UTC().Unix()
	// expires_at is a first-admission/epoch-consumption deadline, not a
	// multi-day Paper Raid work deadline. Once this durable participant has
	// joined the epoch, its signed actions may continue after admission expiry.
	if now < claim.IssuedAtUnix || action.IssuedAtUnix < claim.IssuedAtUnix || action.IssuedAtUnix > now {
		return MutationResult{}, fmt.Errorf("%w: action predates authorization or is future-issued", ErrUnauthorized)
	}
	if !state.Joined || !state.Connected {
		return MutationResult{}, fmt.Errorf("%w: participant is not connected", ErrInvalidState)
	}
	if action.ParticipantSequence != state.LastActionSequence+1 || action.ExpectedSessionVersion != e.version {
		return MutationResult{}, fmt.Errorf("%w: action sequence or expected version differs", ErrConflict)
	}
	expectedPayloadType, ok := payloadTypeForAction[action.ActionType]
	if !ok || action.PayloadType != expectedPayloadType {
		return MutationResult{}, fmt.Errorf("%w: action_type/payload_type pair is not allowed", ErrInvalidState)
	}
	if action.ActionType == researchcontract.ActionParticipantReady {
		if action.ReferenceHash != e.currentEpoch().Root {
			return MutationResult{}, fmt.Errorf("%w: ready does not bind current roster_root", ErrInvalidState)
		}
		if state.Ready {
			return MutationResult{}, fmt.Errorf("%w: participant is already ready", ErrConflict)
		}
	} else if e.status != StatusActive {
		return MutationResult{}, fmt.Errorf("%w: ordinary actions require all-ready active state", ErrInvalidState)
	}
	if len(e.actions) >= MaxDistinctActions {
		return MutationResult{}, ErrCapacity
	}
	if e.totalPayloadBytes()+len(action.Payload) > MaxTotalPayloadBytes {
		return MutationResult{}, ErrCapacity
	}
	event, err := e.appendEvent(action.RosterVersion, action.ParticipantSlot, "research_action_applied",
		action.ActionID, action.ActionType, action.PayloadType, action.Payload, action.ReferenceHash, at)
	if err != nil {
		return MutationResult{}, err
	}
	e.actions[action.ActionID] = actionRecord{Action: cloneAction(action), Fingerprint: fingerprint, Event: event}
	state.LastActionSequence++
	if action.ActionType == researchcontract.ActionParticipantReady {
		state.Ready = true
	}
	if action.ActionType == researchcontract.ActionPaperReleaseAcknowledged {
		state.AcknowledgementHash = action.ReferenceHash
	}
	e.rederiveStatus()
	return MutationResult{Event: &event}, nil
}

func (e *Engine) ReplaceRoster(authorizations []researchcontract.SignedAuthorization, at time.Time) (researchcontract.ResearchEvent, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return researchcontract.ResearchEvent{}, ErrInvalidState
	}
	snapshot, err := researchcontract.ValidateAuthorizationSet(authorizations, e.trustedIssuerKeys, at.UTC().Unix(), true)
	if err != nil {
		return researchcontract.ResearchEvent{}, err
	}
	oldEpoch := e.currentEpoch()
	if snapshot.SessionID != e.sessionID || snapshot.TeamID != e.teamID || snapshot.PaperProjectID != e.paperProjectID ||
		snapshot.ChallengeID != e.challengeID || snapshot.RulesetHash != e.rulesetHash ||
		snapshot.ChallengeHash != e.challengeHash || snapshot.RosterVersion != oldEpoch.Version+1 ||
		len(authorizations) != len(e.participants) {
		return researchcontract.ResearchEvent{}, errors.New("replacement roster changes immutable identity, size, or epoch")
	}
	allPriorAuthorizationIDs := make(map[string]struct{})
	for _, epoch := range e.epochs {
		for _, auth := range epoch.Authorizations {
			allPriorAuthorizationIDs[auth.Claim.AuthorizationID] = struct{}{}
		}
	}
	changedSlot := -1
	for index, fresh := range authorizations {
		old := e.participants[index].Authorization.Claim
		claim := fresh.Claim
		if _, exists := allPriorAuthorizationIDs[claim.AuthorizationID]; exists {
			return researchcontract.ResearchEvent{}, errors.New("replacement requires fresh authorization_id for every slot")
		}
		if claim.Role != old.Role || claim.SubjectUserID != old.SubjectUserID || claim.AgentID != old.AgentID || claim.AgentDID != old.AgentDID {
			return researchcontract.ResearchEvent{}, errors.New("v1 roster epoch rotation cannot change human, Agent identity, or role")
		}
		keyIDChanged := claim.AgentKeyID != old.AgentKeyID
		publicKeyChanged := !bytes.Equal(claim.AgentPublicKey, old.AgentPublicKey)
		if keyIDChanged != publicKeyChanged {
			return researchcontract.ResearchEvent{}, errors.New("v1 Agent key rotation must change both key id and public key")
		}
		if keyIDChanged {
			if changedSlot != -1 {
				return researchcontract.ResearchEvent{}, errors.New("v1 roster epoch rotation must change exactly one Agent signing key")
			}
			changedSlot = index
		}
	}
	if changedSlot == -1 {
		return researchcontract.ResearchEvent{}, errors.New("v1 roster epoch rotation must change one Agent signing key")
	}
	if e.participants[changedSlot].Connected {
		return researchcontract.ResearchEvent{}, errors.New("replaced participant must be durably disconnected")
	}
	oldClaim := e.participants[changedSlot].Authorization.Claim
	newClaim := authorizations[changedSlot].Claim
	payload, err := researchcontract.RosterReplacedPayload(oldEpoch.Version, snapshot.RosterVersion,
		uint32(changedSlot+1), oldEpoch.Root, snapshot.RosterRoot, oldClaim.AuthorizationID, newClaim.AuthorizationID)
	if err != nil {
		return researchcontract.ResearchEvent{}, err
	}
	event, err := e.appendEvent(snapshot.RosterVersion, uint32(changedSlot+1), "roster_replaced",
		string(researchcontract.NewDigest(payload)), "server.roster-replaced", "trnm.research-session.roster-replaced.v1",
		payload, snapshot.RosterRoot, at)
	if err != nil {
		return researchcontract.ResearchEvent{}, err
	}
	auths := cloneAuthorizations(authorizations)
	e.epochs = append(e.epochs, rosterEpoch{Version: snapshot.RosterVersion, Root: snapshot.RosterRoot, Authorizations: auths})
	e.participants = make([]participantState, len(auths))
	for i := range auths {
		e.participants[i].Authorization = cloneAuthorization(auths[i])
	}
	e.status = StatusPaused
	return event, nil
}

func (e *Engine) Complete(facts researchcontract.TerminalFacts, at time.Time) (researchcontract.SessionCompletedV1, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.completion != nil {
		if reflect.DeepEqual(e.completion.TerminalFacts, facts) {
			return cloneCompletion(*e.completion), nil
		}
		return researchcontract.SessionCompletedV1{}, fmt.Errorf("%w: completion facts differ", ErrConflict)
	}
	if e.status != StatusActive {
		return researchcontract.SessionCompletedV1{}, fmt.Errorf("%w: session is not active", ErrInvalidState)
	}
	if _, err := facts.CanonicalBytes(); err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	if e.currentSubstantiveActions() == 0 {
		return researchcontract.SessionCompletedV1{}, errors.New("completion requires a substantive research action in current roster epoch")
	}
	for _, participant := range e.participants {
		if !participant.Joined || !participant.Connected || !participant.Ready ||
			participant.AcknowledgementHash != facts.PaperReleaseCandidateHash {
			return researchcontract.SessionCompletedV1{}, errors.New("completion requires every current Agent to acknowledge the release candidate; human authorship consent is a Hepta fact")
		}
	}
	terminal, err := facts.CanonicalBytes()
	if err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	epoch := e.currentEpoch()
	_, err = e.appendEvent(epoch.Version, 0, "research_session_completed", string(researchcontract.NewDigest(terminal)),
		"server.complete", "trnm.research-session.terminal-facts.v1", terminal, facts.PaperReleaseCandidateHash, at)
	if err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	eventRoot, err := researchcontract.EventRoot(e.events)
	if err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	archiveHash, err := researchcontract.ArchiveHash(e.events)
	if err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	commitmentID, err := researchcontract.CommitmentID(e.sessionID, eventRoot, archiveHash)
	if err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	completion, err := researchcontract.SignCompletion(researchcontract.SessionCompletedV1{
		Schema: researchcontract.CompletionSchema, CommitmentID: commitmentID, SessionID: e.sessionID,
		TeamID: e.teamID, PaperProjectID: e.paperProjectID, ChallengeID: e.challengeID,
		RosterVersion: epoch.Version, RosterRoot: epoch.Root, TerminalFacts: facts,
		EventCount: uint64(len(e.events)), EventRoot: eventRoot, ArchiveHash: archiveHash,
		RulesetHash: e.rulesetHash, ChallengeSnapshotHash: e.challengeHash,
		CompletedAtUnix: at.UTC().Unix(), AuthorityKeyID: e.authorityKeyID,
	}, e.authorityPrivateKey)
	if err != nil {
		return researchcontract.SessionCompletedV1{}, err
	}
	e.completion = &completion
	e.status = StatusCompleted
	return cloneCompletion(completion), nil
}

func (e *Engine) appendEvent(rosterVersion uint64, slot uint32, eventType, causationID, actionType,
	payloadType string, payload []byte, referenceHash researchcontract.Digest, at time.Time) (researchcontract.ResearchEvent, error) {
	if at.IsZero() {
		return researchcontract.ResearchEvent{}, errors.New("event time is required")
	}
	when := at.UTC().Unix()
	if len(e.events) > 0 && when < e.events[len(e.events)-1].OccurredAtUnix {
		return researchcontract.ResearchEvent{}, errors.New("event time cannot move backwards")
	}
	sequence := uint64(len(e.events) + 1)
	eventID, err := researchcontract.CanonicalEventID(e.sessionID, sequence, causationID)
	if err != nil {
		return researchcontract.ResearchEvent{}, err
	}
	event, err := researchcontract.SealEvent(researchcontract.ResearchEvent{
		Schema: researchcontract.EventSchema, EventID: eventID, EventType: eventType,
		SessionID: e.sessionID, TeamID: e.teamID, PaperProjectID: e.paperProjectID,
		ChallengeID: e.challengeID, RosterVersion: rosterVersion, Sequence: sequence,
		CausationID: causationID, OccurredAtUnix: when, ParticipantSlot: slot,
		SessionVersion: e.version + 1, ActionType: actionType, PayloadType: payloadType,
		Payload: append([]byte(nil), payload...), ReferenceHash: referenceHash,
	})
	if err != nil {
		return researchcontract.ResearchEvent{}, err
	}
	e.events = append(e.events, event)
	e.version++
	return event, nil
}

func (e *Engine) participantIndex(userID, authorizationID string) (int, error) {
	for i, participant := range e.participants {
		claim := participant.Authorization.Claim
		if claim.SubjectUserID == userID && claim.AuthorizationID == authorizationID {
			return i, nil
		}
	}
	return -1, fmt.Errorf("%w: user and authorization do not identify a current roster member", ErrUnauthorized)
}

func (e *Engine) rederiveStatus() {
	if e.completion != nil {
		e.status = StatusCompleted
		return
	}
	joined, connected, ready := 0, 0, 0
	for _, participant := range e.participants {
		if participant.Joined {
			joined++
		}
		if participant.Connected {
			connected++
		}
		if participant.Ready {
			ready++
		}
	}
	if joined > 0 && connected < joined {
		e.status = StatusPaused
		return
	}
	if ready == len(e.participants) && connected == len(e.participants) {
		e.status = StatusActive
		return
	}
	if e.currentEpoch().Version > 1 {
		e.status = StatusPaused
		return
	}
	if joined == len(e.participants) {
		e.status = StatusReady
		return
	}
	if joined > 0 {
		e.status = StatusWaiting
		return
	}
	e.status = StatusCreated
}

func (e *Engine) currentEpoch() rosterEpoch { return e.epochs[len(e.epochs)-1] }

func (e *Engine) totalPayloadBytes() int {
	total := 0
	for _, record := range e.actions {
		total += len(record.Action.Payload)
	}
	return total
}

func (e *Engine) currentSubstantiveActions() int {
	version := e.currentEpoch().Version
	count := 0
	for _, record := range e.actions {
		if record.Action.RosterVersion == version && record.Action.ActionType != researchcontract.ActionParticipantReady &&
			record.Action.ActionType != researchcontract.ActionPaperReleaseAcknowledged {
			count++
		}
	}
	return count
}

func cloneAuthorization(in researchcontract.SignedAuthorization) researchcontract.SignedAuthorization {
	in.Claim.AgentPublicKey = append([]byte(nil), in.Claim.AgentPublicKey...)
	in.Signature = append([]byte(nil), in.Signature...)
	return in
}
func cloneAuthorizations(in []researchcontract.SignedAuthorization) []researchcontract.SignedAuthorization {
	out := make([]researchcontract.SignedAuthorization, len(in))
	for i := range in {
		out[i] = cloneAuthorization(in[i])
	}
	return out
}
func cloneAction(in researchcontract.ActionEnvelope) researchcontract.ActionEnvelope {
	in.Payload = append([]byte(nil), in.Payload...)
	in.Signature = append([]byte(nil), in.Signature...)
	return in
}
func cloneEvent(in researchcontract.ResearchEvent) researchcontract.ResearchEvent {
	in.Payload = append([]byte(nil), in.Payload...)
	return in
}
func cloneEvents(in []researchcontract.ResearchEvent) []researchcontract.ResearchEvent {
	out := make([]researchcontract.ResearchEvent, len(in))
	for i := range in {
		out[i] = cloneEvent(in[i])
	}
	return out
}
func cloneCompletion(in researchcontract.SessionCompletedV1) researchcontract.SessionCompletedV1 {
	in.Signature = append([]byte(nil), in.Signature...)
	return in
}
func cloneKeys(in map[string]ed25519.PublicKey) map[string]ed25519.PublicKey {
	out := make(map[string]ed25519.PublicKey, len(in))
	for id, key := range in {
		out[id] = append(ed25519.PublicKey(nil), key...)
	}
	return out
}
