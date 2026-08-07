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
	controlPublic, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	controlJSON, _ := json.Marshal(map[string]string{
		"hepta-control-test-v2": base64.StdEncoding.EncodeToString(controlPublic),
	})
	authorityPublicJSON, _ := json.Marshal(map[string]string{
		"nakama-test-v1": base64.StdEncoding.EncodeToString(authorityPrivate.Public().(ed25519.PublicKey)),
	})
	return map[string]string{
		envIssuerKeys:          string(issuerJSON),
		envControlIssuerKeys:   string(controlJSON),
		envAuthorityKeyID:      "nakama-test-v1",
		envAuthorityPrivate:    base64.StdEncoding.EncodeToString(authorityPrivate.Seed()),
		envAuthorityPublicRing: string(authorityPublicJSON),
		envOperatorToken:       "0123456789abcdef0123456789abcdef",
		envMatchTickRate:       "10",
		envHeptaBaseURL:        "http://hepta-research-league:8088",
		envHeptaServiceToken:   "abcdef0123456789abcdef0123456789",
	}, issuerPublic, authorityPrivate
}

func TestLoadModuleConfigLimitsSingletonAuthorityFallbackToExplicitDevTest(t *testing.T) {
	env, _, authority := validModuleConfigEnv(t)
	delete(env, envAuthorityPublicRing)
	if err := loadModuleConfig(env).ready(); err == nil || !strings.Contains(err.Error(), envAuthorityPublicRing+" is required") {
		t.Fatal("production configuration accepted an implicit singleton authority fallback")
	}

	env[envDevAllowSingleton] = "false"
	if err := loadModuleConfig(env).ready(); err == nil || !strings.Contains(err.Error(), envDevAllowSingleton+" must be exactly true") {
		t.Fatal("noncanonical singleton fallback opt-in was accepted")
	}

	env[envDevAllowSingleton] = "true"
	cfg := loadModuleConfig(env)
	if err := cfg.ready(); err != nil {
		t.Fatalf("explicit isolated dev/test singleton fallback was rejected: %v", err)
	}
	if !cfg.authorityPrivateKey.Equal(authority) || !cfg.authorityPublicKeys[cfg.authorityKeyID].Equal(authority.Public().(ed25519.PublicKey)) {
		t.Fatal("explicit singleton fallback did not derive only the active public authority")
	}
}

func TestLoadModuleConfigReady(t *testing.T) {
	env, _, _ := validModuleConfigEnv(t)
	cfg := loadModuleConfig(env)
	if err := cfg.ready(); err != nil {
		t.Fatalf("expected ready configuration, got %v", err)
	}
	if cfg.matchTickRate != 10 || len(cfg.issuerKeys) != 1 || len(cfg.controlIssuerKeys) != 1 {
		t.Fatalf("unexpected decoded configuration: %#v", cfg)
	}
	if !cfg.operatorAuthorized(env[envOperatorToken]) {
		t.Fatal("operator token was not accepted")
	}
	if cfg.operatorAuthorized(env[envOperatorToken]+"x") || cfg.operatorAuthorized("wrong") {
		t.Fatal("invalid operator token was accepted")
	}
}

func TestLoadModuleConfigRejectsDeprecatedAuthorityPrivateRing(t *testing.T) {
	_, _, authority := validModuleConfigEnv(t)
	ring, _ := json.Marshal(map[string]string{
		"nakama-test-v1": base64.StdEncoding.EncodeToString(authority.Seed()),
	})
	for name, value := range map[string]string{"empty": "", "populated": string(ring)} {
		t.Run(name, func(t *testing.T) {
			env, _, _ := validModuleConfigEnv(t)
			env[envAuthorityPrivateRing] = value
			if err := loadModuleConfig(env).ready(); err == nil || !strings.Contains(err.Error(), envAuthorityPrivateRing+" is forbidden") {
				t.Fatal("runtime retained a production multi-private-key ring")
			}
		})
	}
}

func TestLoadModuleConfigAcceptsOnlyAbsoluteOptionalControlTestHook(t *testing.T) {
	env, _, _ := validModuleConfigEnv(t)
	env[envControlTestHook] = "/control-test/nakama-failpoint"
	cfg := loadModuleConfig(env)
	if err := cfg.ready(); err != nil || cfg.controlTestHook != env[envControlTestHook] {
		t.Fatalf("absolute control test hook was rejected: %v", err)
	}
	env[envControlTestHook] = "relative/failpoint"
	cfg = loadModuleConfig(env)
	if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), envControlTestHook) {
		t.Fatal("relative control test hook path was accepted")
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

func TestLoadModuleConfigRejectsNonStrictAuthorizationIssuerRing(t *testing.T) {
	env, issuerPublic, _ := validModuleConfigEnv(t)
	encoded := base64.StdEncoding.EncodeToString(issuerPublic)
	tests := map[string]string{
		"empty":              `{}`,
		"duplicate JSON key": `{"hepta-test-v1":"` + encoded + `","hepta-test-v1":"` + encoded + `"}`,
		"duplicate material": `{"hepta-test-v1":"` + encoded + `","hepta-retiring-v0":"` + encoded + `"}`,
		"non-string value":   `{"hepta-test-v1":7}`,
		"trailing JSON":      `{"hepta-test-v1":"` + encoded + `"} {}`,
	}
	for name, raw := range tests {
		t.Run(name, func(t *testing.T) {
			candidate := mapsClone(env)
			candidate[envIssuerKeys] = raw
			if err := loadModuleConfig(candidate).ready(); err == nil || !strings.Contains(err.Error(), envIssuerKeys) {
				t.Fatal("non-strict authorization issuer ring was accepted")
			}
		})
	}
}

func TestLoadModuleConfigRequiresStrictAuthorityPublicRegistry(t *testing.T) {
	env, _, authorityPrivate := validModuleConfigEnv(t)
	delete(env, envAuthorityPublicRing)
	if err := loadModuleConfig(env).ready(); err == nil || !strings.Contains(err.Error(), envAuthorityPublicRing+" is required") {
		t.Fatal("production configuration accepted a missing authority public registry")
	}

	public := base64.StdEncoding.EncodeToString(authorityPrivate.Public().(ed25519.PublicKey))
	tests := map[string]string{
		"empty":              `{}`,
		"duplicate JSON key": `{"nakama-test-v1":"` + public + `","nakama-test-v1":"` + public + `"}`,
		"duplicate material": `{"nakama-test-v1":"` + public + `","nakama-retiring-v0":"` + public + `"}`,
		"non-string value":   `{"nakama-test-v1":7}`,
		"trailing JSON":      `{"nakama-test-v1":"` + public + `"} {}`,
	}
	for name, raw := range tests {
		t.Run(name, func(t *testing.T) {
			candidate := mapsClone(env)
			candidate[envAuthorityPublicRing] = raw
			if err := loadModuleConfig(candidate).ready(); err == nil || !strings.Contains(err.Error(), envAuthorityPublicRing) {
				t.Fatal("non-strict authority public registry was accepted")
			}
		})
	}
}

func TestLoadModuleConfigSeparatesHistoricalPublicKeysFromPrivateRing(t *testing.T) {
	env, _, _ := validModuleConfigEnv(t)
	_, retiring, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	activePublic, active, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	env[envAuthorityKeyID] = "nakama-active-k1"
	env[envAuthorityPrivate] = base64.StdEncoding.EncodeToString(active.Seed())
	publicRing, _ := json.Marshal(map[string]string{
		"nakama-retired-k0": base64.StdEncoding.EncodeToString(retiring.Public().(ed25519.PublicKey)),
		"nakama-active-k1":  base64.StdEncoding.EncodeToString(activePublic),
	})
	env[envAuthorityPublicRing] = string(publicRing)
	cfg := loadModuleConfig(env)
	if err := cfg.ready(); err != nil {
		t.Fatalf("historical public key without historical private material was rejected: %v", err)
	}
	if !cfg.authorityPublicKeys["nakama-retired-k0"].Equal(retiring.Public().(ed25519.PublicKey)) {
		t.Fatal("historical public verification key was not retained")
	}
}

func TestLoadModuleConfigRejectsAuthorityPublicPrivateMismatchOrAlias(t *testing.T) {
	env, _, authorityPrivate := validModuleConfigEnv(t)
	otherPublic, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	mismatch, _ := json.Marshal(map[string]string{
		"nakama-test-v1": base64.StdEncoding.EncodeToString(otherPublic),
	})
	env[envAuthorityPublicRing] = string(mismatch)
	if err := loadModuleConfig(env).ready(); err == nil || !strings.Contains(err.Error(), "must match") {
		t.Fatal("same authority key id with mismatched public/private material was accepted")
	}

	alias, _ := json.Marshal(map[string]string{
		"nakama-test-v1": base64.StdEncoding.EncodeToString(otherPublic),
		"nakama-alias":   base64.StdEncoding.EncodeToString(authorityPrivate.Public().(ed25519.PublicKey)),
	})
	env[envAuthorityPublicRing] = string(alias)
	if err := loadModuleConfig(env).ready(); err == nil || !strings.Contains(err.Error(), "aliases active private key") {
		t.Fatal("authority public/private material alias under different key ids was accepted")
	}
}

func TestKeyEncodingRejectsWhitespaceAndNonCanonicalForms(t *testing.T) {
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	canonicalPrivate := base64.StdEncoding.EncodeToString(privateKey.Seed())
	canonicalPublic := base64.StdEncoding.EncodeToString(privateKey.Public().(ed25519.PublicKey))
	for name, encoded := range map[string]string{
		"leading whitespace":  " " + canonicalPrivate,
		"trailing whitespace": canonicalPrivate + "\n",
		"uppercase hex":       strings.ToUpper(strings.Repeat("ab", ed25519.SeedSize)),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := decodePrivateKey(encoded); err == nil {
				t.Fatal("noncanonical private-key encoding was accepted")
			}
		})
	}

	env, _, _ := validModuleConfigEnv(t)
	for name, encoded := range map[string]string{
		"public leading whitespace":  " " + canonicalPublic,
		"public trailing whitespace": canonicalPublic + "\t",
	} {
		t.Run(name, func(t *testing.T) {
			candidate := mapsClone(env)
			ring, _ := json.Marshal(map[string]string{"nakama-test-v1": encoded})
			candidate[envAuthorityPublicRing] = string(ring)
			if err := loadModuleConfig(candidate).ready(); err == nil || !strings.Contains(err.Error(), envAuthorityPublicRing) {
				t.Fatal("noncanonical public-key encoding was accepted")
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
	t.Run("control and authorization issuer", func(t *testing.T) {
		env, issuerPublic, _ := validModuleConfigEnv(t)
		controlJSON, _ := json.Marshal(map[string]string{
			"hepta-control-test-v2": base64.StdEncoding.EncodeToString(issuerPublic),
		})
		env[envControlIssuerKeys] = string(controlJSON)
		cfg := loadModuleConfig(env)
		if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), "must differ from authorization issuer key") {
			t.Fatal("control issuer was allowed to alias an authorization issuer")
		}
	})
	t.Run("control and Nakama authority", func(t *testing.T) {
		env, _, authorityPrivate := validModuleConfigEnv(t)
		controlJSON, _ := json.Marshal(map[string]string{
			"hepta-control-test-v2": base64.StdEncoding.EncodeToString(authorityPrivate.Public().(ed25519.PublicKey)),
		})
		env[envControlIssuerKeys] = string(controlJSON)
		cfg := loadModuleConfig(env)
		if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), "must differ from the Nakama completion authority") {
			t.Fatal("control issuer was allowed to alias the Nakama authority")
		}
	})
}

func TestLoadModuleConfigRejectsInvalidControlTrustSet(t *testing.T) {
	env, _, _ := validModuleConfigEnv(t)
	validPublic, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	encoded := base64.StdEncoding.EncodeToString(validPublic)
	tests := map[string]string{
		"empty":            `{}`,
		"duplicate id":     `{"hepta-control":"` + encoded + `","hepta-control":"` + encoded + `"}`,
		"invalid id":       `{"hepta/control":"` + encoded + `"}`,
		"duplicate public": `{"hepta-control-a":"` + encoded + `","hepta-control-b":"` + encoded + `"}`,
		"trailing":         `{"hepta-control":"` + encoded + `"} {}`,
	}
	for name, raw := range tests {
		t.Run(name, func(t *testing.T) {
			candidate := mapsClone(env)
			candidate[envControlIssuerKeys] = raw
			cfg := loadModuleConfig(candidate)
			if err := cfg.ready(); err == nil || !strings.Contains(err.Error(), envControlIssuerKeys) {
				t.Fatal("invalid control trust set was accepted")
			}
		})
	}
}

func mapsClone(source map[string]string) map[string]string {
	copy := make(map[string]string, len(source))
	for key, value := range source {
		copy[key] = value
	}
	return copy
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
