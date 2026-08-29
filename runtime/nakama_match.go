package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	matchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/core"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	opCodeCommand       int64 = 1
	opCodeEvent         int64 = 2
	opCodeCommandError  int64 = 3
	opCodeCompletion    int64 = 4
	maximumCommandBytes       = 128 * 1024
	// A logical match may be resumed only by an authenticated operator, but no
	// single in-memory runtime generation is allowed to live forever. At the
	// configured tick rate this caps one generation at six hours.
	maximumRuntimeGenerationSeconds = 6 * 60 * 60
)

type authoritativeMatch struct {
	module *moduleRuntime
}

type authoritativeMatchState struct {
	engine                    *matchcore.Engine
	record                    storedMatch
	storageVersion            string
	instanceLogicalMatchID    string
	instanceRuntimeGeneration uint64
	pendingJoinEvents         map[string]contract.MatchEvent
}

var _ runtime.Match = (*authoritativeMatch)(nil)

func (m *authoritativeMatch) MatchInit(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, params map[string]interface{}) (interface{}, int, string) {
	if err := m.module.config.ready(); err != nil {
		logger.Error("authoritative match init rejected unready configuration: %s", err.Error())
		return nil, 0, ""
	}
	logicalMatchID, ok := params["logical_match_id"].(string)
	if !ok || contract.ValidateLogicalMatchID(logicalMatchID) != nil {
		logger.Error("authoritative match init received invalid logical_match_id")
		return nil, 0, ""
	}
	generation, err := generationParameter(params["runtime_generation"])
	if err != nil {
		logger.Error("authoritative match init received invalid runtime_generation: %s", err.Error())
		return nil, 0, ""
	}
	stored, err := loadStoredMatch(ctx, nk, logicalMatchID)
	if err != nil {
		logger.Error("authoritative match init could not load snapshot: %s", err.Error())
		return nil, 0, ""
	}
	if stored.record.RuntimeGeneration != generation {
		logger.Error("authoritative match init generation is fenced")
		return nil, 0, ""
	}
	engine, err := m.module.restoreStoredEngine(stored.record)
	if err != nil {
		logger.Error("authoritative match init rejected snapshot: %s", err.Error())
		return nil, 0, ""
	}
	if _, completed := engine.Completion(); completed {
		logger.Error("authoritative match init refused an already completed match")
		return nil, 0, ""
	}

	externalMatchID, _ := ctx.Value(runtime.RUNTIME_CTX_MATCH_ID).(string)
	if externalMatchID == "" {
		logger.Error("authoritative match init has no external match id in runtime context")
		return nil, 0, ""
	}
	stored.record.ExternalMatchID = externalMatchID
	newVersion, err := updateStoredMatch(ctx, nk, stored.record, stored.version)
	if err != nil {
		logger.Error("authoritative match init could not fence storage record: %s", err.Error())
		return nil, 0, ""
	}
	state := &authoritativeMatchState{
		engine:                    engine,
		record:                    stored.record,
		storageVersion:            newVersion,
		instanceLogicalMatchID:    logicalMatchID,
		instanceRuntimeGeneration: generation,
		pendingJoinEvents:         make(map[string]contract.MatchEvent),
	}
	return state, m.module.config.matchTickRate, state.label()
}

func (m *authoritativeMatch) MatchJoinAttempt(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, _ int64, rawState interface{}, presence runtime.Presence, metadata map[string]string) (interface{}, bool, string) {
	state, ok := rawState.(*authoritativeMatchState)
	if !ok || state == nil {
		return nil, false, "invalid match state"
	}
	authorizationID := metadata["authorization_id"]
	if len(metadata) != 1 || contract.ValidateAuthorizationID(authorizationID) != nil {
		return state, false, "join metadata must contain only one valid authorization_id"
	}
	before, err := state.engine.Snapshot()
	if err != nil {
		logger.Error("join checkpoint failed: %s", err.Error())
		return nil, false, "match snapshot unavailable"
	}
	result, err := state.engine.Join(presence.GetUserId(), authorizationID, time.Now().UTC())
	if err != nil {
		return state, false, err.Error()
	}
	if !result.Replay {
		if err := m.persistMutation(ctx, nk, state, before); err != nil {
			logger.Error("join persistence failed: %s", err.Error())
			return nil, false, "match persistence failed"
		}
		if result.Event != nil {
			state.pendingJoinEvents[presence.GetSessionId()] = *result.Event
		}
		if err := dispatcher.MatchLabelUpdate(state.label()); err != nil {
			logger.Warn("durable join label update failed: %s", err.Error())
		}
	}
	return state, true, ""
}

func (m *authoritativeMatch) MatchJoin(_ context.Context, logger runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, dispatcher runtime.MatchDispatcher, _ int64, rawState interface{}, presences []runtime.Presence) interface{} {
	state, ok := rawState.(*authoritativeMatchState)
	if !ok || state == nil {
		return nil
	}
	for _, presence := range presences {
		event, exists := state.pendingJoinEvents[presence.GetSessionId()]
		if !exists {
			continue
		}
		delete(state.pendingJoinEvents, presence.GetSessionId())
		if err := broadcastJSON(dispatcher, opCodeEvent, event, nil, presence); err != nil {
			// The event is already durable. A reconnect can recover current state;
			// never roll back committed authority because a broadcast failed.
			logger.Warn("durable join event broadcast failed: %s", err.Error())
		}
	}
	return state
}

func (*authoritativeMatch) MatchLeave(_ context.Context, _ runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, _ runtime.MatchDispatcher, _ int64, state interface{}, _ []runtime.Presence) interface{} {
	// Presence is transport state. Authorization consumption and authoritative
	// match state remain durable so reconnecting cannot replay admission.
	return state
}

func (m *authoritativeMatch) MatchLoop(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, tick int64, rawState interface{}, messages []runtime.MatchData) interface{} {
	state, ok := rawState.(*authoritativeMatchState)
	if !ok || state == nil {
		return nil
	}
	// Returning nil directly from MatchSignal makes Nakama report the signal as
	// failed even after completion was durably committed and broadcast. Preserve
	// completed state through the signal response; the next loop tick stops the
	// runtime before any additional client message can be processed.
	if _, completed := state.engine.Completion(); completed {
		return nil
	}
	if runtimeGenerationExpired(tick, m.module.config.matchTickRate) {
		logger.Warn("authoritative runtime generation reached its six-hour lifecycle limit")
		return nil
	}
	for _, message := range messages {
		if message.GetOpCode() != opCodeCommand {
			broadcastCommandError(logger, dispatcher, message, "", "unsupported opcode")
			continue
		}
		if len(message.GetData()) == 0 || len(message.GetData()) > maximumCommandBytes {
			broadcastCommandError(logger, dispatcher, message, "", "command payload size is invalid")
			continue
		}
		var command contract.CommandEnvelope
		if err := decodeJSONStrict(string(message.GetData()), &command); err != nil {
			broadcastCommandError(logger, dispatcher, message, "", "command JSON is invalid")
			continue
		}
		before, err := state.engine.Snapshot()
		if err != nil {
			logger.Error("command checkpoint failed: %s", err.Error())
			return nil
		}
		result, err := state.engine.ApplyCommand(message.GetUserId(), command, time.Now().UTC())
		if err != nil {
			broadcastCommandError(logger, dispatcher, message, command.CommandID, err.Error())
			continue
		}
		if !result.Replay {
			if err := m.persistMutation(ctx, nk, state, before); err != nil {
				logger.Error("command persistence failed: %s", err.Error())
				return nil
			}
			if err := dispatcher.MatchLabelUpdate(state.label()); err != nil {
				logger.Warn("durable command label update failed: %s", err.Error())
			}
		}
		recipients := []runtime.Presence(nil)
		if result.Replay {
			recipients = []runtime.Presence{message}
		}
		if err := broadcastJSON(dispatcher, opCodeEvent, result.Event, recipients, message); err != nil {
			logger.Warn("durable command event broadcast failed: %s", err.Error())
		}
	}
	return state
}

func runtimeGenerationExpired(tick int64, tickRate int) bool {
	if tick < 0 || tickRate < 1 {
		return true
	}
	return tick >= int64(tickRate)*maximumRuntimeGenerationSeconds
}

func (*authoritativeMatch) MatchTerminate(_ context.Context, _ runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, _ runtime.MatchDispatcher, _ int64, state interface{}, _ int) interface{} {
	// Every accepted mutation is persisted before acknowledgement/broadcast.
	// Termination has no additional in-memory state to flush.
	return state
}

type completeSignal struct {
	Schema            string                 `json:"schema"`
	Action            string                 `json:"action"`
	LogicalMatchID    string                 `json:"logical_match_id"`
	RuntimeGeneration uint64                 `json:"runtime_generation"`
	OperatorToken     string                 `json:"operator_token"`
	Facts             contract.TerminalFacts `json:"facts"`
}

func (m *authoritativeMatch) MatchSignal(ctx context.Context, logger runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, dispatcher runtime.MatchDispatcher, _ int64, rawState interface{}, data string) (interface{}, string) {
	state, ok := rawState.(*authoritativeMatchState)
	if !ok || state == nil {
		return nil, `{"error":"invalid match state"}`
	}
	var signal completeSignal
	if err := decodeJSONStrict(data, &signal); err != nil || signal.Schema != "trnm.nakama.match-signal.v1" ||
		signal.Action != "complete" || !operatorTokenWireValid(signal.OperatorToken, false) {
		return state, `{"error":"invalid signal"}`
	}
	if !m.module.config.operatorAuthorized(signal.OperatorToken) {
		return state, `{"error":"operator authorization rejected"}`
	}
	if err := validateCompleteSignalBinding(signal, state); err != nil {
		return state, errorSignal(err.Error())
	}
	return m.completeAndTerminate(ctx, logger, nk, dispatcher, state, signal.Facts)
}

func validateCompleteSignalBinding(signal completeSignal, state *authoritativeMatchState) error {
	if state == nil || contract.ValidateLogicalMatchID(signal.LogicalMatchID) != nil || signal.RuntimeGeneration == 0 {
		return errors.New("completion signal binding is invalid")
	}
	view := state.engine.View()
	if signal.LogicalMatchID != state.instanceLogicalMatchID || signal.LogicalMatchID != state.record.LogicalMatchID ||
		signal.LogicalMatchID != view.MatchID {
		return errors.New("completion signal logical match is fenced")
	}
	if signal.RuntimeGeneration != state.instanceRuntimeGeneration || signal.RuntimeGeneration != state.record.RuntimeGeneration {
		return errors.New("completion signal runtime generation is fenced")
	}
	return nil
}

func (m *authoritativeMatch) completeAndTerminate(ctx context.Context, logger runtime.Logger, nk storageGateway, dispatcher runtime.MatchDispatcher, state *authoritativeMatchState, facts contract.TerminalFacts) (interface{}, string) {
	beforeCompletion, alreadyCompleted := state.engine.Completion()
	before, err := state.engine.Snapshot()
	if err != nil {
		logger.Error("completion checkpoint failed: %s", err.Error())
		return nil, `{"error":"match snapshot unavailable"}`
	}
	completion, err := state.engine.Complete(facts, time.Now().UTC())
	if err != nil {
		return state, errorSignal(err.Error())
	}
	if !alreadyCompleted {
		if err := m.persistMutation(ctx, nk, state, before); err != nil {
			logger.Error("completion persistence failed: %s", err.Error())
			return nil, `{"error":"match persistence failed"}`
		}
		if err := dispatcher.MatchLabelUpdate(state.label()); err != nil {
			logger.Warn("durable completion label update failed: %s", err.Error())
		}
		if err := broadcastJSON(dispatcher, opCodeCompletion, completion, nil, nil); err != nil {
			logger.Warn("durable completion broadcast failed: %s", err.Error())
		}
	} else if beforeCompletion != nil {
		completion = *beforeCompletion
	}
	response, _ := json.Marshal(evidenceResponseFrom(state, completion))
	return state, string(response)
}

func (m *authoritativeMatch) persistMutation(ctx context.Context, nk storageGateway, state *authoritativeMatchState, before []byte) error {
	after, err := state.engine.Snapshot()
	if err != nil {
		return m.rollback(state, before, fmt.Errorf("encode mutated snapshot: %w", err))
	}
	updated := state.record
	updated.setSnapshot(after)
	newVersion, err := updateStoredMatch(ctx, nk, updated, state.storageVersion)
	if err != nil {
		return m.rollback(state, before, err)
	}
	state.record = updated
	state.storageVersion = newVersion
	return nil
}

func (m *authoritativeMatch) rollback(state *authoritativeMatchState, before []byte, cause error) error {
	restored, restoreErr := m.module.restoreEngineForRecord(state.record, before)
	if restoreErr != nil {
		return fmt.Errorf("%w; rollback failed: %v", cause, restoreErr)
	}
	state.engine = restored
	return cause
}

func (s *authoritativeMatchState) label() string {
	view := s.engine.View()
	label, _ := json.Marshal(struct {
		Schema         string           `json:"schema"`
		LogicalMatchID string           `json:"logical_match_id"`
		Status         matchcore.Status `json:"status"`
		Generation     uint64           `json:"runtime_generation"`
	}{"trnm.nakama.match-label.v1", view.MatchID, view.Status, s.record.RuntimeGeneration})
	return string(label)
}

func generationParameter(value interface{}) (uint64, error) {
	switch typed := value.(type) {
	case uint64:
		if typed > 0 {
			return typed, nil
		}
	case int:
		if typed > 0 {
			return uint64(typed), nil
		}
	case int64:
		if typed > 0 {
			return uint64(typed), nil
		}
	case float64:
		if typed > 0 && typed == float64(uint64(typed)) {
			return uint64(typed), nil
		}
	case string:
		parsed, err := strconv.ParseUint(typed, 10, 64)
		if err == nil && parsed > 0 {
			return parsed, nil
		}
	}
	return 0, errors.New("generation must be a positive integer")
}

func broadcastJSON(dispatcher runtime.MatchDispatcher, opcode int64, value any, recipients []runtime.Presence, sender runtime.Presence) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return dispatcher.BroadcastMessage(opcode, encoded, recipients, sender, true)
}

func broadcastCommandError(logger runtime.Logger, dispatcher runtime.MatchDispatcher, sender runtime.MatchData, commandID, reason string) {
	value := commandRejection(commandID, reason)
	if err := broadcastJSON(dispatcher, opCodeCommandError, value, []runtime.Presence{sender}, sender); err != nil {
		logger.Warn("command rejection broadcast failed: %s", err.Error())
	}
}

type commandRejected struct {
	Schema    string `json:"schema"`
	CommandID string `json:"command_id,omitempty"`
	Reason    string `json:"reason"`
}

func commandRejection(commandID, reason string) commandRejected {
	if contract.ValidateCommandID(commandID) != nil {
		commandID = ""
	}
	reason = strings.ToValidUTF8(reason, "�")
	reason = strings.ReplaceAll(reason, "\x00", "�")
	if reason == "" {
		reason = "command rejected"
	}
	const maximumReasonRunes = 4096
	if utf8.RuneCountInString(reason) > maximumReasonRunes {
		reason = string([]rune(reason)[:maximumReasonRunes])
	}
	return commandRejected{Schema: "trnm.match.command-rejected.v1", CommandID: commandID, Reason: reason}
}

func errorSignal(reason string) string {
	encoded, _ := json.Marshal(struct {
		Error string `json:"error"`
	}{reason})
	return string(encoded)
}
