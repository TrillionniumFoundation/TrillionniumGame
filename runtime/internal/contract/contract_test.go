package contract

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
)

func deterministicKey(label string) (ed25519.PublicKey, ed25519.PrivateKey) {
	seed := sha256.Sum256([]byte(label))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	return privateKey.Public().(ed25519.PublicKey), privateKey
}

func contractClaim(agentPublicKey ed25519.PublicKey) AuthorizationClaim {
	return AuthorizationClaim{
		Schema: AuthorizationSchema, AuthorizationID: "auth-1", MatchID: "match-1",
		ChallengeID: "challenge-1", AgentID: "agent-1", AgentDID: "did:trnm:agent-1",
		AgentKeyID: "agent-key-1", AgentPublicKey: agentPublicKey, SubjectUserID: "user-1",
		ParticipantSlot: 1, Role: "challenger", RulesetHash: NewDigest([]byte("ruleset")),
		DatasetHash: NewDigest([]byte("dataset")), ChallengeSnapshotHash: NewDigest([]byte("challenge")),
		IssuedAtUnix: 100, ExpiresAtUnix: 200,
	}
}

func TestContractDigestStrictness(t *testing.T) {
	digest := NewDigest([]byte("payload"))
	if err := digest.Validate(); err != nil {
		t.Fatalf("generated digest failed validation: %v", err)
	}
	for _, invalid := range []string{
		strings.ToUpper(string(digest)), strings.TrimPrefix(string(digest), "sha256:"),
		"sha256:xyz", string(digest) + " ",
	} {
		if _, err := ParseDigest(invalid); err == nil {
			t.Fatalf("invalid digest was accepted: %q", invalid)
		}
	}
}

func TestContractLogicalMatchIDAndTextBounds(t *testing.T) {
	for _, invalid := range []string{"../outside", "-starts-with-dash", strings.Repeat("a", 129)} {
		if err := ValidateLogicalMatchID(invalid); err == nil {
			t.Fatalf("invalid match ID was accepted: %q", invalid)
		}
	}
	claim := contractClaim(make(ed25519.PublicKey, ed25519.PublicKeySize))
	claim.ChallengeID = strings.Repeat("x", maxContractTextRunes+1)
	if err := claim.Validate(); err == nil {
		t.Fatal("oversized contract text was accepted")
	}
}

func TestContractAuthorizationSignatureSubjectAndExpiry(t *testing.T) {
	issuerPublic, issuerPrivate := deterministicKey("issuer")
	agentPublic, _ := deterministicKey("agent")
	auth, err := SignAuthorization(contractClaim(agentPublic), "issuer-key-1", issuerPrivate)
	if err != nil {
		t.Fatal(err)
	}
	trusted := map[string]ed25519.PublicKey{"issuer-key-1": issuerPublic}
	if err := VerifyAuthorization(auth, trusted, 150); err != nil {
		t.Fatalf("valid authorization rejected: %v", err)
	}
	if err := VerifyAuthorization(auth, trusted, 200); err == nil {
		t.Fatal("expired authorization accepted")
	}
	if err := VerifyAuthorization(auth, trusted, 99); err == nil {
		t.Fatal("not-yet-active authorization accepted")
	}
	tampered := auth
	tampered.Claim.SubjectUserID = "attacker"
	if err := VerifyAuthorization(tampered, trusted, 150); err == nil {
		t.Fatal("subject_user_id tampering was not detected")
	}
}

func TestContractCommandSignatureAndPayloadBinding(t *testing.T) {
	publicKey, privateKey := deterministicKey("agent-command")
	command, err := SignCommand(CommandEnvelope{
		Schema: CommandSchema, CommandID: "cmd-1", AuthorizationID: "auth-1", MatchID: "match-1",
		ChallengeID: "challenge-1", AgentID: "agent-1", ParticipantSlot: 1,
		ParticipantSequence: 1, ExpectedMatchVersion: 3, IssuedAtUnix: 150,
		PayloadType: "trnm.turn.v1", Payload: []byte("move-a"), AgentKeyID: "agent-key-1",
	}, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	if err := VerifyCommand(command, publicKey); err != nil {
		t.Fatalf("valid command rejected: %v", err)
	}
	tampered := command
	tampered.Payload = []byte("move-b")
	if err := VerifyCommand(tampered, publicKey); err == nil {
		t.Fatal("payload tampering was not detected")
	}
	tampered = command
	tampered.ExpectedMatchVersion++
	if err := VerifyCommand(tampered, publicKey); err == nil {
		t.Fatal("version tampering was not detected")
	}
}

func TestContractEventMerkleGoldenVector(t *testing.T) {
	type vectorFile struct {
		Schema      string `json:"schema"`
		EventMerkle struct {
			EventHashes []Digest `json:"event_hashes"`
			EventRoot   Digest   `json:"event_root"`
		} `json:"event_merkle"`
	}
	raw, err := os.ReadFile("../../../contracts/golden-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors vectorFile
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	if vectors.Schema != "trnm.nakama.golden_vectors.v1" || len(vectors.EventMerkle.EventHashes) < 2 {
		t.Fatal("golden vector schema is invalid")
	}
	commitments := make([]EventCommitment, len(vectors.EventMerkle.EventHashes))
	for index, eventHash := range vectors.EventMerkle.EventHashes {
		commitments[index] = EventCommitment{Sequence: uint64(index + 1), EventHash: eventHash}
	}
	root, err := EventRootFromCommitments(commitments)
	if err != nil {
		t.Fatal(err)
	}
	if root != vectors.EventMerkle.EventRoot {
		t.Fatalf("event root mismatch: got %s want %s", root, vectors.EventMerkle.EventRoot)
	}
	if _, err := EventRootFromCommitments([]EventCommitment{{Sequence: 2, EventHash: vectors.EventMerkle.EventHashes[0]}}); err == nil {
		t.Fatal("non-contiguous sequence was accepted")
	}
}

func TestContractRosterArchiveAndCompletion(t *testing.T) {
	keyOne, _ := deterministicKey("roster-one")
	keyTwo, _ := deterministicKey("roster-two")
	roster := []RosterEntry{
		{ParticipantSlot: 2, SubjectUserID: "user-2", AgentID: "agent-2", AgentDID: "did:2", AgentKeyID: "key-2", AgentKeyHash: NewDigest(keyTwo), Role: "defender"},
		{ParticipantSlot: 1, SubjectUserID: "user-1", AgentID: "agent-1", AgentDID: "did:1", AgentKeyID: "key-1", AgentKeyHash: NewDigest(keyOne), Role: "challenger"},
	}
	rootOne, err := RosterRoot(roster)
	if err != nil {
		t.Fatal(err)
	}
	rootTwo, err := RosterRoot([]RosterEntry{roster[1], roster[0]})
	if err != nil || rootOne != rootTwo {
		t.Fatal("roster root is not order-independent after slot canonicalization")
	}
	duplicate := append([]RosterEntry(nil), roster...)
	duplicate[0].SubjectUserID = duplicate[1].SubjectUserID
	if _, err := RosterRoot(duplicate); err == nil {
		t.Fatal("duplicate roster identity was accepted")
	}

	event, err := SealEvent(MatchEvent{Schema: EventSchema, EventID: "event-1", EventType: "turn", MatchID: "match-1",
		ChallengeID: "challenge-1", Sequence: 1, CausationID: "cmd-1", OccurredAtUnix: 150,
		ParticipantSlot: 1, MatchVersion: 2, PayloadType: "turn.v1", Payload: []byte("move")})
	if err != nil {
		t.Fatal(err)
	}
	eventRoot, _ := EventRoot([]MatchEvent{event})
	archiveHash, _ := ArchiveHash([]MatchEvent{event})
	commitmentID, err := CommitmentID("match-1", eventRoot, archiveHash)
	if err != nil {
		t.Fatal(err)
	}
	authorityPublic, authorityPrivate := deterministicKey("authority")
	completion, err := SignCompletion(MatchCompletedV1{Schema: CompletionSchema, CommitmentID: commitmentID,
		MatchID: "match-1", ChallengeID: "challenge-1", EventCount: 1, EventRoot: eventRoot,
		TerminalFacts: TerminalFacts{ResultCode: "decisive", WinnerSlot: 1, OutcomeHash: NewDigest([]byte("outcome"))},
		RosterRoot:    rootOne, RulesetHash: NewDigest([]byte("rules")), DatasetHash: NewDigest([]byte("data")),
		ChallengeSnapshotHash: NewDigest([]byte("challenge")), ArchiveHash: archiveHash,
		CompletedAtUnix: 160, AuthorityKeyID: "authority-1"}, authorityPrivate)
	if err != nil {
		t.Fatal(err)
	}
	if err := VerifyCompletion(completion, authorityPublic); err != nil {
		t.Fatal(err)
	}
	tamperedFacts := completion
	tamperedFacts.TerminalFacts.WinnerSlot = 2
	if err := VerifyCompletion(tamperedFacts, authorityPublic); err == nil {
		t.Fatal("signed terminal facts tampering was not detected")
	}
	mismatched := completion
	mismatched.CommitmentID = NewDigest([]byte("not-derived"))
	if _, err := SignCompletion(mismatched, authorityPrivate); err == nil {
		t.Fatal("completion with a non-derived commitment ID was signed")
	}
	completion.ArchiveHash = NewDigest([]byte("tampered"))
	if err := VerifyCompletion(completion, authorityPublic); err == nil {
		t.Fatal("completion tampering was not detected")
	}
}

func TestContractPayloadBoundsAndMalformedPrivateKeys(t *testing.T) {
	_, privateKey := deterministicKey("payload-bounds")
	command := CommandEnvelope{
		Schema: CommandSchema, CommandID: "cmd-1", AuthorizationID: "auth-1", MatchID: "match-1",
		ChallengeID: "challenge-1", AgentID: "agent-1", ParticipantSlot: 1, ParticipantSequence: 1,
		ExpectedMatchVersion: 1, IssuedAtUnix: 100, PayloadType: "turn.v1",
		Payload: make([]byte, MaxPayloadBytes+1), AgentKeyID: "agent-key-1",
	}
	if _, err := SignCommand(command, privateKey); err == nil {
		t.Fatal("oversized command payload was accepted")
	}
	event := MatchEvent{Schema: EventSchema, EventID: "event-1", EventType: "turn", MatchID: "match-1",
		ChallengeID: "challenge-1", Sequence: 1, CausationID: "cmd-1", OccurredAtUnix: 100,
		ParticipantSlot: 1, MatchVersion: 2, PayloadType: "turn.v1", Payload: make([]byte, MaxPayloadBytes+1)}
	if _, err := SealEvent(event); err == nil {
		t.Fatal("oversized event payload was accepted")
	}
	malformed := append(ed25519.PrivateKey(nil), privateKey...)
	malformed[len(malformed)-1] ^= 1
	command.Payload = []byte("move")
	if _, err := SignCommand(command, malformed); err == nil {
		t.Fatal("private key with a mismatched public suffix was accepted")
	}
}

func TestContractCompletionSigningGoldenVector(t *testing.T) {
	publicKey, privateKey := deterministicKey("completion-golden-v1")
	eventRoot := NewDigest([]byte("golden-event-root"))
	archiveHash := NewDigest([]byte("golden-archive"))
	commitmentID, err := CommitmentID("match-golden-1", eventRoot, archiveHash)
	if err != nil {
		t.Fatal(err)
	}
	completion, err := SignCompletion(MatchCompletedV1{
		Schema: CompletionSchema, CommitmentID: commitmentID, MatchID: "match-golden-1",
		ChallengeID: "challenge-golden-1", TerminalFacts: TerminalFacts{ResultCode: "decisive", WinnerSlot: 2,
			OutcomeHash: NewDigest([]byte("golden-outcome"))}, EventCount: 7, EventRoot: eventRoot,
		RosterRoot: NewDigest([]byte("golden-roster")), RulesetHash: NewDigest([]byte("golden-ruleset")),
		DatasetHash: NewDigest([]byte("golden-dataset")), ChallengeSnapshotHash: NewDigest([]byte("golden-challenge")),
		ArchiveHash: archiveHash, CompletedAtUnix: 1_800_000_123, AuthorityKeyID: "authority-golden-1",
	}, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	const expectedCommitmentID = "sha256:3b4959c95bcfdc116d4d02265fd09c25cc7c93c14421aa9257590186d848cc0c"
	const expectedSignature = "e11004073ccd031c6958ae1b3be5f0069cae5b8faa5d17c51ec69937d5b76d9e13f576c5a1b8504e5ba92655d4fbdbab7a1041e3d3401e98241e0ed983bcaa03"
	if string(completion.CommitmentID) != expectedCommitmentID {
		t.Fatalf("golden vector not frozen: commitment=%s signature=%x", completion.CommitmentID, completion.Signature)
	}
	if got := strings.ToLower(fmt.Sprintf("%x", completion.Signature)); got != expectedSignature {
		t.Fatalf("completion signature mismatch: got %s want %s", got, expectedSignature)
	}
	if err := VerifyCompletion(completion, publicKey); err != nil {
		t.Fatalf("golden completion failed verification: %v", err)
	}
}
