package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldcommand"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	worldCommandStorageCollection = "trnm_world_command_v1"
	worldCommandStorageSchema     = "trnm.game.world-command-storage.v1"
	maxWorldCommandSnapshotBytes  = 16 * 1024 * 1024
)

var errAtomicWorldCommitAmbiguous = errors.New("atomic World/core commit acknowledgement is ambiguous; runtime restart is required")

type storedWorldCommand struct {
	Schema         string `json:"schema"`
	LogicalMatchID string `json:"logical_match_id"`
	SnapshotBase64 string `json:"snapshot_base64"`
	SnapshotSHA256 string `json:"snapshot_sha256"`
}

type nakamaWorldCommandBackend struct {
	nk             storageGateway
	logicalMatchID string
}

func (b *nakamaWorldCommandBackend) Load(ctx context.Context, key string) ([]byte, string, error) {
	if b == nil || b.nk == nil || key != b.logicalMatchID || contract.ValidateLogicalMatchID(key) != nil {
		return nil, "", worldcommand.ErrInvalidState
	}
	objects, err := b.nk.StorageRead(ctx, []*runtime.StorageRead{{
		Collection: worldCommandStorageCollection,
		Key:        key,
		UserID:     "",
	}})
	if err != nil {
		return nil, "", fmt.Errorf("read World command storage: %w", err)
	}
	if len(objects) == 0 {
		return nil, "", nil
	}
	if len(objects) != 1 || objects[0].Version == "" {
		return nil, "", errors.New("World command storage returned an invalid object set")
	}
	payload, err := decodeStoredWorldCommand(objects[0].Value, key)
	if err != nil {
		return nil, "", err
	}
	return payload, objects[0].Version, nil
}

func (b *nakamaWorldCommandBackend) CompareAndSwap(ctx context.Context, key, expectedVersion string, payload []byte) (string, error) {
	if b == nil || b.nk == nil || key != b.logicalMatchID {
		return "", worldcommand.ErrInvalidState
	}
	value, err := encodeStoredWorldCommand(key, payload)
	if err != nil {
		return "", err
	}
	version := expectedVersion
	if version == "" {
		version = "*"
	}
	acks, err := b.nk.StorageWrite(ctx, []*runtime.StorageWrite{{
		Collection:      worldCommandStorageCollection,
		Key:             key,
		UserID:          "",
		Value:           value,
		Version:         version,
		PermissionRead:  0,
		PermissionWrite: 0,
	}})
	if err != nil {
		if errors.Is(err, runtime.ErrStorageRejectedVersion) {
			return "", worldcommand.ErrVersionConflict
		}
		return "", fmt.Errorf("write World command storage: %w", err)
	}
	if len(acks) != 1 || acks[0].Version == "" || acks[0].Collection != worldCommandStorageCollection || acks[0].Key != key {
		return "", errors.New("World command storage write returned an invalid acknowledgement")
	}
	return acks[0].Version, nil
}

func encodeStoredWorldCommand(logicalMatchID string, payload []byte) (string, error) {
	if contract.ValidateLogicalMatchID(logicalMatchID) != nil || len(payload) == 0 || len(payload) > maxWorldCommandSnapshotBytes {
		return "", errors.New("World command snapshot identity or size is invalid")
	}
	sum := sha256.Sum256(payload)
	record := storedWorldCommand{
		Schema:         worldCommandStorageSchema,
		LogicalMatchID: logicalMatchID,
		SnapshotBase64: base64.StdEncoding.EncodeToString(payload),
		SnapshotSHA256: hex.EncodeToString(sum[:]),
	}
	encoded, err := json.Marshal(record)
	if err != nil {
		return "", fmt.Errorf("encode World command storage: %w", err)
	}
	return string(encoded), nil
}

func decodeStoredWorldCommand(raw, logicalMatchID string) ([]byte, error) {
	var record storedWorldCommand
	if err := decodeJSONStrict(raw, &record); err != nil {
		return nil, fmt.Errorf("decode World command storage: %w", err)
	}
	if record.Schema != worldCommandStorageSchema || record.LogicalMatchID != logicalMatchID {
		return nil, errors.New("World command storage identity is inconsistent")
	}
	payload, err := base64.StdEncoding.Strict().DecodeString(record.SnapshotBase64)
	if err != nil || base64.StdEncoding.EncodeToString(payload) != record.SnapshotBase64 || len(payload) == 0 || len(payload) > maxWorldCommandSnapshotBytes {
		return nil, errors.New("World command snapshot is not bounded canonical base64")
	}
	sum := sha256.Sum256(payload)
	if record.SnapshotSHA256 != hex.EncodeToString(sum[:]) {
		return nil, errors.New("World command snapshot checksum mismatch")
	}
	return payload, nil
}

func persistWorldAndCoreAtomic(
	ctx context.Context,
	nk storageGateway,
	state *authoritativeMatchState,
	expectedWorldVersion string,
	worldPayload []byte,
	rollback func([]byte) error,
	beforeCore []byte,
) (string, error) {
	if state == nil || expectedWorldVersion == "" || state.storageVersion == "" || rollback == nil {
		return "", errors.New("atomic World/core persistence is not initialized")
	}
	rollbackBeforeWrite := func(cause error) (string, error) {
		if rollbackErr := rollback(beforeCore); rollbackErr != nil {
			return "", fmt.Errorf("%w; core rollback failed: %v", cause, rollbackErr)
		}
		return "", cause
	}

	afterCore, err := state.engine.Snapshot()
	if err != nil {
		return rollbackBeforeWrite(fmt.Errorf("encode mutated core snapshot: %w", err))
	}
	updatedMatch := state.record
	updatedMatch.setSnapshot(afterCore)
	matchValue, err := json.Marshal(updatedMatch)
	if err != nil {
		return rollbackBeforeWrite(fmt.Errorf("encode stored match: %w", err))
	}
	worldValue, err := encodeStoredWorldCommand(state.instanceLogicalMatchID, worldPayload)
	if err != nil {
		return rollbackBeforeWrite(err)
	}

	// From this call onward the outcome must be treated as potentially
	// committed. Never restore an older in-memory snapshot and continue the
	// generation after an error or malformed acknowledgement; terminate and
	// reload both objects from storage instead.
	acks, err := nk.StorageWrite(ctx, []*runtime.StorageWrite{
		{
			Collection:      matchStorageCollection,
			Key:             state.instanceLogicalMatchID,
			UserID:          "",
			Value:           string(matchValue),
			Version:         state.storageVersion,
			PermissionRead:  0,
			PermissionWrite: 0,
		},
		{
			Collection:      worldCommandStorageCollection,
			Key:             state.instanceLogicalMatchID,
			UserID:          "",
			Value:           worldValue,
			Version:         expectedWorldVersion,
			PermissionRead:  0,
			PermissionWrite: 0,
		},
	})
	if err != nil {
		return "", fmt.Errorf("%w: %v", errAtomicWorldCommitAmbiguous, err)
	}
	if len(acks) != 2 {
		return "", fmt.Errorf("%w: expected two acknowledgements, received %d", errAtomicWorldCommitAmbiguous, len(acks))
	}
	versions := map[string]string{}
	for _, ack := range acks {
		if ack == nil || ack.Version == "" {
			continue
		}
		versions[ack.Collection+"/"+ack.Key] = ack.Version
	}
	matchVersion := versions[matchStorageCollection+"/"+state.instanceLogicalMatchID]
	worldVersion := versions[worldCommandStorageCollection+"/"+state.instanceLogicalMatchID]
	if matchVersion == "" || worldVersion == "" {
		return "", fmt.Errorf("%w: acknowledgement identities are incomplete", errAtomicWorldCommitAmbiguous)
	}
	state.record = updatedMatch
	state.storageVersion = matchVersion
	return worldVersion, nil
}
