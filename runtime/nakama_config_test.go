package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
)

func TestLoadModuleConfigReady(t *testing.T) {
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
	env := map[string]string{
		envIssuerKeys:       string(issuerJSON),
		envAuthorityKeyID:   "nakama-test-v1",
		envAuthorityPrivate: base64.StdEncoding.EncodeToString(authorityPrivate.Seed()),
		envOperatorToken:    "0123456789abcdef0123456789abcdef",
		envMatchTickRate:    "10",
	}
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
	issuerPublic, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	issuerJSON, _ := json.Marshal(map[string]string{
		" hepta-test-v1 ": base64.StdEncoding.EncodeToString(issuerPublic),
	})
	cfg := loadModuleConfig(map[string]string{envIssuerKeys: string(issuerJSON)})
	if err := cfg.ready(); err == nil {
		t.Fatal("issuer key id with surrounding whitespace was accepted")
	}
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
