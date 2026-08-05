package researchcontract

import (
	"crypto/ed25519"
	"errors"
	"fmt"
)

const AuthorizationSchema = "trnm.research-session.authorization.v1"

// AuthorizationClaim is a Hepta-issued admission for one immutable roster
// epoch. Replacement creates a complete fresh authorization set for the next
// epoch; an authorization is never silently carried across a roster change.
type AuthorizationClaim struct {
	Schema                string `json:"schema"`
	AuthorizationID       string `json:"authorization_id"`
	SessionID             string `json:"session_id"`
	TeamID                string `json:"team_id"`
	PaperProjectID        string `json:"paper_project_id"`
	ChallengeID           string `json:"challenge_id"`
	AgentID               string `json:"agent_id"`
	AgentDID              string `json:"agent_did"`
	AgentKeyID            string `json:"agent_key_id"`
	AgentPublicKey        []byte `json:"agent_public_key"`
	SubjectUserID         string `json:"subject_user_id"`
	ParticipantSlot       uint32 `json:"participant_slot"`
	Role                  string `json:"role"`
	RosterVersion         uint64 `json:"roster_version"`
	RosterRoot            Digest `json:"roster_root"`
	RulesetHash           Digest `json:"ruleset_hash"`
	ChallengeSnapshotHash Digest `json:"challenge_snapshot_hash"`
	IssuedAtUnix          int64  `json:"issued_at_unix"`
	ExpiresAtUnix         int64  `json:"expires_at_unix"`
}

type SignedAuthorization struct {
	Claim       AuthorizationClaim `json:"claim"`
	IssuerKeyID string             `json:"issuer_key_id"`
	Signature   []byte             `json:"signature"`
}

type RosterEntry struct {
	ParticipantSlot uint32 `json:"participant_slot"`
	AuthorizationID string `json:"authorization_id"`
	SubjectUserID   string `json:"subject_user_id"`
	AgentID         string `json:"agent_id"`
	AgentDID        string `json:"agent_did"`
	AgentKeyID      string `json:"agent_key_id"`
	AgentKeyHash    Digest `json:"agent_key_hash"`
	Role            string `json:"role"`
}

type RosterSnapshot struct {
	SessionID      string
	TeamID         string
	PaperProjectID string
	ChallengeID    string
	RosterVersion  uint64
	RosterRoot     Digest
	RulesetHash    Digest
	ChallengeHash  Digest
	Entries        []RosterEntry
}

func (c AuthorizationClaim) Validate() error {
	if c.Schema != AuthorizationSchema {
		return fmt.Errorf("unsupported authorization schema %q", c.Schema)
	}
	for name, value := range map[string]string{
		"agent_id": c.AgentID, "agent_did": c.AgentDID, "agent_key_id": c.AgentKeyID,
		"role": c.Role,
	} {
		if err := validateText(name, value); err != nil {
			return err
		}
	}
	if err := ValidateSessionID(c.SessionID); err != nil {
		return err
	}
	if err := ValidateAuthorizationID(c.AuthorizationID); err != nil {
		return err
	}
	if err := ValidateNakamaUserID(c.SubjectUserID); err != nil {
		return err
	}
	if err := ValidateTeamID(c.TeamID); err != nil {
		return err
	}
	if err := ValidatePaperProjectID(c.PaperProjectID); err != nil {
		return err
	}
	if err := ValidateChallengeID(c.ChallengeID); err != nil {
		return err
	}
	if len(c.AgentPublicKey) != ed25519.PublicKeySize {
		return fmt.Errorf("agent_public_key must contain %d bytes", ed25519.PublicKeySize)
	}
	if c.ParticipantSlot < 1 || c.ParticipantSlot > MaxParticipants {
		return fmt.Errorf("participant_slot must be from 1 through %d", MaxParticipants)
	}
	if c.RosterVersion == 0 {
		return errors.New("roster_version must start at 1")
	}
	for name, value := range map[string]Digest{
		"roster_root": c.RosterRoot, "ruleset_hash": c.RulesetHash,
		"challenge_snapshot_hash": c.ChallengeSnapshotHash,
	} {
		if err := value.Validate(); err != nil {
			return fmt.Errorf("%s: %w", name, err)
		}
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
	return newFrame("trnm_research_session_authorization_claim_v1").
		string(c.Schema).string(c.AuthorizationID).string(c.SessionID).
		string(c.TeamID).string(c.PaperProjectID).string(c.ChallengeID).
		string(c.AgentID).string(c.AgentDID).string(c.AgentKeyID).
		bytes(c.AgentPublicKey).string(c.SubjectUserID).u32(c.ParticipantSlot).
		string(c.Role).u64(c.RosterVersion).digest(c.RosterRoot).
		digest(c.RulesetHash).digest(c.ChallengeSnapshotHash).
		i64(c.IssuedAtUnix).i64(c.ExpiresAtUnix).result()
}

func AuthorizationSigningBytes(claim AuthorizationClaim, issuerKeyID string) ([]byte, error) {
	if err := validateText("issuer_key_id", issuerKeyID); err != nil {
		return nil, err
	}
	claimBytes, err := claim.CanonicalBytes()
	if err != nil {
		return nil, err
	}
	return newFrame("trnm_research_session_authorization_signature_v1").
		string(issuerKeyID).bytes(claimBytes).result()
}

func SignAuthorization(claim AuthorizationClaim, issuerKeyID string, key ed25519.PrivateKey) (SignedAuthorization, error) {
	if err := validatePrivateKey(key, "issuer"); err != nil {
		return SignedAuthorization{}, err
	}
	message, err := AuthorizationSigningBytes(claim, issuerKeyID)
	if err != nil {
		return SignedAuthorization{}, err
	}
	return SignedAuthorization{Claim: claim, IssuerKeyID: issuerKeyID, Signature: ed25519.Sign(key, message)}, nil
}

func VerifyAuthorizationSignature(auth SignedAuthorization, trusted map[string]ed25519.PublicKey) error {
	key, ok := trusted[auth.IssuerKeyID]
	if !ok {
		return fmt.Errorf("untrusted issuer key %q", auth.IssuerKeyID)
	}
	if len(key) != ed25519.PublicKeySize || len(auth.Signature) != ed25519.SignatureSize {
		return errors.New("issuer key or signature has invalid length")
	}
	message, err := AuthorizationSigningBytes(auth.Claim, auth.IssuerKeyID)
	if err != nil {
		return err
	}
	if !ed25519.Verify(key, message, auth.Signature) {
		return errors.New("authorization signature verification failed")
	}
	return nil
}

func VerifyAuthorization(auth SignedAuthorization, trusted map[string]ed25519.PublicKey, nowUnix int64) error {
	if err := VerifyAuthorizationSignature(auth, trusted); err != nil {
		return err
	}
	if nowUnix < auth.Claim.IssuedAtUnix {
		return errors.New("authorization is not active yet")
	}
	if nowUnix >= auth.Claim.ExpiresAtUnix {
		return errors.New("authorization has expired")
	}
	return nil
}

func RosterEntries(authorizations []SignedAuthorization) []RosterEntry {
	entries := make([]RosterEntry, len(authorizations))
	for i, auth := range authorizations {
		claim := auth.Claim
		entries[i] = RosterEntry{
			ParticipantSlot: claim.ParticipantSlot, AuthorizationID: claim.AuthorizationID,
			SubjectUserID: claim.SubjectUserID, AgentID: claim.AgentID, AgentDID: claim.AgentDID,
			AgentKeyID: claim.AgentKeyID, AgentKeyHash: NewDigest(claim.AgentPublicKey), Role: claim.Role,
		}
	}
	return sortedRoster(entries)
}

func RosterCanonicalBytes(sessionID, teamID, paperProjectID string, rosterVersion uint64, entries []RosterEntry) ([]byte, error) {
	if err := ValidateSessionID(sessionID); err != nil {
		return nil, err
	}
	if err := ValidateTeamID(teamID); err != nil {
		return nil, err
	}
	if err := ValidatePaperProjectID(paperProjectID); err != nil {
		return nil, err
	}
	if rosterVersion == 0 {
		return nil, errors.New("roster_version must start at 1")
	}
	if len(entries) < MinParticipants || len(entries) > MaxParticipants {
		return nil, fmt.Errorf("roster must contain %d through %d participants", MinParticipants, MaxParticipants)
	}
	entries = sortedRoster(entries)
	f := newFrame("trnm_research_session_roster_v1").string(sessionID).string(teamID).
		string(paperProjectID).u64(rosterVersion).u32(uint32(len(entries)))
	seen := map[string]struct{}{}
	seenKeyHashes := map[Digest]struct{}{}
	for index, entry := range entries {
		if entry.ParticipantSlot != uint32(index+1) {
			return nil, errors.New("roster slots must be unique and gapless from 1")
		}
		if err := ValidateAuthorizationID(entry.AuthorizationID); err != nil {
			return nil, err
		}
		if err := ValidateNakamaUserID(entry.SubjectUserID); err != nil {
			return nil, err
		}
		for name, value := range map[string]string{
			"agent_id": entry.AgentID, "agent_did": entry.AgentDID,
			"agent_key_id": entry.AgentKeyID, "role": entry.Role,
		} {
			if err := validateText(name, value); err != nil {
				return nil, err
			}
			key := name + "\x00" + value
			if _, exists := seen[key]; exists && name != "role" {
				return nil, fmt.Errorf("roster contains duplicate %s", name)
			}
			seen[key] = struct{}{}
		}
		for name, value := range map[string]string{"authorization_id": entry.AuthorizationID, "subject_user_id": entry.SubjectUserID} {
			key := name + "\x00" + value
			if _, exists := seen[key]; exists {
				return nil, fmt.Errorf("roster contains duplicate %s", name)
			}
			seen[key] = struct{}{}
		}
		if err := entry.AgentKeyHash.Validate(); err != nil {
			return nil, fmt.Errorf("agent_key_hash: %w", err)
		}
		if _, exists := seenKeyHashes[entry.AgentKeyHash]; exists {
			return nil, errors.New("roster contains duplicate agent public key")
		}
		seenKeyHashes[entry.AgentKeyHash] = struct{}{}
		f = f.u32(entry.ParticipantSlot).string(entry.AuthorizationID).string(entry.SubjectUserID).
			string(entry.AgentID).string(entry.AgentDID).string(entry.AgentKeyID).
			digest(entry.AgentKeyHash).string(entry.Role)
	}
	return f.result()
}

func RosterRoot(sessionID, teamID, paperProjectID string, rosterVersion uint64, entries []RosterEntry) (Digest, error) {
	encoded, err := RosterCanonicalBytes(sessionID, teamID, paperProjectID, rosterVersion, entries)
	if err != nil {
		return "", err
	}
	return NewDigest(encoded), nil
}

// ValidateAuthorizationSet verifies a complete 3-5 participant roster epoch.
func ValidateAuthorizationSet(authorizations []SignedAuthorization, trusted map[string]ed25519.PublicKey, nowUnix int64, checkTime bool) (RosterSnapshot, error) {
	if len(authorizations) < MinParticipants || len(authorizations) > MaxParticipants {
		return RosterSnapshot{}, fmt.Errorf("authorization set must contain %d through %d entries", MinParticipants, MaxParticipants)
	}
	auths := append([]SignedAuthorization(nil), authorizations...)
	// Slot ordering is canonical on input too: it prevents multiple JSON byte
	// representations of the same operator request.
	for i := range auths {
		if auths[i].Claim.ParticipantSlot != uint32(i+1) {
			return RosterSnapshot{}, errors.New("authorizations must be ordered by gapless participant_slot")
		}
		var err error
		if checkTime {
			err = VerifyAuthorization(auths[i], trusted, nowUnix)
		} else {
			err = VerifyAuthorizationSignature(auths[i], trusted)
		}
		if err != nil {
			return RosterSnapshot{}, fmt.Errorf("slot %d authorization: %w", i+1, err)
		}
	}
	first := auths[0].Claim
	entries := RosterEntries(auths)
	root, err := RosterRoot(first.SessionID, first.TeamID, first.PaperProjectID, first.RosterVersion, entries)
	if err != nil {
		return RosterSnapshot{}, err
	}
	for _, auth := range auths {
		claim := auth.Claim
		if claim.SessionID != first.SessionID || claim.TeamID != first.TeamID ||
			claim.PaperProjectID != first.PaperProjectID || claim.ChallengeID != first.ChallengeID ||
			claim.RosterVersion != first.RosterVersion || claim.RosterRoot != first.RosterRoot ||
			claim.RulesetHash != first.RulesetHash || claim.ChallengeSnapshotHash != first.ChallengeSnapshotHash {
			return RosterSnapshot{}, errors.New("authorization set does not share one immutable session snapshot")
		}
	}
	if root != first.RosterRoot {
		return RosterSnapshot{}, errors.New("authorization roster_root does not match canonical roster")
	}
	return RosterSnapshot{
		SessionID: first.SessionID, TeamID: first.TeamID, PaperProjectID: first.PaperProjectID,
		ChallengeID: first.ChallengeID, RosterVersion: first.RosterVersion, RosterRoot: root,
		RulesetHash: first.RulesetHash, ChallengeHash: first.ChallengeSnapshotHash, Entries: entries,
	}, nil
}
