// trnm-fixture-signer creates ephemeral keys and signs test contract values.
// It deliberately reads private key material from an environment variable and
// never writes a key file. Callers are responsible for keeping its JSON output
// out of version control.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/contract"
)

const defaultPrivateKeyEnv = "TRNM_FIXTURE_PRIVATE_KEY"

func main() {
	if err := run(os.Args[1:], os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "trnm-fixture-signer:", err)
		os.Exit(2)
	}
}

func run(args []string, stdin io.Reader, stdout io.Writer) error {
	if len(args) == 0 {
		return usageError()
	}
	switch args[0] {
	case "keygen":
		if len(args) != 1 {
			return errors.New("keygen accepts no arguments")
		}
		publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			return fmt.Errorf("generate key: %w", err)
		}
		return writeJSON(stdout, struct {
			Schema            string `json:"schema"`
			PublicKeyBase64   string `json:"public_key_base64"`
			PrivateSeedBase64 string `json:"private_seed_base64"`
		}{
			Schema:            "trnm.fixture.ed25519-key.v1",
			PublicKeyBase64:   base64.StdEncoding.EncodeToString(publicKey),
			PrivateSeedBase64: base64.StdEncoding.EncodeToString(privateKey.Seed()),
		})
	case "sign-authorization":
		flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
		flags.SetOutput(io.Discard)
		keyID := flags.String("issuer-key-id", "", "trusted issuer key identifier")
		keyEnv := flags.String("private-key-env", defaultPrivateKeyEnv, "environment variable containing a base64/hex Ed25519 seed")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if flags.NArg() != 0 || strings.TrimSpace(*keyID) == "" {
			return errors.New("sign-authorization requires -issuer-key-id and a claim JSON object on stdin")
		}
		privateKey, err := privateKeyFromEnvironment(*keyEnv)
		if err != nil {
			return err
		}
		var claim contract.AuthorizationClaim
		if err := readJSON(stdin, &claim); err != nil {
			return fmt.Errorf("read authorization claim: %w", err)
		}
		signed, err := contract.SignAuthorization(claim, *keyID, privateKey)
		if err != nil {
			return fmt.Errorf("sign authorization: %w", err)
		}
		return writeJSON(stdout, signed)
	case "sign-command":
		flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
		flags.SetOutput(io.Discard)
		keyEnv := flags.String("private-key-env", defaultPrivateKeyEnv, "environment variable containing a base64/hex Ed25519 seed")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if flags.NArg() != 0 {
			return errors.New("sign-command accepts a command JSON object on stdin")
		}
		privateKey, err := privateKeyFromEnvironment(*keyEnv)
		if err != nil {
			return err
		}
		var command contract.CommandEnvelope
		if err := readJSON(stdin, &command); err != nil {
			return fmt.Errorf("read command: %w", err)
		}
		signed, err := contract.SignCommand(command, privateKey)
		if err != nil {
			return fmt.Errorf("sign command: %w", err)
		}
		return writeJSON(stdout, signed)
	default:
		return usageError()
	}
}

func usageError() error {
	return errors.New("usage: trnm-fixture-signer keygen | sign-authorization -issuer-key-id ID | sign-command")
}

func privateKeyFromEnvironment(name string) (ed25519.PrivateKey, error) {
	if strings.TrimSpace(name) == "" {
		return nil, errors.New("private key environment variable name is empty")
	}
	value, ok := os.LookupEnv(name)
	if !ok || strings.TrimSpace(value) == "" {
		return nil, fmt.Errorf("environment variable %s is empty", name)
	}
	raw, err := decodeKey(value)
	if err != nil {
		return nil, fmt.Errorf("environment variable %s is not base64 or hexadecimal", name)
	}
	switch len(raw) {
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(raw), nil
	case ed25519.PrivateKeySize:
		key := ed25519.PrivateKey(append([]byte(nil), raw...))
		derived := ed25519.NewKeyFromSeed(key.Seed())
		if !ed25519.PublicKey(key[ed25519.SeedSize:]).Equal(derived.Public()) {
			return nil, errors.New("Ed25519 private key has an inconsistent public suffix")
		}
		return key, nil
	default:
		return nil, fmt.Errorf("environment variable %s must contain a 32-byte seed or 64-byte private key", name)
	}
}

func decodeKey(value string) ([]byte, error) {
	value = strings.TrimSpace(value)
	if b, err := hex.DecodeString(value); err == nil {
		return b, nil
	}
	if b, err := base64.StdEncoding.DecodeString(value); err == nil {
		return b, nil
	}
	if b, err := base64.RawStdEncoding.DecodeString(value); err == nil {
		return b, nil
	}
	if b, err := base64.RawURLEncoding.DecodeString(value); err == nil {
		return b, nil
	}
	return nil, errors.New("unsupported key encoding")
}

func readJSON(reader io.Reader, dst any) error {
	decoder := json.NewDecoder(reader)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(dst); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("multiple JSON values are not allowed")
		}
		return err
	}
	return nil
}

func writeJSON(writer io.Writer, value any) error {
	encoder := json.NewEncoder(writer)
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	return encoder.Encode(value)
}
