package researchcontract

import (
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"fmt"
)

const HeptaAuthorizationConsumptionReceiptSchema = "hepta.paper_raid.authorization_set_consumption_receipt.v1"

// SignedAuthorizationSetConsumptionReceiptV1 proves that Hepta durably
// consumed one complete Nakama session authorization epoch. The signed
// authorization objects alone do not prove this state transition.
type SignedAuthorizationSetConsumptionReceiptV1 struct {
	Schema               string   `json:"schema"`
	SessionID            string   `json:"session_id"`
	TeamID               string   `json:"team_id"`
	PaperProjectID       string   `json:"paper_project_id"`
	ChallengeID          string   `json:"challenge_id"`
	SessionRosterVersion uint64   `json:"session_roster_version"`
	RosterRoot           Digest   `json:"roster_root"`
	AuthorizationIDs     []string `json:"authorization_ids"`
	ConsumedAtUnix       int64    `json:"consumed_at_unix"`
	IssuerKeyID          string   `json:"issuer_key_id"`
	Signature            string   `json:"signature"`
}

func (receipt SignedAuthorizationSetConsumptionReceiptV1) SigningBytes() ([]byte, error) {
	if receipt.Schema != HeptaAuthorizationConsumptionReceiptSchema || receipt.SessionRosterVersion == 0 ||
		receipt.ConsumedAtUnix < 0 || len(receipt.AuthorizationIDs) < MinParticipants || len(receipt.AuthorizationIDs) > MaxParticipants {
		return nil, errors.New("Hepta authorization consumption receipt schema, epoch, count, or time is invalid")
	}
	if err := ValidateSessionID(receipt.SessionID); err != nil {
		return nil, err
	}
	if err := ValidateTeamID(receipt.TeamID); err != nil {
		return nil, err
	}
	if err := ValidatePaperProjectID(receipt.PaperProjectID); err != nil {
		return nil, err
	}
	if err := ValidateChallengeID(receipt.ChallengeID); err != nil {
		return nil, err
	}
	if err := receipt.RosterRoot.Validate(); err != nil {
		return nil, err
	}
	if err := validateText("issuer_key_id", receipt.IssuerKeyID); err != nil {
		return nil, err
	}
	seen := make(map[string]struct{}, len(receipt.AuthorizationIDs))
	for _, authorizationID := range receipt.AuthorizationIDs {
		if err := ValidateAuthorizationID(authorizationID); err != nil {
			return nil, err
		}
		if _, duplicate := seen[authorizationID]; duplicate {
			return nil, errors.New("Hepta authorization consumption receipt duplicates authorization_id")
		}
		seen[authorizationID] = struct{}{}
	}
	if receipt.Signature != "" {
		signature, err := base64.StdEncoding.Strict().DecodeString(receipt.Signature)
		if err != nil || len(signature) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(signature) != receipt.Signature {
			return nil, errors.New("Hepta authorization consumption receipt signature must be canonical padded base64 for 64 bytes")
		}
	}
	frame := newFrame("hepta_research_session_authorization_set_consumption_receipt_v1").
		string(receipt.Schema).string(receipt.SessionID).string(receipt.TeamID).
		string(receipt.PaperProjectID).string(receipt.ChallengeID).u64(receipt.SessionRosterVersion).
		digest(receipt.RosterRoot).u32(uint32(len(receipt.AuthorizationIDs)))
	for _, authorizationID := range receipt.AuthorizationIDs {
		frame = frame.string(authorizationID)
	}
	return frame.i64(receipt.ConsumedAtUnix).string(receipt.IssuerKeyID).result()
}

func (receipt SignedAuthorizationSetConsumptionReceiptV1) Verify(trusted map[string]ed25519.PublicKey) error {
	key, ok := trusted[receipt.IssuerKeyID]
	if !ok || len(key) != ed25519.PublicKeySize {
		return fmt.Errorf("untrusted Hepta authorization consumption issuer key %q", receipt.IssuerKeyID)
	}
	message, err := receipt.SigningBytes()
	if err != nil {
		return err
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(receipt.Signature)
	if err != nil || len(signature) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(signature) != receipt.Signature {
		return errors.New("Hepta authorization consumption receipt signature is not canonical")
	}
	if !ed25519.Verify(key, message, signature) {
		return errors.New("Hepta authorization consumption receipt signature verification failed")
	}
	return nil
}
