package main

import (
	"crypto/ed25519"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	envIssuerKeys           = "TRNM_HEPTA_ISSUER_KEYS"
	envControlIssuerKeys    = "TRNM_HEPTA_CONTROL_ISSUER_KEYS"
	envControlTestHook      = "TRNM_RESEARCH_CONTROL_TEST_FAILPOINT_FILE"
	envAuthorityKeyID       = "TRNM_NAKAMA_AUTHORITY_KEY_ID"
	envAuthorityPrivate     = "TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY"
	envAuthorityPrivateRing = "TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS"
	envAuthorityPublicRing  = "TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS"
	envDevAllowSingleton    = "TRNM_NAKAMA_DEV_ALLOW_SINGLETON_AUTHORITY_KEY"
	envOperatorToken        = "TRNM_NAKAMA_OPERATOR_TOKEN"
	envMatchTickRate        = "TRNM_NAKAMA_MATCH_TICK_RATE"
	envHeptaBaseURL         = "TRNM_HEPTA_BASE_URL"
	envHeptaServiceToken    = "TRNM_HEPTA_SERVICE_TOKEN"
	defaultMatchTickRate    = 5
	minimumOperatorLength   = 32
	maximumOperatorLength   = 4096
)

type moduleConfig struct {
	issuerKeys          map[string]ed25519.PublicKey
	controlIssuerKeys   map[string]ed25519.PublicKey
	controlTestHook     string
	authorityKeyID      string
	authorityPrivateKey ed25519.PrivateKey
	authorityPublicKeys map[string]ed25519.PublicKey
	operatorToken       string
	matchTickRate       int
	heptaBaseURL        string
	heptaServiceToken   string
	errors              []string
}

func loadModuleConfig(env map[string]string) moduleConfig {
	cfg := moduleConfig{matchTickRate: defaultMatchTickRate}

	if raw := strings.TrimSpace(env[envIssuerKeys]); raw == "" {
		cfg.errors = append(cfg.errors, envIssuerKeys+" is required")
	} else if keys, err := decodePublicKeyRing(raw); err != nil {
		cfg.errors = append(cfg.errors, envIssuerKeys+": "+err.Error())
	} else {
		cfg.issuerKeys = keys
	}
	if raw := strings.TrimSpace(env[envControlIssuerKeys]); raw == "" {
		cfg.errors = append(cfg.errors, envControlIssuerKeys+" is required")
	} else if keys, err := decodeControlIssuerKeys(raw); err != nil {
		cfg.errors = append(cfg.errors, envControlIssuerKeys+": "+err.Error())
	} else {
		cfg.controlIssuerKeys = keys
	}
	if raw := env[envControlTestHook]; raw != "" {
		if raw != strings.TrimSpace(raw) || len(raw) > 4096 || !filepath.IsAbs(raw) {
			cfg.errors = append(cfg.errors, envControlTestHook+" must be an absolute path without surrounding whitespace")
		} else {
			cfg.controlTestHook = raw
		}
	}

	cfg.authorityKeyID = strings.TrimSpace(env[envAuthorityKeyID])
	if cfg.authorityKeyID == "" || cfg.authorityKeyID != env[envAuthorityKeyID] || !validConfigKeyID(cfg.authorityKeyID) {
		cfg.errors = append(cfg.errors, envAuthorityKeyID+" must contain 1 through 128 ASCII characters from A-Za-z0-9._:-")
	}
	if raw := strings.TrimSpace(env[envAuthorityPrivate]); raw == "" {
		cfg.errors = append(cfg.errors, envAuthorityPrivate+" is required")
	} else if key, err := decodePrivateKey(raw); err != nil {
		cfg.errors = append(cfg.errors, envAuthorityPrivate+": "+err.Error())
	} else {
		cfg.authorityPrivateKey = key
	}
	if env[envAuthorityPrivate] != strings.TrimSpace(env[envAuthorityPrivate]) {
		cfg.errors = append(cfg.errors, envAuthorityPrivate+" must not contain surrounding whitespace")
	}
	if _, present := env[envAuthorityPrivateRing]; present {
		cfg.errors = append(cfg.errors, envAuthorityPrivateRing+" is forbidden; configure only the active singleton private key and retain historical public keys in "+envAuthorityPublicRing)
	}
	allowSingleton := env[envDevAllowSingleton] == "true"
	if env[envDevAllowSingleton] != "" && !allowSingleton {
		cfg.errors = append(cfg.errors, envDevAllowSingleton+" must be exactly true when the isolated dev/test fallback is used")
	}
	rawAuthorityPublicRing := strings.TrimSpace(env[envAuthorityPublicRing])
	if rawAuthorityPublicRing != "" {
		if keys, err := decodePublicKeyRing(rawAuthorityPublicRing); err != nil {
			cfg.errors = append(cfg.errors, envAuthorityPublicRing+": "+err.Error())
		} else {
			cfg.authorityPublicKeys = keys
		}
	}
	if rawAuthorityPublicRing == "" && allowSingleton && len(cfg.authorityPrivateKey) == ed25519.PrivateKeySize {
		cfg.authorityPublicKeys = map[string]ed25519.PublicKey{
			cfg.authorityKeyID: append(ed25519.PublicKey(nil), cfg.authorityPrivateKey.Public().(ed25519.PublicKey)...),
		}
	}
	if rawAuthorityPublicRing == "" && !allowSingleton {
		cfg.errors = append(cfg.errors, envAuthorityPublicRing+" is required; singleton fallback is limited to isolated dev/test with "+envDevAllowSingleton+"=true")
	}
	if publicActive := cfg.authorityPublicKeys[cfg.authorityKeyID]; len(publicActive) != ed25519.PublicKeySize {
		cfg.errors = append(cfg.errors, envAuthorityPublicRing+" must contain the active "+envAuthorityKeyID)
	} else if len(cfg.authorityPrivateKey) == ed25519.PrivateKeySize &&
		subtle.ConstantTimeCompare(publicActive, cfg.authorityPrivateKey.Public().(ed25519.PublicKey)) != 1 {
		cfg.errors = append(cfg.errors, envAuthorityPublicRing+" active public key must match "+envAuthorityPrivate)
	}
	if len(cfg.authorityPrivateKey) == ed25519.PrivateKeySize {
		derived := cfg.authorityPrivateKey.Public().(ed25519.PublicKey)
		for publicKeyID, publicKey := range cfg.authorityPublicKeys {
			if publicKeyID != cfg.authorityKeyID && len(publicKey) == ed25519.PublicKeySize &&
				subtle.ConstantTimeCompare(derived, publicKey) == 1 {
				cfg.errors = append(cfg.errors, envAuthorityPublicRing+" key "+publicKeyID+" aliases active private key "+cfg.authorityKeyID)
			}
		}
	}

	cfg.operatorToken = env[envOperatorToken]
	if len(cfg.operatorToken) < minimumOperatorLength || len(cfg.operatorToken) > maximumOperatorLength {
		cfg.errors = append(cfg.errors, fmt.Sprintf("%s must contain between %d and %d bytes", envOperatorToken, minimumOperatorLength, maximumOperatorLength))
	}

	cfg.heptaBaseURL = env[envHeptaBaseURL]
	parsedHeptaURL, err := url.Parse(cfg.heptaBaseURL)
	if err != nil || cfg.heptaBaseURL == "" || strings.TrimSuffix(cfg.heptaBaseURL, "/") != cfg.heptaBaseURL ||
		(parsedHeptaURL.Scheme != "http" && parsedHeptaURL.Scheme != "https") || parsedHeptaURL.Host == "" ||
		parsedHeptaURL.User != nil || parsedHeptaURL.RawQuery != "" || parsedHeptaURL.Fragment != "" {
		cfg.errors = append(cfg.errors, envHeptaBaseURL+" must be a canonical http(s) base URL without credentials, query, fragment, or trailing slash")
	}
	cfg.heptaServiceToken = env[envHeptaServiceToken]
	if len(cfg.heptaServiceToken) < minimumOperatorLength || len(cfg.heptaServiceToken) > maximumOperatorLength {
		cfg.errors = append(cfg.errors, fmt.Sprintf("%s must contain between %d and %d bytes", envHeptaServiceToken, minimumOperatorLength, maximumOperatorLength))
	}
	if len(cfg.operatorToken) >= minimumOperatorLength && len(cfg.operatorToken) <= maximumOperatorLength &&
		len(cfg.heptaServiceToken) == len(cfg.operatorToken) &&
		subtle.ConstantTimeCompare([]byte(cfg.operatorToken), []byte(cfg.heptaServiceToken)) == 1 {
		cfg.errors = append(cfg.errors, envOperatorToken+" and "+envHeptaServiceToken+" must use distinct credentials")
	}
	for authorityKeyID, authorityPublic := range cfg.authorityPublicKeys {
		if len(authorityPublic) != ed25519.PublicKeySize {
			continue
		}
		for issuerKeyID, issuerPublic := range cfg.issuerKeys {
			if len(issuerPublic) == ed25519.PublicKeySize && subtle.ConstantTimeCompare(authorityPublic, issuerPublic) == 1 {
				cfg.errors = append(cfg.errors, envAuthorityPublicRing+" key "+authorityKeyID+" must differ from Hepta issuer key "+issuerKeyID)
			}
		}
		for controlKeyID, controlPublic := range cfg.controlIssuerKeys {
			if len(controlPublic) == ed25519.PublicKeySize && subtle.ConstantTimeCompare(authorityPublic, controlPublic) == 1 {
				cfg.errors = append(cfg.errors, envControlIssuerKeys+" key "+controlKeyID+" must differ from the Nakama completion authority "+authorityKeyID)
			}
		}
	}
	for controlKeyID, controlPublic := range cfg.controlIssuerKeys {
		for authorizationKeyID, authorizationPublic := range cfg.issuerKeys {
			if len(controlPublic) == ed25519.PublicKeySize && len(authorizationPublic) == ed25519.PublicKeySize &&
				subtle.ConstantTimeCompare(controlPublic, authorizationPublic) == 1 {
				cfg.errors = append(cfg.errors, envControlIssuerKeys+" key "+controlKeyID+" must differ from authorization issuer key "+authorizationKeyID)
			}
		}
	}

	if raw := strings.TrimSpace(env[envMatchTickRate]); raw != "" {
		rate, err := strconv.Atoi(raw)
		if err != nil || rate < 1 || rate > 60 {
			cfg.errors = append(cfg.errors, envMatchTickRate+" must be an integer from 1 through 60")
		} else {
			cfg.matchTickRate = rate
		}
	}

	return cfg
}

func decodeControlIssuerKeys(raw string) (map[string]ed25519.PublicKey, error) {
	return decodePublicKeyRing(raw)
}

func decodePublicKeyRing(raw string) (map[string]ed25519.PublicKey, error) {
	decoder := json.NewDecoder(strings.NewReader(raw))
	opening, err := decoder.Token()
	if err != nil || opening != json.Delim('{') {
		return nil, errors.New("must be a JSON object")
	}
	keys := map[string]ed25519.PublicKey{}
	seenPublic := map[string]string{}
	for decoder.More() {
		token, err := decoder.Token()
		if err != nil {
			return nil, errors.New("invalid JSON object")
		}
		keyID, ok := token.(string)
		if !ok || !validConfigKeyID(keyID) {
			return nil, errors.New("key ids must contain 1 through 128 ASCII characters from A-Za-z0-9._:-")
		}
		if _, duplicate := keys[keyID]; duplicate {
			return nil, fmt.Errorf("duplicate key id %q", keyID)
		}
		var encoded string
		if err := decoder.Decode(&encoded); err != nil {
			return nil, fmt.Errorf("key %q must be a string containing a 32-byte Ed25519 public key", keyID)
		}
		decoded, err := decodeKey(encoded)
		if err != nil || len(decoded) != ed25519.PublicKeySize {
			return nil, fmt.Errorf("key %q must contain a 32-byte Ed25519 public key", keyID)
		}
		fingerprint := hex.EncodeToString(decoded)
		if existing, duplicate := seenPublic[fingerprint]; duplicate {
			return nil, fmt.Errorf("keys %q and %q reuse one public key", existing, keyID)
		}
		seenPublic[fingerprint] = keyID
		keys[keyID] = ed25519.PublicKey(append([]byte(nil), decoded...))
	}
	closing, err := decoder.Token()
	if err != nil || closing != json.Delim('}') {
		return nil, errors.New("invalid JSON object")
	}
	if token, err := decoder.Token(); err != io.EOF || token != nil {
		return nil, errors.New("trailing JSON value is forbidden")
	}
	if len(keys) == 0 {
		return nil, errors.New("at least one key is required")
	}
	return keys, nil
}

func validConfigKeyID(value string) bool {
	if !utf8.ValidString(value) || len(value) < 1 || len(value) > 128 {
		return false
	}
	for _, character := range []byte(value) {
		if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') || character == '.' || character == '_' ||
			character == ':' || character == '-' {
			continue
		}
		return false
	}
	return true
}

func (c moduleConfig) ready() error {
	if len(c.errors) == 0 {
		return nil
	}
	return errors.New(strings.Join(c.errors, "; "))
}

func (c moduleConfig) operatorAuthorized(token string) bool {
	if len(c.operatorToken) < minimumOperatorLength || len(c.operatorToken) > maximumOperatorLength ||
		len(token) < minimumOperatorLength || len(token) > maximumOperatorLength || len(token) != len(c.operatorToken) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(token), []byte(c.operatorToken)) == 1
}

func decodePrivateKey(value string) (ed25519.PrivateKey, error) {
	b, err := decodeKey(value)
	if err != nil {
		return nil, errors.New("must be base64, base64url, or hexadecimal")
	}
	switch len(b) {
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(b), nil
	case ed25519.PrivateKeySize:
		key := ed25519.PrivateKey(append([]byte(nil), b...))
		derived := ed25519.NewKeyFromSeed(key.Seed())
		if subtle.ConstantTimeCompare(key, derived) != 1 {
			return nil, errors.New("64-byte Ed25519 private key has an inconsistent public suffix")
		}
		return key, nil
	default:
		return nil, fmt.Errorf("must contain a %d-byte seed or %d-byte Ed25519 private key", ed25519.SeedSize, ed25519.PrivateKeySize)
	}
}

func decodeKey(value string) ([]byte, error) {
	if value == "" || value != strings.TrimSpace(value) {
		return nil, errors.New("key encoding is empty or contains whitespace")
	}
	if b, err := hex.DecodeString(value); err == nil && hex.EncodeToString(b) == value {
		return b, nil
	}
	if b, err := base64.StdEncoding.Strict().DecodeString(value); err == nil && base64.StdEncoding.EncodeToString(b) == value {
		return b, nil
	}
	if b, err := base64.RawStdEncoding.Strict().DecodeString(value); err == nil && base64.RawStdEncoding.EncodeToString(b) == value {
		return b, nil
	}
	if b, err := base64.RawURLEncoding.Strict().DecodeString(value); err == nil && base64.RawURLEncoding.EncodeToString(b) == value {
		return b, nil
	}
	return nil, errors.New("unsupported key encoding")
}
