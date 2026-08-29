package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"log"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

func main() {
	output := required("TRNM_TLS_FIXTURE_OUTPUT_DIR")
	if err := os.MkdirAll(output, 0o700); err != nil {
		log.Fatal(err)
	}
	now := time.Now().UTC().Add(-time.Minute)
	caPublic, caPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		log.Fatal(err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber:          serial(),
		Subject:               pkix.Name{CommonName: "Trillionnium isolated fault-lab CA"},
		NotBefore:             now,
		NotAfter:              now.Add(48 * time.Hour),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLenZero:        true,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, caPublic, caPrivate)
	if err != nil {
		log.Fatal(err)
	}
	ca, err := x509.ParseCertificate(caDER)
	if err != nil {
		log.Fatal(err)
	}
	if err := writeAtomic(filepath.Join(output, "ca.pem"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), 0o644); err != nil {
		log.Fatal(err)
	}

	if err := createServer(output, "world", []string{"world-fixture", "localhost"}, ca, caPrivate, now); err != nil {
		log.Fatal(err)
	}
	if err := createServer(output, "proxy", []string{"response-drop-proxy", "localhost"}, ca, caPrivate, now); err != nil {
		log.Fatal(err)
	}
	directory, err := os.Open(output)
	if err != nil {
		log.Fatal(err)
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil {
		log.Fatal(err)
	}
	log.Printf("ephemeral fault-lab CA and server certificates written to %s", output)
}

func createServer(output, prefix string, names []string, ca *x509.Certificate, caPrivate ed25519.PrivateKey, now time.Time) error {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	template := &x509.Certificate{
		SerialNumber: serial(),
		Subject:      pkix.Name{CommonName: names[0]},
		DNSNames:     append([]string(nil), names...),
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")},
		NotBefore:    now,
		NotAfter:     now.Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, ca, publicKey, caPrivate)
	if err != nil {
		return err
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		return err
	}
	if err := writeAtomic(filepath.Join(output, prefix+".pem"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER}), 0o644); err != nil {
		return err
	}
	return writeAtomic(filepath.Join(output, prefix+"-key.pem"), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER}), 0o600)
}

func writeAtomic(path string, payload []byte, mode os.FileMode) error {
	if len(payload) == 0 {
		return errors.New("refusing to write empty TLS fixture")
	}
	directory := filepath.Dir(path)
	file, err := os.CreateTemp(directory, ".tls-pending-*")
	if err != nil {
		return err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	if err := file.Chmod(mode); err != nil {
		file.Close()
		return err
	}
	if _, err := file.Write(payload); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

func serial() *big.Int {
	limit := new(big.Int).Lsh(big.NewInt(1), 128)
	value, err := rand.Int(rand.Reader, limit)
	if err != nil || value.Sign() == 0 {
		return big.NewInt(time.Now().UnixNano())
	}
	return value
}

func required(name string) string {
	value := os.Getenv(name)
	if value == "" {
		log.Fatalf("%s is required", name)
	}
	return value
}
