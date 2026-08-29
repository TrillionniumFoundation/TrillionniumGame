package contract

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"
)

const digestPrefix = "sha256:"

const maxContractTextRunes = 512

// MaxPayloadBytes is the v1 wire limit for opaque command and event payloads.
// Keeping this bound in the contract prevents unbounded canonical frames,
// snapshots, and realtime messages from reaching the runtime.
const MaxPayloadBytes = 64 * 1024

var logicalMatchIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)

// Digest is the only wire representation accepted for SHA-256 values.
// It is deliberately strict: lowercase hexadecimal with an explicit algorithm.
type Digest string

func NewDigest(data []byte) Digest {
	sum := sha256.Sum256(data)
	return Digest(digestPrefix + hex.EncodeToString(sum[:]))
}

func ParseDigest(value string) (Digest, error) {
	if len(value) != len(digestPrefix)+sha256.Size*2 || !strings.HasPrefix(value, digestPrefix) {
		return "", fmt.Errorf("digest must be sha256 followed by 64 lowercase hexadecimal characters")
	}
	hexPart := value[len(digestPrefix):]
	if strings.ToLower(hexPart) != hexPart {
		return "", errors.New("digest hexadecimal must be lowercase")
	}
	raw, err := hex.DecodeString(hexPart)
	if err != nil || len(raw) != sha256.Size {
		return "", errors.New("digest contains invalid hexadecimal")
	}
	return Digest(value), nil
}

func (d Digest) Validate() error {
	_, err := ParseDigest(string(d))
	return err
}

func (d Digest) Bytes() ([sha256.Size]byte, error) {
	var out [sha256.Size]byte
	parsed, err := ParseDigest(string(d))
	if err != nil {
		return out, err
	}
	raw, _ := hex.DecodeString(string(parsed)[len(digestPrefix):])
	copy(out[:], raw)
	return out, nil
}

func validateText(name, value string) error {
	if value == "" {
		return fmt.Errorf("%s is required", name)
	}
	if !utf8.ValidString(value) {
		return fmt.Errorf("%s is not valid UTF-8", name)
	}
	if utf8.RuneCountInString(value) > maxContractTextRunes {
		return fmt.Errorf("%s exceeds %d Unicode code points", name, maxContractTextRunes)
	}
	if strings.IndexByte(value, 0) >= 0 {
		return fmt.Errorf("%s contains a NUL byte", name)
	}
	return nil
}

// ValidateKeyID applies the contract text constraints to a signing key ID.
// It is exported so runtimes can reject unusable authority configuration
// before mutating any match state.
func ValidateKeyID(value string) error {
	return validateText("key_id", value)
}

// ValidateAuthorizationID exposes the exact text constraints used by signed
// authorization claims for realtime join metadata validation.
func ValidateAuthorizationID(value string) error {
	return validateText("authorization_id", value)
}

// ValidateCommandID exposes the text constraints required when a decoded
// command id is reflected in an ephemeral command-rejected response.
func ValidateCommandID(value string) error {
	return validateText("command_id", value)
}

// ValidateLogicalMatchID defines the storage-safe identifier shared by the
// public contract and Nakama's server-owned storage key.
func ValidateLogicalMatchID(value string) error {
	if !logicalMatchIDPattern.MatchString(value) {
		return errors.New("match_id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
	}
	return nil
}
