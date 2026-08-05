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
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	researchConsumptionOutboxSchema  = "trnm.nakama.research-session-consumption-outbox.v1"
	researchConsumptionRequestSchema = "hepta.paper_raid.research_session_consumption.v1"
	researchConsumptionPath          = "/v2/hepta/nakama/research-session-authorizations/consumed"
	maximumHeptaResponseBytes        = 2 * 1024 * 1024
)

type researchHTTPClient interface {
	Do(*http.Request) (*http.Response, error)
}

type researchConsumptionRequest struct {
	Schema           string                  `json:"schema"`
	SessionID        string                  `json:"session_id"`
	RosterVersion    uint64                  `json:"roster_version"`
	RosterRoot       researchcontract.Digest `json:"roster_root"`
	AuthorizationIDs []string                `json:"authorization_ids"`
	ConsumedAtUnix   int64                   `json:"consumed_at_unix"`
	IdempotencyKey   string                  `json:"idempotency_key"`
}

type storedResearchConsumptionOutbox struct {
	Schema            string                  `json:"schema"`
	SessionID         string                  `json:"session_id"`
	TeamID            string                  `json:"team_id"`
	PaperProjectID    string                  `json:"paper_project_id"`
	ChallengeID       string                  `json:"challenge_id"`
	RosterVersion     uint64                  `json:"roster_version"`
	RosterRoot        researchcontract.Digest `json:"roster_root"`
	AuthorizationIDs  []string                `json:"authorization_ids"`
	IdempotencyKey    string                  `json:"idempotency_key"`
	RequestBodyBase64 string                  `json:"request_body_base64"`
	RequestSHA256     string                  `json:"request_sha256"`
	ReceiptBodyBase64 string                  `json:"receipt_body_base64,omitempty"`
	ReceiptSHA256     string                  `json:"receipt_sha256,omitempty"`
	ConsumedAtUnix    int64                   `json:"consumed_at_unix"`
	DeliveredAtUnix   *int64                  `json:"delivered_at_unix"`
}

type heptaResearchConsumptionReceipt = researchcontract.SignedAuthorizationSetConsumptionReceiptV1

func newStoredResearchConsumptionOutbox(authorizations []researchcontract.SignedAuthorization, consumedAtUnix int64) (storedResearchConsumptionOutbox, error) {
	if len(authorizations) < researchcontract.MinParticipants || len(authorizations) > researchcontract.MaxParticipants || consumedAtUnix < 0 {
		return storedResearchConsumptionOutbox{}, errors.New("research consumption outbox has invalid authorization count or time")
	}
	first := authorizations[0].Claim
	authorizationIDs := make([]string, len(authorizations))
	for index, authorization := range authorizations {
		claim := authorization.Claim
		if claim.ParticipantSlot != uint32(index+1) || claim.SessionID != first.SessionID ||
			claim.TeamID != first.TeamID || claim.PaperProjectID != first.PaperProjectID ||
			claim.ChallengeID != first.ChallengeID || claim.RosterVersion != first.RosterVersion ||
			claim.RosterRoot != first.RosterRoot {
			return storedResearchConsumptionOutbox{}, errors.New("research consumption outbox requires one ordered authorization epoch")
		}
		if err := researchcontract.ValidateAuthorizationID(claim.AuthorizationID); err != nil {
			return storedResearchConsumptionOutbox{}, err
		}
		authorizationIDs[index] = claim.AuthorizationID
	}
	seed, err := json.Marshal(struct {
		SessionID        string                  `json:"session_id"`
		RosterVersion    uint64                  `json:"roster_version"`
		RosterRoot       researchcontract.Digest `json:"roster_root"`
		AuthorizationIDs []string                `json:"authorization_ids"`
	}{first.SessionID, first.RosterVersion, first.RosterRoot, authorizationIDs})
	if err != nil {
		return storedResearchConsumptionOutbox{}, err
	}
	idempotencyDigest := sha256.Sum256(seed)
	idempotencyKey := "nakama-research-consume-" + hex.EncodeToString(idempotencyDigest[:])
	request := researchConsumptionRequest{
		Schema: researchConsumptionRequestSchema, SessionID: first.SessionID,
		RosterVersion: first.RosterVersion, RosterRoot: first.RosterRoot,
		AuthorizationIDs: authorizationIDs, ConsumedAtUnix: consumedAtUnix,
		IdempotencyKey: idempotencyKey,
	}
	body, err := json.Marshal(request)
	if err != nil {
		return storedResearchConsumptionOutbox{}, err
	}
	bodyDigest := sha256.Sum256(body)
	return storedResearchConsumptionOutbox{
		Schema: researchConsumptionOutboxSchema, SessionID: first.SessionID,
		TeamID: first.TeamID, PaperProjectID: first.PaperProjectID, ChallengeID: first.ChallengeID,
		RosterVersion: first.RosterVersion, RosterRoot: first.RosterRoot,
		AuthorizationIDs: append([]string(nil), authorizationIDs...), IdempotencyKey: idempotencyKey,
		RequestBodyBase64: base64.StdEncoding.EncodeToString(body), RequestSHA256: hex.EncodeToString(bodyDigest[:]),
		ConsumedAtUnix: consumedAtUnix,
	}, nil
}

func validateStoredResearchConsumptionOutboxes(sessionID string, outboxes []storedResearchConsumptionOutbox) error {
	if len(outboxes) == 0 {
		return errors.New("stored research session has no authorization consumption outbox")
	}
	seenVersions := make(map[uint64]struct{}, len(outboxes))
	for index := range outboxes {
		outbox := outboxes[index]
		if outbox.Schema != researchConsumptionOutboxSchema || outbox.SessionID != sessionID ||
			outbox.RosterVersion != uint64(index+1) || outbox.ConsumedAtUnix < 0 ||
			len(outbox.AuthorizationIDs) < researchcontract.MinParticipants || len(outbox.AuthorizationIDs) > researchcontract.MaxParticipants {
			return fmt.Errorf("research consumption outbox %d has invalid identity, epoch, count, or time", index)
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
		if _, duplicate := seenVersions[outbox.RosterVersion]; duplicate {
			return errors.New("research consumption outbox duplicates roster_version")
		}
		seenVersions[outbox.RosterVersion] = struct{}{}
		if err := outbox.RosterRoot.Validate(); err != nil {
			return err
		}
		seenAuthorization := map[string]struct{}{}
		for _, authorizationID := range outbox.AuthorizationIDs {
			if err := researchcontract.ValidateAuthorizationID(authorizationID); err != nil {
				return err
			}
			if _, duplicate := seenAuthorization[authorizationID]; duplicate {
				return errors.New("research consumption outbox duplicates authorization_id")
			}
			seenAuthorization[authorizationID] = struct{}{}
		}
		body, err := base64.StdEncoding.Strict().DecodeString(outbox.RequestBodyBase64)
		if err != nil || base64.StdEncoding.EncodeToString(body) != outbox.RequestBodyBase64 {
			return errors.New("research consumption request body is not canonical base64")
		}
		digest := sha256.Sum256(body)
		if outbox.RequestSHA256 != hex.EncodeToString(digest[:]) {
			return errors.New("research consumption request body checksum differs")
		}
		var request researchConsumptionRequest
		if decodeJSONStrict(string(body), &request) != nil || request.Schema != researchConsumptionRequestSchema ||
			request.SessionID != outbox.SessionID || request.RosterVersion != outbox.RosterVersion ||
			request.RosterRoot != outbox.RosterRoot || request.ConsumedAtUnix != outbox.ConsumedAtUnix ||
			request.IdempotencyKey != outbox.IdempotencyKey || !equalStringSlices(request.AuthorizationIDs, outbox.AuthorizationIDs) {
			return errors.New("research consumption request differs from durable outbox identity")
		}
		if outbox.DeliveredAtUnix != nil && *outbox.DeliveredAtUnix < outbox.ConsumedAtUnix {
			return errors.New("research consumption delivery predates local consumption")
		}
		if outbox.DeliveredAtUnix == nil {
			if outbox.ReceiptBodyBase64 != "" || outbox.ReceiptSHA256 != "" {
				return errors.New("undelivered research consumption outbox carries a receipt")
			}
		} else {
			receiptBody, err := base64.StdEncoding.Strict().DecodeString(outbox.ReceiptBodyBase64)
			if err != nil || base64.StdEncoding.EncodeToString(receiptBody) != outbox.ReceiptBodyBase64 {
				return errors.New("research consumption receipt body is not canonical base64")
			}
			receiptDigest := sha256.Sum256(receiptBody)
			if outbox.ReceiptSHA256 != hex.EncodeToString(receiptDigest[:]) {
				return errors.New("research consumption receipt body checksum differs")
			}
			var receipt heptaResearchConsumptionReceipt
			if decodeJSONStrict(string(receiptBody), &receipt) != nil {
				return errors.New("stored research consumption receipt is invalid JSON")
			}
		}
	}
	return nil
}

func (m *moduleRuntime) deliverPendingResearchConsumption(ctx context.Context, logger runtime.Logger, nk storageGateway, state *researchMatchState) error {
	index := -1
	for candidate := range state.record.ConsumptionOutboxes {
		if state.record.ConsumptionOutboxes[candidate].DeliveredAtUnix == nil {
			index = candidate
			break
		}
	}
	if index == -1 {
		return nil
	}
	outbox := state.record.ConsumptionOutboxes[index]
	body, err := base64.StdEncoding.Strict().DecodeString(outbox.RequestBodyBase64)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, m.config.heptaBaseURL+researchConsumptionPath, bytes.NewReader(body))
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
		return fmt.Errorf("deliver Hepta authorization consumption: %w", err)
	}
	defer response.Body.Close()
	encoded, err := io.ReadAll(io.LimitReader(response.Body, maximumHeptaResponseBytes+1))
	if err != nil {
		return fmt.Errorf("read Hepta authorization consumption response: %w", err)
	}
	if len(encoded) > maximumHeptaResponseBytes {
		return errors.New("Hepta authorization consumption response exceeds size limit")
	}
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("Hepta authorization consumption returned HTTP %d", response.StatusCode)
	}
	if err := requireHeptaJSONResponse(response); err != nil {
		return err
	}
	var result heptaResearchConsumptionReceipt
	if err := decodeJSONStrict(string(encoded), &result); err != nil {
		return fmt.Errorf("decode Hepta authorization consumption response: %w", err)
	}
	if err := validateHeptaResearchConsumptionReceipt(outbox, result, m.config.issuerKeys); err != nil {
		return err
	}
	updated := cloneStoredResearchSession(state.record)
	deliveredAt := time.Now().UTC().Unix()
	if deliveredAt < outbox.ConsumedAtUnix {
		deliveredAt = outbox.ConsumedAtUnix
	}
	updated.ConsumptionOutboxes[index].DeliveredAtUnix = &deliveredAt
	receiptDigest := sha256.Sum256(encoded)
	updated.ConsumptionOutboxes[index].ReceiptBodyBase64 = base64.StdEncoding.EncodeToString(encoded)
	updated.ConsumptionOutboxes[index].ReceiptSHA256 = hex.EncodeToString(receiptDigest[:])
	version, err := updateStoredResearch(ctx, nk, updated, state.storageVersion)
	if err != nil {
		return fmt.Errorf("persist Hepta authorization consumption delivery: %w", err)
	}
	state.record = updated
	state.storageVersion = version
	logger.Info("research authorization epoch consumption delivered: session=%s roster_version=%d", outbox.SessionID, outbox.RosterVersion)
	return nil
}

func validateHeptaResearchConsumptionReceipt(outbox storedResearchConsumptionOutbox, receipt heptaResearchConsumptionReceipt, trusted map[string]ed25519.PublicKey) error {
	if receipt.Schema != researchcontract.HeptaAuthorizationConsumptionReceiptSchema ||
		receipt.SessionID != outbox.SessionID || receipt.TeamID != outbox.TeamID ||
		receipt.PaperProjectID != outbox.PaperProjectID || receipt.ChallengeID != outbox.ChallengeID ||
		receipt.SessionRosterVersion != outbox.RosterVersion || receipt.RosterRoot != outbox.RosterRoot ||
		receipt.ConsumedAtUnix != outbox.ConsumedAtUnix || !equalStringSlices(receipt.AuthorizationIDs, outbox.AuthorizationIDs) {
		return errors.New("Hepta authorization consumption receipt differs from durable outbox")
	}
	if err := receipt.Verify(trusted); err != nil {
		return fmt.Errorf("verify Hepta authorization consumption receipt ACK: %w", err)
	}
	return nil
}

func cloneStoredResearchSession(record storedResearchSession) storedResearchSession {
	copy := record
	copy.ConsumptionOutboxes = make([]storedResearchConsumptionOutbox, len(record.ConsumptionOutboxes))
	for index := range record.ConsumptionOutboxes {
		copy.ConsumptionOutboxes[index] = record.ConsumptionOutboxes[index]
		copy.ConsumptionOutboxes[index].AuthorizationIDs = append([]string(nil), record.ConsumptionOutboxes[index].AuthorizationIDs...)
		if record.ConsumptionOutboxes[index].DeliveredAtUnix != nil {
			value := *record.ConsumptionOutboxes[index].DeliveredAtUnix
			copy.ConsumptionOutboxes[index].DeliveredAtUnix = &value
		}
	}
	if record.CompletionOutbox != nil {
		completion := *record.CompletionOutbox
		if record.CompletionOutbox.DeliveredAtUnix != nil {
			value := *record.CompletionOutbox.DeliveredAtUnix
			completion.DeliveredAtUnix = &value
		}
		copy.CompletionOutbox = &completion
	}
	return copy
}

func equalStringSlices(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}
