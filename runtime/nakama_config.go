package main

import (
	"crypto/ed25519"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	envIssuerKeys         = "TRNM_HEPTA_ISSUER_KEYS"
	envAuthorityKeyID     = "TRNM_NAKAMA_AUTHORITY_KEY_ID"
	envAuthorityPrivate   = "TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY"
	envOperatorToken      = "TRNM_NAKAMA_OPERATOR_TOKEN"
	envMatchTickRate      = "TRNM_NAKAMA_MATCH_TICK_RATE"
	defaultMatchTickRate  = 5
	minimumOperatorLength = 32
	maximumOperatorLength = 4096
)

type moduleConfig struct {
	issuerKeys          map[string]ed25519.PublicKey
	authorityKeyID      string
	authorityPrivateKey ed25519.PrivateKey
	operatorToken       string
	matchTickRate       int
	errors              []string
}

func loadModuleConfig(env map[string]string) moduleConfig {
	cfg := moduleConfig{matchTickRate: defaultMatchTickRate}

	if raw := strings.TrimSpace(env[envIssuerKeys]); raw == "" {
		cfg.errors = append(cfg.errors, envIssuerKeys+" is required")
	} else {
		var encoded map[string]string
		if err := json.Unmarshal([]byte(raw), &encoded); err != nil {
			cfg.errors = append(cfg.errors, envIssuerKeys+": invalid JSON object")
		} else if len(encoded) == 0 {
			cfg.errors = append(cfg.errors, envIssuerKeys+": at least one issuer key is required")
		} else {
			cfg.issuerKeys = make(map[string]ed25519.PublicKey, len(encoded))
			for rawKeyID, value := range encoded {
				keyID := strings.TrimSpace(rawKeyID)
				if keyID == "" || keyID != rawKeyID || !validConfigKeyID(keyID) {
					cfg.errors = append(cfg.errors, envIssuerKeys+": key ids must be canonical non-empty UTF-8 without surrounding whitespace")
					continue
				}
				key, err := decodeKey(value)
				if err != nil || len(key) != ed25519.PublicKeySize {
					cfg.errors = append(cfg.errors, fmt.Sprintf("%s: key %q must contain a 32-byte Ed25519 public key", envIssuerKeys, keyID))
					continue
				}
				cfg.issuerKeys[keyID] = ed25519.PublicKey(append([]byte(nil), key...))
			}
		}
	}

	cfg.authorityKeyID = strings.TrimSpace(env[envAuthorityKeyID])
	if cfg.authorityKeyID == "" || cfg.authorityKeyID != env[envAuthorityKeyID] || !validConfigKeyID(cfg.authorityKeyID) {
		cfg.errors = append(cfg.errors, envAuthorityKeyID+" must be a canonical non-empty UTF-8 key id")
	}
	if raw := strings.TrimSpace(env[envAuthorityPrivate]); raw == "" {
		cfg.errors = append(cfg.errors, envAuthorityPrivate+" is required")
	} else if key, err := decodePrivateKey(raw); err != nil {
		cfg.errors = append(cfg.errors, envAuthorityPrivate+": "+err.Error())
	} else {
		cfg.authorityPrivateKey = key
	}

	cfg.operatorToken = env[envOperatorToken]
	if len(cfg.operatorToken) < minimumOperatorLength || len(cfg.operatorToken) > maximumOperatorLength {
		cfg.errors = append(cfg.errors, fmt.Sprintf("%s must contain between %d and %d bytes", envOperatorToken, minimumOperatorLength, maximumOperatorLength))
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

func validConfigKeyID(value string) bool {
	return utf8.ValidString(value) && utf8.RuneCountInString(value) <= 512 && !strings.ContainsRune(value, '\x00')
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
	value = strings.TrimSpace(value)
	if value == "" {
		return nil, errors.New("empty key")
	}
	if b, err := hex.DecodeString(value); err == nil {
		return b, nil
	}
	if b, err := base64.StdEncoding.DecodeString(value); err == nil {
		return b, nil
	}
	if b, err := base64.RawStdEncoding.DecodeString(value); err == nil {
		return b, nil
	}
	if b, err := base64.RawURLEncoding.DecodeString(value); err == nil {
		return b, nil
	}
	return nil, errors.New("unsupported key encoding")
}
