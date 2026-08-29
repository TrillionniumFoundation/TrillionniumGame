package worldtransition

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"unicode/utf8"
)

const (
	ContractVersion      = "trnm_world_transition_v1"
	RequestHashDomain    = "trnm.world.transition.request.v1"
	TransitionHashDomain = "trnm.world.transition.accepted.v1"
	OutcomeHashDomain    = "trnm.world.outcome.v1"
	ContextHashDomain    = "trnm.nakama.world.transition.context.v1"
	TransitionIDDomain   = "trnm.nakama.world.transition.id.v1"
	CommandIDDomain      = "trnm.nakama.world.command.id.v1"

	MaxStateBytes   = 2 * 1024 * 1024
	MaxCommandBytes = 128 * 1024
	MaxReplayBytes  = 2 * 1024 * 1024
	MaxOutcomeBytes = 512 * 1024
)

var (
	identifierPattern = regexp.MustCompile(`^[A-Za-z0-9._:/+@-]{1,160}$`)
	hex64Pattern      = regexp.MustCompile(`^[0-9a-f]{64}$`)

	ErrContract = errors.New("World transition contract violation")
)

var stableErrorCodes = map[string]struct{}{
	"invalid_contract_version":    {},
	"invalid_request":             {},
	"unknown_ruleset_revision":    {},
	"unknown_content_revision":    {},
	"payload_hash_mismatch":       {},
	"invalid_canonical_payload":   {},
	"forbidden_authority_surface": {},
	"resource_budget_exceeded":    {},
	"invalid_command":             {},
	"domain_rejected":             {},
	"nondeterministic_output":     {},
	"internal_unavailable":        {},
}

var forbiddenAuthorityKeys = map[string]struct{}{
	"nakama_session_token":          {},
	"nakama_private_key":            {},
	"match_authority_private_key":   {},
	"canonical_archive_root":        {},
	"chain_finality":                {},
	"chain_app_hash":                {},
	"match_completed_v1":            {},
	"participant_admission_receipt": {},
	"global_event_cursor":           {},
	"participant_roster":            {},
	"participant_roles":             {},
	"completion_signature":          {},
	"authority_key_id":              {},
	"wallet_balance":                {},
}

var (
	requestFields = fieldSet("command", "content_revision", "contract_version", "expected_tick", "previous_state", "ruleset_revision", "transition_id")
	commandFields = fieldSet("command_id", "payload")
	payloadFields = fieldSet("canonical_json", "schema_id", "sha256")
	acceptedFields = fieldSet(
		"content_revision", "contract_version", "next_state", "next_tick",
		"outcome_material", "previous_state_hash", "replay_material",
		"request_hash", "ruleset_revision", "transition_id",
		"world_outcome_hash", "world_transition_hash",
	)
	rejectedFields = fieldSet("code", "contract_version", "detail", "request_hash", "retryable", "transition_id")
)

type CanonicalPayload struct {
	SchemaID      string
	Value         any
	SHA256        string
	CanonicalJSON []byte
}

func NewCanonicalPayload(value any, schemaID string, maximumBytes int, label string) (CanonicalPayload, error) {
	if err := requireIdentifier(schemaID, label+".schema_id"); err != nil {
		return CanonicalPayload{}, err
	}
	if err := rejectAuthorityKeys(value, label); err != nil {
		return CanonicalPayload{}, err
	}
	canonical, err := CanonicalJSON(value, true)
	if err != nil {
		return CanonicalPayload{}, fmt.Errorf("%w: %s: %v", ErrContract, label, err)
	}
	if len(canonical) > maximumBytes {
		return CanonicalPayload{}, fmt.Errorf("%w: %s exceeds %d bytes", ErrContract, label, maximumBytes)
	}
	return CanonicalPayload{
		SchemaID:      schemaID,
		Value:         value,
		SHA256:        sha256Hex(canonical),
		CanonicalJSON: canonical,
	}, nil
}

func PayloadFromWire(value any, maximumBytes int, label string) (CanonicalPayload, error) {
	object, err := requireExactObject(value, payloadFields, label)
	if err != nil {
		return CanonicalPayload{}, err
	}
	schemaID, ok := object["schema_id"].(string)
	if !ok {
		return CanonicalPayload{}, fmt.Errorf("%w: %s.schema_id must be a string", ErrContract, label)
	}
	if err := requireIdentifier(schemaID, label+".schema_id"); err != nil {
		return CanonicalPayload{}, err
	}
	canonicalValue := object["canonical_json"]
	payload, err := NewCanonicalPayload(canonicalValue, schemaID, maximumBytes, label)
	if err != nil {
		return CanonicalPayload{}, err
	}
	supplied, ok := object["sha256"].(string)
	if !ok || !hex64Pattern.MatchString(supplied) {
		return CanonicalPayload{}, fmt.Errorf("%w: %s.sha256 must be lowercase 64-hex", ErrContract, label)
	}
	if supplied != payload.SHA256 {
		return CanonicalPayload{}, fmt.Errorf("%w: %s payload hash mismatch", ErrContract, label)
	}
	payload.SHA256 = supplied
	return payload, nil
}

func (p CanonicalPayload) Wire() map[string]any {
	return map[string]any{
		"canonical_json": p.Value,
		"schema_id":      p.SchemaID,
		"sha256":         p.SHA256,
	}
}

type AuthorityContext struct {
	MatchID               string
	AuthorizationID       string
	ParticipantRosterHash string
	MatchVersion           int64
	GlobalEventSequence    int64
	CommandIdempotencyKey  string
	RulesetRevision        string
	ContentRevision        string
	ExpectedTick           int64
}

func (c AuthorityContext) Validate() error {
	for name, value := range map[string]string{
		"match_id":                c.MatchID,
		"authorization_id":        c.AuthorizationID,
		"command_idempotency_key": c.CommandIdempotencyKey,
		"ruleset_revision":        c.RulesetRevision,
		"content_revision":        c.ContentRevision,
	} {
		if err := requireIdentifier(value, name); err != nil {
			return err
		}
	}
	if !hex64Pattern.MatchString(c.ParticipantRosterHash) {
		return fmt.Errorf("%w: participant_roster_hash must be lowercase 64-hex", ErrContract)
	}
	if c.MatchVersion < 0 || c.GlobalEventSequence < 0 || c.ExpectedTick < 0 {
		return fmt.Errorf("%w: context counters must be non-negative signed i64", ErrContract)
	}
	return nil
}

func (c AuthorityContext) Binding() ([]byte, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	return CanonicalJSON(map[string]any{
		"authorization_id":        c.AuthorizationID,
		"command_idempotency_key": c.CommandIdempotencyKey,
		"content_revision":        c.ContentRevision,
		"expected_tick":           c.ExpectedTick,
		"global_event_sequence":   c.GlobalEventSequence,
		"match_id":                c.MatchID,
		"match_version":           c.MatchVersion,
		"participant_roster_hash": c.ParticipantRosterHash,
		"ruleset_revision":        c.RulesetRevision,
	}, false)
}

func (c AuthorityContext) Fingerprint() (string, error) {
	binding, err := c.Binding()
	if err != nil {
		return "", err
	}
	return domainHash(ContextHashDomain, binding)
}

type Prepared struct {
	Context           AuthorityContext
	Request           map[string]any
	CanonicalRequest  []byte
	RequestHash       string
	TransitionID      string
	CommandID         string
	PreviousStateHash string
}

type Disposition string

const (
	DispositionAccepted Disposition = "accepted"
	DispositionRejected Disposition = "rejected"
)

type Verified struct {
	Context                     AuthorityContext
	AuthorityContextFingerprint string
	RequestHash                 string
	TransitionID                string
	Disposition                 Disposition
	NextTick                    *int64
	PreviousStateHash           *string
	NextState                   *CanonicalPayload
	ReplayMaterial              *CanonicalPayload
	OutcomeMaterial             *CanonicalPayload
	WorldOutcomeHash            *string
	WorldTransitionHash         *string
	ErrorCode                   *string
	Retryable                   *bool
	CanonicalResult             []byte
	CanonicalResultSHA256       string
}

func (v Verified) NextStateHash() *string {
	if v.NextState == nil {
		return nil
	}
	value := v.NextState.SHA256
	return &value
}

func (v Verified) ReplayHash() *string {
	if v.ReplayMaterial == nil {
		return nil
	}
	value := v.ReplayMaterial.SHA256
	return &value
}

func fieldSet(fields ...string) map[string]struct{} {
	result := make(map[string]struct{}, len(fields))
	for _, field := range fields {
		result[field] = struct{}{}
	}
	return result
}

func requireIdentifier(value, label string) error {
	if !identifierPattern.MatchString(value) {
		return fmt.Errorf("%w: %s must be a portable identifier", ErrContract, label)
	}
	return nil
}

func requireExactObject(value any, expected map[string]struct{}, label string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%w: %s must be an object", ErrContract, label)
	}
	for field := range expected {
		if _, present := object[field]; !present {
			return nil, fmt.Errorf("%w: %s missing field %q", ErrContract, label, field)
		}
	}
	for field := range object {
		if _, allowed := expected[field]; allowed {
			continue
		}
		if _, authority := forbiddenAuthorityKeys[field]; authority {
			return nil, fmt.Errorf("%w: %s contains forbidden authority field %q", ErrContract, label, field)
		}
		return nil, fmt.Errorf("%w: %s has unknown field %q", ErrContract, label, field)
	}
	return object, nil
}

func rejectAuthorityKeys(value any, label string) error {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			if _, forbidden := forbiddenAuthorityKeys[key]; forbidden {
				return fmt.Errorf("%w: %s contains forbidden authority key %q", ErrContract, label, key)
			}
			if err := rejectAuthorityKeys(child, label); err != nil {
				return err
			}
		}
	case []any:
		for _, child := range typed {
			if err := rejectAuthorityKeys(child, label); err != nil {
				return err
			}
		}
	}
	return nil
}

func sha256Hex(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

func domainHash(domain string, canonical []byte) (string, error) {
	if domain == "" || !utf8.ValidString(domain) {
		return "", fmt.Errorf("%w: hash domain is invalid", ErrContract)
	}
	for _, r := range domain {
		if r > 0x7f || r == '\n' || r == '\r' {
			return "", fmt.Errorf("%w: hash domain must be single-line ASCII", ErrContract)
		}
	}
	material := make([]byte, 0, len(domain)+1+len(canonical))
	material = append(material, domain...)
	material = append(material, '\n')
	material = append(material, canonical...)
	return sha256Hex(material), nil
}
