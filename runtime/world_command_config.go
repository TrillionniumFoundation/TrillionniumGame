package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	matchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/core"
	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldtransition"
)

const (
	worldProfileLegacy = "legacy_direct"
	worldProfileTarget = "world_transition_v1"

	worldFailpointAfterReservation = "after_reservation"
	worldFailpointAfterVerify      = "after_verify"

	envWorldProfile          = "TRNM_WORLD_COMMAND_PROFILE"
	envWorldURL              = "TRNM_WORLD_TRANSITION_URL"
	envWorldBearer           = "TRNM_WORLD_TRANSITION_BEARER_TOKEN"
	envWorldCAPath           = "TRNM_WORLD_TRANSITION_CA_PATH"
	envWorldCAPEMBase64      = "TRNM_WORLD_TRANSITION_CA_PEM_BASE64"
	envWorldTimeoutMS        = "TRNM_WORLD_TRANSITION_TIMEOUT_MS"
	envWorldMaxResponseBytes = "TRNM_WORLD_TRANSITION_MAX_RESPONSE_BYTES"
	envWorldRulesetRevision  = "TRNM_WORLD_RULESET_REVISION"
	envWorldContentRevision  = "TRNM_WORLD_CONTENT_REVISION"
	envWorldStateSchema      = "TRNM_WORLD_STATE_SCHEMA_ID"
	envWorldCommandSchema    = "TRNM_WORLD_COMMAND_SCHEMA_ID"
	envWorldInitialState     = "TRNM_WORLD_INITIAL_STATE_JSON_BASE64"
	envWorldInitialTick      = "TRNM_WORLD_INITIAL_TICK"
	envWorldRulesetHash      = "TRNM_WORLD_RULESET_HASH"
	envWorldContentHash      = "TRNM_WORLD_CONTENT_HASH"
	envWorldInitialStateHash = "TRNM_WORLD_INITIAL_STATE_HASH"
	envWorldChallengeHash    = "TRNM_WORLD_CHALLENGE_SNAPSHOT_HASH"
	envWorldFaultLab         = "TRNM_WORLD_COMMAND_FAULT_LAB"
	envWorldFailpoint        = "TRNM_WORLD_COMMAND_FAILPOINT"
)

type worldCommandRuntimeConfig struct {
	profile               string
	endpoint              *url.URL
	bearerToken           string
	caPath                string
	caPEM                 []byte
	timeout               time.Duration
	maxResponseBytes      int64
	rulesetRevision       string
	contentRevision       string
	stateSchemaID         string
	commandSchemaID       string
	initialStateJSON      []byte
	initialStateValue     any
	initialStateHash      string
	initialStateDigest    contract.Digest
	initialTick           int64
	rulesetHash           contract.Digest
	contentHash           contract.Digest
	challengeSnapshotHash contract.Digest
	faultLab              bool
	failpoint             string
	errors                []string
}

func loadWorldCommandRuntimeConfig(env map[string]string) worldCommandRuntimeConfig {
	cfg := worldCommandRuntimeConfig{
		profile:          strings.TrimSpace(env[envWorldProfile]),
		timeout:          5 * time.Second,
		maxResponseBytes: 4 * 1024 * 1024,
		failpoint:        strings.TrimSpace(env[envWorldFailpoint]),
	}
	switch strings.TrimSpace(env[envWorldFaultLab]) {
	case "", "0":
	case "1":
		cfg.faultLab = true
	default:
		cfg.errors = append(cfg.errors, envWorldFaultLab+" must be empty, 0, or 1")
	}
	if cfg.profile == "" {
		cfg.profile = worldProfileLegacy
	}
	if cfg.profile != worldProfileLegacy && cfg.profile != worldProfileTarget {
		cfg.errors = append(cfg.errors, envWorldProfile+" must be legacy_direct or world_transition_v1")
		return cfg
	}
	if cfg.profile == worldProfileLegacy {
		if cfg.faultLab || cfg.failpoint != "" {
			cfg.errors = append(cfg.errors, "World fault-lab controls require the world_transition_v1 profile")
		}
		return cfg
	}
	if cfg.failpoint != "" && !cfg.faultLab {
		cfg.errors = append(cfg.errors, envWorldFailpoint+" requires "+envWorldFaultLab+"=1")
	}
	if cfg.failpoint != "" && cfg.failpoint != worldFailpointAfterReservation && cfg.failpoint != worldFailpointAfterVerify {
		cfg.errors = append(cfg.errors, envWorldFailpoint+" must be after_reservation or after_verify")
	}

	rawURL := strings.TrimSpace(env[envWorldURL])
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		cfg.errors = append(cfg.errors, envWorldURL+" must be an absolute HTTPS URL without userinfo, query, or fragment")
	} else {
		cfg.endpoint = parsed
	}

	cfg.bearerToken = env[envWorldBearer]
	if len(cfg.bearerToken) < 32 || len(cfg.bearerToken) > 4096 || strings.TrimSpace(cfg.bearerToken) != cfg.bearerToken {
		cfg.errors = append(cfg.errors, envWorldBearer+" must contain 32 through 4096 non-trimmed bytes")
	}

	cfg.caPath = strings.TrimSpace(env[envWorldCAPath])
	rawCAPEM := strings.TrimSpace(env[envWorldCAPEMBase64])
	if cfg.caPath != "" && rawCAPEM != "" {
		cfg.errors = append(cfg.errors, "configure only one of "+envWorldCAPath+" or "+envWorldCAPEMBase64)
	} else if rawCAPEM != "" {
		decoded, decodeErr := base64.StdEncoding.Strict().DecodeString(rawCAPEM)
		if decodeErr != nil || len(decoded) == 0 || len(decoded) > 1024*1024 {
			cfg.errors = append(cfg.errors, envWorldCAPEMBase64+" must be canonical base64 containing at most 1 MiB")
		} else {
			cfg.caPEM = decoded
		}
	} else if cfg.caPath == "" {
		cfg.errors = append(cfg.errors, "one of "+envWorldCAPath+" or "+envWorldCAPEMBase64+" is required")
	}

	if raw := strings.TrimSpace(env[envWorldTimeoutMS]); raw != "" {
		milliseconds, parseErr := strconv.Atoi(raw)
		if parseErr != nil || milliseconds < 100 || milliseconds > 30_000 {
			cfg.errors = append(cfg.errors, envWorldTimeoutMS+" must be an integer from 100 through 30000")
		} else {
			cfg.timeout = time.Duration(milliseconds) * time.Millisecond
		}
	}
	if raw := strings.TrimSpace(env[envWorldMaxResponseBytes]); raw != "" {
		limit, parseErr := strconv.ParseInt(raw, 10, 64)
		if parseErr != nil || limit < 1024 || limit > 16*1024*1024 {
			cfg.errors = append(cfg.errors, envWorldMaxResponseBytes+" must be an integer from 1024 through 16777216")
		} else {
			cfg.maxResponseBytes = limit
		}
	}

	cfg.rulesetRevision = strings.TrimSpace(env[envWorldRulesetRevision])
	cfg.contentRevision = strings.TrimSpace(env[envWorldContentRevision])
	cfg.stateSchemaID = strings.TrimSpace(env[envWorldStateSchema])
	cfg.commandSchemaID = strings.TrimSpace(env[envWorldCommandSchema])
	for name, value := range map[string]string{
		envWorldRulesetRevision: cfg.rulesetRevision,
		envWorldContentRevision: cfg.contentRevision,
		envWorldStateSchema:     cfg.stateSchemaID,
		envWorldCommandSchema:   cfg.commandSchemaID,
	} {
		if value == "" || len(value) > 160 {
			cfg.errors = append(cfg.errors, name+" must contain 1 through 160 bytes")
		}
	}

	rawInitialState := strings.TrimSpace(env[envWorldInitialState])
	decodedState, decodeErr := base64.StdEncoding.Strict().DecodeString(rawInitialState)
	if decodeErr != nil || len(decodedState) == 0 || len(decodedState) > worldtransition.MaxStateBytes {
		cfg.errors = append(cfg.errors, envWorldInitialState+" must be canonical base64 containing a bounded canonical JSON object or array")
	} else if value, parseErr := worldtransition.ParseCanonical(decodedState, true, worldtransition.MaxStateBytes); parseErr != nil {
		cfg.errors = append(cfg.errors, envWorldInitialState+": "+parseErr.Error())
	} else {
		cfg.initialStateJSON = append([]byte(nil), decodedState...)
		cfg.initialStateValue = value
		sum := sha256.Sum256(decodedState)
		cfg.initialStateHash = hex.EncodeToString(sum[:])
	}

	if raw := strings.TrimSpace(env[envWorldInitialTick]); raw == "" {
		cfg.errors = append(cfg.errors, envWorldInitialTick+" is required")
	} else if tick, parseErr := strconv.ParseInt(raw, 10, 64); parseErr != nil || tick < 0 {
		cfg.errors = append(cfg.errors, envWorldInitialTick+" must be a non-negative signed i64")
	} else {
		cfg.initialTick = tick
	}

	cfg.rulesetHash = contract.Digest(strings.TrimSpace(env[envWorldRulesetHash]))
	cfg.contentHash = contract.Digest(strings.TrimSpace(env[envWorldContentHash]))
	cfg.initialStateDigest = contract.Digest(strings.TrimSpace(env[envWorldInitialStateHash]))
	cfg.challengeSnapshotHash = contract.Digest(strings.TrimSpace(env[envWorldChallengeHash]))
	for name, value := range map[string]contract.Digest{
		envWorldRulesetHash:      cfg.rulesetHash,
		envWorldContentHash:      cfg.contentHash,
		envWorldInitialStateHash: cfg.initialStateDigest,
		envWorldChallengeHash:    cfg.challengeSnapshotHash,
	} {
		if err := value.Validate(); err != nil {
			cfg.errors = append(cfg.errors, name+": "+err.Error())
		}
	}
	if len(cfg.initialStateJSON) != 0 && cfg.initialStateDigest != contract.Digest("sha256:"+cfg.initialStateHash) {
		cfg.errors = append(cfg.errors, envWorldInitialStateHash+" does not match the canonical initial state bytes")
	}
	return cfg
}

func (c worldCommandRuntimeConfig) ready() error {
	if c.profile != worldProfileLegacy && c.profile != worldProfileTarget {
		return errors.New("World command profile is invalid")
	}
	if len(c.errors) != 0 {
		return errors.New(strings.Join(c.errors, "; "))
	}
	return nil
}

func (c worldCommandRuntimeConfig) targetBinding(binding matchcore.WorldBinding) error {
	if c.profile != worldProfileTarget {
		return nil
	}
	if err := c.ready(); err != nil {
		return err
	}
	if binding.RulesetHash != c.rulesetHash || binding.DatasetHash != c.contentHash || binding.ChallengeSnapshotHash != c.challengeSnapshotHash {
		return fmt.Errorf("match immutable hashes do not match the configured World target profile")
	}
	return nil
}

func (c worldCommandRuntimeConfig) caCertificatePEM() ([]byte, error) {
	if len(c.caPEM) != 0 {
		return append([]byte(nil), c.caPEM...), nil
	}
	if c.caPath == "" {
		return nil, errors.New("World CA certificate is not configured")
	}
	payload, err := os.ReadFile(c.caPath)
	if err != nil {
		return nil, fmt.Errorf("read World CA certificate: %w", err)
	}
	if len(payload) == 0 || len(payload) > 1024*1024 {
		return nil, errors.New("World CA certificate size is invalid")
	}
	return payload, nil
}
