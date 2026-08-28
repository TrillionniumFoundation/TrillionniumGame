package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldcommand"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	rpcWorldCommandReady  = "trnm_world_command_ready_v1"
	rpcWorldCommandStatus = "trnm_world_command_status_v1"
	rpcWorldCommandAbort  = "trnm_world_command_abort_v1"

	// All operator RPCs remain source-level fail closed. These exact wire-floor
	// markers are also consumed by the independent authority boundary gate:
	// cutover_authorized": false
	// public_online_enabled": false
	// public_player_market_enabled": false
	operatorPromotionAuthorized = false
)

type worldCommandRPC struct {
	module *moduleRuntime
	world  *worldCommandRuntime
}

type worldCommandStatusRequest struct {
	Schema         string `json:"schema"`
	OperatorToken  string `json:"operator_token"`
	LogicalMatchID string `json:"logical_match_id"`
}

type worldCommandAbortRequest struct {
	Schema          string `json:"schema"`
	OperatorToken   string `json:"operator_token"`
	LogicalMatchID  string `json:"logical_match_id"`
	ClientCommandID string `json:"client_command_id"`
	Generation      uint64 `json:"generation"`
	Reason          string `json:"reason"`
}

func (r *worldCommandRPC) ready(
	_ context.Context,
	_ runtime.Logger,
	_ *sql.DB,
	_ runtime.NakamaModule,
	_ string,
) (string, error) {
	ready := r != nil && r.world != nil && r.world.config.ready() == nil && r.world.initErr == nil
	response := map[string]any{
		"schema":                       "trnm.game.world-command-ready.v1",
		"profile":                      worldProfileLegacy,
		"ready":                        ready,
		"external_execution_under_lock": false,
		"cutover_authorized":           operatorPromotionAuthorized,
		"public_online_enabled":        operatorPromotionAuthorized,
		"public_player_market_enabled": operatorPromotionAuthorized,
	}
	if r != nil && r.world != nil {
		response["profile"] = r.world.config.profile
		if r.world.config.endpoint != nil {
			response["endpoint_host"] = r.world.config.endpoint.Host
			response["endpoint_path"] = r.world.config.endpoint.Path
		}
		if r.world.initErr != nil {
			response["error"] = r.world.initErr.Error()
		} else if err := r.world.config.ready(); err != nil {
			response["error"] = err.Error()
		}
	}
	encoded, err := json.Marshal(response)
	return string(encoded), err
}

func (r *worldCommandRPC) status(
	ctx context.Context,
	_ runtime.Logger,
	_ *sql.DB,
	nk runtime.NakamaModule,
	payload string,
) (string, error) {
	var request worldCommandStatusRequest
	if r == nil || r.module == nil || r.world == nil || decodeJSONStrict(payload, &request) != nil ||
		request.Schema != "trnm.game.world-command-status-request.v1" ||
		contract.ValidateLogicalMatchID(request.LogicalMatchID) != nil || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid World command status request", 3)
	}
	if !r.module.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	backend := &nakamaWorldCommandBackend{nk: nk, logicalMatchID: request.LogicalMatchID}
	store, err := worldcommand.OpenStore(ctx, request.LogicalMatchID, backend, worldcommand.WorldTransitionCodec{})
	if err != nil {
		return "", runtime.NewError("World command store not found or invalid", 5)
	}
	status := store.Status(time.Now().UTC())
	response := map[string]any{
		"schema":                       "trnm.game.world-command-status-response.v1",
		"logical_match_id":             request.LogicalMatchID,
		"status":                       status,
		"cutover_authorized":           operatorPromotionAuthorized,
		"public_online_enabled":        operatorPromotionAuthorized,
		"public_player_market_enabled": operatorPromotionAuthorized,
	}
	if latest, exists := store.LatestAcceptedReceipt(); exists {
		response["latest_accepted_receipt"] = map[string]any{
			"client_command_id":     latest.ClientCommandID,
			"generation":            latest.Generation,
			"event_sequence":        latest.EventSequence,
			"match_version":         latest.MatchVersion,
			"state_revision":        latest.StateRevision,
			"state_hash":            latest.StateHash,
			"tick":                  latest.Tick,
			"request_hash":          latest.RequestHash,
			"transition_id":         latest.TransitionID,
			"world_outcome_hash":    latest.WorldOutcomeHash,
			"world_transition_hash": latest.WorldTransitionHash,
		}
	}
	encoded, err := json.Marshal(response)
	return string(encoded), err
}

func (r *worldCommandRPC) abort(
	ctx context.Context,
	_ runtime.Logger,
	_ *sql.DB,
	nk runtime.NakamaModule,
	payload string,
) (string, error) {
	var request worldCommandAbortRequest
	if r == nil || r.module == nil || r.world == nil || decodeJSONStrict(payload, &request) != nil ||
		request.Schema != "trnm.game.world-command-abort-request.v1" ||
		contract.ValidateLogicalMatchID(request.LogicalMatchID) != nil || request.ClientCommandID == "" ||
		request.Generation == 0 || request.Reason == "" || len(request.Reason) > 256 ||
		!operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid World command abort request", 3)
	}
	if !r.module.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	backend := &nakamaWorldCommandBackend{nk: nk, logicalMatchID: request.LogicalMatchID}
	store, err := worldcommand.OpenStore(ctx, request.LogicalMatchID, backend, worldcommand.WorldTransitionCodec{})
	if err != nil {
		return "", runtime.NewError("World command store not found or invalid", 5)
	}
	if err := store.Abort(ctx, request.ClientCommandID, request.Generation, request.Reason, time.Now().UTC()); err != nil {
		if errors.Is(err, worldcommand.ErrReservationAbsent) {
			return "", runtime.NewError("World command reservation not found", 5)
		}
		if errors.Is(err, worldcommand.ErrStaleReservation) {
			return "", runtime.NewError("World command reservation generation is stale", 10)
		}
		return "", runtime.NewError("World command abort failed", 13)
	}
	encoded, err := json.Marshal(map[string]any{
		"schema":                       "trnm.game.world-command-abort-response.v1",
		"logical_match_id":             request.LogicalMatchID,
		"client_command_id":            request.ClientCommandID,
		"generation":                   request.Generation,
		"status":                       "retired",
		"cutover_authorized":           operatorPromotionAuthorized,
		"public_online_enabled":        operatorPromotionAuthorized,
		"public_player_market_enabled": operatorPromotionAuthorized,
	})
	return string(encoded), err
}
