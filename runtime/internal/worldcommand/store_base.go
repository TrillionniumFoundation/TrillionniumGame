package worldcommand

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
)

type snapshotDocument struct {
	Schema       string                   `json:"schema"`
	State        MatchState               `json:"state"`
	Reservations map[string]Reservation   `json:"reservations"`
	Receipts     map[string]Receipt       `json:"receipts"`
	Retired      map[string][]Reservation `json:"retired"`
}

type Store struct {
	mu           sync.Mutex
	key          string
	backend      SnapshotBackend
	codec        Codec
	version      string
	state        MatchState
	reservations map[string]Reservation
	receipts     map[string]Receipt
	retired      map[string][]Reservation
}

func NewStore(ctx context.Context, key string, backend SnapshotBackend, codec Codec, initial MatchState) (*Store, error) {
	if strings.TrimSpace(key) == "" || backend == nil || codec == nil {
		return nil, fmt.Errorf("%w: key, backend, and codec are required", ErrInvalidState)
	}
	store := &Store{
		key:          key,
		backend:      backend,
		codec:        codec,
		state:        cloneMatchState(initial),
		reservations: make(map[string]Reservation),
		receipts:     make(map[string]Receipt),
		retired:      make(map[string][]Reservation),
	}
	if err := store.validateState(store.state); err != nil {
		return nil, err
	}
	payload, err := store.marshalLocked()
	if err != nil {
		return nil, err
	}
	version, err := backend.CompareAndSwap(ctx, key, "", payload)
	if err != nil {
		return nil, fmt.Errorf("initialize World command store: %w", err)
	}
	store.version = version
	return store, nil
}

func OpenStore(ctx context.Context, key string, backend SnapshotBackend, codec Codec) (*Store, error) {
	if strings.TrimSpace(key) == "" || backend == nil || codec == nil {
		return nil, fmt.Errorf("%w: key, backend, and codec are required", ErrInvalidState)
	}
	payload, version, err := backend.Load(ctx, key)
	if err != nil {
		return nil, fmt.Errorf("load World command store: %w", err)
	}
	if len(payload) == 0 || version == "" {
		return nil, fmt.Errorf("%w: persisted store does not exist", ErrInvalidState)
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var document snapshotDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("%w: decode persisted store: %v", ErrInvalidState, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, fmt.Errorf("%w: persisted store has trailing data", ErrInvalidState)
	}
	store := &Store{
		key:          key,
		backend:      backend,
		codec:        codec,
		version:      version,
		state:        cloneMatchState(document.State),
		reservations: cloneReservationMap(document.Reservations),
		receipts:     cloneReceiptMap(document.Receipts),
		retired:      cloneRetiredMap(document.Retired),
	}
	if err := store.validateLocked(document.Schema); err != nil {
		return nil, err
	}
	return store, nil
}
