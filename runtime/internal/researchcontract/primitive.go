package researchcontract

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

const (
	MinParticipants = 3
	MaxParticipants = 5
	MaxPayloadBytes = 64 * 1024
	maxTextRunes    = 512
)

const digestPrefix = "sha256:"

var (
	sessionIDPattern     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)
	canonicalUUIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
)

// Digest is the only accepted JSON representation of a SHA-256 value.
type Digest string

func NewDigest(data []byte) Digest {
	sum := sha256.Sum256(data)
	return Digest(digestPrefix + hex.EncodeToString(sum[:]))
}

func ParseDigest(value string) (Digest, error) {
	if len(value) != len(digestPrefix)+sha256.Size*2 || !strings.HasPrefix(value, digestPrefix) {
		return "", errors.New("digest must be sha256: followed by 64 lowercase hexadecimal characters")
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

func (d Digest) Validate() error { _, err := ParseDigest(string(d)); return err }

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

func ValidateSessionID(value string) error {
	if !sessionIDPattern.MatchString(value) {
		return errors.New("session_id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
	}
	return nil
}

func ValidateAuthorizationID(value string) error {
	return validateCanonicalUUID("authorization_id", value)
}
func ValidateCommandID(value string) error { return validateCanonicalUUID("command_id", value) }
func ValidateAuthorizationSetID(value string) error {
	return validateCanonicalUUID("authorization_set_id", value)
}
func ValidateNakamaUserID(value string) error { return validateCanonicalUUID("subject_user_id", value) }
func ValidateTeamID(value string) error       { return validateCanonicalUUID("team_id", value) }
func ValidatePaperProjectID(value string) error {
	return validateCanonicalUUID("paper_project_id", value)
}
func ValidateChallengeID(value string) error { return validateCanonicalUUID("challenge_id", value) }
func ValidateActionID(value string) error    { return validateText("action_id", value) }
func ValidateKeyID(value string) error       { return validateText("key_id", value) }

func validateCanonicalUUID(name, value string) error {
	if !canonicalUUIDPattern.MatchString(value) {
		return fmt.Errorf("%s must be a canonical lowercase UUID", name)
	}
	return nil
}

func validateText(name, value string) error {
	if value == "" {
		return fmt.Errorf("%s is required", name)
	}
	if !utf8.ValidString(value) || strings.IndexByte(value, 0) >= 0 {
		return fmt.Errorf("%s must be valid UTF-8 without NUL", name)
	}
	if utf8.RuneCountInString(value) > maxTextRunes {
		return fmt.Errorf("%s exceeds %d Unicode code points", name, maxTextRunes)
	}
	return nil
}

func validatePrivateKey(key ed25519.PrivateKey, label string) error {
	if len(key) != ed25519.PrivateKeySize {
		return fmt.Errorf("%s private key must contain %d bytes", label, ed25519.PrivateKeySize)
	}
	derived := ed25519.NewKeyFromSeed(key.Seed())
	if !equalBytes(key, derived) {
		return fmt.Errorf("%s private key has an inconsistent public suffix", label)
	}
	return nil
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	var diff byte
	for i := range a {
		diff |= a[i] ^ b[i]
	}
	return diff == 0
}

type frame struct {
	data []byte
	err  error
}

func newFrame(domain string) frame        { return frame{data: append(append([]byte(nil), domain...), 0)} }
func (f frame) string(value string) frame { return f.bytes([]byte(value)) }
func (f frame) bytes(value []byte) frame {
	if f.err != nil {
		return f
	}
	if uint64(len(value)) > math.MaxUint32 {
		f.err = errors.New("canonical field exceeds uint32 length")
		return f
	}
	var size [4]byte
	binary.BigEndian.PutUint32(size[:], uint32(len(value)))
	f.data = append(f.data, size[:]...)
	f.data = append(f.data, value...)
	return f
}
func (f frame) u32(value uint32) frame {
	if f.err != nil {
		return f
	}
	var raw [4]byte
	binary.BigEndian.PutUint32(raw[:], value)
	f.data = append(f.data, raw[:]...)
	return f
}
func (f frame) u64(value uint64) frame {
	if f.err != nil {
		return f
	}
	var raw [8]byte
	binary.BigEndian.PutUint64(raw[:], value)
	f.data = append(f.data, raw[:]...)
	return f
}
func (f frame) i64(value int64) frame { return f.u64(uint64(value)) }
func (f frame) digest(value Digest) frame {
	if f.err != nil {
		return f
	}
	raw, err := value.Bytes()
	if err != nil {
		f.err = err
		return f
	}
	f.data = append(f.data, raw[:]...)
	return f
}
func (f frame) raw(value []byte) frame {
	if f.err == nil {
		f.data = append(f.data, value...)
	}
	return f
}
func (f frame) result() ([]byte, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.data, nil
}

func sortedRoster(entries []RosterEntry) []RosterEntry {
	out := append([]RosterEntry(nil), entries...)
	sort.Slice(out, func(i, j int) bool { return out[i].ParticipantSlot < out[j].ParticipantSlot })
	return out
}
