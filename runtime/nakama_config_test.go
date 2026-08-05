package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
)

func validModuleConfigEnv(t *testing.T) (map[string]string, ed25519.PublicKey, ed25519.PrivateKey) {
	t.Helper()
	issuerPublic, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	_, authorityPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	issuerJSON, _ := json.Marshal(map[string]string{
		"hepta-test-v1": base64.StdEncoding.EncodeToString(issuerPublic),
	})
	return map[string]string{
		envIssuerKeys:        string(issuerJSON),
		envAuthorityKeyID:    "nakama-test-v1",
		envAuthorityPrivate:  base64.StdEncoding.EncodeToString(authorityPrivate.Seed()),
		envOperatorToken:     "0123456789abcdef0123456789abcdef",
		envMatchTickRate:     "10",
		envHeptaBaseURL:      "http://hepta-research-league:8088",
		envHeptaServiceToken: "abcdef0123456789abcdef0123456789",
	}, issuerPublic, authorityPrivate
}

func TestLoadModuleConfigReady(t *testing.T) {
	env, _, _ := validModuleConfigEnv(t)
	cfg := loadModuleConfig(env)
	if err := cfg.ready(); err != nil {
		t.Fatalf("expected ready configuration, got %v", err)
	}
	if cfg.matchTickRate != 10 || len(cfg.issuerKeys) != 1 {
		t.Fatalf("unexpected decoded configuration: %#v", cfg)
	}
	if !cfg.operatorAuthorized(env[envOperatorToken]) {
		t.Fatal("operator token was not accepted")
	}
	if cfg.operatorAuthorized(env[envOperatorToken]+"x") || cfg.operatorAuthorized("wrong") {
		t.Fatal("invalid operator token was accepted")
	}
}

func TestLoadModuleConfigRejectsMissingSecrets(t *testing.T) {
	cfg := loadModuleConfig(map[string]string{})
	if err := cfg.ready(); err == nil {
		t.Fatal("empty configuration unexpectedly became ready")
	}
	if cfg.operatorAuthorized("") {
		t.Fatal("empty operator token was accepted")
	}
}

func TestLoadModuleConfigRejectsOversizedOperatorToken(t *testing.T) {
	cfg := moduleConfig{operatorToken: strings.Repeat("x", maximumOperatorLength+1)}
	if cfg.operatorAuthorized(cfg.operatorToken) {
		t.Fatal("oversized operator token was authorized")
	}
	env := map[string]string{envOperatorToken: strings.Repeat("x", maximumOperatorLength+1)}
	loaded := loadModuleConfig(env)
	if err := loaded.ready(); err == nil || !strings.Contains(err.Error(), envOperatorToken) {
		t.Fatal("oversized configured operator token did not make runtime unready")
	}
}

func TestLoadModuleConfigRejectsNonCanonicalIssuerKeyID(t *testing.T) {
	tests := []string{" hepta-test-v1 ", "hepta\ntest", "hepta/test", "签名者", strings.Repeat("a", 129)}
	for _, keyID := range tests {
		t.Run(base64.RawURLEncoding.EncodeToString([]byte(keyID)), func(t *testing.T) {
			env, issuerPublic, _ := validModuleConfigEnv(t)
			issuerJSON, _ := json.Marshal(map[string]string{keyID: base64.StdEncoding.EncodeToString(issuerPublic)})
			env[envIssuerKeys] = string(issuerJSON)
			cfg := loadModuleConfig(env)
			if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), envIssuerKeys) {
				t.Fatal("noncanonical issuer key id was accepted")
			}
		})
	}
}

func TestLoadModuleConfigRequiresCredentialAndKeyRoleSeparation(t *testing.T) {
	t.Run("operator and Hepta service token", func(t *testing.T) {
		env, _, _ := validModuleConfigEnv(t)
		env[envHeptaServiceToken] = env[envOperatorToken]
		cfg := loadModuleConfig(env)
		if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), "distinct credentials") {
			t.Fatal("operator and Hepta callback credentials were allowed to alias")
		}
	})
	t.Run("Nakama authority and Hepta issuer key", func(t *testing.T) {
		env, _, authorityPrivate := validModuleConfigEnv(t)
		issuerJSON, _ := json.Marshal(map[string]string{
			"hepta-test-v1": base64.StdEncoding.EncodeToString(authorityPrivate.Public().(ed25519.PublicKey)),
		})
		env[envIssuerKeys] = string(issuerJSON)
		cfg := loadModuleConfig(env)
		if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), "must differ from Hepta issuer key") {
			t.Fatal("Nakama completion authority was allowed to alias a Hepta issuer key")
		}
	})
}

func TestDecodePrivateKeyRejectsInconsistentSuffix(t *testing.T) {
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	privateKey = append(ed25519.PrivateKey(nil), privateKey...)
	privateKey[len(privateKey)-1] ^= 1
	if _, err := decodePrivateKey(base64.StdEncoding.EncodeToString(privateKey)); err == nil {
		t.Fatal("inconsistent Ed25519 private key was accepted")
	}
}
