package worldtransition

import (
	"strings"
	"testing"
)

func TestRejectionDetailIsBoundedAndControlFree(t *testing.T) {
	prepared := fixturePrepared(t)
	for _, detail := range []string{"", strings.Repeat("x", 257), "line\nbreak", "control\x7f"} {
		result, err := CanonicalJSON(map[string]any{
			"code": "domain_rejected", "contract_version": ContractVersion,
			"detail": detail, "request_hash": prepared.RequestHash,
			"retryable": false, "transition_id": prepared.TransitionID,
		}, true)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := VerifyResult(prepared, result); err == nil {
			t.Fatalf("invalid detail accepted: %q", detail)
		}
	}
}
