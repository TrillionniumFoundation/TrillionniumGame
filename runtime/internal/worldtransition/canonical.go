package worldtransition

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"unicode/utf8"
)

const MaxCanonicalDepth = 128

var ErrCanonicalJSON = errors.New("invalid canonical JSON")

func ParseCanonical(raw []byte, rootContainer bool, maximumBytes int) (any, error) {
	if maximumBytes >= 0 && len(raw) > maximumBytes {
		return nil, fmt.Errorf("%w: payload exceeds %d bytes", ErrCanonicalJSON, maximumBytes)
	}
	if !utf8.Valid(raw) {
		return nil, fmt.Errorf("%w: input is not valid UTF-8", ErrCanonicalJSON)
	}
	if bytes.HasPrefix(raw, []byte{0xef, 0xbb, 0xbf}) {
		return nil, fmt.Errorf("%w: UTF-8 BOM is forbidden", ErrCanonicalJSON)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	value, err := parseJSONValue(dec, 0)
	if err != nil {
		return nil, err
	}
	if token, err := dec.Token(); !errors.Is(err, io.EOF) {
		if err == nil {
			return nil, fmt.Errorf("%w: trailing JSON token %v", ErrCanonicalJSON, token)
		}
		return nil, fmt.Errorf("%w: trailing data: %v", ErrCanonicalJSON, err)
	}
	if rootContainer {
		switch value.(type) {
		case map[string]any, []any:
		default:
			return nil, fmt.Errorf("%w: root must be an object or array", ErrCanonicalJSON)
		}
	}
	canonical, err := CanonicalJSON(value, rootContainer)
	if err != nil {
		return nil, err
	}
	if !bytes.Equal(raw, canonical) {
		return nil, fmt.Errorf("%w: bytes are not the exact canonical representation", ErrCanonicalJSON)
	}
	return value, nil
}

func parseJSONValue(dec *json.Decoder, depth int) (any, error) {
	if depth > MaxCanonicalDepth {
		return nil, fmt.Errorf("%w: nesting depth exceeds %d", ErrCanonicalJSON, MaxCanonicalDepth)
	}
	token, err := dec.Token()
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrCanonicalJSON, err)
	}
	switch value := token.(type) {
	case nil, bool, string:
		return value, nil
	case json.Number:
		raw := value.String()
		if raw == "-0" {
			return nil, fmt.Errorf("%w: -0 is forbidden", ErrCanonicalJSON)
		}
		if len(raw) == 0 || bytes.ContainsAny([]byte(raw), ".eE+") {
			return nil, fmt.Errorf("%w: floating-point and exponent forms are forbidden", ErrCanonicalJSON)
		}
		if raw[0] == '0' && len(raw) > 1 || len(raw) > 2 && raw[0] == '-' && raw[1] == '0' {
			return nil, fmt.Errorf("%w: integer has a leading zero", ErrCanonicalJSON)
		}
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("%w: integer is outside signed i64", ErrCanonicalJSON)
		}
		return parsed, nil
	case json.Delim:
		switch value {
		case '{':
			result := make(map[string]any)
			var previous string
			first := true
			for dec.More() {
				nameToken, err := dec.Token()
				if err != nil {
					return nil, fmt.Errorf("%w: object key: %v", ErrCanonicalJSON, err)
				}
				name, ok := nameToken.(string)
				if !ok {
					return nil, fmt.Errorf("%w: object key is not a string", ErrCanonicalJSON)
				}
				if _, duplicate := result[name]; duplicate {
					return nil, fmt.Errorf("%w: duplicate object key %q", ErrCanonicalJSON, name)
				}
				if !first && bytes.Compare([]byte(name), []byte(previous)) <= 0 {
					return nil, fmt.Errorf("%w: object keys are not strictly ascending UTF-8 bytes", ErrCanonicalJSON)
				}
				first = false
				previous = name
				child, err := parseJSONValue(dec, depth+1)
				if err != nil {
					return nil, err
				}
				result[name] = child
			}
			closing, err := dec.Token()
			if err != nil || closing != json.Delim('}') {
				return nil, fmt.Errorf("%w: object is not closed", ErrCanonicalJSON)
			}
			return result, nil
		case '[':
			result := make([]any, 0)
			for dec.More() {
				child, err := parseJSONValue(dec, depth+1)
				if err != nil {
					return nil, err
				}
				result = append(result, child)
			}
			closing, err := dec.Token()
			if err != nil || closing != json.Delim(']') {
				return nil, fmt.Errorf("%w: array is not closed", ErrCanonicalJSON)
			}
			return result, nil
		default:
			return nil, fmt.Errorf("%w: unexpected delimiter %q", ErrCanonicalJSON, value)
		}
	default:
		return nil, fmt.Errorf("%w: unsupported token %T", ErrCanonicalJSON, token)
	}
}

func CanonicalJSON(value any, rootContainer bool) ([]byte, error) {
	if rootContainer {
		switch value.(type) {
		case map[string]any, []any:
		default:
			return nil, fmt.Errorf("%w: root must be an object or array", ErrCanonicalJSON)
		}
	}
	return appendCanonical(nil, value, 0)
}

func appendCanonical(out []byte, value any, depth int) ([]byte, error) {
	if depth > MaxCanonicalDepth {
		return nil, fmt.Errorf("%w: nesting depth exceeds %d", ErrCanonicalJSON, MaxCanonicalDepth)
	}
	switch typed := value.(type) {
	case nil:
		return append(out, "null"...), nil
	case bool:
		if typed {
			return append(out, "true"...), nil
		}
		return append(out, "false"...), nil
	case string:
		return appendJSONString(out, typed), nil
	case int:
		return strconv.AppendInt(out, int64(typed), 10), nil
	case int8:
		return strconv.AppendInt(out, int64(typed), 10), nil
	case int16:
		return strconv.AppendInt(out, int64(typed), 10), nil
	case int32:
		return strconv.AppendInt(out, int64(typed), 10), nil
	case int64:
		return strconv.AppendInt(out, typed, 10), nil
	case uint:
		if uint64(typed) > math.MaxInt64 {
			return nil, fmt.Errorf("%w: integer is outside signed i64", ErrCanonicalJSON)
		}
		return strconv.AppendUint(out, uint64(typed), 10), nil
	case uint8:
		return strconv.AppendUint(out, uint64(typed), 10), nil
	case uint16:
		return strconv.AppendUint(out, uint64(typed), 10), nil
	case uint32:
		return strconv.AppendUint(out, uint64(typed), 10), nil
	case uint64:
		if typed > math.MaxInt64 {
			return nil, fmt.Errorf("%w: integer is outside signed i64", ErrCanonicalJSON)
		}
		return strconv.AppendUint(out, typed, 10), nil
	case float32, float64, json.Number:
		return nil, fmt.Errorf("%w: floating-point numbers are forbidden", ErrCanonicalJSON)
	case []any:
		out = append(out, '[')
		for index, child := range typed {
			if index > 0 {
				out = append(out, ',')
			}
			var err error
			out, err = appendCanonical(out, child, depth+1)
			if err != nil {
				return nil, err
			}
		}
		return append(out, ']'), nil
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			if !utf8.ValidString(key) {
				return nil, fmt.Errorf("%w: object key is invalid UTF-8", ErrCanonicalJSON)
			}
			keys = append(keys, key)
		}
		sort.Slice(keys, func(i, j int) bool { return bytes.Compare([]byte(keys[i]), []byte(keys[j])) < 0 })
		out = append(out, '{')
		for index, key := range keys {
			if index > 0 {
				out = append(out, ',')
			}
			out = appendJSONString(out, key)
			out = append(out, ':')
			var err error
			out, err = appendCanonical(out, typed[key], depth+1)
			if err != nil {
				return nil, err
			}
		}
		return append(out, '}'), nil
	default:
		return nil, fmt.Errorf("%w: unsupported value %T", ErrCanonicalJSON, value)
	}
}

func appendJSONString(out []byte, value string) []byte {
	out = append(out, '"')
	for _, r := range value {
		switch r {
		case '"', '\\':
			out = append(out, '\\', byte(r))
		case '\b':
			out = append(out, `\b`...)
		case '\t':
			out = append(out, `\t`...)
		case '\n':
			out = append(out, `\n`...)
		case '\f':
			out = append(out, `\f`...)
		case '\r':
			out = append(out, `\r`...)
		default:
			if r < 0x20 {
				const hex = "0123456789abcdef"
				out = append(out, '\\', 'u', '0', '0', hex[byte(r)>>4], hex[byte(r)&0x0f])
			} else {
				out = utf8.AppendRune(out, r)
			}
		}
	}
	return append(out, '"')
}
