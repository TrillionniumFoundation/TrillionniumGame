package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const maximumProxyBodyBytes = 16 * 1024 * 1024

type proxyMode string

const (
	modePass      proxyMode = "pass"
	modeDropNext  proxyMode = "drop_next"
	modeDelayNext proxyMode = "delay_next"
)

type proxyState struct {
	mu           sync.Mutex
	mode         proxyMode
	delay        time.Duration
	requests     atomic.Uint64
	upstreamOK   atomic.Uint64
	dropped      atomic.Uint64
	delayed      atomic.Uint64
	forwarded    atomic.Uint64
	controlCalls atomic.Uint64
}

type proxyServer struct {
	upstreamURL string
	worldBearer string
	control     string
	client      *http.Client
	state       proxyState
}

type controlRequest struct {
	Mode        proxyMode `json:"mode"`
	DelayMillis int64     `json:"delay_millis,omitempty"`
}

func main() {
	listen := required("TRNM_RESPONSE_PROXY_LISTEN")
	certificate := required("TRNM_RESPONSE_PROXY_TLS_CERT")
	privateKey := required("TRNM_RESPONSE_PROXY_TLS_KEY")
	upstreamCA, err := os.ReadFile(required("TRNM_RESPONSE_PROXY_UPSTREAM_CA"))
	if err != nil {
		log.Fatal(err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(upstreamCA) {
		log.Fatal("upstream CA contains no valid certificate")
	}
	transport := &http.Transport{
		Proxy:                 nil,
		ForceAttemptHTTP2:     false,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		IdleConnTimeout:       30 * time.Second,
		MaxIdleConns:          8,
		MaxIdleConnsPerHost:   4,
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS13,
			RootCAs:    roots,
		},
	}
	proxy := &proxyServer{
		upstreamURL: required("TRNM_RESPONSE_PROXY_UPSTREAM_URL"),
		worldBearer: required("TRNM_WORLD_FIXTURE_BEARER_TOKEN"),
		control:     required("TRNM_RESPONSE_PROXY_CONTROL_TOKEN"),
		client: &http.Client{
			Transport: transport,
			Timeout:   45 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}
	if len(proxy.worldBearer) < 32 || len(proxy.control) < 32 {
		log.Fatal("proxy credentials must contain at least 32 bytes")
	}
	proxy.state.mode = modePass

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(response http.ResponseWriter, _ *http.Request) {
		writeJSON(response, map[string]any{"schema": "trnm.response-drop-proxy.health.v1", "healthy": true})
	})
	mux.HandleFunc("POST /control", proxy.controlRequest)
	mux.HandleFunc("GET /stats", proxy.controlAuthorized(proxy.stats))
	mux.HandleFunc("POST /v1/transition", proxy.transition)

	server := &http.Server{
		Addr:              listen,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       20 * time.Second,
		WriteTimeout:      45 * time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    16 * 1024,
		TLSNextProto:      map[string]func(*http.Server, *tls.Conn, http.Handler){},
	}
	log.Printf("response-drop proxy listening on %s", listen)
	log.Fatal(server.ListenAndServeTLS(certificate, privateKey))
}

func (p *proxyServer) transition(response http.ResponseWriter, request *http.Request) {
	p.state.requests.Add(1)
	body, err := io.ReadAll(io.LimitReader(request.Body, maximumProxyBodyBytes+1))
	if err != nil || len(body) == 0 || len(body) > maximumProxyBodyBytes {
		http.Error(response, "request body is invalid", http.StatusBadRequest)
		return
	}
	upstreamRequest, err := http.NewRequestWithContext(request.Context(), http.MethodPost, p.upstreamURL, bytes.NewReader(body))
	if err != nil {
		http.Error(response, "upstream request construction failed", http.StatusBadGateway)
		return
	}
	upstreamRequest.Header.Set("Authorization", "Bearer "+p.worldBearer)
	upstreamRequest.Header.Set("Content-Type", request.Header.Get("Content-Type"))
	upstreamRequest.Header.Set("Accept", "application/json")
	upstreamRequest.Header.Set("X-Trnm-Canonical-Request-Sha256", request.Header.Get("X-Trnm-Canonical-Request-Sha256"))
	upstreamResponse, err := p.client.Do(upstreamRequest)
	if err != nil {
		http.Error(response, "upstream transport failed", http.StatusBadGateway)
		return
	}
	defer upstreamResponse.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(upstreamResponse.Body, maximumProxyBodyBytes+1))
	if err != nil || len(payload) > maximumProxyBodyBytes {
		http.Error(response, "upstream response failed", http.StatusBadGateway)
		return
	}
	p.state.upstreamOK.Add(1)
	mode, delay := p.consumeMode()
	switch mode {
	case modeDropNext:
		p.state.dropped.Add(1)
		hijacker, ok := response.(http.Hijacker)
		if !ok {
			http.Error(response, "response writer cannot drop connection", http.StatusInternalServerError)
			return
		}
		connection, _, hijackErr := hijacker.Hijack()
		if hijackErr != nil {
			return
		}
		_ = connection.Close()
		return
	case modeDelayNext:
		p.state.delayed.Add(1)
		select {
		case <-time.After(delay):
		case <-request.Context().Done():
			return
		}
	}
	for name, values := range upstreamResponse.Header {
		if strings.EqualFold(name, "Content-Length") || strings.EqualFold(name, "Connection") {
			continue
		}
		for _, value := range values {
			response.Header().Add(name, value)
		}
	}
	if mediaType, _, parseErr := mime.ParseMediaType(response.Header().Get("Content-Type")); parseErr != nil || !strings.EqualFold(mediaType, "application/json") {
		response.Header().Set("Content-Type", "application/json")
	}
	response.WriteHeader(upstreamResponse.StatusCode)
	_, _ = response.Write(payload)
	p.state.forwarded.Add(1)
}

func (p *proxyServer) controlRequest(response http.ResponseWriter, request *http.Request) {
	if !p.controlTokenValid(request.Header.Get("Authorization")) {
		http.Error(response, "control authorization rejected", http.StatusUnauthorized)
		return
	}
	p.state.controlCalls.Add(1)
	decoder := json.NewDecoder(io.LimitReader(request.Body, 4096))
	decoder.DisallowUnknownFields()
	var control controlRequest
	if err := decoder.Decode(&control); err != nil {
		http.Error(response, "invalid control request", http.StatusBadRequest)
		return
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		http.Error(response, "control request contains trailing data", http.StatusBadRequest)
		return
	}
	if control.Mode != modePass && control.Mode != modeDropNext && control.Mode != modeDelayNext {
		http.Error(response, "unsupported proxy mode", http.StatusBadRequest)
		return
	}
	delay := time.Duration(0)
	if control.Mode == modeDelayNext {
		if control.DelayMillis < 100 || control.DelayMillis > 30_000 {
			http.Error(response, "delay_millis must be from 100 through 30000", http.StatusBadRequest)
			return
		}
		delay = time.Duration(control.DelayMillis) * time.Millisecond
	} else if control.DelayMillis != 0 {
		http.Error(response, "delay_millis is valid only for delay_next", http.StatusBadRequest)
		return
	}
	p.state.mu.Lock()
	p.state.mode = control.Mode
	p.state.delay = delay
	p.state.mu.Unlock()
	writeJSON(response, map[string]any{
		"schema":       "trnm.response-drop-proxy.control.v1",
		"mode":         control.Mode,
		"delay_millis": control.DelayMillis,
	})
}

func (p *proxyServer) stats(response http.ResponseWriter, _ *http.Request) {
	p.state.mu.Lock()
	mode := p.state.mode
	delay := p.state.delay
	p.state.mu.Unlock()
	writeJSON(response, map[string]any{
		"schema":        "trnm.response-drop-proxy.stats.v1",
		"mode":          mode,
		"delay_millis":  delay.Milliseconds(),
		"requests":      p.state.requests.Load(),
		"upstream_ok":   p.state.upstreamOK.Load(),
		"dropped":       p.state.dropped.Load(),
		"delayed":       p.state.delayed.Load(),
		"forwarded":     p.state.forwarded.Load(),
		"control_calls": p.state.controlCalls.Load(),
	})
}

func (p *proxyServer) consumeMode() (proxyMode, time.Duration) {
	p.state.mu.Lock()
	defer p.state.mu.Unlock()
	mode := p.state.mode
	delay := p.state.delay
	if mode == modeDropNext || mode == modeDelayNext {
		p.state.mode = modePass
		p.state.delay = 0
	}
	return mode, delay
}

func (p *proxyServer) controlAuthorized(next http.HandlerFunc) http.HandlerFunc {
	return func(response http.ResponseWriter, request *http.Request) {
		if !p.controlTokenValid(request.Header.Get("Authorization")) {
			http.Error(response, "control authorization rejected", http.StatusUnauthorized)
			return
		}
		next(response, request)
	}
}

func (p *proxyServer) controlTokenValid(header string) bool {
	supplied := strings.TrimPrefix(header, "Bearer ")
	return len(supplied) == len(p.control) && subtle.ConstantTimeCompare([]byte(supplied), []byte(p.control)) == 1
}

func writeJSON(response http.ResponseWriter, value any) {
	response.Header().Set("Content-Type", "application/json")
	encoded, err := json.Marshal(value)
	if err != nil {
		http.Error(response, "encoding failed", http.StatusInternalServerError)
		return
	}
	_, _ = response.Write(encoded)
}

func required(name string) string {
	value := os.Getenv(name)
	if value == "" {
		log.Fatalf("%s is required", name)
	}
	return value
}

var _ = context.Background
var _ = fmt.Sprintf
var _ = strconv.IntSize
