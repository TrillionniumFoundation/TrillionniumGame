package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	consumptionPath = "/v2/hepta/nakama/research-session-authorizations/consumed"
	completionPath  = "/v2/hepta/nakama/research-session-completions"
	controlPath     = "/state/control"
	requestLogPath  = "/state/requests.jsonl"
)

type digest string

type consumptionRequest struct {
	Schema           string   `json:"schema"`
	SessionID        string   `json:"session_id"`
	RosterVersion    uint64   `json:"roster_version"`
	RosterRoot       digest   `json:"roster_root"`
	AuthorizationIDs []string `json:"authorization_ids"`
	ConsumedAtUnix   int64    `json:"consumed_at_unix"`
	IdempotencyKey   string   `json:"idempotency_key"`
}

type consumptionReceipt struct {
	Schema               string   `json:"schema"`
	SessionID            string   `json:"session_id"`
	TeamID               string   `json:"team_id"`
	PaperProjectID       string   `json:"paper_project_id"`
	ChallengeID          string   `json:"challenge_id"`
	SessionRosterVersion uint64   `json:"session_roster_version"`
	RosterRoot           digest   `json:"roster_root"`
	AuthorizationIDs     []string `json:"authorization_ids"`
	ConsumedAtUnix       int64    `json:"consumed_at_unix"`
	IssuerKeyID          string   `json:"issuer_key_id"`
	Signature            string   `json:"signature"`
}

type terminalFacts struct {
	ResultCode                string `json:"result_code"`
	PaperBundleHash           digest `json:"paper_bundle_hash"`
	PaperReleaseCandidateHash digest `json:"paper_release_candidate_hash"`
	ContributionLedgerHash    digest `json:"contribution_ledger_hash"`
}

type completion struct {
	Schema                string        `json:"schema"`
	CommitmentID          digest        `json:"commitment_id"`
	SessionID             string        `json:"session_id"`
	TeamID                string        `json:"team_id"`
	PaperProjectID        string        `json:"paper_project_id"`
	ChallengeID           string        `json:"challenge_id"`
	RosterVersion         uint64        `json:"roster_version"`
	RosterRoot            digest        `json:"roster_root"`
	TerminalFacts         terminalFacts `json:"terminal_facts"`
	EventCount            uint64        `json:"event_count"`
	EventRoot             digest        `json:"event_root"`
	ArchiveHash           digest        `json:"archive_hash"`
	RulesetHash           digest        `json:"ruleset_hash"`
	ChallengeSnapshotHash digest        `json:"challenge_snapshot_hash"`
	CompletedAtUnix       int64         `json:"completed_at_unix"`
	AuthorityKeyID        string        `json:"authority_key_id"`
	Signature             string        `json:"signature"`
}

type completionRequest struct {
	Schema         string          `json:"schema"`
	Completion     completion      `json:"completion"`
	Archive        json.RawMessage `json:"archive"`
	IdempotencyKey string          `json:"idempotency_key"`
}

type completionReceipt struct {
	Schema                string        `json:"schema"`
	CommitmentID          digest        `json:"commitment_id"`
	SessionID             string        `json:"session_id"`
	TeamID                string        `json:"team_id"`
	PaperProjectID        string        `json:"paper_project_id"`
	ChallengeID           string        `json:"challenge_id"`
	RosterVersion         uint64        `json:"roster_version"`
	RosterRoot            digest        `json:"roster_root"`
	EventCount            uint64        `json:"event_count"`
	EventRoot             digest        `json:"event_root"`
	ArchiveHash           digest        `json:"archive_hash"`
	RulesetHash           digest        `json:"ruleset_hash"`
	ChallengeSnapshotHash digest        `json:"challenge_snapshot_hash"`
	NakamaAuthorityKeyID  string        `json:"nakama_authority_key_id"`
	TerminalFacts         terminalFacts `json:"terminal_facts"`
	VerifiedAtUnix        int64         `json:"verified_at_unix"`
	IssuerKeyID           string        `json:"issuer_key_id"`
	Signature             string        `json:"signature"`
}

type requestLog struct {
	Path           string `json:"path"`
	BodyBase64     string `json:"body_base64"`
	BodySHA256     string `json:"body_sha256"`
	IdempotencyKey string `json:"idempotency_key,omitempty"`
	Mode           string `json:"mode"`
	Response       string `json:"response"`
}

type server struct {
	issuerKeyID     string
	issuerPrivate   ed25519.PrivateKey
	authorityKeyID  string
	authorityPublic ed25519.PublicKey
	token           string
	teamID          string
	paperProjectID  string
	challengeID     string

	mu                 sync.Mutex
	lastMode           string
	tamperedCompletion bool
}

type frame struct{ bytes.Buffer }

func newFrame(domain string) *frame {
	value := &frame{}
	value.WriteString(domain)
	value.WriteByte(0)
	return value
}

func (f *frame) field(value []byte) *frame {
	if len(value) > int(^uint32(0)) {
		panic("canonical field too large")
	}
	var length [4]byte
	binary.BigEndian.PutUint32(length[:], uint32(len(value)))
	f.Write(length[:])
	f.Write(value)
	return f
}
func (f *frame) text(value string) *frame { return f.field([]byte(value)) }
func (f *frame) u32(value uint32) *frame {
	var encoded [4]byte
	binary.BigEndian.PutUint32(encoded[:], value)
	f.Write(encoded[:])
	return f
}
func (f *frame) u64(value uint64) *frame {
	var encoded [8]byte
	binary.BigEndian.PutUint64(encoded[:], value)
	f.Write(encoded[:])
	return f
}
func (f *frame) i64(value int64) *frame { return f.u64(uint64(value)) }
func (f *frame) hash(value digest) *frame {
	encoded, err := digestBytes(value)
	if err != nil {
		panic(err)
	}
	f.Write(encoded)
	return f
}

func digestBytes(value digest) ([]byte, error) {
	text := string(value)
	if !strings.HasPrefix(text, "sha256:") || len(text) != len("sha256:")+sha256.Size*2 {
		return nil, errors.New("invalid digest")
	}
	raw, err := hex.DecodeString(strings.TrimPrefix(text, "sha256:"))
	if err != nil || len(raw) != sha256.Size {
		return nil, errors.New("invalid digest")
	}
	return raw, nil
}

func terminalFrame(value terminalFacts) []byte {
	return newFrame("trnm_research_session_terminal_facts_v1").text(value.ResultCode).
		hash(value.PaperBundleHash).hash(value.PaperReleaseCandidateHash).
		hash(value.ContributionLedgerHash).Bytes()
}

func consumptionSigning(value consumptionReceipt) []byte {
	f := newFrame("hepta_research_session_authorization_set_consumption_receipt_v1").
		text(value.Schema).text(value.SessionID).text(value.TeamID).text(value.PaperProjectID).
		text(value.ChallengeID).u64(value.SessionRosterVersion).hash(value.RosterRoot).
		u32(uint32(len(value.AuthorizationIDs)))
	for _, authorizationID := range value.AuthorizationIDs {
		f.text(authorizationID)
	}
	return f.i64(value.ConsumedAtUnix).text(value.IssuerKeyID).Bytes()
}

func completionSigning(value completion) []byte {
	return newFrame("trnm_research_session_completed_signature_v1").text(value.Schema).
		hash(value.CommitmentID).text(value.SessionID).text(value.TeamID).text(value.PaperProjectID).
		text(value.ChallengeID).u64(value.RosterVersion).hash(value.RosterRoot).
		field(terminalFrame(value.TerminalFacts)).u64(value.EventCount).hash(value.EventRoot).
		hash(value.ArchiveHash).hash(value.RulesetHash).hash(value.ChallengeSnapshotHash).
		i64(value.CompletedAtUnix).text(value.AuthorityKeyID).Bytes()
}

func completionReceiptSigning(value completionReceipt) []byte {
	return newFrame("hepta_nakama_research_session_completion_receipt_v1").text(value.Schema).
		hash(value.CommitmentID).text(value.SessionID).text(value.TeamID).text(value.PaperProjectID).
		text(value.ChallengeID).u64(value.RosterVersion).hash(value.RosterRoot).u64(value.EventCount).
		hash(value.EventRoot).hash(value.ArchiveHash).hash(value.RulesetHash).
		hash(value.ChallengeSnapshotHash).text(value.NakamaAuthorityKeyID).
		field(terminalFrame(value.TerminalFacts)).i64(value.VerifiedAtUnix).text(value.IssuerKeyID).Bytes()
}

func decodeStrict(body []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("trailing JSON value")
	}
	return nil
}

func env(name string) string {
	value := os.Getenv(name)
	if value == "" {
		panic(name + " is required")
	}
	return value
}

func loadPrivate() ed25519.PrivateKey {
	seed, err := base64.StdEncoding.Strict().DecodeString(env("HEPTA_MOCK_ISSUER_PRIVATE_SEED"))
	if err != nil || len(seed) != ed25519.SeedSize {
		panic("HEPTA_MOCK_ISSUER_PRIVATE_SEED must be a canonical Ed25519 seed")
	}
	return ed25519.NewKeyFromSeed(seed)
}

func loadPublic(name string) ed25519.PublicKey {
	raw, err := base64.StdEncoding.Strict().DecodeString(env(name))
	if err != nil || len(raw) != ed25519.PublicKeySize {
		panic(name + " must be a canonical Ed25519 public key")
	}
	return raw
}

func (s *server) mode() string {
	raw, err := os.ReadFile(controlPath)
	if err != nil {
		return "down"
	}
	mode := strings.TrimSpace(string(raw))
	if mode == "" {
		return "down"
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if mode != s.lastMode {
		s.lastMode = mode
		s.tamperedCompletion = false
	}
	return mode
}

func (s *server) appendLog(value requestLog) {
	s.mu.Lock()
	defer s.mu.Unlock()
	// The bind-mounted log contains public protocol bytes only (never callback
	// tokens or private keys) and must be readable by the unprivileged host-side
	// independent verifier even though the scratch container runs as uid 65532.
	file, err := os.OpenFile(requestLogPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		panic(err)
	}
	defer file.Close()
	encoded, _ := json.Marshal(value)
	_, _ = file.Write(append(encoded, '\n'))
}

func (s *server) respond(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

func (s *server) callback(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost || request.Header.Get("Content-Type") != "application/json" ||
		request.Header.Get("x-hepta-nakama-token") != s.token {
		s.respond(writer, http.StatusBadRequest, map[string]string{"error": "invalid request envelope"})
		return
	}
	body, err := io.ReadAll(io.LimitReader(request.Body, 32*1024*1024))
	if err != nil {
		s.respond(writer, http.StatusBadRequest, map[string]string{"error": "body"})
		return
	}
	mode := s.mode()
	responseName := "down"
	idempotencyKey := ""
	// Parse the public envelope before applying the simulated outage so the
	// independent host verifier can compare the idempotency key across the 503
	// attempt and all later retries. The callback still validates the complete
	// typed request before issuing any signed receipt.
	var envelope struct {
		IdempotencyKey string `json:"idempotency_key"`
	}
	if json.Unmarshal(body, &envelope) == nil {
		idempotencyKey = envelope.IdempotencyKey
	}
	defer func() {
		hash := sha256.Sum256(body)
		s.appendLog(requestLog{Path: request.URL.Path, BodyBase64: base64.StdEncoding.EncodeToString(body),
			BodySHA256: hex.EncodeToString(hash[:]), IdempotencyKey: idempotencyKey, Mode: mode, Response: responseName})
	}()
	if mode == "down" {
		s.respond(writer, http.StatusServiceUnavailable, map[string]string{"error": "fixture down"})
		return
	}
	switch request.URL.Path {
	case consumptionPath:
		var input consumptionRequest
		if decodeStrict(body, &input) != nil || input.Schema != "hepta.paper_raid.research_session_consumption.v1" {
			responseName = "bad_consumption"
			s.respond(writer, http.StatusBadRequest, map[string]string{"error": "consumption"})
			return
		}
		idempotencyKey = input.IdempotencyKey
		receipt := consumptionReceipt{Schema: "hepta.paper_raid.authorization_set_consumption_receipt.v1",
			SessionID: input.SessionID, TeamID: s.teamID, PaperProjectID: s.paperProjectID,
			ChallengeID: s.challengeID, SessionRosterVersion: input.RosterVersion,
			RosterRoot: input.RosterRoot, AuthorizationIDs: input.AuthorizationIDs,
			ConsumedAtUnix: input.ConsumedAtUnix, IssuerKeyID: s.issuerKeyID}
		receipt.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(s.issuerPrivate, consumptionSigning(receipt)))
		responseName = "valid_consumption"
		s.respond(writer, http.StatusOK, receipt)
	case completionPath:
		var input completionRequest
		if decodeStrict(body, &input) != nil || input.Schema != "hepta.paper_raid.nakama_completion_ingest.v1" {
			responseName = "bad_completion"
			s.respond(writer, http.StatusBadRequest, map[string]string{"error": "completion"})
			return
		}
		idempotencyKey = input.IdempotencyKey
		signature, err := base64.StdEncoding.Strict().DecodeString(input.Completion.Signature)
		if err != nil || !ed25519.Verify(s.authorityPublic, completionSigning(input.Completion), signature) ||
			input.Completion.AuthorityKeyID != s.authorityKeyID {
			responseName = "rejected_nakama_signature"
			s.respond(writer, http.StatusBadRequest, map[string]string{"error": "Nakama signature"})
			return
		}
		receipt := completionReceipt{Schema: "hepta.paper_raid.nakama_completion_receipt.v1",
			CommitmentID: input.Completion.CommitmentID, SessionID: input.Completion.SessionID,
			TeamID: input.Completion.TeamID, PaperProjectID: input.Completion.PaperProjectID,
			ChallengeID: input.Completion.ChallengeID, RosterVersion: input.Completion.RosterVersion,
			RosterRoot: input.Completion.RosterRoot, EventCount: input.Completion.EventCount,
			EventRoot: input.Completion.EventRoot, ArchiveHash: input.Completion.ArchiveHash,
			RulesetHash: input.Completion.RulesetHash, ChallengeSnapshotHash: input.Completion.ChallengeSnapshotHash,
			NakamaAuthorityKeyID: input.Completion.AuthorityKeyID, TerminalFacts: input.Completion.TerminalFacts,
			VerifiedAtUnix: input.Completion.CompletedAtUnix, IssuerKeyID: s.issuerKeyID}
		signed := ed25519.Sign(s.issuerPrivate, completionReceiptSigning(receipt))
		if mode == "tamper_completion_once" {
			s.mu.Lock()
			if !s.tamperedCompletion {
				s.tamperedCompletion = true
				signed[0] ^= 1
				responseName = "tampered_completion"
			} else {
				responseName = "valid_completion"
			}
			s.mu.Unlock()
		} else {
			responseName = "valid_completion"
		}
		receipt.Signature = base64.StdEncoding.EncodeToString(signed)
		s.respond(writer, http.StatusCreated, receipt)
	default:
		responseName = "not_found"
		s.respond(writer, http.StatusNotFound, map[string]string{"error": "not found"})
	}
}

func healthcheck() {
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get("http://127.0.0.1:8080/health")
	if err != nil || response.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	_ = response.Body.Close()
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "-healthcheck" {
		healthcheck()
		return
	}
	value := &server{issuerKeyID: env("HEPTA_MOCK_ISSUER_KEY_ID"), issuerPrivate: loadPrivate(),
		authorityKeyID:  env("HEPTA_MOCK_NAKAMA_AUTHORITY_KEY_ID"),
		authorityPublic: loadPublic("HEPTA_MOCK_NAKAMA_AUTHORITY_PUBLIC_KEY"), token: env("HEPTA_MOCK_SERVICE_TOKEN"),
		teamID: env("HEPTA_MOCK_TEAM_ID"), paperProjectID: env("HEPTA_MOCK_PAPER_PROJECT_ID"),
		challengeID: env("HEPTA_MOCK_CHALLENGE_ID")}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(writer http.ResponseWriter, _ *http.Request) {
		value.respond(writer, http.StatusOK, map[string]bool{"ok": true})
	})
	mux.HandleFunc(consumptionPath, value.callback)
	mux.HandleFunc(completionPath, value.callback)
	server := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 3 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		panic(fmt.Sprintf("Hepta callback fixture stopped: %v", err))
	}
}
