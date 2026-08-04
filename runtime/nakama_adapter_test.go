package main

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	matchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/core"
	"github.com/heroiclabs/nakama-common/runtime"
)

const adapterOperatorToken = "0123456789abcdef0123456789abcdef"

type adapterFixture struct {
	module       *moduleRuntime
	match        *authoritativeMatch
	state        *authoritativeMatchState
	store        *fakeStorage
	dispatcher   *fakeDispatcher
	logger       *fakeLogger
	facts        contract.TerminalFacts
	issuerKey    ed25519.PrivateKey
	authorityKey ed25519.PrivateKey
}

func adapterDeterministicKey(label string) (ed25519.PublicKey, ed25519.PrivateKey) {
	seed := sha256.Sum256([]byte(label))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	return privateKey.Public().(ed25519.PublicKey), privateKey
}

func newAdapterFixture(t *testing.T) *adapterFixture {
	t.Helper()
	issuerPublic, issuerPrivate := adapterDeterministicKey("adapter-issuer")
	authorityPublic, authorityPrivate := adapterDeterministicKey("adapter-authority")
	_ = authorityPublic
	now := time.Now().UTC().Truncate(time.Second)
	var authorizations [2]contract.SignedAuthorization
	agentPrivate := [2]ed25519.PrivateKey{}
	for index := range 2 {
		agentPublic, privateKey := adapterDeterministicKey("adapter-agent-" + string(rune('1'+index)))
		agentPrivate[index] = privateKey
		claim := contract.AuthorizationClaim{
			Schema: contract.AuthorizationSchema, AuthorizationID: "auth-" + string(rune('1'+index)),
			MatchID: "adapter-match-1", ChallengeID: "challenge-1", AgentID: "agent-" + string(rune('1'+index)),
			AgentDID: "did:trnm:agent-" + string(rune('1'+index)), AgentKeyID: "agent-key-" + string(rune('1'+index)),
			AgentPublicKey: agentPublic, SubjectUserID: "user-" + string(rune('1'+index)), ParticipantSlot: uint32(index + 1),
			Role: []string{"challenger", "defender"}[index], RulesetHash: contract.NewDigest([]byte("ruleset")),
			DatasetHash: contract.NewDigest([]byte("dataset")), ChallengeSnapshotHash: contract.NewDigest([]byte("challenge")),
			IssuedAtUnix: now.Add(-time.Hour).Unix(), ExpiresAtUnix: now.Add(time.Hour).Unix(),
		}
		var err error
		authorizations[index], err = contract.SignAuthorization(claim, "issuer-key-1", issuerPrivate)
		if err != nil {
			t.Fatal(err)
		}
	}
	engine, err := matchcore.NewMatch(matchcore.NewMatchOptions{
		Authorizations: authorizations, TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": issuerPublic},
		AuthorityKeyID: "authority-key-1", AuthorityPrivateKey: authorityPrivate, Now: now.Add(-10 * time.Second),
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := engine.Join("user-1", "auth-1", now.Add(-9*time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.Join("user-2", "auth-2", now.Add(-8*time.Second)); err != nil {
		t.Fatal(err)
	}
	command, err := contract.SignCommand(contract.CommandEnvelope{
		Schema: contract.CommandSchema, CommandID: "cmd-1", AuthorizationID: "auth-1", MatchID: "adapter-match-1",
		ChallengeID: "challenge-1", AgentID: "agent-1", ParticipantSlot: 1, ParticipantSequence: 1,
		ExpectedMatchVersion: 3, IssuedAtUnix: now.Add(-7 * time.Second).Unix(), PayloadType: "turn.v1",
		Payload: []byte("move-a"), AgentKeyID: "agent-key-1",
	}, agentPrivate[0])
	if err != nil {
		t.Fatal(err)
	}
	if _, err := engine.ApplyCommand("user-1", command, now.Add(-6*time.Second)); err != nil {
		t.Fatal(err)
	}
	module := &moduleRuntime{config: moduleConfig{
		issuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": issuerPublic}, authorityKeyID: "authority-key-1",
		authorityPrivateKey: authorityPrivate, operatorToken: adapterOperatorToken, matchTickRate: 5,
	}}
	snapshot, err := engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	record, err := newStoredMatch("adapter-match-1", snapshot)
	if err != nil {
		t.Fatal(err)
	}
	record.RuntimeGeneration = 7
	record.ExternalMatchID = "external-match-7"
	store := &fakeStorage{}
	version, err := createStoredMatch(context.Background(), store, record)
	if err != nil {
		t.Fatal(err)
	}
	state := &authoritativeMatchState{
		engine: engine, record: record, storageVersion: version, instanceLogicalMatchID: record.LogicalMatchID,
		instanceRuntimeGeneration: record.RuntimeGeneration, pendingJoinEvents: make(map[string]contract.MatchEvent),
	}
	return &adapterFixture{
		module: module, match: &authoritativeMatch{module: module}, state: state, store: store,
		dispatcher: &fakeDispatcher{}, logger: &fakeLogger{}, issuerKey: issuerPrivate, authorityKey: authorityPrivate,
		facts: contract.TerminalFacts{ResultCode: "decisive", WinnerSlot: 1, OutcomeHash: contract.NewDigest([]byte("outcome"))},
	}
}

type fakeDispatcher struct {
	opcodes []int64
	labels  []string
}

func (f *fakeDispatcher) BroadcastMessage(opcode int64, _ []byte, _ []runtime.Presence, _ runtime.Presence, _ bool) error {
	f.opcodes = append(f.opcodes, opcode)
	return nil
}
func (*fakeDispatcher) BroadcastMessageDeferred(int64, []byte, []runtime.Presence, runtime.Presence, bool) error {
	return nil
}
func (*fakeDispatcher) MatchKick([]runtime.Presence) error { return nil }
func (f *fakeDispatcher) MatchLabelUpdate(label string) error {
	f.labels = append(f.labels, label)
	return nil
}

type fakeLogger struct{}

func (*fakeLogger) Debug(string, ...interface{})                       {}
func (*fakeLogger) Info(string, ...interface{})                        {}
func (*fakeLogger) Warn(string, ...interface{})                        {}
func (*fakeLogger) Error(string, ...interface{})                       {}
func (f *fakeLogger) WithField(string, interface{}) runtime.Logger     { return f }
func (f *fakeLogger) WithFields(map[string]interface{}) runtime.Logger { return f }
func (*fakeLogger) Fields() map[string]interface{}                     { return nil }

func TestAdapterCompletionTerminatesAndPersistsReadableEvidence(t *testing.T) {
	f := newAdapterFixture(t)
	rawState, response := f.match.completeAndTerminate(context.Background(), f.logger, f.store, f.dispatcher, f.state, f.facts)
	if rawState != f.state {
		t.Fatal("completion did not preserve state long enough to return a successful signal response")
	}
	if strings.Contains(response, `"error"`) {
		t.Fatalf("completion failed: %s", response)
	}
	if !reflect.DeepEqual(f.dispatcher.opcodes, []int64{opCodeCompletion}) {
		t.Fatalf("completion broadcast mismatch: %v", f.dispatcher.opcodes)
	}
	if next := f.match.MatchLoop(context.Background(), f.logger, nil, nil, f.dispatcher, 1, f.state, nil); next != nil {
		t.Fatal("completed authoritative runtime did not terminate on its next loop tick")
	}
	loaded, err := loadStoredMatch(context.Background(), f.store, "adapter-match-1")
	if err != nil {
		t.Fatal(err)
	}
	restored, err := f.module.restoreStoredEngine(loaded.record)
	if err != nil {
		t.Fatal(err)
	}
	completion, completed := restored.Completion()
	if !completed || completion == nil || !reflect.DeepEqual(completion.TerminalFacts, f.facts) {
		t.Fatal("persisted completion evidence is not readable or lost terminal facts")
	}
	var evidence evidenceResponse
	if err := json.Unmarshal([]byte(response), &evidence); err != nil || evidence.LogicalMatchID != "adapter-match-1" || !reflect.DeepEqual(evidence.Completion, *completion) {
		t.Fatalf("completion response does not match persisted evidence: %v", err)
	}
}

func TestAdapterCompletionResponseAllowsResultCodeErrorAndBindsDurableEvidence(t *testing.T) {
	f := newAdapterFixture(t)
	f.facts.ResultCode = "error"
	_, response := f.match.completeAndTerminate(context.Background(), f.logger, f.store, f.dispatcher, f.state, f.facts)
	evidence, remoteError, err := decodeCompleteSignalResponse(response)
	if err != nil || remoteError != "" {
		t.Fatalf("legitimate result_code=error was confused with an error envelope: remote=%q err=%v", remoteError, err)
	}
	loaded, err := loadStoredMatch(context.Background(), f.store, "adapter-match-1")
	if err != nil {
		t.Fatal(err)
	}
	engine, err := f.module.restoreStoredEngine(loaded.record)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateCompletionSignalEvidence(evidence, loaded.record, engine, f.facts, 7); err != nil {
		t.Fatalf("valid completion response was not bound to durable evidence: %v", err)
	}

	mutations := map[string]func(*evidenceResponse){
		"schema":         func(value *evidenceResponse) { value.Schema = "wrong" },
		"logical match":  func(value *evidenceResponse) { value.LogicalMatchID = "other-match" },
		"generation":     func(value *evidenceResponse) { value.RuntimeGeneration++ },
		"external match": func(value *evidenceResponse) { value.ExternalMatchID = "other-runtime" },
		"authority key": func(value *evidenceResponse) {
			value.AuthorityPublicKey = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
		},
		"terminal facts": func(value *evidenceResponse) { value.Completion.TerminalFacts.WinnerSlot = 2 },
		"signature": func(value *evidenceResponse) {
			value.Completion.Signature = append([]byte(nil), value.Completion.Signature...)
			value.Completion.Signature[0] ^= 1
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			changed := evidence
			mutate(&changed)
			if err := validateCompletionSignalEvidence(changed, loaded.record, engine, f.facts, 7); err == nil {
				t.Fatal("inconsistent completion response was accepted")
			}
		})
	}
	if err := validateCompletionSignalEvidence(evidence, loaded.record, engine, f.facts, 8); err == nil {
		t.Fatal("completion response was accepted for a different signaled generation")
	}
}

func TestAdapterCompletionResponseRejectsMalformedMixedAndIncompleteJSON(t *testing.T) {
	tests := map[string]string{
		"not json":       `not-json`,
		"array":          `[]`,
		"empty object":   `{}`,
		"empty error":    `{"error":""}`,
		"mixed envelope": `{"error":"rejected","schema":"trnm.nakama.evidence.v1"}`,
		"missing fields": `{"schema":"trnm.nakama.evidence.v1","logical_match_id":"match-1"}`,
		"unknown field":  `{"schema":"trnm.nakama.evidence.v1","logical_match_id":"match-1","runtime_generation":1,"completion":{},"authority_public_key_base64":"x","unknown":true}`,
	}
	for name, response := range tests {
		t.Run(name, func(t *testing.T) {
			if _, _, err := decodeCompleteSignalResponse(response); err == nil {
				t.Fatal("malformed completion response was accepted")
			}
		})
	}
	evidence, remoteError, err := decodeCompleteSignalResponse(`{"error":"authoritative rejection"}`)
	if err != nil || remoteError != "authoritative rejection" || evidence.Schema != "" {
		t.Fatalf("valid error envelope was not decoded: evidence=%+v remote=%q err=%v", evidence, remoteError, err)
	}
}

func TestAdapterCompletionStorageFailureRollsBackAndTerminates(t *testing.T) {
	f := newAdapterFixture(t)
	f.store.writeErr = errors.New("storage unavailable")
	rawState, response := f.match.completeAndTerminate(context.Background(), f.logger, f.store, f.dispatcher, f.state, f.facts)
	if rawState != nil || !strings.Contains(response, `"error"`) {
		t.Fatalf("storage failure did not fail closed: state=%T response=%s", rawState, response)
	}
	if _, completed := f.state.engine.Completion(); completed || f.state.engine.View().Status != matchcore.StatusActive {
		t.Fatal("storage failure did not roll back in-memory completion")
	}
	loaded, err := loadStoredMatch(context.Background(), f.store, "adapter-match-1")
	if err != nil {
		t.Fatal(err)
	}
	restored, err := f.module.restoreStoredEngine(loaded.record)
	if err != nil || restored.View().Status != matchcore.StatusActive {
		t.Fatalf("storage failure changed durable state: %v", err)
	}
}

func TestAdapterCompletedFastPathValidatesTerminalFacts(t *testing.T) {
	f := newAdapterFixture(t)
	completion, err := f.state.engine.Complete(f.facts, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	f.state.record.setSnapshot(mustSnapshot(t, f.state.engine))
	response, err := completedEvidenceForFacts(f.state.record, f.state.engine, f.facts)
	if err != nil || !reflect.DeepEqual(response.Completion, completion) {
		t.Fatalf("matching completion retry failed: %v", err)
	}
	conflict := f.facts
	conflict.WinnerSlot = 2
	if _, err := completedEvidenceForFacts(f.state.record, f.state.engine, conflict); err == nil {
		t.Fatal("completed fast path accepted conflicting terminal facts")
	}
}

func TestAdapterStoredRecordBindsSignedSnapshotMatchID(t *testing.T) {
	f := newAdapterFixture(t)
	transplanted, err := newStoredMatch("different-match", mustSnapshot(t, f.state.engine))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.module.restoreStoredEngine(transplanted); err == nil {
		t.Fatal("signed snapshot was transplanted into a different logical match record")
	}
}

func TestAdapterCompletionSignalFencesMatchAndGeneration(t *testing.T) {
	f := newAdapterFixture(t)
	valid := completeSignalFor(f.state.record, completeMatchRequest{OperatorToken: adapterOperatorToken, Facts: f.facts})
	if valid.LogicalMatchID != "adapter-match-1" || valid.RuntimeGeneration != 7 {
		t.Fatal("RPC completion signal omitted its logical match or runtime generation binding")
	}
	if err := validateCompleteSignalBinding(valid, f.state); err != nil {
		t.Fatalf("valid signal binding rejected: %v", err)
	}
	wrongMatch := valid
	wrongMatch.LogicalMatchID = "other-match"
	if err := validateCompleteSignalBinding(wrongMatch, f.state); err == nil {
		t.Fatal("wrong logical match signal was accepted")
	}
	wrongGeneration := valid
	wrongGeneration.RuntimeGeneration++
	if err := validateCompleteSignalBinding(wrongGeneration, f.state); err == nil {
		t.Fatal("stale or future runtime generation signal was accepted")
	}
	encoded, _ := json.Marshal(wrongGeneration)
	returnedState, response := f.match.MatchSignal(context.Background(), f.logger, nil, nil, nil, 0, f.state, string(encoded))
	if returnedState != f.state || !strings.Contains(response, `"error"`) {
		t.Fatal("MatchSignal handler did not fence a mismatched generation")
	}
	f.state.record.RuntimeGeneration++
	if err := validateCompleteSignalBinding(valid, f.state); err == nil {
		t.Fatal("signal was accepted after outer record generation changed")
	}
}

func TestAdapterCreateRejectsNonExactAuthorizationCount(t *testing.T) {
	f := newAdapterFixture(t)
	for _, count := range []int{1, 3} {
		t.Run(string(rune('0'+count)), func(t *testing.T) {
			request := createMatchRequest{Schema: "trnm.nakama.create-match.v1", OperatorToken: adapterOperatorToken,
				Authorizations: make([]contract.SignedAuthorization, count)}
			payload, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := f.module.rpcCreateMatch(context.Background(), f.logger, nil, nil, string(payload)); err == nil {
				t.Fatalf("create accepted %d authorizations", count)
			}
		})
	}
}

func TestAdapterJoinMetadataIsStrict(t *testing.T) {
	f := newAdapterFixture(t)
	tests := map[string]map[string]string{
		"missing":      {},
		"empty":        {"authorization_id": ""},
		"oversized":    {"authorization_id": strings.Repeat("x", 513)},
		"nul":          {"authorization_id": "auth\x00id"},
		"invalid utf8": {"authorization_id": string([]byte{0xff})},
		"extra":        {"authorization_id": "auth-1", "unexpected": "value"},
	}
	for name, metadata := range tests {
		t.Run(name, func(t *testing.T) {
			state, accepted, reason := f.match.MatchJoinAttempt(context.Background(), f.logger, nil, nil, nil, 0, f.state, nil, metadata)
			if state != f.state || accepted || reason == "" {
				t.Fatalf("invalid join metadata was accepted: state=%T accepted=%v reason=%q", state, accepted, reason)
			}
		})
	}
}

func TestAdapterRejectsOversizedOperatorTokenOnEveryWireRequest(t *testing.T) {
	f := newAdapterFixture(t)
	oversized := strings.Repeat("x", maximumOperatorLength+1)
	tests := map[string]func(string) error{
		"create": func(token string) error {
			payload, _ := json.Marshal(createMatchRequest{Schema: "trnm.nakama.create-match.v1", OperatorToken: token,
				Authorizations: make([]contract.SignedAuthorization, 2)})
			_, err := f.module.rpcCreateMatch(context.Background(), f.logger, nil, nil, string(payload))
			return err
		},
		"resume": func(token string) error {
			payload, _ := json.Marshal(resumeMatchRequest{Schema: "trnm.nakama.resume-match.v1", OperatorToken: token, LogicalMatchID: "adapter-match-1"})
			_, err := f.module.rpcResumeMatch(context.Background(), f.logger, nil, nil, string(payload))
			return err
		},
		"evidence": func(token string) error {
			payload, _ := json.Marshal(evidenceRequest{Schema: "trnm.nakama.get-evidence.v1", OperatorToken: token, LogicalMatchID: "adapter-match-1"})
			_, err := f.module.rpcEvidence(context.Background(), f.logger, nil, nil, string(payload))
			return err
		},
		"complete": func(token string) error {
			payload, _ := json.Marshal(completeMatchRequest{Schema: "trnm.nakama.complete-match.v1", OperatorToken: token,
				LogicalMatchID: "adapter-match-1", Facts: f.facts})
			_, err := f.module.rpcComplete(context.Background(), f.logger, nil, nil, string(payload))
			return err
		},
	}
	for name, invoke := range tests {
		t.Run(name, func(t *testing.T) {
			if err := invoke(oversized); err == nil {
				t.Fatal("oversized operator token was accepted")
			}
		})
	}

	signal, _ := json.Marshal(completeSignal{Schema: "trnm.nakama.match-signal.v1", Action: "complete",
		LogicalMatchID: "adapter-match-1", RuntimeGeneration: 7, OperatorToken: oversized, Facts: f.facts})
	state, response := f.match.MatchSignal(context.Background(), f.logger, nil, nil, nil, 0, f.state, string(signal))
	if state != f.state || !strings.Contains(response, `"error"`) {
		t.Fatal("oversized match signal token was accepted")
	}
}

func TestAdapterCommandRejectionSanitizesReflectedFields(t *testing.T) {
	value := commandRejection(strings.Repeat("x", 513)+"\x00", strings.Repeat("r", 5000)+"\x00")
	if value.CommandID != "" {
		t.Fatal("invalid command id was reflected in rejection payload")
	}
	if len([]rune(value.Reason)) != 4096 || strings.ContainsRune(value.Reason, '\x00') {
		t.Fatal("command rejection reason violated its public schema")
	}
	valid := commandRejection("command-1", "rejected")
	if valid.CommandID != "command-1" || valid.Reason != "rejected" {
		t.Fatal("valid rejection fields changed during sanitization")
	}
}

func TestRuntimeGenerationAbsoluteLifetime(t *testing.T) {
	for _, tickRate := range []int{1, 5, 60} {
		boundary := int64(tickRate) * maximumRuntimeGenerationSeconds
		if runtimeGenerationExpired(boundary-1, tickRate) {
			t.Fatalf("tick rate %d expired before its six-hour boundary", tickRate)
		}
		if !runtimeGenerationExpired(boundary, tickRate) {
			t.Fatalf("tick rate %d did not expire at its six-hour boundary", tickRate)
		}
	}
	if !runtimeGenerationExpired(-1, 5) || !runtimeGenerationExpired(0, 0) {
		t.Fatal("invalid lifecycle inputs did not fail closed")
	}
}

func mustSnapshot(t *testing.T, engine *matchcore.Engine) []byte {
	t.Helper()
	snapshot, err := engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	return snapshot
}
