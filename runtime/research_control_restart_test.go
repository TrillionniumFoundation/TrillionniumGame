package main

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
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
	createCommand := newStoredResearchControlCommand(createRequest.Control, createCanonical, time.Now().UTC().Unix())
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
	resumeCommand := newStoredResearchControlCommand(resumeRequest.Control, resumeCanonical, time.Now().UTC().Unix())
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

func applyControlRestartPaperActions(t *testing.T, fixture *controlRestartFixture, state *researchMatchState) researchcontract.Digest {
	t.Helper()
	base := time.Now().UTC()
	for _, authorization := range fixture.replacement {
		if _, err := state.engine.Join(authorization.Claim.SubjectUserID, authorization.Claim.AuthorizationID,
			base); err != nil {
			t.Fatal(err)
		}
	}
	apply := func(slot int, actionType, payloadType string, reference researchcontract.Digest, at time.Time) {
		view := state.engine.View()
		participant := view.Participants[slot-1]
		claim := fixture.replacement[slot-1].Claim
		action, err := researchcontract.SignAction(researchcontract.ActionEnvelope{
			Schema: researchcontract.ActionSchema, ActionID: fmt.Sprintf("control-restart-action-%d-%d", slot, participant.LastActionSequence+1),
			AuthorizationID: claim.AuthorizationID, SessionID: claim.SessionID, TeamID: claim.TeamID,
			PaperProjectID: claim.PaperProjectID, ChallengeID: claim.ChallengeID, RosterVersion: claim.RosterVersion,
			ParticipantSlot: uint32(slot), ParticipantSequence: participant.LastActionSequence + 1,
			ExpectedSessionVersion: view.Version, IssuedAtUnix: at.Unix(), ActionType: actionType, PayloadType: payloadType,
			Payload: []byte(fmt.Sprintf("control-restart-%d-%s", slot, actionType)), ReferenceHash: reference,
			AgentKeyID: claim.AgentKeyID,
		}, fixture.replacementAgents[slot-1])
		if err != nil {
			t.Fatal(err)
		}
		if _, err := state.engine.ApplyAction(claim.SubjectUserID, action, at); err != nil {
			t.Fatal(err)
		}
	}
	for slot := 1; slot <= len(fixture.replacement); slot++ {
		apply(slot, researchcontract.ActionParticipantReady, researchcontract.PayloadParticipantReady,
			state.engine.View().RosterRoot, base)
	}
	apply(1, researchcontract.ActionProposalSubmitted, researchcontract.PayloadProposalSubmitted,
		researchcontract.NewDigest([]byte("control-restart-proposal")), base)
	release := researchcontract.NewDigest([]byte("control-restart-release"))
	for slot := 1; slot <= len(fixture.replacement); slot++ {
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
	replaceCommand := newStoredResearchControlCommand(replaceRequest.Control, replaceCanonical, time.Now().UTC().Unix())
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
	release := applyControlRestartPaperActions(t, fixture, state)
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
	completeCommand := newStoredResearchControlCommand(completeRequest.Control, completeCanonical, time.Now().UTC().Unix())
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
