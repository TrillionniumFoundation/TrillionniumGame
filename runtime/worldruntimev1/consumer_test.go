package worldruntimev1

import (
	"strings"
	"testing"
)

func testContext() AuthorityContext {
	return AuthorityContext{
		MatchID:                 "nakama-match-0001",
		AuthorizationID:         "nakama-authorization-0001",
		RosterHash:              strings.Repeat("3", 64),
		NextGlobalEventSequence: 9_001,
		RulesetID:               "fixture",
		RulesetVersion:          "1",
		RulesetDigest:           strings.Repeat("1", 64),
		ContentDigest:           strings.Repeat("2", 64),
	}
}

func testRequest() map[string]any {
	return map[string]any{
		"contract_version": RuntimeContractVersion,
		"message_type":     ExecuteRequestType,
		"ruleset": map[string]any{
			"id":      "fixture",
			"version": "1",
			"digest":  strings.Repeat("1", 64),
		},
		"content_digest": strings.Repeat("2", 64),
		"initial_state": map[string]any{
			"tick":  int64(0),
			"value": int64(0),
		},
		"commands": []any{
			map[string]any{
				"batch_ordinal": int64(0),
				"kind":          "advance",
				"payload": map[string]any{
					"target_tick": int64(2),
				},
			},
		},
	}
}

func testSuccess(t *testing.T, value int64) map[string]any {
	t.Helper()
	request := testRequest()
	finalState := map[string]any{"tick": int64(2), "value": value}
	outcome := map[string]any{"terminal": true, "value": value}
	replay := map[string]any{"commands": int64(1), "final_tick": int64(2)}
	initialStateHash, err := DomainHash(InitialStateDomain, request["initial_state"])
	if err != nil {
		t.Fatal(err)
	}
	commandBatchHash, err := DomainHash(CommandBatchDomain, request["commands"])
	if err != nil {
		t.Fatal(err)
	}
	finalStateHash, err := DomainHash(FinalStateDomain, finalState)
	if err != nil {
		t.Fatal(err)
	}
	outcomeHash, err := DomainHash(OutcomeDomain, outcome)
	if err != nil {
		t.Fatal(err)
	}
	replayHash, err := DomainHash(ReplayMaterialDomain, replay)
	if err != nil {
		t.Fatal(err)
	}
	return map[string]any{
		"contract_version":     RuntimeContractVersion,
		"message_type":         ExecuteResultType,
		"ruleset":              request["ruleset"],
		"content_digest":       request["content_digest"],
		"initial_state_hash":   initialStateHash,
		"command_batch_hash":   commandBatchHash,
		"final_state":          finalState,
		"final_state_hash":     finalStateHash,
		"outcome":              outcome,
		"outcome_hash":         outcomeHash,
		"replay_material":      replay,
		"replay_material_hash": replayHash,
	}
}

func errorCode(err error) string {
	if typed, ok := err.(*ContractError); ok {
		return typed.Code
	}
	return ""
}

func TestCanonicalJSONAndDomainHash(t *testing.T) {
	value, err := ParseStrict([]byte(`{"b":2,"a":"e\u0301","raw":"<>& "}`))
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := CanonicalBytes(value)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := string(canonical), `{"a":"é","b":2,"raw":"<>& "}`; got != want {
		t.Fatalf("canonical mismatch\n got: %s\nwant: %s", got, want)
	}
	object, err := ParseStrict([]byte(`{"b":2,"a":1}`))
	if err != nil {
		t.Fatal(err)
	}
	hash, err := DomainHash("fixture", object)
	if err != nil {
		t.Fatal(err)
	}
	if len(hash) != 64 {
		t.Fatalf("unexpected hash %q", hash)
	}
}

func TestStrictParserRejectsAmbiguousJSON(t *testing.T) {
	tests := []struct {
		name string
		data []byte
		code string
	}{
		{"duplicate", []byte(`{"a":1,"a":2}`), "invalid_canonical_json"},
		{"nfc collision", []byte("{\"é\":1,\"e\\u0301\":2}"), "invalid_canonical_json"},
		{"float", []byte(`{"a":1.0}`), "invalid_canonical_json"},
		{"exponent", []byte(`{"a":1e2}`), "invalid_canonical_json"},
		{"overflow", []byte(`{"a":9223372036854775808}`), "invalid_canonical_json"},
		{"invalid utf8", []byte{'{', '"', 'a', '"', ':', '"', 0xff, '"', '}'}, "invalid_canonical_json"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := ParseStrict(test.data)
			if err == nil || errorCode(err) != test.code {
				t.Fatalf("expected %s, got %v", test.code, err)
			}
		})
	}
}

func TestVerifySuccessPreservesNakamaGlobalAuthority(t *testing.T) {
	context := testContext()
	verified, err := VerifyResponse(context, testRequest(), testSuccess(t, 7))
	if err != nil {
		t.Fatal(err)
	}
	if verified.Kind != "success" {
		t.Fatalf("unexpected kind %s", verified.Kind)
	}
	if verified.NextGlobalEventSequence != context.NextGlobalEventSequence {
		t.Fatal("World request changed Nakama global sequence")
	}
	if verified.OutcomeHash == "" || verified.ReplayMaterialHash == "" {
		t.Fatal("verified material hashes are missing")
	}
}

func TestTamperedAndAuthorityMaterialFailClosed(t *testing.T) {
	response := testSuccess(t, 7)
	response["outcome"].(map[string]any)["value"] = int64(8)
	if _, err := VerifyResponse(testContext(), testRequest(), response); errorCode(err) != "output_contract_violation" {
		t.Fatalf("expected output_contract_violation, got %v", err)
	}

	response = testSuccess(t, 7)
	response["outcome"].(map[string]any)["completion_signature"] = "forbidden"
	if _, err := VerifyResponse(testContext(), testRequest(), response); errorCode(err) != "authority_boundary_violation" {
		t.Fatalf("expected authority_boundary_violation, got %v", err)
	}
}

func TestOrdinalAndContextMismatchFailClosed(t *testing.T) {
	request := testRequest()
	request["commands"] = append(request["commands"].([]any), map[string]any{
		"batch_ordinal": int64(2),
		"kind":          "advance",
		"payload":       map[string]any{"target_tick": int64(3)},
	})
	if _, err := VerifyResponse(testContext(), request, testSuccess(t, 7)); errorCode(err) != "ordinal_discontinuity" {
		t.Fatalf("expected ordinal_discontinuity, got %v", err)
	}

	context := testContext()
	context.ContentDigest = strings.Repeat("4", 64)
	if _, err := VerifyResponse(context, testRequest(), testSuccess(t, 7)); errorCode(err) != "nakama_context_mismatch" {
		t.Fatalf("expected nakama_context_mismatch, got %v", err)
	}
}

func TestStableDeterministicErrorsAndUnknownCode(t *testing.T) {
	response := map[string]any{
		"contract_version": RuntimeErrorVersion,
		"error_code":       "invalid_game_command",
		"error":            "diagnostic wording",
		"recoverable":      false,
	}
	verified, err := VerifyResponse(testContext(), testRequest(), response)
	if err != nil {
		t.Fatal(err)
	}
	if verified.Kind != "error" || verified.ErrorCode != "invalid_game_command" {
		t.Fatalf("unexpected verified error: %+v", verified)
	}
	response["error_code"] = "invented_error"
	if _, err := VerifyResponse(testContext(), testRequest(), response); errorCode(err) != "invalid_contract" {
		t.Fatalf("expected invalid_contract for unknown code, got %v", err)
	}
}

func TestObservationBindsRequestResponseAndAuthorityNonClaims(t *testing.T) {
	context := testContext()
	observation, verified, err := BuildObservation(
		context,
		"nakama-go-consumer",
		strings.Repeat("b", 40),
		testRequest(),
		testSuccess(t, 7),
		int64(11),
	)
	if err != nil {
		t.Fatal(err)
	}
	responseBytes, err := CanonicalBytes(observation["response"])
	if err != nil {
		t.Fatal(err)
	}
	if observation["response_bytes"] != int64(len(responseBytes)) {
		t.Fatal("response byte binding mismatch")
	}
	expectedRequestHash, err := DomainHash(ShadowRequestDomain, testRequest())
	if err != nil {
		t.Fatal(err)
	}
	if observation["request_hash"] != expectedRequestHash {
		t.Fatal("request hash binding mismatch")
	}
	report := ConsumerReport(context, observation, verified)
	claims := report["authority_claims"].(map[string]any)
	for claim, value := range claims {
		if value != false {
			t.Fatalf("authority claim %s must remain false", claim)
		}
	}
}
