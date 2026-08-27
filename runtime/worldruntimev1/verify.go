package worldruntimev1

import "fmt"

var requestFields = []string{
	"contract_version",
	"message_type",
	"ruleset",
	"content_digest",
	"initial_state",
	"commands",
}
var resultFields = []string{
	"contract_version",
	"message_type",
	"ruleset",
	"content_digest",
	"initial_state_hash",
	"command_batch_hash",
	"final_state",
	"final_state_hash",
	"outcome",
	"outcome_hash",
	"replay_material",
	"replay_material_hash",
}
var rulesetFields = []string{"id", "version", "digest"}
var commandFields = []string{"batch_ordinal", "kind", "payload"}
var errorFields = []string{"contract_version", "error_code", "error", "recoverable"}

// ValidateRequest validates the language-neutral World request and confirms its
// exact ruleset/content selection against the already authoritative Nakama
// context. Batch ordinals remain request-local positions only.
func ValidateRequest(context AuthorityContext, requestValue any) (map[string]any, error) {
	if err := validateContext(context); err != nil {
		return nil, err
	}
	if err := rejectAuthorityFields(requestValue, "World request"); err != nil {
		return nil, err
	}
	request, err := requireExactObject(requestValue, requestFields, "World request")
	if err != nil {
		return nil, err
	}
	version, err := requireString(request["contract_version"], "contract_version")
	if err != nil {
		return nil, err
	}
	if version != RuntimeContractVersion {
		return nil, contractError("unsupported_contract", "unsupported World request contract %s", version)
	}
	messageType, err := requireString(request["message_type"], "message_type")
	if err != nil {
		return nil, err
	}
	if messageType != ExecuteRequestType {
		return nil, contractError("invalid_contract", "message_type must be %s", ExecuteRequestType)
	}
	ruleset, err := requireExactObject(request["ruleset"], rulesetFields, "World ruleset")
	if err != nil {
		return nil, err
	}
	rulesetID, err := requireIdentifier(ruleset["id"], "ruleset.id")
	if err != nil {
		return nil, err
	}
	rulesetVersion, err := requireIdentifier(ruleset["version"], "ruleset.version")
	if err != nil {
		return nil, err
	}
	rulesetDigest, err := requireHex64(ruleset["digest"], "ruleset.digest")
	if err != nil {
		return nil, err
	}
	contentDigest, err := requireHex64(request["content_digest"], "content_digest")
	if err != nil {
		return nil, err
	}
	if rulesetID != context.RulesetID || rulesetVersion != context.RulesetVersion || rulesetDigest != context.RulesetDigest {
		return nil, contractError("nakama_context_mismatch", "World ruleset differs from authoritative Nakama selection")
	}
	if contentDigest != context.ContentDigest {
		return nil, contractError("nakama_context_mismatch", "World content differs from authoritative Nakama selection")
	}
	if _, err := CanonicalBytes(request["initial_state"]); err != nil {
		return nil, err
	}
	commands, ok := request["commands"].([]any)
	if !ok {
		return nil, contractError("invalid_contract", "commands must be an array")
	}
	for index, raw := range commands {
		command, err := requireExactObject(raw, commandFields, fmt.Sprintf("World command %d", index))
		if err != nil {
			return nil, err
		}
		ordinal, err := requireInt64(command["batch_ordinal"], "batch_ordinal")
		if err != nil {
			return nil, contractError("ordinal_discontinuity", "%v", err)
		}
		if ordinal != int64(index) {
			return nil, contractError("ordinal_discontinuity", "batch ordinals must be contiguous from zero")
		}
		if _, err := requireIdentifier(command["kind"], "command.kind"); err != nil {
			return nil, err
		}
		if err := rejectAuthorityFields(command["payload"], "World command payload"); err != nil {
			return nil, err
		}
		if _, err := CanonicalBytes(command["payload"]); err != nil {
			return nil, err
		}
	}
	if _, err := CanonicalBytes(commands); err != nil {
		return nil, err
	}
	return request, nil
}

// VerifyResponse independently verifies unsigned World success or deterministic
// rejection material without constructing canonical completion evidence.
func VerifyResponse(context AuthorityContext, requestValue, responseValue any) (VerifiedExecution, error) {
	request, err := ValidateRequest(context, requestValue)
	if err != nil {
		return VerifiedExecution{}, err
	}
	if err := rejectAuthorityFields(responseValue, "World response"); err != nil {
		return VerifiedExecution{}, err
	}
	response, ok := responseValue.(map[string]any)
	if !ok {
		return VerifiedExecution{}, contractError("invalid_contract", "World response must be an object")
	}
	version, err := requireString(response["contract_version"], "response.contract_version")
	if err != nil {
		return VerifiedExecution{}, err
	}
	base := VerifiedExecution{
		MatchID:                 context.MatchID,
		AuthorizationID:         context.AuthorizationID,
		RosterHash:              context.RosterHash,
		NextGlobalEventSequence: context.NextGlobalEventSequence,
		RulesetDigest:           context.RulesetDigest,
		ContentDigest:           context.ContentDigest,
	}
	switch version {
	case RuntimeContractVersion:
		result, err := requireExactObject(response, resultFields, "World execute result")
		if err != nil {
			return VerifiedExecution{}, err
		}
		messageType, err := requireString(result["message_type"], "response.message_type")
		if err != nil {
			return VerifiedExecution{}, err
		}
		if messageType != ExecuteResultType {
			return VerifiedExecution{}, contractError("invalid_contract", "response.message_type must be %s", ExecuteResultType)
		}
		resultRuleset, err := requireExactObject(result["ruleset"], rulesetFields, "World result ruleset")
		if err != nil {
			return VerifiedExecution{}, err
		}
		if err := expectEqualCanonical(request["ruleset"], resultRuleset, "World result ruleset"); err != nil {
			return VerifiedExecution{}, err
		}
		if err := expectEqualCanonical(request["content_digest"], result["content_digest"], "World result content_digest"); err != nil {
			return VerifiedExecution{}, err
		}
		initialStateHash, err := DomainHash(InitialStateDomain, request["initial_state"])
		if err != nil {
			return VerifiedExecution{}, err
		}
		commandBatchHash, err := DomainHash(CommandBatchDomain, request["commands"])
		if err != nil {
			return VerifiedExecution{}, err
		}
		claimedInitialState, err := requireHex64(result["initial_state_hash"], "initial_state_hash")
		if err != nil {
			return VerifiedExecution{}, err
		}
		if claimedInitialState != initialStateHash {
			return VerifiedExecution{}, contractError("output_contract_violation", "initial_state_hash does not bind request initial_state")
		}
		claimedCommandBatch, err := requireHex64(result["command_batch_hash"], "command_batch_hash")
		if err != nil {
			return VerifiedExecution{}, err
		}
		if claimedCommandBatch != commandBatchHash {
			return VerifiedExecution{}, contractError("output_contract_violation", "command_batch_hash does not bind request commands")
		}
		finalStateHash, err := verifyClaimedHash(result, "final_state", "final_state_hash", FinalStateDomain)
		if err != nil {
			return VerifiedExecution{}, err
		}
		outcomeHash, err := verifyClaimedHash(result, "outcome", "outcome_hash", OutcomeDomain)
		if err != nil {
			return VerifiedExecution{}, err
		}
		replayHash, err := verifyClaimedHash(result, "replay_material", "replay_material_hash", ReplayMaterialDomain)
		if err != nil {
			return VerifiedExecution{}, err
		}
		base.Kind = "success"
		base.InitialStateHash = initialStateHash
		base.CommandBatchHash = commandBatchHash
		base.FinalStateHash = finalStateHash
		base.OutcomeHash = outcomeHash
		base.ReplayMaterialHash = replayHash
		return base, nil
	case RuntimeErrorVersion:
		errorEnvelope, err := requireExactObject(response, errorFields, "World runtime error")
		if err != nil {
			return VerifiedExecution{}, err
		}
		code, err := requireIdentifier(errorEnvelope["error_code"], "error_code")
		if err != nil {
			return VerifiedExecution{}, err
		}
		if _, stable := stableRuntimeErrors[code]; !stable {
			return VerifiedExecution{}, contractError("invalid_contract", "unknown World runtime error code %s", code)
		}
		message, err := requireString(errorEnvelope["error"], "error")
		if err != nil {
			return VerifiedExecution{}, err
		}
		if len(message) == 0 || len(message) > 4096 {
			return VerifiedExecution{}, contractError("invalid_contract", "error message must contain 1..=4096 bytes")
		}
		recoverable, ok := errorEnvelope["recoverable"].(bool)
		if !ok {
			return VerifiedExecution{}, contractError("invalid_contract", "recoverable must be a boolean")
		}
		base.Kind = "error"
		base.ErrorCode = code
		base.Recoverable = recoverable
		return base, nil
	default:
		return VerifiedExecution{}, contractError("unsupported_contract", "unsupported World response contract %s", version)
	}
}
