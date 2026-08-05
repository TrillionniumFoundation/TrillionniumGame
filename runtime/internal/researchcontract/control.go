package researchcontract

import (
	"crypto/ed25519"
	"errors"
	"fmt"
)

const (
	ResearchControlClaimSchemaV2 = "trnm.nakama.research-control.claim.v2"
	ResearchControlAudienceV2    = "trnm:nakama:research-control:v2"

	ResearchControlOperationCreate   = "create"
	ResearchControlOperationResume   = "resume"
	ResearchControlOperationReplace  = "replace_roster"
	ResearchControlOperationComplete = "complete"

	ResearchControlRPCCreateV2   = "trnm_research_session_create_v2"
	ResearchControlRPCResumeV2   = "trnm_research_session_resume_v2"
	ResearchControlRPCReplaceV2  = "trnm_research_session_replace_roster_v2"
	ResearchControlRPCCompleteV2 = "trnm_research_session_complete_v2"

	ResearchControlCreateRequestSchemaV2   = "trnm.nakama.research-session.create.v2"
	ResearchControlResumeRequestSchemaV2   = "trnm.nakama.research-session.resume.v2"
	ResearchControlReplaceRequestSchemaV2  = "trnm.nakama.research-session.replace-roster.v2"
	ResearchControlCompleteRequestSchemaV2 = "trnm.nakama.research-session.complete.v2"

	ResearchControlMaximumLifetimeSeconds int64 = 120
	ResearchControlClockSkewSeconds       int64 = 30
)

func researchControlAuthorizationBytesV2(authorization SignedAuthorization) ([]byte, error) {
	if len(authorization.Signature) != ed25519.SignatureSize {
		return nil, errors.New("authorization signature length is invalid")
	}
	signed, err := AuthorizationSigningBytes(authorization.Claim, authorization.IssuerKeyID)
	if err != nil {
		return nil, err
	}
	return newFrame("trnm_research_control_authorization_envelope_v2").
		bytes(signed).bytes(authorization.Signature).result()
}

func researchControlAuthorizationSetBytesV2(frameDomain, schema, sessionID, authorizationSetID string,
	authorizations []SignedAuthorization) ([]byte, error) {
	if err := ValidateSessionID(sessionID); err != nil {
		return nil, err
	}
	if err := ValidateAuthorizationSetID(authorizationSetID); err != nil {
		return nil, err
	}
	if len(authorizations) < MinParticipants || len(authorizations) > MaxParticipants {
		return nil, fmt.Errorf("authorization set must contain %d through %d entries", MinParticipants, MaxParticipants)
	}
	rosterVersion := authorizations[0].Claim.RosterVersion
	if rosterVersion == 0 {
		return nil, errors.New("research control authorization roster version must start at 1")
	}
	frame := newFrame(frameDomain).string(schema).string(sessionID).string(authorizationSetID).
		u32(uint32(len(authorizations)))
	for index, authorization := range authorizations {
		if authorization.Claim.ParticipantSlot != uint32(index+1) || authorization.Claim.SessionID != sessionID ||
			authorization.Claim.RosterVersion != rosterVersion {
			return nil, errors.New("research control authorizations must be ordered and bind one session and roster version")
		}
		encoded, err := researchControlAuthorizationBytesV2(authorization)
		if err != nil {
			return nil, err
		}
		frame = frame.bytes(encoded)
	}
	return frame.result()
}

func ResearchControlCreateBusinessBytesV2(schema, authorizationSetID string, authorizations []SignedAuthorization) ([]byte, error) {
	if schema != ResearchControlCreateRequestSchemaV2 || len(authorizations) == 0 {
		return nil, errors.New("invalid research control create business schema or authorization set")
	}
	return researchControlAuthorizationSetBytesV2("trnm_research_control_create_business_v2", schema,
		authorizations[0].Claim.SessionID, authorizationSetID, authorizations)
}

func ResearchControlResumeBusinessBytesV2(schema, sessionID, authorizationSetID string) ([]byte, error) {
	if schema != ResearchControlResumeRequestSchemaV2 {
		return nil, errors.New("invalid research control resume business schema")
	}
	if err := ValidateSessionID(sessionID); err != nil {
		return nil, err
	}
	if err := ValidateAuthorizationSetID(authorizationSetID); err != nil {
		return nil, err
	}
	return newFrame("trnm_research_control_resume_business_v2").string(schema).
		string(sessionID).string(authorizationSetID).result()
}

func ResearchControlReplaceBusinessBytesV2(schema, sessionID, authorizationSetID string, authorizations []SignedAuthorization) ([]byte, error) {
	if schema != ResearchControlReplaceRequestSchemaV2 {
		return nil, errors.New("invalid research control replacement business schema")
	}
	return researchControlAuthorizationSetBytesV2("trnm_research_control_replace_business_v2", schema,
		sessionID, authorizationSetID, authorizations)
}

func ResearchControlCompleteBusinessBytesV2(schema, sessionID, authorizationSetID string, facts TerminalFacts) ([]byte, error) {
	if schema != ResearchControlCompleteRequestSchemaV2 {
		return nil, errors.New("invalid research control completion business schema")
	}
	if err := ValidateSessionID(sessionID); err != nil {
		return nil, err
	}
	if err := ValidateAuthorizationSetID(authorizationSetID); err != nil {
		return nil, err
	}
	terminal, err := facts.CanonicalBytes()
	if err != nil {
		return nil, err
	}
	return newFrame("trnm_research_control_complete_business_v2").string(schema).
		string(sessionID).string(authorizationSetID).bytes(terminal).result()
}

// ResearchControlClaimV2 is a short-lived, operation-specific Hepta command.
// PayloadHash commits to the operation-specific, length-prefixed binary
// business frame. The full canonical JSON request is stored separately only
// for exact command replay and command_id conflict detection.
type ResearchControlClaimV2 struct {
	Schema               string `json:"schema"`
	CommandID            string `json:"command_id"`
	Operation            string `json:"operation"`
	TargetRPC            string `json:"target_rpc"`
	SessionID            string `json:"session_id"`
	SessionRosterVersion uint64 `json:"session_roster_version"`
	AuthorizationSetID   string `json:"authorization_set_id"`
	PayloadHash          Digest `json:"payload_hash"`
	Audience             string `json:"audience"`
	IssuedAtUnix         int64  `json:"issued_at_unix"`
	ExpiresAtUnix        int64  `json:"expires_at_unix"`
	IssuerKeyID          string `json:"issuer_key_id"`
}

type SignedResearchControlV2 struct {
	Claim     ResearchControlClaimV2 `json:"claim"`
	Signature []byte                 `json:"signature"`
}

func ResearchControlTargetRPC(operation string) (string, error) {
	switch operation {
	case ResearchControlOperationCreate:
		return ResearchControlRPCCreateV2, nil
	case ResearchControlOperationResume:
		return ResearchControlRPCResumeV2, nil
	case ResearchControlOperationReplace:
		return ResearchControlRPCReplaceV2, nil
	case ResearchControlOperationComplete:
		return ResearchControlRPCCompleteV2, nil
	default:
		return "", fmt.Errorf("unsupported research control operation %q", operation)
	}
}

func (claim ResearchControlClaimV2) Validate() error {
	if claim.Schema != ResearchControlClaimSchemaV2 {
		return fmt.Errorf("unsupported research control claim schema %q", claim.Schema)
	}
	if err := ValidateCommandID(claim.CommandID); err != nil {
		return err
	}
	if err := ValidateSessionID(claim.SessionID); err != nil {
		return err
	}
	if claim.SessionRosterVersion == 0 {
		return errors.New("session_roster_version must start at 1")
	}
	if err := ValidateAuthorizationSetID(claim.AuthorizationSetID); err != nil {
		return err
	}
	if err := claim.PayloadHash.Validate(); err != nil {
		return fmt.Errorf("payload_hash: %w", err)
	}
	target, err := ResearchControlTargetRPC(claim.Operation)
	if err != nil {
		return err
	}
	if claim.TargetRPC != target {
		return errors.New("research control target_rpc does not match operation")
	}
	if claim.Audience != ResearchControlAudienceV2 {
		return errors.New("research control audience differs")
	}
	if err := validateText("issuer_key_id", claim.IssuerKeyID); err != nil {
		return err
	}
	if claim.IssuedAtUnix < 0 || claim.ExpiresAtUnix <= claim.IssuedAtUnix ||
		claim.ExpiresAtUnix-claim.IssuedAtUnix > ResearchControlMaximumLifetimeSeconds {
		return fmt.Errorf("research control validity must be from 1 through %d seconds", ResearchControlMaximumLifetimeSeconds)
	}
	return nil
}

func (claim ResearchControlClaimV2) CanonicalBytes() ([]byte, error) {
	if err := claim.Validate(); err != nil {
		return nil, err
	}
	return newFrame("trnm_research_control_claim_v2").
		string(claim.Schema).string(claim.CommandID).string(claim.Operation).
		string(claim.TargetRPC).string(claim.SessionID).u64(claim.SessionRosterVersion).
		string(claim.AuthorizationSetID).digest(claim.PayloadHash).string(claim.Audience).
		i64(claim.IssuedAtUnix).i64(claim.ExpiresAtUnix).string(claim.IssuerKeyID).result()
}

func ResearchControlSigningBytes(claim ResearchControlClaimV2) ([]byte, error) {
	claimBytes, err := claim.CanonicalBytes()
	if err != nil {
		return nil, err
	}
	return newFrame("trnm_research_control_signature_v2").bytes(claimBytes).result()
}

func SignResearchControlV2(claim ResearchControlClaimV2, key ed25519.PrivateKey) (SignedResearchControlV2, error) {
	if err := validatePrivateKey(key, "research control issuer"); err != nil {
		return SignedResearchControlV2{}, err
	}
	message, err := ResearchControlSigningBytes(claim)
	if err != nil {
		return SignedResearchControlV2{}, err
	}
	return SignedResearchControlV2{Claim: claim, Signature: ed25519.Sign(key, message)}, nil
}

func VerifyResearchControlSignatureV2(control SignedResearchControlV2, trusted map[string]ed25519.PublicKey) error {
	key, ok := trusted[control.Claim.IssuerKeyID]
	if !ok {
		return errors.New("research control issuer is not trusted")
	}
	if len(key) != ed25519.PublicKeySize || len(control.Signature) != ed25519.SignatureSize {
		return errors.New("research control key or signature length is invalid")
	}
	message, err := ResearchControlSigningBytes(control.Claim)
	if err != nil {
		return err
	}
	if !ed25519.Verify(key, message, control.Signature) {
		return errors.New("research control signature verification failed")
	}
	return nil
}

func ResearchControlAcceptedAtV2(claim ResearchControlClaimV2, atUnix int64) error {
	if err := claim.Validate(); err != nil {
		return err
	}
	if atUnix < 0 {
		return errors.New("research control verification time is invalid")
	}
	if atUnix < claim.IssuedAtUnix && claim.IssuedAtUnix-atUnix > ResearchControlClockSkewSeconds {
		return errors.New("research control is not active yet")
	}
	if atUnix > claim.ExpiresAtUnix && atUnix-claim.ExpiresAtUnix > ResearchControlClockSkewSeconds {
		return errors.New("research control has expired")
	}
	return nil
}

func VerifyResearchControlV2(control SignedResearchControlV2, trusted map[string]ed25519.PublicKey, nowUnix int64) error {
	if err := VerifyResearchControlSignatureV2(control, trusted); err != nil {
		return err
	}
	return ResearchControlAcceptedAtV2(control.Claim, nowUnix)
}
