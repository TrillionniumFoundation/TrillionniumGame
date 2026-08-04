package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
	"github.com/heroiclabs/nakama-common/api"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	matchStorageCollection = "trnm_authoritative_match_v1"
	matchStorageSchema     = "trnm.nakama.stored_match.v1"
)

type storageGateway interface {
	StorageRead(context.Context, []*runtime.StorageRead) ([]*api.StorageObject, error)
	StorageWrite(context.Context, []*runtime.StorageWrite) ([]*api.StorageObjectAck, error)
}

// storedMatch is a server-owned, globally addressed record. CoreSnapshot is
// base64 rather than json.RawMessage so a corrupt/non-JSON snapshot can never
// make the Nakama storage value itself invalid JSON.
type storedMatch struct {
	Schema            string `json:"schema"`
	LogicalMatchID    string `json:"logical_match_id"`
	CoreSnapshot      string `json:"core_snapshot_base64"`
	SnapshotSHA256    string `json:"snapshot_sha256"`
	ExternalMatchID   string `json:"external_match_id,omitempty"`
	RuntimeGeneration uint64 `json:"runtime_generation"`
}

type versionedStoredMatch struct {
	record  storedMatch
	version string
}

func newStoredMatch(logicalMatchID string, snapshot []byte) (storedMatch, error) {
	if err := contract.ValidateLogicalMatchID(logicalMatchID); err != nil {
		return storedMatch{}, err
	}
	record := storedMatch{
		Schema:            matchStorageSchema,
		LogicalMatchID:    logicalMatchID,
		RuntimeGeneration: 0,
	}
	record.setSnapshot(snapshot)
	return record, nil
}

func (r *storedMatch) setSnapshot(snapshot []byte) {
	sum := sha256.Sum256(snapshot)
	r.CoreSnapshot = base64.StdEncoding.EncodeToString(snapshot)
	r.SnapshotSHA256 = hex.EncodeToString(sum[:])
}

func (r storedMatch) snapshot() ([]byte, error) {
	if r.Schema != matchStorageSchema {
		return nil, fmt.Errorf("unsupported stored match schema %q", r.Schema)
	}
	if contract.ValidateLogicalMatchID(r.LogicalMatchID) != nil {
		return nil, errors.New("stored logical match id is invalid")
	}
	snapshot, err := base64.StdEncoding.DecodeString(r.CoreSnapshot)
	if err != nil {
		return nil, errors.New("stored core snapshot is not canonical base64")
	}
	sum := sha256.Sum256(snapshot)
	if r.SnapshotSHA256 != hex.EncodeToString(sum[:]) {
		return nil, errors.New("stored core snapshot checksum mismatch")
	}
	return snapshot, nil
}

func loadStoredMatch(ctx context.Context, nk storageGateway, logicalMatchID string) (versionedStoredMatch, error) {
	if contract.ValidateLogicalMatchID(logicalMatchID) != nil {
		return versionedStoredMatch{}, errors.New("invalid logical match id")
	}
	objects, err := nk.StorageRead(ctx, []*runtime.StorageRead{{
		Collection: matchStorageCollection,
		Key:        logicalMatchID,
		UserID:     "",
	}})
	if err != nil {
		return versionedStoredMatch{}, fmt.Errorf("read match storage: %w", err)
	}
	if len(objects) != 1 {
		return versionedStoredMatch{}, errors.New("logical match not found")
	}
	var record storedMatch
	if err := decodeJSONStrict(objects[0].Value, &record); err != nil {
		return versionedStoredMatch{}, fmt.Errorf("decode stored match: %w", err)
	}
	if record.LogicalMatchID != logicalMatchID {
		return versionedStoredMatch{}, errors.New("stored logical match id does not match its key")
	}
	if _, err := record.snapshot(); err != nil {
		return versionedStoredMatch{}, err
	}
	return versionedStoredMatch{record: record, version: objects[0].Version}, nil
}

func createStoredMatch(ctx context.Context, nk storageGateway, record storedMatch) (string, error) {
	return writeStoredMatch(ctx, nk, record, "*")
}

func updateStoredMatch(ctx context.Context, nk storageGateway, record storedMatch, version string) (string, error) {
	if version == "" || version == "*" {
		return "", errors.New("a concrete storage version is required for update")
	}
	return writeStoredMatch(ctx, nk, record, version)
}

func writeStoredMatch(ctx context.Context, nk storageGateway, record storedMatch, version string) (string, error) {
	if _, err := record.snapshot(); err != nil {
		return "", err
	}
	value, err := json.Marshal(record)
	if err != nil {
		return "", fmt.Errorf("encode stored match: %w", err)
	}
	acks, err := nk.StorageWrite(ctx, []*runtime.StorageWrite{{
		Collection:      matchStorageCollection,
		Key:             record.LogicalMatchID,
		UserID:          "",
		Value:           string(value),
		Version:         version,
		PermissionRead:  0,
		PermissionWrite: 0,
	}})
	if err != nil {
		if errors.Is(err, runtime.ErrStorageRejectedVersion) {
			return "", errors.New("match storage version conflict: another active writer exists")
		}
		return "", fmt.Errorf("write match storage: %w", err)
	}
	if len(acks) != 1 || acks[0].Version == "" {
		return "", errors.New("match storage write returned no version")
	}
	return acks[0].Version, nil
}

func decodeJSONStrict(raw string, dst any) error {
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(dst); err != nil {
		return err
	}
	if decoder.More() {
		return errors.New("multiple JSON values are not allowed")
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("multiple JSON values are not allowed")
		}
		return err
	}
	return nil
}
