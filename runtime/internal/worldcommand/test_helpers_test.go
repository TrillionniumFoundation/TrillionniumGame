package worldcommand

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/worldtransition"
)

type fakeCodec struct{}

type fakeRequest struct {
	Context             worldtransition.AuthorityContext `json:"context"`
	PreviousStateSchema string                           `json:"previous_state_schema"`
	PreviousState       any                              `json:"previous_state"`
	CommandSchema       string                           `json:"command_schema"`
	Command             any                              `json:"command"`
}

func (fakeCodec) DecodeCanonical(raw []byte, maximumBytes int) (any, error) {
	if maximumBytes >= 0 && len(raw) > maximumBytes {
		return nil, fmt.Errorf("too large")
	}
	var value any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&value); err != nil {
		return nil, err
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	if !bytes.Equal(canonical, raw) {
		return nil, fmt.Errorf("not canonical")
	}
	return normalizeNumbers(value), nil
}

func normalizeNumbers(value any) any {
	switch typed := value.(type) {
	case json.Number:
		parsed, _ := typed.Int64()
		return parsed
	case []any:
		for index := range typed {
			typed[index] = normalizeNumbers(typed[index])
		}
		return typed
	case map[string]any:
		for key := range typed {
			typed[key] = normalizeNumbers(typed[key])
		}
		return typed
	default:
		return value
	}
}

func (fakeCodec) EncodeCanonical(value any) ([]byte, error) {
	return json.Marshal(value)
}

func (codec fakeCodec) Prepare(context worldtransition.AuthorityContext, previousStateSchemaID string, previousState any, commandSchemaID string, command any) (PreparedTransition, error) {
	request := fakeRequest{context, previousStateSchemaID, previousState, commandSchemaID, command}
	canonical, err := json.Marshal(request)
	if err != nil {
		return PreparedTransition{}, err
	}
	previousCanonical, err := json.Marshal(previousState)
	if err != nil {
		return PreparedTransition{}, err
	}
	requestHash := hashBytes(canonical)
	return PreparedTransition{
		CanonicalRequest:  canonical,
		RequestHash:       requestHash,
		TransitionID:      "wtx-" + requestHash[:24],
		WorldCommandID:    "wcmd-" + requestHash[24:48],
		PreviousStateHash: hashBytes(previousCanonical),
	}, nil
}

func (codec fakeCodec) Restore(context worldtransition.AuthorityContext, canonicalRequest []byte) (PreparedTransition, error) {
	var request fakeRequest
	if err := json.Unmarshal(canonicalRequest, &request); err != nil {
		return PreparedTransition{}, err
	}
	if request.Context != context {
		return PreparedTransition{}, fmt.Errorf("context drift")
	}
	return codec.Prepare(context, request.PreviousStateSchema, request.PreviousState, request.CommandSchema, request.Command)
}

func (codec fakeCodec) Verify(prepared PreparedTransition, context worldtransition.AuthorityContext, rawResult []byte) (VerifiedTransition, error) {
	restored, err := codec.Restore(context, prepared.CanonicalRequest)
	if err != nil {
		return VerifiedTransition{}, err
	}
	if restored.RequestHash != prepared.RequestHash {
		return VerifiedTransition{}, fmt.Errorf("prepared request drift")
	}
	switch string(rawResult) {
	case "accepted":
		var request fakeRequest
		if err := json.Unmarshal(prepared.CanonicalRequest, &request); err != nil {
			return VerifiedTransition{}, err
		}
		state, ok := normalizeNumbers(request.PreviousState).(map[string]any)
		if !ok {
			return VerifiedTransition{}, fmt.Errorf("state is not an object")
		}
		counter, _ := state["counter"].(int64)
		nextState := map[string]any{"counter": counter + 1}
		canonical, _ := json.Marshal(nextState)
		stateHash := hashBytes(canonical)
		nextTick := context.ExpectedTick + 1
		previous := prepared.PreviousStateHash
		replayHash := hashBytes([]byte("replay:" + prepared.RequestHash))
		outcomeHash := hashBytes([]byte("outcome:" + prepared.RequestHash))
		transitionHash := hashBytes([]byte("transition:" + prepared.RequestHash))
		return VerifiedTransition{
			Disposition:            DispositionAccepted,
			RequestHash:            prepared.RequestHash,
			TransitionID:           prepared.TransitionID,
			NextTick:               &nextTick,
			PreviousStateHash:      &previous,
			NextStateSchemaID:      request.PreviousStateSchema,
			NextStateCanonicalJSON: canonical,
			NextStateHash:          &stateHash,
			ReplayHash:             &replayHash,
			WorldOutcomeHash:       &outcomeHash,
			WorldTransitionHash:    &transitionHash,
			CanonicalResultSHA256:  hashBytes(rawResult),
		}, nil
	case "rejected":
		code := "domain_rejected"
		retryable := false
		return VerifiedTransition{
			Disposition:           DispositionRejected,
			RequestHash:           prepared.RequestHash,
			TransitionID:          prepared.TransitionID,
			ErrorCode:             &code,
			Retryable:             &retryable,
			CanonicalResultSHA256: hashBytes(rawResult),
		}, nil
	case "retryable":
		code := "internal_unavailable"
		retryable := true
		return VerifiedTransition{
			Disposition:           DispositionRejected,
			RequestHash:           prepared.RequestHash,
			TransitionID:          prepared.TransitionID,
			ErrorCode:             &code,
			Retryable:             &retryable,
			CanonicalResultSHA256: hashBytes(rawResult),
		}, nil
	default:
		return VerifiedTransition{}, fmt.Errorf("invalid result")
	}
}

type scriptStep struct {
	result []byte
	err    error
}

type scriptedExecutor struct {
	mu       sync.Mutex
	steps    []scriptStep
	requests [][]byte
}

func (e *scriptedExecutor) Execute(_ context.Context, request []byte) ([]byte, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.requests = append(e.requests, append([]byte(nil), request...))
	if len(e.steps) == 0 {
		return nil, fmt.Errorf("no scripted response")
	}
	step := e.steps[0]
	e.steps = e.steps[1:]
	return append([]byte(nil), step.result...), step.err
}

func (e *scriptedExecutor) count() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.requests)
}

func testState() MatchState {
	raw := []byte(`{"counter":0}`)
	return MatchState{
		MatchID:                 "match-1",
		ParticipantRosterHash:   repeat("a", 64),
		MatchVersion:            3,
		NextGlobalEventSequence: 3,
		StateRevision:           1,
		StateSchemaID:           "trnm.rts.state.v1",
		StateCanonicalJSON:      raw,
		StateHash:               hashBytes(raw),
		Tick:                    10,
		ParticipantSequences:    map[string]uint64{"participant-1": 0, "participant-2": 0},
	}
}

func testRequest(commandID, participant string, participantSequence uint64, command any) PrepareRequest {
	state := testState()
	return PrepareRequest{
		ClientCommandID:       commandID,
		UserID:                "user-1",
		ParticipantID:         participant,
		ParticipantSequence:   participantSequence,
		ExpectedStateRevision: state.StateRevision,
		ExpectedStateHash:     state.StateHash,
		CommandSchemaID:       "trnm.rts.order.v1",
		Command:               command,
		Context: worldtransition.AuthorityContext{
			MatchID:               state.MatchID,
			AuthorizationID:       "authorization-1",
			ParticipantRosterHash: state.ParticipantRosterHash,
			MatchVersion:          int64(state.MatchVersion),
			GlobalEventSequence:   int64(state.NextGlobalEventSequence),
			CommandIdempotencyKey: commandID,
			RulesetRevision:       "rules-v1",
			ContentRevision:       "content-v1",
			ExpectedTick:          state.Tick,
		},
	}
}

func repeat(value string, count int) string {
	out := ""
	for i := 0; i < count; i++ {
		out += value
	}
	return out
}

func newTestStore(t *testing.T, backend *MemoryBackend) *Store {
	t.Helper()
	store, err := NewStore(context.Background(), "match-1", backend, fakeCodec{}, testState())
	if err != nil {
		t.Fatal(err)
	}
	return store
}

func fixedClock() func() time.Time {
	current := time.Unix(1_800_000_000, 0).UTC()
	return func() time.Time {
		current = current.Add(time.Second)
		return current
	}
}

type barrierExecutor struct {
	mu       sync.Mutex
	requests [][]byte
	ready    chan struct{}
	release  chan struct{}
}

func newBarrierExecutor() *barrierExecutor {
	return &barrierExecutor{ready: make(chan struct{}), release: make(chan struct{})}
}

func (e *barrierExecutor) Execute(_ context.Context, request []byte) ([]byte, error) {
	e.mu.Lock()
	e.requests = append(e.requests, append([]byte(nil), request...))
	if len(e.requests) == 2 {
		close(e.ready)
	}
	e.mu.Unlock()
	<-e.release
	return []byte("accepted"), nil
}

func (e *barrierExecutor) requestCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.requests)
}
