package main

import (
	"context"
	"encoding/pem"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/worldcommand"
)

func newTLSWorldTestServer(t *testing.T, handler http.Handler) (*httptest.Server, []byte) {
	t.Helper()
	server := httptest.NewUnstartedServer(handler)
	server.EnableHTTP2 = false
	server.StartTLS()
	t.Cleanup(server.Close)
	certificate := server.Certificate()
	if certificate == nil || len(certificate.Raw) == 0 {
		t.Fatal("test server has no certificate")
	}
	return server, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificate.Raw})
}

func testWorldHTTPConfig(t *testing.T, rawURL string, ca []byte) worldCommandRuntimeConfig {
	t.Helper()
	parsed, err := url.Parse(rawURL)
	if err != nil {
		t.Fatal(err)
	}
	return worldCommandRuntimeConfig{
		profile:          worldProfileTarget,
		endpoint:         parsed,
		bearerToken:      strings.Repeat("t", 32),
		caPEM:            ca,
		timeout:          3 * time.Second,
		maxResponseBytes: 1024,
	}
}

func TestWorldHTTPSExecutorPostsExactCanonicalBytes(t *testing.T) {
	requestBody := []byte(`{"command":"hold"}`)
	server, ca := newTLSWorldTestServer(t, http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.Header.Get("Authorization") != "Bearer "+strings.Repeat("t", 32) {
			t.Errorf("unexpected request identity")
		}
		if request.Header.Get("Content-Type") != "application/json" || request.Header.Get("Accept") != "application/json" {
			t.Errorf("unexpected content negotiation")
		}
		body, err := io.ReadAll(request.Body)
		if err != nil {
			t.Error(err)
		}
		if string(body) != string(requestBody) {
			t.Errorf("request bytes changed: %q", body)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":"domain_rejected"}`))
	}))
	executor, err := newWorldHTTPSExecutor(testWorldHTTPConfig(t, server.URL, ca))
	if err != nil {
		t.Fatal(err)
	}
	response, err := executor.Execute(context.Background(), requestBody)
	if err != nil {
		t.Fatal(err)
	}
	if string(response) != `{"code":"domain_rejected"}` {
		t.Fatalf("unexpected response %q", response)
	}
}

func TestWorldHTTPSExecutorDoesNotFollowRedirects(t *testing.T) {
	server, ca := newTLSWorldTestServer(t, http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		http.Redirect(w, request, "/other", http.StatusTemporaryRedirect)
	}))
	executor, err := newWorldHTTPSExecutor(testWorldHTTPConfig(t, server.URL, ca))
	if err != nil {
		t.Fatal(err)
	}
	_, err = executor.Execute(context.Background(), []byte(`{"command":"hold"}`))
	var execution *worldcommand.ExecutionError
	if !errors.As(err, &execution) || execution.Kind != worldcommand.FailureTransport {
		t.Fatalf("redirect was not rejected as transport failure: %v", err)
	}
}

func TestWorldHTTPSExecutorClassifiesResponseLossAsAmbiguous(t *testing.T) {
	server, ca := newTLSWorldTestServer(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hijacker, ok := w.(http.Hijacker)
		if !ok {
			t.Error("test response writer cannot hijack")
			return
		}
		connection, _, err := hijacker.Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		_ = connection.Close()
	}))
	executor, err := newWorldHTTPSExecutor(testWorldHTTPConfig(t, server.URL, ca))
	if err != nil {
		t.Fatal(err)
	}
	_, err = executor.Execute(context.Background(), []byte(`{"command":"hold"}`))
	var execution *worldcommand.ExecutionError
	if !errors.As(err, &execution) || execution.Kind != worldcommand.FailureAmbiguousCommit || !execution.Retryable {
		t.Fatalf("response loss was not classified as ambiguous: %v", err)
	}
}

func TestWorldHTTPSExecutorRejectsNonJSONAndOversizedBodies(t *testing.T) {
	for name, handler := range map[string]http.Handler{
		"content type": http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/plain")
			_, _ = w.Write([]byte("not json"))
		}),
		"oversized": http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"payload":"` + strings.Repeat("x", 2048) + `"}`))
		}),
	} {
		t.Run(name, func(t *testing.T) {
			server, ca := newTLSWorldTestServer(t, handler)
			config := testWorldHTTPConfig(t, server.URL, ca)
			config.maxResponseBytes = 128
			executor, err := newWorldHTTPSExecutor(config)
			if err != nil {
				t.Fatal(err)
			}
			_, err = executor.Execute(context.Background(), []byte(`{"command":"hold"}`))
			var execution *worldcommand.ExecutionError
			if !errors.As(err, &execution) || execution.Kind != worldcommand.FailureInvalidResult || execution.Retryable {
				t.Fatalf("invalid response was not rejected fail closed: %v", err)
			}
		})
	}
}
