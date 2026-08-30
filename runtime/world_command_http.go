package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/worldcommand"
)

type worldHTTPSExecutor struct {
	endpoint         string
	bearerToken      string
	maxResponseBytes int64
	client           *http.Client
}

func newWorldHTTPSExecutor(config worldCommandRuntimeConfig) (*worldHTTPSExecutor, error) {
	if err := config.ready(); err != nil {
		return nil, err
	}
	if config.profile != worldProfileTarget || config.endpoint == nil {
		return nil, errors.New("World HTTPS executor requires the target profile")
	}
	caPEM, err := config.caCertificatePEM()
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("World CA certificate contains no valid PEM certificate")
	}
	transport := &http.Transport{
		Proxy:                 nil,
		ForceAttemptHTTP2:     true,
		TLSHandshakeTimeout:   config.timeout,
		ResponseHeaderTimeout: config.timeout,
		IdleConnTimeout:       30 * time.Second,
		MaxIdleConns:          8,
		MaxIdleConnsPerHost:   4,
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS13,
			RootCAs:    roots,
		},
	}
	client := &http.Client{
		Transport: transport,
		Timeout:   config.timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return &worldHTTPSExecutor{
		endpoint:         config.endpoint.String(),
		bearerToken:      config.bearerToken,
		maxResponseBytes: config.maxResponseBytes,
		client:           client,
	}, nil
}

func (e *worldHTTPSExecutor) Execute(ctx context.Context, canonicalRequest []byte) ([]byte, error) {
	if e == nil || e.client == nil || len(canonicalRequest) == 0 {
		return nil, &worldcommand.ExecutionError{Kind: worldcommand.FailureTransport, Retryable: true, Err: errors.New("World HTTPS executor is not configured")}
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, e.endpoint, bytes.NewReader(canonicalRequest))
	if err != nil {
		return nil, &worldcommand.ExecutionError{Kind: worldcommand.FailureTransport, Retryable: true, Err: err}
	}
	sum := sha256.Sum256(canonicalRequest)
	request.Header.Set("Authorization", "Bearer "+e.bearerToken)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "trillionnium-game-world-transition-v1")
	request.Header.Set("X-Trnm-Canonical-Request-Sha256", hex.EncodeToString(sum[:]))

	response, err := e.client.Do(request)
	if err != nil {
		kind := worldcommand.FailureAmbiguousCommit
		if errors.Is(err, context.Canceled) || errors.Is(ctx.Err(), context.Canceled) {
			kind = worldcommand.FailureCancelled
		}
		return nil, &worldcommand.ExecutionError{Kind: kind, Retryable: true, Err: err}
	}
	defer response.Body.Close()

	if response.StatusCode < 200 || response.StatusCode >= 300 {
		kind := worldcommand.FailureTransport
		retryable := response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500
		return nil, &worldcommand.ExecutionError{
			Kind:      kind,
			Retryable: retryable,
			Err:       fmt.Errorf("World HTTPS endpoint returned status %d", response.StatusCode),
		}
	}
	mediaType, _, parseErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if parseErr != nil || !strings.EqualFold(mediaType, "application/json") {
		return nil, &worldcommand.ExecutionError{
			Kind:      worldcommand.FailureInvalidResult,
			Retryable: false,
			Err:       errors.New("World HTTPS response Content-Type is not application/json"),
		}
	}
	limited := io.LimitReader(response.Body, e.maxResponseBytes+1)
	payload, readErr := io.ReadAll(limited)
	if readErr != nil {
		return nil, &worldcommand.ExecutionError{Kind: worldcommand.FailureAmbiguousCommit, Retryable: true, Err: readErr}
	}
	if int64(len(payload)) > e.maxResponseBytes {
		return nil, &worldcommand.ExecutionError{Kind: worldcommand.FailureInvalidResult, Retryable: false, Err: errors.New("World HTTPS response exceeds the configured limit")}
	}
	if len(payload) == 0 {
		return nil, &worldcommand.ExecutionError{Kind: worldcommand.FailureAmbiguousCommit, Retryable: true, Err: errors.New("World HTTPS response body is empty")}
	}
	return payload, nil
}
