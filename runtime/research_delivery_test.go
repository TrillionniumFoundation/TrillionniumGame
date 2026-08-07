package main

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/runtime"
)

type scriptedResearchClient struct {
	bodies  [][]byte
	paths   []string
	handler func(*http.Request, []byte) (*http.Response, error)
}

func (client *scriptedResearchClient) Do(request *http.Request) (*http.Response, error) {
	body, err := io.ReadAll(request.Body)
	if err != nil {
		return nil, err
	}
	client.bodies = append(client.bodies, append([]byte(nil), body...))
	client.paths = append(client.paths, request.URL.Path)
	return client.handler(request, body)
}

func jsonHTTPResponse(status int, value any) *http.Response {
	body, _ := json.Marshal(value)
	return &http.Response{
		StatusCode: status,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(bytesReader(body)),
	}
}

type byteReader struct {
	value []byte
	off   int
}

func bytesReader(value []byte) *byteReader { return &byteReader{value: value} }
func (reader *byteReader) Read(target []byte) (int, error) {
	if reader.off == len(reader.value) {
		return 0, io.EOF
	}
	count := copy(target, reader.value[reader.off:])
	reader.off += count
	return count, nil
}

type researchDeliveryFixture struct {
	now              time.Time
	issuerPrivate    ed25519.PrivateKey
	issuerPublic     ed25519.PublicKey
	authorityPrivate ed25519.PrivateKey
	agentPrivate     []ed25519.PrivateKey
	authorizations   []researchcontract.SignedAuthorization
	engine           *researchcore.Engine
	store            *fakeStorage
	state            *researchMatchState
	module           *moduleRuntime
}

func researchTestKey(label string) (ed25519.PublicKey, ed25519.PrivateKey) {
	seed := sha256.Sum256([]byte(label))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	return privateKey.Public().(ed25519.PublicKey), privateKey
}

func newResearchDeliveryFixture(t *testing.T) *researchDeliveryFixture {
	t.Helper()
	now := time.Unix(1_800_100_000, 0).UTC()
	issuerPublic, issuerPrivate := researchTestKey("research-delivery-issuer")
	_, authorityPrivate := researchTestKey("research-delivery-authority")
	agentPrivate := make([]ed25519.PrivateKey, 3)
	claims := make([]researchcontract.AuthorizationClaim, 3)
	for index := range claims {
		publicKey, privateKey := researchTestKey(fmt.Sprintf("research-delivery-agent-%d", index+1))
		agentPrivate[index] = privateKey
		claims[index] = researchcontract.AuthorizationClaim{
			Schema: researchcontract.AuthorizationSchema, AuthorizationID: fmt.Sprintf("10000000-0000-4000-8000-%012d", index+1),
			SessionID: "research-delivery-test", TeamID: "30000000-0000-4000-8000-000000000001",
			PaperProjectID: "40000000-0000-4000-8000-000000000001", ChallengeID: "50000000-0000-4000-8000-000000000001",
			AgentID: fmt.Sprintf("agent-%d", index+1), AgentDID: fmt.Sprintf("did:trnm:agent-%d", index+1),
			AgentKeyID: fmt.Sprintf("agent-key-%d", index+1), AgentPublicKey: publicKey,
			SubjectUserID: fmt.Sprintf("20000000-0000-4000-8000-%012d", index+1), ParticipantSlot: uint32(index + 1),
			Role: fmt.Sprintf("role-%d", index+1), RosterVersion: 1, RosterRoot: researchcontract.NewDigest([]byte("placeholder")),
			RulesetHash: researchcontract.NewDigest([]byte("ruleset")), ChallengeSnapshotHash: researchcontract.NewDigest([]byte("challenge")),
			IssuedAtUnix: now.Unix(), ExpiresAtUnix: now.Add(15 * time.Minute).Unix(),
		}
	}
	provisional := make([]researchcontract.SignedAuthorization, len(claims))
	for index := range claims {
		provisional[index].Claim = claims[index]
	}
	root, err := researchcontract.RosterRoot(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID, 1, researchcontract.RosterEntries(provisional))
	if err != nil {
		t.Fatal(err)
	}
	authorizations := make([]researchcontract.SignedAuthorization, len(claims))
	for index := range claims {
		claims[index].RosterRoot = root
		authorizations[index], err = researchcontract.SignAuthorization(claims[index], "hepta-research-test", issuerPrivate)
		if err != nil {
			t.Fatal(err)
		}
	}
	engine, err := researchcore.NewSession(researchcore.NewSessionOptions{
		Authorizations: authorizations, TrustedIssuerKeys: map[string]ed25519.PublicKey{"hepta-research-test": issuerPublic},
		AuthorityKeyID: "nakama-research-test", AuthorityPrivateKey: authorityPrivate, Now: now,
	})
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	record, err := newStoredResearch(claims[0].SessionID, snapshot, authorizations, now.Unix())
	if err != nil {
		t.Fatal(err)
	}
	record.RuntimeGeneration = 1
	record.ExternalMatchID = "research-runtime-1"
	store := &fakeStorage{}
	version, err := createStoredResearch(context.Background(), store, record)
	if err != nil {
		t.Fatal(err)
	}
	module := &moduleRuntime{config: moduleConfig{
		issuerKeys: map[string]ed25519.PublicKey{"hepta-research-test": issuerPublic}, authorityKeyID: "nakama-research-test",
		authorityPrivateKey: authorityPrivate,
		authorityPublicKeys: map[string]ed25519.PublicKey{"nakama-research-test": authorityPrivate.Public().(ed25519.PublicKey)},
		operatorToken:       adapterOperatorToken, matchTickRate: 5,
		heptaBaseURL: "http://hepta.test", heptaServiceToken: adapterOperatorToken,
	}}
	state := &researchMatchState{engine: engine, record: record, storageVersion: version,
		instanceSessionID: record.LogicalSessionID, instanceGeneration: record.RuntimeGeneration,
		pendingAuthorization: map[string]pendingResearchAdmission{}, sessionAuthorization: map[string]string{},
		authorizationSessions: map[string]map[string]struct{}{}, sessionPresences: map[string]runtime.Presence{}}
	return &researchDeliveryFixture{now: now, issuerPrivate: issuerPrivate, issuerPublic: issuerPublic,
		authorityPrivate: authorityPrivate, agentPrivate: agentPrivate, authorizations: authorizations,
		engine: engine, store: store, state: state, module: module}
}

func signConsumptionACK(t *testing.T, outbox storedResearchConsumptionOutbox, key ed25519.PrivateKey) heptaResearchConsumptionReceipt {
	t.Helper()
	receipt := heptaResearchConsumptionReceipt{
		Schema:    researchcontract.HeptaAuthorizationConsumptionReceiptSchema,
		SessionID: outbox.SessionID, TeamID: outbox.TeamID, PaperProjectID: outbox.PaperProjectID, ChallengeID: outbox.ChallengeID,
		SessionRosterVersion: outbox.RosterVersion, RosterRoot: outbox.RosterRoot,
		AuthorizationIDs: append([]string(nil), outbox.AuthorizationIDs...), ConsumedAtUnix: outbox.ConsumedAtUnix,
		IssuerKeyID: "hepta-research-test",
	}
	message, err := receipt.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	receipt.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(key, message))
	return receipt
}

func (fixture *researchDeliveryFixture) applyAction(t *testing.T, slot int, actionType, payloadType string, reference researchcontract.Digest, at time.Time) {
	t.Helper()
	view := fixture.engine.View()
	participant := view.Participants[slot-1]
	claim := fixture.authorizations[slot-1].Claim
	action, err := researchcontract.SignAction(researchcontract.ActionEnvelope{
		Schema: researchcontract.ActionSchema, ActionID: fmt.Sprintf("delivery-action-%d-%d", slot, participant.LastActionSequence+1),
		AuthorizationID: claim.AuthorizationID, SessionID: claim.SessionID, TeamID: claim.TeamID,
		PaperProjectID: claim.PaperProjectID, ChallengeID: claim.ChallengeID, RosterVersion: claim.RosterVersion,
		ParticipantSlot: uint32(slot), ParticipantSequence: participant.LastActionSequence + 1,
		ExpectedSessionVersion: view.Version, IssuedAtUnix: at.Unix(), ActionType: actionType, PayloadType: payloadType,
		Payload: []byte(fmt.Sprintf("payload-%d-%s", slot, actionType)), ReferenceHash: reference, AgentKeyID: claim.AgentKeyID,
	}, fixture.agentPrivate[slot-1])
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.engine.ApplyAction(claim.SubjectUserID, action, at); err != nil {
		t.Fatal(err)
	}
}

func (fixture *researchDeliveryFixture) complete(t *testing.T) (researchcontract.SessionCompletedV1, storedResearchCompletionOutbox) {
	t.Helper()
	for index, authorization := range fixture.authorizations {
		if _, err := fixture.engine.Join(authorization.Claim.SubjectUserID, authorization.Claim.AuthorizationID, fixture.now.Add(time.Duration(index+1)*time.Second)); err != nil {
			t.Fatal(err)
		}
	}
	for slot := 1; slot <= 3; slot++ {
		fixture.applyAction(t, slot, researchcontract.ActionParticipantReady, researchcontract.PayloadParticipantReady,
			fixture.engine.View().RosterRoot, fixture.now.Add(time.Duration(10+slot)*time.Second))
	}
	fixture.applyAction(t, 1, researchcontract.ActionProposalSubmitted, researchcontract.PayloadProposalSubmitted,
		researchcontract.NewDigest([]byte("proposal")), fixture.now.Add(20*time.Second))
	releaseHash := researchcontract.NewDigest([]byte("release"))
	for slot := 1; slot <= 3; slot++ {
		fixture.applyAction(t, slot, researchcontract.ActionPaperReleaseAcknowledged, researchcontract.PayloadPaperReleaseAcknowledged,
			releaseHash, fixture.now.Add(time.Duration(30+slot)*time.Second))
	}
	completion, err := fixture.engine.Complete(researchcontract.TerminalFacts{
		ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("bundle")),
		PaperReleaseCandidateHash: releaseHash, ContributionLedgerHash: researchcontract.NewDigest([]byte("ledger")),
	}, fixture.now.Add(40*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	outbox, err := newStoredResearchCompletionOutbox(completion, fixture.engine.Events(), fixture.engine.AuthorityPublicKey())
	if err != nil {
		t.Fatal(err)
	}
	return completion, outbox
}

func signCompletionACK(t *testing.T, completion researchcontract.SessionCompletedV1, key ed25519.PrivateKey) heptaResearchCompletionReceipt {
	t.Helper()
	receipt := heptaResearchCompletionReceipt{
		Schema: researchcontract.HeptaCompletionReceiptSchema, CommitmentID: completion.CommitmentID,
		SessionID: completion.SessionID, TeamID: completion.TeamID, PaperProjectID: completion.PaperProjectID, ChallengeID: completion.ChallengeID,
		RosterVersion: completion.RosterVersion, RosterRoot: completion.RosterRoot, EventCount: completion.EventCount,
		EventRoot: completion.EventRoot, ArchiveHash: completion.ArchiveHash, RulesetHash: completion.RulesetHash,
		ChallengeSnapshotHash: completion.ChallengeSnapshotHash, NakamaAuthorityKeyID: completion.AuthorityKeyID,
		TerminalFacts: completion.TerminalFacts, VerifiedAtUnix: completion.CompletedAtUnix + 1, IssuerKeyID: "hepta-research-test",
	}
	message, err := receipt.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	receipt.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(key, message))
	return receipt
}

func TestConsumptionOutboxRetriesExactBytesAndRestoresOnlySignedACK(t *testing.T) {
	fixture := newResearchDeliveryFixture(t)
	outage := errors.New("Hepta unavailable")
	receipt := signConsumptionACK(t, fixture.state.record.ConsumptionOutboxes[0], fixture.issuerPrivate)
	calls := 0
	client := &scriptedResearchClient{handler: func(_ *http.Request, _ []byte) (*http.Response, error) {
		calls++
		if calls == 1 {
			return nil, outage
		}
		return jsonHTTPResponse(http.StatusOK, receipt), nil
	}}
	fixture.module.httpClient = client
	if err := fixture.module.deliverPendingResearchConsumption(context.Background(), &fakeLogger{}, fixture.store, fixture.state); !errors.Is(err, outage) {
		t.Fatalf("outage was not surfaced: %v", err)
	}
	if fixture.state.record.ConsumptionOutboxes[0].DeliveredAtUnix != nil {
		t.Fatal("unsigned/failed consumption callback was marked delivered")
	}
	loaded, err := loadStoredResearch(context.Background(), fixture.store, fixture.state.record.LogicalSessionID)
	if err != nil {
		t.Fatal(err)
	}
	restarted, err := fixture.module.restoreStoredResearch(loaded.record)
	if err != nil {
		t.Fatal(err)
	}
	fixture.state = &researchMatchState{engine: restarted, record: loaded.record, storageVersion: loaded.version}
	if err := fixture.module.deliverPendingResearchConsumption(context.Background(), &fakeLogger{}, fixture.store, fixture.state); err != nil {
		t.Fatal(err)
	}
	if len(client.bodies) != 2 || !reflect.DeepEqual(client.bodies[0], client.bodies[1]) {
		t.Fatal("consumption retry changed the durable request bytes")
	}
	if _, err := fixture.module.restoreStoredResearch(fixture.state.record); err != nil {
		t.Fatalf("signed persisted consumption ACK did not restore: %v", err)
	}

	tampered := cloneStoredResearchSession(fixture.state.record)
	var changed heptaResearchConsumptionReceipt
	receiptBody, _ := base64.StdEncoding.DecodeString(tampered.ConsumptionOutboxes[0].ReceiptBodyBase64)
	if err := json.Unmarshal(receiptBody, &changed); err != nil {
		t.Fatal(err)
	}
	changed.ConsumedAtUnix++
	changedBody, _ := json.Marshal(changed)
	digest := sha256.Sum256(changedBody)
	tampered.ConsumptionOutboxes[0].ReceiptBodyBase64 = base64.StdEncoding.EncodeToString(changedBody)
	tampered.ConsumptionOutboxes[0].ReceiptSHA256 = hex.EncodeToString(digest[:])
	if _, err := fixture.module.restoreStoredResearch(tampered); err == nil {
		t.Fatal("restart accepted a checksummed but signature-invalid consumption ACK")
	}
}

func TestCompletionOutboxSurvivesOutageRestartAndGenerationExpiryUntilSignedACK(t *testing.T) {
	fixture := newResearchDeliveryFixture(t)
	consumptionACK := signConsumptionACK(t, fixture.state.record.ConsumptionOutboxes[0], fixture.issuerPrivate)
	fixture.module.httpClient = &scriptedResearchClient{handler: func(_ *http.Request, _ []byte) (*http.Response, error) {
		return jsonHTTPResponse(http.StatusOK, consumptionACK), nil
	}}
	if err := fixture.module.deliverPendingResearchConsumption(context.Background(), &fakeLogger{}, fixture.store, fixture.state); err != nil {
		t.Fatal(err)
	}
	completion, completionOutbox := fixture.complete(t)
	updated := cloneStoredResearchSession(fixture.state.record)
	updated.setSnapshot(mustResearchSnapshot(t, fixture.engine))
	updated.CompletionOutbox = &completionOutbox
	version, err := updateStoredResearch(context.Background(), fixture.store, updated, fixture.state.storageVersion)
	if err != nil {
		t.Fatal(err)
	}
	fixture.state.record, fixture.state.storageVersion = updated, version

	outage := errors.New("Hepta completion endpoint down")
	receipt := signCompletionACK(t, completion, fixture.issuerPrivate)
	calls := 0
	client := &scriptedResearchClient{handler: func(_ *http.Request, _ []byte) (*http.Response, error) {
		calls++
		if calls <= 2 {
			return nil, outage
		}
		return jsonHTTPResponse(http.StatusCreated, receipt), nil
	}}
	fixture.module.httpClient = client
	fixture.state.nextDeliveryAttempt = time.Time{}
	if err := fixture.module.deliverPendingResearchCompletion(context.Background(), &fakeLogger{}, fixture.store, fixture.state); !errors.Is(err, outage) {
		t.Fatalf("initial completion outage was not surfaced: %v", err)
	}
	boundary := int64(fixture.module.config.matchTickRate) * maximumRuntimeGenerationSeconds
	if shouldTerminateResearchRuntime(fixture.state, boundary, fixture.module.config.matchTickRate) {
		t.Fatal("completed match with pending callback terminated at generation expiry")
	}

	loaded, err := loadStoredResearch(context.Background(), fixture.store, fixture.state.record.LogicalSessionID)
	if err != nil {
		t.Fatal(err)
	}
	restarted, err := fixture.module.restoreStoredResearch(loaded.record)
	if err != nil {
		t.Fatal(err)
	}
	fixture.state = &researchMatchState{engine: restarted, record: loaded.record, storageVersion: loaded.version}
	if err := fixture.module.deliverPendingResearchCompletion(context.Background(), &fakeLogger{}, fixture.store, fixture.state); !errors.Is(err, outage) {
		t.Fatalf("second outage was not preserved across restart: %v", err)
	}
	if err := fixture.module.deliverPendingResearchCompletion(context.Background(), &fakeLogger{}, fixture.store, fixture.state); err != nil {
		t.Fatal(err)
	}
	if len(client.bodies) != 3 || !reflect.DeepEqual(client.bodies[0], client.bodies[1]) || !reflect.DeepEqual(client.bodies[1], client.bodies[2]) {
		t.Fatal("completion retry changed exact durable body/idempotency bytes")
	}
	if _, err := fixture.module.restoreStoredResearch(fixture.state.record); err != nil {
		t.Fatalf("signed persisted completion ACK did not restore: %v", err)
	}
	if !shouldTerminateResearchRuntime(fixture.state, boundary, fixture.module.config.matchTickRate) {
		t.Fatal("completed match remained live after signed ACK was durably persisted")
	}

	for name, mutate := range map[string]func(*heptaResearchCompletionReceipt){
		"signature": func(value *heptaResearchCompletionReceipt) {
			raw, _ := base64.StdEncoding.DecodeString(value.Signature)
			raw[0] ^= 1
			value.Signature = base64.StdEncoding.EncodeToString(raw)
		},
		"ruleset": func(value *heptaResearchCompletionReceipt) {
			value.RulesetHash = researchcontract.NewDigest([]byte("tampered"))
		},
	} {
		t.Run(name, func(t *testing.T) {
			tampered := cloneStoredResearchSession(fixture.state.record)
			receiptBody, _ := base64.StdEncoding.DecodeString(tampered.CompletionOutbox.ReceiptBodyBase64)
			var changed heptaResearchCompletionReceipt
			if err := json.Unmarshal(receiptBody, &changed); err != nil {
				t.Fatal(err)
			}
			mutate(&changed)
			changedBody, _ := json.Marshal(changed)
			digest := sha256.Sum256(changedBody)
			tampered.CompletionOutbox.ReceiptBodyBase64 = base64.StdEncoding.EncodeToString(changedBody)
			tampered.CompletionOutbox.ReceiptSHA256 = hex.EncodeToString(digest[:])
			if _, err := fixture.module.restoreStoredResearch(tampered); err == nil {
				t.Fatal("restart accepted a checksummed but invalid completion ACK")
			}
		})
	}
}

func TestDelayedCompletionSignalReplaysHistoricalEvidenceWithoutReplacingOutbox(t *testing.T) {
	fixture := newResearchDeliveryFixture(t)
	completion, completionOutbox := fixture.complete(t)
	updated := cloneStoredResearchSession(fixture.state.record)
	updated.setSnapshot(mustResearchSnapshot(t, fixture.engine))
	updated.CompletionOutbox = &completionOutbox
	version, err := updateStoredResearch(context.Background(), fixture.store, updated, fixture.state.storageVersion)
	if err != nil {
		t.Fatal(err)
	}

	k1Public, k1Private := researchTestKey("research-delayed-completion-k1")
	fixture.module.config.authorityKeyID = "nakama-research-k1"
	fixture.module.config.authorityPrivateKey = k1Private
	fixture.module.config.authorityPublicKeys = map[string]ed25519.PublicKey{
		"nakama-research-test": fixture.authorityPrivate.Public().(ed25519.PublicKey),
		"nakama-research-k1":   k1Public,
	}
	restored, err := fixture.module.restoreStoredResearch(updated)
	if err != nil {
		t.Fatal(err)
	}
	state := &researchMatchState{
		engine: restored, record: updated, storageVersion: version,
		instanceSessionID: updated.LogicalSessionID, instanceGeneration: updated.RuntimeGeneration,
	}
	signalBody, _ := json.Marshal(researchSignal{
		Schema: "trnm.nakama.research-session.signal.v1", Action: "complete",
		LogicalSessionID: updated.LogicalSessionID, RuntimeGeneration: updated.RuntimeGeneration,
		OperatorToken: adapterOperatorToken, Facts: &completion.TerminalFacts,
	})
	match := &researchMatch{module: fixture.module}
	_, first := match.MatchSignal(context.Background(), &fakeLogger{}, nil, nil, &fakeDispatcher{}, 0, state, string(signalBody))
	_, second := match.MatchSignal(context.Background(), &fakeLogger{}, nil, nil, &fakeDispatcher{}, 0, state, string(signalBody))
	if first != second || first == "" {
		t.Fatal("delayed completion signal did not replay exact evidence bytes")
	}
	var evidence researchEvidenceResponse
	if decodeJSONStrict(first, &evidence) != nil || !reflect.DeepEqual(evidence.Completion, completion) ||
		evidence.AuthorityPublicKey != base64.StdEncoding.EncodeToString(fixture.authorityPrivate.Public().(ed25519.PublicKey)) {
		t.Fatal("delayed completion replay did not use the historical completion authority")
	}
	if state.storageVersion != version || !reflect.DeepEqual(state.record.CompletionOutbox, &completionOutbox) {
		t.Fatal("delayed completion replay replaced or rewrote the durable completion outbox")
	}
}

func mustResearchSnapshot(t *testing.T, engine *researchcore.Engine) []byte {
	t.Helper()
	snapshot, err := engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	return snapshot
}
