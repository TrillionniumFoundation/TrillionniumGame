package core

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"errors"
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/contract"
)

type coreFixture struct {
	engine           *Engine
	now              time.Time
	issuerPublic     ed25519.PublicKey
	issuerPrivate    ed25519.PrivateKey
	authorityPublic  ed25519.PublicKey
	authorityPrivate ed25519.PrivateKey
	agentPublic      [2]ed25519.PublicKey
	agentPrivate     [2]ed25519.PrivateKey
	authorizations   [2]contract.SignedAuthorization
}

func fixtureKey(label string) (ed25519.PublicKey, ed25519.PrivateKey) {
	seed := sha256.Sum256([]byte(label))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	return privateKey.Public().(ed25519.PublicKey), privateKey
}

func newCoreFixture(t *testing.T) *coreFixture {
	t.Helper()
	f := &coreFixture{now: time.Unix(1_800_000_000, 0)}
	f.issuerPublic, f.issuerPrivate = fixtureKey("fixture-issuer")
	f.authorityPublic, f.authorityPrivate = fixtureKey("fixture-authority")
	for index := range 2 {
		f.agentPublic[index], f.agentPrivate[index] = fixtureKey("fixture-agent-" + string(rune('1'+index)))
		claim := contract.AuthorizationClaim{
			Schema: contract.AuthorizationSchema, AuthorizationID: "auth-" + string(rune('1'+index)),
			MatchID: "match-1", ChallengeID: "challenge-1", AgentID: "agent-" + string(rune('1'+index)),
			AgentDID: "did:trnm:agent-" + string(rune('1'+index)), AgentKeyID: "agent-key-" + string(rune('1'+index)),
			AgentPublicKey: f.agentPublic[index], SubjectUserID: "user-" + string(rune('1'+index)),
			ParticipantSlot: uint32(index + 1), Role: []string{"challenger", "defender"}[index],
			RulesetHash: contract.NewDigest([]byte("ruleset-v1")), DatasetHash: contract.NewDigest([]byte("dataset-v1")),
			ChallengeSnapshotHash: contract.NewDigest([]byte("challenge-snapshot-v1")),
			IssuedAtUnix:          f.now.Add(-time.Minute).Unix(), ExpiresAtUnix: f.now.Add(time.Hour).Unix(),
		}
		var err error
		f.authorizations[index], err = contract.SignAuthorization(claim, "issuer-key-1", f.issuerPrivate)
		if err != nil {
			t.Fatal(err)
		}
	}
	var err error
	f.engine, err = NewMatch(NewMatchOptions{Authorizations: f.authorizations,
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
		AuthorityKeyID:    "nakama-authority-1", AuthorityPrivateKey: f.authorityPrivate, Now: f.now})
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *coreFixture) joinBoth(t *testing.T) {
	t.Helper()
	for index := range 2 {
		result, err := f.engine.Join("user-"+string(rune('1'+index)), "auth-"+string(rune('1'+index)), f.now.Add(time.Duration(index+1)*time.Second))
		if err != nil {
			t.Fatal(err)
		}
		if result.Replay || result.Event == nil || result.Event.Sequence != uint64(index+1) {
			t.Fatalf("unexpected join result: %+v", result)
		}
	}
}

func (f *coreFixture) command(t *testing.T, slot int, id string, sequence, expectedVersion uint64, payload string) contract.CommandEnvelope {
	t.Helper()
	index := slot - 1
	command, err := contract.SignCommand(contract.CommandEnvelope{
		Schema: contract.CommandSchema, CommandID: id, AuthorizationID: "auth-" + string(rune('1'+index)),
		MatchID: "match-1", ChallengeID: "challenge-1", AgentID: "agent-" + string(rune('1'+index)),
		ParticipantSlot: uint32(slot), ParticipantSequence: sequence, ExpectedMatchVersion: expectedVersion,
		IssuedAtUnix: f.now.Add(2 * time.Second).Unix(), PayloadType: "trnm.turn.v1", Payload: []byte(payload),
		AgentKeyID: "agent-key-" + string(rune('1'+index)),
	}, f.agentPrivate[index])
	if err != nil {
		t.Fatal(err)
	}
	return command
}

func TestCoreAuthorizationAdmissionAndLifecycle(t *testing.T) {
	f := newCoreFixture(t)
	if got := f.engine.View(); got.Status != StatusCreated || got.Version != 1 {
		t.Fatalf("unexpected initial view: %+v", got)
	}
	if _, err := f.engine.Join("attacker", "auth-1", f.now); !errors.Is(err, ErrAuthorization) {
		t.Fatalf("subject binding was not enforced: %v", err)
	}
	first, err := f.engine.Join("user-1", "auth-1", f.now)
	if err != nil || first.Status != StatusWaiting || first.Version != 2 {
		t.Fatalf("first join failed: %+v %v", first, err)
	}
	replay, err := f.engine.Join("user-1", "auth-1", f.now.Add(2*time.Hour))
	if err != nil || !replay.Replay || replay.Event != nil || replay.Version != 2 {
		t.Fatalf("consumed authorization was not idempotent after expiry: %+v %v", replay, err)
	}
	if _, err := f.engine.Join("user-2", "auth-2", f.now.Add(2*time.Hour)); !errors.Is(err, ErrAuthorization) {
		t.Fatalf("unused expired authorization was accepted: %v", err)
	}
}

func TestCoreRejectsTamperedAuthorization(t *testing.T) {
	f := newCoreFixture(t)
	tampered := f.authorizations
	tampered[0].Claim.SubjectUserID = "attacker"
	if _, err := NewMatch(NewMatchOptions{Authorizations: tampered,
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
		AuthorityKeyID:    "nakama-authority-1", AuthorityPrivateKey: f.authorityPrivate, Now: f.now}); !errors.Is(err, ErrAuthorization) {
		t.Fatalf("tampered authorization was accepted: %v", err)
	}
}

func TestCoreRejectsDuplicateAgentKeysAndMalformedAuthority(t *testing.T) {
	f := newCoreFixture(t)
	for name, mutate := range map[string]func(*contract.AuthorizationClaim){
		"key id": func(claim *contract.AuthorizationClaim) { claim.AgentKeyID = f.authorizations[0].Claim.AgentKeyID },
		"public key": func(claim *contract.AuthorizationClaim) {
			claim.AgentPublicKey = append([]byte(nil), f.authorizations[0].Claim.AgentPublicKey...)
		},
	} {
		t.Run(name, func(t *testing.T) {
			authorizations := f.authorizations
			claim := authorizations[1].Claim
			mutate(&claim)
			var err error
			authorizations[1], err = contract.SignAuthorization(claim, "issuer-key-1", f.issuerPrivate)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := NewMatch(NewMatchOptions{Authorizations: authorizations,
				TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
				AuthorityKeyID:    "nakama-authority-1", AuthorityPrivateKey: f.authorityPrivate, Now: f.now}); !errors.Is(err, ErrAuthorization) {
				t.Fatalf("duplicate agent %s was accepted: %v", name, err)
			}
		})
	}

	malformed := append(ed25519.PrivateKey(nil), f.authorityPrivate...)
	malformed[len(malformed)-1] ^= 1
	if _, err := NewMatch(NewMatchOptions{Authorizations: f.authorizations,
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
		AuthorityKeyID:    "nakama-authority-1", AuthorityPrivateKey: malformed, Now: f.now}); err == nil {
		t.Fatal("authority private key with mismatched public suffix was accepted")
	}
	if _, err := NewMatch(NewMatchOptions{Authorizations: f.authorizations,
		TrustedIssuerKeys: map[string]ed25519.PublicKey{"issuer-key-1": f.issuerPublic},
		AuthorityKeyID:    "", AuthorityPrivateKey: f.authorityPrivate, Now: f.now}); err == nil {
		t.Fatal("empty authority key ID was accepted")
	}
}

func TestCoreCommandSequenceVersionAndIdempotency(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	if view := f.engine.View(); view.Status != StatusReady || view.Version != 3 || view.EventCount != 2 {
		t.Fatalf("unexpected ready state: %+v", view)
	}
	command := f.command(t, 1, "cmd-1", 1, 3, "move-a")
	result, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second))
	if err != nil || result.Replay || result.Status != StatusActive || result.Event.Sequence != 3 || result.Version != 4 {
		t.Fatalf("command application failed: %+v %v", result, err)
	}
	replay, err := f.engine.ApplyCommand("user-1", command, f.now.Add(4*time.Second))
	if err != nil || !replay.Replay || !reflect.DeepEqual(replay.Event, result.Event) {
		t.Fatalf("exact command replay changed result: %+v %v", replay, err)
	}
	conflict := f.command(t, 1, "cmd-1", 1, 3, "different")
	if _, err := f.engine.ApplyCommand("user-1", conflict, f.now.Add(4*time.Second)); !errors.Is(err, ErrConflict) {
		t.Fatalf("command ID conflict was not rejected: %v", err)
	}
	outOfOrder := f.command(t, 1, "cmd-3", 3, 4, "move-c")
	if _, err := f.engine.ApplyCommand("user-1", outOfOrder, f.now.Add(4*time.Second)); !errors.Is(err, ErrSequence) {
		t.Fatalf("out-of-order command was not rejected: %v", err)
	}
	wrongVersion := f.command(t, 1, "cmd-2", 2, 99, "move-b")
	if _, err := f.engine.ApplyCommand("user-1", wrongVersion, f.now.Add(4*time.Second)); !errors.Is(err, ErrVersion) {
		t.Fatalf("wrong match version was not rejected: %v", err)
	}
	tampered := command
	tampered.CommandID = "cmd-tampered"
	if _, err := f.engine.ApplyCommand("user-1", tampered, f.now.Add(4*time.Second)); !errors.Is(err, ErrAuthorization) {
		t.Fatalf("signature tampering was not rejected: %v", err)
	}
}

func TestCoreCompletionRootsSignatureAndNoSelfReference(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	command := f.command(t, 1, "cmd-1", 1, 3, "move-a")
	if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second)); err != nil {
		t.Fatal(err)
	}
	facts := contract.TerminalFacts{ResultCode: "decisive", WinnerSlot: 1, OutcomeHash: contract.NewDigest([]byte("outcome"))}
	completion, err := f.engine.Complete(facts, f.now.Add(4*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if err := contract.VerifyCompletion(completion, f.authorityPublic); err != nil {
		t.Fatalf("completion signature failed: %v", err)
	}
	if !reflect.DeepEqual(completion.TerminalFacts, facts) {
		t.Fatal("completion does not carry the signed terminal facts")
	}
	events := f.engine.Events()
	if completion.EventCount != uint64(len(events)) || events[len(events)-1].Sequence != uint64(len(events)) {
		t.Fatal("completion event count or sequence is inconsistent")
	}
	root, _ := contract.EventRoot(events)
	archiveHash, _ := contract.ArchiveHash(events)
	if completion.EventRoot != root || completion.ArchiveHash != archiveHash {
		t.Fatal("completion roots do not bind the event archive")
	}
	terminal := events[len(events)-1]
	if bytes.Contains(terminal.Payload, []byte(completion.EventRoot)) || bytes.Contains(terminal.Payload, []byte(completion.ArchiveHash)) {
		t.Fatal("terminal event self-references a derived root")
	}
	retry, err := f.engine.Complete(facts, f.now.Add(time.Hour))
	if err != nil || !reflect.DeepEqual(completion, retry) {
		t.Fatal("completion retry was not byte-identical")
	}
	changed := facts
	changed.WinnerSlot = 2
	if _, err := f.engine.Complete(changed, f.now.Add(time.Hour)); !errors.Is(err, ErrConflict) {
		t.Fatalf("conflicting completion retry was accepted: %v", err)
	}
	if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(time.Hour)); !errors.Is(err, ErrState) {
		t.Fatalf("completed match accepted a command: %v", err)
	}
}

func TestCoreTransitionsAreAtomicAndTimeMonotonic(t *testing.T) {
	t.Run("join rollback", func(t *testing.T) {
		f := newCoreFixture(t)
		if _, err := f.engine.Join("user-1", "auth-1", f.now.Add(2*time.Second)); err != nil {
			t.Fatal(err)
		}
		beforeView, beforeEvents := f.engine.View(), f.engine.Events()
		if _, err := f.engine.Join("user-2", "auth-2", f.now.Add(time.Second)); err == nil {
			t.Fatal("join with regressing event time was accepted")
		}
		if !reflect.DeepEqual(beforeView, f.engine.View()) || !reflect.DeepEqual(beforeEvents, f.engine.Events()) {
			t.Fatal("failed join changed engine state")
		}
	})

	t.Run("command rollback", func(t *testing.T) {
		f := newCoreFixture(t)
		f.joinBoth(t)
		command, err := contract.SignCommand(contract.CommandEnvelope{
			Schema: contract.CommandSchema, CommandID: "cmd-backwards", AuthorizationID: "auth-1",
			MatchID: "match-1", ChallengeID: "challenge-1", AgentID: "agent-1", ParticipantSlot: 1,
			ParticipantSequence: 1, ExpectedMatchVersion: 3, IssuedAtUnix: f.now.Add(time.Second).Unix(),
			PayloadType: "trnm.turn.v1", Payload: []byte("move"), AgentKeyID: "agent-key-1",
		}, f.agentPrivate[0])
		if err != nil {
			t.Fatal(err)
		}
		beforeView, beforeEvents := f.engine.View(), f.engine.Events()
		if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(time.Second)); err == nil {
			t.Fatal("command with regressing event time was accepted")
		}
		if !reflect.DeepEqual(beforeView, f.engine.View()) || !reflect.DeepEqual(beforeEvents, f.engine.Events()) || len(f.engine.commands) != 0 {
			t.Fatal("failed command changed engine state")
		}
	})

	t.Run("completion rollback", func(t *testing.T) {
		f := newCoreFixture(t)
		f.joinBoth(t)
		command := f.command(t, 1, "cmd-1", 1, 3, "move")
		if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second)); err != nil {
			t.Fatal(err)
		}
		beforeView, beforeEvents := f.engine.View(), f.engine.Events()
		facts := contract.TerminalFacts{ResultCode: "decisive", WinnerSlot: 1, OutcomeHash: contract.NewDigest([]byte("outcome"))}
		if _, err := f.engine.Complete(facts, f.now.Add(2*time.Second)); err == nil {
			t.Fatal("completion with regressing event time was accepted")
		}
		if !reflect.DeepEqual(beforeView, f.engine.View()) || !reflect.DeepEqual(beforeEvents, f.engine.Events()) {
			t.Fatal("failed completion changed engine state")
		}
		if _, ok := f.engine.Completion(); ok || f.engine.terminalFacts != nil {
			t.Fatal("failed completion retained terminal evidence")
		}
	})

	t.Run("append event rollback", func(t *testing.T) {
		f := newCoreFixture(t)
		beforeView, beforeEvents := f.engine.View(), f.engine.Events()
		if _, err := f.engine.appendEvent("oversized", "cause", f.now, 0, "payload.v1", make([]byte, contract.MaxPayloadBytes+1)); err == nil {
			t.Fatal("oversized event payload was accepted")
		}
		if !reflect.DeepEqual(beforeView, f.engine.View()) || !reflect.DeepEqual(beforeEvents, f.engine.Events()) {
			t.Fatal("failed append changed engine state")
		}
	})
}

func TestCoreCommandCountBoundaryAlwaysLeavesCompletableSnapshot(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	for sequence := 1; sequence <= MaxCommandsPerMatch; sequence++ {
		command := f.command(t, 1, fmt.Sprintf("capacity-command-%04d", sequence), uint64(sequence), f.engine.View().Version, "x")
		if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second)); err != nil {
			t.Fatalf("accepted command %d failed before the declared limit: %v", sequence, err)
		}
	}
	before := f.engine.View()
	overflow := f.command(t, 1, "capacity-command-overflow", uint64(MaxCommandsPerMatch+1), before.Version, "x")
	if _, err := f.engine.ApplyCommand("user-1", overflow, f.now.Add(3*time.Second)); !errors.Is(err, ErrCapacity) {
		t.Fatalf("command count overflow was not rejected as capacity: %v", err)
	}
	if !reflect.DeepEqual(before, f.engine.View()) {
		t.Fatal("command count rejection mutated the engine")
	}
	assertCapacityBoundaryCompletes(t, f)
}

func TestCoreCumulativePayloadBoundaryAlwaysLeavesCompletableSnapshot(t *testing.T) {
	f := newCoreFixture(t)
	f.joinBoth(t)
	payload := string(make([]byte, contract.MaxPayloadBytes))
	commands := MaxCumulativeCommandPayloadBytes / contract.MaxPayloadBytes
	if commands*contract.MaxPayloadBytes != MaxCumulativeCommandPayloadBytes {
		t.Fatal("test requires an exact payload multiple")
	}
	for sequence := 1; sequence <= commands; sequence++ {
		command := f.command(t, 1, fmt.Sprintf("payload-command-%04d", sequence), uint64(sequence), f.engine.View().Version, payload)
		if _, err := f.engine.ApplyCommand("user-1", command, f.now.Add(3*time.Second)); err != nil {
			t.Fatalf("accepted payload command %d failed before the declared limit: %v", sequence, err)
		}
	}
	before := f.engine.View()
	overflow := f.command(t, 1, "payload-command-overflow", uint64(commands+1), before.Version, "x")
	if _, err := f.engine.ApplyCommand("user-1", overflow, f.now.Add(3*time.Second)); !errors.Is(err, ErrCapacity) {
		t.Fatalf("cumulative payload overflow was not rejected as capacity: %v", err)
	}
	if !reflect.DeepEqual(before, f.engine.View()) {
		t.Fatal("payload capacity rejection mutated the engine")
	}
	assertCapacityBoundaryCompletes(t, f)
}

func assertCapacityBoundaryCompletes(t *testing.T, f *coreFixture) {
	t.Helper()
	facts := contract.TerminalFacts{ResultCode: "capacity-boundary", WinnerSlot: 1, OutcomeHash: contract.NewDigest([]byte("capacity-outcome"))}
	if _, err := f.engine.Complete(facts, f.now.Add(4*time.Second)); err != nil {
		t.Fatalf("an accepted capacity-boundary match became impossible to complete: %v", err)
	}
	if _, err := f.engine.Snapshot(); err != nil {
		t.Fatalf("completed capacity-boundary match became impossible to snapshot: %v", err)
	}
}
