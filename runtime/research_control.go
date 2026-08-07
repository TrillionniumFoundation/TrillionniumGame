package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"time"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
	researchcore "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/runtime"
)

const (
	researchControlStorageCollection       = "trnm_research_control_v2"
	researchControlStorageSchemaV2         = "trnm.nakama.stored-research-control-command.v2"
	researchControlStorageSchema           = "trnm.nakama.stored-research-control-command.v3"
	researchControlResultSchema            = "trnm.nakama.research-control.result.v2"
	researchControlResponseSealSchemaV2    = "trnm.nakama.research-control.response-seal.v2"
	researchControlResponseSealSchema      = "trnm.nakama.research-control.response-seal.v3"
	researchControlStatusPending           = "pending"
	researchControlStatusApplied           = "applied"
	researchControlActivationMaximumRows   = 100_000
	nakamaSystemStorageOwnerID             = "00000000-0000-0000-0000-000000000000"
)

var (
	errResearchControlNotFound       = errors.New("research control command not found")
	errLegacyResearchControlPending = errors.New("legacy v2 research control command is pending")
)

type researchCreateRequestV2 struct {
	Schema             string                                   `json:"schema"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Authorizations     []researchcontract.SignedAuthorization   `json:"authorizations"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}

type researchResumeRequestV2 struct {
	Schema             string                                   `json:"schema"`
	LogicalSessionID   string                                   `json:"logical_session_id"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}

type researchReplaceRequestV2 struct {
	Schema             string                                   `json:"schema"`
	LogicalSessionID   string                                   `json:"logical_session_id"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Authorizations     []researchcontract.SignedAuthorization   `json:"authorizations"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}

type researchCompleteRequestV2 struct {
	Schema             string                                   `json:"schema"`
	LogicalSessionID   string                                   `json:"logical_session_id"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Facts              researchcontract.TerminalFacts           `json:"facts"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}

type researchControlResultV2 struct {
	Schema    string          `json:"schema"`
	CommandID string          `json:"command_id"`
	Operation string          `json:"operation"`
	TargetRPC string          `json:"target_rpc"`
	Result    json.RawMessage `json:"result"`
}

type researchControlResponseSealV2 struct {
	Schema                 string                  `json:"schema"`
	CommandID              string                  `json:"command_id"`
	Operation              string                  `json:"operation"`
	TargetRPC              string                  `json:"target_rpc"`
	SessionID              string                  `json:"session_id"`
	SessionRosterVersion   uint64                  `json:"session_roster_version"`
	AuthorizationSetID     string                  `json:"authorization_set_id"`
	PayloadHash            researchcontract.Digest `json:"payload_hash"`
	RequestSHA256          string                  `json:"request_sha256"`
	ResponseSHA256         string                  `json:"response_sha256"`
	AcceptedAtUnix         int64                   `json:"accepted_at_unix"`
	AppliedAtUnix          int64                   `json:"applied_at_unix"`
	ExpectedAuthorityKeyID string                  `json:"expected_authority_key_id"`
	AuthorityKeyID         string                  `json:"authority_key_id"`
}

// legacyResearchControlResponseSealV2 is the frozen response-signature domain
// written by the pre-v3 runtime. It must never gain the v3 expected-authority
// member: doing so would reinterpret and invalidate historical signatures.
type legacyResearchControlResponseSealV2 struct {
	Schema               string                  `json:"schema"`
	CommandID            string                  `json:"command_id"`
	Operation            string                  `json:"operation"`
	TargetRPC            string                  `json:"target_rpc"`
	SessionID            string                  `json:"session_id"`
	SessionRosterVersion uint64                  `json:"session_roster_version"`
	AuthorizationSetID   string                  `json:"authorization_set_id"`
	PayloadHash          researchcontract.Digest `json:"payload_hash"`
	RequestSHA256        string                  `json:"request_sha256"`
	ResponseSHA256       string                  `json:"response_sha256"`
	AcceptedAtUnix       int64                   `json:"accepted_at_unix"`
	AppliedAtUnix        int64                   `json:"applied_at_unix"`
	AuthorityKeyID       string                  `json:"authority_key_id"`
}

type storedResearchControlCommand struct {
	Schema                         string                  `json:"schema"`
	CommandID                      string                  `json:"command_id"`
	Operation                      string                  `json:"operation"`
	TargetRPC                      string                  `json:"target_rpc"`
	SessionID                      string                  `json:"session_id"`
	SessionRosterVersion           uint64                  `json:"session_roster_version"`
	AuthorizationSetID             string                  `json:"authorization_set_id"`
	PayloadHash                    researchcontract.Digest `json:"payload_hash"`
	RequestBodyBase64              string                  `json:"request_body_base64"`
	RequestSHA256                  string                  `json:"request_sha256"`
	AcceptedAtUnix                 int64                   `json:"accepted_at_unix"`
	ExpectedResponseAuthorityKeyID string                  `json:"expected_response_authority_key_id"`
	Status                         string                  `json:"status"`
	ResponseBodyBase64             string                  `json:"response_body_base64,omitempty"`
	ResponseSHA256                 string                  `json:"response_sha256,omitempty"`
	ResponseAuthorityKeyID         string                  `json:"response_authority_key_id,omitempty"`
	ResponseSignatureBase64        string                  `json:"response_signature_base64,omitempty"`
	AppliedAtUnix                  *int64                  `json:"applied_at_unix,omitempty"`
}

// legacyStoredResearchControlCommandV2 is the exact persisted shape used by
// the frozen v2 storage protocol. Applied rows remain verification-only replay
// inputs. Pending rows are activation blockers because v2 did not reserve an
// immutable future response signer and therefore cannot be safely completed by
// a v3 writer.
type legacyStoredResearchControlCommandV2 struct {
	Schema                  string                  `json:"schema"`
	CommandID               string                  `json:"command_id"`
	Operation               string                  `json:"operation"`
	TargetRPC               string                  `json:"target_rpc"`
	SessionID               string                  `json:"session_id"`
	SessionRosterVersion    uint64                  `json:"session_roster_version"`
	AuthorizationSetID      string                  `json:"authorization_set_id"`
	PayloadHash             researchcontract.Digest `json:"payload_hash"`
	RequestBodyBase64       string                  `json:"request_body_base64"`
	RequestSHA256           string                  `json:"request_sha256"`
	AcceptedAtUnix          int64                   `json:"accepted_at_unix"`
	Status                  string                  `json:"status"`
	ResponseBodyBase64      string                  `json:"response_body_base64,omitempty"`
	ResponseSHA256          string                  `json:"response_sha256,omitempty"`
	ResponseAuthorityKeyID  string                  `json:"response_authority_key_id,omitempty"`
	ResponseSignatureBase64 string                  `json:"response_signature_base64,omitempty"`
	AppliedAtUnix           *int64                  `json:"applied_at_unix,omitempty"`
}

type versionedStoredResearchControl struct {
	record   storedResearchControlCommand
	version  string
	legacyV2 *legacyStoredResearchControlCommandV2
	rawValue string
}

func canonicalResearchCreateBusinessV2(request researchCreateRequestV2) ([]byte, error) {
	return researchcontract.ResearchControlCreateBusinessBytesV2(request.Schema, request.AuthorizationSetID, request.Authorizations)
}

func canonicalResearchResumeBusinessV2(request researchResumeRequestV2) ([]byte, error) {
	return researchcontract.ResearchControlResumeBusinessBytesV2(request.Schema, request.LogicalSessionID, request.AuthorizationSetID)
}

func canonicalResearchReplaceBusinessV2(request researchReplaceRequestV2) ([]byte, error) {
	return researchcontract.ResearchControlReplaceBusinessBytesV2(request.Schema, request.LogicalSessionID,
		request.AuthorizationSetID, request.Authorizations)
}

func canonicalResearchCompleteBusinessV2(request researchCompleteRequestV2) ([]byte, error) {
	return researchcontract.ResearchControlCompleteBusinessBytesV2(request.Schema, request.LogicalSessionID,
		request.AuthorizationSetID, request.Facts)
}

func validateResearchControlBinding(control researchcontract.SignedResearchControlV2, business []byte,
	operation, sessionID string, rosterVersion uint64, authorizationSetID string,
	trusted map[string]ed25519.PublicKey, acceptedAt *int64) error {
	targetRPC, err := researchcontract.ResearchControlTargetRPC(operation)
	if err != nil {
		return err
	}
	claim := control.Claim
	if claim.Operation != operation || claim.TargetRPC != targetRPC || claim.SessionID != sessionID ||
		claim.SessionRosterVersion != rosterVersion || claim.AuthorizationSetID != authorizationSetID ||
		claim.PayloadHash != researchcontract.NewDigest(business) {
		return errors.New("research control claim differs from canonical business request")
	}
	if err := researchcontract.VerifyResearchControlSignatureV2(control, trusted); err != nil {
		return err
	}
	if acceptedAt != nil {
		if err := researchcontract.ResearchControlAcceptedAtV2(claim, *acceptedAt); err != nil {
			return err
		}
	}
	return nil
}

func newStoredResearchControlCommand(control researchcontract.SignedResearchControlV2, canonicalRequest []byte,
	acceptedAt int64, expectedResponseAuthorityKeyID string) storedResearchControlCommand {
	requestDigest := sha256.Sum256(canonicalRequest)
	claim := control.Claim
	return storedResearchControlCommand{
		Schema: researchControlStorageSchema, CommandID: claim.CommandID, Operation: claim.Operation,
		TargetRPC: claim.TargetRPC, SessionID: claim.SessionID, SessionRosterVersion: claim.SessionRosterVersion,
		AuthorizationSetID: claim.AuthorizationSetID, PayloadHash: claim.PayloadHash,
		RequestBodyBase64: base64.StdEncoding.EncodeToString(canonicalRequest), RequestSHA256: hex.EncodeToString(requestDigest[:]),
		AcceptedAtUnix: acceptedAt, ExpectedResponseAuthorityKeyID: expectedResponseAuthorityKeyID,
		Status: researchControlStatusPending,
	}
}

func (record *storedResearchControlCommand) applyResult(result any, appliedAt time.Time,
	authorityKeyID string, authorityPrivateKey ed25519.PrivateKey) error {
	if record.Status != researchControlStatusPending || record.ResponseBodyBase64 != "" ||
		record.ResponseSHA256 != "" || record.ResponseAuthorityKeyID != "" ||
		record.ResponseSignatureBase64 != "" || record.AppliedAtUnix != nil {
		return errors.New("research control result may only be applied to an empty pending command")
	}
	if err := researchcontract.ValidateKeyID(authorityKeyID); err != nil {
		return err
	}
	if authorityKeyID != record.ExpectedResponseAuthorityKeyID {
		return errors.New("research control response authority differs from the immutable command epoch")
	}
	if len(authorityPrivateKey) != ed25519.PrivateKeySize ||
		!bytes.Equal(authorityPrivateKey, ed25519.NewKeyFromSeed(authorityPrivateKey.Seed())) {
		return errors.New("research control response authority private key is invalid")
	}
	resultBody, err := json.Marshal(result)
	if err != nil {
		return err
	}
	wrapper, err := json.Marshal(researchControlResultV2{
		Schema: researchControlResultSchema, CommandID: record.CommandID, Operation: record.Operation,
		TargetRPC: record.TargetRPC, Result: resultBody,
	})
	if err != nil {
		return err
	}
	digest := sha256.Sum256(wrapper)
	unix := appliedAt.UTC().Unix()
	if unix < record.AcceptedAtUnix {
		unix = record.AcceptedAtUnix
	}
	if unix < 0 || uint64(unix) > researchcontract.MaximumJSONSafeInteger {
		return errors.New("research control response applied_at_unix is outside the JSON-safe range")
	}
	candidate := *record
	candidate.Status = researchControlStatusApplied
	candidate.ResponseBodyBase64 = base64.StdEncoding.EncodeToString(wrapper)
	candidate.ResponseSHA256 = hex.EncodeToString(digest[:])
	candidate.ResponseAuthorityKeyID = authorityKeyID
	candidate.AppliedAtUnix = &unix
	anchor, err := candidate.responseSealBytes()
	if err != nil {
		return err
	}
	candidate.ResponseSignatureBase64 = base64.StdEncoding.EncodeToString(ed25519.Sign(authorityPrivateKey, anchor))
	if _, err = candidate.response(); err != nil {
		return err
	}
	*record = candidate
	return nil
}

func (record storedResearchControlCommand) responseSealBytes() ([]byte, error) {
	if record.Status != researchControlStatusApplied || record.AppliedAtUnix == nil ||
		researchcontract.ValidateKeyID(record.ExpectedResponseAuthorityKeyID) != nil ||
		researchcontract.ValidateKeyID(record.ResponseAuthorityKeyID) != nil ||
		record.ResponseAuthorityKeyID != record.ExpectedResponseAuthorityKeyID {
		return nil, errors.New("research control response seal identity is invalid")
	}
	return json.Marshal(researchControlResponseSealV2{
		Schema: researchControlResponseSealSchema, CommandID: record.CommandID, Operation: record.Operation,
		TargetRPC: record.TargetRPC, SessionID: record.SessionID, SessionRosterVersion: record.SessionRosterVersion,
		AuthorizationSetID: record.AuthorizationSetID, PayloadHash: record.PayloadHash,
		RequestSHA256: record.RequestSHA256, ResponseSHA256: record.ResponseSHA256,
		AcceptedAtUnix: record.AcceptedAtUnix, AppliedAtUnix: *record.AppliedAtUnix,
		ExpectedAuthorityKeyID: record.ExpectedResponseAuthorityKeyID,
		AuthorityKeyID:         record.ResponseAuthorityKeyID,
	})
}

func (record storedResearchControlCommand) response() (string, error) {
	if record.Status != researchControlStatusApplied || record.AppliedAtUnix == nil {
		return "", errors.New("research control command is not applied")
	}
	body, err := base64.StdEncoding.Strict().DecodeString(record.ResponseBodyBase64)
	if err != nil || base64.StdEncoding.EncodeToString(body) != record.ResponseBodyBase64 {
		return "", errors.New("research control response is not canonical base64")
	}
	digest := sha256.Sum256(body)
	if record.ResponseSHA256 != hex.EncodeToString(digest[:]) {
		return "", errors.New("research control response checksum differs")
	}
	if _, err := record.responseSealBytes(); err != nil {
		return "", err
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(record.ResponseSignatureBase64)
	if err != nil || base64.StdEncoding.EncodeToString(signature) != record.ResponseSignatureBase64 || len(signature) != ed25519.SignatureSize {
		return "", errors.New("research control response signature encoding is invalid")
	}
	if err := validateResearchControlResponseBody(record, body); err != nil {
		return "", err
	}
	return string(body), nil
}

func validateResearchControlResponseBody(record storedResearchControlCommand, body []byte) error {
	var wrapper researchControlResultV2
	if decodeJSONStrict(string(body), &wrapper) != nil || wrapper.Schema != researchControlResultSchema ||
		wrapper.CommandID != record.CommandID || wrapper.Operation != record.Operation || wrapper.TargetRPC != record.TargetRPC || len(wrapper.Result) == 0 {
		return errors.New("research control response identity differs")
	}
	switch record.Operation {
	case researchcontract.ResearchControlOperationCreate, researchcontract.ResearchControlOperationResume, researchcontract.ResearchControlOperationReplace:
		var result researchRuntimeResponse
		if decodeJSONStrict(string(wrapper.Result), &result) != nil || result.Schema != "trnm.nakama.research-session.match-runtime.v1" || result.LogicalSessionID != record.SessionID {
			return errors.New("research control runtime result is invalid")
		}
		if result.RosterVersion != record.SessionRosterVersion ||
			!researchControlSafePositive(result.RuntimeGeneration) || !researchControlSafePositive(result.SessionVersion) ||
			!researchControlSafePositive(result.RosterVersion) || result.ExternalMatchID == "" ||
			result.RosterRoot.Validate() != nil || !validResearchControlRuntimeStatus(result.Status) {
			return errors.New("research control runtime result roster differs")
		}
	case researchcontract.ResearchControlOperationComplete:
		var result researchEvidenceResponse
		if decodeJSONStrict(string(wrapper.Result), &result) != nil || result.Schema != "trnm.nakama.research-session.evidence.v1" || result.LogicalSessionID != record.SessionID {
			return errors.New("research control completion result is invalid")
		}
		if result.Completion.RosterVersion != record.SessionRosterVersion ||
			!researchControlSafePositive(result.RuntimeGeneration) || result.ExternalMatchID == "" {
			return errors.New("research control completion result roster differs")
		}
	default:
		return errors.New("research control response operation is invalid")
	}
	reencoded, err := json.Marshal(wrapper)
	if err != nil || !bytes.Equal(reencoded, body) {
		return errors.New("research control response is not canonical JSON")
	}
	return nil
}

func (record legacyStoredResearchControlCommandV2) normalized() storedResearchControlCommand {
	return storedResearchControlCommand{
		Schema: record.Schema, CommandID: record.CommandID, Operation: record.Operation,
		TargetRPC: record.TargetRPC, SessionID: record.SessionID,
		SessionRosterVersion: record.SessionRosterVersion, AuthorizationSetID: record.AuthorizationSetID,
		PayloadHash: record.PayloadHash, RequestBodyBase64: record.RequestBodyBase64,
		RequestSHA256: record.RequestSHA256, AcceptedAtUnix: record.AcceptedAtUnix, Status: record.Status,
		ResponseBodyBase64: record.ResponseBodyBase64, ResponseSHA256: record.ResponseSHA256,
		ResponseAuthorityKeyID: record.ResponseAuthorityKeyID,
		ResponseSignatureBase64: record.ResponseSignatureBase64, AppliedAtUnix: record.AppliedAtUnix,
	}
}

func (record legacyStoredResearchControlCommandV2) responseSealBytes() ([]byte, error) {
	if record.Status != researchControlStatusApplied || record.AppliedAtUnix == nil ||
		researchcontract.ValidateKeyID(record.ResponseAuthorityKeyID) != nil {
		return nil, errors.New("legacy v2 research control response seal identity is invalid")
	}
	return json.Marshal(legacyResearchControlResponseSealV2{
		Schema: researchControlResponseSealSchemaV2, CommandID: record.CommandID,
		Operation: record.Operation, TargetRPC: record.TargetRPC, SessionID: record.SessionID,
		SessionRosterVersion: record.SessionRosterVersion, AuthorizationSetID: record.AuthorizationSetID,
		PayloadHash: record.PayloadHash, RequestSHA256: record.RequestSHA256,
		ResponseSHA256: record.ResponseSHA256, AcceptedAtUnix: record.AcceptedAtUnix,
		AppliedAtUnix: *record.AppliedAtUnix, AuthorityKeyID: record.ResponseAuthorityKeyID,
	})
}

func (record legacyStoredResearchControlCommandV2) response() (string, error) {
	if record.Status != researchControlStatusApplied || record.AppliedAtUnix == nil {
		return "", errors.New("legacy v2 research control command is not applied")
	}
	body, err := base64.StdEncoding.Strict().DecodeString(record.ResponseBodyBase64)
	if err != nil || base64.StdEncoding.EncodeToString(body) != record.ResponseBodyBase64 {
		return "", errors.New("legacy v2 research control response is not canonical base64")
	}
	digest := sha256.Sum256(body)
	if record.ResponseSHA256 != hex.EncodeToString(digest[:]) {
		return "", errors.New("legacy v2 research control response checksum differs")
	}
	if _, err := record.responseSealBytes(); err != nil {
		return "", err
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(record.ResponseSignatureBase64)
	if err != nil || base64.StdEncoding.EncodeToString(signature) != record.ResponseSignatureBase64 ||
		len(signature) != ed25519.SignatureSize {
		return "", errors.New("legacy v2 research control response signature encoding is invalid")
	}
	if err := validateResearchControlResponseBody(record.normalized(), body); err != nil {
		return "", err
	}
	return string(body), nil
}

func (command versionedStoredResearchControl) response() (string, error) {
	if command.legacyV2 != nil {
		return command.legacyV2.response()
	}
	return command.record.response()
}

func (command versionedStoredResearchControl) responseSealBytes() ([]byte, error) {
	if command.legacyV2 != nil {
		return command.legacyV2.responseSealBytes()
	}
	return command.record.responseSealBytes()
}

func (command versionedStoredResearchControl) responseAuthorityKeyID() string {
	if command.legacyV2 != nil {
		return command.legacyV2.ResponseAuthorityKeyID
	}
	return command.record.ResponseAuthorityKeyID
}

func verifyStoredResearchControlResponseAuthority(command versionedStoredResearchControl,
	publicKeys map[string]ed25519.PublicKey) error {
	if command.legacyV2 == nil &&
		command.record.ResponseAuthorityKeyID != command.record.ExpectedResponseAuthorityKeyID {
		return errors.New("research control response authority differs from the immutable command epoch")
	}
	anchor, err := command.responseSealBytes()
	if err != nil {
		return err
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(command.record.ResponseSignatureBase64)
	if err != nil || base64.StdEncoding.EncodeToString(signature) != command.record.ResponseSignatureBase64 ||
		len(signature) != ed25519.SignatureSize {
		return errors.New("research control response signature encoding is invalid")
	}
	responseAuthorityKeyID := command.responseAuthorityKeyID()
	responsePublic := publicKeys[responseAuthorityKeyID]
	if len(responsePublic) != ed25519.PublicKeySize {
		return fmt.Errorf("%w: research control response authority key %q is unavailable",
			researchcore.ErrAuthorityVerificationKeyUnavailable, responseAuthorityKeyID)
	}
	if !ed25519.Verify(responsePublic, anchor, signature) {
		return errors.New("research control response authority signature failed verification")
	}
	return nil
}

func researchControlSafePositive(value uint64) bool {
	return value > 0 && value <= researchcontract.MaximumJSONSafeInteger
}

func validResearchControlRuntimeStatus(status researchcore.Status) bool {
	switch status {
	case researchcore.StatusCreated, researchcore.StatusWaiting, researchcore.StatusReady,
		researchcore.StatusActive, researchcore.StatusPaused, researchcore.StatusCompleted:
		return true
	default:
		return false
	}
}

// verifiedResearchControlResponse anchors an applied command response to the
// independently authenticated durable research snapshot before any replay.
// The response checksum detects corruption, but is not an authority boundary:
// an attacker with direct storage access could otherwise change the result and
// recompute that checksum. Historical epoch lookup is required because a valid
// create/resume/replace response can be replayed after later roster rotations.
func (m *moduleRuntime) verifiedResearchControlResponse(ctx context.Context, nk storageGateway,
	command versionedStoredResearchControl) (string, error) {
	stored, err := loadStoredResearch(ctx, nk, command.record.SessionID)
	if err != nil {
		return "", errors.New("research control response has no durable session")
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		if errors.Is(err, researchcore.ErrAuthorityVerificationKeyUnavailable) {
			return "", fmt.Errorf("%w: research control durable session authority is unavailable",
				researchcore.ErrAuthorityVerificationKeyUnavailable)
		}
		return "", errors.New("research control response session failed verification")
	}
	return verifyResearchControlResponseAgainstEngine(command, engine, m.config.authorityPublicKeys)
}

// verifyResearchControlResponseAgainstEngine is shared by online exact replay
// and the startup activation scan. A structurally valid, authority-signed v2
// response is not activation-safe unless its referenced durable session also
// proves the historical roster or exact completion carried by that response.
func verifyResearchControlResponseAgainstEngine(command versionedStoredResearchControl,
	engine *researchcore.Engine, authorityPublicKeys map[string]ed25519.PublicKey) (string, error) {
	raw, err := command.response()
	if err != nil {
		return "", err
	}
	if err := verifyStoredResearchControlResponseAuthority(command, authorityPublicKeys); err != nil {
		return "", err
	}
	var wrapper researchControlResultV2
	if decodeJSONStrict(raw, &wrapper) != nil {
		return "", errors.New("research control response wrapper failed verification")
	}
	switch command.record.Operation {
	case researchcontract.ResearchControlOperationCreate, researchcontract.ResearchControlOperationResume,
		researchcontract.ResearchControlOperationReplace:
		var result researchRuntimeResponse
		if decodeJSONStrict(string(wrapper.Result), &result) != nil {
			return "", errors.New("research control runtime result failed verification")
		}
		expectedRoot, ok := engine.RosterRootForVersion(command.record.SessionRosterVersion)
		if !ok || result.RosterRoot != expectedRoot {
			return "", errors.New("research control runtime result roster_root differs from durable session")
		}
	case researchcontract.ResearchControlOperationComplete:
		var result researchEvidenceResponse
		if decodeJSONStrict(string(wrapper.Result), &result) != nil {
			return "", errors.New("research control completion result failed verification")
		}
		completion, ok := engine.Completion()
		completionPublic, found := engine.CompletionAuthorityPublicKey()
		if !ok || !found || !reflect.DeepEqual(result.Completion, *completion) ||
			result.AuthorityPublicKey != base64.StdEncoding.EncodeToString(completionPublic) {
			return "", errors.New("research control completion result differs from durable signed evidence")
		}
	default:
		return "", errors.New("research control response operation is invalid")
	}
	return raw, nil
}

func (m *moduleRuntime) researchAuthoritySigningKey(keyID string) (ed25519.PrivateKey, error) {
	if keyID == m.config.authorityKeyID && len(m.config.authorityPrivateKey) == ed25519.PrivateKeySize {
		return m.config.authorityPrivateKey, nil
	}
	return nil, errors.New("research session must use the active authority signing key")
}

func decodeStoredResearchControlRequest(record storedResearchControlCommand, trusted map[string]ed25519.PublicKey) error {
	body, err := base64.StdEncoding.Strict().DecodeString(record.RequestBodyBase64)
	if err != nil || base64.StdEncoding.EncodeToString(body) != record.RequestBodyBase64 {
		return errors.New("research control request is not canonical base64")
	}
	digest := sha256.Sum256(body)
	if record.RequestSHA256 != hex.EncodeToString(digest[:]) {
		return errors.New("research control request checksum differs")
	}
	var control researchcontract.SignedResearchControlV2
	var business []byte
	var canonical []byte
	var encodeErr error
	switch record.Operation {
	case researchcontract.ResearchControlOperationCreate:
		var request researchCreateRequestV2
		if decodeJSONStrict(string(body), &request) != nil {
			return errors.New("stored create control request is invalid")
		}
		control = request.Control
		business, encodeErr = canonicalResearchCreateBusinessV2(request)
		if encodeErr != nil {
			return fmt.Errorf("stored create business frame: %w", encodeErr)
		}
		canonical, encodeErr = json.Marshal(request)
	case researchcontract.ResearchControlOperationResume:
		var request researchResumeRequestV2
		if decodeJSONStrict(string(body), &request) != nil {
			return errors.New("stored resume control request is invalid")
		}
		control = request.Control
		business, encodeErr = canonicalResearchResumeBusinessV2(request)
		if encodeErr != nil {
			return fmt.Errorf("stored resume business frame: %w", encodeErr)
		}
		canonical, encodeErr = json.Marshal(request)
	case researchcontract.ResearchControlOperationReplace:
		var request researchReplaceRequestV2
		if decodeJSONStrict(string(body), &request) != nil {
			return errors.New("stored replacement control request is invalid")
		}
		control = request.Control
		business, encodeErr = canonicalResearchReplaceBusinessV2(request)
		if encodeErr != nil {
			return fmt.Errorf("stored replacement business frame: %w", encodeErr)
		}
		canonical, encodeErr = json.Marshal(request)
	case researchcontract.ResearchControlOperationComplete:
		var request researchCompleteRequestV2
		if decodeJSONStrict(string(body), &request) != nil {
			return errors.New("stored completion control request is invalid")
		}
		control = request.Control
		business, encodeErr = canonicalResearchCompleteBusinessV2(request)
		if encodeErr != nil {
			return fmt.Errorf("stored completion business frame: %w", encodeErr)
		}
		canonical, encodeErr = json.Marshal(request)
	default:
		return errors.New("stored research control operation is invalid")
	}
	if encodeErr != nil {
		return fmt.Errorf("stored research control canonical request: %w", encodeErr)
	}
	if !bytes.Equal(canonical, body) {
		return errors.New("stored research control request is not canonical JSON")
	}
	if control.Claim.CommandID != record.CommandID || control.Claim.TargetRPC != record.TargetRPC ||
		control.Claim.SessionID != record.SessionID || control.Claim.SessionRosterVersion != record.SessionRosterVersion ||
		control.Claim.AuthorizationSetID != record.AuthorizationSetID || control.Claim.PayloadHash != record.PayloadHash {
		return errors.New("stored research control request differs from record identity")
	}
	return validateResearchControlBinding(control, business, record.Operation, record.SessionID,
		record.SessionRosterVersion, record.AuthorizationSetID, trusted, &record.AcceptedAtUnix)
}

func validateStoredResearchControlCommand(record storedResearchControlCommand, trusted map[string]ed25519.PublicKey) error {
	if record.Schema != researchControlStorageSchema || researchcontract.ValidateCommandID(record.CommandID) != nil ||
		researchcontract.ValidateSessionID(record.SessionID) != nil || record.SessionRosterVersion == 0 ||
		record.SessionRosterVersion > researchcontract.MaximumJSONSafeInteger ||
		researchcontract.ValidateAuthorizationSetID(record.AuthorizationSetID) != nil || record.PayloadHash.Validate() != nil ||
		researchcontract.ValidateKeyID(record.ExpectedResponseAuthorityKeyID) != nil ||
		record.AcceptedAtUnix < 0 || uint64(record.AcceptedAtUnix) > researchcontract.MaximumJSONSafeInteger {
		return errors.New("stored research control identity is invalid")
	}
	target, err := researchcontract.ResearchControlTargetRPC(record.Operation)
	if err != nil || target != record.TargetRPC {
		return errors.New("stored research control target differs")
	}
	if err := decodeStoredResearchControlRequest(record, trusted); err != nil {
		return err
	}
	switch record.Status {
	case researchControlStatusPending:
		if record.ResponseBodyBase64 != "" || record.ResponseSHA256 != "" || record.ResponseAuthorityKeyID != "" ||
			record.ResponseSignatureBase64 != "" || record.AppliedAtUnix != nil {
			return errors.New("pending research control carries an applied response")
		}
	case researchControlStatusApplied:
		if _, err := record.response(); err != nil {
			return err
		}
		if *record.AppliedAtUnix < record.AcceptedAtUnix || uint64(*record.AppliedAtUnix) > researchcontract.MaximumJSONSafeInteger {
			return errors.New("research control application predates acceptance")
		}
	default:
		return errors.New("stored research control status is invalid")
	}
	return nil
}

func validateLegacyStoredResearchControlCommandV2(record legacyStoredResearchControlCommandV2,
	trusted map[string]ed25519.PublicKey) error {
	normalized := record.normalized()
	if record.Schema != researchControlStorageSchemaV2 || researchcontract.ValidateCommandID(record.CommandID) != nil ||
		researchcontract.ValidateSessionID(record.SessionID) != nil || record.SessionRosterVersion == 0 ||
		record.SessionRosterVersion > researchcontract.MaximumJSONSafeInteger ||
		researchcontract.ValidateAuthorizationSetID(record.AuthorizationSetID) != nil || record.PayloadHash.Validate() != nil ||
		record.AcceptedAtUnix < 0 || uint64(record.AcceptedAtUnix) > researchcontract.MaximumJSONSafeInteger {
		return errors.New("legacy v2 stored research control identity is invalid")
	}
	target, err := researchcontract.ResearchControlTargetRPC(record.Operation)
	if err != nil || target != record.TargetRPC {
		return errors.New("legacy v2 stored research control target differs")
	}
	if err := decodeStoredResearchControlRequest(normalized, trusted); err != nil {
		return err
	}
	switch record.Status {
	case researchControlStatusPending:
		if record.ResponseBodyBase64 != "" || record.ResponseSHA256 != "" ||
			record.ResponseAuthorityKeyID != "" || record.ResponseSignatureBase64 != "" ||
			record.AppliedAtUnix != nil {
			return errors.New("pending legacy v2 research control carries an applied response")
		}
	case researchControlStatusApplied:
		if _, err := record.response(); err != nil {
			return err
		}
		if *record.AppliedAtUnix < record.AcceptedAtUnix ||
			uint64(*record.AppliedAtUnix) > researchcontract.MaximumJSONSafeInteger {
			return errors.New("legacy v2 research control application predates acceptance")
		}
	default:
		return errors.New("legacy v2 stored research control status is invalid")
	}
	return nil
}

func decodeVersionedStoredResearchControl(value, commandID string,
	trusted map[string]ed25519.PublicKey) (versionedStoredResearchControl, error) {
	if err := validateJSONWire(value); err != nil {
		return versionedStoredResearchControl{}, errors.New("stored research control cannot be decoded")
	}
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal([]byte(value), &envelope); err != nil {
		return versionedStoredResearchControl{}, errors.New("stored research control cannot be decoded")
	}
	var schema string
	if raw, ok := envelope["schema"]; !ok || json.Unmarshal(raw, &schema) != nil {
		return versionedStoredResearchControl{}, errors.New("stored research control schema is missing")
	}
	switch schema {
	case researchControlStorageSchema:
		var record storedResearchControlCommand
		if decodeJSONStrict(value, &record) != nil || record.CommandID != commandID {
			return versionedStoredResearchControl{}, errors.New("stored research control cannot be decoded")
		}
		if err := validateStoredResearchControlCommand(record, trusted); err != nil {
			return versionedStoredResearchControl{}, err
		}
		return versionedStoredResearchControl{record: record, rawValue: value}, nil
	case researchControlStorageSchemaV2:
		var legacy legacyStoredResearchControlCommandV2
		if decodeJSONStrict(value, &legacy) != nil || legacy.CommandID != commandID {
			return versionedStoredResearchControl{}, errors.New("legacy v2 stored research control cannot be decoded")
		}
		if err := validateLegacyStoredResearchControlCommandV2(legacy, trusted); err != nil {
			return versionedStoredResearchControl{}, err
		}
		return versionedStoredResearchControl{
			record: legacy.normalized(), legacyV2: &legacy, rawValue: value,
		}, nil
	default:
		return versionedStoredResearchControl{}, fmt.Errorf("unsupported stored research control schema %q", schema)
	}
}

func assessStoredResearchControlActivation(ownerID, commandID, value string,
	trusted map[string]ed25519.PublicKey, authorityPublicKeys map[string]ed25519.PublicKey,
	activeAuthorityKeyID string) error {
	if ownerID != nakamaSystemStorageOwnerID {
		return errors.New("research control storage row is not system-owned")
	}
	stored, err := decodeVersionedStoredResearchControl(value, commandID, trusted)
	if err != nil {
		return err
	}
	if stored.legacyV2 != nil && stored.record.Status == researchControlStatusPending {
		return errLegacyResearchControlPending
	}
	if stored.legacyV2 == nil && stored.record.Status == researchControlStatusPending &&
		stored.record.ExpectedResponseAuthorityKeyID != activeAuthorityKeyID {
		return errors.New("pending v3 research control is reserved to an inactive response authority")
	}
	if stored.record.Status == researchControlStatusApplied {
		if err := verifyStoredResearchControlResponseAuthority(stored, authorityPublicKeys); err != nil {
			return err
		}
	}
	return nil
}

func assessStoredResearchControlActivationAgainstSession(ownerID, commandID, value string,
	sessionValue *string, config moduleConfig) error {
	if err := assessStoredResearchControlActivation(ownerID, commandID, value, config.controlIssuerKeys,
		config.authorityPublicKeys, config.authorityKeyID); err != nil {
		return err
	}
	stored, err := decodeVersionedStoredResearchControl(value, commandID, config.controlIssuerKeys)
	if err != nil {
		return err
	}
	if sessionValue == nil {
		return errors.New("research control has no durable session row")
	}
	var session storedResearchSession
	if decodeJSONStrict(*sessionValue, &session) != nil || session.LogicalSessionID != stored.record.SessionID {
		return errors.New("research control durable session cannot be decoded")
	}
	module := moduleRuntime{config: config}
	engine, err := module.restoreStoredResearch(session)
	if err != nil {
		if errors.Is(err, researchcore.ErrAuthorityVerificationKeyUnavailable) {
			return fmt.Errorf("%w: research control durable session authority is unavailable",
				researchcore.ErrAuthorityVerificationKeyUnavailable)
		}
		return fmt.Errorf("research control durable session failed verification: %w", err)
	}
	if stored.record.Status == researchControlStatusPending {
		view := engine.View()
		switch stored.record.Operation {
		case researchcontract.ResearchControlOperationCreate, researchcontract.ResearchControlOperationResume,
			researchcontract.ResearchControlOperationComplete:
			if session.ControlAuthorizationSetID != stored.record.AuthorizationSetID ||
				view.RosterVersion != stored.record.SessionRosterVersion {
				return errors.New("pending research control is fenced by a different durable roster epoch")
			}
		case researchcontract.ResearchControlOperationReplace:
			if stored.record.SessionRosterVersion != view.RosterVersion+1 {
				return errors.New("pending research roster replacement does not advance the durable roster epoch")
			}
		default:
			return errors.New("pending research control operation is invalid")
		}
		return nil
	}
	if _, err := verifyResearchControlResponseAgainstEngine(stored, engine, config.authorityPublicKeys); err != nil {
		return fmt.Errorf("applied research control response differs from durable session: %w", err)
	}
	return nil
}

// scanStoredResearchControlActivation is intentionally a read-only,
// repeatable-read scan. It makes every unsupported/malformed legacy row and
// every pending v2 reservation an explicit readiness blocker before a v3
// writer is admitted. Pending v3 rows must remain recoverable from an exact,
// fully verified durable session in the same snapshot. Applied v2 rows remain
// allowed only when their frozen response signature verifies against the
// public authority registry and that session proves the response's roster or
// completion facts.
func scanStoredResearchControlActivation(ctx context.Context, db *sql.DB, config moduleConfig) error {
	if db == nil {
		return errors.New("database handle is absent")
	}
	tx, err := db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		return fmt.Errorf("begin research control activation scan: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	rows, err := tx.QueryContext(ctx, `
SELECT controls.user_id::text, controls.key, controls.value::text, sessions.value::text
FROM public.storage AS controls
LEFT JOIN public.storage AS sessions
  ON sessions.collection = $2
 AND sessions.key = controls.value->>'session_id'
 AND sessions.user_id = '00000000-0000-0000-0000-000000000000'::uuid
WHERE controls.collection = $1
ORDER BY controls.user_id::text COLLATE "C", controls.key COLLATE "C"`,
		researchControlStorageCollection, researchStorageCollection)
	if err != nil {
		return fmt.Errorf("query research control activation rows: %w", err)
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		count++
		if count > researchControlActivationMaximumRows {
			return errors.New("research control activation scan exceeds its bounded row limit")
		}
		var ownerID, commandID, value string
		var storedSession sql.NullString
		if err := rows.Scan(&ownerID, &commandID, &value, &storedSession); err != nil {
			return fmt.Errorf("read research control activation row: %w", err)
		}
		var sessionValue *string
		if storedSession.Valid {
			sessionValue = &storedSession.String
		}
		if err := assessStoredResearchControlActivationAgainstSession(ownerID, commandID, value,
			sessionValue, config); err != nil {
			return fmt.Errorf("research control activation blocked at command %q: %w", commandID, err)
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate research control activation rows: %w", err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("close research control activation rows: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("finish research control activation scan: %w", err)
	}
	return nil
}

func loadStoredResearchControl(ctx context.Context, nk storageGateway, commandID string, trusted map[string]ed25519.PublicKey) (versionedStoredResearchControl, error) {
	if researchcontract.ValidateCommandID(commandID) != nil {
		return versionedStoredResearchControl{}, errors.New("invalid research control command id")
	}
	objects, err := nk.StorageRead(ctx, []*runtime.StorageRead{{Collection: researchControlStorageCollection, Key: commandID, UserID: ""}})
	if err != nil {
		return versionedStoredResearchControl{}, fmt.Errorf("read research control storage: %w", err)
	}
	if len(objects) == 0 {
		return versionedStoredResearchControl{}, errResearchControlNotFound
	}
	if len(objects) != 1 {
		return versionedStoredResearchControl{}, errors.New("research control storage returned multiple commands")
	}
	stored, err := decodeVersionedStoredResearchControl(objects[0].Value, commandID, trusted)
	if err != nil {
		return versionedStoredResearchControl{}, err
	}
	if stored.legacyV2 != nil && stored.record.Status == researchControlStatusPending {
		return versionedStoredResearchControl{}, errLegacyResearchControlPending
	}
	stored.version = objects[0].Version
	return stored, nil
}

func storedResearchControlWrite(record storedResearchControlCommand, version string, trusted map[string]ed25519.PublicKey) (*runtime.StorageWrite, error) {
	if err := validateStoredResearchControlCommand(record, trusted); err != nil {
		return nil, err
	}
	value, err := json.Marshal(record)
	if err != nil {
		return nil, err
	}
	return &runtime.StorageWrite{Collection: researchControlStorageCollection, Key: record.CommandID, UserID: "", Value: string(value), Version: version, PermissionRead: 0, PermissionWrite: 0}, nil
}

func createStoredResearchControl(ctx context.Context, nk storageGateway, record storedResearchControlCommand, trusted map[string]ed25519.PublicKey) (string, error) {
	return writeStoredResearchControl(ctx, nk, record, "*", trusted)
}

func updateStoredResearchControl(ctx context.Context, nk storageGateway, before,
	after storedResearchControlCommand, version string, trusted map[string]ed25519.PublicKey) (string, error) {
	if version == "" || version == "*" {
		return "", errors.New("concrete research control storage version required")
	}
	if err := validateResearchControlTransition(before, after, trusted); err != nil {
		return "", err
	}
	return writeStoredResearchControl(ctx, nk, after, version, trusted)
}

func writeStoredResearchControl(ctx context.Context, nk storageGateway, record storedResearchControlCommand, version string, trusted map[string]ed25519.PublicKey) (string, error) {
	write, err := storedResearchControlWrite(record, version, trusted)
	if err != nil {
		return "", err
	}
	acks, err := nk.StorageWrite(ctx, []*runtime.StorageWrite{write})
	if err != nil {
		if errors.Is(err, runtime.ErrStorageRejectedVersion) {
			return "", errors.New("research control storage version conflict")
		}
		return "", err
	}
	if len(acks) != 1 || acks[0].Collection != researchControlStorageCollection || acks[0].Key != record.CommandID || acks[0].Version == "" {
		return "", errors.New("research control storage write returned no matching version")
	}
	return acks[0].Version, nil
}

func createStoredResearchWithControl(ctx context.Context, nk storageGateway, session storedResearchSession,
	command storedResearchControlCommand, trusted map[string]ed25519.PublicKey) (string, string, error) {
	return writeStoredResearchWithControl(ctx, nk, session, "*", command, "*", trusted)
}

func updateStoredResearchWithControl(ctx context.Context, nk storageGateway, session storedResearchSession, sessionVersion string,
	commandBefore, commandAfter storedResearchControlCommand, commandVersion string,
	trusted map[string]ed25519.PublicKey) (string, string, error) {
	if sessionVersion == "" || sessionVersion == "*" || commandVersion == "" || commandVersion == "*" {
		return "", "", errors.New("concrete session and control storage versions required")
	}
	if err := validateResearchControlTransition(commandBefore, commandAfter, trusted); err != nil {
		return "", "", err
	}
	return writeStoredResearchWithControl(ctx, nk, session, sessionVersion, commandAfter, commandVersion, trusted)
}

func writeStoredResearchWithControl(ctx context.Context, nk storageGateway, session storedResearchSession, sessionVersion string,
	command storedResearchControlCommand, commandVersion string, trusted map[string]ed25519.PublicKey) (string, string, error) {
	if session.LogicalSessionID != command.SessionID {
		return "", "", errors.New("research session and control command identities differ")
	}
	sessionWrite, err := storedResearchWrite(session, sessionVersion)
	if err != nil {
		return "", "", err
	}
	commandWrite, err := storedResearchControlWrite(command, commandVersion, trusted)
	if err != nil {
		return "", "", err
	}
	acks, err := nk.StorageWrite(ctx, []*runtime.StorageWrite{sessionWrite, commandWrite})
	if err != nil {
		if errors.Is(err, runtime.ErrStorageRejectedVersion) {
			return "", "", errors.New("research session or control storage version conflict")
		}
		return "", "", err
	}
	var sessionAck, commandAck string
	for _, ack := range acks {
		switch {
		case ack.Collection == researchStorageCollection && ack.Key == session.LogicalSessionID:
			sessionAck = ack.Version
		case ack.Collection == researchControlStorageCollection && ack.Key == command.CommandID:
			commandAck = ack.Version
		}
	}
	if len(acks) != 2 || sessionAck == "" || commandAck == "" {
		return "", "", errors.New("atomic research session/control write returned incomplete acknowledgements")
	}
	return sessionAck, commandAck, nil
}

func exactResearchControlRequest(record storedResearchControlCommand, canonical []byte) bool {
	digest := sha256.Sum256(canonical)
	return record.RequestBodyBase64 == base64.StdEncoding.EncodeToString(canonical) && record.RequestSHA256 == hex.EncodeToString(digest[:])
}

func sameResearchControlReservation(left, right storedResearchControlCommand) bool {
	return left.Schema == right.Schema && left.CommandID == right.CommandID && left.Operation == right.Operation &&
		left.TargetRPC == right.TargetRPC && left.SessionID == right.SessionID &&
		left.SessionRosterVersion == right.SessionRosterVersion && left.AuthorizationSetID == right.AuthorizationSetID &&
		left.PayloadHash == right.PayloadHash && left.RequestBodyBase64 == right.RequestBodyBase64 &&
		left.RequestSHA256 == right.RequestSHA256 && left.AcceptedAtUnix == right.AcceptedAtUnix &&
		left.ExpectedResponseAuthorityKeyID == right.ExpectedResponseAuthorityKeyID
}

func validateResearchControlTransition(before, after storedResearchControlCommand,
	trusted map[string]ed25519.PublicKey) error {
	if err := validateStoredResearchControlCommand(before, trusted); err != nil {
		return fmt.Errorf("research control transition source is invalid: %w", err)
	}
	if err := validateStoredResearchControlCommand(after, trusted); err != nil {
		return fmt.Errorf("research control transition result is invalid: %w", err)
	}
	if before.Status != researchControlStatusPending || after.Status != researchControlStatusApplied ||
		!sameResearchControlReservation(before, after) {
		return errors.New("research control transition must preserve its immutable pending reservation")
	}
	return nil
}

func storedResearchReplaceRequestV2(record storedResearchControlCommand) (researchReplaceRequestV2, error) {
	var request researchReplaceRequestV2
	body, err := base64.StdEncoding.Strict().DecodeString(record.RequestBodyBase64)
	if err != nil || decodeJSONStrict(string(body), &request) != nil || request.Control.Claim.CommandID != record.CommandID {
		return researchReplaceRequestV2{}, errors.New("stored replacement control request is unavailable")
	}
	return request, nil
}

func storedResearchCompleteRequestV2(record storedResearchControlCommand) (researchCompleteRequestV2, error) {
	var request researchCompleteRequestV2
	body, err := base64.StdEncoding.Strict().DecodeString(record.RequestBodyBase64)
	if err != nil || decodeJSONStrict(string(body), &request) != nil || request.Control.Claim.CommandID != record.CommandID {
		return researchCompleteRequestV2{}, errors.New("stored completion control request is unavailable")
	}
	return request, nil
}

func (m *moduleRuntime) existingResearchControl(ctx context.Context, nk storageGateway,
	control researchcontract.SignedResearchControlV2, canonicalRequest []byte) (versionedStoredResearchControl, string, bool, error) {
	stored, err := loadStoredResearchControl(ctx, nk, control.Claim.CommandID, m.config.controlIssuerKeys)
	if errors.Is(err, errResearchControlNotFound) {
		return versionedStoredResearchControl{}, "", false, nil
	}
	if err != nil {
		return versionedStoredResearchControl{}, "", false, err
	}
	if !exactResearchControlRequest(stored.record, canonicalRequest) {
		return versionedStoredResearchControl{}, "", true, errors.New("research control command_id was reused with a different request")
	}
	if stored.record.Status == researchControlStatusApplied {
		response, err := m.verifiedResearchControlResponse(ctx, nk, stored)
		return stored, response, true, err
	}
	return stored, "", true, nil
}

func (m *moduleRuntime) recoverResearchRuntimeControl(ctx context.Context, nk runtime.NakamaModule,
	command versionedStoredResearchControl) (string, error) {
	if command.record.Status == researchControlStatusApplied {
		return m.verifiedResearchControlResponse(ctx, nk, command)
	}
	stored, err := loadStoredResearch(ctx, nk, command.record.SessionID)
	if err != nil {
		return "", errors.New("research control session is not recoverable")
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		return "", errors.New("research control session failed verification")
	}
	view := engine.View()
	if stored.record.ControlAuthorizationSetID != command.record.AuthorizationSetID ||
		view.RosterVersion != command.record.SessionRosterVersion {
		return "", errors.New("research control command is fenced by a different roster epoch")
	}
	if _, completed := engine.Completion(); completed && !hasPendingResearchDeliveries(stored.record) {
		return "", errors.New("completed research session has no resumable runtime work")
	}
	if _, _, err := m.ensureResearchRuntime(ctx, nk, stored); err != nil {
		return "", err
	}
	current, err := loadStoredResearch(ctx, nk, command.record.SessionID)
	if err != nil || current.record.ExternalMatchID == "" {
		return "", errors.New("research runtime did not become durable")
	}
	currentEngine, err := m.restoreStoredResearch(current.record)
	if err != nil {
		return "", errors.New("research runtime snapshot failed verification")
	}
	m.researchControlTestFailpoint(command.record.Operation+"_after_runtime", command.record.CommandID)
	updated := command.record
	authorityPrivateKey, err := m.researchAuthoritySigningKey(command.record.ExpectedResponseAuthorityKeyID)
	if err != nil {
		return "", err
	}
	if err := updated.applyResult(researchRuntimeFor(current.record, currentEngine.View(), current.record.ExternalMatchID),
		time.Now().UTC(), command.record.ExpectedResponseAuthorityKeyID, authorityPrivateKey); err != nil {
		return "", err
	}
	if _, err := updateStoredResearchControl(ctx, nk, command.record, updated, command.version,
		m.config.controlIssuerKeys); err != nil {
		reloaded, loadErr := loadStoredResearchControl(ctx, nk, command.record.CommandID, m.config.controlIssuerKeys)
		if loadErr == nil && sameResearchControlReservation(command.record, reloaded.record) &&
			reloaded.record.Status == researchControlStatusApplied {
			return m.verifiedResearchControlResponse(ctx, nk, reloaded)
		}
		return "", errors.New("research control result persistence conflict")
	}
	return m.verifiedResearchControlResponse(ctx, nk, versionedStoredResearchControl{record: updated, version: command.version})
}

func mustDecodeBase64(value string) []byte {
	decoded, _ := base64.StdEncoding.Strict().DecodeString(value)
	return decoded
}

func (m *moduleRuntime) rpcResearchCreateV2(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchCreateRequestV2
	if decodeJSONStrict(payload, &request) != nil || request.Schema != "trnm.nakama.research-session.create.v2" ||
		len(request.Authorizations) < researchcontract.MinParticipants || len(request.Authorizations) > researchcontract.MaxParticipants ||
		researchcontract.ValidateAuthorizationSetID(request.AuthorizationSetID) != nil {
		return "", runtime.NewError("invalid signed research create request", 3)
	}
	first := request.Authorizations[0].Claim
	business, err := canonicalResearchCreateBusinessV2(request)
	if err != nil || validateResearchControlBinding(request.Control, business, researchcontract.ResearchControlOperationCreate,
		first.SessionID, first.RosterVersion, request.AuthorizationSetID, m.config.controlIssuerKeys, nil) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	canonicalRequest, err := json.Marshal(request)
	if err != nil {
		return "", runtime.NewError("research create request encoding failed", 13)
	}
	if existing, response, found, existingErr := m.existingResearchControl(ctx, nk, request.Control, canonicalRequest); found {
		if existingErr != nil {
			if errors.Is(existingErr, researchcore.ErrAuthorityVerificationKeyUnavailable) {
				return "", runtime.NewError("stored research control or session authority key is missing from the public verification registry", 13)
			}
			return "", runtime.NewError("research control command conflict", 10)
		}
		if response != "" {
			return response, nil
		}
		response, err := m.recoverResearchRuntimeControl(ctx, nk, existing)
		if err != nil {
			return "", runtime.NewError("pending research create recovery failed", 14)
		}
		return response, nil
	} else if existingErr != nil {
		return "", runtime.NewError("research control storage failed", 13)
	}
	now := time.Now().UTC()
	if err := researchcontract.ResearchControlAcceptedAtV2(request.Control.Claim, now.Unix()); err != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	engine, err := researchcore.NewSession(researchcore.NewSessionOptions{
		Authorizations: request.Authorizations, TrustedIssuerKeys: m.config.issuerKeys,
		AuthorityKeyID: m.config.authorityKeyID, AuthorityPrivateKey: m.config.authorityPrivateKey, Now: now,
	})
	if err != nil {
		return "", runtime.NewError("research authorization snapshot rejected", 3)
	}
	view := engine.View()
	if view.SessionID != request.Control.Claim.SessionID || view.RosterVersion != request.Control.Claim.SessionRosterVersion {
		return "", runtime.NewError("signed research control roster binding rejected", 7)
	}
	snapshot, err := engine.Snapshot()
	if err != nil {
		return "", runtime.NewError("research snapshot encoding failed", 13)
	}
	session, err := newStoredResearch(view.SessionID, snapshot, request.Authorizations, now.Unix())
	if err != nil {
		return "", runtime.NewError("research session construction failed", 3)
	}
	session.ControlAuthorizationSetID = request.AuthorizationSetID
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix(), m.config.authorityKeyID)
	_, commandVersion, err := createStoredResearchWithControl(ctx, nk, session, command, m.config.controlIssuerKeys)
	if err != nil {
		if existing, response, found, loadErr := m.existingResearchControl(ctx, nk, request.Control, canonicalRequest); found && loadErr == nil {
			if response != "" {
				return response, nil
			}
			result, recoverErr := m.recoverResearchRuntimeControl(ctx, nk, existing)
			if recoverErr == nil {
				return result, nil
			}
		}
		return "", runtime.NewError("research session or command already exists", 10)
	}
	response, err := m.recoverResearchRuntimeControl(ctx, nk, versionedStoredResearchControl{record: command, version: commandVersion})
	if err != nil {
		return "", runtime.NewError("research snapshot persisted but runtime recovery is pending", 14)
	}
	return response, nil
}

func (m *moduleRuntime) rpcResearchResumeV2(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchResumeRequestV2
	if decodeJSONStrict(payload, &request) != nil || request.Schema != "trnm.nakama.research-session.resume.v2" ||
		researchcontract.ValidateSessionID(request.LogicalSessionID) != nil || researchcontract.ValidateAuthorizationSetID(request.AuthorizationSetID) != nil {
		return "", runtime.NewError("invalid signed research resume request", 3)
	}
	business, err := canonicalResearchResumeBusinessV2(request)
	claim := request.Control.Claim
	if err != nil || validateResearchControlBinding(request.Control, business, researchcontract.ResearchControlOperationResume,
		request.LogicalSessionID, claim.SessionRosterVersion, request.AuthorizationSetID, m.config.controlIssuerKeys, nil) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	canonicalRequest, err := json.Marshal(request)
	if err != nil {
		return "", runtime.NewError("research resume request encoding failed", 13)
	}
	if existing, response, found, existingErr := m.existingResearchControl(ctx, nk, request.Control, canonicalRequest); found {
		if existingErr != nil {
			if errors.Is(existingErr, researchcore.ErrAuthorityVerificationKeyUnavailable) {
				return "", runtime.NewError("stored research control or session authority key is missing from the public verification registry", 13)
			}
			return "", runtime.NewError("research control command conflict", 10)
		}
		if response != "" {
			return response, nil
		}
		result, recoverErr := m.recoverResearchRuntimeControl(ctx, nk, existing)
		if recoverErr != nil {
			return "", runtime.NewError("pending research resume recovery failed", 14)
		}
		return result, nil
	} else if existingErr != nil {
		return "", runtime.NewError("research control storage failed", 13)
	}
	now := time.Now().UTC()
	if researchcontract.ResearchControlAcceptedAtV2(claim, now.Unix()) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil || stored.record.ControlAuthorizationSetID != request.AuthorizationSetID || engine.View().RosterVersion != claim.SessionRosterVersion {
		return "", runtime.NewError("signed research control epoch rejected", 7)
	}
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix(), m.config.authorityKeyID)
	version, err := createStoredResearchControl(ctx, nk, command, m.config.controlIssuerKeys)
	if err != nil {
		return "", runtime.NewError("research control command conflict", 10)
	}
	result, err := m.recoverResearchRuntimeControl(ctx, nk, versionedStoredResearchControl{record: command, version: version})
	if err != nil {
		return "", runtime.NewError("research resume recovery is pending", 14)
	}
	return result, nil
}

func (m *moduleRuntime) rpcResearchReplaceV2(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchReplaceRequestV2
	if decodeJSONStrict(payload, &request) != nil || request.Schema != "trnm.nakama.research-session.replace-roster.v2" ||
		researchcontract.ValidateSessionID(request.LogicalSessionID) != nil || researchcontract.ValidateAuthorizationSetID(request.AuthorizationSetID) != nil ||
		len(request.Authorizations) < researchcontract.MinParticipants || len(request.Authorizations) > researchcontract.MaxParticipants {
		return "", runtime.NewError("invalid signed research replacement request", 3)
	}
	business, err := canonicalResearchReplaceBusinessV2(request)
	claim := request.Control.Claim
	if err != nil || validateResearchControlBinding(request.Control, business, researchcontract.ResearchControlOperationReplace,
		request.LogicalSessionID, claim.SessionRosterVersion, request.AuthorizationSetID, m.config.controlIssuerKeys, nil) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	canonicalRequest, err := json.Marshal(request)
	if err != nil {
		return "", runtime.NewError("research replacement request encoding failed", 13)
	}
	if existing, response, found, existingErr := m.existingResearchControl(ctx, nk, request.Control, canonicalRequest); found {
		if existingErr != nil {
			if errors.Is(existingErr, researchcore.ErrAuthorityVerificationKeyUnavailable) {
				return "", runtime.NewError("stored research control or session authority key is missing from the public verification registry", 13)
			}
			return "", runtime.NewError("research control command conflict", 10)
		}
		if response != "" {
			return response, nil
		}
		return m.executePendingResearchControlSignal(ctx, nk, existing)
	} else if existingErr != nil {
		return "", runtime.NewError("research control storage failed", 13)
	}
	now := time.Now().UTC()
	if researchcontract.ResearchControlAcceptedAtV2(claim, now.Unix()) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil || stored.record.ExternalMatchID == "" || claim.SessionRosterVersion != engine.View().RosterVersion+1 ||
		request.Authorizations[0].Claim.RosterVersion != claim.SessionRosterVersion || request.Authorizations[0].Claim.SessionID != request.LogicalSessionID {
		return "", runtime.NewError("signed research replacement epoch rejected", 7)
	}
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix(), m.config.authorityKeyID)
	version, err := createStoredResearchControl(ctx, nk, command, m.config.controlIssuerKeys)
	if err != nil {
		return "", runtime.NewError("research control command conflict", 10)
	}
	return m.executePendingResearchControlSignal(ctx, nk, versionedStoredResearchControl{record: command, version: version})
}

func (m *moduleRuntime) rpcResearchCompleteV2(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
		return "", runtime.NewError("research runtime is not ready", 14)
	}
	var request researchCompleteRequestV2
	if decodeJSONStrict(payload, &request) != nil || request.Schema != "trnm.nakama.research-session.complete.v2" ||
		researchcontract.ValidateSessionID(request.LogicalSessionID) != nil || researchcontract.ValidateAuthorizationSetID(request.AuthorizationSetID) != nil {
		return "", runtime.NewError("invalid signed research completion request", 3)
	}
	if _, err := request.Facts.CanonicalBytes(); err != nil {
		return "", runtime.NewError("invalid signed research completion facts", 3)
	}
	business, err := canonicalResearchCompleteBusinessV2(request)
	claim := request.Control.Claim
	if err != nil || validateResearchControlBinding(request.Control, business, researchcontract.ResearchControlOperationComplete,
		request.LogicalSessionID, claim.SessionRosterVersion, request.AuthorizationSetID, m.config.controlIssuerKeys, nil) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	canonicalRequest, err := json.Marshal(request)
	if err != nil {
		return "", runtime.NewError("research completion request encoding failed", 13)
	}
	if existing, response, found, existingErr := m.existingResearchControl(ctx, nk, request.Control, canonicalRequest); found {
		if existingErr != nil {
			if errors.Is(existingErr, researchcore.ErrAuthorityVerificationKeyUnavailable) {
				return "", runtime.NewError("stored research control or session authority key is missing from the public verification registry", 13)
			}
			return "", runtime.NewError("research control command conflict", 10)
		}
		if response != "" {
			return response, nil
		}
		return m.executePendingResearchControlSignal(ctx, nk, existing)
	} else if existingErr != nil {
		return "", runtime.NewError("research control storage failed", 13)
	}
	now := time.Now().UTC()
	if researchcontract.ResearchControlAcceptedAtV2(claim, now.Unix()) != nil {
		return "", runtime.NewError("signed research control rejected", 7)
	}
	stored, err := loadStoredResearch(ctx, nk, request.LogicalSessionID)
	if err != nil {
		return "", runtime.NewError("research session not found", 5)
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil || stored.record.ExternalMatchID == "" || stored.record.ControlAuthorizationSetID != request.AuthorizationSetID ||
		engine.View().RosterVersion != claim.SessionRosterVersion {
		return "", runtime.NewError("signed research completion epoch rejected", 7)
	}
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix(), m.config.authorityKeyID)
	version, err := createStoredResearchControl(ctx, nk, command, m.config.controlIssuerKeys)
	if err != nil {
		return "", runtime.NewError("research control command conflict", 10)
	}
	return m.executePendingResearchControlSignal(ctx, nk, versionedStoredResearchControl{record: command, version: version})
}

func (m *moduleRuntime) executePendingResearchControlSignal(ctx context.Context, nk runtime.NakamaModule,
	command versionedStoredResearchControl) (string, error) {
	stored, err := loadStoredResearch(ctx, nk, command.record.SessionID)
	if err != nil || stored.record.ExternalMatchID == "" {
		return "", runtime.NewError("research runtime is absent; signed resume required", 9)
	}
	m.researchControlTestFailpoint(command.record.Operation+"_before_signal", command.record.CommandID)
	signal, _ := json.Marshal(researchSignal{
		Schema: "trnm.nakama.research-session.signal.v1", Action: command.record.Operation,
		LogicalSessionID: command.record.SessionID, RuntimeGeneration: stored.record.RuntimeGeneration,
		OperatorToken: m.config.operatorToken, ControlCommandID: command.record.CommandID,
	})
	response, signalErr := nk.MatchSignal(ctx, stored.record.ExternalMatchID, string(signal))
	if signalErr == nil && signalError(response) != "" {
		signalErr = errors.New("research control signal rejected")
	}
	reloaded, loadErr := loadStoredResearchControl(ctx, nk, command.record.CommandID, m.config.controlIssuerKeys)
	if loadErr == nil && sameResearchControlReservation(command.record, reloaded.record) &&
		reloaded.record.Status == researchControlStatusApplied {
		return m.verifiedResearchControlResponse(ctx, nk, reloaded)
	}
	if signalErr != nil {
		return "", runtime.NewError("research control signal failed", 14)
	}
	return "", runtime.NewError("research control operation lacks an atomic durable receipt", 13)
}

// researchControlTestFailpoint is inert unless the explicitly test-named
// environment path is configured. The Compose black box writes an exact
// "stage:command_id" trigger, waits for the reached marker, then sends SIGKILL.
// Changing/removing the trigger releases the loop during test cleanup.
func (m *moduleRuntime) researchControlTestFailpoint(stage, commandID string) {
	path := m.config.controlTestHook
	if path == "" {
		return
	}
	trigger := stage + ":" + commandID
	raw, err := os.ReadFile(path)
	if err != nil || strings.TrimSpace(string(raw)) != trigger {
		return
	}
	_ = os.WriteFile(path+".reached", []byte(trigger+"\n"), 0o666)
	for {
		time.Sleep(20 * time.Millisecond)
		raw, err = os.ReadFile(path)
		if err != nil || strings.TrimSpace(string(raw)) != trigger {
			return
		}
	}
}
