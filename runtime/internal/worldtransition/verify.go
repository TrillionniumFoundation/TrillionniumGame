package worldtransition

import (
	"fmt"
	"unicode/utf8"
)

func VerifyResult(prepared Prepared, raw []byte) (Verified, error) {
	value, err := ParseCanonical(raw, true, MaxStateBytes+MaxReplayBytes+MaxOutcomeBytes+16384)
	if err != nil {
		return Verified{}, fmt.Errorf("%w: result: %v", ErrContract, err)
	}
	result, ok := value.(map[string]any)
	if !ok {
		return Verified{}, fmt.Errorf("%w: result root must be an object", ErrContract)
	}
	canonical, err := CanonicalJSON(result, true)
	if err != nil {
		return Verified{}, err
	}
	canonicalSHA := sha256Hex(canonical)
	if hasExactFields(result, acceptedFields) {
		return verifyAccepted(prepared, result, canonical, canonicalSHA)
	}
	if hasExactFields(result, rejectedFields) {
		return verifyRejected(prepared, result, canonical, canonicalSHA)
	}
	for key := range result {
		if _, forbidden := forbiddenAuthorityKeys[key]; forbidden {
			return Verified{}, fmt.Errorf("%w: result contains forbidden authority field %q", ErrContract, key)
		}
	}
	return Verified{}, fmt.Errorf("%w: result does not match accepted or rejected field set", ErrContract)
}

func verifyAccepted(prepared Prepared, result map[string]any, canonical []byte, canonicalSHA string) (Verified, error) {
	if _, err := requireExactObject(result, acceptedFields, "accepted World result"); err != nil {
		return Verified{}, err
	}
	if err := verifyIdentity(prepared, result); err != nil {
		return Verified{}, err
	}
	if result["ruleset_revision"] != prepared.Context.RulesetRevision || result["content_revision"] != prepared.Context.ContentRevision {
		return Verified{}, fmt.Errorf("%w: accepted result revision mismatch", ErrContract)
	}
	requestHash, err := requireHex64Value(result["request_hash"], "request_hash")
	if err != nil || requestHash != prepared.RequestHash {
		return Verified{}, fmt.Errorf("%w: request_hash mismatch", ErrContract)
	}
	previousStateHash, err := requireHex64Value(result["previous_state_hash"], "previous_state_hash")
	if err != nil || previousStateHash != prepared.PreviousStateHash {
		return Verified{}, fmt.Errorf("%w: previous_state_hash mismatch", ErrContract)
	}
	nextTick, ok := result["next_tick"].(int64)
	if !ok || nextTick < prepared.Context.ExpectedTick {
		return Verified{}, fmt.Errorf("%w: next_tick is invalid or regresses", ErrContract)
	}
	nextState, err := PayloadFromWire(result["next_state"], MaxStateBytes, "next_state")
	if err != nil {
		return Verified{}, err
	}
	replay, err := PayloadFromWire(result["replay_material"], MaxReplayBytes, "replay_material")
	if err != nil {
		return Verified{}, err
	}
	var outcome *CanonicalPayload
	var outcomeHash *string
	if result["outcome_material"] == nil {
		if result["world_outcome_hash"] != nil {
			return Verified{}, fmt.Errorf("%w: outcome hash present without outcome material", ErrContract)
		}
	} else {
		parsed, err := PayloadFromWire(result["outcome_material"], MaxOutcomeBytes, "outcome_material")
		if err != nil {
			return Verified{}, err
		}
		supplied, err := requireHex64Value(result["world_outcome_hash"], "world_outcome_hash")
		if err != nil {
			return Verified{}, err
		}
		expected, err := computeOutcomeHash(prepared.Context.RulesetRevision, prepared.Context.ContentRevision, parsed)
		if err != nil {
			return Verified{}, err
		}
		if supplied != expected {
			return Verified{}, fmt.Errorf("%w: world_outcome_hash mismatch", ErrContract)
		}
		outcome = &parsed
		outcomeHash = &supplied
	}
	transitionHash, err := requireHex64Value(result["world_transition_hash"], "world_transition_hash")
	if err != nil {
		return Verified{}, err
	}
	facts := map[string]any{
		"content_revision":    result["content_revision"],
		"contract_version":    result["contract_version"],
		"next_state":          result["next_state"],
		"next_tick":           result["next_tick"],
		"outcome_material":    result["outcome_material"],
		"previous_state_hash": result["previous_state_hash"],
		"replay_material":     result["replay_material"],
		"request_hash":        result["request_hash"],
		"ruleset_revision":    result["ruleset_revision"],
		"transition_id":       result["transition_id"],
		"world_outcome_hash":  result["world_outcome_hash"],
	}
	canonicalFacts, err := CanonicalJSON(facts, false)
	if err != nil {
		return Verified{}, err
	}
	expectedTransitionHash, err := domainHash(TransitionHashDomain, canonicalFacts)
	if err != nil {
		return Verified{}, err
	}
	if transitionHash != expectedTransitionHash {
		return Verified{}, fmt.Errorf("%w: world_transition_hash mismatch", ErrContract)
	}
	fingerprint, err := prepared.Context.Fingerprint()
	if err != nil {
		return Verified{}, err
	}
	nextTickCopy := nextTick
	previousCopy := previousStateHash
	transitionCopy := transitionHash
	return Verified{
		Context:                     prepared.Context,
		AuthorityContextFingerprint: fingerprint,
		RequestHash:                 requestHash,
		TransitionID:                prepared.TransitionID,
		Disposition:                 DispositionAccepted,
		NextTick:                    &nextTickCopy,
		PreviousStateHash:           &previousCopy,
		NextState:                   &nextState,
		ReplayMaterial:              &replay,
		OutcomeMaterial:             outcome,
		WorldOutcomeHash:            outcomeHash,
		WorldTransitionHash:         &transitionCopy,
		CanonicalResult:             canonical,
		CanonicalResultSHA256:       canonicalSHA,
	}, nil
}

func verifyRejected(prepared Prepared, result map[string]any, canonical []byte, canonicalSHA string) (Verified, error) {
	if _, err := requireExactObject(result, rejectedFields, "rejected World result"); err != nil {
		return Verified{}, err
	}
	if err := verifyIdentity(prepared, result); err != nil {
		return Verified{}, err
	}
	code, ok := result["code"].(string)
	if !ok {
		return Verified{}, fmt.Errorf("%w: rejection code must be a string", ErrContract)
	}
	if _, known := stableErrorCodes[code]; !known {
		return Verified{}, fmt.Errorf("%w: rejection code is not stable", ErrContract)
	}
	retryable, ok := result["retryable"].(bool)
	if !ok || retryable != (code == "internal_unavailable") {
		return Verified{}, fmt.Errorf("%w: retryable disagrees with stable error catalogue", ErrContract)
	}
	requestHash, err := requireHex64Value(result["request_hash"], "request_hash")
	if err != nil || requestHash != prepared.RequestHash {
		return Verified{}, fmt.Errorf("%w: rejection request_hash mismatch", ErrContract)
	}
	detail, ok := result["detail"].(string)
	if !ok || !boundedDetail(detail) {
		return Verified{}, fmt.Errorf("%w: rejection detail is not bounded text", ErrContract)
	}
	fingerprint, err := prepared.Context.Fingerprint()
	if err != nil {
		return Verified{}, err
	}
	codeCopy := code
	retryCopy := retryable
	return Verified{
		Context:                     prepared.Context,
		AuthorityContextFingerprint: fingerprint,
		RequestHash:                 requestHash,
		TransitionID:                prepared.TransitionID,
		Disposition:                 DispositionRejected,
		ErrorCode:                   &codeCopy,
		Retryable:                   &retryCopy,
		CanonicalResult:             canonical,
		CanonicalResultSHA256:       canonicalSHA,
	}, nil
}

func verifyIdentity(prepared Prepared, result map[string]any) error {
	if result["contract_version"] != ContractVersion {
		return fmt.Errorf("%w: result contract version mismatch", ErrContract)
	}
	if result["transition_id"] != prepared.TransitionID {
		return fmt.Errorf("%w: result transition_id mismatch", ErrContract)
	}
	return nil
}

func computeOutcomeHash(rulesetRevision, contentRevision string, outcome CanonicalPayload) (string, error) {
	binding, err := CanonicalJSON(map[string]any{
		"content_revision":     contentRevision,
		"outcome_payload_hash": outcome.SHA256,
		"outcome_schema_id":    outcome.SchemaID,
		"ruleset_revision":     rulesetRevision,
	}, false)
	if err != nil {
		return "", err
	}
	return domainHash(OutcomeHashDomain, binding)
}

func hasExactFields(object map[string]any, expected map[string]struct{}) bool {
	if len(object) != len(expected) {
		return false
	}
	for key := range object {
		if _, ok := expected[key]; !ok {
			return false
		}
	}
	return true
}

func requireHex64Value(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || !hex64Pattern.MatchString(text) {
		return "", fmt.Errorf("%w: %s must be lowercase 64-hex", ErrContract, label)
	}
	return text, nil
}

func boundedDetail(value string) bool {
	if value == "" || len(value) > 256 || !utf8.ValidString(value) {
		return false
	}
	for _, r := range value {
		if r < 0x20 || r == 0x7f {
			return false
		}
	}
	return true
}
