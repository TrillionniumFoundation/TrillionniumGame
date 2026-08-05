package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	researchStorageCollection = "trnm_research_session_v1"
	researchStorageSchema     = "trnm.nakama.stored-research-session.v1"
)

type storedResearchSession struct {
	Schema                    string                            `json:"schema"`
	LogicalSessionID          string                            `json:"logical_session_id"`
	CoreSnapshot              string                            `json:"core_snapshot_base64"`
	SnapshotSHA256            string                            `json:"snapshot_sha256"`
	ExternalMatchID           string                            `json:"external_match_id,omitempty"`
	RuntimeGeneration         uint64                            `json:"runtime_generation"`
	ControlAuthorizationSetID string                            `json:"control_authorization_set_id,omitempty"`
	ConsumptionOutboxes       []storedResearchConsumptionOutbox `json:"consumption_outboxes"`
	CompletionOutbox          *storedResearchCompletionOutbox   `json:"completion_outbox"`
}

type versionedStoredResearch struct {
	record  storedResearchSession
	version string
}

func newStoredResearch(sessionID string, snapshot []byte, authorizations []researchcontract.SignedAuthorization, consumedAtUnix int64) (storedResearchSession, error) {
	if err := researchcontract.ValidateSessionID(sessionID); err != nil {
		return storedResearchSession{}, err
	}
	outbox, err := newStoredResearchConsumptionOutbox(authorizations, consumedAtUnix)
	if err != nil {
		return storedResearchSession{}, err
	}
	record := storedResearchSession{Schema: researchStorageSchema, LogicalSessionID: sessionID, ConsumptionOutboxes: []storedResearchConsumptionOutbox{outbox}}
	record.setSnapshot(snapshot)
	return record, nil
}

func (record *storedResearchSession) setSnapshot(snapshot []byte) {
	sum := sha256.Sum256(snapshot)
	record.CoreSnapshot = base64.StdEncoding.EncodeToString(snapshot)
	record.SnapshotSHA256 = hex.EncodeToString(sum[:])
}

func (record storedResearchSession) snapshot() ([]byte, error) {
	if record.Schema != researchStorageSchema || researchcontract.ValidateSessionID(record.LogicalSessionID) != nil {
		return nil, errors.New("stored research session schema or identity is invalid")
	}
	if record.RuntimeGeneration > researchcontract.MaximumJSONSafeInteger {
		return nil, errors.New("stored research runtime generation is outside the JSON-safe range")
	}
	if record.ControlAuthorizationSetID != "" {
		if err := researchcontract.ValidateAuthorizationSetID(record.ControlAuthorizationSetID); err != nil {
			return nil, err
		}
	}
	snapshot, err := base64.StdEncoding.Strict().DecodeString(record.CoreSnapshot)
	if err != nil || base64.StdEncoding.EncodeToString(snapshot) != record.CoreSnapshot {
		return nil, errors.New("stored research snapshot is not canonical base64")
	}
	sum := sha256.Sum256(snapshot)
	if record.SnapshotSHA256 != hex.EncodeToString(sum[:]) {
		return nil, errors.New("stored research snapshot checksum mismatch")
	}
	if err := validateStoredResearchConsumptionOutboxes(record.LogicalSessionID, record.ConsumptionOutboxes); err != nil {
		return nil, err
	}
	if err := validateStoredResearchCompletionOutbox(record.LogicalSessionID, record.CompletionOutbox); err != nil {
		return nil, err
	}
	return snapshot, nil
}

func loadStoredResearch(ctx context.Context, nk storageGateway, sessionID string) (versionedStoredResearch, error) {
	if researchcontract.ValidateSessionID(sessionID) != nil {
		return versionedStoredResearch{}, errors.New("invalid research session id")
	}
	objects, err := nk.StorageRead(ctx, []*runtime.StorageRead{{Collection: researchStorageCollection, Key: sessionID, UserID: ""}})
	if err != nil {
		return versionedStoredResearch{}, fmt.Errorf("read research storage: %w", err)
	}
	if len(objects) != 1 {
		return versionedStoredResearch{}, errors.New("research session not found")
	}
	var record storedResearchSession
	if err := decodeJSONStrict(objects[0].Value, &record); err != nil {
		return versionedStoredResearch{}, err
	}
	if record.LogicalSessionID != sessionID {
		return versionedStoredResearch{}, errors.New("stored research session identity differs from key")
	}
	if _, err := record.snapshot(); err != nil {
		return versionedStoredResearch{}, err
	}
	return versionedStoredResearch{record: record, version: objects[0].Version}, nil
}

func createStoredResearch(ctx context.Context, nk storageGateway, record storedResearchSession) (string, error) {
	return writeStoredResearch(ctx, nk, record, "*")
}
func updateStoredResearch(ctx context.Context, nk storageGateway, record storedResearchSession, version string) (string, error) {
	if version == "" || version == "*" {
		return "", errors.New("concrete research storage version required")
	}
	return writeStoredResearch(ctx, nk, record, version)
}
func writeStoredResearch(ctx context.Context, nk storageGateway, record storedResearchSession, version string) (string, error) {
	write, err := storedResearchWrite(record, version)
	if err != nil {
		return "", err
	}
	acks, err := nk.StorageWrite(ctx, []*runtime.StorageWrite{write})
	if err != nil {
		if errors.Is(err, runtime.ErrStorageRejectedVersion) {
			return "", errors.New("research storage version conflict")
		}
		return "", err
	}
	if len(acks) != 1 || acks[0].Version == "" {
		return "", errors.New("research storage write returned no version")
	}
	return acks[0].Version, nil
}

func storedResearchWrite(record storedResearchSession, version string) (*runtime.StorageWrite, error) {
	if _, err := record.snapshot(); err != nil {
		return nil, err
	}
	value, err := json.Marshal(record)
	if err != nil {
		return nil, err
	}
	return &runtime.StorageWrite{Collection: researchStorageCollection, Key: record.LogicalSessionID, UserID: "", Value: string(value), Version: version, PermissionRead: 0, PermissionWrite: 0}, nil
}
