package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
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

func TestResearchControlStorageSurvivesRestartAndReturnsExactAppliedResult(t *testing.T) {
	const acceptedAt = int64(1_700_000_000)
	request, publicKey := signedResumeRequestV2(t, acceptedAt)
	trusted := map[string]ed25519.PublicKey{"hepta-control-test-v2": publicKey}
	canonical, _ := json.Marshal(request)
	record := newStoredResearchControlCommand(request.Control, canonical, acceptedAt)
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
	if err := restarted.record.applyResult(researchRuntimeResponse{
		Schema: "trnm.nakama.research-session.match-runtime.v1", LogicalSessionID: record.SessionID,
		ExternalMatchID: "runtime-1", RuntimeGeneration: 2, Status: researchcore.StatusPaused,
		SessionVersion: 9, RosterVersion: 1, RosterRoot: researchcontract.NewDigest([]byte("roster")),
	}, time.Unix(acceptedAt+500, 0), "nakama-control-storage-authority", authorityPrivate); err != nil {
		t.Fatal(err)
	}
	if _, err := updateStoredResearchControl(context.Background(), store, restarted.record, restarted.version, trusted); err != nil {
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
			record := newStoredResearchControlCommand(request.Control, canonical, acceptedAt)
			result := valid
			mutate(&result)
			if err := record.applyResult(result, time.Unix(acceptedAt+1, 0),
				"nakama-safe-integer-authority", authorityPrivate); err == nil {
				t.Fatal("applied response with a non-JSON-safe integer was accepted")
			}
		})
	}

	record := newStoredResearchControlCommand(request.Control, canonical, acceptedAt)
	if err := record.applyResult(valid, time.Unix(int64(researchcontract.MaximumJSONSafeInteger)+1, 0),
		"nakama-safe-integer-authority", authorityPrivate); err == nil {
		t.Fatal("applied response with a non-JSON-safe timestamp was accepted")
	}
	valid.RuntimeGeneration = researchcontract.MaximumJSONSafeInteger
	valid.SessionVersion = researchcontract.MaximumJSONSafeInteger
	record = newStoredResearchControlCommand(request.Control, canonical, acceptedAt)
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
	record := newStoredResearchControlCommand(request.Control, canonical, now)
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
