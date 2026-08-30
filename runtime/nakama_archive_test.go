package main

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"
	"time"

	matchcore "github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/core"
	"github.com/heroiclabs/nakama-common/runtime"
)

func archiveUint64(value uint64) *uint64 { return &value }
func archiveUint32(value uint32) *uint32 { return &value }
func archiveString(value string) *string { return &value }

func encodeArchiveRequest(t *testing.T, request archiveRequest) string {
	t.Helper()
	payload, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return string(payload)
}

func TestArchiveRPCPaginatesDurableActiveSnapshotForParticipantAndOperator(t *testing.T) {
	f := newAdapterFixture(t)
	participantContext := context.WithValue(context.Background(), runtime.RUNTIME_CTX_USER_ID, "user-1")
	firstRaw, err := f.module.rpcArchiveFromStorage(participantContext, f.store, encodeArchiveRequest(t, archiveRequest{
		Schema: "trnm.nakama.get-archive.v1", LogicalMatchID: "adapter-match-1",
		AfterSequence: archiveUint64(0), Limit: archiveUint32(2), AuthorizationID: archiveString("auth-1"),
	}))
	if err != nil {
		t.Fatal(err)
	}
	var first archiveResponse
	if err := json.Unmarshal([]byte(firstRaw), &first); err != nil {
		t.Fatal(err)
	}
	if first.Schema != "trnm.nakama.archive.v1" || first.LogicalMatchID != "adapter-match-1" ||
		first.ExternalMatchID != "external-match-7" || first.RuntimeGeneration != 7 ||
		first.Status != matchcore.StatusActive || first.MatchVersion != 4 || first.EventCount != 3 ||
		first.AfterSequence != 0 || first.NextAfterSequence != 2 || !first.HasMore {
		t.Fatalf("unexpected first archive page: %+v", first)
	}
	if len(first.Events) != 2 || first.Events[0].Sequence != 1 || first.Events[1].Sequence != 2 {
		t.Fatalf("unexpected first archive events: %+v", first.Events)
	}
	if len(first.Roster) != 2 || first.Roster[0].ParticipantSlot != 1 || first.Roster[1].ParticipantSlot != 2 {
		t.Fatalf("unexpected archive roster: %+v", first.Roster)
	}
	if len(first.Participants) != 2 || first.Participants[0].AuthorizationID != "auth-1" ||
		first.Participants[0].LastCommandSequence != 1 || first.Participants[1].AuthorizationID != "auth-2" ||
		first.Participants[1].LastCommandSequence != 0 || !first.Participants[0].Joined || !first.Participants[1].Joined {
		t.Fatalf("unexpected participant cursors: %+v", first.Participants)
	}

	secondRequest := archiveRequest{
		Schema: "trnm.nakama.get-archive.v1", LogicalMatchID: "adapter-match-1",
		AfterSequence: archiveUint64(2), OperatorToken: archiveString(adapterOperatorToken),
	}
	secondRaw, err := f.module.rpcArchiveFromStorage(context.Background(), f.store, encodeArchiveRequest(t, secondRequest))
	if err != nil {
		t.Fatal(err)
	}
	var second archiveResponse
	if err := json.Unmarshal([]byte(secondRaw), &second); err != nil {
		t.Fatal(err)
	}
	if len(second.Events) != 1 || second.Events[0].Sequence != 3 || second.NextAfterSequence != 3 || second.HasMore {
		t.Fatalf("unexpected second archive page: %+v", second)
	}
	repeatedRaw, err := f.module.rpcArchiveFromStorage(context.Background(), f.store, encodeArchiveRequest(t, secondRequest))
	if err != nil || repeatedRaw != secondRaw {
		t.Fatalf("immutable archive retry changed: equal=%v err=%v", repeatedRaw == secondRaw, err)
	}

	emptyRaw, err := f.module.rpcArchiveFromStorage(participantContext, f.store, encodeArchiveRequest(t, archiveRequest{
		Schema: "trnm.nakama.get-archive.v1", LogicalMatchID: "adapter-match-1",
		AfterSequence: archiveUint64(3), AuthorizationID: archiveString("auth-1"),
	}))
	if err != nil {
		t.Fatal(err)
	}
	var empty archiveResponse
	if err := json.Unmarshal([]byte(emptyRaw), &empty); err != nil {
		t.Fatal(err)
	}
	if empty.Events == nil || len(empty.Events) != 0 || empty.NextAfterSequence != 3 || empty.HasMore {
		t.Fatalf("empty terminal page is not a stable JSON array/cursor: %+v", empty)
	}
}

func TestArchiveRPCReadsCompletedSnapshotAndTerminalEvent(t *testing.T) {
	f := newAdapterFixture(t)
	if _, err := f.state.engine.Complete(f.facts, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	loaded, err := loadStoredMatch(context.Background(), f.store, "adapter-match-1")
	if err != nil {
		t.Fatal(err)
	}
	loaded.record.setSnapshot(mustSnapshot(t, f.state.engine))
	if _, err := updateStoredMatch(context.Background(), f.store, loaded.record, loaded.version); err != nil {
		t.Fatal(err)
	}
	raw, err := f.module.rpcArchiveFromStorage(context.Background(), f.store, encodeArchiveRequest(t, archiveRequest{
		Schema: "trnm.nakama.get-archive.v1", LogicalMatchID: "adapter-match-1",
		AfterSequence: archiveUint64(3), Limit: archiveUint32(1), OperatorToken: archiveString(adapterOperatorToken),
	}))
	if err != nil {
		t.Fatal(err)
	}
	var response archiveResponse
	if err := json.Unmarshal([]byte(raw), &response); err != nil {
		t.Fatal(err)
	}
	if response.Status != matchcore.StatusCompleted || response.MatchVersion != 5 || response.EventCount != 4 ||
		len(response.Events) != 1 || response.Events[0].Sequence != 4 ||
		response.Events[0].EventType != "match_completed" || response.HasMore {
		t.Fatalf("completed archive page is inconsistent: %+v", response)
	}
}

func TestArchiveRPCRejectsInvalidCredentialsCursorAndWireShape(t *testing.T) {
	f := newAdapterFixture(t)
	base := archiveRequest{
		Schema: "trnm.nakama.get-archive.v1", LogicalMatchID: "adapter-match-1",
		AfterSequence: archiveUint64(0), AuthorizationID: archiveString("auth-1"),
	}
	invalidWire := map[string]archiveRequest{
		"missing cursor":       {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AuthorizationID: archiveString("auth-1")},
		"missing credential":   {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AfterSequence: archiveUint64(0)},
		"multiple credentials": {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AfterSequence: archiveUint64(0), AuthorizationID: archiveString("auth-1"), OperatorToken: archiveString(adapterOperatorToken)},
		"empty credential":     {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AfterSequence: archiveUint64(0), AuthorizationID: archiveString("")},
		"nul credential":       {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AfterSequence: archiveUint64(0), AuthorizationID: archiveString("auth\x00id")},
		"zero limit":           {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AfterSequence: archiveUint64(0), Limit: archiveUint32(0), AuthorizationID: archiveString("auth-1")},
		"oversized limit":      {Schema: base.Schema, LogicalMatchID: base.LogicalMatchID, AfterSequence: archiveUint64(0), Limit: archiveUint32(129), AuthorizationID: archiveString("auth-1")},
	}
	for name, request := range invalidWire {
		t.Run(name, func(t *testing.T) {
			if archiveRequestWireValid(request) {
				t.Fatal("invalid archive request passed wire validation")
			}
			if _, err := f.module.rpcArchiveFromStorage(context.Background(), f.store, encodeArchiveRequest(t, request)); err == nil {
				t.Fatal("invalid archive request reached storage successfully")
			}
		})
	}
	if _, err := f.module.rpcArchiveFromStorage(context.Background(), f.store,
		`{"schema":"trnm.nakama.get-archive.v1","logical_match_id":"adapter-match-1","after_sequence":0,"authorization_id":"auth-1","unknown":true}`); err == nil {
		t.Fatal("archive request with an unknown field was accepted")
	}
	wrongUser := context.WithValue(context.Background(), runtime.RUNTIME_CTX_USER_ID, "user-2")
	if _, err := f.module.rpcArchiveFromStorage(wrongUser, f.store, encodeArchiveRequest(t, base)); err == nil {
		t.Fatal("participant authorization was accepted for a different authenticated user")
	}
	badOperator := base
	badOperator.AuthorizationID = nil
	badOperator.OperatorToken = archiveString("fedcba9876543210fedcba9876543210")
	if _, err := f.module.rpcArchiveFromStorage(context.Background(), f.store, encodeArchiveRequest(t, badOperator)); err == nil {
		t.Fatal("untrusted operator token read the archive")
	}
	pastEnd := base
	pastEnd.AfterSequence = archiveUint64(4)
	participantContext := context.WithValue(context.Background(), runtime.RUNTIME_CTX_USER_ID, "user-1")
	if _, err := f.module.rpcArchiveFromStorage(participantContext, f.store, encodeArchiveRequest(t, pastEnd)); err == nil {
		t.Fatal("archive cursor beyond the durable event count was accepted")
	}
}

func TestArchiveResponseDoesNotAliasEngineEventPayload(t *testing.T) {
	f := newAdapterFixture(t)
	response, err := archiveResponseFor(f.state.record, f.state.engine, 0, maximumArchivePageSize)
	if err != nil {
		t.Fatal(err)
	}
	original := f.state.engine.Events()
	response.Events[0].Payload[0] ^= 1
	if !reflect.DeepEqual(f.state.engine.Events(), original) {
		t.Fatal("mutating an archive response changed the authoritative engine archive")
	}
}
