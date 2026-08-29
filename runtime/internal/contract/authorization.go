package contract

import (
	"crypto/ed25519"
	"errors"
	"fmt"
)

const AuthorizationSchema = "trnm.match.authorization.v1"

// AuthorizationClaim is an immutable Hepta-issued admission and roster
// snapshot. SubjectUserID binds the claim to one authenticated Nakama user.
type AuthorizationClaim struct {
	Schema                string `json:"schema"`
	AuthorizationID       string `json:"authorization_id"`
	MatchID               string `json:"match_id"`
	ChallengeID           string `json:"challenge_id"`
	AgentID               string `json:"agent_id"`
	AgentDID              string `json:"agent_did"`
	AgentKeyID            string `json:"agent_key_id"`
	AgentPublicKey        []byte `json:"agent_public_key"`
	SubjectUserID         string `json:"subject_user_id"`
	ParticipantSlot       uint32 `json:"participant_slot"`
	Role                  string `json:"role"`
	RulesetHash           Digest `json:"ruleset_hash"`
	DatasetHash           Digest `json:"dataset_hash"`
	ChallengeSnapshotHash Digest `json:"challenge_snapshot_hash"`
	IssuedAtUnix          int64  `json:"issued_at_unix"`
	ExpiresAtUnix         int64  `json:"expires_at_unix"`
}

type SignedAuthorization struct {
	Claim       AuthorizationClaim `json:"claim"`
	IssuerKeyID string             `json:"issuer_key_id"`
	Signature   []byte             `json:"signature"`
}

func (c AuthorizationClaim) Validate() error {
	if c.Schema != AuthorizationSchema {
		return fmt.Errorf("unsupported authorization schema %q", c.Schema)
	}
	for name, value := range map[string]string{
		"match_id":        c.MatchID,
		"challenge_id":    c.ChallengeID,
		"agent_id":        c.AgentID,
		"agent_did":       c.AgentDID,
		"agent_key_id":    c.AgentKeyID,
		"subject_user_id": c.SubjectUserID,
		"role":            c.Role,
	} {
		if err := validateText(name, value); err != nil {
			return err
		}
	}
	if err := ValidateAuthorizationID(c.AuthorizationID); err != nil {
		return err
	}
	if len(c.AgentPublicKey) != ed25519.PublicKeySize {
		return fmt.Errorf("agent_public_key must be %d bytes", ed25519.PublicKeySize)
	}
	if err := ValidateLogicalMatchID(c.MatchID); err != nil {
		return err
	}
	if c.ParticipantSlot != 1 && c.ParticipantSlot != 2 {
		return errors.New("participant_slot must be 1 or 2")
	}
	if err := c.RulesetHash.Validate(); err != nil {
		return fmt.Errorf("ruleset_hash: %w", err)
	}
	if err := c.DatasetHash.Validate(); err != nil {
		return fmt.Errorf("dataset_hash: %w", err)
	}
	if err := c.ChallengeSnapshotHash.Validate(); err != nil {
		return fmt.Errorf("challenge_snapshot_hash: %w", err)
	}
	if c.IssuedAtUnix < 0 || c.ExpiresAtUnix <= c.IssuedAtUnix {
		return errors.New("authorization validity interval is invalid")
	}
	return nil
}

func (c AuthorizationClaim) CanonicalBytes() ([]byte, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	f := newFrame("trnm_match_authorization_claim_v1").
		string(c.Schema).
		string(c.AuthorizationID).
		string(c.MatchID).
		string(c.ChallengeID).
		string(c.AgentID).
		string(c.AgentDID).
		string(c.AgentKeyID).
		bytes(c.AgentPublicKey).
		string(c.SubjectUserID).
		u32(c.ParticipantSlot).
		string(c.Role)
	var err error
	if f, err = f.digest(c.RulesetHash); err != nil {
		return nil, err
	}
	if f, err = f.digest(c.DatasetHash); err != nil {
		return nil, err
	}
	if f, err = f.digest(c.ChallengeSnapshotHash); err != nil {
		return nil, err
	}
	f = f.i64(c.IssuedAtUnix).i64(c.ExpiresAtUnix)
	return f.result()
}

func authorizationSigningBytes(claim AuthorizationClaim, issuerKeyID string) ([]byte, error) {
	if err := validateText("issuer_key_id", issuerKeyID); err != nil {
		return nil, err
	}
	claimBytes, err := claim.CanonicalBytes()
	if err != nil {
		return nil, err
	}
	return newFrame("trnm_match_authorization_signature_v1").string(issuerKeyID).bytes(claimBytes).result()
}

func SignAuthorization(claim AuthorizationClaim, issuerKeyID string, privateKey ed25519.PrivateKey) (SignedAuthorization, error) {
	if err := validateEd25519PrivateKey(privateKey, "issuer"); err != nil {
		return SignedAuthorization{}, err
	}
	message, err := authorizationSigningBytes(claim, issuerKeyID)
	if err != nil {
		return SignedAuthorization{}, err
	}
	return SignedAuthorization{
		Claim:       claim,
		IssuerKeyID: issuerKeyID,
		Signature:   ed25519.Sign(privateKey, message),
	}, nil
}

// VerifyAuthorization validates structure, signature, and first-use validity.
func VerifyAuthorization(auth SignedAuthorization, trustedIssuerKeys map[string]ed25519.PublicKey, nowUnix int64) error {
	publicKey, ok := trustedIssuerKeys[auth.IssuerKeyID]
	if !ok {
		return fmt.Errorf("untrusted issuer key %q", auth.IssuerKeyID)
	}
	if len(publicKey) != ed25519.PublicKeySize || len(auth.Signature) != ed25519.SignatureSize {
		return errors.New("issuer key or signature has invalid length")
	}
	message, err := authorizationSigningBytes(auth.Claim, auth.IssuerKeyID)
	if err != nil {
		return err
	}
	if !ed25519.Verify(publicKey, message, auth.Signature) {
		return errors.New("authorization signature verification failed")
	}
	if nowUnix < auth.Claim.IssuedAtUnix {
		return errors.New("authorization is not active yet")
	}
	if nowUnix >= auth.Claim.ExpiresAtUnix {
		return errors.New("authorization has expired")
	}
	return nil
}

// VerifyAuthorizationSignature omits the wall-clock check and is intended for
// restoring an already-consumed authorization from a durable snapshot.
func VerifyAuthorizationSignature(auth SignedAuthorization, trustedIssuerKeys map[string]ed25519.PublicKey) error {
	publicKey, ok := trustedIssuerKeys[auth.IssuerKeyID]
	if !ok {
		return fmt.Errorf("untrusted issuer key %q", auth.IssuerKeyID)
	}
	if len(publicKey) != ed25519.PublicKeySize || len(auth.Signature) != ed25519.SignatureSize {
		return errors.New("issuer key or signature has invalid length")
	}
	message, err := authorizationSigningBytes(auth.Claim, auth.IssuerKeyID)
	if err != nil {
		return err
	}
	if !ed25519.Verify(publicKey, message, auth.Signature) {
		return errors.New("authorization signature verification failed")
	}
	return nil
}
