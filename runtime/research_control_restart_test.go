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
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/api"
	"github.com/heroiclabs/nakama-common/runtime"
)

type controlTestNakama struct {
	runtime.NakamaModule
	store  *controlTestStorage
	active map[string]bool
}

func (nakama *controlTestNakama) StorageRead(ctx context.Context, reads []*runtime.StorageRead) ([]*api.StorageObject, error) {
	return nakama.store.StorageRead(ctx, reads)
}

func (nakama *controlTestNakama) StorageWrite(ctx context.Context, writes []*runtime.StorageWrite) ([]*api.StorageObjectAck, error) {
	return nakama.store.StorageWrite(ctx, writes)
}

func (nakama *controlTestNakama) StorageDelete(_ context.Context, deletes []*runtime.StorageDelete) error {
	for _, deletion := range deletes {
		object := nakama.store.objects[controlStorageKey(deletion.Collection, deletion.Key)]
		if object == nil || object.Version != deletion.Version {
			return runtime.ErrStorageRejectedVersion
		}
	}
	for _, deletion := range deletes {
		delete(nakama.store.objects, controlStorageKey(deletion.Collection, deletion.Key))
	}
	return nil
}

func (nakama *controlTestNakama) MatchGet(_ context.Context, id string) (*api.Match, error) {
	if nakama.active[id] {
		return &api.Match{MatchId: id}, nil
	}
	return nil, nil
}

type controlRestartFixture struct {
	now                 time.Time
	module              *moduleRuntime
	match               *researchMatch
	store               *controlTestStorage
	nakama              *controlTestNakama
	issuerPrivate       ed25519.PrivateKey
	controlPrivate      ed25519.PrivateKey
	authorityPrivate    ed25519.PrivateKey
	initialAgents       []ed25519.PrivateKey
	replacementAgents   []ed25519.PrivateKey
	initial             []researchcontract.SignedAuthorization
	replacement         []researchcontract.SignedAuthorization
	authorizationSetOne string
	authorizationSetTwo string
}

func controlRestartAuthorizations(t *testing.T, now time.Time, version uint64, offset int,
	issuer ed25519.PrivateKey, agents []ed25519.PrivateKey) []researchcontract.SignedAuthorization {
	t.Helper()
	claims := make([]researchcontract.AuthorizationClaim, len(agents))
	for index, privateKey := range agents {
		agentKeyVersion := uint64(1)
		if version > 1 && index == 2 {
			agentKeyVersion = version
		}
		claims[index] = researchcontract.AuthorizationClaim{
			Schema: researchcontract.AuthorizationSchema, AuthorizationID: fmt.Sprintf("10000000-0000-4000-8000-%012d", offset+index+1),
			SessionID: "paper-raid-control-restart", TeamID: "30000000-0000-4000-8000-000000000001",
			PaperProjectID: "40000000-0000-4000-8000-000000000001", ChallengeID: "50000000-0000-4000-8000-000000000001",
			AgentID: fmt.Sprintf("agent-%d", index+1), AgentDID: fmt.Sprintf("did:trnm:agent-%d", index+1),
			AgentKeyID: fmt.Sprintf("agent-key-%d-v%d", index+1, agentKeyVersion), AgentPublicKey: privateKey.Public().(ed25519.PublicKey),
			SubjectUserID: fmt.Sprintf("60000000-0000-4000-8000-%012d", index+1), ParticipantSlot: uint32(index + 1),
			Role: fmt.Sprintf("paper-role-%d", index+1), RosterVersion: version,
			RosterRoot: researchcontract.NewDigest([]byte("placeholder")), RulesetHash: researchcontract.NewDigest([]byte("control-restart-ruleset")),
			ChallengeSnapshotHash: researchcontract.NewDigest([]byte("control-restart-challenge")),
			IssuedAtUnix:          now.Add(-time.Hour).Unix(), ExpiresAtUnix: now.Add(time.Hour).Unix(),
		}
	}
	provisional := make([]researchcontract.SignedAuthorization, len(claims))
	for index := range claims {
		provisional[index].Claim = claims[index]
	}
	root, err := researchcontract.RosterRoot(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID,
		version, researchcontract.RosterEntries(provisional))
	if err != nil {
		t.Fatal(err)
	}
	result := make([]researchcontract.SignedAuthorization, len(claims))
	for index := range claims {
		claims[index].RosterRoot = root
		result[index], err = researchcontract.SignAuthorization(claims[index], "hepta-authorization-restart", issuer)
		if err != nil {
			t.Fatal(err)
		}
	}
	return result
}

func newControlRestartFixture(t *testing.T) *controlRestartFixture {
	t.Helper()
	now := time.Now().UTC().Add(-2 * time.Minute).Truncate(time.Second)
	issuerPublic, issuerPrivate := researchTestKey("control-restart-authorization-issuer")
	controlPublic, controlPrivate := researchTestKey("control-restart-control-issuer")
	_, authorityPrivate := researchTestKey("control-restart-authority")
	initialAgents := make([]ed25519.PrivateKey, 3)
	replacementAgents := make([]ed25519.PrivateKey, 3)
	for index := range initialAgents {
		_, initialAgents[index] = researchTestKey(fmt.Sprintf("control-restart-agent-%d-v1", index+1))
		replacementAgents[index] = initialAgents[index]
	}
	_, replacementAgents[2] = researchTestKey("control-restart-agent-3-v2")
	initial := controlRestartAuthorizations(t, now, 1, 0, issuerPrivate, initialAgents)
	replacement := controlRestartAuthorizations(t, now, 2, 100, issuerPrivate, replacementAgents)
	store := &controlTestStorage{}
	nakama := &controlTestNakama{store: store, active: map[string]bool{"research-runtime-1": true}}
	module := &moduleRuntime{config: moduleConfig{
		issuerKeys:        map[string]ed25519.PublicKey{"hepta-authorization-restart": issuerPublic},
		controlIssuerKeys: map[string]ed25519.PublicKey{"hepta-control-restart": controlPublic},
		authorityKeyID:    "nakama-control-restart", authorityPrivateKey: authorityPrivate,
		authorityPublicKeys: map[string]ed25519.PublicKey{
			"nakama-control-restart": authorityPrivate.Public().(ed25519.PublicKey),
		},
		operatorToken: adapterOperatorToken, matchTickRate: 5,
		heptaBaseURL: "http://hepta.invalid", heptaServiceToken: adapterOperatorToken,
	}}
	module.httpClient = &scriptedResearchClient{handler: func(_ *http.Request, _ []byte) (*http.Response, error) {
		return nil, errors.New("intentional callback outage")
	}}
	return &controlRestartFixture{
		now: now, module: module, match: &researchMatch{module: module}, store: store, nakama: nakama,
		issuerPrivate: issuerPrivate, controlPrivate: controlPrivate, authorityPrivate: authorityPrivate,
		initialAgents: initialAgents, replacementAgents: replacementAgents, initial: initial, replacement: replacement,
		authorizationSetOne: "20000000-0000-4000-8000-000000000001",
		authorizationSetTwo: "20000000-0000-4000-8000-000000000002",
	}
}

func (fixture *controlRestartFixture) signControl(t *testing.T, operation, commandID, setID string,
	rosterVersion uint64, business []byte) researchcontract.SignedResearchControlV2 {
	t.Helper()
	target, err := researchcontract.ResearchControlTargetRPC(operation)
	if err != nil {
		t.Fatal(err)
	}
	control, err := researchcontract.SignResearchControlV2(researchcontract.ResearchControlClaimV2{
		Schema: researchcontract.ResearchControlClaimSchemaV2, CommandID: commandID, Operation: operation, TargetRPC: target,
		SessionID: fixture.initial[0].Claim.SessionID, SessionRosterVersion: rosterVersion, AuthorizationSetID: setID,
		PayloadHash: researchcontract.NewDigest(business), Audience: researchcontract.ResearchControlAudienceV2,
		IssuedAtUnix: time.Now().UTC().Unix(), ExpiresAtUnix: time.Now().UTC().Unix() + 120, IssuerKeyID: "hepta-control-restart",
	}, fixture.controlPrivate)
	if err != nil {
		t.Fatal(err)
	}
	return control
}

func (fixture *controlRestartFixture) initialStoredSession(t *testing.T) storedResearchSession {
	t.Helper()
	engine, err := researchcore.NewSession(researchcore.NewSessionOptions{
		Authorizations: fixture.initial, TrustedIssuerKeys: fixture.module.config.issuerKeys,
		AuthorityKeyID: fixture.module.config.authorityKeyID, AuthorityPrivateKey: fixture.authorityPrivate, Now: fixture.now,
	})
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	record, err := newStoredResearch(fixture.initial[0].Claim.SessionID, snapshot, fixture.initial, fixture.now.Unix())
	if err != nil {
		t.Fatal(err)
	}
	record.ControlAuthorizationSetID = fixture.authorizationSetOne
	return record
}

func (fixture *controlRestartFixture) reloadState(t *testing.T) *researchMatchState {
	t.Helper()
	stored, err := loadStoredResearch(context.Background(), fixture.store, fixture.initial[0].Claim.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	engine, err := fixture.module.restoreStoredResearch(stored.record)
	if err != nil {
		t.Fatal(err)
	}
	return &researchMatchState{
		engine: engine, record: stored.record, storageVersion: stored.version,
		instanceSessionID: stored.record.LogicalSessionID, instanceGeneration: stored.record.RuntimeGeneration,
		pendingAuthorization: map[string]pendingResearchAdmission{}, sessionAuthorization: map[string]string{},
		authorizationSessions: map[string]map[string]struct{}{}, sessionPresences: map[string]runtime.Presence{},
	}
}

func (fixture *controlRestartFixture) signal(t *testing.T, state *researchMatchState, commandID, operation string) (interface{}, string) {
	t.Helper()
	encoded, err := json.Marshal(researchSignal{
		Schema: "trnm.nakama.research-session.signal.v1", Action: operation,
		LogicalSessionID: state.record.LogicalSessionID, RuntimeGeneration: state.record.RuntimeGeneration,
		OperatorToken: fixture.module.config.operatorToken, ControlCommandID: commandID,
	})
	if err != nil {
		t.Fatal(err)
	}
	return fixture.match.MatchSignal(context.Background(), &fakeLogger{}, nil, fixture.nakama,
		&fakeDispatcher{}, 0, state, string(encoded))
}

func TestRestartResearchControlSIGKILLCreateAndResumeAfterRuntimeBeforeReceipt(t *testing.T) {
	fixture := newControlRestartFixture(t)
	createRequest := researchCreateRequestV2{
		Schema:             researchcontract.ResearchControlCreateRequestSchemaV2,
		AuthorizationSetID: fixture.authorizationSetOne, Authorizations: fixture.initial,
	}
	createBusiness, err := canonicalResearchCreateBusinessV2(createRequest)
	if err != nil {
		t.Fatal(err)
	}
	createRequest.Control = fixture.signControl(t, researchcontract.ResearchControlOperationCreate,
		"90000000-0000-4000-8000-000000000001", fixture.authorizationSetOne, 1, createBusiness)
	createCanonical, _ := json.Marshal(createRequest)
	createCommand := newStoredResearchControlCommand(createRequest.Control, createCanonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	session := fixture.initialStoredSession(t)
	sessionVersion, createCommandVersion, err := createStoredResearchWithControl(context.Background(), fixture.store,
		session, createCommand, fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}

	// MatchInit has made the runtime identity durable, then the process dies
	// before it can turn the command receipt from pending into applied.
	session.RuntimeGeneration = 1
	session.ExternalMatchID = "research-runtime-1"
	if _, err := updateStoredResearch(context.Background(), fixture.store, session, sessionVersion); err != nil {
		t.Fatal(err)
	}
	createResponse, err := fixture.module.recoverResearchRuntimeControl(context.Background(), fixture.nakama,
		versionedStoredResearchControl{record: createCommand, version: createCommandVersion})
	if err != nil || !strings.Contains(createResponse, `"operation":"create"`) {
		t.Fatalf("pending create did not recover after runtime durability: %v %s", err, createResponse)
	}

	resumeRequest := researchResumeRequestV2{
		Schema: researchcontract.ResearchControlResumeRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetOne,
	}
	resumeBusiness, _ := canonicalResearchResumeBusinessV2(resumeRequest)
	resumeRequest.Control = fixture.signControl(t, researchcontract.ResearchControlOperationResume,
		"90000000-0000-4000-8000-000000000002", fixture.authorizationSetOne, 1, resumeBusiness)
	resumeCanonical, _ := json.Marshal(resumeRequest)
	resumeCommand := newStoredResearchControlCommand(resumeRequest.Control, resumeCanonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	resumeVersion, err := createStoredResearchControl(context.Background(), fixture.store, resumeCommand, fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}
	first, err := fixture.module.recoverResearchRuntimeControl(context.Background(), fixture.nakama,
		versionedStoredResearchControl{record: resumeCommand, version: resumeVersion})
	if err != nil {
		t.Fatal(err)
	}
	applied, err := loadStoredResearchControl(context.Background(), fixture.store, resumeCommand.CommandID,
		fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}
	second, err := applied.record.response()
	if err != nil || first != second || !strings.Contains(first, `"operation":"resume"`) {
		t.Fatal("resume retry after the runtime/receipt SIGKILL window did not return exact stored bytes")
	}
}

func TestAppliedResearchControlRejectsResponseTamperWithRecomputedChecksum(t *testing.T) {
	fixture := newControlRestartFixture(t)
	session := fixture.initialStoredSession(t)
	session.RuntimeGeneration = 1
	session.ExternalMatchID = "research-runtime-1"
	if _, err := createStoredResearch(context.Background(), fixture.store, session); err != nil {
		t.Fatal(err)
	}

	request := researchResumeRequestV2{
		Schema: researchcontract.ResearchControlResumeRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetOne,
	}
	business, err := canonicalResearchResumeBusinessV2(request)
	if err != nil {
		t.Fatal(err)
	}
	request.Control = fixture.signControl(t, researchcontract.ResearchControlOperationResume,
		"90000000-0000-4000-8000-000000000005", fixture.authorizationSetOne, 1, business)
	canonical, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	command := newStoredResearchControlCommand(request.Control, canonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	version, err := createStoredResearchControl(context.Background(), fixture.store, command,
		fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fixture.module.recoverResearchRuntimeControl(context.Background(), fixture.nakama,
		versionedStoredResearchControl{record: command, version: version}); err != nil {
		t.Fatal(err)
	}

	applied, err := loadStoredResearchControl(context.Background(), fixture.store, command.CommandID,
		fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}
	responseBody, err := base64.StdEncoding.Strict().DecodeString(applied.record.ResponseBodyBase64)
	if err != nil {
		t.Fatal(err)
	}
	var wrapper researchControlResultV2
	var runtimeResult researchRuntimeResponse
	if decodeJSONStrict(string(responseBody), &wrapper) != nil ||
		decodeJSONStrict(string(wrapper.Result), &runtimeResult) != nil {
		t.Fatal("could not decode applied response fixture")
	}
	if runtimeResult.Status == researchcore.StatusPaused {
		runtimeResult.Status = researchcore.StatusReady
	} else {
		runtimeResult.Status = researchcore.StatusPaused
	}
	wrapper.Result, err = json.Marshal(runtimeResult)
	if err != nil {
		t.Fatal(err)
	}
	tamperedBody, err := json.Marshal(wrapper)
	if err != nil {
		t.Fatal(err)
	}
	tamperedChecksum := sha256.Sum256(tamperedBody)
	applied.record.ResponseBodyBase64 = base64.StdEncoding.EncodeToString(tamperedBody)
	applied.record.ResponseSHA256 = hex.EncodeToString(tamperedChecksum[:])
	if _, err := applied.record.response(); err != nil {
		t.Fatalf("tamper fixture did not preserve checksum and structural validity: %v", err)
	}
	value, err := json.Marshal(applied.record)
	if err != nil {
		t.Fatal(err)
	}
	fixture.store.objects[controlStorageKey(researchControlStorageCollection, command.CommandID)].Value = string(value)

	_, response, found, err := fixture.module.existingResearchControl(context.Background(), fixture.store,
		request.Control, canonical)
	if !found || response != "" || err == nil || !strings.Contains(err.Error(), "authority signature") {
		t.Fatalf("checksum-recomputed response tamper was not rejected by the semantic seal: found=%v response=%q err=%v",
			found, response, err)
	}
}

func TestAppliedResearchControlReplaysWithHistoricalPublicKeyOnly(t *testing.T) {
	fixture := newControlRestartFixture(t)
	session := fixture.initialStoredSession(t)
	session.RuntimeGeneration = 1
	session.ExternalMatchID = "research-runtime-1"
	if _, err := createStoredResearch(context.Background(), fixture.store, session); err != nil {
		t.Fatal(err)
	}
	request := researchResumeRequestV2{
		Schema: researchcontract.ResearchControlResumeRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetOne,
	}
	business, _ := canonicalResearchResumeBusinessV2(request)
	request.Control = fixture.signControl(t, researchcontract.ResearchControlOperationResume,
		"90000000-0000-4000-8000-000000000006", fixture.authorizationSetOne, 1, business)
	canonical, _ := json.Marshal(request)
	command := newStoredResearchControlCommand(request.Control, canonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	commandVersion, err := createStoredResearchControl(context.Background(), fixture.store, command,
		fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}
	first, err := fixture.module.recoverResearchRuntimeControl(context.Background(), fixture.nakama,
		versionedStoredResearchControl{record: command, version: commandVersion})
	if err != nil {
		t.Fatal(err)
	}

	k0Public := fixture.authorityPrivate.Public().(ed25519.PublicKey)
	k1Public, k1Private := researchTestKey("control-restart-authority-k1")
	fixture.module.config.authorityKeyID = "nakama-control-restart-k1"
	fixture.module.config.authorityPrivateKey = k1Private
	fixture.module.config.authorityPublicKeys = map[string]ed25519.PublicKey{
		"nakama-control-restart":    k0Public,
		"nakama-control-restart-k1": k1Public,
	}
	stored, err := loadStoredResearch(context.Background(), fixture.store, session.LogicalSessionID)
	if err != nil {
		t.Fatal(err)
	}
	engine, err := fixture.module.restoreStoredResearch(stored.record)
	if err != nil {
		t.Fatal(err)
	}
	resigned := cloneStoredResearchSession(stored.record)
	resigned.setSnapshot(mustResearchSnapshot(t, engine))
	if _, err := updateStoredResearch(context.Background(), fixture.store, resigned, stored.version); err != nil {
		t.Fatal(err)
	}

	_, replay, found, err := fixture.module.existingResearchControl(context.Background(), fixture.store, request.Control, canonical)
	if err != nil || !found || replay != first {
		t.Fatalf("historical applied response did not replay with K0 public only: found=%v err=%v", found, err)
	}
	delete(fixture.module.config.authorityPublicKeys, "nakama-control-restart")
	_, replay, found, err = fixture.module.existingResearchControl(context.Background(), fixture.store, request.Control, canonical)
	if !found || replay != "" || !errors.Is(err, researchcore.ErrAuthorityVerificationKeyUnavailable) {
		t.Fatalf("historical applied response survived K0 public removal: found=%v replay=%q err=%v", found, replay, err)
	}
}

func TestAppliedLegacyV2ControlReplaysExactBytesWithoutWrites(t *testing.T) {
	fixture := newControlRestartFixture(t)
	session := fixture.initialStoredSession(t)
	session.RuntimeGeneration = 1
	session.ExternalMatchID = "legacy-v2-runtime"
	if _, err := createStoredResearch(context.Background(), fixture.store, session); err != nil {
		t.Fatal(err)
	}
	engine, err := fixture.module.restoreStoredResearch(session)
	if err != nil {
		t.Fatal(err)
	}
	request := researchResumeRequestV2{
		Schema: researchcontract.ResearchControlResumeRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetOne,
	}
	business, err := canonicalResearchResumeBusinessV2(request)
	if err != nil {
		t.Fatal(err)
	}
	request.Control = fixture.signControl(t, researchcontract.ResearchControlOperationResume,
		"90000000-0000-4000-8000-000000000008", fixture.authorizationSetOne, 1, business)
	canonical, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	acceptedAt := time.Now().UTC().Unix()
	legacy := appliedLegacyStoredResearchControlV2(t, request.Control, canonical, acceptedAt, acceptedAt+1,
		researchRuntimeFor(session, engine.View(), session.ExternalMatchID), fixture.module.config.authorityKeyID,
		fixture.authorityPrivate)
	legacyValue, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	// The old v2 response remains signed by K0. Before replaying it, model the
	// state-preserving activation: verify the K0 snapshot from the public
	// registry, continue with active K1, and persist a K1-signed snapshot. This
	// proves v2 replay depends on the historical public capability carried by
	// the frozen response rather than on a retired private key or the current
	// snapshot wrapper.
	const historicalAuthorityKeyID = "nakama-control-restart"
	const activeAuthorityKeyID = "nakama-control-bridge-k1"
	activePublic, activePrivate := researchTestKey("research-control-bridge-k1")
	fixture.module.config.authorityKeyID = activeAuthorityKeyID
	fixture.module.config.authorityPrivateKey = activePrivate
	fixture.module.config.authorityPublicKeys = map[string]ed25519.PublicKey{
		historicalAuthorityKeyID: fixture.authorityPrivate.Public().(ed25519.PublicKey),
		activeAuthorityKeyID:     activePublic,
	}
	storedSession, err := loadStoredResearch(context.Background(), fixture.store, session.LogicalSessionID)
	if err != nil {
		t.Fatal(err)
	}
	continuedEngine, err := fixture.module.restoreStoredResearch(storedSession.record)
	if err != nil {
		t.Fatal(err)
	}
	continuedSession := cloneStoredResearchSession(storedSession.record)
	continuedSession.setSnapshot(mustResearchSnapshot(t, continuedEngine))
	if _, err := updateStoredResearch(context.Background(), fixture.store, continuedSession,
		storedSession.version); err != nil {
		t.Fatal(err)
	}
	continuedSessionValue, err := json.Marshal(continuedSession)
	if err != nil {
		t.Fatal(err)
	}
	continuedSessionRaw := string(continuedSessionValue)
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		legacy.CommandID, string(legacyValue), &continuedSessionRaw, fixture.module.config); err != nil {
		t.Fatalf("valid applied legacy v2 row blocked activation: %v", err)
	}
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		legacy.CommandID, string(legacyValue), nil, fixture.module.config); err == nil ||
		!strings.Contains(err.Error(), "no durable session") {
		t.Fatalf("applied legacy v2 row without its durable session passed activation: %v", err)
	}
	wrongResult := researchRuntimeFor(session, engine.View(), session.ExternalMatchID)
	wrongResult.RosterRoot = researchcontract.NewDigest([]byte("wrong-legacy-v2-roster"))
	wrongLegacy := appliedLegacyStoredResearchControlV2(t, request.Control, canonical, acceptedAt,
		acceptedAt+1, wrongResult, historicalAuthorityKeyID, fixture.authorityPrivate)
	wrongLegacyValue, err := json.Marshal(wrongLegacy)
	if err != nil {
		t.Fatal(err)
	}
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		wrongLegacy.CommandID, string(wrongLegacyValue), &continuedSessionRaw, fixture.module.config); err == nil ||
		!strings.Contains(err.Error(), "roster_root differs") {
		t.Fatalf("legacy v2 response with a wrong durable roster root passed activation: %v", err)
	}
	controlKey := controlStorageKey(researchControlStorageCollection, legacy.CommandID)
	fixture.store.objects[controlKey] = &api.StorageObject{
		Collection: researchControlStorageCollection, Key: legacy.CommandID, UserId: "",
		Value: string(legacyValue), Version: "legacy-v2-applied",
	}
	expectedResponse, err := legacy.response()
	if err != nil {
		t.Fatal(err)
	}
	valuesBefore := make(map[string][2]string, len(fixture.store.objects))
	for key, object := range fixture.store.objects {
		valuesBefore[key] = [2]string{object.Value, object.Version}
	}
	versionBefore := fixture.store.version

	for attempt := 0; attempt < 2; attempt++ {
		stored, response, found, err := fixture.module.existingResearchControl(context.Background(), fixture.store,
			request.Control, canonical)
		if err != nil || !found || response != expectedResponse || stored.legacyV2 == nil ||
			stored.rawValue != string(legacyValue) {
			t.Fatalf("legacy v2 exact replay failed on attempt %d: found=%v response=%q err=%v", attempt, found,
				response, err)
		}
	}
	if fixture.store.version != versionBefore || len(fixture.store.objects) != len(valuesBefore) {
		t.Fatal("legacy v2 replay performed a storage write")
	}
	for key, before := range valuesBefore {
		object := fixture.store.objects[key]
		if object == nil || object.Value != before[0] || object.Version != before[1] {
			t.Fatalf("legacy v2 replay changed storage object %q", key)
		}
	}

	delete(fixture.module.config.authorityPublicKeys, historicalAuthorityKeyID)
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		legacy.CommandID, string(legacyValue), &continuedSessionRaw, fixture.module.config); !errors.Is(err,
		researchcore.ErrAuthorityVerificationKeyUnavailable) {
		t.Fatalf("activation scan accepted legacy v2 with a missing public key: %v", err)
	}
	_, response, found, err := fixture.module.existingResearchControl(context.Background(), fixture.store,
		request.Control, canonical)
	if !found || response != "" || !errors.Is(err, researchcore.ErrAuthorityVerificationKeyUnavailable) {
		t.Fatalf("legacy v2 replay survived missing historical public key: found=%v response=%q err=%v", found,
			response, err)
	}
	if fixture.store.version != versionBefore || fixture.store.objects[controlKey].Value != string(legacyValue) ||
		fixture.store.objects[controlKey].Version != "legacy-v2-applied" {
		t.Fatal("missing-key legacy v2 replay failure changed persisted bytes or version")
	}
}

func TestPendingV3ControlActivationRequiresRecoverableDurableSession(t *testing.T) {
	fixture := newControlRestartFixture(t)
	session := fixture.initialStoredSession(t)
	sessionValue, err := json.Marshal(session)
	if err != nil {
		t.Fatal(err)
	}
	sessionRaw := string(sessionValue)
	request := researchResumeRequestV2{
		Schema: researchcontract.ResearchControlResumeRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetOne,
	}
	business, err := canonicalResearchResumeBusinessV2(request)
	if err != nil {
		t.Fatal(err)
	}
	request.Control = fixture.signControl(t, researchcontract.ResearchControlOperationResume,
		"90000000-0000-4000-8000-000000000009", fixture.authorizationSetOne, 1, business)
	canonical, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	command := newStoredResearchControlCommand(request.Control, canonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	commandValue, err := json.Marshal(command)
	if err != nil {
		t.Fatal(err)
	}
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		command.CommandID, string(commandValue), &sessionRaw, fixture.module.config); err != nil {
		t.Fatalf("recoverable pending v3 command blocked activation: %v", err)
	}
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		command.CommandID, string(commandValue), nil, fixture.module.config); err == nil ||
		!strings.Contains(err.Error(), "no durable session") {
		t.Fatalf("pending v3 command without its durable session passed activation: %v", err)
	}
	corruptSession := strings.Replace(sessionRaw, session.SnapshotSHA256,
		strings.Repeat("0", len(session.SnapshotSHA256)), 1)
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		command.CommandID, string(commandValue), &corruptSession, fixture.module.config); err == nil ||
		!strings.Contains(err.Error(), "failed verification") {
		t.Fatalf("pending v3 command with a corrupt durable session passed activation: %v", err)
	}
	wrongEpochSession := cloneStoredResearchSession(session)
	wrongEpochSession.ControlAuthorizationSetID = fixture.authorizationSetTwo
	wrongEpochValue, err := json.Marshal(wrongEpochSession)
	if err != nil {
		t.Fatal(err)
	}
	wrongEpochRaw := string(wrongEpochValue)
	if err := assessStoredResearchControlActivationAgainstSession(nakamaSystemStorageOwnerID,
		command.CommandID, string(commandValue), &wrongEpochRaw, fixture.module.config); err == nil ||
		!strings.Contains(err.Error(), "different durable roster epoch") {
		t.Fatalf("pending v3 command with the wrong durable epoch passed activation: %v", err)
	}
}

func TestK1ControlWrapperReplaysK0CompletionWithoutSessionOrOutboxMutation(t *testing.T) {
	fixture := newControlRestartFixture(t)
	session := fixture.initialStoredSession(t)
	session.RuntimeGeneration = 1
	session.ExternalMatchID = "research-runtime-1"
	if _, err := createStoredResearch(context.Background(), fixture.store, session); err != nil {
		t.Fatal(err)
	}
	state := fixture.reloadState(t)
	joinAt := time.Now().UTC().Add(-time.Minute)
	for index, authorization := range fixture.initial {
		if _, err := state.engine.Join(authorization.Claim.SubjectUserID, authorization.Claim.AuthorizationID,
			joinAt.Add(time.Duration(index)*time.Second)); err != nil {
			t.Fatal(err)
		}
	}
	release := applyControlRestartPaperActions(t, state, fixture.initial, fixture.initialAgents)
	facts := researchcontract.TerminalFacts{
		ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("k0-control-bundle")),
		PaperReleaseCandidateHash: release, ContributionLedgerHash: researchcontract.NewDigest([]byte("k0-control-ledger")),
	}
	completion, err := state.engine.Complete(facts, time.Now().UTC().Add(time.Second))
	if err != nil {
		t.Fatal(err)
	}
	k0Public, ok := state.engine.CompletionAuthorityPublicKey()
	if !ok || completion.AuthorityKeyID != fixture.module.config.authorityKeyID {
		t.Fatal("K0 completion authority was unavailable")
	}
	outbox, err := newStoredResearchCompletionOutbox(completion, state.engine.Events(), k0Public)
	if err != nil {
		t.Fatal(err)
	}
	completedRecord := cloneStoredResearchSession(state.record)
	completedRecord.setSnapshot(mustResearchSnapshot(t, state.engine))
	completedRecord.CompletionOutbox = &outbox
	completedVersion, err := updateStoredResearch(context.Background(), fixture.store, completedRecord, state.storageVersion)
	if err != nil {
		t.Fatal(err)
	}

	k1Public, k1Private := researchTestKey("control-wrapper-authority-k1")
	fixture.module.config.authorityKeyID = "nakama-control-wrapper-k1"
	fixture.module.config.authorityPrivateKey = k1Private
	fixture.module.config.authorityPublicKeys = map[string]ed25519.PublicKey{
		"nakama-control-restart":    k0Public,
		"nakama-control-wrapper-k1": k1Public,
	}
	rotatedEngine, err := fixture.module.restoreStoredResearch(completedRecord)
	if err != nil {
		t.Fatalf("K1 failed to restore the K0 completion: %v", err)
	}
	rotatedRecord := cloneStoredResearchSession(completedRecord)
	rotatedRecord.setSnapshot(mustResearchSnapshot(t, rotatedEngine))
	rotatedVersion, err := updateStoredResearch(context.Background(), fixture.store, rotatedRecord, completedVersion)
	if err != nil {
		t.Fatal(err)
	}

	request := researchCompleteRequestV2{
		Schema:             researchcontract.ResearchControlCompleteRequestSchemaV2,
		LogicalSessionID:   rotatedRecord.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetOne, Facts: facts,
	}
	business, _ := canonicalResearchCompleteBusinessV2(request)
	request.Control = fixture.signControl(t, researchcontract.ResearchControlOperationComplete,
		"90000000-0000-4000-8000-000000000007", fixture.authorizationSetOne, 1, business)
	canonical, _ := json.Marshal(request)
	pending := newStoredResearchControlCommand(request.Control, canonical, time.Now().UTC().Unix(),
		fixture.module.config.authorityKeyID)
	pendingVersion, err := createStoredResearchControl(context.Background(), fixture.store, pending,
		fixture.module.config.controlIssuerKeys)
	if err != nil {
		t.Fatal(err)
	}
	state = &researchMatchState{
		engine: rotatedEngine, record: rotatedRecord, storageVersion: rotatedVersion,
		instanceSessionID: rotatedRecord.LogicalSessionID, instanceGeneration: rotatedRecord.RuntimeGeneration,
		pendingAuthorization: map[string]pendingResearchAdmission{}, sessionAuthorization: map[string]string{},
		authorizationSessions: map[string]map[string]struct{}{}, sessionPresences: map[string]runtime.Presence{},
	}
	sessionKey := controlStorageKey(researchStorageCollection, rotatedRecord.LogicalSessionID)
	controlKey := controlStorageKey(researchControlStorageCollection, pending.CommandID)
	sessionValueBefore := fixture.store.objects[sessionKey].Value
	sessionVersionBefore := fixture.store.objects[sessionKey].Version
	outboxBefore := *rotatedRecord.CompletionOutbox

	_, first := fixture.signal(t, state, pending.CommandID, researchcontract.ResearchControlOperationComplete)
	if strings.Contains(first, `"error"`) {
		t.Fatalf("K1 control wrapper over K0 completion failed: %s", first)
	}
	applied := fixture.store.objects[controlKey]
	appliedValue := applied.Value
	appliedVersion := applied.Version
	storeVersionAfterApply := fixture.store.version
	_, second := fixture.signal(t, state, pending.CommandID, researchcontract.ResearchControlOperationComplete)
	if first != second || fixture.store.version != storeVersionAfterApply ||
		fixture.store.objects[controlKey].Value != appliedValue || fixture.store.objects[controlKey].Version != appliedVersion {
		t.Fatal("applied K1 control command did not replay exact bytes without another write")
	}
	var wrapper researchControlResultV2
	var evidence researchEvidenceResponse
	if decodeJSONStrict(first, &wrapper) != nil || decodeJSONStrict(string(wrapper.Result), &evidence) != nil ||
		evidence.Completion.AuthorityKeyID != "nakama-control-restart" ||
		evidence.AuthorityPublicKey != base64.StdEncoding.EncodeToString(k0Public) {
		t.Fatal("K1 control receipt did not preserve its embedded K0 completion evidence")
	}
	appliedCommand, err := loadStoredResearchControl(context.Background(), fixture.store, pending.CommandID,
		fixture.module.config.controlIssuerKeys)
	if err != nil || appliedCommand.record.ResponseAuthorityKeyID != "nakama-control-wrapper-k1" ||
		appliedCommand.record.ExpectedResponseAuthorityKeyID != "nakama-control-wrapper-k1" {
		t.Fatal("outer control response was not signed by the immutable K1 command epoch")
	}
	if fixture.store.objects[sessionKey].Value != sessionValueBefore ||
		fixture.store.objects[sessionKey].Version != sessionVersionBefore ||
		!reflect.DeepEqual(state.record.CompletionOutbox, &outboxBefore) {
		t.Fatal("completed control replay rewrote the session snapshot or K0 completion outbox")
	}

	loser := pending
	if err := loser.applyResult(researchEvidenceFor(rotatedRecord, completion, k0Public), time.Now().UTC(),
		"nakama-control-wrapper-k1", k1Private); err != nil {
		t.Fatal(err)
	}
	if _, err := updateStoredResearchControl(context.Background(), fixture.store, pending, loser,
		pendingVersion, fixture.module.config.controlIssuerKeys); err == nil ||
		!strings.Contains(err.Error(), "version conflict") {
		t.Fatalf("stale K1 control OCC writer was accepted: %v", err)
	}
	if fixture.store.objects[sessionKey].Value != sessionValueBefore ||
		fixture.store.objects[sessionKey].Version != sessionVersionBefore ||
		fixture.store.objects[controlKey].Value != appliedValue || fixture.store.objects[controlKey].Version != appliedVersion {
		t.Fatal("rejected stale K1 control OCC write changed session, outbox, or receipt bytes")
	}

	delete(fixture.module.config.authorityPublicKeys, "nakama-control-restart")
	if _, err := fixture.module.verifiedResearchControlResponse(context.Background(), fixture.store, appliedCommand); err == nil ||
		!errors.Is(err, researchcore.ErrAuthorityVerificationKeyUnavailable) {
		t.Fatalf("K1 control/K0 completion replay did not expose the retired K0 public key: %v", err)
	}
	if fixture.store.objects[sessionKey].Value != sessionValueBefore ||
		fixture.store.objects[sessionKey].Version != sessionVersionBefore ||
		fixture.store.objects[controlKey].Value != appliedValue || fixture.store.objects[controlKey].Version != appliedVersion {
		t.Fatal("missing-K0 control replay changed session, outbox, or receipt bytes")
	}
}

func applyControlRestartPaperActions(t *testing.T, state *researchMatchState,
	authorizations []researchcontract.SignedAuthorization, agents []ed25519.PrivateKey) researchcontract.Digest {
	t.Helper()
	base := time.Now().UTC()
	for _, authorization := range authorizations {
		if _, err := state.engine.Join(authorization.Claim.SubjectUserID, authorization.Claim.AuthorizationID,
			base); err != nil {
			t.Fatal(err)
		}
	}
	apply := func(slot int, actionType, payloadType string, reference researchcontract.Digest, at time.Time) {
		view := state.engine.View()
		participant := view.Participants[slot-1]
		claim := authorizations[slot-1].Claim
		action, err := researchcontract.SignAction(researchcontract.ActionEnvelope{
			Schema: researchcontract.ActionSchema, ActionID: fmt.Sprintf("control-restart-action-%d-%d", slot, participant.LastActionSequence+1),
			AuthorizationID: claim.AuthorizationID, SessionID: claim.SessionID, TeamID: claim.TeamID,
			PaperProjectID: claim.PaperProjectID, ChallengeID: claim.ChallengeID, RosterVersion: claim.RosterVersion,
			ParticipantSlot: uint32(slot), ParticipantSequence: participant.LastActionSequence + 1,
			ExpectedSessionVersion: view.Version, IssuedAtUnix: at.Unix(), ActionType: actionType, PayloadType: payloadType,
			Payload: []byte(fmt.Sprintf("control-restart-%d-%s", slot, actionType)), ReferenceHash: reference,
			AgentKeyID: claim.AgentKeyID,
		}, agents[slot-1])
		if err != nil {
			t.Fatal(err)
		}
		if _, err := state.engine.ApplyAction(claim.SubjectUserID, action, at); err != nil {
			t.Fatal(err)
		}
	}
	for slot := 1; slot <= len(authorizations); slot++ {
		apply(slot, researchcontract.ActionParticipantReady, researchcontract.PayloadParticipantReady,
			state.engine.View().RosterRoot, base)
	}
	apply(1, researchcontract.ActionProposalSubmitted, researchcontract.PayloadProposalSubmitted,
		researchcontract.NewDigest([]byte("control-restart-proposal")), base)
	release := researchcontract.NewDigest([]byte("control-restart-release"))
	for slot := 1; slot <= len(authorizations); slot++ {
		apply(slot, researchcontract.ActionPaperReleaseAcknowledged, researchcontract.PayloadPaperReleaseAcknowledged,
			release, base)
	}
	return release
}

func TestRestartResearchControlSIGKILLReplaceAndCompleteSignalWindows(t *testing.T) {
	fixture := newControlRestartFixture(t)
	session := fixture.initialStoredSession(t)
	session.RuntimeGeneration = 1
	session.ExternalMatchID = "research-runtime-1"
	if _, err := createStoredResearch(context.Background(), fixture.store, session); err != nil {
		t.Fatal(err)
	}

	replaceRequest := researchReplaceRequestV2{
		Schema: researchcontract.ResearchControlReplaceRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetTwo, Authorizations: fixture.replacement,
	}
	replaceBusiness, _ := canonicalResearchReplaceBusinessV2(replaceRequest)
	replaceRequest.Control = fixture.signControl(t, researchcontract.ResearchControlOperationReplace,
		"90000000-0000-4000-8000-000000000003", fixture.authorizationSetTwo, 2, replaceBusiness)
	replaceCanonical, _ := json.Marshal(replaceRequest)
	replaceCommand := newStoredResearchControlCommand(replaceRequest.Control, replaceCanonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	if _, err := createStoredResearchControl(context.Background(), fixture.store, replaceCommand,
		fixture.module.config.controlIssuerKeys); err != nil {
		t.Fatal(err)
	}

	// The command reservation is durable but no signal result is. A failed
	// atomic batch models a SIGKILL before the storage commit becomes visible.
	state := fixture.reloadState(t)
	fixture.store.fail = errors.New("simulated SIGKILL before replacement atomic commit")
	_, failed := fixture.signal(t, state, replaceCommand.CommandID, researchcontract.ResearchControlOperationReplace)
	if !strings.Contains(failed, `"error"`) {
		t.Fatalf("replacement failpoint unexpectedly succeeded: %s", failed)
	}
	fixture.store.fail = nil
	pending, err := loadStoredResearchControl(context.Background(), fixture.store, replaceCommand.CommandID,
		fixture.module.config.controlIssuerKeys)
	if err != nil || pending.record.Status != researchControlStatusPending {
		t.Fatal("failed replacement atomic batch changed its pending command")
	}
	unchanged, _ := loadStoredResearch(context.Background(), fixture.store, session.LogicalSessionID)
	if unchanged.record.ControlAuthorizationSetID != fixture.authorizationSetOne {
		t.Fatal("failed replacement atomic batch partially changed the session")
	}

	state = fixture.reloadState(t)
	_, replaceResponse := fixture.signal(t, state, replaceCommand.CommandID, researchcontract.ResearchControlOperationReplace)
	if strings.Contains(replaceResponse, `"error"`) || !strings.Contains(replaceResponse, `"operation":"replace_roster"`) {
		t.Fatalf("pending replacement did not recover: %s", replaceResponse)
	}
	var replaceWrapper researchControlResultV2
	var replaceRuntime researchRuntimeResponse
	if decodeJSONStrict(replaceResponse, &replaceWrapper) != nil || decodeJSONStrict(string(replaceWrapper.Result), &replaceRuntime) != nil ||
		replaceRuntime.RosterVersion != 2 || replaceRuntime.RosterRoot != fixture.replacement[0].Claim.RosterRoot ||
		replaceRuntime.RosterRoot == fixture.initial[0].Claim.RosterRoot {
		t.Fatal("signed replacement result leaked the old snapshot roster")
	}
	replaced, err := loadStoredResearch(context.Background(), fixture.store, session.LogicalSessionID)
	if err != nil || replaced.record.ControlAuthorizationSetID != fixture.authorizationSetTwo {
		t.Fatal("replacement did not atomically advance the authorization set")
	}
	replacedEngine, err := fixture.module.restoreStoredResearch(replaced.record)
	if err != nil || replacedEngine.View().RosterVersion != 2 {
		t.Fatal("replacement roster was not durable")
	}

	state = fixture.reloadState(t)
	beforeActions, _ := state.engine.Snapshot()
	release := applyControlRestartPaperActions(t, state, fixture.replacement, fixture.replacementAgents)
	if err := fixture.match.persist(context.Background(), fixture.nakama, state, beforeActions); err != nil {
		t.Fatal(err)
	}
	facts := researchcontract.TerminalFacts{
		ResultCode: "paper_bundle_ready", PaperBundleHash: researchcontract.NewDigest([]byte("control-restart-bundle")),
		PaperReleaseCandidateHash: release, ContributionLedgerHash: researchcontract.NewDigest([]byte("control-restart-ledger")),
	}
	completeRequest := researchCompleteRequestV2{
		Schema: researchcontract.ResearchControlCompleteRequestSchemaV2, LogicalSessionID: session.LogicalSessionID,
		AuthorizationSetID: fixture.authorizationSetTwo, Facts: facts,
	}
	completeBusiness, _ := canonicalResearchCompleteBusinessV2(completeRequest)
	completeRequest.Control = fixture.signControl(t, researchcontract.ResearchControlOperationComplete,
		"90000000-0000-4000-8000-000000000004", fixture.authorizationSetTwo, 2, completeBusiness)
	completeCanonical, _ := json.Marshal(completeRequest)
	completeCommand := newStoredResearchControlCommand(completeRequest.Control, completeCanonical,
		time.Now().UTC().Unix(), fixture.module.config.authorityKeyID)
	if _, err := createStoredResearchControl(context.Background(), fixture.store, completeCommand,
		fixture.module.config.controlIssuerKeys); err != nil {
		t.Fatal(err)
	}

	state = fixture.reloadState(t)
	fixture.store.fail = errors.New("simulated SIGKILL before completion atomic commit")
	_, failed = fixture.signal(t, state, completeCommand.CommandID, researchcontract.ResearchControlOperationComplete)
	if !strings.Contains(failed, `"error"`) {
		t.Fatalf("completion failpoint unexpectedly succeeded: %s", failed)
	}
	fixture.store.fail = nil
	pending, err = loadStoredResearchControl(context.Background(), fixture.store, completeCommand.CommandID,
		fixture.module.config.controlIssuerKeys)
	if err != nil || pending.record.Status != researchControlStatusPending {
		t.Fatal("failed completion atomic batch changed its pending command")
	}
	uncompleted, _ := loadStoredResearch(context.Background(), fixture.store, session.LogicalSessionID)
	if uncompleted.record.CompletionOutbox != nil {
		t.Fatal("failed completion atomic batch partially published an outbox")
	}

	state = fixture.reloadState(t)
	_, first := fixture.signal(t, state, completeCommand.CommandID, researchcontract.ResearchControlOperationComplete)
	if strings.Contains(first, `"error"`) || !strings.Contains(first, `"operation":"complete"`) {
		t.Fatalf("pending completion did not recover: %s", first)
	}
	var completeWrapper researchControlResultV2
	var completeEvidence researchEvidenceResponse
	if decodeJSONStrict(first, &completeWrapper) != nil || decodeJSONStrict(string(completeWrapper.Result), &completeEvidence) != nil ||
		completeEvidence.Completion.RosterVersion != 2 || completeEvidence.Completion.RosterRoot != fixture.replacement[0].Claim.RosterRoot {
		t.Fatal("signed completion result did not bind the current replacement epoch")
	}
	state = fixture.reloadState(t)
	_, second := fixture.signal(t, state, completeCommand.CommandID, researchcontract.ResearchControlOperationComplete)
	if first != second {
		t.Fatal("completed command did not replay exact response bytes after restart")
	}
	completed, err := loadStoredResearch(context.Background(), fixture.store, session.LogicalSessionID)
	if err != nil || completed.record.CompletionOutbox == nil {
		t.Fatal("completion snapshot/outbox was not durable")
	}
	completedEngine, err := fixture.module.restoreStoredResearch(completed.record)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := completedEngine.Completion(); !ok {
		t.Fatal("completion command receipt was applied without terminal research evidence")
	}
}
