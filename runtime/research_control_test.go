package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/api"
	"github.com/heroiclabs/nakama-common/runtime"
)

type controlTestStorage struct {
	objects map[string]*api.StorageObject
	version uint64
	fail    error
}

func controlStorageKey(collection, key string) string { return collection + "\x00" + key }

func (store *controlTestStorage) StorageRead(_ context.Context, reads []*runtime.StorageRead) ([]*api.StorageObject, error) {
	objects := make([]*api.StorageObject, 0, len(reads))
	for _, read := range reads {
		if object := store.objects[controlStorageKey(read.Collection, read.Key)]; object != nil {
			objects = append(objects, &api.StorageObject{
				Collection: object.Collection,
				Key:        object.Key,
				UserId:     object.UserId,
				Value:      object.Value,
				Version:    object.Version,
			})
		}
	}
	return objects, nil
}

func (store *controlTestStorage) StorageWrite(_ context.Context, writes []*runtime.StorageWrite) ([]*api.StorageObjectAck, error) {
	if store.fail != nil {
		return nil, store.fail
	}
	if store.objects == nil {
		store.objects = map[string]*api.StorageObject{}
	}
	for _, write := range writes {
		existing := store.objects[controlStorageKey(write.Collection, write.Key)]
		if (write.Version == "*" && existing != nil) || (write.Version != "*" && (existing == nil || existing.Version != write.Version)) {
			return nil, runtime.ErrStorageRejectedVersion
		}
	}
	acks := make([]*api.StorageObjectAck, len(writes))
	for index, write := range writes {
		store.version++
		version := fmt.Sprintf("control-v%d", store.version)
		store.objects[controlStorageKey(write.Collection, write.Key)] = &api.StorageObject{
			Collection: write.Collection, Key: write.Key, UserId: write.UserID, Value: write.Value, Version: version,
		}
		acks[index] = &api.StorageObjectAck{Collection: write.Collection, Key: write.Key, Version: version}
	}
	return acks, nil
}

func signedResumeRequestV2(t *testing.T, now int64) (researchResumeRequestV2, ed25519.PublicKey) {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	request := researchResumeRequestV2{
		Schema:           researchcontract.ResearchControlResumeRequestSchemaV2,
		LogicalSessionID: "paper-raid-control-storage", AuthorizationSetID: "20000000-0000-4000-8000-000000000001",
	}
	business, err := canonicalResearchResumeBusinessV2(request)
	if err != nil {
		t.Fatal(err)
	}
	request.Control, err = researchcontract.SignResearchControlV2(researchcontract.ResearchControlClaimV2{
		Schema: researchcontract.ResearchControlClaimSchemaV2, CommandID: "10000000-0000-4000-8000-000000000001",
		Operation: researchcontract.ResearchControlOperationResume, TargetRPC: researchcontract.ResearchControlRPCResumeV2,
		SessionID: request.LogicalSessionID, SessionRosterVersion: 1, AuthorizationSetID: request.AuthorizationSetID,
		PayloadHash: researchcontract.NewDigest(business), Audience: researchcontract.ResearchControlAudienceV2,
		IssuedAtUnix: now, ExpiresAtUnix: now + 120, IssuerKeyID: "hepta-control-test-v2",
	}, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return request, publicKey
}

func appliedLegacyStoredResearchControlV2(t *testing.T, control researchcontract.SignedResearchControlV2,
	canonicalRequest []byte, acceptedAt, appliedAt int64, result any, authorityKeyID string,
	authorityPrivateKey ed25519.PrivateKey) legacyStoredResearchControlCommandV2 {
	t.Helper()
	requestDigest := sha256.Sum256(canonicalRequest)
	claim := control.Claim
	record := legacyStoredResearchControlCommandV2{
		Schema: researchControlStorageSchemaV2, CommandID: claim.CommandID, Operation: claim.Operation,
		TargetRPC: claim.TargetRPC, SessionID: claim.SessionID, SessionRosterVersion: claim.SessionRosterVersion,
		AuthorizationSetID: claim.AuthorizationSetID, PayloadHash: claim.PayloadHash,
		RequestBodyBase64: base64.StdEncoding.EncodeToString(canonicalRequest),
		RequestSHA256: hex.EncodeToString(requestDigest[:]), AcceptedAtUnix: acceptedAt,
		Status: researchControlStatusApplied, ResponseAuthorityKeyID: authorityKeyID, AppliedAtUnix: &appliedAt,
	}
	resultBody, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	wrapper, err := json.Marshal(researchControlResultV2{
		Schema: researchControlResultSchema, CommandID: record.CommandID, Operation: record.Operation,
		TargetRPC: record.TargetRPC, Result: resultBody,
	})
	if err != nil {
		t.Fatal(err)
	}
	responseDigest := sha256.Sum256(wrapper)
	record.ResponseBodyBase64 = base64.StdEncoding.EncodeToString(wrapper)
	record.ResponseSHA256 = hex.EncodeToString(responseDigest[:])
	seal, err := record.responseSealBytes()
	if err != nil {
		t.Fatal(err)
	}
	record.ResponseSignatureBase64 = base64.StdEncoding.EncodeToString(ed25519.Sign(authorityPrivateKey, seal))
	return record
}

func TestLegacyStoredResearchControlV2FrozenWireAndSealGolden(t *testing.T) {
	// This vector is intentionally independent of legacyStoredResearchControlCommandV2
	// construction and responseSealBytes. The request is the tracked canonical
	// resume vector; the response and seal were generated once with the test-only
	// deterministic authority seed 0x40..0x5f. Changing the frozen v2 field set,
	// order, domain, or signature bytes must break this test rather than silently
	// regenerating matching fixtures through the production serializer.
	const recordJSON = `{"schema":"trnm.nakama.stored-research-control-command.v2","command_id":"90000000-0000-4000-8000-000000000002","operation":"resume","target_rpc":"trnm_research_session_resume_v2","session_id":"research-control-golden-001","session_roster_version":1,"authorization_set_id":"20000000-0000-4000-8000-000000000001","payload_hash":"sha256:33a5f2ffd580ba1abf1ff5b67bc5ff606eabf2c49bc4a423f40a6a6a69d7000f","request_body_base64":"eyJzY2hlbWEiOiJ0cm5tLm5ha2FtYS5yZXNlYXJjaC1zZXNzaW9uLnJlc3VtZS52MiIsImxvZ2ljYWxfc2Vzc2lvbl9pZCI6InJlc2VhcmNoLWNvbnRyb2wtZ29sZGVuLTAwMSIsImF1dGhvcml6YXRpb25fc2V0X2lkIjoiMjAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAxIiwiY29udHJvbCI6eyJjbGFpbSI6eyJzY2hlbWEiOiJ0cm5tLm5ha2FtYS5yZXNlYXJjaC1jb250cm9sLmNsYWltLnYyIiwiY29tbWFuZF9pZCI6IjkwMDAwMDAwLTAwMDAtNDAwMC04MDAwLTAwMDAwMDAwMDAwMiIsIm9wZXJhdGlvbiI6InJlc3VtZSIsInRhcmdldF9ycGMiOiJ0cm5tX3Jlc2VhcmNoX3Nlc3Npb25fcmVzdW1lX3YyIiwic2Vzc2lvbl9pZCI6InJlc2VhcmNoLWNvbnRyb2wtZ29sZGVuLTAwMSIsInNlc3Npb25fcm9zdGVyX3ZlcnNpb24iOjEsImF1dGhvcml6YXRpb25fc2V0X2lkIjoiMjAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAxIiwicGF5bG9hZF9oYXNoIjoic2hhMjU2OjMzYTVmMmZmZDU4MGJhMWFiZjFmZjViNjdiYzVmZjYwNmVhYmYyYzQ5YmM0YTQyM2Y0MGE2YTZhNjlkNzAwMGYiLCJhdWRpZW5jZSI6InRybm06bmFrYW1hOnJlc2VhcmNoLWNvbnRyb2w6djIiLCJpc3N1ZWRfYXRfdW5peCI6MTgwMDAwMDAwMCwiZXhwaXJlc19hdF91bml4IjoxODAwMDAwMTIwLCJpc3N1ZXJfa2V5X2lkIjoiaGVwdGEtY29udHJvbC1nb2xkZW4tdjIifSwic2lnbmF0dXJlIjoiTzJIRmJjV3dITFNLL1pDU0lBS3RqaE5semxCelJyMDVNRG9LYlRzdlZ5YldPUnBabkdidFg0WEN4T3o4c3krY3dzamQ1N2JTUU9VY2pxa2JHdWl1RHc9PSJ9fQ==","request_sha256":"5164f707e93fe03db3f764c6cb0b744df44bf7642b33923e5d5679be6b440e3c","accepted_at_unix":1800000000,"status":"applied","response_body_base64":"eyJzY2hlbWEiOiJ0cm5tLm5ha2FtYS5yZXNlYXJjaC1jb250cm9sLnJlc3VsdC52MiIsImNvbW1hbmRfaWQiOiI5MDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDIiLCJvcGVyYXRpb24iOiJyZXN1bWUiLCJ0YXJnZXRfcnBjIjoidHJubV9yZXNlYXJjaF9zZXNzaW9uX3Jlc3VtZV92MiIsInJlc3VsdCI6eyJzY2hlbWEiOiJ0cm5tLm5ha2FtYS5yZXNlYXJjaC1zZXNzaW9uLm1hdGNoLXJ1bnRpbWUudjEiLCJsb2dpY2FsX3Nlc3Npb25faWQiOiJyZXNlYXJjaC1jb250cm9sLWdvbGRlbi0wMDEiLCJleHRlcm5hbF9tYXRjaF9pZCI6InJ1bnRpbWUtZ29sZGVuIiwicnVudGltZV9nZW5lcmF0aW9uIjoxLCJzdGF0dXMiOiJwYXVzZWQiLCJzZXNzaW9uX3ZlcnNpb24iOjgsInJvc3Rlcl92ZXJzaW9uIjoxLCJyb3N0ZXJfcm9vdCI6InNoYTI1NjozMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzIn19","response_sha256":"8e82a9f3e2f1193b9257668ba9a40a84d0800db0abb035fbd96a490b1f6c911f","response_authority_key_id":"nakama-legacy-golden-k0","response_signature_base64":"tiAsoQruWBmM1KSfXBU43gioM9zPZxiWsRRYGY8VnT0DOCkdDN2W8Hl3ihYx+iaK+OhGoD/PcGiKT5NhaiRYAA==","applied_at_unix":1800000123}`
	const responseJSON = `{"schema":"trnm.nakama.research-control.result.v2","command_id":"90000000-0000-4000-8000-000000000002","operation":"resume","target_rpc":"trnm_research_session_resume_v2","result":{"schema":"trnm.nakama.research-session.match-runtime.v1","logical_session_id":"research-control-golden-001","external_match_id":"runtime-golden","runtime_generation":1,"status":"paused","session_version":8,"roster_version":1,"roster_root":"sha256:3333333333333333333333333333333333333333333333333333333333333333"}}`
	const sealJSON = `{"schema":"trnm.nakama.research-control.response-seal.v2","command_id":"90000000-0000-4000-8000-000000000002","operation":"resume","target_rpc":"trnm_research_session_resume_v2","session_id":"research-control-golden-001","session_roster_version":1,"authorization_set_id":"20000000-0000-4000-8000-000000000001","payload_hash":"sha256:33a5f2ffd580ba1abf1ff5b67bc5ff606eabf2c49bc4a423f40a6a6a69d7000f","request_sha256":"5164f707e93fe03db3f764c6cb0b744df44bf7642b33923e5d5679be6b440e3c","response_sha256":"8e82a9f3e2f1193b9257668ba9a40a84d0800db0abb035fbd96a490b1f6c911f","accepted_at_unix":1800000000,"applied_at_unix":1800000123,"authority_key_id":"nakama-legacy-golden-k0"}`
	const recordSHA256 = "2dd99eeff819f1fd5ef5811359dd375880a26be0073fa9a2f5cdb0dd1d7254fb"
	const sealSHA256 = "f88a4125d226b01fc0ac99a0171e286417fa10374a35f9f2adee070b86bc9316"
	const authorityPublicBase64 = "JUO5L/EJVRFHatyDadtt3JM2ZaEZeN2hQE7hBmypVZ0="

	controlSeed := make([]byte, ed25519.SeedSize)
	authoritySeed := make([]byte, ed25519.SeedSize)
	for index := range controlSeed {
		controlSeed[index] = byte(0x20 + index)
		authoritySeed[index] = byte(0x40 + index)
	}
	controlPublic := ed25519.NewKeyFromSeed(controlSeed).Public().(ed25519.PublicKey)
	authorityPublic := ed25519.NewKeyFromSeed(authoritySeed).Public().(ed25519.PublicKey)
	if base64.StdEncoding.EncodeToString(authorityPublic) != authorityPublicBase64 {
		t.Fatal("legacy v2 golden authority public key drifted")
	}
	stored, err := decodeVersionedStoredResearchControl(recordJSON,
		"90000000-0000-4000-8000-000000000002", map[string]ed25519.PublicKey{
			"hepta-control-golden-v2": controlPublic,
		})
	if err != nil || stored.legacyV2 == nil || stored.rawValue != recordJSON {
		t.Fatalf("frozen legacy v2 record did not decode exactly: %#v %v", stored, err)
	}
	reencoded, err := json.Marshal(stored.legacyV2)
	recordDigest := sha256.Sum256(reencoded)
	if err != nil || string(reencoded) != recordJSON || hex.EncodeToString(recordDigest[:]) != recordSHA256 {
		t.Fatalf("frozen legacy v2 outer record encoding drifted: %v", err)
	}
	response, err := stored.response()
	if err != nil || response != responseJSON {
		t.Fatalf("frozen legacy v2 response bytes drifted: %v", err)
	}
	seal, err := stored.responseSealBytes()
	sealDigest := sha256.Sum256(seal)
	if err != nil || string(seal) != sealJSON || hex.EncodeToString(sealDigest[:]) != sealSHA256 {
		t.Fatalf("frozen legacy v2 response seal drifted: %v", err)
	}
	if err := verifyStoredResearchControlResponseAuthority(stored, map[string]ed25519.PublicKey{
		"nakama-legacy-golden-k0": authorityPublic,
	}); err != nil {
		t.Fatalf("frozen legacy v2 response signature did not verify: %v", err)
	}
}

func TestResearchControlStorageSurvivesRestartAndReturnsExactAppliedResult(t *testing.T) {
	const acceptedAt = int64(1_700_000_000)
	request, publicKey := signedResumeRequestV2(t, acceptedAt)
	trusted := map[string]ed25519.PublicKey{"hepta-control-test-v2": publicKey}
	canonical, _ := json.Marshal(request)
	record := newStoredResearchControlCommand(request.Control, canonical, acceptedAt,
		"nakama-control-storage-authority")
	store := &controlTestStorage{}
	version, err := createStoredResearchControl(context.Background(), store, record, trusted)
	if err != nil {
		t.Fatal(err)
	}

	// Loading has no dependency on current wall-clock time: accepted_at proves
	// the command was verified during its original short validity window.
	restarted, err := loadStoredResearchControl(context.Background(), store, record.CommandID, trusted)
	if err != nil || restarted.version != version || restarted.record.Status != researchControlStatusPending {
		t.Fatalf("pending command did not survive restart: %#v %v", restarted, err)
	}
	_, authorityPrivate := researchTestKey("research-control-storage-authority")
	pending := restarted.record
	if err := restarted.record.applyResult(researchRuntimeResponse{
		Schema: "trnm.nakama.research-session.match-runtime.v1", LogicalSessionID: record.SessionID,
		ExternalMatchID: "runtime-1", RuntimeGeneration: 2, Status: researchcore.StatusPaused,
		SessionVersion: 9, RosterVersion: 1, RosterRoot: researchcontract.NewDigest([]byte("roster")),
	}, time.Unix(acceptedAt+500, 0), "nakama-control-storage-authority", authorityPrivate); err != nil {
		t.Fatal(err)
	}
	if _, err := updateStoredResearchControl(context.Background(), store, pending, restarted.record,
		restarted.version, trusted); err != nil {
		t.Fatal(err)
	}
	applied, err := loadStoredResearchControl(context.Background(), store, record.CommandID, trusted)
	if err != nil {
		t.Fatal(err)
	}
	first, err := applied.record.response()
	if err != nil {
		t.Fatal(err)
	}
	second, err := applied.record.response()
	if err != nil || first != second || !strings.Contains(first, `"command_id":"`+record.CommandID+`"`) {
		t.Fatal("applied command did not replay exact response bytes")
	}

	changed := request
	changed.AuthorizationSetID = "20000000-0000-4000-8000-000000000002"
	changedCanonical, _ := json.Marshal(changed)
	if exactResearchControlRequest(applied.record, changedCanonical) {
		t.Fatal("command_id reuse with a different body was accepted")
	}
}

func TestResearchControlCommandEpochRejectsCrossKeyResealAndReservationRewrite(t *testing.T) {
	const acceptedAt = int64(1_700_000_000)
	request, issuerPublic := signedResumeRequestV2(t, acceptedAt)
	trusted := map[string]ed25519.PublicKey{"hepta-control-test-v2": issuerPublic}
	canonical, _ := json.Marshal(request)
	const k0ID = "nakama-control-epoch-k0"
	const k1ID = "nakama-control-epoch-k1"
	_, k0Private := researchTestKey("research-control-epoch-k0")
	_, k1Private := researchTestKey("research-control-epoch-k1")
	pending := newStoredResearchControlCommand(request.Control, canonical, acceptedAt, k0ID)
	store := &controlTestStorage{}
	version, err := createStoredResearchControl(context.Background(), store, pending, trusted)
	if err != nil {
		t.Fatal(err)
	}
	result := researchRuntimeResponse{
		Schema: "trnm.nakama.research-session.match-runtime.v1", LogicalSessionID: pending.SessionID,
		ExternalMatchID: "runtime-epoch", RuntimeGeneration: 1, Status: researchcore.StatusPaused,
		SessionVersion: 1, RosterVersion: 1, RosterRoot: researchcontract.NewDigest([]byte("epoch-roster")),
	}
	applied := pending
	if err := applied.applyResult(result, time.Unix(acceptedAt+1, 0), k0ID, k0Private); err != nil {
		t.Fatal(err)
	}
	stableApplied := applied
	if err := applied.applyResult(result, time.Unix(acceptedAt+2, 0), k0ID, k0Private); err == nil ||
		!reflect.DeepEqual(applied, stableApplied) {
		t.Fatal("an applied command was rewritten or mutated by a second application")
	}

	// An attacker can recompute a K1 signature over a syntactically complete
	// response, but the actual key still cannot differ from the signer reserved
	// when the command was accepted.
	crossKey := stableApplied
	crossKey.ResponseAuthorityKeyID = k1ID
	crossSeal, err := json.Marshal(researchControlResponseSealV2{
		Schema: researchControlResponseSealSchema, CommandID: crossKey.CommandID,
		Operation: crossKey.Operation, TargetRPC: crossKey.TargetRPC, SessionID: crossKey.SessionID,
		SessionRosterVersion: crossKey.SessionRosterVersion, AuthorizationSetID: crossKey.AuthorizationSetID,
		PayloadHash: crossKey.PayloadHash, RequestSHA256: crossKey.RequestSHA256,
		ResponseSHA256: crossKey.ResponseSHA256, AcceptedAtUnix: crossKey.AcceptedAtUnix,
		AppliedAtUnix: *crossKey.AppliedAtUnix, ExpectedAuthorityKeyID: k0ID, AuthorityKeyID: k1ID,
	})
	if err != nil {
		t.Fatal(err)
	}
	crossKey.ResponseSignatureBase64 = base64.StdEncoding.EncodeToString(ed25519.Sign(k1Private, crossSeal))
	if err := validateStoredResearchControlCommand(crossKey, trusted); err == nil ||
		!strings.Contains(err.Error(), "seal identity") {
		t.Fatalf("cross-key response reseal was accepted: %v", err)
	}

	// Mutating both expected and actual ids can form a self-consistent record,
	// but it cannot cross the pending->applied transition for the stored K0
	// reservation. The rejected OCC update must leave exact value/version bytes.
	rekeyedPending := pending
	rekeyedPending.ExpectedResponseAuthorityKeyID = k1ID
	rekeyedApplied := rekeyedPending
	if err := rekeyedApplied.applyResult(result, time.Unix(acceptedAt+1, 0), k1ID, k1Private); err != nil {
		t.Fatal(err)
	}
	objectKey := controlStorageKey(researchControlStorageCollection, pending.CommandID)
	valueBefore := store.objects[objectKey].Value
	versionBefore := store.objects[objectKey].Version
	if _, err := updateStoredResearchControl(context.Background(), store, pending, rekeyedApplied,
		version, trusted); err == nil || !strings.Contains(err.Error(), "immutable pending reservation") {
		t.Fatalf("authority epoch rewrite crossed the persistence transition: %v", err)
	}
	if store.objects[objectKey].Value != valueBefore || store.objects[objectKey].Version != versionBefore {
		t.Fatal("rejected authority epoch rewrite changed persisted command bytes or version")
	}
}

func TestPendingStoredResearchControlV2BlocksLoadAndActivation(t *testing.T) {
	const acceptedAt = int64(1_700_000_000)
	request, issuerPublic := signedResumeRequestV2(t, acceptedAt)
	trusted := map[string]ed25519.PublicKey{"hepta-control-test-v2": issuerPublic}
	canonical, _ := json.Marshal(request)
	requestDigest := sha256.Sum256(canonical)
	claim := request.Control.Claim
	legacy := legacyStoredResearchControlCommandV2{
		Schema: researchControlStorageSchemaV2, CommandID: claim.CommandID, Operation: claim.Operation,
		TargetRPC: claim.TargetRPC, SessionID: claim.SessionID, SessionRosterVersion: claim.SessionRosterVersion,
		AuthorizationSetID: claim.AuthorizationSetID, PayloadHash: claim.PayloadHash,
		RequestBodyBase64: base64.StdEncoding.EncodeToString(canonical),
		RequestSHA256: hex.EncodeToString(requestDigest[:]), AcceptedAtUnix: acceptedAt,
		Status: researchControlStatusPending,
	}
	value, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	store := &controlTestStorage{objects: map[string]*api.StorageObject{
		controlStorageKey(researchControlStorageCollection, legacy.CommandID): {
			Collection: researchControlStorageCollection, Key: legacy.CommandID,
			Value: string(value), Version: "legacy-v2",
		},
	}}
	if _, err := loadStoredResearchControl(context.Background(), store, legacy.CommandID, trusted); !errors.Is(err,
		errLegacyResearchControlPending) {
		t.Fatalf("pending legacy v2 command was not an explicit load blocker: %v", err)
	}
	if err := assessStoredResearchControlActivation(nakamaSystemStorageOwnerID, legacy.CommandID, string(value),
		trusted, map[string]ed25519.PublicKey{}, "nakama-control-active"); !errors.Is(err,
		errLegacyResearchControlPending) {
		t.Fatalf("pending legacy v2 command was not an activation blocker: %v", err)
	}
	unknown := strings.Replace(string(value), researchControlStorageSchemaV2,
		"trnm.nakama.stored-research-control-command.v99", 1)
	if err := assessStoredResearchControlActivation(nakamaSystemStorageOwnerID, legacy.CommandID, unknown,
		trusted, map[string]ed25519.PublicKey{}, "nakama-control-active"); err == nil ||
		!strings.Contains(err.Error(), "unsupported") {
		t.Fatalf("unknown legacy storage schema did not fail closed: %v", err)
	}
	v3Pending := newStoredResearchControlCommand(request.Control, canonical, acceptedAt,
		"nakama-control-retired")
	v3Value, err := json.Marshal(v3Pending)
	if err != nil {
		t.Fatal(err)
	}
	if err := assessStoredResearchControlActivation(nakamaSystemStorageOwnerID, v3Pending.CommandID,
		string(v3Value), trusted, map[string]ed25519.PublicKey{}, "nakama-control-active"); err == nil ||
		!strings.Contains(err.Error(), "inactive response authority") {
		t.Fatalf("pending v3 reservation to an inactive private signer did not block activation: %v", err)
	}
}

func TestResearchControlActivationBlockerFencesRPCsAndMatchInitBeforeStorage(t *testing.T) {
	fixture := newControlRestartFixture(t)
	fixture.module.activationError = errLegacyResearchControlPending
	versionBefore := fixture.store.version
	objectsBefore := len(fixture.store.objects)

	if _, err := fixture.module.rpcResearchResumeV2(context.Background(), &fakeLogger{}, nil,
		fixture.nakama, `{}`); err == nil {
		t.Fatal("research-control activation blocker did not fence the v2 mutation RPC")
	}
	if _, err := fixture.module.rpcCreateMatch(context.Background(), &fakeLogger{}, nil,
		fixture.nakama, `{}`); err == nil {
		t.Fatal("research-control activation blocker did not fence the legacy match mutation RPC")
	}
	if state, tick, label := (&authoritativeMatch{module: fixture.module}).MatchInit(context.Background(),
		&fakeLogger{}, nil, fixture.nakama, map[string]interface{}{}); state != nil || tick != 0 || label != "" {
		t.Fatal("research-control activation blocker did not fence authoritative MatchInit")
	}
	if state, tick, label := (&researchMatch{module: fixture.module}).MatchInit(context.Background(),
		&fakeLogger{}, nil, fixture.nakama, map[string]interface{}{}); state != nil || tick != 0 || label != "" {
		t.Fatal("research-control activation blocker did not fence research MatchInit")
	}
	ready, err := fixture.module.rpcReady(context.Background(), &fakeLogger{}, nil, fixture.nakama, "")
	if err != nil || !strings.Contains(ready, `"ready":false`) ||
		!strings.Contains(ready, `"storage":"error"`) {
		t.Fatalf("activation-blocked readiness did not fail closed: response=%q err=%v", ready, err)
	}
	if fixture.store.version != versionBefore || len(fixture.store.objects) != objectsBefore {
		t.Fatal("activation-blocked RPC, MatchInit, or readiness mutated storage")
	}
}

func TestStoredResearchVerificationErrorExposesRetiredAuthority(t *testing.T) {
	err := storedResearchVerificationError(
		fmt.Errorf("restore completion: %w", researchcore.ErrAuthorityVerificationKeyUnavailable),
		"generic research snapshot failure",
	)
	if !strings.Contains(err.Error(), "authority key is missing from the public verification registry") ||
		strings.Contains(err.Error(), "generic research snapshot failure") {
		t.Fatalf("retired research authority was not exposed as an actionable fail-closed error: %v", err)
	}
}

func TestResearchControlAppliedRuntimeRejectsIntegersOutsideJSONSafeRange(t *testing.T) {
	const acceptedAt = int64(1_700_000_000)
	request, _ := signedResumeRequestV2(t, acceptedAt)
	canonical, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	_, authorityPrivate := researchTestKey("research-control-safe-integer-authority")
	valid := researchRuntimeResponse{
		Schema: "trnm.nakama.research-session.match-runtime.v1", LogicalSessionID: request.LogicalSessionID,
		ExternalMatchID: "runtime-safe-integers", RuntimeGeneration: 1, Status: researchcore.StatusPaused,
		SessionVersion: 1, RosterVersion: 1, RosterRoot: researchcontract.NewDigest([]byte("safe-roster")),
	}
	tests := map[string]func(*researchRuntimeResponse){
		"runtime_generation": func(result *researchRuntimeResponse) {
			result.RuntimeGeneration = researchcontract.MaximumJSONSafeInteger + 1
		},
		"session_version": func(result *researchRuntimeResponse) {
			result.SessionVersion = researchcontract.MaximumJSONSafeInteger + 1
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			record := newStoredResearchControlCommand(request.Control, canonical, acceptedAt,
				"nakama-safe-integer-authority")
			result := valid
			mutate(&result)
			if err := record.applyResult(result, time.Unix(acceptedAt+1, 0),
				"nakama-safe-integer-authority", authorityPrivate); err == nil {
				t.Fatal("applied response with a non-JSON-safe integer was accepted")
			}
		})
	}

	record := newStoredResearchControlCommand(request.Control, canonical, acceptedAt,
		"nakama-safe-integer-authority")
	if err := record.applyResult(valid, time.Unix(int64(researchcontract.MaximumJSONSafeInteger)+1, 0),
		"nakama-safe-integer-authority", authorityPrivate); err == nil {
		t.Fatal("applied response with a non-JSON-safe timestamp was accepted")
	}
	valid.RuntimeGeneration = researchcontract.MaximumJSONSafeInteger
	valid.SessionVersion = researchcontract.MaximumJSONSafeInteger
	record = newStoredResearchControlCommand(request.Control, canonical, acceptedAt,
		"nakama-safe-integer-authority")
	if err := record.applyResult(valid, time.Unix(int64(researchcontract.MaximumJSONSafeInteger), 0),
		"nakama-safe-integer-authority", authorityPrivate); err != nil {
		t.Fatalf("JSON-safe applied response boundary was rejected: %v", err)
	}
}

func TestResearchControlBindingRejectsBodyUnknownFieldAndAcceptsJSONKeyReordering(t *testing.T) {
	const now = int64(1_700_000_000)
	request, publicKey := signedResumeRequestV2(t, now)
	trusted := map[string]ed25519.PublicKey{"hepta-control-test-v2": publicKey}
	business, _ := canonicalResearchResumeBusinessV2(request)
	if err := validateResearchControlBinding(request.Control, business, researchcontract.ResearchControlOperationResume,
		request.LogicalSessionID, 1, request.AuthorizationSetID, trusted, &[]int64{now}[0]); err != nil {
		t.Fatal(err)
	}
	changed := request
	changed.AuthorizationSetID = "20000000-0000-4000-8000-000000000002"
	changedBusiness, _ := canonicalResearchResumeBusinessV2(changed)
	if err := validateResearchControlBinding(request.Control, changedBusiness, researchcontract.ResearchControlOperationResume,
		request.LogicalSessionID, 1, changed.AuthorizationSetID, trusted, &[]int64{now}[0]); err == nil {
		t.Fatal("body mutation under the signed payload hash was accepted")
	}

	canonical, _ := json.Marshal(request)
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(canonical, &fields); err != nil {
		t.Fatal(err)
	}
	reordered := `{"control":` + string(fields["control"]) + `,"authorization_set_id":` + string(fields["authorization_set_id"]) +
		`,"logical_session_id":` + string(fields["logical_session_id"]) + `,"schema":` + string(fields["schema"]) + `}`
	var decoded researchResumeRequestV2
	if err := decodeJSONStrict(reordered, &decoded); err != nil {
		t.Fatal("key reordering changed the typed request")
	}
	reorderedBusiness, _ := canonicalResearchResumeBusinessV2(decoded)
	if !reflect.DeepEqual(business, reorderedBusiness) {
		t.Fatal("key reordering changed canonical business frame bytes")
	}
	unknown := strings.TrimSuffix(reordered, "}") + `,"operator_token":"forbidden"}`
	if decodeJSONStrict(unknown, &decoded) == nil {
		t.Fatal("unknown/secret-bearing v2 request member was accepted")
	}
}

func TestResearchControlUnicodeBusinessFrameIsStable(t *testing.T) {
	facts := researchcontract.TerminalFacts{
		ResultCode: "可复现✅", PaperBundleHash: researchcontract.NewDigest([]byte("bundle")),
		PaperReleaseCandidateHash: researchcontract.NewDigest([]byte("release")),
		ContributionLedgerHash:    researchcontract.NewDigest([]byte("ledger")),
	}
	left, err := researchcontract.ResearchControlCompleteBusinessBytesV2(
		researchcontract.ResearchControlCompleteRequestSchemaV2, "paper-raid-unicode", "20000000-0000-4000-8000-000000000001", facts)
	if err != nil {
		t.Fatal(err)
	}
	right, err := canonicalResearchCompleteBusinessV2(researchCompleteRequestV2{
		Schema: researchcontract.ResearchControlCompleteRequestSchemaV2, LogicalSessionID: "paper-raid-unicode",
		AuthorizationSetID: "20000000-0000-4000-8000-000000000001", Facts: facts,
	})
	if err != nil || !reflect.DeepEqual(left, right) || base64.StdEncoding.EncodeToString(left) == "" {
		t.Fatal("Unicode terminal facts do not have stable UTF-8 frame bytes")
	}
}

func TestStoredResearchControlRejectsCorruptBusinessBeforeHashValidation(t *testing.T) {
	const now = int64(1_700_000_000)
	request, publicKey := signedResumeRequestV2(t, now)
	trusted := map[string]ed25519.PublicKey{"hepta-control-test-v2": publicKey}
	canonical, _ := json.Marshal(request)
	record := newStoredResearchControlCommand(request.Control, canonical, now,
		"nakama-control-storage-authority")
	request.Schema = "trnm.nakama.research-session.corrupt.v2"
	corrupt, _ := json.Marshal(request)
	checksum := sha256.Sum256(corrupt)
	record.RequestBodyBase64 = base64.StdEncoding.EncodeToString(corrupt)
	record.RequestSHA256 = hex.EncodeToString(checksum[:])
	err := validateStoredResearchControlCommand(record, trusted)
	if err == nil || !strings.Contains(err.Error(), "stored resume business frame") {
		t.Fatalf("corrupt stored business frame did not fail at canonical encoding: %v", err)
	}
}
