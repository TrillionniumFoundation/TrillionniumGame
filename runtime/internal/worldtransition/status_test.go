package worldtransition

import "testing"

func TestNoAuthorityOrReleaseGrantFromComparison(t *testing.T) {
	comparison, err := CompareObservations(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if comparison.CutoverAuthorized || comparison.PublicOnlineEnabled {
		t.Fatal("empty source comparison granted authority or public-online state")
	}
}
