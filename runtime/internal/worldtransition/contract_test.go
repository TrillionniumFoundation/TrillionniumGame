package worldtransition

import (
	"bytes"
	"strings"
	"testing"
)

func fixtureContext(sequence int64) AuthorityContext {
	return AuthorityContext{
		MatchID:               "match-accepted",
		AuthorizationID:       "authorization-accepted",
		ParticipantRosterHash: strings.Repeat("3", 64),
		MatchVersion:          1,
		GlobalEventSequence:   sequence,
		CommandIdempotencyKey: "idempotency-accepted",
		RulesetRevision:       "trnm-rts-rules-v1",
		ContentRevision:       "first-contact-content-v1",
		ExpectedTick:          120,
	}
}

func fixturePrepared(t *testing.T) Prepared {
	t.Helper()
	prepared, err := Prepare(
		fixtureContext(9001),
		"trnm.rts.state.v1",
		map[string]any{"tick": int64(120), "units": []any{map[string]any{"hp": int64(10), "id": "alpha"}}},
		"trnm.rts.order.v1",
		map[string]any{"kind": "hold", "unit_id": "alpha"},
	)
	if err != nil {
		t.Fatal(err)
	}
	return prepared
}

func TestCanonicalStrictness(t *testing.T) {
	valid := []byte(`{"a":[1,true,null,"é"],"b":{"c":"x"}}`)
	value, err := ParseCanonical(valid, true, len(valid))
	if err != nil {
		t.Fatalf("valid canonical JSON rejected: %v", err)
	}
	reencoded, err := CanonicalJSON(value, true)
	if err != nil || !bytes.Equal(valid, reencoded) {
		t.Fatalf("round trip mismatch: %s %v", reencoded, err)
	}

	invalid := []string{
		` {"a":1}`,
		`{"b":1,"a":2}`,
		`{"a":1,"a":1}`,
		`{"a":1.0}`,
		`{"a":1e0}`,
		`{"a":-0}`,
		`{"a":"\u0061"}`,
		`{"a":9223372036854775808}`,
	}
	for _, raw := range invalid {
		if _, err := ParseCanonical([]byte(raw), true, -1); err == nil {
			t.Fatalf("noncanonical input accepted: %s", raw)
		}
	}
}

func TestPrepareMatchesLockedFixture(t *testing.T) {
	prepared := fixturePrepared(t)
	if prepared.CommandID != "wcmd-6a3de61fdbd12c0e1174f500db10e56d3ba8a6b3038ee76e" {
		t.Fatalf("command ID mismatch: %s", prepared.CommandID)
	}
	if prepared.TransitionID != "wtx-455664ac7d0fe188a535f9058277599ba81d30268b6474ba" {
		t.Fatalf("transition ID mismatch: %s", prepared.TransitionID)
	}
	if prepared.RequestHash != "06ff6ecb203fbe49733edea9c3c0eb6499604e0b430cefcdfbc25ee2991c989a" {
		t.Fatalf("request hash mismatch: %s\n%s", prepared.RequestHash, prepared.CanonicalRequest)
	}
	recovered, err := PreparedFromCanonicalRequest(prepared.Context, prepared.CanonicalRequest)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(recovered.CanonicalRequest, prepared.CanonicalRequest) || recovered.RequestHash != prepared.RequestHash {
		t.Fatal("prepared request changed across reconstruction")
	}
}

func acceptedResult(t *testing.T, prepared Prepared) []byte {
	t.Helper()
	nextState, err := NewCanonicalPayload(
		map[string]any{"tick": int64(121), "units": []any{map[string]any{"hp": int64(10), "id": "alpha"}}},
		"trnm.rts.state.v1", MaxStateBytes, "next_state",
	)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := NewCanonicalPayload(
		map[string]any{"applied_command_ids": []any{prepared.CommandID}, "tick": int64(121)},
		"trnm.rts.replay.v1", MaxReplayBytes, "replay_material",
	)
	if err != nil {
		t.Fatal(err)
	}
	outcome, err := NewCanonicalPayload(
		map[string]any{"result": "held", "score": int64(10)},
		"trnm.rts.outcome.v1", MaxOutcomeBytes, "outcome_material",
	)
	if err != nil {
		t.Fatal(err)
	}
	outcomeHash, err := computeOutcomeHash(prepared.Context.RulesetRevision, prepared.Context.ContentRevision, outcome)
	if err != nil {
		t.Fatal(err)
	}
	facts := map[string]any{
		"content_revision":    prepared.Context.ContentRevision,
		"contract_version":    ContractVersion,
		"next_state":          nextState.Wire(),
		"next_tick":           int64(121),
		"outcome_material":    outcome.Wire(),
		"previous_state_hash": prepared.PreviousStateHash,
		"replay_material":     replay.Wire(),
		"request_hash":        prepared.RequestHash,
		"ruleset_revision":    prepared.Context.RulesetRevision,
		"transition_id":       prepared.TransitionID,
		"world_outcome_hash":  outcomeHash,
	}
	factsCanonical, err := CanonicalJSON(facts, false)
	if err != nil {
		t.Fatal(err)
	}
	transitionHash, err := domainHash(TransitionHashDomain, factsCanonical)
	if err != nil {
		t.Fatal(err)
	}
	result := make(map[string]any, len(facts)+1)
	for key, value := range facts {
		result[key] = value
	}
	result["world_transition_hash"] = transitionHash
	encoded, err := CanonicalJSON(result, true)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func TestVerifyAcceptedAndRejected(t *testing.T) {
	prepared := fixturePrepared(t)
	verified, err := VerifyResult(prepared, acceptedResult(t, prepared))
	if err != nil {
		t.Fatal(err)
	}
	if verified.Disposition != DispositionAccepted || verified.NextState == nil || verified.ReplayMaterial == nil || verified.WorldTransitionHash == nil {
		t.Fatalf("accepted result lost verified material: %+v", verified)
	}

	rejected, err := CanonicalJSON(map[string]any{
		"code":             "domain_rejected",
		"contract_version": ContractVersion,
		"detail":           "order rejected by deterministic rules",
		"request_hash":     prepared.RequestHash,
		"retryable":        false,
		"transition_id":    prepared.TransitionID,
	}, true)
	if err != nil {
		t.Fatal(err)
	}
	verifiedRejected, err := VerifyResult(prepared, rejected)
	if err != nil {
		t.Fatal(err)
	}
	if verifiedRejected.Disposition != DispositionRejected || verifiedRejected.ErrorCode == nil || *verifiedRejected.ErrorCode != "domain_rejected" {
		t.Fatalf("rejected result mismatch: %+v", verifiedRejected)
	}
}

func TestTamperAndAuthoritySmugglingFailClosed(t *testing.T) {
	prepared := fixturePrepared(t)
	accepted := acceptedResult(t, prepared)
	value, err := ParseCanonical(accepted, true, -1)
	if err != nil {
		t.Fatal(err)
	}
	result := value.(map[string]any)
	result["request_hash"] = strings.Repeat("0", 64)
	tampered, _ := CanonicalJSON(result, true)
	if _, err := VerifyResult(prepared, tampered); err == nil {
		t.Fatal("tampered request hash accepted")
	}

	if _, err := Prepare(prepared.Context, "trnm.rts.state.v1", map[string]any{"global_event_cursor": int64(1)}, "trnm.rts.order.v1", map[string]any{}); err == nil {
		t.Fatal("authority key crossed into World state")
	}

	value, _ = ParseCanonical(accepted, true, -1)
	result = value.(map[string]any)
	result["completion_signature"] = "forbidden"
	withAuthority, _ := CanonicalJSON(result, true)
	if _, err := VerifyResult(prepared, withAuthority); err == nil {
		t.Fatal("authority field in result accepted")
	}
}

func TestObservationComparison(t *testing.T) {
	prepared := fixturePrepared(t)
	verified, err := VerifyResult(prepared, acceptedResult(t, prepared))
	if err != nil {
		t.Fatal(err)
	}
	world, err := Observe("fixture-1", "world-reference", "rev-1", 1, verified)
	if err != nil {
		t.Fatal(err)
	}
	candidate, err := Observe("fixture-1", "nakama-go", "rev-2", 2, verified)
	if err != nil {
		t.Fatal(err)
	}
	comparison, err := CompareObservations([]Observation{world}, []Observation{candidate})
	if err != nil {
		t.Fatal(err)
	}
	if comparison.Status != "matched" || comparison.MatchedCount != 1 || comparison.CutoverAuthorized || comparison.PublicOnlineEnabled {
		t.Fatalf("unexpected comparison: %+v", comparison)
	}
	candidate.RequestHash = strings.Repeat("f", 64)
	comparison, err = CompareObservations([]Observation{world}, []Observation{candidate})
	if err != nil {
		t.Fatal(err)
	}
	if comparison.Status != "diverged" || len(comparison.Divergences) == 0 {
		t.Fatal("divergence not detected")
	}
}
