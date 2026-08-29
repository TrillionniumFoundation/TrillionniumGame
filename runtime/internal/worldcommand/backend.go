package worldcommand

import (
	"context"
	"fmt"
	"strconv"
	"sync"
)

type MemoryBackend struct {
	mu       sync.Mutex
	payloads map[string][]byte
	versions map[string]uint64
	failNext error
}

func NewMemoryBackend() *MemoryBackend {
	return &MemoryBackend{
		payloads: make(map[string][]byte),
		versions: make(map[string]uint64),
	}
}

func (b *MemoryBackend) Load(_ context.Context, key string) ([]byte, string, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	payload, ok := b.payloads[key]
	if !ok {
		return nil, "", nil
	}
	return append([]byte(nil), payload...), strconv.FormatUint(b.versions[key], 10), nil
}

func (b *MemoryBackend) CompareAndSwap(_ context.Context, key, expectedVersion string, payload []byte) (string, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.failNext != nil {
		err := b.failNext
		b.failNext = nil
		return "", err
	}
	current, exists := b.versions[key]
	if !exists {
		if expectedVersion != "" {
			return "", ErrVersionConflict
		}
	} else if expectedVersion != strconv.FormatUint(current, 10) {
		return "", ErrVersionConflict
	}
	next := current + 1
	b.versions[key] = next
	b.payloads[key] = append([]byte(nil), payload...)
	return strconv.FormatUint(next, 10), nil
}

func (b *MemoryBackend) FailNext(err error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if err == nil {
		err = fmt.Errorf("injected persistence failure")
	}
	b.failNext = err
}

func (b *MemoryBackend) Corrupt(key string, mutate func([]byte) []byte) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	payload, ok := b.payloads[key]
	if !ok {
		return fmt.Errorf("snapshot %q does not exist", key)
	}
	b.payloads[key] = append([]byte(nil), mutate(append([]byte(nil), payload...))...)
	return nil
}
