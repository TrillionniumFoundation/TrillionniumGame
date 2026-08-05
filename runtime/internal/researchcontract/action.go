package researchcontract

import (
	"crypto/ed25519"
	"errors"
	"fmt"
)

const ActionSchema = "trnm.research-session.action.v1"

const (
	ActionParticipantReady         = "participant.ready"
	ActionTaskClaimed              = "research.task.claimed"
	ActionProposalSubmitted        = "agent.proposal.submitted"
	ActionArtifactPublished        = "artifact.manifest.published"
	ActionReviewSubmitted          = "review.submitted"
	ActionCheckpointRecorded       = "checkpoint.recorded"
	ActionPaperReleaseAcknowledged = "paper.release.acknowledged"

	PayloadParticipantReady         = "trnm.research-session.ready.v1"
	PayloadTaskClaimed              = "trnm.paper-raid.task-claim.v1"
	PayloadProposalSubmitted        = "trnm.paper-raid.agent-proposal.v1"
	PayloadArtifactPublished        = "trnm.paper-raid.artifact-manifest.v1"
	PayloadReviewSubmitted          = "trnm.paper-raid.review.v1"
	PayloadCheckpointRecorded       = "trnm.paper-raid.checkpoint.v1"
	PayloadPaperReleaseAcknowledged = "trnm.paper-raid.release-acknowledgement.v1"
)

// actionPayloadTypes is deliberately a one-to-one whitelist. Validating the
// action and payload type as two independent enums would allow a payload from
// one scientific operation to be committed under another authoritative event.
var actionPayloadTypes = map[string]string{
	ActionParticipantReady:         PayloadParticipantReady,
	ActionTaskClaimed:              PayloadTaskClaimed,
	ActionProposalSubmitted:        PayloadProposalSubmitted,
	ActionArtifactPublished:        PayloadArtifactPublished,
	ActionReviewSubmitted:          PayloadReviewSubmitted,
	ActionCheckpointRecorded:       PayloadCheckpointRecorded,
	ActionPaperReleaseAcknowledged: PayloadPaperReleaseAcknowledged,
}

type ActionEnvelope struct {
	Schema                 string `json:"schema"`
	ActionID               string `json:"action_id"`
	AuthorizationID        string `json:"authorization_id"`
	SessionID              string `json:"session_id"`
	TeamID                 string `json:"team_id"`
	PaperProjectID         string `json:"paper_project_id"`
	ChallengeID            string `json:"challenge_id"`
	RosterVersion          uint64 `json:"roster_version"`
	ParticipantSlot        uint32 `json:"participant_slot"`
	ParticipantSequence    uint64 `json:"participant_sequence"`
	ExpectedSessionVersion uint64 `json:"expected_session_version"`
	IssuedAtUnix           int64  `json:"issued_at_unix"`
	ActionType             string `json:"action_type"`
	PayloadType            string `json:"payload_type"`
	Payload                []byte `json:"payload"`
	PayloadHash            Digest `json:"payload_hash"`
	ReferenceHash          Digest `json:"reference_hash"`
	AgentKeyID             string `json:"agent_key_id"`
	Signature              []byte `json:"signature"`
}

func (a ActionEnvelope) Validate() error {
	if a.Schema != ActionSchema {
		return fmt.Errorf("unsupported action schema %q", a.Schema)
	}
	for name, value := range map[string]string{
		"action_id": a.ActionID, "action_type": a.ActionType,
		"payload_type": a.PayloadType, "agent_key_id": a.AgentKeyID,
	} {
		if err := validateText(name, value); err != nil {
			return err
		}
	}
	if err := ValidateSessionID(a.SessionID); err != nil {
		return err
	}
	if err := ValidateAuthorizationID(a.AuthorizationID); err != nil {
		return err
	}
	if err := ValidateTeamID(a.TeamID); err != nil {
		return err
	}
	if err := ValidatePaperProjectID(a.PaperProjectID); err != nil {
		return err
	}
	if err := ValidateChallengeID(a.ChallengeID); err != nil {
		return err
	}
	payloadType, ok := actionPayloadTypes[a.ActionType]
	if !ok {
		return fmt.Errorf("unsupported action_type %q", a.ActionType)
	}
	if a.PayloadType != payloadType {
		return fmt.Errorf("payload_type %q is not valid for action_type %q; expected %q", a.PayloadType, a.ActionType, payloadType)
	}
	if a.RosterVersion == 0 {
		return errors.New("roster_version must start at 1")
	}
	if a.ParticipantSlot < 1 || a.ParticipantSlot > MaxParticipants {
		return fmt.Errorf("participant_slot must be from 1 through %d", MaxParticipants)
	}
	if a.ParticipantSequence == 0 {
		return errors.New("participant_sequence must start at 1")
	}
	if a.ExpectedSessionVersion == 0 {
		return errors.New("expected_session_version must be positive")
	}
	if a.IssuedAtUnix < 0 {
		return errors.New("issued_at_unix must be non-negative")
	}
	if len(a.Payload) == 0 || len(a.Payload) > MaxPayloadBytes {
		return fmt.Errorf("payload must contain between 1 and %d bytes", MaxPayloadBytes)
	}
	if a.PayloadHash != NewDigest(a.Payload) {
		return errors.New("payload_hash does not match payload")
	}
	if err := a.ReferenceHash.Validate(); err != nil {
		return fmt.Errorf("reference_hash: %w", err)
	}
	if len(a.Signature) != 0 && len(a.Signature) != ed25519.SignatureSize {
		return errors.New("action signature has invalid length")
	}
	return nil
}

func (a ActionEnvelope) SigningBytes() ([]byte, error) {
	if err := a.Validate(); err != nil {
		return nil, err
	}
	return newFrame("trnm_research_session_action_signature_v1").
		string(a.Schema).string(a.ActionID).string(a.AuthorizationID).
		string(a.SessionID).string(a.TeamID).string(a.PaperProjectID).string(a.ChallengeID).
		u64(a.RosterVersion).u32(a.ParticipantSlot).u64(a.ParticipantSequence).
		u64(a.ExpectedSessionVersion).i64(a.IssuedAtUnix).string(a.ActionType).
		string(a.PayloadType).bytes(a.Payload).digest(a.PayloadHash).
		digest(a.ReferenceHash).string(a.AgentKeyID).result()
}

func SignAction(action ActionEnvelope, key ed25519.PrivateKey) (ActionEnvelope, error) {
	if err := validatePrivateKey(key, "agent"); err != nil {
		return ActionEnvelope{}, err
	}
	action.PayloadHash = NewDigest(action.Payload)
	action.Signature = nil
	message, err := action.SigningBytes()
	if err != nil {
		return ActionEnvelope{}, err
	}
	action.Signature = ed25519.Sign(key, message)
	return action, nil
}

func VerifyAction(action ActionEnvelope, key ed25519.PublicKey) error {
	if len(key) != ed25519.PublicKeySize || len(action.Signature) != ed25519.SignatureSize {
		return errors.New("agent key or signature has invalid length")
	}
	message, err := action.SigningBytes()
	if err != nil {
		return err
	}
	if !ed25519.Verify(key, message, action.Signature) {
		return errors.New("action signature verification failed")
	}
	return nil
}

func ActionFingerprint(action ActionEnvelope) (Digest, error) {
	message, err := action.SigningBytes()
	if err != nil {
		return "", err
	}
	encoded, err := newFrame("trnm_research_session_action_fingerprint_v1").
		bytes(message).bytes(action.Signature).result()
	if err != nil {
		return "", err
	}
	return NewDigest(encoded), nil
}
