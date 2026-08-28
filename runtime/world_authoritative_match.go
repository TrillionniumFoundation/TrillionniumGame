package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	matchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/core"
	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldcommand"
	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldtransition"
	"github.com/heroiclabs/nakama-common/runtime"
)

type worldCommandRuntime struct {
	config   worldCommandRuntimeConfig
	executor worldcommand.Executor
	initErr  error
}

func newWorldCommandRuntime(config worldCommandRuntimeConfig) *worldCommandRuntime {
	result := &worldCommandRuntime{config: config}
	if config.profile == worldProfileLegacy {
		return result
	}
	result.executor, result.initErr = newWorldHTTPSExecutor(config)
	return result
}

type worldAuthoritativeMatch struct {
	*authoritativeMatch
	world  *worldCommandRuntime
	store  *worldcommand.Store
	target bool
}

var _ runtime.Match = (*worldAuthoritativeMatch)(nil)

func (m *worldAuthoritativeMatch) MatchInit(
	ctx context.Context,
	logger runtime.Logger,
	db *sql.DB,
	nk runtime.NakamaModule,
	params map[string]interface{},
) (interface{}, int, string) {
	raw, tickRate, label := m.authoritativeMatch.MatchInit(ctx, logger, db, nk, params)
	state, ok := raw.(*authoritativeMatchState)
	if !ok || state == nil {
		return raw, tickRate, label
	}
	if m.world == nil || m.world.config.profile == worldProfileLegacy {
		m.target = false
		return state, tickRate, label
	}
	if m.world.initErr != nil {
		logger.Error("target World command runtime is unready: %s", m.world.initErr.Error())
		return nil, 0, ""
	}
	binding, err := state.engine.WorldBinding()
	if err != nil || m.world.config.targetBinding(binding) != nil {
		logger.Error("target World command binding rejected: %v", errors.Join(err, m.world.config.targetBinding(binding)))
		return nil, 0, ""
	}
	m.target = true
	if err := m.openWorldStoreIfPresent(ctx, nk, state); err != nil {
		logger.Error("target World command store restore failed: %s", err.Error())
		return nil, 0, ""
	}
	return state, tickRate, label
}

func (m *worldAuthoritativeMatch) MatchLoop(
	ctx context.Context,
	logger runtime.Logger,
	db *sql.DB,
	nk runtime.NakamaModule,
	dispatcher runtime.MatchDispatcher,
	tick int64,
	rawState interface{},
	messages []runtime.MatchData,
) interface{} {
	if !m.target {
		return m.authoritativeMatch.MatchLoop(ctx, logger, db, nk, dispatcher, tick, rawState, messages)
	}
	state, ok := rawState.(*authoritativeMatchState)
	if !ok || state == nil {
		return nil
	}
	if _, completed := state.engine.Completion(); completed {
		return nil
	}
	if runtimeGenerationExpired(tick, m.module.config.matchTickRate) {
		logger.Warn("target World runtime generation reached its lifecycle limit")
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
		fatal, err := m.executeTargetCommand(ctx, nk, dispatcher, state, message, command)
		if err != nil {
			broadcastCommandError(logger, dispatcher, message, command.CommandID, err.Error())
			if fatal {
				logger.Error("target World command failed closed: %s", err.Error())
				return nil
			}
			continue
		}
		if err := dispatcher.MatchLabelUpdate(state.label()); err != nil {
			logger.Warn("target World command label update failed: %s", err.Error())
		}
	}
	return state
}

func (m *worldAuthoritativeMatch) executeTargetCommand(
	ctx context.Context,
	nk runtime.NakamaModule,
	dispatcher runtime.MatchDispatcher,
	state *authoritativeMatchState,
	message runtime.MatchData,
	command contract.CommandEnvelope,
) (bool, error) {
	now := time.Now().UTC()
	preflight, err := state.engine.PreflightCommand(message.GetUserId(), command, now)
	if err != nil {
		return false, err
	}
	if err := m.ensureWorldStore(ctx, nk, state); err != nil {
		return true, err
	}
	if preflight.Replay {
		receipt, exists := m.store.Receipt(command.CommandID)
		if !exists || receipt.Disposition != worldcommand.DispositionAccepted || preflight.Event == nil {
			return true, errors.New("core replay has no matching committed World receipt")
		}
		if err := broadcastJSON(dispatcher, opCodeEvent, *preflight.Event, []runtime.Presence{message}, message); err != nil {
			return false, err
		}
		return false, nil
	}
	if command.PayloadType != m.world.config.commandSchemaID {
		return false, fmt.Errorf("target command payload_type must be %q", m.world.config.commandSchemaID)
	}
	codec := worldcommand.WorldTransitionCodec{}
	commandValue, err := codec.DecodeCanonical(command.Payload, worldtransition.MaxCommandBytes)
	if err != nil {
		return false, fmt.Errorf("target command payload is not exact canonical JSON: %w", err)
	}
	storeState := m.store.State()
	binding, err := state.engine.WorldBinding()
	if err != nil {
		return true, err
	}
	if err := validateWorldStoreAgainstCore(storeState, binding); err != nil {
		return true, err
	}
	if binding.MatchVersion > math.MaxInt64 || binding.NextGlobalEventSequence > math.MaxInt64 {
		return true, errors.New("authoritative cursor exceeds World transition signed-i64 range")
	}
	request := worldcommand.PrepareRequest{
		ClientCommandID:       command.CommandID,
		UserID:                message.GetUserId(),
		ParticipantID:         command.AuthorizationID,
		ParticipantSequence:   command.ParticipantSequence,
		ExpectedStateRevision: storeState.StateRevision,
		ExpectedStateHash:     storeState.StateHash,
		CommandSchemaID:       m.world.config.commandSchemaID,
		Command:               commandValue,
		Context: worldtransition.AuthorityContext{
			MatchID:               binding.MatchID,
			AuthorizationID:       command.AuthorizationID,
			ParticipantRosterHash: bareDigest(binding.RosterRoot),
			MatchVersion:          int64(binding.MatchVersion),
			GlobalEventSequence:   int64(binding.NextGlobalEventSequence),
			CommandIdempotencyKey: command.CommandID,
			RulesetRevision:       m.world.config.rulesetRevision,
			ContentRevision:       m.world.config.contentRevision,
			ExpectedTick:          storeState.Tick,
		},
	}

	var appliedEvent *contract.MatchEvent
	coordinator := worldcommand.Coordinator{
		Store:    m.store,
		Executor: m.world.executor,
		Persister: func(
			commitCtx context.Context,
			_ string,
			expectedWorldVersion string,
			worldPayload []byte,
			receipt worldcommand.Receipt,
			candidate worldcommand.MatchState,
		) (string, error) {
			beforeCore, snapshotErr := state.engine.Snapshot()
			if snapshotErr != nil {
				return "", snapshotErr
			}
			if receipt.Disposition == worldcommand.DispositionAccepted {
				result, applyErr := state.engine.ApplyCommand(message.GetUserId(), command, time.Unix(receipt.CommittedAtUnix, 0).UTC())
				if applyErr != nil {
					return "", applyErr
				}
				if result.Replay || receipt.EventSequence == nil || result.Event.Sequence != *receipt.EventSequence ||
					result.Event.MatchVersion != receipt.MatchVersion || result.Version != candidate.MatchVersion {
					return "", errors.New("core event and World candidate authority cursors diverged")
				}
				copyEvent := result.Event
				appliedEvent = &copyEvent
			}
			rollback := func(snapshot []byte) error {
				restored, restoreErr := m.module.restoreEngineForRecord(state.record, snapshot)
				if restoreErr != nil {
					return restoreErr
				}
				state.engine = restored
				appliedEvent = nil
				return nil
			}
			return persistWorldAndCoreAtomic(commitCtx, nk, state, expectedWorldVersion, worldPayload, rollback, beforeCore)
		},
	}
	receipt, err := coordinator.Execute(ctx, request)
	if err != nil {
		var execution *worldcommand.ExecutionError
		if errors.As(err, &execution) && execution.Retryable {
			return false, err
		}
		if errors.Is(err, worldcommand.ErrRetryable) {
			return false, err
		}
		return true, err
	}
	if receipt.Disposition == worldcommand.DispositionRejected {
		code := "world_rejected"
		if receipt.ErrorCode != nil {
			code = *receipt.ErrorCode
		}
		return false, fmt.Errorf("World deterministic command rejected: %s", code)
	}
	if appliedEvent == nil {
		return true, errors.New("accepted World receipt committed without a core event")
	}
	if err := broadcastJSON(dispatcher, opCodeEvent, *appliedEvent, nil, message); err != nil {
		return false, err
	}
	return false, nil
}

func (m *worldAuthoritativeMatch) openWorldStoreIfPresent(ctx context.Context, nk storageGateway, state *authoritativeMatchState) error {
	backend := &nakamaWorldCommandBackend{nk: nk, logicalMatchID: state.instanceLogicalMatchID}
	payload, _, err := backend.Load(ctx, state.instanceLogicalMatchID)
	if err != nil {
		return err
	}
	if len(payload) == 0 {
		return nil
	}
	store, err := worldcommand.OpenStore(ctx, state.instanceLogicalMatchID, backend, worldcommand.WorldTransitionCodec{})
	if err != nil {
		return err
	}
	binding, err := state.engine.WorldBinding()
	if err != nil {
		return err
	}
	if err := validateWorldStoreAgainstCore(store.State(), binding); err != nil {
		return err
	}
	m.store = store
	return nil
}

func (m *worldAuthoritativeMatch) ensureWorldStore(ctx context.Context, nk storageGateway, state *authoritativeMatchState) error {
	if m.store != nil {
		return nil
	}
	if err := m.openWorldStoreIfPresent(ctx, nk, state); err != nil || m.store != nil {
		return err
	}
	binding, err := state.engine.WorldBinding()
	if err != nil {
		return err
	}
	// Two participant_joined events are the only canonical events permitted
	// before the target deterministic store is first created.
	if binding.NextGlobalEventSequence > 3 {
		return errors.New("target World store is missing after canonical commands already exist")
	}
	initial := worldcommand.MatchState{
		MatchID:                 binding.MatchID,
		ParticipantRosterHash:   bareDigest(binding.RosterRoot),
		MatchVersion:            binding.MatchVersion,
		NextGlobalEventSequence: binding.NextGlobalEventSequence,
		StateRevision:           1,
		StateSchemaID:           m.world.config.stateSchemaID,
		StateCanonicalJSON:      append([]byte(nil), m.world.config.initialStateJSON...),
		StateHash:               m.world.config.initialStateHash,
		Tick:                    m.world.config.initialTick,
		ParticipantSequences:    cloneParticipantSequences(binding.ParticipantSequences),
	}
	backend := &nakamaWorldCommandBackend{nk: nk, logicalMatchID: state.instanceLogicalMatchID}
	store, err := worldcommand.NewStore(ctx, state.instanceLogicalMatchID, backend, worldcommand.WorldTransitionCodec{}, initial)
	if errors.Is(err, worldcommand.ErrVersionConflict) {
		store, err = worldcommand.OpenStore(ctx, state.instanceLogicalMatchID, backend, worldcommand.WorldTransitionCodec{})
	}
	if err != nil {
		return err
	}
	if err := validateWorldStoreAgainstCore(store.State(), binding); err != nil {
		return err
	}
	m.store = store
	return nil
}

func validateWorldStoreAgainstCore(state worldcommand.MatchState, binding matchcore.WorldBinding) error {
	if state.MatchID != binding.MatchID || state.ParticipantRosterHash != bareDigest(binding.RosterRoot) ||
		state.MatchVersion != binding.MatchVersion || state.NextGlobalEventSequence != binding.NextGlobalEventSequence {
		return errors.New("World command store authority cursors differ from the Nakama core")
	}
	if len(state.ParticipantSequences) != len(binding.ParticipantSequences) {
		return errors.New("World command participant cursor set differs from the Nakama core")
	}
	for participant, sequence := range binding.ParticipantSequences {
		if state.ParticipantSequences[participant] != sequence {
			return errors.New("World command participant cursor differs from the Nakama core")
		}
	}
	return nil
}

func cloneParticipantSequences(input map[string]uint64) map[string]uint64 {
	out := make(map[string]uint64, len(input))
	for key, value := range input {
		out[key] = value
	}
	return out
}

func bareDigest(value contract.Digest) string {
	return strings.TrimPrefix(string(value), "sha256:")
}

func (m *worldAuthoritativeMatch) MatchSignal(
	ctx context.Context,
	logger runtime.Logger,
	db *sql.DB,
	nk runtime.NakamaModule,
	dispatcher runtime.MatchDispatcher,
	tick int64,
	rawState interface{},
	data string,
) (interface{}, string) {
	if !m.target {
		return m.authoritativeMatch.MatchSignal(ctx, logger, db, nk, dispatcher, tick, rawState, data)
	}
	state, ok := rawState.(*authoritativeMatchState)
	if !ok || state == nil {
		return nil, `{"error":"invalid match state"}`
	}
	var signal completeSignal
	if err := decodeJSONStrict(data, &signal); err == nil && signal.Action == "complete" {
		if err := m.ensureWorldStore(ctx, nk, state); err != nil {
			return state, errorSignal("World completion store unavailable")
		}
		status := m.store.Status(time.Now().UTC())
		if status.PendingReservations != 0 {
			return state, errorSignal("World completion blocked by pending reservations")
		}
		latest, exists := m.store.LatestAcceptedReceipt()
		if !exists || latest.WorldOutcomeHash == nil || string(signal.Facts.OutcomeHash) != "sha256:"+*latest.WorldOutcomeHash {
			return state, errorSignal("completion outcome_hash is not bound to the latest accepted World transition")
		}
	}
	return m.authoritativeMatch.MatchSignal(ctx, logger, db, nk, dispatcher, tick, state, data)
}
