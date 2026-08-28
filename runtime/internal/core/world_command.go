package core

import (
	"crypto/ed25519"
	"fmt"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/contract"
)

// WorldBinding exposes only immutable hashes and already-authoritative cursors
// needed to prepare a World transition. It contains no signing key or mutable
// authority capability.
type WorldBinding struct {
	MatchID                 string
	ChallengeID             string
	RulesetHash             contract.Digest
	DatasetHash             contract.Digest
	ChallengeSnapshotHash   contract.Digest
	RosterRoot              contract.Digest
	MatchVersion            uint64
	NextGlobalEventSequence uint64
	ParticipantSequences    map[string]uint64
}

// CommandPreflight is a non-mutating validation result used before reservation
// persistence and external World execution.
type CommandPreflight struct {
	Replay                  bool
	Event                   *contract.MatchEvent
	Fingerprint             contract.Digest
	MatchVersion            uint64
	NextGlobalEventSequence uint64
	ParticipantLastSequence uint64
}

func (e *Engine) WorldBinding() (WorldBinding, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	rosterRoot, err := contract.RosterRoot(e.roster())
	if err != nil {
		return WorldBinding{}, err
	}
	claim := e.participants[0].Authorization.Claim
	sequences := make(map[string]uint64, len(e.participants))
	for _, participant := range e.participants {
		sequences[participant.Authorization.Claim.AuthorizationID] = participant.LastCommandSequence
	}
	return WorldBinding{
		MatchID:                 e.matchID,
		ChallengeID:             e.challengeID,
		RulesetHash:             claim.RulesetHash,
		DatasetHash:             claim.DatasetHash,
		ChallengeSnapshotHash:   claim.ChallengeSnapshotHash,
		RosterRoot:              rosterRoot,
		MatchVersion:            e.version,
		NextGlobalEventSequence: uint64(len(e.events) + 1),
		ParticipantSequences:    sequences,
	}, nil
}

func (e *Engine) PreflightCommand(userID string, command contract.CommandEnvelope, now time.Time) (CommandPreflight, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.status == StatusCompleted {
		return CommandPreflight{}, fmt.Errorf("%w: completed matches reject commands", ErrState)
	}
	if e.status != StatusReady && e.status != StatusActive {
		return CommandPreflight{}, fmt.Errorf("%w: both participants must join before commands", ErrState)
	}
	participant, err := e.findParticipant(command.AuthorizationID)
	if err != nil {
		return CommandPreflight{}, err
	}
	claim := participant.Authorization.Claim
	if !participant.Joined || userID != claim.SubjectUserID || command.MatchID != e.matchID ||
		command.ChallengeID != e.challengeID || command.AgentID != claim.AgentID ||
		command.AgentKeyID != claim.AgentKeyID || command.ParticipantSlot != claim.ParticipantSlot {
		return CommandPreflight{}, fmt.Errorf("%w: command identity does not match consumed authorization", ErrAuthorization)
	}
	if err := contract.VerifyCommand(command, ed25519.PublicKey(claim.AgentPublicKey)); err != nil {
		return CommandPreflight{}, fmt.Errorf("%w: %v", ErrAuthorization, err)
	}
	fingerprint, err := contract.CommandFingerprint(command)
	if err != nil {
		return CommandPreflight{}, err
	}
	if previous, ok := e.commands[command.CommandID]; ok {
		if previous.Fingerprint != fingerprint {
			return CommandPreflight{}, fmt.Errorf("%w: command_id was reused with different signed bytes", ErrConflict)
		}
		copyEvent := cloneEvent(previous.Event)
		return CommandPreflight{
			Replay:                  true,
			Event:                   &copyEvent,
			Fingerprint:             fingerprint,
			MatchVersion:            e.version,
			NextGlobalEventSequence: uint64(len(e.events) + 1),
			ParticipantLastSequence: participant.LastCommandSequence,
		}, nil
	}
	if err := e.checkCommandQuota(len(command.Payload)); err != nil {
		return CommandPreflight{}, err
	}
	expectedSequence := participant.LastCommandSequence + 1
	if command.ParticipantSequence != expectedSequence {
		return CommandPreflight{}, fmt.Errorf("%w: expected participant sequence %d", ErrSequence, expectedSequence)
	}
	if command.ExpectedMatchVersion != e.version {
		return CommandPreflight{}, fmt.Errorf("%w: expected match version %d", ErrVersion, e.version)
	}
	if now.IsZero() || command.IssuedAtUnix < claim.IssuedAtUnix || now.Unix() < command.IssuedAtUnix {
		return CommandPreflight{}, fmt.Errorf("%w: command timestamp is outside the accepted interval", ErrAuthorization)
	}
	return CommandPreflight{
		Fingerprint:             fingerprint,
		MatchVersion:            e.version,
		NextGlobalEventSequence: uint64(len(e.events) + 1),
		ParticipantLastSequence: participant.LastCommandSequence,
	}, nil
}
