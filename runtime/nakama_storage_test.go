package main

import (
	"context"
	"encoding/base64"
	"fmt"
	"testing"

	"github.com/heroiclabs/nakama-common/api"
	"github.com/heroiclabs/nakama-common/runtime"
)

type fakeStorage struct {
	object    *api.StorageObject
	writes    int
	writeErr  error
	deleteErr error
}

func (f *fakeStorage) StorageRead(_ context.Context, _ []*runtime.StorageRead) ([]*api.StorageObject, error) {
	if f.object == nil {
		return []*api.StorageObject{}, nil
	}
	return []*api.StorageObject{{
		Collection: f.object.Collection,
		Key:        f.object.Key,
		UserId:     f.object.UserId,
		Value:      f.object.Value,
		Version:    f.object.Version,
	}}, nil
}

func (f *fakeStorage) StorageWrite(_ context.Context, writes []*runtime.StorageWrite) ([]*api.StorageObjectAck, error) {
	if f.writeErr != nil {
		return nil, f.writeErr
	}
	if len(writes) != 1 {
		return nil, fmt.Errorf("expected one write")
	}
	write := writes[0]
	if write.Version == "*" && f.object != nil {
		return nil, runtime.ErrStorageRejectedVersion
	}
	if write.Version != "*" && (f.object == nil || write.Version != f.object.Version) {
		return nil, runtime.ErrStorageRejectedVersion
	}
	f.writes++
	version := fmt.Sprintf("v%d", f.writes)
	f.object = &api.StorageObject{
		Collection: write.Collection,
		Key:        write.Key,
		UserId:     write.UserID,
		Value:      write.Value,
		Version:    version,
	}
	return []*api.StorageObjectAck{{
		Collection: write.Collection,
		Key:        write.Key,
		Version:    version,
	}}, nil
}

func (f *fakeStorage) StorageDelete(_ context.Context, deletes []*runtime.StorageDelete) error {
	if f.deleteErr != nil {
		return f.deleteErr
	}
	if len(deletes) != 1 || f.object == nil || deletes[0].Version != f.object.Version {
		return runtime.ErrStorageRejectedVersion
	}
	f.object = nil
	return nil
}

func TestStoredMatchRoundTripAndOCC(t *testing.T) {
	ctx := context.Background()
	store := &fakeStorage{}
	record, err := newStoredMatch("match-001", []byte(`{"schema":"core-snapshot-test"}`))
	if err != nil {
		t.Fatal(err)
	}
	version1, err := createStoredMatch(ctx, store, record)
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := loadStoredMatch(ctx, store, "match-001")
	if err != nil {
		t.Fatal(err)
	}
	if loaded.version != version1 {
		t.Fatalf("loaded version %q, want %q", loaded.version, version1)
	}
	snapshot, err := loaded.record.snapshot()
	if err != nil || string(snapshot) != `{"schema":"core-snapshot-test"}` {
		t.Fatalf("unexpected snapshot %q: %v", snapshot, err)
	}

	loaded.record.setSnapshot([]byte(`{"schema":"core-snapshot-test-2"}`))
	if _, err := updateStoredMatch(ctx, store, loaded.record, version1); err != nil {
		t.Fatal(err)
	}
	if _, err := updateStoredMatch(ctx, store, loaded.record, version1); err == nil {
		t.Fatal("stale storage version was accepted")
	}
}

func TestStoredMatchDetectsSnapshotCorruption(t *testing.T) {
	record, err := newStoredMatch("match-002", []byte("original"))
	if err != nil {
		t.Fatal(err)
	}
	record.CoreSnapshot = "Y29ycnVwdA=="
	if _, err := record.snapshot(); err == nil {
		t.Fatal("corrupt snapshot checksum was accepted")
	}
}

func TestStoredMatchRejectsUnsafeLogicalID(t *testing.T) {
	if _, err := newStoredMatch("../outside", []byte("x")); err == nil {
		t.Fatal("unsafe logical match id was accepted")
	}
}

func TestReadinessProbeRequiresWriteAndDelete(t *testing.T) {
	store := &fakeStorage{}
	if err := probeWritableStorage(context.Background(), store); err != nil {
		t.Fatalf("writable storage was rejected: %v", err)
	}
	if store.object != nil {
		t.Fatal("readiness probe left a storage object behind")
	}
	store.writeErr = runtime.ErrStorageRejectedPermission
	if err := probeWritableStorage(context.Background(), store); err == nil {
		t.Fatal("read-only storage was reported ready")
	}
}

func TestDecodeJSONStrictCanonicalWire(t *testing.T) {
	type envelope struct {
		Schema  string `json:"schema"`
		Payload []byte `json:"payload"`
	}
	validPayload := base64.StdEncoding.EncodeToString([]byte{1})
	var decoded envelope
	if err := decodeJSONStrict(`{"schema":"test.v1","payload":"`+validPayload+`"}`, &decoded); err != nil {
		t.Fatalf("canonical JSON was rejected: %v", err)
	}
	if len(decoded.Payload) != 1 || decoded.Payload[0] != 1 {
		t.Fatalf("canonical payload decoded incorrectly: %x", decoded.Payload)
	}

	tests := map[string]string{
		"duplicate top-level member": `{"schema":"test.v1","schema":"test.v2","payload":"AQ=="}`,
		"duplicate nested member":    `{"schema":"test.v1","payload":"AQ==","nested":{"key":1,"key":2}}`,
		"line-wrapped base64":        "{\"schema\":\"test.v1\",\"payload\":\"A\\r\\nQ==\"}",
		"unpadded base64":            `{"schema":"test.v1","payload":"AQ"}`,
	}
	for name, raw := range tests {
		t.Run(name, func(t *testing.T) {
			var value any
			if err := decodeJSONStrict(raw, &value); err == nil {
				t.Fatal("non-canonical JSON was accepted")
			}
		})
	}

	invalidUTF8 := string([]byte{'{', '"', 'x', '"', ':', '"', 0xff, '"', '}'})
	var value any
	if err := decodeJSONStrict(invalidUTF8, &value); err == nil {
		t.Fatal("invalid UTF-8 was accepted")
	}
}
