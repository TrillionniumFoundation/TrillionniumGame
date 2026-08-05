package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	registeredResearchMatchName       = "trnm_research_session_v1"
	opCodeResearchAction        int64 = 11
	opCodeResearchEvent         int64 = 12
	opCodeResearchError         int64 = 13
	opCodeResearchCompletion    int64 = 14
	maximumResearchActionBytes        = 128 * 1024
	researchAdmissionTTL              = 30 * time.Second
)

type researchMatch struct{ module *moduleRuntime }
type researchMatchState struct {
	engine                *researchcore.Engine
	record                storedResearchSession
	storageVersion        string
	instanceSessionID     string
	instanceGeneration    uint64
	pendingAuthorization  map[string]pendingResearchAdmission
	sessionAuthorization  map[string]string
	authorizationSessions map[string]map[string]struct{}
	sessionPresences      map[string]runtime.Presence
	nextDeliveryAttempt   time.Time
	deliveryAttempts      uint32
}

type pendingResearchAdmission struct {
	authorizationID string
	expiresAt       time.Time
}

var _ runtime.Match = (*researchMatch)(nil)

func (m *researchMatch) MatchInit(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, params map[string]interface{}) (interface{}, int, string) {
	if err := m.module.config.ready(); err != nil {
		logger.Error("research match init unready: %s", err.Error())
		return nil, 0, ""
	}
	sessionID, ok := params["logical_session_id"].(string)
	if !ok || researchcontract.ValidateSessionID(sessionID) != nil {
		logger.Error("research match init invalid session id")
		return nil, 0, ""
	}
	generation, err := generationParameter(params["runtime_generation"])
	if err != nil {
		logger.Error("research match init invalid generation")
		return nil, 0, ""
	}
	stored, err := loadStoredResearch(ctx, nk, sessionID)
	if err != nil || stored.record.RuntimeGeneration != generation {
		logger.Error("research match init load/fence failed")
		return nil, 0, ""
	}
	engine, err := m.module.restoreStoredResearch(stored.record)
	if err != nil {
		logger.Error("research match init restore failed: %s", err.Error())
		return nil, 0, ""
	}
	_, completed := engine.Completion()
	if completed && !hasPendingResearchDeliveries(stored.record) {
		return nil, 0, ""
	}
	if !completed {
		// A resumed runtime owns no old websocket presence. Convert any signed
		// connected state into durable disconnect events before accepting traffic.
		if _, err := engine.FenceAllConnections(time.Now().UTC()); err != nil {
			logger.Error("research match init connection fence failed: %s", err.Error())
			return nil, 0, ""
		}
	}
	external, _ := ctx.Value(runtime.RUNTIME_CTX_MATCH_ID).(string)
	if external == "" {
		return nil, 0, ""
	}
	snapshot, err := engine.Snapshot()
	if err != nil {
		return nil, 0, ""
	}
	stored.record.setSnapshot(snapshot)
	stored.record.ExternalMatchID = external
	newVersion, err := updateStoredResearch(ctx, nk, stored.record, stored.version)
	if err != nil {
		logger.Error("research match init storage fence failed: %s", err.Error())
		return nil, 0, ""
	}
	state := &researchMatchState{engine: engine, record: stored.record, storageVersion: newVersion, instanceSessionID: sessionID, instanceGeneration: generation, pendingAuthorization: map[string]pendingResearchAdmission{}, sessionAuthorization: map[string]string{}, authorizationSessions: map[string]map[string]struct{}{}, sessionPresences: map[string]runtime.Presence{}}
	if err := m.deliverPendingResearch(ctx, logger, nk, state); err != nil {
		logger.Warn("research Hepta delivery pending after init: %s", err.Error())
	}
	return state, m.module.config.matchTickRate, state.label()
}

func (m *researchMatch) MatchJoinAttempt(_ context.Context, _ runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, _ runtime.MatchDispatcher, _ int64, raw interface{}, presence runtime.Presence, metadata map[string]string) (interface{}, bool, string) {
	state, ok := raw.(*researchMatchState)
	if !ok || state == nil {
		return nil, false, "invalid research state"
	}
	authID := metadata["authorization_id"]
	if len(metadata) != 1 || researchcontract.ValidateAuthorizationID(authID) != nil {
		return state, false, "join metadata must contain one authorization_id"
	}
	now := time.Now().UTC()
	state.prunePendingAdmissions(now)
	if len(state.pendingAuthorization) >= 4096 {
		return state, false, "too many pending research admissions"
	}
	if err := state.engine.ValidateJoin(presence.GetUserId(), authID, now); err != nil {
		return state, false, err.Error()
	}
	state.pendingAuthorization[presence.GetSessionId()] = pendingResearchAdmission{authorizationID: authID, expiresAt: now.Add(researchAdmissionTTL)}
	return state, true, ""
}
func (m *researchMatch) MatchJoin(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, _ int64, raw interface{}, presences []runtime.Presence) interface{} {
	state, ok := raw.(*researchMatchState)
	if !ok || state == nil {
		return nil
	}
	for _, presence := range presences {
		sessionID := presence.GetSessionId()
		pending := state.pendingAuthorization[sessionID]
		delete(state.pendingAuthorization, sessionID)
		if pending.authorizationID == "" || time.Now().UTC().After(pending.expiresAt) {
			_ = dispatcher.MatchKick([]runtime.Presence{presence})
			continue
		}
		authID := pending.authorizationID
		before, err := state.engine.Snapshot()
		if err != nil {
			_ = dispatcher.MatchKick([]runtime.Presence{presence})
			continue
		}
		result, err := state.engine.Join(presence.GetUserId(), authID, time.Now().UTC())
		if err != nil {
			_ = dispatcher.MatchKick([]runtime.Presence{presence})
			continue
		}
		if !result.Replay {
			if err := m.persist(ctx, nk, state, before); err != nil {
				logger.Error("research join persistence failed: %s", err.Error())
				_ = dispatcher.MatchKick([]runtime.Presence{presence})
				continue
			}
			_ = dispatcher.MatchLabelUpdate(state.label())
		}
		state.sessionAuthorization[sessionID] = authID
		state.sessionPresences[sessionID] = presence
		if state.authorizationSessions[authID] == nil {
			state.authorizationSessions[authID] = map[string]struct{}{}
		}
		state.authorizationSessions[authID][sessionID] = struct{}{}
		if result.Event != nil {
			if err := broadcastJSON(dispatcher, opCodeResearchEvent, *result.Event, state.currentPresences(), presence); err != nil {
				logger.Warn("research join broadcast failed: %s", err.Error())
			}
		}
	}
	return state
}
func (m *researchMatch) MatchLeave(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, _ int64, raw interface{}, presences []runtime.Presence) interface{} {
	state, ok := raw.(*researchMatchState)
	if !ok || state == nil {
		return nil
	}
	for _, presence := range presences {
		sessionID := presence.GetSessionId()
		authID := state.sessionAuthorization[sessionID]
		delete(state.sessionAuthorization, sessionID)
		delete(state.sessionPresences, sessionID)
		if authID == "" {
			continue
		}
		sessions := state.authorizationSessions[authID]
		delete(sessions, sessionID)
		if len(sessions) > 0 {
			continue
		}
		delete(state.authorizationSessions, authID)
		before, err := state.engine.Snapshot()
		if err != nil {
			return nil
		}
		result, err := state.engine.Disconnect(presence.GetUserId(), authID, time.Now().UTC())
		if err != nil || result.Replay {
			continue
		}
		if err := m.persist(ctx, nk, state, before); err != nil {
			logger.Error("research disconnect persistence failed: %s", err.Error())
			return nil
		}
		if result.Event != nil {
			_ = broadcastJSON(dispatcher, opCodeResearchEvent, *result.Event, state.currentPresences(), presence)
		}
		_ = dispatcher.MatchLabelUpdate(state.label())
	}
	return state
}
func (m *researchMatch) MatchLoop(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, tick int64, raw interface{}, messages []runtime.MatchData) interface{} {
	state, ok := raw.(*researchMatchState)
	if !ok || state == nil {
		return nil
	}
	if err := m.deliverPendingResearch(ctx, logger, nk, state); err != nil {
		logger.Warn("research Hepta delivery remains pending: %s", err.Error())
	}
	if shouldTerminateResearchRuntime(state, tick, m.module.config.matchTickRate) {
		return nil
	}
	if _, done := state.engine.Completion(); done {
		return state
	}
	for _, message := range messages {
		if message.GetOpCode() != opCodeResearchAction || len(message.GetData()) == 0 || len(message.GetData()) > maximumResearchActionBytes {
			broadcastResearchError(logger, dispatcher, message, "", "invalid research action opcode or size")
			continue
		}
		var action researchcontract.ActionEnvelope
		if err := decodeJSONStrict(string(message.GetData()), &action); err != nil {
			broadcastResearchError(logger, dispatcher, message, "", "invalid research action JSON")
			continue
		}
		if state.sessionAuthorization[message.GetSessionId()] != action.AuthorizationID {
			broadcastResearchError(logger, dispatcher, message, action.ActionID, "socket admission does not match current authorization")
			continue
		}
		before, err := state.engine.Snapshot()
		if err != nil {
			return nil
		}
		result, err := state.engine.ApplyAction(message.GetUserId(), action, time.Now().UTC())
		if err != nil {
			broadcastResearchError(logger, dispatcher, message, action.ActionID, err.Error())
			continue
		}
		if !result.Replay {
			if err := m.persist(ctx, nk, state, before); err != nil {
				logger.Error("research action persistence failed: %s", err.Error())
				return nil
			}
			_ = dispatcher.MatchLabelUpdate(state.label())
		}
		recipients := state.currentPresences()
		if result.Replay {
			recipients = []runtime.Presence{message}
		}
		if result.Event != nil {
			_ = broadcastJSON(dispatcher, opCodeResearchEvent, *result.Event, recipients, message)
		}
	}
	return state
}
func (*researchMatch) MatchTerminate(_ context.Context, _ runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, _ runtime.MatchDispatcher, _ int64, state interface{}, _ int) interface{} {
	return state
}

type researchSignal struct {
	Schema            string                                 `json:"schema"`
	Action            string                                 `json:"action"`
	LogicalSessionID  string                                 `json:"logical_session_id"`
	RuntimeGeneration uint64                                 `json:"runtime_generation"`
	OperatorToken     string                                 `json:"operator_token"`
	ControlCommandID  string                                 `json:"control_command_id,omitempty"`
	Facts             *researchcontract.TerminalFacts        `json:"facts,omitempty"`
	Authorizations    []researchcontract.SignedAuthorization `json:"authorizations,omitempty"`
}

func (m *researchMatch) MatchSignal(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, _ int64, raw interface{}, data string) (interface{}, string) {
	state, ok := raw.(*researchMatchState)
	if !ok || state == nil {
		return nil, `{"error":"invalid research state"}`
	}
	var signal researchSignal
	if err := decodeJSONStrict(data, &signal); err != nil || signal.Schema != "trnm.nakama.research-session.signal.v1" || !operatorTokenWireValid(signal.OperatorToken, false) || !m.module.config.operatorAuthorized(signal.OperatorToken) {
		return state, `{"error":"invalid or unauthorized signal"}`
	}
	if signal.LogicalSessionID != state.instanceSessionID || signal.LogicalSessionID != state.record.LogicalSessionID || signal.RuntimeGeneration != state.instanceGeneration || signal.RuntimeGeneration != state.record.RuntimeGeneration {
		return state, `{"error":"research signal is fenced"}`
	}
	var control *versionedStoredResearchControl
	if signal.ControlCommandID != "" {
		storedControl, err := loadStoredResearchControl(ctx, nk, signal.ControlCommandID, m.module.config.controlIssuerKeys)
		if err != nil || storedControl.record.SessionID != state.record.LogicalSessionID || storedControl.record.Operation != signal.Action {
			return state, `{"error":"signed research control is invalid"}`
		}
		if storedControl.record.Status == researchControlStatusApplied {
			response, err := storedControl.record.response()
			if err != nil {
				return state, `{"error":"signed research control receipt is invalid"}`
			}
			return state, response
		}
		control = &storedControl
	}
	before, err := state.engine.Snapshot()
	if err != nil {
		return nil, `{"error":"research snapshot unavailable"}`
	}
	switch signal.Action {
	case "complete":
		facts := signal.Facts
		if control != nil {
			if signal.Facts != nil || len(signal.Authorizations) != 0 ||
				control.record.SessionRosterVersion != state.engine.View().RosterVersion ||
				control.record.AuthorizationSetID != state.record.ControlAuthorizationSetID {
				return state, `{"error":"signed completion control is fenced"}`
			}
			request, err := storedResearchCompleteRequestV2(control.record)
			if err != nil {
				return state, `{"error":"signed completion control payload is invalid"}`
			}
			facts = &request.Facts
		} else if signal.Facts == nil || len(signal.Authorizations) != 0 {
			return state, `{"error":"invalid completion signal"}`
		}
		now := time.Now().UTC()
		completion, err := state.engine.Complete(*facts, now)
		if err != nil {
			return state, errorSignal(err.Error())
		}
		completionOutbox, err := newStoredResearchCompletionOutbox(completion, state.engine.Events(), state.engine.AuthorityPublicKey())
		if err != nil {
			return state, errorSignal(m.rollback(state, before, err).Error())
		}
		var controlResponse string
		if control != nil {
			if err := control.record.applyResult(researchEvidenceFor(state.record, completion, state.engine.AuthorityPublicKey()), now); err != nil {
				return state, errorSignal(m.rollback(state, before, err).Error())
			}
			controlResponse, err = control.record.response()
			if err != nil {
				return state, errorSignal(m.rollback(state, before, err).Error())
			}
		}
		if err := m.persistWithCompletionOutboxAndControl(ctx, nk, state, before, completionOutbox, control); err != nil {
			logger.Error("research completion persistence failed: %s", err.Error())
			return nil, `{"error":"research persistence failed"}`
		}
		_ = dispatcher.MatchLabelUpdate(state.label())
		_ = broadcastJSON(dispatcher, opCodeResearchCompletion, completion, state.currentPresences(), nil)
		state.nextDeliveryAttempt = time.Time{}
		state.deliveryAttempts = 0
		if err := m.deliverPendingResearch(ctx, logger, nk, state); err != nil {
			logger.Warn("research completion delivery pending: %s", err.Error())
		}
		if control != nil {
			return state, controlResponse
		}
		response, _ := json.Marshal(researchEvidenceFor(state.record, completion, state.engine.AuthorityPublicKey()))
		return state, string(response)
	case "replace_roster":
		authorizations := signal.Authorizations
		if control != nil {
			if signal.Facts != nil || len(signal.Authorizations) != 0 || control.record.SessionRosterVersion != state.engine.View().RosterVersion+1 {
				return state, `{"error":"signed replacement control is fenced"}`
			}
			request, err := storedResearchReplaceRequestV2(control.record)
			if err != nil {
				return state, `{"error":"signed replacement control payload is invalid"}`
			}
			authorizations = request.Authorizations
		} else if signal.Facts != nil || len(signal.Authorizations) < researchcontract.MinParticipants || len(signal.Authorizations) > researchcontract.MaxParticipants {
			return state, `{"error":"invalid replacement signal"}`
		}
		now := time.Now().UTC()
		outbox, outboxErr := newStoredResearchConsumptionOutbox(authorizations, now.Unix())
		if outboxErr != nil {
			return state, errorSignal(outboxErr.Error())
		}
		_, err := state.engine.ReplaceRoster(authorizations, now)
		if err != nil {
			return state, errorSignal(err.Error())
		}
		var controlResponse string
		if control != nil {
			if err := control.record.applyResult(researchRuntimeFor(state.record, state.engine.View(), state.record.ExternalMatchID), now); err != nil {
				return state, errorSignal(m.rollback(state, before, err).Error())
			}
			controlResponse, err = control.record.response()
			if err != nil {
				return state, errorSignal(m.rollback(state, before, err).Error())
			}
		}
		if err := m.persistWithConsumptionOutboxAndControl(ctx, nk, state, before, outbox, control); err != nil {
			return nil, `{"error":"research persistence failed"}`
		}
		oldPresences := state.currentPresences()
		state.pendingAuthorization = map[string]pendingResearchAdmission{}
		state.sessionAuthorization = map[string]string{}
		state.authorizationSessions = map[string]map[string]struct{}{}
		state.sessionPresences = map[string]runtime.Presence{}
		_ = dispatcher.MatchLabelUpdate(state.label())
		if len(oldPresences) > 0 {
			if err := dispatcher.MatchKick(oldPresences); err != nil {
				logger.Warn("research replacement could not kick every old epoch presence: %s", err.Error())
			}
		}
		state.nextDeliveryAttempt = time.Time{}
		state.deliveryAttempts = 0
		if err := m.deliverPendingResearch(ctx, logger, nk, state); err != nil {
			logger.Warn("research replacement authorization consumption delivery pending: %s", err.Error())
		}
		if control != nil {
			return state, controlResponse
		}
		response, _ := json.Marshal(researchRuntimeFor(state.record, state.engine.View(), state.record.ExternalMatchID))
		return state, string(response)
	default:
		return state, `{"error":"unsupported research signal"}`
	}
}

func (m *researchMatch) persist(ctx context.Context, nk storageGateway, state *researchMatchState, before []byte) error {
	return m.persistWithOptionalOutboxes(ctx, nk, state, before, nil, nil, nil)
}

func (m *researchMatch) persistWithConsumptionOutbox(ctx context.Context, nk storageGateway, state *researchMatchState, before []byte, outbox storedResearchConsumptionOutbox) error {
	return m.persistWithOptionalOutboxes(ctx, nk, state, before, &outbox, nil, nil)
}

func (m *researchMatch) persistWithCompletionOutbox(ctx context.Context, nk storageGateway, state *researchMatchState, before []byte, outbox storedResearchCompletionOutbox) error {
	return m.persistWithOptionalOutboxes(ctx, nk, state, before, nil, &outbox, nil)
}

func (m *researchMatch) persistWithConsumptionOutboxAndControl(ctx context.Context, nk storageGateway, state *researchMatchState,
	before []byte, outbox storedResearchConsumptionOutbox, control *versionedStoredResearchControl) error {
	return m.persistWithOptionalOutboxes(ctx, nk, state, before, &outbox, nil, control)
}

func (m *researchMatch) persistWithCompletionOutboxAndControl(ctx context.Context, nk storageGateway, state *researchMatchState,
	before []byte, outbox storedResearchCompletionOutbox, control *versionedStoredResearchControl) error {
	return m.persistWithOptionalOutboxes(ctx, nk, state, before, nil, &outbox, control)
}

func (m *researchMatch) persistWithOptionalOutboxes(ctx context.Context, nk storageGateway, state *researchMatchState, before []byte,
	consumption *storedResearchConsumptionOutbox, completion *storedResearchCompletionOutbox, control *versionedStoredResearchControl) error {
	after, err := state.engine.Snapshot()
	if err != nil {
		return m.rollback(state, before, err)
	}
	updated := cloneStoredResearchSession(state.record)
	if consumption != nil {
		if consumption.RosterVersion != uint64(len(updated.ConsumptionOutboxes)+1) {
			return m.rollback(state, before, errors.New("research consumption outbox epoch is not next"))
		}
		updated.ConsumptionOutboxes = append(updated.ConsumptionOutboxes, *consumption)
	}
	if completion != nil {
		if updated.CompletionOutbox != nil {
			return m.rollback(state, before, errors.New("research completion outbox already exists"))
		}
		copy := *completion
		updated.CompletionOutbox = &copy
	}
	if control != nil && control.record.Operation == researchcontract.ResearchControlOperationReplace {
		updated.ControlAuthorizationSetID = control.record.AuthorizationSetID
	}
	updated.setSnapshot(after)
	var version string
	if control == nil {
		version, err = updateStoredResearch(ctx, nk, updated, state.storageVersion)
	} else {
		var controlVersion string
		version, controlVersion, err = updateStoredResearchWithControl(ctx, nk, updated, state.storageVersion,
			control.record, control.version, m.module.config.controlIssuerKeys)
		if err == nil {
			control.version = controlVersion
		}
	}
	if err != nil {
		return m.rollback(state, before, err)
	}
	state.record = updated
	state.storageVersion = version
	return nil
}

func (m *researchMatch) deliverPendingResearch(ctx context.Context, logger runtime.Logger, nk storageGateway, state *researchMatchState) error {
	now := time.Now().UTC()
	if !state.nextDeliveryAttempt.IsZero() && now.Before(state.nextDeliveryAttempt) {
		return nil
	}
	var deliveryErr error
	for hasPendingResearchConsumption(state.record) {
		if deliveryErr = m.module.deliverPendingResearchConsumption(ctx, logger, nk, state); deliveryErr != nil {
			break
		}
	}
	if deliveryErr == nil {
		deliveryErr = m.module.deliverPendingResearchCompletion(ctx, logger, nk, state)
	}
	if deliveryErr != nil {
		state.deliveryAttempts++
		delay := time.Second << min(state.deliveryAttempts, uint32(5))
		if delay > 30*time.Second {
			delay = 30 * time.Second
		}
		state.nextDeliveryAttempt = now.Add(delay)
		return deliveryErr
	}
	state.deliveryAttempts = 0
	state.nextDeliveryAttempt = time.Time{}
	return nil
}

func hasPendingResearchConsumption(record storedResearchSession) bool {
	for _, outbox := range record.ConsumptionOutboxes {
		if outbox.DeliveredAtUnix == nil {
			return true
		}
	}
	return false
}

func hasPendingResearchDeliveries(record storedResearchSession) bool {
	return hasPendingResearchConsumption(record) ||
		(record.CompletionOutbox != nil && record.CompletionOutbox.DeliveredAtUnix == nil)
}

func shouldTerminateResearchRuntime(state *researchMatchState, tick int64, tickRate int) bool {
	if _, completed := state.engine.Completion(); completed {
		return !hasPendingResearchDeliveries(state.record)
	}
	return runtimeGenerationExpired(tick, tickRate)
}
func (m *researchMatch) rollback(state *researchMatchState, before []byte, cause error) error {
	engine, err := researchcore.Restore(before, researchcore.RestoreOptions{TrustedIssuerKeys: m.module.config.issuerKeys, AuthorityKeyID: m.module.config.authorityKeyID, AuthorityPrivateKey: m.module.config.authorityPrivateKey})
	if err != nil {
		return fmt.Errorf("%w; rollback failed: %v", cause, err)
	}
	state.engine = engine
	return cause
}
func (state *researchMatchState) label() string {
	view := state.engine.View()
	encoded, _ := json.Marshal(struct {
		Schema        string              `json:"schema"`
		SessionID     string              `json:"session_id"`
		Status        researchcore.Status `json:"status"`
		RosterVersion uint64              `json:"roster_version"`
		Generation    uint64              `json:"runtime_generation"`
	}{"trnm.nakama.research-session.match-label.v1", view.SessionID, view.Status, view.RosterVersion, state.record.RuntimeGeneration})
	return string(encoded)
}

func (state *researchMatchState) currentPresences() []runtime.Presence {
	out := make([]runtime.Presence, 0, len(state.sessionPresences))
	for sessionID, presence := range state.sessionPresences {
		if state.sessionAuthorization[sessionID] != "" {
			out = append(out, presence)
		}
	}
	return out
}

func (state *researchMatchState) prunePendingAdmissions(now time.Time) {
	for sessionID, pending := range state.pendingAuthorization {
		if !pending.expiresAt.After(now) {
			delete(state.pendingAuthorization, sessionID)
		}
	}
}
func broadcastResearchError(logger runtime.Logger, dispatcher runtime.MatchDispatcher, sender runtime.MatchData, actionID, reason string) {
	value := struct {
		Schema   string `json:"schema"`
		ActionID string `json:"action_id,omitempty"`
		Reason   string `json:"reason"`
	}{"trnm.research-session.action-rejected.v1", actionID, reason}
	if err := broadcastJSON(dispatcher, opCodeResearchError, value, []runtime.Presence{sender}, sender); err != nil {
		logger.Warn("research rejection broadcast failed")
	}
}
