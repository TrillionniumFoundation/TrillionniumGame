package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcontract"
)

const fixtureNow int64 = 1_800_000_000

type createRequest struct {
	Schema             string                                   `json:"schema"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Authorizations     []researchcontract.SignedAuthorization   `json:"authorizations"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}
type resumeRequest struct {
	Schema             string                                   `json:"schema"`
	LogicalSessionID   string                                   `json:"logical_session_id"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}
type replaceRequest struct {
	Schema             string                                   `json:"schema"`
	LogicalSessionID   string                                   `json:"logical_session_id"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Authorizations     []researchcontract.SignedAuthorization   `json:"authorizations"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}
type completeRequest struct {
	Schema             string                                   `json:"schema"`
	LogicalSessionID   string                                   `json:"logical_session_id"`
	AuthorizationSetID string                                   `json:"authorization_set_id"`
	Facts              researchcontract.TerminalFacts           `json:"facts"`
	Control            researchcontract.SignedResearchControlV2 `json:"control"`
}

type fixtureKey struct {
	SeedHex         string `json:"seed_hex"`
	PublicKeyBase64 string `json:"public_key_base64"`
}
type vector struct {
	Operation                  string          `json:"operation"`
	TargetRPC                  string          `json:"target_rpc"`
	BusinessFrameBase64        string          `json:"business_frame_base64"`
	PayloadHash                string          `json:"payload_hash"`
	ControlSigningFrameBase64  string          `json:"control_signing_frame_base64"`
	CanonicalRequestBodyBase64 string          `json:"canonical_request_body_base64"`
	Request                    json.RawMessage `json:"request"`
}
type fixture struct {
	Schema        string                `json:"schema"`
	FixtureNotice string                `json:"fixture_notice"`
	Keys          map[string]fixtureKey `json:"keys"`
	Vectors       []vector              `json:"vectors"`
}

func deterministicKey(start byte) (fixtureKey, ed25519.PrivateKey) {
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = start + byte(index)
	}
	privateKey := ed25519.NewKeyFromSeed(seed)
	return fixtureKey{SeedHex: hex.EncodeToString(seed), PublicKeyBase64: base64.StdEncoding.EncodeToString(privateKey.Public().(ed25519.PublicKey))}, privateKey
}

func makeAuthorizations(version uint64, setOffset int, issuer ed25519.PrivateKey, agents []ed25519.PrivateKey) []researchcontract.SignedAuthorization {
	claims := make([]researchcontract.AuthorizationClaim, 3)
	for index := range claims {
		agentKeyVersion := uint64(1)
		if version > 1 && index == 2 {
			agentKeyVersion = version
		}
		claims[index] = researchcontract.AuthorizationClaim{
			Schema:          researchcontract.AuthorizationSchema,
			AuthorizationID: fmt.Sprintf("10000000-0000-4000-8000-%012d", setOffset+index+1),
			SessionID:       "research-control-golden-001", TeamID: "30000000-0000-4000-8000-000000000001",
			PaperProjectID: "40000000-0000-4000-8000-000000000001", ChallengeID: "50000000-0000-4000-8000-000000000001",
			AgentID: fmt.Sprintf("agent-golden-%d", index+1), AgentDID: fmt.Sprintf("did:trnm:research-agent-%d", index+1),
			AgentKeyID: fmt.Sprintf("agent-key-golden-%d-v%d", index+1, agentKeyVersion), AgentPublicKey: agents[index].Public().(ed25519.PublicKey),
			SubjectUserID: fmt.Sprintf("00000000-0000-4000-8000-%012d", index+1), ParticipantSlot: uint32(index + 1),
			Role: fmt.Sprintf("paper-role-%d", index+1), RosterVersion: version,
			RosterRoot: researchcontract.NewDigest([]byte("placeholder")), RulesetHash: researchcontract.NewDigest([]byte("paper-raid-ruleset:control:v2")),
			ChallengeSnapshotHash: researchcontract.NewDigest([]byte("paper-raid-challenge:control:v2")),
			IssuedAtUnix:          fixtureNow, ExpiresAtUnix: fixtureNow + 3600,
		}
	}
	provisional := make([]researchcontract.SignedAuthorization, len(claims))
	for index := range claims {
		provisional[index].Claim = claims[index]
	}
	root, err := researchcontract.RosterRoot(claims[0].SessionID, claims[0].TeamID, claims[0].PaperProjectID,
		version, researchcontract.RosterEntries(provisional))
	if err != nil {
		panic(err)
	}
	result := make([]researchcontract.SignedAuthorization, len(claims))
	for index := range claims {
		claims[index].RosterRoot = root
		result[index], err = researchcontract.SignAuthorization(claims[index], "authorization-issuer-golden-v1", issuer)
		if err != nil {
			panic(err)
		}
	}
	return result
}

func makeVector(operation, commandID, sessionID, setID string, rosterVersion uint64, business []byte,
	request any, controlPrivate ed25519.PrivateKey) vector {
	target, err := researchcontract.ResearchControlTargetRPC(operation)
	if err != nil {
		panic(err)
	}
	control, err := researchcontract.SignResearchControlV2(researchcontract.ResearchControlClaimV2{
		Schema: researchcontract.ResearchControlClaimSchemaV2, CommandID: commandID, Operation: operation, TargetRPC: target,
		SessionID: sessionID, SessionRosterVersion: rosterVersion, AuthorizationSetID: setID,
		PayloadHash: researchcontract.NewDigest(business), Audience: researchcontract.ResearchControlAudienceV2,
		IssuedAtUnix: fixtureNow, ExpiresAtUnix: fixtureNow + 120, IssuerKeyID: "hepta-control-golden-v2",
	}, controlPrivate)
	if err != nil {
		panic(err)
	}
	switch value := request.(type) {
	case *createRequest:
		value.Control = control
	case *resumeRequest:
		value.Control = control
	case *replaceRequest:
		value.Control = control
	case *completeRequest:
		value.Control = control
	default:
		panic("unsupported fixture request")
	}
	requestBody, err := json.Marshal(request)
	if err != nil {
		panic(err)
	}
	signing, err := researchcontract.ResearchControlSigningBytes(control.Claim)
	if err != nil {
		panic(err)
	}
	return vector{
		Operation: operation, TargetRPC: target, BusinessFrameBase64: base64.StdEncoding.EncodeToString(business),
		PayloadHash: string(researchcontract.NewDigest(business)), ControlSigningFrameBase64: base64.StdEncoding.EncodeToString(signing),
		CanonicalRequestBodyBase64: base64.StdEncoding.EncodeToString(requestBody), Request: requestBody,
	}
}

func main() {
	controlFixtureKey, controlPrivate := deterministicKey(0x20)
	authorizationFixtureKey, authorizationPrivate := deterministicKey(0x00)
	_, agentOne := deterministicKey(0x40)
	_, agentTwo := deterministicKey(0x60)
	_, agentThree := deterministicKey(0x80)
	_, rotatedAgentThree := deterministicKey(0xa0)
	createAuthorizations := makeAuthorizations(1, 0, authorizationPrivate, []ed25519.PrivateKey{agentOne, agentTwo, agentThree})
	replacementAuthorizations := makeAuthorizations(2, 100, authorizationPrivate, []ed25519.PrivateKey{agentOne, agentTwo, rotatedAgentThree})
	const sessionID = "research-control-golden-001"
	const setOne = "20000000-0000-4000-8000-000000000001"
	const setTwo = "20000000-0000-4000-8000-000000000002"

	create := &createRequest{Schema: researchcontract.ResearchControlCreateRequestSchemaV2, AuthorizationSetID: setOne, Authorizations: createAuthorizations}
	createBusiness, _ := researchcontract.ResearchControlCreateBusinessBytesV2(create.Schema, create.AuthorizationSetID, create.Authorizations)
	resume := &resumeRequest{Schema: researchcontract.ResearchControlResumeRequestSchemaV2, LogicalSessionID: sessionID, AuthorizationSetID: setOne}
	resumeBusiness, _ := researchcontract.ResearchControlResumeBusinessBytesV2(resume.Schema, resume.LogicalSessionID, resume.AuthorizationSetID)
	replace := &replaceRequest{Schema: researchcontract.ResearchControlReplaceRequestSchemaV2, LogicalSessionID: sessionID, AuthorizationSetID: setTwo, Authorizations: replacementAuthorizations}
	replaceBusiness, _ := researchcontract.ResearchControlReplaceBusinessBytesV2(replace.Schema, replace.LogicalSessionID, replace.AuthorizationSetID, replace.Authorizations)
	complete := &completeRequest{Schema: researchcontract.ResearchControlCompleteRequestSchemaV2, LogicalSessionID: sessionID, AuthorizationSetID: setTwo,
		Facts: researchcontract.TerminalFacts{ResultCode: "可复现✅", PaperBundleHash: researchcontract.NewDigest([]byte("paper-bundle:control:v2")),
			PaperReleaseCandidateHash: researchcontract.NewDigest([]byte("release-candidate:control:v2")), ContributionLedgerHash: researchcontract.NewDigest([]byte("contribution-ledger:control:v2"))}}
	completeBusiness, _ := researchcontract.ResearchControlCompleteBusinessBytesV2(complete.Schema, complete.LogicalSessionID, complete.AuthorizationSetID, complete.Facts)

	value := fixture{
		Schema:        "trnm.nakama.research_control.golden_vectors.v2",
		FixtureNotice: "TEST ONLY. Deterministic seeds and private keys MUST NEVER be used in production.",
		Keys:          map[string]fixtureKey{"authorization_issuer": authorizationFixtureKey, "control_issuer": controlFixtureKey},
		Vectors: []vector{
			makeVector(researchcontract.ResearchControlOperationCreate, "90000000-0000-4000-8000-000000000001", sessionID, setOne, 1, createBusiness, create, controlPrivate),
			makeVector(researchcontract.ResearchControlOperationResume, "90000000-0000-4000-8000-000000000002", sessionID, setOne, 1, resumeBusiness, resume, controlPrivate),
			makeVector(researchcontract.ResearchControlOperationReplace, "90000000-0000-4000-8000-000000000003", sessionID, setTwo, 2, replaceBusiness, replace, controlPrivate),
			makeVector(researchcontract.ResearchControlOperationComplete, "90000000-0000-4000-8000-000000000004", sessionID, setTwo, 2, completeBusiness, complete, controlPrivate),
		},
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		panic(err)
	}
}
