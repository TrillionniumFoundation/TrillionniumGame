package contract

import (
	"crypto/ed25519"
	"errors"
	"fmt"
)

const CommandSchema = "trnm.match.command.v1"

type CommandEnvelope struct {
	Schema               string `json:"schema"`
	CommandID            string `json:"command_id"`
	AuthorizationID      string `json:"authorization_id"`
	MatchID              string `json:"match_id"`
	ChallengeID          string `json:"challenge_id"`
	AgentID              string `json:"agent_id"`
	ParticipantSlot      uint32 `json:"participant_slot"`
	ParticipantSequence  uint64 `json:"participant_sequence"`
	ExpectedMatchVersion uint64 `json:"expected_match_version"`
	IssuedAtUnix         int64  `json:"issued_at_unix"`
	PayloadType          string `json:"payload_type"`
	Payload              []byte `json:"payload"`
	PayloadHash          Digest `json:"payload_hash"`
	AgentKeyID           string `json:"agent_key_id"`
	Signature            []byte `json:"signature"`
}

func (c CommandEnvelope) Validate() error {
	if c.Schema != CommandSchema {
		return fmt.Errorf("unsupported command schema %q", c.Schema)
	}
	for name, value := range map[string]string{
		"command_id":       c.CommandID,
		"authorization_id": c.AuthorizationID,
		"match_id":         c.MatchID,
		"challenge_id":     c.ChallengeID,
		"agent_id":         c.AgentID,
		"payload_type":     c.PayloadType,
		"agent_key_id":     c.AgentKeyID,
	} {
		if err := validateText(name, value); err != nil {
			return err
		}
	}
	if c.ParticipantSlot != 1 && c.ParticipantSlot != 2 {
		return errors.New("participant_slot must be 1 or 2")
	}
	if err := ValidateLogicalMatchID(c.MatchID); err != nil {
		return err
	}
	if c.ParticipantSequence == 0 {
		return errors.New("participant_sequence must start at 1")
	}
	if c.IssuedAtUnix < 0 {
		return errors.New("issued_at_unix must be non-negative")
	}
	if len(c.Payload) == 0 || len(c.Payload) > MaxPayloadBytes {
		return fmt.Errorf("payload must contain between 1 and %d bytes", MaxPayloadBytes)
	}
	if c.PayloadHash != NewDigest(c.Payload) {
		return errors.New("payload_hash does not match payload")
	}
	if len(c.Signature) != 0 && len(c.Signature) != ed25519.SignatureSize {
		return errors.New("command signature has invalid length")
	}
	return nil
}

func (c CommandEnvelope) signingBytes() ([]byte, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	f := newFrame("trnm_match_command_signature_v1").
		string(c.Schema).
		string(c.CommandID).
		string(c.AuthorizationID).
		string(c.MatchID).
		string(c.ChallengeID).
		string(c.AgentID).
		u32(c.ParticipantSlot).
		u64(c.ParticipantSequence).
		u64(c.ExpectedMatchVersion).
		i64(c.IssuedAtUnix).
		string(c.PayloadType).
		bytes(c.Payload)
	var err error
	if f, err = f.digest(c.PayloadHash); err != nil {
		return nil, err
	}
	f = f.string(c.AgentKeyID)
	return f.result()
}

func SignCommand(command CommandEnvelope, privateKey ed25519.PrivateKey) (CommandEnvelope, error) {
	if err := validateEd25519PrivateKey(privateKey, "agent"); err != nil {
		return CommandEnvelope{}, err
	}
	command.PayloadHash = NewDigest(command.Payload)
	command.Signature = nil
	message, err := command.signingBytes()
	if err != nil {
		return CommandEnvelope{}, err
	}
	command.Signature = ed25519.Sign(privateKey, message)
	return command, nil
}

func VerifyCommand(command CommandEnvelope, publicKey ed25519.PublicKey) error {
	if len(publicKey) != ed25519.PublicKeySize || len(command.Signature) != ed25519.SignatureSize {
		return errors.New("agent key or signature has invalid length")
	}
	message, err := command.signingBytes()
	if err != nil {
		return err
	}
	if !ed25519.Verify(publicKey, message, command.Signature) {
		return errors.New("command signature verification failed")
	}
	return nil
}

func CommandFingerprint(command CommandEnvelope) (Digest, error) {
	message, err := command.signingBytes()
	if err != nil {
		return "", err
	}
	f := newFrame("trnm_match_command_fingerprint_v1").bytes(message).bytes(command.Signature)
	encoded, err := f.result()
	if err != nil {
		return "", err
	}
	return NewDigest(encoded), nil
}
