package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestResearchHTTPClientNeverForwardsServiceTokenAcrossRedirect(t *testing.T) {
	forwarded := make(chan string, 1)
	target := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, request *http.Request) {
		forwarded <- request.Header.Get("x-hepta-nakama-token")
	}))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		http.Redirect(response, request, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()

	request, err := http.NewRequest(http.MethodPost, redirect.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("x-hepta-nakama-token", "secret-service-token")
	response, err := newResearchHTTPClient().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusTemporaryRedirect {
		t.Fatalf("redirect was followed or changed: %d", response.StatusCode)
	}
	select {
	case token := <-forwarded:
		t.Fatalf("service token reached redirected server: %q", token)
	default:
	}
}

func TestHeptaResponseContentTypeIsExactJSON(t *testing.T) {
	for _, accepted := range []string{"application/json", "APPLICATION/JSON"} {
		response := &http.Response{Header: http.Header{"Content-Type": []string{accepted}}}
		if err := requireHeptaJSONResponse(response); err != nil {
			t.Fatalf("JSON content type %q rejected: %v", accepted, err)
		}
	}
	for _, rejected := range [][]string{{}, {"text/json"}, {"application/json; charset=utf-8"}, {"application/json", "application/json"}} {
		response := &http.Response{Header: http.Header{"Content-Type": rejected}}
		if err := requireHeptaJSONResponse(response); err == nil {
			t.Fatalf("non-canonical content-type accepted: %#v", rejected)
		}
	}
}
