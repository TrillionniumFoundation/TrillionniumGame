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
	researchControlStorageCollection  = "trnm_research_control_v2"
	researchControlStorageSchema      = "trnm.nakama.stored-research-control-command.v2"
	researchControlResultSchema       = "trnm.nakama.research-control.result.v2"
	researchControlResponseSealSchema = "trnm.nakama.research-control.response-seal.v2"
	researchControlStatusPending      = "pending"
	researchControlStatusApplied      = "applied"
)

var errResearchControlNotFound = errors.New("research control command not found")

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
	record  storedResearchControlCommand
	version string
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

func newStoredResearchControlCommand(control researchcontract.SignedResearchControlV2, canonicalRequest []byte, acceptedAt int64) storedResearchControlCommand {
	requestDigest := sha256.Sum256(canonicalRequest)
	claim := control.Claim
	return storedResearchControlCommand{
		Schema: researchControlStorageSchema, CommandID: claim.CommandID, Operation: claim.Operation,
		TargetRPC: claim.TargetRPC, SessionID: claim.SessionID, SessionRosterVersion: claim.SessionRosterVersion,
		AuthorizationSetID: claim.AuthorizationSetID, PayloadHash: claim.PayloadHash,
		RequestBodyBase64: base64.StdEncoding.EncodeToString(canonicalRequest), RequestSHA256: hex.EncodeToString(requestDigest[:]),
		AcceptedAtUnix: acceptedAt, Status: researchControlStatusPending,
	}
}

func (record *storedResearchControlCommand) applyResult(result any, appliedAt time.Time,
	authorityKeyID string, authorityPrivateKey ed25519.PrivateKey) error {
	if err := researchcontract.ValidateKeyID(authorityKeyID); err != nil {
		return err
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
	record.Status = researchControlStatusApplied
	record.ResponseBodyBase64 = base64.StdEncoding.EncodeToString(wrapper)
	record.ResponseSHA256 = hex.EncodeToString(digest[:])
	record.ResponseAuthorityKeyID = authorityKeyID
	record.AppliedAtUnix = &unix
	anchor, err := record.responseSealBytes()
	if err != nil {
		return err
	}
	record.ResponseSignatureBase64 = base64.StdEncoding.EncodeToString(ed25519.Sign(authorityPrivateKey, anchor))
	_, err = record.response()
	return err
}

func (record storedResearchControlCommand) responseSealBytes() ([]byte, error) {
	if record.Status != researchControlStatusApplied || record.AppliedAtUnix == nil ||
		researchcontract.ValidateKeyID(record.ResponseAuthorityKeyID) != nil {
		return nil, errors.New("research control response seal identity is invalid")
	}
	return json.Marshal(researchControlResponseSealV2{
		Schema: researchControlResponseSealSchema, CommandID: record.CommandID, Operation: record.Operation,
		TargetRPC: record.TargetRPC, SessionID: record.SessionID, SessionRosterVersion: record.SessionRosterVersion,
		AuthorizationSetID: record.AuthorizationSetID, PayloadHash: record.PayloadHash,
		RequestSHA256: record.RequestSHA256, ResponseSHA256: record.ResponseSHA256,
		AcceptedAtUnix: record.AcceptedAtUnix, AppliedAtUnix: *record.AppliedAtUnix,
		AuthorityKeyID: record.ResponseAuthorityKeyID,
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
	var wrapper researchControlResultV2
	if decodeJSONStrict(string(body), &wrapper) != nil || wrapper.Schema != researchControlResultSchema ||
		wrapper.CommandID != record.CommandID || wrapper.Operation != record.Operation || wrapper.TargetRPC != record.TargetRPC || len(wrapper.Result) == 0 {
		return "", errors.New("research control response identity differs")
	}
	switch record.Operation {
	case researchcontract.ResearchControlOperationCreate, researchcontract.ResearchControlOperationResume, researchcontract.ResearchControlOperationReplace:
		var result researchRuntimeResponse
		if decodeJSONStrict(string(wrapper.Result), &result) != nil || result.Schema != "trnm.nakama.research-session.match-runtime.v1" || result.LogicalSessionID != record.SessionID {
			return "", errors.New("research control runtime result is invalid")
		}
		if result.RosterVersion != record.SessionRosterVersion ||
			!researchControlSafePositive(result.RuntimeGeneration) || !researchControlSafePositive(result.SessionVersion) ||
			!researchControlSafePositive(result.RosterVersion) || result.ExternalMatchID == "" ||
			result.RosterRoot.Validate() != nil || !validResearchControlRuntimeStatus(result.Status) {
			return "", errors.New("research control runtime result roster differs")
		}
	case researchcontract.ResearchControlOperationComplete:
		var result researchEvidenceResponse
		if decodeJSONStrict(string(wrapper.Result), &result) != nil || result.Schema != "trnm.nakama.research-session.evidence.v1" || result.LogicalSessionID != record.SessionID {
			return "", errors.New("research control completion result is invalid")
		}
		if result.Completion.RosterVersion != record.SessionRosterVersion ||
			!researchControlSafePositive(result.RuntimeGeneration) || result.ExternalMatchID == "" {
			return "", errors.New("research control completion result roster differs")
		}
	default:
		return "", errors.New("research control response operation is invalid")
	}
	reencoded, err := json.Marshal(wrapper)
	if err != nil || !bytes.Equal(reencoded, body) {
		return "", errors.New("research control response is not canonical JSON")
	}
	return string(body), nil
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
	raw, err := command.record.response()
	if err != nil {
		return "", err
	}
	stored, err := loadStoredResearch(ctx, nk, command.record.SessionID)
	if err != nil {
		return "", errors.New("research control response has no durable session")
	}
	engine, err := m.restoreStoredResearch(stored.record)
	if err != nil {
		return "", errors.New("research control response session failed verification")
	}
	if command.record.ResponseAuthorityKeyID != engine.AuthorityKeyID() {
		return "", errors.New("research control response authority differs from durable session")
	}
	anchor, err := command.record.responseSealBytes()
	if err != nil {
		return "", err
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(command.record.ResponseSignatureBase64)
	if err != nil || !ed25519.Verify(engine.AuthorityPublicKey(), anchor, signature) {
		return "", errors.New("research control response authority signature failed verification")
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
		if !ok || !reflect.DeepEqual(result.Completion, *completion) ||
			result.AuthorityPublicKey != base64.StdEncoding.EncodeToString(engine.AuthorityPublicKey()) {
			return "", errors.New("research control completion result differs from durable signed evidence")
		}
	default:
		return "", errors.New("research control response operation is invalid")
	}
	return raw, nil
}

func (m *moduleRuntime) researchAuthoritySigningKey(keyID string) (ed25519.PrivateKey, error) {
	if key := m.config.authorityPrivateKeys[keyID]; len(key) == ed25519.PrivateKeySize {
		return key, nil
	}
	if keyID == m.config.authorityKeyID && len(m.config.authorityPrivateKey) == ed25519.PrivateKeySize {
		return m.config.authorityPrivateKey, nil
	}
	return nil, errors.New("research session authority signing key is not configured")
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
		if record.ResponseBodyBase64 != "" || record.ResponseSHA256 != "" || record.AppliedAtUnix != nil {
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
	var record storedResearchControlCommand
	if decodeJSONStrict(objects[0].Value, &record) != nil || record.CommandID != commandID {
		return versionedStoredResearchControl{}, errors.New("stored research control cannot be decoded")
	}
	if err := validateStoredResearchControlCommand(record, trusted); err != nil {
		return versionedStoredResearchControl{}, err
	}
	return versionedStoredResearchControl{record: record, version: objects[0].Version}, nil
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

func updateStoredResearchControl(ctx context.Context, nk storageGateway, record storedResearchControlCommand, version string, trusted map[string]ed25519.PublicKey) (string, error) {
	if version == "" || version == "*" {
		return "", errors.New("concrete research control storage version required")
	}
	return writeStoredResearchControl(ctx, nk, record, version, trusted)
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
	command storedResearchControlCommand, commandVersion string, trusted map[string]ed25519.PublicKey) (string, string, error) {
	if sessionVersion == "" || sessionVersion == "*" || commandVersion == "" || commandVersion == "*" {
		return "", "", errors.New("concrete session and control storage versions required")
	}
	return writeStoredResearchWithControl(ctx, nk, session, sessionVersion, command, commandVersion, trusted)
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
	authorityPrivateKey, err := m.researchAuthoritySigningKey(currentEngine.AuthorityKeyID())
	if err != nil {
		return "", err
	}
	if err := updated.applyResult(researchRuntimeFor(current.record, currentEngine.View(), current.record.ExternalMatchID),
		time.Now().UTC(), currentEngine.AuthorityKeyID(), authorityPrivateKey); err != nil {
		return "", err
	}
	if _, err := updateStoredResearchControl(ctx, nk, updated, command.version, m.config.controlIssuerKeys); err != nil {
		reloaded, loadErr := loadStoredResearchControl(ctx, nk, command.record.CommandID, m.config.controlIssuerKeys)
		if loadErr == nil && exactResearchControlRequest(reloaded.record, mustDecodeBase64(command.record.RequestBodyBase64)) && reloaded.record.Status == researchControlStatusApplied {
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
	if err := m.config.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
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
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix())
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
	if err := m.config.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
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
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix())
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
	if err := m.config.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
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
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix())
	version, err := createStoredResearchControl(ctx, nk, command, m.config.controlIssuerKeys)
	if err != nil {
		return "", runtime.NewError("research control command conflict", 10)
	}
	return m.executePendingResearchControlSignal(ctx, nk, versionedStoredResearchControl{record: command, version: version})
}

func (m *moduleRuntime) rpcResearchCompleteV2(ctx context.Context, _ runtime.Logger, _ *sql.DB, nk runtime.NakamaModule, payload string) (string, error) {
	if err := m.config.ready(); err != nil || len(m.config.controlIssuerKeys) == 0 {
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
	command := newStoredResearchControlCommand(request.Control, canonicalRequest, now.Unix())
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
	if loadErr == nil && exactResearchControlRequest(reloaded.record, mustDecodeBase64(command.record.RequestBodyBase64)) && reloaded.record.Status == researchControlStatusApplied {
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
