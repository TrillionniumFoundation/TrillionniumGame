// Diagnostic only: the runner inserts the exact pinned root registration and
// CORS options and retains the original Apache-2.0 source header.
package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"time"

	"github.com/gorilla/handlers"
	"github.com/gorilla/mux"
)

type fixture struct {
	ID      string            `json:"id"`
	Method  string            `json:"method"`
	Target  string            `json:"target"`
	Headers map[string]string `json:"headers"`
}

func main() {
	grpcGatewayRouter := mux.NewRouter()
	// PINNED_ROOT_REGISTRATION
	// PINNED_CORS_OPTIONS
	server := httptest.NewServer(handlerWithCORS)
	defer server.Close()
	client := server.Client()
	client.Timeout = 2 * time.Second
	client.Transport.(*http.Transport).DisableCompression = true
	client.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	var cases []fixture
	if err := json.NewDecoder(io.LimitReader(os.Stdin, 1<<20)).Decode(&cases); err != nil {
		panic(err)
	}
	if len(cases) != 9 {
		panic("root fixture denominator changed")
	}
	observations := make([]map[string]any, 0, len(cases))
	for _, row := range cases {
		request, err := http.NewRequest(row.Method, server.URL+row.Target, strings.NewReader(""))
		if err != nil {
			panic(err)
		}
		for name, value := range row.Headers {
			request.Header.Set(name, value)
		}
		response, err := client.Do(request)
		if err != nil {
			panic(err)
		}
		body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
		response.Body.Close()
		if err != nil {
			panic(err)
		}
		observations = append(observations, map[string]any{
			"id": row.ID, "method": row.Method, "target": row.Target, "request_headers": row.Headers,
			"status": response.StatusCode, "body": string(body), "raw_headers": response.Header,
		})
	}
	if err := json.NewEncoder(os.Stdout).Encode(observations); err != nil {
		panic(err)
	}
}
