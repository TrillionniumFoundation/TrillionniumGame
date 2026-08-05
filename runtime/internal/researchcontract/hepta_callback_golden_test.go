package researchcontract

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	goruntime "runtime"
	"testing"
)

type heptaCallbackGolden struct {
	Schema        string `json:"schema"`
	SourceFixture struct {
		Schema string `json:"schema"`
		SHA256 string `json:"sha256"`
	} `json:"source_fixture"`
	Issuer struct {
		KeyID           string `json:"key_id"`
		SeedHex         string `json:"seed_hex"`
		PublicKeyBase64 string `json:"public_key_base64"`
	} `json:"issuer"`
	AuthorizationConsumptionReceipt struct {
		SigningFrameHex string                                     `json:"signing_frame_hex"`
		Value           SignedAuthorizationSetConsumptionReceiptV1 `json:"value"`
	} `json:"authorization_consumption_receipt"`
	NakamaCompletionReceipt struct {
		SigningFrameHex string                         `json:"signing_frame_hex"`
		Value           SignedHeptaCompletionReceiptV1 `json:"value"`
	} `json:"nakama_completion_receipt"`
}

func loadHeptaCallbackGolden(t *testing.T) heptaCallbackGolden {
	t.Helper()
	_, source, _, ok := goruntime.Caller(0)
	if !ok {
		t.Fatal("cannot locate golden test source")
	}
	path := filepath.Join(filepath.Dir(source), "..", "..", "..", "contracts", "hepta-callback-golden-vectors.json")
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture heptaCallbackGolden
	if err := json.Unmarshal(encoded, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestExactHeptaSignedACKCrossLanguageGolden(t *testing.T) {
	fixture := loadHeptaCallbackGolden(t)
	if fixture.Schema != "trnm.nakama.hepta_callback.golden_vectors.v1" ||
		fixture.SourceFixture.Schema != "hepta.paper_raid.golden_vectors.v2" ||
		fixture.SourceFixture.SHA256 != "309584cc21a7169473a7bd37b93528edce4a3b248b313238cd81f6a7c3cad19d" {
		t.Fatal("vendored Hepta fixture identity differs from the frozen source")
	}
	seed, err := hex.DecodeString(fixture.Issuer.SeedHex)
	if err != nil || len(seed) != ed25519.SeedSize {
		t.Fatal("fixture issuer seed is invalid")
	}
	private := ed25519.NewKeyFromSeed(seed)
	public := private.Public().(ed25519.PublicKey)
	if base64.StdEncoding.EncodeToString(public) != fixture.Issuer.PublicKeyBase64 {
		t.Fatal("fixture issuer seed and public key differ")
	}
	trusted := map[string]ed25519.PublicKey{fixture.Issuer.KeyID: public}

	consumeBytes, err := fixture.AuthorizationConsumptionReceipt.Value.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(consumeBytes) != fixture.AuthorizationConsumptionReceipt.SigningFrameHex {
		t.Fatal("Go authorization consumption ACK frame differs from frozen Hepta bytes")
	}
	if err := fixture.AuthorizationConsumptionReceipt.Value.Verify(trusted); err != nil {
		t.Fatal(err)
	}
	consumeSignature, _ := base64.StdEncoding.Strict().DecodeString(fixture.AuthorizationConsumptionReceipt.Value.Signature)
	if !bytes.Equal(ed25519.Sign(private, consumeBytes), consumeSignature) {
		t.Fatal("authorization consumption ACK signature is not deterministic across Hepta and Nakama")
	}

	completionBytes, err := fixture.NakamaCompletionReceipt.Value.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(completionBytes) != fixture.NakamaCompletionReceipt.SigningFrameHex {
		t.Fatal("Go completion ACK frame differs from frozen Hepta bytes")
	}
	if err := fixture.NakamaCompletionReceipt.Value.Verify(trusted); err != nil {
		t.Fatal(err)
	}
	completionSignature, _ := base64.StdEncoding.Strict().DecodeString(fixture.NakamaCompletionReceipt.Value.Signature)
	if !bytes.Equal(ed25519.Sign(private, completionBytes), completionSignature) {
		t.Fatal("completion ACK signature is not deterministic across Hepta and Nakama")
	}
}
