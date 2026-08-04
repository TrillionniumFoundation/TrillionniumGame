package contract

import (
	"crypto/ed25519"
	"crypto/subtle"
	"errors"
)

// validateEd25519PrivateKey rejects a 64-byte value whose cached public-key
// suffix does not correspond to its seed. The standard signer trusts that
// suffix, so length validation alone is not sufficient for durable evidence.
func validateEd25519PrivateKey(privateKey ed25519.PrivateKey, name string) error {
	if len(privateKey) != ed25519.PrivateKeySize {
		return errors.New(name + " private key has invalid length")
	}
	derived := ed25519.NewKeyFromSeed(privateKey[:ed25519.SeedSize])
	if subtle.ConstantTimeCompare(derived, privateKey) != 1 {
		return errors.New(name + " private key public suffix does not match its seed")
	}
	return nil
}
