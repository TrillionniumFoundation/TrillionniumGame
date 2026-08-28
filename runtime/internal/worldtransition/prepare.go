package worldtransition

import "fmt"

func Prepare(
	context AuthorityContext,
	previousStateSchemaID string,
	previousState any,
	commandSchemaID string,
	command any,
) (Prepared, error) {
	if err := context.Validate(); err != nil {
		return Prepared{}, err
	}
	binding, err := context.Binding()
	if err != nil {
		return Prepared{}, err
	}
	transitionID, err := opaqueIdentifier("wtx-", TransitionIDDomain, binding)
	if err != nil {
		return Prepared{}, err
	}
	commandID, err := opaqueIdentifier("wcmd-", CommandIDDomain, binding)
	if err != nil {
		return Prepared{}, err
	}
	statePayload, err := NewCanonicalPayload(previousState, previousStateSchemaID, MaxStateBytes, "previous_state")
	if err != nil {
		return Prepared{}, err
	}
	commandPayload, err := NewCanonicalPayload(command, commandSchemaID, MaxCommandBytes, "command.payload")
	if err != nil {
		return Prepared{}, err
	}
	request := map[string]any{
		"command": map[string]any{
			"command_id": commandID,
			"payload":    commandPayload.Wire(),
		},
		"content_revision": context.ContentRevision,
		"contract_version": ContractVersion,
		"expected_tick":    context.ExpectedTick,
		"previous_state":   statePayload.Wire(),
		"ruleset_revision": context.RulesetRevision,
		"transition_id":    transitionID,
	}
	if _, err := requireExactObject(request, requestFields, "World request"); err != nil {
		return Prepared{}, err
	}
	canonical, err := CanonicalJSON(request, true)
	if err != nil {
		return Prepared{}, err
	}
	requestHash, err := domainHash(RequestHashDomain, canonical)
	if err != nil {
		return Prepared{}, err
	}
	return Prepared{
		Context:           context,
		Request:           request,
		CanonicalRequest:  canonical,
		RequestHash:       requestHash,
		TransitionID:      transitionID,
		CommandID:         commandID,
		PreviousStateHash: statePayload.SHA256,
	}, nil
}

func PreparedFromCanonicalRequest(context AuthorityContext, raw []byte) (Prepared, error) {
	if err := context.Validate(); err != nil {
		return Prepared{}, err
	}
	value, err := ParseCanonical(raw, true, MaxStateBytes+MaxCommandBytes+4096)
	if err != nil {
		return Prepared{}, fmt.Errorf("%w: request: %v", ErrContract, err)
	}
	request, err := requireExactObject(value, requestFields, "World request")
	if err != nil {
		return Prepared{}, err
	}
	if request["contract_version"] != ContractVersion {
		return Prepared{}, fmt.Errorf("%w: contract version mismatch", ErrContract)
	}
	if request["ruleset_revision"] != context.RulesetRevision || request["content_revision"] != context.ContentRevision {
		return Prepared{}, fmt.Errorf("%w: request revision mismatch", ErrContract)
	}
	if tick, ok := request["expected_tick"].(int64); !ok || tick != context.ExpectedTick {
		return Prepared{}, fmt.Errorf("%w: request expected_tick mismatch", ErrContract)
	}
	binding, err := context.Binding()
	if err != nil {
		return Prepared{}, err
	}
	expectedTransitionID, err := opaqueIdentifier("wtx-", TransitionIDDomain, binding)
	if err != nil {
		return Prepared{}, err
	}
	expectedCommandID, err := opaqueIdentifier("wcmd-", CommandIDDomain, binding)
	if err != nil {
		return Prepared{}, err
	}
	transitionID, ok := request["transition_id"].(string)
	if !ok || transitionID != expectedTransitionID {
		return Prepared{}, fmt.Errorf("%w: transition_id is not bound to context", ErrContract)
	}
	commandObject, err := requireExactObject(request["command"], commandFields, "World command")
	if err != nil {
		return Prepared{}, err
	}
	commandID, ok := commandObject["command_id"].(string)
	if !ok || commandID != expectedCommandID {
		return Prepared{}, fmt.Errorf("%w: command_id is not bound to context", ErrContract)
	}
	previousState, err := PayloadFromWire(request["previous_state"], MaxStateBytes, "previous_state")
	if err != nil {
		return Prepared{}, err
	}
	if _, err := PayloadFromWire(commandObject["payload"], MaxCommandBytes, "command.payload"); err != nil {
		return Prepared{}, err
	}
	canonical, err := CanonicalJSON(request, true)
	if err != nil {
		return Prepared{}, err
	}
	requestHash, err := domainHash(RequestHashDomain, canonical)
	if err != nil {
		return Prepared{}, err
	}
	return Prepared{
		Context:           context,
		Request:           request,
		CanonicalRequest:  canonical,
		RequestHash:       requestHash,
		TransitionID:      expectedTransitionID,
		CommandID:         expectedCommandID,
		PreviousStateHash: previousState.SHA256,
	}, nil
}

func opaqueIdentifier(prefix, domain string, binding []byte) (string, error) {
	digest, err := domainHash(domain, binding)
	if err != nil {
		return "", err
	}
	return prefix + digest[:48], nil
}
