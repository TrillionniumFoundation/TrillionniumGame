package worldtransition

import (
	"strings"
	"testing"
)

func TestRejectedRetryPolicyIsFixed(t *testing.T) {
	prepared := fixturePrepared(t)
	for code := range stableErrorCodes {
		retryable := code == "internal_unavailable"
		result, err := CanonicalJSON(map[string]any{
			"code": code, "contract_version": ContractVersion, "detail": "stable rejection",
			"request_hash": prepared.RequestHash, "retryable": retryable,
			"transition_id": prepared.TransitionID,
		}, true)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := VerifyResult(prepared, result); err != nil {
			t.Fatalf("stable code %q rejected: %v", code, err)
		}
		value, _ := ParseCanonical(result, true, -1)
		object := value.(map[string]any)
		object["retryable"] = !retryable
		tampered, _ := CanonicalJSON(object, true)
		if _, err := VerifyResult(prepared, tampered); err == nil {
			t.Fatalf("retry policy tamper accepted for %q", code)
		}
	}

	unknown, _ := CanonicalJSON(map[string]any{
		"code": "new_unknown_code", "contract_version": ContractVersion,
		"detail": "unknown", "request_hash": prepared.RequestHash,
		"retryable": false, "transition_id": prepared.TransitionID,
	}, true)
	if _, err := VerifyResult(prepared, unknown); err == nil || !strings.Contains(err.Error(), "stable") {
		t.Fatal("unknown error code did not fail closed")
	}
}
