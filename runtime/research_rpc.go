package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	rpcResearchCreate   = "trnm_research_session_create_v1"
	rpcResearchResume   = "trnm_research_session_resume_v1"
	rpcResearchEvidence = "trnm_research_session_evidence_v1"
	rpcResearchArchive  = "trnm_research_session_archive_v1"
	rpcResearchComplete = "trnm_research_session_complete_v1"
	rpcResearchReplace  = "trnm_research_session_replace_roster_v1"
)

type researchCreateRequest struct {
	Schema         string                                 `json:"schema"`
	OperatorToken  string                                 `json:"operator_token"`
	Authorizations []researchcontract.SignedAuthorization `json:"authorizations"`
}
type researchResumeRequest struct {
	Schema           string `json:"schema"`
	OperatorToken    string `json:"operator_token"`
	LogicalSessionID string `json:"logical_session_id"`
}
type researchEvidenceRequest struct {
	Schema           string `json:"schema"`
	LogicalSessionID string `json:"logical_session_id"`
	AuthorizationID  string `json:"authorization_id,omitempty"`
	OperatorToken    string `json:"operator_token,omitempty"`
}
type researchArchiveRequest struct {
	Schema           string  `json:"schema"`
	LogicalSessionID string  `json:"logical_session_id"`
	AfterSequence    *uint64 `json:"after_sequence"`
	Limit            *uint32 `json:"limit,omitempty"`
	AuthorizationID  *string `json:"authorization_id,omitempty"`
	OperatorToken    *string `json:"operator_token,omitempty"`
}
type researchCompleteRequest struct {
	Schema           string                         `json:"schema"`
	OperatorToken    string                         `json:"operator_token"`
	LogicalSessionID string                         `json:"logical_session_id"`
	Facts            researchcontract.TerminalFacts `json:"facts"`
}
type researchReplaceRequest struct {
	Schema           string                                 `json:"schema"`
	OperatorToken    string                                 `json:"operator_token"`
	LogicalSessionID string                                 `json:"logical_session_id"`
	Authorizations   []researchcontract.SignedAuthorization `json:"authorizations"`
}

type researchRuntimeResponse struct {
	Schema            string                  `json:"schema"`
	LogicalSessionID  string                  `json:"logical_session_id"`
	ExternalMatchID   string                  `json:"external_match_id,omitempty"`
	RuntimeGeneration uint64                  `json:"runtime_generation"`
	Status            researchcore.Status     `json:"status"`
	SessionVersion    uint64                  `json:"session_version"`
	RosterVersion     uint64                  `json:"roster_version"`
	RosterRoot        researchcontract.Digest `json:"roster_root"`
}
type researchEvidenceResponse struct {
	Schema             string                              `json:"schema"`
	LogicalSessionID   string                              `json:"logical_session_id"`
	ExternalMatchID    string                              `json:"external_match_id,omitempty"`
	RuntimeGeneration  uint64                              `json:"runtime_generation"`
	Completion         researchcontract.SessionCompletedV1 `json:"completion"`
	AuthorityPublicKey string                              `json:"authority_public_key_base64"`
}
type researchArchiveResponse struct {
	Schema            string                           `json:"schema"`
	LogicalSessionID  string                           `json:"logical_session_id"`
	ExternalMatchID   string                           `json:"external_match_id,omitempty"`
	RuntimeGeneration uint64                           `json:"runtime_generation"`
	Status            researchcore.Status              `json:"status"`
	SessionVersion    uint64                           `json:"session_version"`
	RosterVersion     uint64                           `json:"roster_version"`
	RosterRoot        researchcontract.Digest          `json:"roster_root"`
	EventCount        uint64                           `json:"event_count"`
	AfterSequence     uint64                           `json:"after_sequence"`
	NextAfterSequence uint64                           `json:"next_after_sequence"`
	HasMore           bool                             `json:"has_more"`
	Events            []researchcontract.ResearchEvent `json:"events"`
	Roster            []researchcontract.RosterEntry   `json:"roster"`
	Participants      []researchcore.ParticipantView   `json:"participants"`
}

func (m *moduleRuntime) rpcResearchCreate(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchCreateRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.research-session.create.v1" || len(request.Authorizations) < researchcontract.MinParticipants || len(request.Authorizations) > researchcontract.MaxParticipants || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid research-session create request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	now := time.Now().UTC()
	engine, err := researchcore.NewSession(researchcore.NewSessionOptions{Authorizations: request.Authorizations, TrustedIssuerKeys: m.config.issuerKeys, AuthorityKeyID: m.config.authorityKeyID, AuthorityPrivateKey: m.config.authorityPrivateKey, Now: now})
	if err != nil {
		return "", runtime.NewError("research authorization snapshot rejected: "+err.Error(), 3)
	}
	snapshot, err := engine.Snapshot()
	if err != nil {
		return "", runtime.NewError("research snapshot encoding failed", 13)
	}
	view := engine.View()
	record, err := newStoredResearch(view.SessionID, snapshot, request.Authorizations, now.Unix())
	if err != nil {
		return "", runtime.NewError(err.Error(), 3)
	}
	record.RuntimeGeneration = 1
	if _, err = createStoredResearch(ctx, nk, record); err != nil {
		return "", runtime.NewError("research session already exists or storage failed", 10)
	}
	external, err := nk.MatchCreate(ctx, registeredResearchMatchName, map[string]interface{}{"logical_session_id": view.SessionID, "runtime_generation": record.RuntimeGeneration})
	if err != nil {
		return "", runtime.NewError("research snapshot persisted but runtime creation failed; use resume", 14)
	}
	return marshalRPC(researchRuntimeFor(record, view, external))
}

func (m *moduleRuntime) rpcResearchResume(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchResumeRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.research-session.resume.v1" || researchcontract.ValidateSessionID(request.LogicalSessionID) != nil || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid research-session resume request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		return "", storedResearchVerificationError(err, "stored research snapshot failed verification")
	}
	if completion, ok := engine.Completion(); ok && !hasPendingResearchDeliveries(stored.record) {
		publicKey, found := engine.CompletionAuthorityPublicKey()
		if !found {
			return "", runtime.NewError("stored research completion authority is unavailable", 13)
		}
		return marshalRPC(researchEvidenceFor(stored.record, *completion, publicKey))
	}
	view := engine.View()
	record, external, err := m.ensureResearchRuntime(ctx, nk, stored)
	if err != nil {
		return "", runtime.NewError(err.Error(), 14)
	}
	return marshalRPC(researchRuntimeFor(record, view, external))
}

func (m *moduleRuntime) rpcResearchEvidence(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchEvidenceRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.research-session.get-evidence.v1" || researchcontract.ValidateSessionID(request.LogicalSessionID) != nil {
		return "", runtime.NewError("invalid research evidence request", 3)
	}
	hasAuth, hasOperator := request.AuthorizationID != "", request.OperatorToken != ""
	if hasAuth == hasOperator || (hasAuth && researchcontract.ValidateAuthorizationID(request.AuthorizationID) != nil) || (hasOperator && !operatorTokenWireValid(request.OperatorToken, false)) {
		return "", runtime.NewError("invalid research evidence access binding", 3)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		return "", storedResearchVerificationError(err, "stored research snapshot failed verification")
	}
	if hasOperator {
		if !m.config.operatorAuthorized(request.OperatorToken) {
			return "", runtime.NewError("research evidence access rejected", 7)
		}
	} else if !researchParticipantCanRead(ctx, engine.View(), request.AuthorizationID) {
		return "", runtime.NewError("research evidence access rejected", 7)
	}
	completion, ok := engine.Completion()
	if !ok {
		return "", runtime.NewError("research session is not completed", 9)
	}
	if hasPendingResearchDeliveries(stored.record) {
		updated, _, ensureErr := m.ensureResearchRuntime(ctx, nk, stored)
		if ensureErr != nil {
			return "", runtime.NewError("completion is durable but Hepta delivery recovery could not start: "+ensureErr.Error(), 14)
		}
		stored.record = updated
	}
	publicKey, found := engine.CompletionAuthorityPublicKey()
	if !found {
		return "", runtime.NewError("stored research completion authority is unavailable", 13)
	}
	return marshalRPC(researchEvidenceFor(stored.record, *completion, publicKey))
}

func (m *moduleRuntime) rpcResearchArchive(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchArchiveRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.research-session.get-archive.v1" || request.AfterSequence == nil || researchcontract.ValidateSessionID(request.LogicalSessionID) != nil {
		return "", runtime.NewError("invalid research archive request", 3)
	}
	hasAuth, hasOperator := request.AuthorizationID != nil, request.OperatorToken != nil
	if hasAuth == hasOperator || (hasAuth && researchcontract.ValidateAuthorizationID(*request.AuthorizationID) != nil) || (hasOperator && !operatorTokenWireValid(*request.OperatorToken, false)) {
		return "", runtime.NewError("invalid research archive access binding", 3)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		return "", storedResearchVerificationError(err, "stored research snapshot failed verification")
	}
	if hasOperator {
		if !m.config.operatorAuthorized(*request.OperatorToken) {
			return "", runtime.NewError("research archive access rejected", 7)
		}
	} else if !researchParticipantCanRead(ctx, engine.View(), *request.AuthorizationID) {
		return "", runtime.NewError("research archive access rejected", 7)
	}
	limit := maximumArchivePageSize
	if request.Limit != nil {
		limit = *request.Limit
	}
	view := engine.View()
	if *request.AfterSequence > view.EventCount || limit == 0 || limit > maximumArchivePageSize {
		return "", runtime.NewError("invalid research archive cursor", 3)
	}
	events := engine.Events()
	start := int(*request.AfterSequence)
	end := start + int(limit)
	if end > len(events) {
		end = len(events)
	}
	page := append([]researchcontract.ResearchEvent(nil), events[start:end]...)
	next := *request.AfterSequence
	if len(page) > 0 {
		next = page[len(page)-1].Sequence
	}
	return marshalRPC(researchArchiveResponse{Schema: "trnm.nakama.research-session.archive.v1", LogicalSessionID: stored.record.LogicalSessionID, ExternalMatchID: stored.record.ExternalMatchID, RuntimeGeneration: stored.record.RuntimeGeneration, Status: view.Status, SessionVersion: view.Version, RosterVersion: view.RosterVersion, RosterRoot: view.RosterRoot, EventCount: view.EventCount, AfterSequence: *request.AfterSequence, NextAfterSequence: next, HasMore: next < view.EventCount, Events: page, Roster: engine.Roster(), Participants: view.Participants})
}

func (m *moduleRuntime) rpcResearchComplete(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchCompleteRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.research-session.complete.v1" || researchcontract.ValidateSessionID(request.LogicalSessionID) != nil || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid research completion request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		return "", storedResearchVerificationError(err, "stored research snapshot failed verification")
	}
	if existing, ok := engine.Completion(); ok {
		if !reflect.DeepEqual(existing.TerminalFacts, request.Facts) {
			return "", runtime.NewError("completion facts conflict", 9)
		}
		publicKey, found := engine.CompletionAuthorityPublicKey()
		if !found {
			return "", runtime.NewError("stored research completion authority is unavailable", 13)
		}
		return marshalRPC(researchEvidenceFor(stored.record, *existing, publicKey))
	}
	if stored.record.ExternalMatchID == "" {
		return "", runtime.NewError("research runtime is absent; resume first", 9)
	}
	signal, _ := json.Marshal(researchSignal{Schema: "trnm.nakama.research-session.signal.v1", Action: "complete", LogicalSessionID: request.LogicalSessionID, RuntimeGeneration: stored.record.RuntimeGeneration, OperatorToken: request.OperatorToken, Facts: &request.Facts})
	response, err := nk.MatchSignal(ctx, stored.record.ExternalMatchID, string(signal))
	if err != nil {
		return "", runtime.NewError("research runtime signal failed", 14)
	}
	if signalError(response) != "" {
		return "", runtime.NewError("research completion rejected: "+signalError(response), 9)
	}
	current, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil || current.version == stored.version {
		return "", runtime.NewError("research completion lacks advanced durable record", 13)
	}
	currentEngine, err := m.restoreStoredResearch(current.record)
	if err != nil {
		return "", storedResearchVerificationError(err, "completed research snapshot failed verification")
	}
	completion, ok := currentEngine.Completion()
	if !ok || !reflect.DeepEqual(completion.TerminalFacts, request.Facts) {
		return "", runtime.NewError("research completion differs from durable request", 13)
	}
	publicKey, found := currentEngine.CompletionAuthorityPublicKey()
	if !found {
		return "", runtime.NewError("stored research completion authority is unavailable", 13)
	}
	return marshalRPC(researchEvidenceFor(current.record, *completion, publicKey))
}

func (m *moduleRuntime) rpcResearchReplace(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchReplaceRequest
	if err := decodeJSONStrict(payload, &request); err != nil || request.Schema != "trnm.nakama.research-session.replace-roster.v1" || researchcontract.ValidateSessionID(request.LogicalSessionID) != nil || len(request.Authorizations) < researchcontract.MinParticipants || len(request.Authorizations) > researchcontract.MaxParticipants || !operatorTokenWireValid(request.OperatorToken, false) {
		return "", runtime.NewError("invalid research replacement request", 3)
	}
	if !m.config.operatorAuthorized(request.OperatorToken) {
		return "", runtime.NewError("operator authorization rejected", 7)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	if stored.record.ExternalMatchID == "" {
		return "", runtime.NewError("research runtime is absent; resume first", 9)
	}
	signal, _ := json.Marshal(researchSignal{Schema: "trnm.nakama.research-session.signal.v1", Action: "replace_roster", LogicalSessionID: request.LogicalSessionID, RuntimeGeneration: stored.record.RuntimeGeneration, OperatorToken: request.OperatorToken, Authorizations: request.Authorizations})
	response, err := nk.MatchSignal(ctx, stored.record.ExternalMatchID, string(signal))
	if err != nil {
		return "", runtime.NewError("research replacement signal failed", 14)
	}
	if message := signalError(response); message != "" {
		return "", runtime.NewError("research replacement rejected: "+message, 9)
	}
	current, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil || current.version == stored.version {
		return "", runtime.NewError("research replacement lacks advanced durable record", 13)
	}
	engine, err := m.restoreStoredResearch(current.record)
	if err != nil {
		return "", storedResearchVerificationError(err, "replaced research snapshot failed verification")
	}
	return marshalRPC(researchRuntimeFor(current.record, engine.View(), current.record.ExternalMatchID))
}

func (m *moduleRuntime) restoreStoredResearch(record storedResearchSession) (*researchcore.Engine, error) {
	snapshot, err := record.snapshot()
	if err != nil {
		return nil, err
	}
	engine, err := researchcore.Restore(snapshot, researchcore.RestoreOptions{TrustedIssuerKeys: m.config.issuerKeys, AuthorityKeyID: m.config.authorityKeyID, AuthorityPrivateKey: m.config.authorityPrivateKey, AuthorityPublicKeys: m.config.authorityPublicKeys})
	if err != nil {
		return nil, err
	}
	if engine.View().SessionID != record.LogicalSessionID {
		return nil, errors.New("signed research snapshot differs from storage identity")
	}
	for index, outbox := range record.ConsumptionOutboxes {
		if outbox.DeliveredAtUnix == nil {
			continue
		}
		receiptBody, err := base64.StdEncoding.Strict().DecodeString(outbox.ReceiptBodyBase64)
		if err != nil {
			return nil, fmt.Errorf("stored Hepta authorization consumption ACK %d cannot be decoded", index)
		}
		var receipt heptaResearchConsumptionReceipt
		if decodeJSONStrict(string(receiptBody), &receipt) != nil {
			return nil, fmt.Errorf("stored Hepta authorization consumption ACK %d is invalid", index)
		}
		if err := validateHeptaResearchConsumptionReceipt(outbox, receipt, m.config.issuerKeys); err != nil {
			return nil, err
		}
	}
	completion, completed := engine.Completion()
	if !completed {
		if record.CompletionOutbox != nil {
			return nil, errors.New("incomplete research snapshot carries a completion outbox")
		}
		return engine, nil
	}
	if record.CompletionOutbox == nil || record.CompletionOutbox.RosterVersion != uint64(len(record.ConsumptionOutboxes)) {
		return nil, errors.New("completed research snapshot lacks an outbox for its latest authorization epoch")
	}
	body, err := base64.StdEncoding.Strict().DecodeString(record.CompletionOutbox.RequestBodyBase64)
	if err != nil {
		return nil, errors.New("completed research request body cannot be decoded")
	}
	var request researchCompletionIngestRequest
	if decodeJSONStrict(string(body), &request) != nil || !reflect.DeepEqual(request.Completion, *completion) || !reflect.DeepEqual(request.Archive, engine.Events()) {
		return nil, errors.New("completion outbox does not carry the exact signed snapshot completion and archive")
	}
	if record.CompletionOutbox.DeliveredAtUnix != nil {
		if hasPendingResearchConsumption(record) {
			return nil, errors.New("completion ACK was persisted before every authorization epoch consumption")
		}
		receiptBody, err := base64.StdEncoding.Strict().DecodeString(record.CompletionOutbox.ReceiptBodyBase64)
		if err != nil {
			return nil, errors.New("stored Hepta completion ACK cannot be decoded")
		}
		var receipt heptaResearchCompletionReceipt
		if decodeJSONStrict(string(receiptBody), &receipt) != nil {
			return nil, errors.New("stored Hepta completion ACK is invalid")
		}
		if err := validateHeptaResearchCompletionReceipt(*record.CompletionOutbox, *completion, receipt, m.config.issuerKeys); err != nil {
			return nil, err
		}
	}
	return engine, nil
}

func storedResearchVerificationError(err error, fallback string) error {
	if errors.Is(err, researchcore.ErrAuthorityVerificationKeyUnavailable) {
		return runtime.NewError("stored research snapshot or completion authority key is missing from the public verification registry", 13)
	}
	return runtime.NewError(fallback, 13)
}

// ensureResearchRuntime returns a live runtime generation. It is also the
// durable callback recovery path for a completed snapshot whose Hepta ACK was
// not yet persisted when Nakama was killed.
func (m *moduleRuntime) ensureResearchRuntime(ctx context.Context, nk runtime.NakamaModule, stored versionedStoredResearch) (storedResearchSession, string, error) {
	if stored.record.ExternalMatchID != "" {
		active, err := nk.MatchGet(ctx, stored.record.ExternalMatchID)
		if err != nil {
			return storedResearchSession{}, "", errors.New("could not inspect research runtime")
		}
		if active != nil {
			return stored.record, stored.record.ExternalMatchID, nil
		}
	}
	updated := cloneStoredResearchSession(stored.record)
	updated.ExternalMatchID = ""
	if updated.RuntimeGeneration >= researchcontract.MaximumJSONSafeInteger {
		return storedResearchSession{}, "", errors.New("research runtime generation exhausted")
	}
	updated.RuntimeGeneration++
	if _, err := updateStoredResearch(ctx, nk, updated, stored.version); err != nil {
		return storedResearchSession{}, "", errors.New("research resume fencing conflict")
	}
	external, err := nk.MatchCreate(ctx, registeredResearchMatchName, map[string]interface{}{"logical_session_id": updated.LogicalSessionID, "runtime_generation": updated.RuntimeGeneration})
	if err != nil {
		return storedResearchSession{}, "", errors.New("research runtime creation failed; retry resume")
	}
	updated.ExternalMatchID = external
	return updated, external, nil
}
func researchRuntimeFor(record storedResearchSession, view researchcore.View, external string) researchRuntimeResponse {
	return researchRuntimeResponse{Schema: "trnm.nakama.research-session.match-runtime.v1", LogicalSessionID: record.LogicalSessionID, ExternalMatchID: external, RuntimeGeneration: record.RuntimeGeneration, Status: view.Status, SessionVersion: view.Version, RosterVersion: view.RosterVersion, RosterRoot: view.RosterRoot}
}
func researchEvidenceFor(record storedResearchSession, completion researchcontract.SessionCompletedV1, key ed25519.PublicKey) researchEvidenceResponse {
	return researchEvidenceResponse{Schema: "trnm.nakama.research-session.evidence.v1", LogicalSessionID: record.LogicalSessionID, ExternalMatchID: record.ExternalMatchID, RuntimeGeneration: record.RuntimeGeneration, Completion: completion, AuthorityPublicKey: base64.StdEncoding.EncodeToString(key)}
}
func researchParticipantCanRead(ctx context.Context, view researchcore.View, authorizationID string) bool {
	userID, _ := ctx.Value(runtime.RUNTIME_CTX_USER_ID).(string)
	for _, p := range view.Participants {
		if p.AuthorizationID != authorizationID {
			continue
		}
		// Nakama authenticates a server-side RPC with the configured HTTP key,
		// but intentionally leaves RUNTIME_CTX_USER_ID empty. The BFF uses that
		// trusted path with the current, player-scoped authorization_id returned
		// by Hepta. A user-session RPC must still bind both identifiers.
		if userID == "" || p.SubjectUserID == userID {
			return true
		}
	}
	return false
}
func signalError(raw string) string {
	var fields map[string]json.RawMessage
	if decodeJSONStrict(raw, &fields) != nil || fields == nil {
		return "invalid signal response"
	}
	encoded, exists := fields["error"]
	if !exists {
		return ""
	}
	if len(fields) != 1 {
		return "invalid mixed signal response"
	}
	var message string
	if json.Unmarshal(encoded, &message) != nil || message == "" {
		return "invalid signal error response"
	}
	return message
}

func verifyResearchEvidence(response researchEvidenceResponse, engine *researchcore.Engine) error {
	key, err := base64.StdEncoding.Strict().DecodeString(response.AuthorityPublicKey)
	expected, ok := engine.CompletionAuthorityPublicKey()
	if err != nil || !ok || !bytes.Equal(key, expected) {
		return errors.New("research evidence authority key differs")
	}
	return researchcontract.VerifyCompletionAgainstArchive(response.Completion, engine.Events(), ed25519.PublicKey(key))
}
