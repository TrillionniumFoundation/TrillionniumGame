package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	matchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/core"
	"github.com/heroiclabs/nakama-common/api"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	rpcCreateMatch = "trnm_match_create_v1"
	rpcResumeMatch = "trnm_match_resume_v1"
	rpcEvidence    = "trnm_match_evidence_v1"
	rpcComplete    = "trnm_match_complete_v1"
	rpcHealth      = "trnm_health_v1"
	rpcReady       = "trnm_ready_v1"
)

type moduleRuntime struct {
	config moduleConfig
}

type createMatchRequest struct {
	Schema         string                         `json:"schema"`
	OperatorToken  string                         `json:"operator_token"`
	Authorizations []contract.SignedAuthorization `json:"authorizations"`
}

type resumeMatchRequest struct {
	Schema         string `json:"schema"`
	OperatorToken  string `json:"operator_token"`
	LogicalMatchID string `json:"logical_match_id"`
}

type evidenceRequest struct {
	Schema          string `json:"schema"`
	LogicalMatchID  string `json:"logical_match_id"`
	AuthorizationID string `json:"authorization_id,omitempty"`
	OperatorToken   string `json:"operator_token,omitempty"`
}

type completeMatchRequest struct {
	Schema         string                 `json:"schema"`
	OperatorToken  string                 `json:"operator_token"`
	LogicalMatchID string                 `json:"logical_match_id"`
	Facts          contract.TerminalFacts `json:"facts"`
}

type matchRPCResponse struct {
	Schema            string           `json:"schema"`
	LogicalMatchID    string           `json:"logical_match_id"`
	ExternalMatchID   string           `json:"external_match_id,omitempty"`
	RuntimeGeneration uint64           `json:"runtime_generation"`
	Status            matchcore.Status `json:"status"`
	MatchVersion      uint64           `json:"match_version"`
}

type evidenceResponse struct {
	Schema             string                    `json:"schema"`
	LogicalMatchID     string                    `json:"logical_match_id"`
	ExternalMatchID    string                    `json:"external_match_id,omitempty"`
	RuntimeGeneration  uint64                    `json:"runtime_generation"`
	Completion         contract.MatchCompletedV1 `json:"completion"`
	AuthorityPublicKey string                    `json:"authority_public_key_base64"`
}

func (m *moduleRuntime) rpcCreateMatch(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.config.ready(); err != nil {
		return "", runtime.NewError("authoritative runtime is not ready", 14)
	}
	var request createMatchRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.create-match.v1" ||
		len(request.Authorizations) != 2 || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid create-match request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	var authorizations [2]contract.SignedAuthorization
	copy(authorizations[:], request.Authorizations)
	engine, err := matchcore.NewMatch(matchcore.NewMatchOptions{
		Authorizations:      authorizations,
		TrustedIssuerKeys:   m.config.issuerKeys,
		AuthorityKeyID:      m.config.authorityKeyID,
		AuthorityPrivateKey: m.config.authorityPrivateKey,
		Now:                 time.Now().UTC(),
	})
	if err != nil {
		return "", runtime.NewError("authorization snapshot rejected: "+err.Error(), 3)
	}
	view := engine.View()
	snapshot, err := engine.Snapshot()
	if err != nil {
		return "", runtime.NewError("could not encode initial match snapshot", 13)
	}
	record, err := newStoredMatch(view.MatchID, snapshot)
	if err != nil {
		return "", runtime.NewError(err.Error(), 3)
	}
	record.RuntimeGeneration = 1
	if _, err := createStoredMatch(ctx, nk, record); err != nil {
		if strings.Contains(err.Error(), "version conflict") {
			return "", runtime.NewError("logical match already exists; use resume", 10)
		}
		return "", runtime.NewError("could not persist initial match snapshot", 13)
	}
	externalMatchID, err := nk.MatchCreate(ctx, registeredMatchName, map[string]interface{}{
		"logical_match_id":   view.MatchID,
		"runtime_generation": record.RuntimeGeneration,
	})
	if err != nil {
		// The durable record is intentionally retained. An operator can repair a
		// transient runtime creation failure through the resume RPC.
		return "", runtime.NewError("snapshot persisted but match runtime creation failed; use resume", 14)
	}
	return marshalRPC(matchRPCResponse{
		Schema: "trnm.nakama.match-runtime.v1", LogicalMatchID: view.MatchID,
		ExternalMatchID: externalMatchID, RuntimeGeneration: record.RuntimeGeneration,
		Status: view.Status, MatchVersion: view.Version,
	})
}

func (m *moduleRuntime) rpcResumeMatch(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.config.ready(); err != nil {
		return "", runtime.NewError("authoritative runtime is not ready", 14)
	}
	var request resumeMatchRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.resume-match.v1" ||
		contract.ValidateLogicalMatchID(request.LogicalMatchID) != nil || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid resume-match request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	stored, err := loadStoredMatch(ctx, nk, request.LogicalMatchID)
	if err != nil {
		return "", runtime.NewError("logical match not found", 5)
	}
	engine, err := m.restoreStoredEngine(stored.record)
	if err != nil {
		return "", runtime.NewError("stored match snapshot failed verification", 13)
	}
	view := engine.View()
	if completion, completed := engine.Completion(); completed {
		return marshalRPC(evidenceResponseFor(stored.record, *completion, engine.AuthorityPublicKey()))
	}
	if stored.record.ExternalMatchID != "" {
		active, lookupErr := nk.MatchGet(ctx, stored.record.ExternalMatchID)
		if lookupErr != nil {
			return "", runtime.NewError("could not inspect current match runtime", 14)
		}
		if active != nil {
			return marshalRPC(matchRPCResponse{
				Schema: "trnm.nakama.match-runtime.v1", LogicalMatchID: view.MatchID,
				ExternalMatchID: stored.record.ExternalMatchID, RuntimeGeneration: stored.record.RuntimeGeneration,
				Status: view.Status, MatchVersion: view.Version,
			})
		}
	}
	stored.record.ExternalMatchID = ""
	stored.record.RuntimeGeneration++
	if stored.record.RuntimeGeneration == 0 {
		return "", runtime.NewError("runtime generation exhausted", 13)
	}
	if _, err := updateStoredMatch(ctx, nk, stored.record, stored.version); err != nil {
		return "", runtime.NewError("resume fencing conflict", 10)
	}
	externalMatchID, err := nk.MatchCreate(ctx, registeredMatchName, map[string]interface{}{
		"logical_match_id":   stored.record.LogicalMatchID,
		"runtime_generation": stored.record.RuntimeGeneration,
	})
	if err != nil {
		return "", runtime.NewError("resume snapshot fenced but runtime creation failed; retry resume", 14)
	}
	return marshalRPC(matchRPCResponse{
		Schema: "trnm.nakama.match-runtime.v1", LogicalMatchID: view.MatchID,
		ExternalMatchID: externalMatchID, RuntimeGeneration: stored.record.RuntimeGeneration,
		Status: view.Status, MatchVersion: view.Version,
	})
}

func (m *moduleRuntime) rpcEvidence(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.config.ready(); err != nil {
		return "", runtime.NewError("authoritative runtime is not ready", 14)
	}
	var request evidenceRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.get-evidence.v1" ||
		contract.ValidateLogicalMatchID(request.LogicalMatchID) != nil || !operatorTokenWireValid(request.OperatorToken, true) {
		return "", runtime.NewError("invalid evidence request", 3)
	}
	stored, err := loadStoredMatch(ctx, nk, request.LogicalMatchID)
	if err != nil {
		return "", runtime.NewError("logical match not found", 5)
	}
	engine, err := m.restoreStoredEngine(stored.record)
	if err != nil {
		return "", runtime.NewError("stored match snapshot failed verification", 13)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) && !participantCanReadEvidence(ctx, engine.View(), request.AuthorizationID) {
		return "", runtime.NewError("evidence access rejected", 7)
	}
	completion, completed := engine.Completion()
	if !completed {
		return "", runtime.NewError("match is not completed", 9)
	}
	return marshalRPC(evidenceResponseFor(stored.record, *completion, engine.AuthorityPublicKey()))
}

func (m *moduleRuntime) rpcComplete(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.config.ready(); err != nil {
		return "", runtime.NewError("authoritative runtime is not ready", 14)
	}
	var request completeMatchRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.complete-match.v1" ||
		contract.ValidateLogicalMatchID(request.LogicalMatchID) != nil || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid complete-match request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	stored, err := loadStoredMatch(ctx, nk, request.LogicalMatchID)
	if err != nil {
		return "", runtime.NewError("logical match not found", 5)
	}
	engine, err := m.restoreStoredEngine(stored.record)
	if err != nil {
		return "", runtime.NewError("stored match snapshot failed verification", 13)
	}
	if _, completed := engine.Completion(); completed {
		response, err := completedEvidenceForFacts(stored.record, engine, request.Facts)
		if err != nil {
			return "", runtime.NewError("authoritative completion rejected: "+err.Error(), 9)
		}
		return marshalRPC(response)
	}
	if stored.record.ExternalMatchID == "" {
		return "", runtime.NewError("match runtime is absent; resume before completion", 9)
	}
	signal, _ := json.Marshal(completeSignalFor(stored.record, request))
	response, err := nk.MatchSignal(ctx, stored.record.ExternalMatchID, string(signal))
	if err != nil {
		return "", runtime.NewError("match runtime signal failed; resume may be required", 14)
	}
	evidence, remoteError, err := decodeCompleteSignalResponse(response)
	if err != nil {
		return "", runtime.NewError("match runtime returned an invalid completion response", 13)
	}
	if remoteError != "" {
		return "", runtime.NewError("authoritative completion rejected: "+remoteError, 9)
	}
	current, err := loadStoredMatch(ctx, nk, request.LogicalMatchID)
	if err != nil || current.version == stored.version {
		return "", runtime.NewError("completion response was not backed by an advanced durable record", 13)
	}
	currentEngine, err := m.restoreStoredEngine(current.record)
	if err != nil {
		return "", runtime.NewError("completed durable snapshot failed verification", 13)
	}
	if err := validateCompletionSignalEvidence(evidence, current.record, currentEngine, request.Facts, stored.record.RuntimeGeneration); err != nil {
		return "", runtime.NewError("completion response does not match durable evidence", 13)
	}
	return marshalRPC(evidence)
}

func (m *moduleRuntime) restoreStoredEngine(record storedMatch) (*matchcore.Engine, error) {
	snapshot, err := record.snapshot()
	if err != nil {
		return nil, err
	}
	return m.restoreEngineForRecord(record, snapshot)
}

func (m *moduleRuntime) restoreEngineForRecord(record storedMatch, snapshot []byte) (*matchcore.Engine, error) {
	engine, err := matchcore.Restore(snapshot, matchcore.RestoreOptions{
		TrustedIssuerKeys: m.config.issuerKeys, AuthorityKeyID: m.config.authorityKeyID,
		AuthorityPrivateKey: m.config.authorityPrivateKey,
	})
	if err != nil {
		return nil, err
	}
	if engine.View().MatchID != record.LogicalMatchID {
		return nil, errors.New("signed snapshot match id does not match its outer storage record")
	}
	return engine, nil
}

func completedEvidenceForFacts(record storedMatch, engine *matchcore.Engine, facts contract.TerminalFacts) (evidenceResponse, error) {
	completion, completed := engine.Completion()
	if !completed || completion == nil {
		return evidenceResponse{}, errors.New("match is not completed")
	}
	validated, err := engine.Complete(facts, time.Unix(completion.CompletedAtUnix, 0).UTC())
	if err != nil {
		return evidenceResponse{}, err
	}
	return evidenceResponseFor(record, validated, engine.AuthorityPublicKey()), nil
}

func completeSignalFor(record storedMatch, request completeMatchRequest) completeSignal {
	return completeSignal{
		Schema: "trnm.nakama.match-signal.v1", Action: "complete", LogicalMatchID: record.LogicalMatchID,
		RuntimeGeneration: record.RuntimeGeneration, OperatorToken: request.OperatorToken, Facts: request.Facts,
	}
}

func decodeCompleteSignalResponse(raw string) (evidenceResponse, string, error) {
	var fields map[string]json.RawMessage
	if err := decodeJSONStrict(raw, &fields); err != nil || fields == nil {
		return evidenceResponse{}, "", errors.New("completion response is not one JSON object")
	}
	if encodedError, hasError := fields["error"]; hasError {
		if len(fields) != 1 {
			return evidenceResponse{}, "", errors.New("completion response mixes error and success fields")
		}
		var message string
		if err := json.Unmarshal(encodedError, &message); err != nil || message == "" {
			return evidenceResponse{}, "", errors.New("completion error envelope is invalid")
		}
		return evidenceResponse{}, message, nil
	}
	allowed := map[string]bool{
		"schema": true, "logical_match_id": true, "external_match_id": true,
		"runtime_generation": true, "completion": true, "authority_public_key_base64": true,
	}
	for name := range fields {
		if !allowed[name] {
			return evidenceResponse{}, "", fmt.Errorf("completion evidence contains unknown field %q", name)
		}
	}
	for _, required := range []string{"schema", "logical_match_id", "runtime_generation", "completion", "authority_public_key_base64"} {
		if _, present := fields[required]; !present {
			return evidenceResponse{}, "", fmt.Errorf("completion evidence is missing %q", required)
		}
	}
	var evidence evidenceResponse
	if err := decodeJSONStrict(raw, &evidence); err != nil {
		return evidenceResponse{}, "", err
	}
	return evidence, "", nil
}

func validateCompletionSignalEvidence(evidence evidenceResponse, record storedMatch, engine *matchcore.Engine, expectedFacts contract.TerminalFacts, signaledGeneration uint64) error {
	if evidence.Schema != "trnm.nakama.evidence.v1" || evidence.LogicalMatchID != record.LogicalMatchID ||
		evidence.ExternalMatchID != record.ExternalMatchID || evidence.RuntimeGeneration != record.RuntimeGeneration ||
		record.RuntimeGeneration != signaledGeneration {
		return errors.New("completion evidence outer identity or generation is inconsistent")
	}
	publicKey, err := base64.StdEncoding.DecodeString(evidence.AuthorityPublicKey)
	if err != nil || len(publicKey) != ed25519.PublicKeySize || !bytes.Equal(publicKey, engine.AuthorityPublicKey()) {
		return errors.New("completion evidence authority key is inconsistent")
	}
	if err := contract.VerifyCompletion(evidence.Completion, ed25519.PublicKey(publicKey)); err != nil {
		return fmt.Errorf("completion evidence signature is invalid: %w", err)
	}
	persisted, completed := engine.Completion()
	if !completed || persisted == nil || !reflect.DeepEqual(evidence.Completion, *persisted) {
		return errors.New("completion evidence does not equal the signed durable completion")
	}
	expectedBytes, err := expectedFacts.CanonicalBytes()
	if err != nil {
		return err
	}
	actualBytes, err := evidence.Completion.TerminalFacts.CanonicalBytes()
	if err != nil || !bytes.Equal(actualBytes, expectedBytes) {
		return errors.New("completion evidence terminal facts differ from the request")
	}
	return nil
}

func operatorTokenWireValid(token string, optional bool) bool {
	if optional && token == "" {
		return true
	}
	return len(token) >= minimumOperatorLength && len(token) <= maximumOperatorLength
}

func participantCanReadEvidence(ctx context.Context, view matchcore.View, authorizationID string) bool {
	userID, _ := ctx.Value(runtime.RUNTIME_CTX_USER_ID).(string)
	if userID == "" || authorizationID == "" {
		return false
	}
	for _, participant := range view.Participants {
		if participant.SubjectUserID == userID && participant.AuthorizationID == authorizationID {
			return true
		}
	}
	return false
}

func evidenceResponseFor(record storedMatch, completion contract.MatchCompletedV1, publicKey ed25519.PublicKey) evidenceResponse {
	return evidenceResponse{
		Schema: "trnm.nakama.evidence.v1", LogicalMatchID: record.LogicalMatchID,
		ExternalMatchID: record.ExternalMatchID, RuntimeGeneration: record.RuntimeGeneration,
		Completion: completion, AuthorityPublicKey: base64.StdEncoding.EncodeToString(publicKey),
	}
}

func evidenceResponseFrom(state *authoritativeMatchState, completion contract.MatchCompletedV1) evidenceResponse {
	return evidenceResponseFor(state.record, completion, state.engine.AuthorityPublicKey())
}

type healthResponse struct {
	Schema  string `json:"schema"`
	Healthy bool   `json:"healthy"`
	Module  string `json:"module"`
	Version string `json:"nakama_version,omitempty"`
}

type readinessChecks struct {
	Configuration string `json:"configuration"`
	Database      string `json:"database"`
	Storage       string `json:"storage"`
}

type readinessResponse struct {
	Schema string          `json:"schema"`
	Ready  bool            `json:"ready"`
	Checks readinessChecks `json:"checks"`
	Reason string          `json:"reason,omitempty"`
}

func (m *moduleRuntime) rpcHealth(ctx context.Context, _ runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, _ string) (string, error) {
	version, _ := ctx.Value(runtime.RUNTIME_CTX_VERSION).(string)
	return marshalRPC(healthResponse{
		Schema:  "trnm.nakama.health.v1",
		Healthy: true,
		Module:  registeredMatchName,
		Version: version,
	})
}

// rpcReady intentionally returns a structured response with no transport-level
// error when a check fails. Operations gates can therefore distinguish a live,
// unconfigured process from an unreachable one without parsing error strings.
func (m *moduleRuntime) rpcReady(ctx context.Context, _ runtime.Logger, db *sql.DB, nk runtime.NakamaModule, _ string) (string, error) {
	response := readinessResponse{
		Schema: "trnm.nakama.readiness.v1",
		Ready:  true,
		Checks: readinessChecks{Configuration: "ok", Database: "ok", Storage: "ok"},
	}
	reasons := make([]string, 0, 3)

	if err := m.config.ready(); err != nil {
		response.Ready = false
		response.Checks.Configuration = "error"
		reasons = append(reasons, err.Error())
	}
	if db == nil {
		response.Ready = false
		response.Checks.Database = "error"
		reasons = append(reasons, "database handle is absent")
	} else if err := db.PingContext(ctx); err != nil {
		response.Ready = false
		response.Checks.Database = "error"
		reasons = append(reasons, "database ping failed")
	}
	if nk == nil {
		response.Ready = false
		response.Checks.Storage = "error"
		reasons = append(reasons, "Nakama runtime module is absent")
	} else if err := probeWritableStorage(ctx, nk); err != nil {
		response.Ready = false
		response.Checks.Storage = "error"
		reasons = append(reasons, "server-owned storage write probe failed")
	}

	response.Reason = strings.Join(reasons, "; ")
	return marshalRPC(response)
}

type readinessStorage interface {
	StorageWrite(context.Context, []*runtime.StorageWrite) ([]*api.StorageObjectAck, error)
	StorageDelete(context.Context, []*runtime.StorageDelete) error
}

func probeWritableStorage(ctx context.Context, nk readinessStorage) error {
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return err
	}
	key := "_readiness_probe:" + hex.EncodeToString(nonce[:])
	acks, err := nk.StorageWrite(ctx, []*runtime.StorageWrite{{
		Collection:      matchStorageCollection,
		Key:             key,
		UserID:          "",
		Value:           `{"schema":"trnm.nakama.readiness-probe.v1"}`,
		Version:         "*",
		PermissionRead:  0,
		PermissionWrite: 0,
	}})
	if err != nil || len(acks) != 1 || acks[0].Version == "" {
		if err != nil {
			return err
		}
		return errors.New("readiness storage write returned no version")
	}
	if err := nk.StorageDelete(ctx, []*runtime.StorageDelete{{
		Collection: matchStorageCollection,
		Key:        key,
		UserID:     "",
		Version:    acks[0].Version,
	}}); err != nil {
		return err
	}
	return nil
}

func marshalRPC(value any) (string, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", runtime.NewError("internal response encoding failure", 13)
	}
	return string(encoded), nil
}
