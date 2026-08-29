package worldtransition

import "testing"

func FuzzCanonicalParserNeverPanics(f *testing.F) {
	for _, seed := range [][]byte{
		[]byte(`{}`), []byte(`[]`), []byte(`{"a":1}`), []byte(`{"a":-0}`),
		{0xff}, []byte(`{"a":1,"a":2}`),
	} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		_, _ = ParseCanonical(raw, false, 1<<20)
	})
}
