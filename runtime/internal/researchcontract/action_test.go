package researchcontract

import (
	"crypto/ed25519"
	"strings"
	"testing"
)

func TestActionValidateRequiresExactActionPayloadPair(t *testing.T) {
	pairs := map[string]string{
		ActionParticipantReady:         PayloadParticipantReady,
		ActionTaskClaimed:              PayloadTaskClaimed,
		ActionProposalSubmitted:        PayloadProposalSubmitted,
		ActionArtifactPublished:        PayloadArtifactPublished,
		ActionReviewSubmitted:          PayloadReviewSubmitted,
		ActionCheckpointRecorded:       PayloadCheckpointRecorded,
		ActionPaperReleaseAcknowledged: PayloadPaperReleaseAcknowledged,
	}
	for actionType, payloadType := range pairs {
		t.Run(actionType, func(t *testing.T) {
			action := validActionForContractTest(actionType, payloadType)
			if err := action.Validate(); err != nil {
				t.Fatalf("valid pair rejected: %v", err)
			}
			wrong := action
			wrong.PayloadType = PayloadReviewSubmitted
			if wrong.PayloadType == payloadType {
				wrong.PayloadType = PayloadProposalSubmitted
			}
			if err := wrong.Validate(); err == nil || !strings.Contains(err.Error(), "is not valid for action_type") {
				t.Fatalf("wrong payload pair was not rejected precisely: %v", err)
			}
		})
	}
}

func TestSignActionRejectsWrongPayloadTypeBeforeSigning(t *testing.T) {
	key := ed25519.NewKeyFromSeed(make([]byte, ed25519.SeedSize))
	action := validActionForContractTest(ActionProposalSubmitted, PayloadReviewSubmitted)
	if _, err := SignAction(action, key); err == nil || !strings.Contains(err.Error(), "is not valid for action_type") {
		t.Fatalf("wrong action/payload pair signed: %v", err)
	}
}

func validActionForContractTest(actionType, payloadType string) ActionEnvelope {
	payload := []byte("contract-test")
	return ActionEnvelope{
		Schema:                 ActionSchema,
		ActionID:               "action-contract-test",
		AuthorizationID:        "30000000-0000-4000-8000-000000000001",
		SessionID:              "research-session-contract-test",
		TeamID:                 "30000000-0000-4000-8000-000000000001",
		PaperProjectID:         "40000000-0000-4000-8000-000000000001",
		ChallengeID:            "50000000-0000-4000-8000-000000000001",
		RosterVersion:          1,
		ParticipantSlot:        1,
		ParticipantSequence:    1,
		ExpectedSessionVersion: 1,
		IssuedAtUnix:           1_800_000_000,
		ActionType:             actionType,
		PayloadType:            payloadType,
		Payload:                payload,
		PayloadHash:            NewDigest(payload),
		ReferenceHash:          NewDigest([]byte("reference")),
		AgentKeyID:             "agent-key-contract-test",
	}
}
