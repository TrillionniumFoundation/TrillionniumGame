package worldruntimev1

// BuildObservation verifies a World response inside the authoritative Nakama
// context and emits the candidate observation consumed by the World shadow
// comparator. Duration is measured by the caller and has no game-domain effect.
func BuildObservation(
	context AuthorityContext,
	implementationID string,
	implementationRevision string,
	requestValue any,
	responseValue any,
	durationMicros int64,
) (map[string]any, VerifiedExecution, error) {
	if !identifierPattern.MatchString(implementationID) {
		return nil, VerifiedExecution{}, contractError("invalid_contract", "implementation_id is not portable")
	}
	if !hex40Pattern.MatchString(implementationRevision) {
		return nil, VerifiedExecution{}, contractError("invalid_contract", "implementation_revision must be lowercase 40-hex")
	}
	if durationMicros < 0 {
		return nil, VerifiedExecution{}, contractError("invalid_contract", "duration_micros must be non-negative")
	}
	verified, err := VerifyResponse(context, requestValue, responseValue)
	if err != nil {
		return nil, VerifiedExecution{}, err
	}
	requestHash, err := DomainHash(ShadowRequestDomain, requestValue)
	if err != nil {
		return nil, VerifiedExecution{}, err
	}
	responseBytes, err := CanonicalBytes(responseValue)
	if err != nil {
		return nil, VerifiedExecution{}, err
	}
	observation := map[string]any{
		"contract_version":        RuntimeObservationVersion,
		"implementation_id":       implementationID,
		"implementation_revision": implementationRevision,
		"request_hash":            requestHash,
		"response":                responseValue,
		"duration_micros":         durationMicros,
		"response_bytes":          int64(len(responseBytes)),
	}
	if _, err := CanonicalBytes(observation); err != nil {
		return nil, VerifiedExecution{}, err
	}
	return observation, verified, nil
}

// ConsumerReport produces a Nakama-owned evidence envelope without completion
// signing or mutation of the authoritative global sequence.
func ConsumerReport(context AuthorityContext, observation map[string]any, verified VerifiedExecution) map[string]any {
	return map[string]any{
		"contract_version": NakamaConsumerReportV1,
		"authority_context": map[string]any{
			"match_id":                   context.MatchID,
			"authorization_id":           context.AuthorizationID,
			"roster_hash":                context.RosterHash,
			"next_global_event_sequence": context.NextGlobalEventSequence,
		},
		"observation": observation,
		"verified": map[string]any{
			"kind":                 verified.Kind,
			"ruleset_digest":       verified.RulesetDigest,
			"content_digest":       verified.ContentDigest,
			"initial_state_hash":   verified.InitialStateHash,
			"command_batch_hash":   verified.CommandBatchHash,
			"final_state_hash":     verified.FinalStateHash,
			"outcome_hash":         verified.OutcomeHash,
			"replay_material_hash": verified.ReplayMaterialHash,
			"error_code":           verified.ErrorCode,
			"recoverable":          verified.Recoverable,
		},
		"authority_claims": map[string]any{
			"world_batch_ordinal_used_as_global_sequence": false,
			"world_participant_authority_accepted":        false,
			"world_canonical_roots_accepted":              false,
			"completion_signing_performed":                false,
			"chain_finality_claimed":                      false,
			"cex_custody_claimed":                         false,
		},
		"limitations": []any{
			"This report verifies unsigned World game-domain material only.",
			"Nakama admission, ordering, idempotency, recovery, roots and completion signing remain separate authority state machines.",
			"Integration must bind exact World and Nakama revisions before cross-repository credit.",
		},
	}
}
