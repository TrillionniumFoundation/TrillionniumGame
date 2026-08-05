package researchcontract

import (
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"fmt"
)

const HeptaCompletionReceiptSchema = "hepta.paper_raid.nakama_completion_receipt.v1"

// SignedHeptaCompletionReceiptV1 is Hepta's cryptographic acknowledgement of
// a verified Nakama completion. Its issuer key is selected only from a locally
// pinned map; issuer_key_id in the response is never a trust source.
type SignedHeptaCompletionReceiptV1 struct {
	Schema                string        `json:"schema"`
	CommitmentID          Digest        `json:"commitment_id"`
	SessionID             string        `json:"session_id"`
	TeamID                string        `json:"team_id"`
	PaperProjectID        string        `json:"paper_project_id"`
	ChallengeID           string        `json:"challenge_id"`
	RosterVersion         uint64        `json:"roster_version"`
	RosterRoot            Digest        `json:"roster_root"`
	EventCount            uint64        `json:"event_count"`
	EventRoot             Digest        `json:"event_root"`
	ArchiveHash           Digest        `json:"archive_hash"`
	RulesetHash           Digest        `json:"ruleset_hash"`
	ChallengeSnapshotHash Digest        `json:"challenge_snapshot_hash"`
	NakamaAuthorityKeyID  string        `json:"nakama_authority_key_id"`
	TerminalFacts         TerminalFacts `json:"terminal_facts"`
	VerifiedAtUnix        int64         `json:"verified_at_unix"`
	IssuerKeyID           string        `json:"issuer_key_id"`
	Signature             string        `json:"signature"`
}

func (receipt SignedHeptaCompletionReceiptV1) SigningBytes() ([]byte, error) {
	if receipt.Schema != HeptaCompletionReceiptSchema || receipt.RosterVersion == 0 ||
		receipt.EventCount == 0 || receipt.VerifiedAtUnix < 0 {
		return nil, errors.New("Hepta completion receipt schema, version, count, or time is invalid")
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
	if err := validateText("nakama_authority_key_id", receipt.NakamaAuthorityKeyID); err != nil {
		return nil, err
	}
	if err := validateText("issuer_key_id", receipt.IssuerKeyID); err != nil {
		return nil, err
	}
	for name, value := range map[string]Digest{
		"commitment_id": receipt.CommitmentID, "roster_root": receipt.RosterRoot,
		"event_root": receipt.EventRoot, "archive_hash": receipt.ArchiveHash,
		"ruleset_hash": receipt.RulesetHash, "challenge_snapshot_hash": receipt.ChallengeSnapshotHash,
	} {
		if err := value.Validate(); err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
	}
	if receipt.Signature != "" {
		signature, err := base64.StdEncoding.Strict().DecodeString(receipt.Signature)
		if err != nil || len(signature) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(signature) != receipt.Signature {
			return nil, errors.New("Hepta completion receipt signature must be canonical padded base64 for 64 bytes")
		}
	}
	terminal, err := receipt.TerminalFacts.CanonicalBytes()
	if err != nil {
		return nil, err
	}
	return newFrame("hepta_nakama_research_session_completion_receipt_v1").
		string(receipt.Schema).digest(receipt.CommitmentID).string(receipt.SessionID).
		string(receipt.TeamID).string(receipt.PaperProjectID).string(receipt.ChallengeID).
		u64(receipt.RosterVersion).digest(receipt.RosterRoot).u64(receipt.EventCount).
		digest(receipt.EventRoot).digest(receipt.ArchiveHash).digest(receipt.RulesetHash).
		digest(receipt.ChallengeSnapshotHash).string(receipt.NakamaAuthorityKeyID).
		bytes(terminal).i64(receipt.VerifiedAtUnix).string(receipt.IssuerKeyID).result()
}

func (receipt SignedHeptaCompletionReceiptV1) Verify(trusted map[string]ed25519.PublicKey) error {
	key, ok := trusted[receipt.IssuerKeyID]
	if !ok || len(key) != ed25519.PublicKeySize {
		return fmt.Errorf("untrusted Hepta completion receipt issuer key %q", receipt.IssuerKeyID)
	}
	message, err := receipt.SigningBytes()
	if err != nil {
		return err
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(receipt.Signature)
	if err != nil || len(signature) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(signature) != receipt.Signature {
		return errors.New("Hepta completion receipt signature is not canonical")
	}
	if !ed25519.Verify(key, message, signature) {
		return errors.New("Hepta completion receipt signature verification failed")
	}
	return nil
}
