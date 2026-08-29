// Package worldtransition independently prepares and verifies the unsigned
// deterministic boundary between Nakama authority and World-owned rules.
//
// It deliberately contains no transport, persistence, signing, wall-clock,
// randomness, session or mutable-global capability. Callers must persist an
// immutable authority reservation before external execution and must revalidate
// every authoritative cursor before committing a verified result.
package worldtransition
