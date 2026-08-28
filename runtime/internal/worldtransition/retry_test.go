package worldtransition

import (
	"bytes"
	"testing"
)

func TestPreparedRequestIdentityIsRetryStable(t *testing.T) {
	first := fixturePrepared(t)
	second := fixturePrepared(t)
	if first.RequestHash != second.RequestHash || first.TransitionID != second.TransitionID || first.CommandID != second.CommandID ||
		!bytes.Equal(first.CanonicalRequest, second.CanonicalRequest) {
		t.Fatal("identical authority context produced a different retry identity")
	}
}
