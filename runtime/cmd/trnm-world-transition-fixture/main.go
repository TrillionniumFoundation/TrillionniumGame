package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/worldtransition"
)

const maximumRequestBytes = worldtransition.MaxStateBytes + worldtransition.MaxCommandBytes + 64*1024

type fixtureServer struct {
	bearer    string
	cacheDir  string
	requests  atomic.Uint64
	cacheHits atomic.Uint64
	accepted  atomic.Uint64
	rejected  atomic.Uint64
}

func main() {
	listen := required("TRNM_WORLD_FIXTURE_LISTEN")
	certificate := required("TRNM_WORLD_FIXTURE_TLS_CERT")
	privateKey := required("TRNM_WORLD_FIXTURE_TLS_KEY")
	server := &fixtureServer{
		bearer:   required("TRNM_WORLD_FIXTURE_BEARER_TOKEN"),
		cacheDir: required("TRNM_WORLD_FIXTURE_CACHE_DIR"),
	}
	if len(server.bearer) < 32 || len(server.bearer) > 4096 {
		log.Fatal("TRNM_WORLD_FIXTURE_BEARER_TOKEN must contain 32 through 4096 bytes")
	}
	if err := os.MkdirAll(server.cacheDir, 0o700); err != nil {
		log.Fatal(err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"healthy":true,"schema":"trnm.world.fixture.health.v1"}`))
	})
	mux.HandleFunc("GET /v1/stats", server.authorized(server.stats))
	mux.HandleFunc("POST /v1/transition", server.authorized(server.transition))

	httpServer := &http.Server{
		Addr:              listen,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    16 * 1024,
	}
	log.Printf("World transition fixture listening on %s", listen)
	log.Fatal(httpServer.ListenAndServeTLS(certificate, privateKey))
}

func (s *fixtureServer) authorized(next http.HandlerFunc) http.HandlerFunc {
	return func(response http.ResponseWriter, request *http.Request) {
		supplied := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
		if len(supplied) != len(s.bearer) || subtle.ConstantTimeCompare([]byte(supplied), []byte(s.bearer)) != 1 {
			http.Error(response, "authorization rejected", http.StatusUnauthorized)
			return
		}
		next(response, request)
	}
}

func (s *fixtureServer) stats(response http.ResponseWriter, _ *http.Request) {
	writeJSON(response, map[string]any{
		"schema":     "trnm.world.fixture.stats.v1",
		"requests":   s.requests.Load(),
		"cache_hits": s.cacheHits.Load(),
		"accepted":   s.accepted.Load(),
		"rejected":   s.rejected.Load(),
	})
}

func (s *fixtureServer) transition(response http.ResponseWriter, request *http.Request) {
	s.requests.Add(1)
	body, err := io.ReadAll(io.LimitReader(request.Body, maximumRequestBytes+1))
	if err != nil || len(body) == 0 || len(body) > maximumRequestBytes {
		http.Error(response, "request body is invalid", http.StatusBadRequest)
		return
	}
	rawHash := sha256.Sum256(body)
	if supplied := request.Header.Get("X-Trnm-Canonical-Request-Sha256"); supplied != "" && supplied != hex.EncodeToString(rawHash[:]) {
		http.Error(response, "raw request hash mismatch", http.StatusBadRequest)
		return
	}
	requestHash := domainHash(worldtransition.RequestHashDomain, body)
	if cached, err := s.loadCached(requestHash); err == nil {
		s.cacheHits.Add(1)
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write(cached)
		return
	} else if !errors.Is(err, os.ErrNotExist) {
		http.Error(response, "fixture cache is invalid", http.StatusInternalServerError)
		return
	}

	result, disposition, err := buildResult(body, requestHash)
	if err != nil {
		http.Error(response, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.storeCached(requestHash, result); err != nil {
		http.Error(response, "fixture cache write failed", http.StatusInternalServerError)
		return
	}
	if disposition == "accepted" {
		s.accepted.Add(1)
	} else {
		s.rejected.Add(1)
	}
	response.Header().Set("Content-Type", "application/json")
	_, _ = response.Write(result)
}

func buildResult(raw []byte, requestHash string) ([]byte, string, error) {
	value, err := worldtransition.ParseCanonical(raw, true, maximumRequestBytes)
	if err != nil {
		return nil, "", err
	}
	request, ok := value.(map[string]any)
	if !ok || !exactFields(request, "command", "content_revision", "contract_version", "expected_tick", "previous_state", "ruleset_revision", "transition_id") {
		return nil, "", errors.New("request does not match trnm_world_transition_v1")
	}
	if request["contract_version"] != worldtransition.ContractVersion {
		return nil, "", errors.New("unsupported contract version")
	}
	transitionID, ok := request["transition_id"].(string)
	if !ok || transitionID == "" {
		return nil, "", errors.New("transition_id is invalid")
	}
	expectedTick, ok := request["expected_tick"].(int64)
	if !ok || expectedTick < 0 || expectedTick == int64(^uint64(0)>>1) {
		return nil, "", errors.New("expected_tick is invalid")
	}
	previous, err := worldtransition.PayloadFromWire(request["previous_state"], worldtransition.MaxStateBytes, "previous_state")
	if err != nil {
		return nil, "", err
	}
	commandEnvelope, ok := request["command"].(map[string]any)
	if !ok || !exactFields(commandEnvelope, "command_id", "payload") {
		return nil, "", errors.New("command envelope is invalid")
	}
	commandID, ok := commandEnvelope["command_id"].(string)
	if !ok || commandID == "" {
		return nil, "", errors.New("command_id is invalid")
	}
	command, err := worldtransition.PayloadFromWire(commandEnvelope["payload"], worldtransition.MaxCommandBytes, "command.payload")
	if err != nil {
		return nil, "", err
	}
	commandObject, ok := command.Value.(map[string]any)
	if !ok {
		return nil, "", errors.New("fixture command must be an object")
	}
	if kind, _ := commandObject["kind"].(string); kind == "reject" {
		result := map[string]any{
			"code":             "domain_rejected",
			"contract_version": worldtransition.ContractVersion,
			"detail":           "fixture command requested deterministic rejection",
			"request_hash":     requestHash,
			"retryable":        false,
			"transition_id":    transitionID,
		}
		encoded, err := worldtransition.CanonicalJSON(result, true)
		return encoded, "rejected", err
	}

	previousObject, ok := previous.Value.(map[string]any)
	if !ok {
		return nil, "", errors.New("fixture state must be an object")
	}
	counter, _ := previousObject["counter"].(int64)
	delta := int64(1)
	if supplied, exists := commandObject["delta"]; exists {
		parsed, valid := supplied.(int64)
		if !valid || parsed < -1000 || parsed > 1000 {
			return nil, "", errors.New("fixture delta is invalid")
		}
		delta = parsed
	}
	nextTick := expectedTick + 1
	nextState, err := worldtransition.NewCanonicalPayload(
		map[string]any{"counter": counter + delta}, previous.SchemaID, worldtransition.MaxStateBytes, "next_state",
	)
	if err != nil {
		return nil, "", err
	}
	replay, err := worldtransition.NewCanonicalPayload(
		map[string]any{"applied_command_id": commandID, "request_hash": requestHash, "tick": nextTick},
		"trnm.world.fixture.replay.v1", worldtransition.MaxReplayBytes, "replay_material",
	)
	if err != nil {
		return nil, "", err
	}
	outcome, err := worldtransition.NewCanonicalPayload(
		map[string]any{"counter": counter + delta, "result": "applied"},
		"trnm.world.fixture.outcome.v1", worldtransition.MaxOutcomeBytes, "outcome_material",
	)
	if err != nil {
		return nil, "", err
	}
	rulesetRevision, _ := request["ruleset_revision"].(string)
	contentRevision, _ := request["content_revision"].(string)
	outcomeBinding, err := worldtransition.CanonicalJSON(map[string]any{
		"content_revision":     contentRevision,
		"outcome_payload_hash": outcome.SHA256,
		"outcome_schema_id":    outcome.SchemaID,
		"ruleset_revision":     rulesetRevision,
	}, false)
	if err != nil {
		return nil, "", err
	}
	outcomeHash := domainHash(worldtransition.OutcomeHashDomain, outcomeBinding)
	facts := map[string]any{
		"content_revision":    contentRevision,
		"contract_version":    worldtransition.ContractVersion,
		"next_state":          nextState.Wire(),
		"next_tick":           nextTick,
		"outcome_material":    outcome.Wire(),
		"previous_state_hash": previous.SHA256,
		"replay_material":     replay.Wire(),
		"request_hash":        requestHash,
		"ruleset_revision":    rulesetRevision,
		"transition_id":       transitionID,
		"world_outcome_hash":  outcomeHash,
	}
	canonicalFacts, err := worldtransition.CanonicalJSON(facts, false)
	if err != nil {
		return nil, "", err
	}
	result := make(map[string]any, len(facts)+1)
	for key, item := range facts {
		result[key] = item
	}
	result["world_transition_hash"] = domainHash(worldtransition.TransitionHashDomain, canonicalFacts)
	encoded, err := worldtransition.CanonicalJSON(result, true)
	return encoded, "accepted", err
}

func (s *fixtureServer) cachePath(requestHash string) string {
	return filepath.Join(s.cacheDir, requestHash+".json")
}

func (s *fixtureServer) loadCached(requestHash string) ([]byte, error) {
	payload, err := os.ReadFile(s.cachePath(requestHash))
	if err != nil {
		return nil, err
	}
	if _, err := worldtransition.ParseCanonical(payload, true, worldtransition.MaxStateBytes+worldtransition.MaxReplayBytes+worldtransition.MaxOutcomeBytes+64*1024); err != nil {
		return nil, err
	}
	return payload, nil
}

func (s *fixtureServer) storeCached(requestHash string, payload []byte) error {
	temporary, err := os.CreateTemp(s.cacheDir, ".pending-*.json")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(payload); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryName, s.cachePath(requestHash)); err != nil {
		if _, statErr := os.Stat(s.cachePath(requestHash)); statErr == nil {
			return nil
		}
		return err
	}
	directory, err := os.Open(s.cacheDir)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

func domainHash(domain string, canonical []byte) string {
	material := make([]byte, 0, len(domain)+1+len(canonical))
	material = append(material, domain...)
	material = append(material, '\n')
	material = append(material, canonical...)
	sum := sha256.Sum256(material)
	return hex.EncodeToString(sum[:])
}

func exactFields(value map[string]any, fields ...string) bool {
	if len(value) != len(fields) {
		return false
	}
	for _, field := range fields {
		if _, present := value[field]; !present {
			return false
		}
	}
	return true
}

func writeJSON(response http.ResponseWriter, value any) {
	response.Header().Set("Content-Type", "application/json")
	encoded, err := json.Marshal(value)
	if err != nil {
		http.Error(response, "encoding failed", http.StatusInternalServerError)
		return
	}
	_, _ = response.Write(encoded)
}

func required(name string) string {
	value := os.Getenv(name)
	if value == "" {
		log.Fatalf("%s is required", name)
	}
	return value
}

var _ = fmt.Sprintf
var _ = strconv.IntSize
