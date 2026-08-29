package worldtransition

import (
	"strings"
	"testing"
)

func TestPayloadBudgetsFailClosed(t *testing.T) {
	prepared := fixturePrepared(t)
	oversized := map[string]any{"value": strings.Repeat("x", MaxStateBytes+1)}
	if _, err := NewCanonicalPayload(oversized, "trnm.rts.state.v1", MaxStateBytes, "state"); err == nil {
		t.Fatal("oversized state payload accepted")
	}
	if _, err := NewCanonicalPayload(map[string]any{"value": strings.Repeat("x", MaxCommandBytes+1)}, "trnm.rts.order.v1", MaxCommandBytes, "command"); err == nil {
		t.Fatal("oversized command payload accepted")
	}
	_ = prepared
}
