package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	matchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/core"
)

func TestWorldCommandConfigDefaultsToLegacyWithoutTargetAuthority(t *testing.T) {
	config := loadWorldCommandRuntimeConfig(map[string]string{})
	if err := config.ready(); err != nil {
		t.Fatal(err)
	}
	if config.profile != worldProfileLegacy || config.faultLab || config.failpoint != "" {
		t.Fatalf("unexpected legacy default: %+v", config)
	}
}

func TestWorldCommandFailpointRequiresExplicitTargetFaultLab(t *testing.T) {
	for name, env := range map[string]map[string]string{
		"legacy": {
			envWorldFailpoint: worldFailpointAfterReservation,
			envWorldFaultLab:  "1",
		},
		"target without lab": {
			envWorldProfile:   worldProfileTarget,
			envWorldFailpoint: worldFailpointAfterVerify,
		},
		"unknown failpoint": {
			envWorldProfile:   worldProfileTarget,
			envWorldFaultLab:  "1",
			envWorldFailpoint: "unknown",
		},
	} {
		t.Run(name, func(t *testing.T) {
			if err := loadWorldCommandRuntimeConfig(env).ready(); err == nil {
				t.Fatal("unsafe fault-lab configuration was accepted")
			}
		})
	}
}

func TestWorldCommandTargetBindingUsesImmutableAuthorizationHashes(t *testing.T) {
	state := []byte(`{"counter":0}`)
	stateSum := sha256.Sum256(state)
	ruleset := contract.NewDigest([]byte("ruleset"))
	content := contract.NewDigest([]byte("content"))
	stateDigest := contract.Digest("sha256:" + hex.EncodeToString(stateSum[:]))
	env := map[string]string{
		envWorldProfile:          worldProfileTarget,
		envWorldURL:              "https://world-fixture:7443/v1/transition",
		envWorldBearer:           strings.Repeat("b", 32),
		envWorldCAPEMBase64:      base64.StdEncoding.EncodeToString([]byte("test-ca-placeholder")),
		envWorldRulesetRevision:  "rules-v1",
		envWorldContentRevision:  "content-v1",
		envWorldStateSchema:      "trnm.rts.state.v1",
		envWorldCommandSchema:    "trnm.rts.order.v1",
		envWorldInitialState:     base64.StdEncoding.EncodeToString(state),
		envWorldInitialTick:      "0",
		envWorldRulesetHash:      string(ruleset),
		envWorldContentHash:      string(content),
		envWorldInitialStateHash: string(stateDigest),
		envWorldFaultLab:         "1",
		envWorldFailpoint:        worldFailpointAfterVerify,
	}
	config := loadWorldCommandRuntimeConfig(env)
	if err := config.ready(); err != nil {
		t.Fatal(err)
	}
	binding := matchcore.WorldBinding{
		RulesetHash:           ruleset,
		DatasetHash:           content,
		ChallengeSnapshotHash: stateDigest,
	}
	if err := config.targetBinding(binding); err != nil {
		t.Fatal(err)
	}
	binding.DatasetHash = contract.NewDigest([]byte("different"))
	if err := config.targetBinding(binding); err == nil {
		t.Fatal("target profile accepted a different immutable content hash")
	}
}
