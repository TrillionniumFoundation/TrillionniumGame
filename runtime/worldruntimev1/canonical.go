package worldruntimev1

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"
)

const (
	MaxCanonicalDepth = 64
	MaxCanonicalNodes = 100_000
	MaxCanonicalBytes = 16 * 1024 * 1024
)

// ContractError is a fail-closed validation error for runtime material.
type ContractError struct {
	Code    string
	Message string
}

func (e *ContractError) Error() string {
	return e.Code + ": " + e.Message
}

func contractError(code, format string, args ...any) error {
	return &ContractError{Code: code, Message: fmt.Sprintf(format, args...)}
}

type parseBudget struct {
	nodes int
}

func (b *parseBudget) visit(depth int) error {
	if depth > MaxCanonicalDepth {
		return contractError("resource_limit_exceeded", "canonical depth exceeds %d", MaxCanonicalDepth)
	}
	b.nodes++
	if b.nodes > MaxCanonicalNodes {
		return contractError("resource_limit_exceeded", "canonical node count exceeds %d", MaxCanonicalNodes)
	}
	return nil
}

// ParseStrict parses JSON without duplicate keys, floats, exponent numbers,
// out-of-range integers, trailing data, or NFC-normalized key collisions.
func ParseStrict(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, contractError("invalid_canonical_json", "input is not valid UTF-8")
	}
	if len(data) > MaxCanonicalBytes {
		return nil, contractError("resource_limit_exceeded", "input exceeds %d bytes", MaxCanonicalBytes)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	budget := &parseBudget{}
	value, err := parseValue(decoder, budget, 0)
	if err != nil {
		return nil, err
	}
	if token, err := decoder.Token(); !errors.Is(err, io.EOF) {
		if err != nil {
			return nil, contractError("invalid_canonical_json", "trailing JSON: %v", err)
		}
		return nil, contractError("invalid_canonical_json", "trailing JSON token %v", token)
	}
	if _, err := CanonicalBytes(value); err != nil {
		return nil, err
	}
	return value, nil
}

func parseValue(decoder *json.Decoder, budget *parseBudget, depth int) (any, error) {
	if err := budget.visit(depth); err != nil {
		return nil, err
	}
	token, err := decoder.Token()
	if err != nil {
		return nil, contractError("invalid_canonical_json", "%v", err)
	}
	switch typed := token.(type) {
	case nil:
		return nil, nil
	case bool:
		return typed, nil
	case string:
		return norm.NFC.String(typed), nil
	case json.Number:
		raw := typed.String()
		if strings.ContainsAny(raw, ".eE") {
			return nil, contractError("invalid_canonical_json", "floating-point and exponent numbers are forbidden: %s", raw)
		}
		value, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			return nil, contractError("invalid_canonical_json", "integer is outside signed 64-bit range: %s", raw)
		}
		return value, nil
	case json.Delim:
		switch typed {
		case '{':
			result := make(map[string]any)
			rawSeen := make(map[string]struct{})
			normalizedSeen := make(map[string]struct{})
			for decoder.More() {
				keyToken, err := decoder.Token()
				if err != nil {
					return nil, contractError("invalid_canonical_json", "object key: %v", err)
				}
				rawKey, ok := keyToken.(string)
				if !ok {
					return nil, contractError("invalid_canonical_json", "object key is not a string")
				}
				if _, exists := rawSeen[rawKey]; exists {
					return nil, contractError("invalid_canonical_json", "duplicate object key: %s", rawKey)
				}
				rawSeen[rawKey] = struct{}{}
				key := norm.NFC.String(rawKey)
				if _, exists := normalizedSeen[key]; exists {
					return nil, contractError("invalid_canonical_json", "normalized object key collision: %s", key)
				}
				normalizedSeen[key] = struct{}{}
				value, err := parseValue(decoder, budget, depth+1)
				if err != nil {
					return nil, err
				}
				result[key] = value
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim('}') {
				return nil, contractError("invalid_canonical_json", "unterminated object")
			}
			return result, nil
		case '[':
			var result []any
			for decoder.More() {
				value, err := parseValue(decoder, budget, depth+1)
				if err != nil {
					return nil, err
				}
				result = append(result, value)
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim(']') {
				return nil, contractError("invalid_canonical_json", "unterminated array")
			}
			return result, nil
		default:
			return nil, contractError("invalid_canonical_json", "unexpected delimiter %q", typed)
		}
	default:
		return nil, contractError("invalid_canonical_json", "unsupported token %T", token)
	}
}

// CanonicalBytes emits NFC-normalized, UTF-8-key-sorted, whitespace-free JSON
// with signed 64-bit integers only and minimal RFC 8259 string escaping.
func CanonicalBytes(value any) ([]byte, error) {
	var output bytes.Buffer
	budget := &parseBudget{}
	if err := writeCanonical(&output, value, budget, 0); err != nil {
		return nil, err
	}
	if output.Len() > MaxCanonicalBytes {
		return nil, contractError("resource_limit_exceeded", "canonical output exceeds %d bytes", MaxCanonicalBytes)
	}
	return output.Bytes(), nil
}

func writeCanonical(output *bytes.Buffer, value any, budget *parseBudget, depth int) error {
	if err := budget.visit(depth); err != nil {
		return err
	}
	switch typed := value.(type) {
	case nil:
		output.WriteString("null")
	case bool:
		if typed {
			output.WriteString("true")
		} else {
			output.WriteString("false")
		}
	case int:
		output.WriteString(strconv.FormatInt(int64(typed), 10))
	case int8:
		output.WriteString(strconv.FormatInt(int64(typed), 10))
	case int16:
		output.WriteString(strconv.FormatInt(int64(typed), 10))
	case int32:
		output.WriteString(strconv.FormatInt(int64(typed), 10))
	case int64:
		output.WriteString(strconv.FormatInt(typed, 10))
	case uint:
		if uint64(typed) > uint64(^uint64(0)>>1) {
			return contractError("invalid_canonical_json", "unsigned integer exceeds signed 64-bit range")
		}
		output.WriteString(strconv.FormatUint(uint64(typed), 10))
	case uint8:
		output.WriteString(strconv.FormatUint(uint64(typed), 10))
	case uint16:
		output.WriteString(strconv.FormatUint(uint64(typed), 10))
	case uint32:
		output.WriteString(strconv.FormatUint(uint64(typed), 10))
	case uint64:
		if typed > uint64(^uint64(0)>>1) {
			return contractError("invalid_canonical_json", "unsigned integer exceeds signed 64-bit range")
		}
		output.WriteString(strconv.FormatUint(typed, 10))
	case string:
		if !utf8.ValidString(typed) {
			return contractError("invalid_canonical_json", "string is not valid UTF-8")
		}
		writeJSONString(output, norm.NFC.String(typed))
	case []any:
		output.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := writeCanonical(output, item, budget, depth+1); err != nil {
				return err
			}
		}
		output.WriteByte(']')
	case map[string]any:
		normalized := make(map[string]any, len(typed))
		keys := make([]string, 0, len(typed))
		for rawKey, item := range typed {
			key := norm.NFC.String(rawKey)
			if _, exists := normalized[key]; exists {
				return contractError("invalid_canonical_json", "normalized object key collision: %s", key)
			}
			normalized[key] = item
			keys = append(keys, key)
		}
		sort.Slice(keys, func(left, right int) bool {
			return bytes.Compare([]byte(keys[left]), []byte(keys[right])) < 0
		})
		output.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				output.WriteByte(',')
			}
			writeJSONString(output, key)
			output.WriteByte(':')
			if err := writeCanonical(output, normalized[key], budget, depth+1); err != nil {
				return err
			}
		}
		output.WriteByte('}')
	default:
		return contractError("invalid_canonical_json", "unsupported canonical type %T", value)
	}
	return nil
}

func writeJSONString(output *bytes.Buffer, value string) {
	output.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"':
			output.WriteString(`\"`)
		case '\\':
			output.WriteString(`\\`)
		case '\b':
			output.WriteString(`\b`)
		case '\f':
			output.WriteString(`\f`)
		case '\n':
			output.WriteString(`\n`)
		case '\r':
			output.WriteString(`\r`)
		case '\t':
			output.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(output, `\u%04x`, r)
			} else {
				output.WriteRune(r)
			}
		}
	}
	output.WriteByte('"')
}

// DomainHash computes SHA-256(ASCII(domain) || LF || canonical JSON bytes).
func DomainHash(domain string, value any) (string, error) {
	if domain == "" || strings.ContainsRune(domain, '\n') {
		return "", contractError("invalid_contract", "hash domain must be non-empty single-line ASCII")
	}
	for _, r := range domain {
		if r > 0x7f {
			return "", contractError("invalid_contract", "hash domain must be ASCII")
		}
	}
	encoded, err := CanonicalBytes(value)
	if err != nil {
		return "", err
	}
	digest := sha256.New()
	digest.Write([]byte(domain))
	digest.Write([]byte{'\n'})
	digest.Write(encoded)
	return hex.EncodeToString(digest.Sum(nil)), nil
}
