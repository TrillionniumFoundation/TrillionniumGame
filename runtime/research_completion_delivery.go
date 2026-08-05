package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strings"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	researchCompletionOutboxSchema  = "trnm.nakama.research-session-completion-outbox.v1"
	researchCompletionRequestSchema = "hepta.paper_raid.nakama_completion_ingest.v1"
	researchCompletionPath          = "/v2/hepta/nakama/research-session-completions"
)

type researchCompletionIngestRequest struct {
	Schema         string                              `json:"schema"`
	Completion     researchcontract.SessionCompletedV1 `json:"completion"`
	Archive        []researchcontract.ResearchEvent    `json:"archive"`
	IdempotencyKey string                              `json:"idempotency_key"`
}

type storedResearchCompletionOutbox struct {
	Schema            string                  `json:"schema"`
	CommitmentID      researchcontract.Digest `json:"commitment_id"`
	SessionID         string                  `json:"session_id"`
	TeamID            string                  `json:"team_id"`
	PaperProjectID    string                  `json:"paper_project_id"`
	ChallengeID       string                  `json:"challenge_id"`
	RosterVersion     uint64                  `json:"roster_version"`
	RosterRoot        researchcontract.Digest `json:"roster_root"`
	EventCount        uint64                  `json:"event_count"`
	EventRoot         researchcontract.Digest `json:"event_root"`
	ArchiveHash       researchcontract.Digest `json:"archive_hash"`
	AuthorityKeyID    string                  `json:"authority_key_id"`
	IdempotencyKey    string                  `json:"idempotency_key"`
	RequestBodyBase64 string                  `json:"request_body_base64"`
	RequestSHA256     string                  `json:"request_sha256"`
	ReceiptBodyBase64 string                  `json:"receipt_body_base64,omitempty"`
	ReceiptSHA256     string                  `json:"receipt_sha256,omitempty"`
	DeliveredAtUnix   *int64                  `json:"delivered_at_unix"`
}

type heptaResearchCompletionReceipt = researchcontract.SignedHeptaCompletionReceiptV1

func newStoredResearchCompletionOutbox(completion researchcontract.SessionCompletedV1, archive []researchcontract.ResearchEvent, authorityPublicKey ed25519.PublicKey) (storedResearchCompletionOutbox, error) {
	if err := researchcontract.VerifyCompletionAgainstArchive(completion, archive, authorityPublicKey); err != nil {
		return storedResearchCompletionOutbox{}, err
	}
	commitmentHex := strings.TrimPrefix(string(completion.CommitmentID), "sha256:")
	if len(commitmentHex) != sha256.Size*2 {
		return storedResearchCompletionOutbox{}, errors.New("completion commitment_id is not canonical")
	}
	idempotencyKey := "nakama-research-completion-" + commitmentHex
	request := researchCompletionIngestRequest{
		Schema: researchCompletionRequestSchema, Completion: completion,
		Archive: append([]researchcontract.ResearchEvent(nil), archive...), IdempotencyKey: idempotencyKey,
	}
	body, err := json.Marshal(request)
	if err != nil {
		return storedResearchCompletionOutbox{}, err
	}
	digest := sha256.Sum256(body)
	return storedResearchCompletionOutbox{
		Schema: researchCompletionOutboxSchema, CommitmentID: completion.CommitmentID,
		SessionID: completion.SessionID, TeamID: completion.TeamID, PaperProjectID: completion.PaperProjectID,
		ChallengeID: completion.ChallengeID, RosterVersion: completion.RosterVersion, RosterRoot: completion.RosterRoot,
		EventCount: completion.EventCount, EventRoot: completion.EventRoot, ArchiveHash: completion.ArchiveHash,
		AuthorityKeyID: completion.AuthorityKeyID, IdempotencyKey: idempotencyKey,
		RequestBodyBase64: base64.StdEncoding.EncodeToString(body), RequestSHA256: hex.EncodeToString(digest[:]),
	}, nil
}

func validateStoredResearchCompletionOutbox(sessionID string, outbox *storedResearchCompletionOutbox) error {
	if outbox == nil {
		return nil
	}
	if outbox.Schema != researchCompletionOutboxSchema || outbox.SessionID != sessionID ||
		outbox.RosterVersion == 0 || outbox.EventCount == 0 || outbox.IdempotencyKey == "" {
		return errors.New("stored research completion outbox identity is invalid")
	}
	if err := researchcontract.ValidateTeamID(outbox.TeamID); err != nil {
		return err
	}
	if err := researchcontract.ValidatePaperProjectID(outbox.PaperProjectID); err != nil {
		return err
	}
	if err := researchcontract.ValidateChallengeID(outbox.ChallengeID); err != nil {
		return err
	}
	for _, digest := range []researchcontract.Digest{outbox.CommitmentID, outbox.RosterRoot, outbox.EventRoot, outbox.ArchiveHash} {
		if err := digest.Validate(); err != nil {
			return err
		}
	}
	body, err := base64.StdEncoding.Strict().DecodeString(outbox.RequestBodyBase64)
	if err != nil || base64.StdEncoding.EncodeToString(body) != outbox.RequestBodyBase64 {
		return errors.New("research completion request body is not canonical base64")
	}
	digest := sha256.Sum256(body)
	if outbox.RequestSHA256 != hex.EncodeToString(digest[:]) {
		return errors.New("research completion request body checksum differs")
	}
	var request researchCompletionIngestRequest
	if decodeJSONStrict(string(body), &request) != nil || request.Schema != researchCompletionRequestSchema ||
		request.IdempotencyKey != outbox.IdempotencyKey || request.Completion.CommitmentID != outbox.CommitmentID ||
		request.Completion.SessionID != outbox.SessionID || request.Completion.TeamID != outbox.TeamID ||
		request.Completion.PaperProjectID != outbox.PaperProjectID || request.Completion.ChallengeID != outbox.ChallengeID ||
		request.Completion.RosterVersion != outbox.RosterVersion || request.Completion.RosterRoot != outbox.RosterRoot ||
		request.Completion.EventCount != outbox.EventCount || request.Completion.EventRoot != outbox.EventRoot ||
		request.Completion.ArchiveHash != outbox.ArchiveHash || request.Completion.AuthorityKeyID != outbox.AuthorityKeyID ||
		uint64(len(request.Archive)) != outbox.EventCount {
		return errors.New("research completion request differs from durable outbox identity")
	}
	if outbox.DeliveredAtUnix != nil && *outbox.DeliveredAtUnix < request.Completion.CompletedAtUnix {
		return errors.New("research completion delivery predates completion")
	}
	if outbox.DeliveredAtUnix == nil {
		if outbox.ReceiptBodyBase64 != "" || outbox.ReceiptSHA256 != "" {
			return errors.New("undelivered research completion outbox carries a receipt")
		}
	} else {
		receiptBody, err := base64.StdEncoding.Strict().DecodeString(outbox.ReceiptBodyBase64)
		if err != nil || base64.StdEncoding.EncodeToString(receiptBody) != outbox.ReceiptBodyBase64 {
			return errors.New("research completion receipt body is not canonical base64")
		}
		receiptDigest := sha256.Sum256(receiptBody)
		if outbox.ReceiptSHA256 != hex.EncodeToString(receiptDigest[:]) {
			return errors.New("research completion receipt checksum differs")
		}
		var receipt heptaResearchCompletionReceipt
		if decodeJSONStrict(string(receiptBody), &receipt) != nil {
			return errors.New("stored research completion receipt is invalid JSON")
		}
	}
	return nil
}

func (m *moduleRuntime) deliverPendingResearchCompletion(ctx context.Context, logger runtime.Logger, nk storageGateway, state *researchMatchState) error {
	outbox := state.record.CompletionOutbox
	if outbox == nil || outbox.DeliveredAtUnix != nil {
		return nil
	}
	body, err := base64.StdEncoding.Strict().DecodeString(outbox.RequestBodyBase64)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, m.config.heptaBaseURL+researchCompletionPath, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("content-type", "application/json")
	request.Header.Set("x-hepta-nakama-token", m.config.heptaServiceToken)
	client := m.httpClient
	if client == nil {
		client = newResearchHTTPClient()
	}
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("deliver Hepta research completion: %w", err)
	}
	defer response.Body.Close()
	encoded, err := io.ReadAll(io.LimitReader(response.Body, maximumHeptaResponseBytes+1))
	if err != nil {
		return fmt.Errorf("read Hepta research completion response: %w", err)
	}
	if len(encoded) > maximumHeptaResponseBytes {
		return errors.New("Hepta research completion response exceeds size limit")
	}
	if response.StatusCode != http.StatusCreated {
		return fmt.Errorf("Hepta research completion returned HTTP %d", response.StatusCode)
	}
	if err := requireHeptaJSONResponse(response); err != nil {
		return err
	}
	var receipt heptaResearchCompletionReceipt
	if err := decodeJSONStrict(string(encoded), &receipt); err != nil {
		return fmt.Errorf("decode Hepta research completion receipt: %w", err)
	}
	var durableRequest researchCompletionIngestRequest
	if err := decodeJSONStrict(string(body), &durableRequest); err != nil {
		return err
	}
	if err := validateHeptaResearchCompletionReceipt(*outbox, durableRequest.Completion, receipt, m.config.issuerKeys); err != nil {
		return err
	}
	updated := cloneStoredResearchSession(state.record)
	deliveredAt := time.Now().UTC().Unix()
	if deliveredAt < durableRequest.Completion.CompletedAtUnix {
		deliveredAt = durableRequest.Completion.CompletedAtUnix
	}
	updated.CompletionOutbox.DeliveredAtUnix = &deliveredAt
	receiptDigest := sha256.Sum256(encoded)
	updated.CompletionOutbox.ReceiptBodyBase64 = base64.StdEncoding.EncodeToString(encoded)
	updated.CompletionOutbox.ReceiptSHA256 = hex.EncodeToString(receiptDigest[:])
	version, err := updateStoredResearch(ctx, nk, updated, state.storageVersion)
	if err != nil {
		return fmt.Errorf("persist Hepta research completion delivery: %w", err)
	}
	state.record = updated
	state.storageVersion = version
	logger.Info("research completion delivered: session=%s commitment=%s", outbox.SessionID, outbox.CommitmentID)
	return nil
}

func validateHeptaResearchCompletionReceipt(outbox storedResearchCompletionOutbox, completion researchcontract.SessionCompletedV1, receipt heptaResearchCompletionReceipt, trusted map[string]ed25519.PublicKey) error {
	if receipt.Schema != researchcontract.HeptaCompletionReceiptSchema || receipt.CommitmentID != outbox.CommitmentID ||
		receipt.SessionID != outbox.SessionID || receipt.TeamID != outbox.TeamID ||
		receipt.PaperProjectID != outbox.PaperProjectID || receipt.ChallengeID != outbox.ChallengeID ||
		receipt.RosterVersion != outbox.RosterVersion || receipt.RosterRoot != outbox.RosterRoot ||
		receipt.EventCount != outbox.EventCount || receipt.EventRoot != outbox.EventRoot ||
		receipt.ArchiveHash != outbox.ArchiveHash || receipt.NakamaAuthorityKeyID != outbox.AuthorityKeyID ||
		receipt.RulesetHash != completion.RulesetHash || receipt.ChallengeSnapshotHash != completion.ChallengeSnapshotHash ||
		!reflect.DeepEqual(receipt.TerminalFacts, completion.TerminalFacts) {
		return errors.New("Hepta research completion receipt differs from durable completion")
	}
	if receipt.VerifiedAtUnix < completion.CompletedAtUnix {
		return errors.New("Hepta research completion receipt predates completion")
	}
	if err := receipt.Verify(trusted); err != nil {
		return fmt.Errorf("verify Hepta research completion receipt ACK: %w", err)
	}
	return nil
}
