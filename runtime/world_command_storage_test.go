package main

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/worldcommand"
	"github.com/heroiclabs/nakama-common/api"
	"github.com/heroiclabs/nakama-common/runtime"
	"google.golang.org/protobuf/proto"
)

type multiStorage struct {
	objects       map[string]*api.StorageObject
	writeCalls    int
	writeErr      error
	malformedAcks bool
}

func newMultiStorage() *multiStorage {
	return &multiStorage{objects: make(map[string]*api.StorageObject)}
}

func storageObjectKey(collection, key string) string {
	return collection + "/" + key
}

func cloneStorageObject(value *api.StorageObject) *api.StorageObject {
	if value == nil {
		return nil
	}
	return proto.Clone(value).(*api.StorageObject)
}

func (s *multiStorage) StorageRead(_ context.Context, reads []*runtime.StorageRead) ([]*api.StorageObject, error) {
	result := make([]*api.StorageObject, 0, len(reads))
	for _, read := range reads {
		if read == nil {
			continue
		}
		if object := s.objects[storageObjectKey(read.Collection, read.Key)]; object != nil {
			result = append(result, cloneStorageObject(object))
		}
	}
	return result, nil
}

func (s *multiStorage) StorageWrite(_ context.Context, writes []*runtime.StorageWrite) ([]*api.StorageObjectAck, error) {
	s.writeCalls++
	if s.writeErr != nil {
		return nil, s.writeErr
	}
	for _, write := range writes {
		if write == nil {
			return nil, errors.New("nil write")
		}
		current := s.objects[storageObjectKey(write.Collection, write.Key)]
		if write.Version == "*" {
			if current != nil {
				return nil, runtime.ErrStorageRejectedVersion
			}
		} else if current == nil || current.Version != write.Version {
			return nil, runtime.ErrStorageRejectedVersion
		}
	}

	versions := make([]string, len(writes))
	for index := range writes {
		versions[index] = fmt.Sprintf("mv%d-%d", s.writeCalls, index+1)
	}
	for index, write := range writes {
		s.objects[storageObjectKey(write.Collection, write.Key)] = &api.StorageObject{
			Collection: write.Collection,
			Key:        write.Key,
			UserId:     write.UserID,
			Value:      write.Value,
			Version:    versions[index],
		}
	}
	if s.malformedAcks {
		return []*api.StorageObjectAck{{
			Collection: writes[0].Collection,
			Key:        writes[0].Key,
			Version:    versions[0],
		}}, nil
	}
	acks := make([]*api.StorageObjectAck, len(writes))
	for index, write := range writes {
		acks[index] = &api.StorageObjectAck{
			Collection: write.Collection,
			Key:        write.Key,
			Version:    versions[index],
		}
	}
	return acks, nil
}

func TestNakamaWorldCommandBackendRoundTripAndOCC(t *testing.T) {
	ctx := context.Background()
	storage := newMultiStorage()
	backend := &nakamaWorldCommandBackend{nk: storage, logicalMatchID: "adapter-match-1"}
	payload := []byte(`{"schema":"world-command-snapshot-test"}`)
	version, err := backend.CompareAndSwap(ctx, "adapter-match-1", "", payload)
	if err != nil {
		t.Fatal(err)
	}
	loaded, loadedVersion, err := backend.Load(ctx, "adapter-match-1")
	if err != nil {
		t.Fatal(err)
	}
	if loadedVersion != version || string(loaded) != string(payload) {
		t.Fatalf("World command storage round trip mismatch: version=%q payload=%q", loadedVersion, loaded)
	}
	if _, err := backend.CompareAndSwap(ctx, "adapter-match-1", "stale-version", payload); !errors.Is(err, worldcommand.ErrVersionConflict) {
		t.Fatalf("stale World storage version was not mapped to the coordinator contract: %v", err)
	}
}

func TestPersistWorldAndCoreAtomicWritesBothObjects(t *testing.T) {
	fixture := newAdapterFixture(t)
	storage := newMultiStorage()
	storage.objects[storageObjectKey(matchStorageCollection, fixture.state.instanceLogicalMatchID)] = cloneStorageObject(fixture.store.object)
	backend := &nakamaWorldCommandBackend{nk: storage, logicalMatchID: fixture.state.instanceLogicalMatchID}
	worldVersion, err := backend.CompareAndSwap(context.Background(), fixture.state.instanceLogicalMatchID, "", []byte(`{"schema":"world-before"}`))
	if err != nil {
		t.Fatal(err)
	}
	beforeCore, err := fixture.state.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	rollbackCalls := 0
	nextWorldVersion, err := persistWorldAndCoreAtomic(
		context.Background(),
		storage,
		fixture.state,
		worldVersion,
		[]byte(`{"schema":"world-after"}`),
		func([]byte) error {
			rollbackCalls++
			return nil
		},
		beforeCore,
	)
	if err != nil {
		t.Fatal(err)
	}
	if rollbackCalls != 0 || nextWorldVersion == "" || storage.writeCalls != 2 {
		t.Fatalf("unexpected atomic write outcome: rollback=%d version=%q calls=%d", rollbackCalls, nextWorldVersion, storage.writeCalls)
	}
	matchObject := storage.objects[storageObjectKey(matchStorageCollection, fixture.state.instanceLogicalMatchID)]
	worldObject := storage.objects[storageObjectKey(worldCommandStorageCollection, fixture.state.instanceLogicalMatchID)]
	if matchObject == nil || worldObject == nil || fixture.state.storageVersion != matchObject.Version || nextWorldVersion != worldObject.Version {
		t.Fatal("atomic write did not return and install both concrete object versions")
	}
	worldPayload, err := decodeStoredWorldCommand(worldObject.Value, fixture.state.instanceLogicalMatchID)
	if err != nil || string(worldPayload) != `{"schema":"world-after"}` {
		t.Fatalf("persisted World snapshot mismatch: %q %v", worldPayload, err)
	}
}

func TestPersistWorldAndCorePreWriteFailureRollsBack(t *testing.T) {
	fixture := newAdapterFixture(t)
	storage := newMultiStorage()
	storage.objects[storageObjectKey(matchStorageCollection, fixture.state.instanceLogicalMatchID)] = cloneStorageObject(fixture.store.object)
	beforeCore, err := fixture.state.engine.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	rollbackCalls := 0
	_, err = persistWorldAndCoreAtomic(
		context.Background(), storage, fixture.state, "world-v1", nil,
		func(snapshot []byte) error {
			rollbackCalls++
			if string(snapshot) != string(beforeCore) {
				t.Fatal("rollback did not receive the exact pre-commit core snapshot")
			}
			return nil
		},
		beforeCore,
	)
	if err == nil || rollbackCalls != 1 || storage.writeCalls != 0 {
		t.Fatalf("pre-write failure did not roll back safely: err=%v rollback=%d calls=%d", err, rollbackCalls, storage.writeCalls)
	}
}

func TestPersistWorldAndCorePostWriteErrorIsAmbiguousAndDoesNotRollback(t *testing.T) {
	for name, configure := range map[string]func(*multiStorage){
		"storage error": func(storage *multiStorage) {
			storage.writeErr = errors.New("injected transport loss after storage call")
		},
		"malformed acknowledgements": func(storage *multiStorage) {
			storage.malformedAcks = true
		},
	} {
		t.Run(name, func(t *testing.T) {
			fixture := newAdapterFixture(t)
			storage := newMultiStorage()
			storage.objects[storageObjectKey(matchStorageCollection, fixture.state.instanceLogicalMatchID)] = cloneStorageObject(fixture.store.object)
			backend := &nakamaWorldCommandBackend{nk: storage, logicalMatchID: fixture.state.instanceLogicalMatchID}
			worldVersion, err := backend.CompareAndSwap(context.Background(), fixture.state.instanceLogicalMatchID, "", []byte(`{"schema":"world-before"}`))
			if err != nil {
				t.Fatal(err)
			}
			configure(storage)
			beforeCore, err := fixture.state.engine.Snapshot()
			if err != nil {
				t.Fatal(err)
			}
			rollbackCalls := 0
			_, err = persistWorldAndCoreAtomic(
				context.Background(), storage, fixture.state, worldVersion, []byte(`{"schema":"world-after"}`),
				func([]byte) error {
					rollbackCalls++
					return nil
				}, beforeCore,
			)
			if !errors.Is(err, errAtomicWorldCommitAmbiguous) {
				t.Fatalf("post-write uncertainty was not classified ambiguous: %v", err)
			}
			if rollbackCalls != 0 {
				t.Fatal("post-write ambiguous outcome incorrectly restored an older in-memory core snapshot")
			}
		})
	}
}
