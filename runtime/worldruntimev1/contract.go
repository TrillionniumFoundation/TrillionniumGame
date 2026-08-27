package worldruntimev1

import "regexp"

const (
	RuntimeContractVersion    = "trnm_world_runtime_v1"
	RuntimeErrorVersion       = "trnm_world_runtime_error_v1"
	RuntimeObservationVersion = "trnm_world_runtime_observation_v1"
	NakamaConsumerReportV1    = "trnm_nakama_world_runtime_consumer_report_v1"
	ExecuteRequestType        = "execute_request"
	ExecuteResultType         = "execute_result"
	InitialStateDomain        = "trnm.world.runtime.v1.initial_state"
	CommandBatchDomain        = "trnm.world.runtime.v1.command_batch"
	FinalStateDomain          = "trnm.world.runtime.v1.final_state"
	OutcomeDomain             = "trnm.world.runtime.v1.outcome"
	ReplayMaterialDomain      = "trnm.world.runtime.v1.replay_material"
	ShadowRequestDomain       = "trnm.world.shadow.v1.request"
)

var (
	hex40Pattern      = regexp.MustCompile(`^[0-9a-f]{40}$`)
	hex64Pattern      = regexp.MustCompile(`^[0-9a-f]{64}$`)
	identifierPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._:-]{0,127}$`)
)

var forbiddenWorldAuthorityFields = map[string]struct{}{
	"participant_roster":   {},
	"participant_roles":    {},
	"global_sequence":      {},
	"event_root":           {},
	"roster_root":          {},
	"archive_root":         {},
	"completion_signature": {},
	"authority_key_id":     {},
	"chain_finality":       {},
	"inclusion_proof":      {},
	"wallet_balance":       {},
	"session_token":        {},
	"idempotency_receipt":  {},
}

var stableRuntimeErrors = map[string]struct{}{
	"unsupported_contract":           {},
	"invalid_contract":               {},
	"invalid_canonical_json":         {},
	"resource_limit_exceeded":        {},
	"ruleset_unavailable":            {},
	"content_unavailable":            {},
	"ordinal_discontinuity":          {},
	"invalid_game_state":             {},
	"invalid_game_command":           {},
	"deterministic_execution_failed": {},
	"output_contract_violation":      {},
	"authority_boundary_violation":   {},
	"invalid_host_configuration":     {},
}

type AuthorityContext struct {
	MatchID                 string `json:"match_id"`
	AuthorizationID         string `json:"authorization_id"`
	RosterHash              string `json:"roster_hash"`
	NextGlobalEventSequence int64  `json:"next_global_event_sequence"`
	RulesetID               string `json:"ruleset_id"`
	RulesetVersion          string `json:"ruleset_version"`
	RulesetDigest           string `json:"ruleset_digest"`
	ContentDigest           string `json:"content_digest"`
}

type VerifiedExecution struct {
	Kind                    string `json:"kind"`
	MatchID                 string `json:"match_id"`
	AuthorizationID         string `json:"authorization_id"`
	RosterHash              string `json:"roster_hash"`
	NextGlobalEventSequence int64  `json:"next_global_event_sequence"`
	RulesetDigest           string `json:"ruleset_digest"`
	ContentDigest           string `json:"content_digest"`
	InitialStateHash        string `json:"initial_state_hash,omitempty"`
	CommandBatchHash        string `json:"command_batch_hash,omitempty"`
	FinalStateHash          string `json:"final_state_hash,omitempty"`
	OutcomeHash             string `json:"outcome_hash,omitempty"`
	ReplayMaterialHash      string `json:"replay_material_hash,omitempty"`
	ErrorCode               string `json:"error_code,omitempty"`
	Recoverable             bool   `json:"recoverable"`
}

func requireExactObject(value any, expected []string, label string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, contractError("invalid_contract", "%s must be an object", label)
	}
	expectedSet := make(map[string]struct{}, len(expected))
	for _, field := range expected {
		expectedSet[field] = struct{}{}
	}
	for field := range object {
		if _, allowed := expectedSet[field]; !allowed {
			if _, forbidden := forbiddenWorldAuthorityFields[field]; forbidden {
				return nil, contractError("authority_boundary_violation", "%s contains forbidden World authority field %s", label, field)
			}
			return nil, contractError("invalid_contract", "%s contains unknown field %s", label, field)
		}
	}
	for _, field := range expected {
		if _, exists := object[field]; !exists {
			return nil, contractError("invalid_contract", "%s is missing field %s", label, field)
		}
	}
	return object, nil
}

func rejectAuthorityFields(value any, label string) error {
	switch typed := value.(type) {
	case map[string]any:
		for field, item := range typed {
			if _, forbidden := forbiddenWorldAuthorityFields[field]; forbidden {
				return contractError("authority_boundary_violation", "%s contains forbidden World authority field %s", label, field)
			}
			if err := rejectAuthorityFields(item, label); err != nil {
				return err
			}
		}
	case []any:
		for _, item := range typed {
			if err := rejectAuthorityFields(item, label); err != nil {
				return err
			}
		}
	}
	return nil
}

func requireString(value any, label string) (string, error) {
	result, ok := value.(string)
	if !ok {
		return "", contractError("invalid_contract", "%s must be a string", label)
	}
	return result, nil
}

func requireIdentifier(value any, label string) (string, error) {
	result, err := requireString(value, label)
	if err != nil {
		return "", err
	}
	if !identifierPattern.MatchString(result) {
		return "", contractError("invalid_contract", "%s is not a portable identifier", label)
	}
	return result, nil
}

func requireHex40(value any, label string) (string, error) {
	result, err := requireString(value, label)
	if err != nil {
		return "", err
	}
	if !hex40Pattern.MatchString(result) {
		return "", contractError("invalid_contract", "%s must be lowercase 40-hex", label)
	}
	return result, nil
}

func requireHex64(value any, label string) (string, error) {
	result, err := requireString(value, label)
	if err != nil {
		return "", err
	}
	if !hex64Pattern.MatchString(result) {
		return "", contractError("invalid_contract", "%s must be lowercase 64-hex", label)
	}
	return result, nil
}

func requireInt64(value any, label string) (int64, error) {
	result, ok := value.(int64)
	if !ok {
		return 0, contractError("invalid_contract", "%s must be a signed 64-bit integer", label)
	}
	return result, nil
}

func validateContext(context AuthorityContext) error {
	if context.MatchID == "" || context.AuthorizationID == "" {
		return contractError("invalid_nakama_context", "match and authorization identities are required")
	}
	if !hex64Pattern.MatchString(context.RosterHash) {
		return contractError("invalid_nakama_context", "roster_hash must be lowercase 64-hex")
	}
	if context.NextGlobalEventSequence < 0 {
		return contractError("invalid_nakama_context", "next_global_event_sequence must be non-negative")
	}
	if !identifierPattern.MatchString(context.RulesetID) || !identifierPattern.MatchString(context.RulesetVersion) {
		return contractError("invalid_nakama_context", "ruleset id/version is invalid")
	}
	if !hex64Pattern.MatchString(context.RulesetDigest) || !hex64Pattern.MatchString(context.ContentDigest) {
		return contractError("invalid_nakama_context", "ruleset/content digest must be lowercase 64-hex")
	}
	return nil
}

func verifyClaimedHash(object map[string]any, valueField, hashField, domain string) (string, error) {
	claimed, err := requireHex64(object[hashField], hashField)
	if err != nil {
		return "", err
	}
	actual, err := DomainHash(domain, object[valueField])
	if err != nil {
		return "", err
	}
	if claimed != actual {
		return "", contractError("output_contract_violation", "%s does not bind %s", hashField, valueField)
	}
	return claimed, nil
}

func equalCanonical(left, right any) (bool, error) {
	leftBytes, err := CanonicalBytes(left)
	if err != nil {
		return false, err
	}
	rightBytes, err := CanonicalBytes(right)
	if err != nil {
		return false, err
	}
	return string(leftBytes) == string(rightBytes), nil
}

func expectEqualCanonical(left, right any, label string) error {
	equal, err := equalCanonical(left, right)
	if err != nil {
		return err
	}
	if !equal {
		return contractError("nakama_context_mismatch", "%s differs from authoritative Nakama selection", label)
	}
	return nil
}

const NakamaConsumerInputV1 = "trnm_nakama_world_runtime_consumer_input_v1"

var authorityContextFields = []string{
	"match_id",
	"authorization_id",
	"roster_hash",
	"next_global_event_sequence",
	"ruleset_id",
	"ruleset_version",
	"ruleset_digest",
	"content_digest",
}

// ParseAuthorityContext decodes a strict JSON object into the Nakama-owned
// authority context used to validate a World request/result pair.
func ParseAuthorityContext(value any) (AuthorityContext, error) {
	object, err := requireExactObject(value, authorityContextFields, "Nakama authority context")
	if err != nil {
		return AuthorityContext{}, err
	}
	matchID, err := requireString(object["match_id"], "match_id")
	if err != nil {
		return AuthorityContext{}, err
	}
	authorizationID, err := requireString(object["authorization_id"], "authorization_id")
	if err != nil {
		return AuthorityContext{}, err
	}
	rosterHash, err := requireHex64(object["roster_hash"], "roster_hash")
	if err != nil {
		return AuthorityContext{}, err
	}
	nextSequence, err := requireInt64(object["next_global_event_sequence"], "next_global_event_sequence")
	if err != nil {
		return AuthorityContext{}, err
	}
	rulesetID, err := requireIdentifier(object["ruleset_id"], "ruleset_id")
	if err != nil {
		return AuthorityContext{}, err
	}
	rulesetVersion, err := requireIdentifier(object["ruleset_version"], "ruleset_version")
	if err != nil {
		return AuthorityContext{}, err
	}
	rulesetDigest, err := requireHex64(object["ruleset_digest"], "ruleset_digest")
	if err != nil {
		return AuthorityContext{}, err
	}
	contentDigest, err := requireHex64(object["content_digest"], "content_digest")
	if err != nil {
		return AuthorityContext{}, err
	}
	context := AuthorityContext{
		MatchID:                 matchID,
		AuthorizationID:         authorizationID,
		RosterHash:              rosterHash,
		NextGlobalEventSequence: nextSequence,
		RulesetID:               rulesetID,
		RulesetVersion:          rulesetVersion,
		RulesetDigest:           rulesetDigest,
		ContentDigest:           contentDigest,
	}
	if err := validateContext(context); err != nil {
		return AuthorityContext{}, err
	}
	return context, nil
}
