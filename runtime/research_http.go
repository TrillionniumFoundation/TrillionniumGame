package main

import (
	"errors"
	"net/http"
	"strings"
	"time"
)

func newResearchHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 3 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			// The request carries x-hepta-nakama-token. Never allow net/http to
			// replay it to a redirected origin (or even a different path).
			return http.ErrUseLastResponse
		},
	}
}

func requireHeptaJSONResponse(response *http.Response) error {
	values := response.Header.Values("content-type")
	if len(values) != 1 || !strings.EqualFold(strings.TrimSpace(values[0]), "application/json") {
		return errors.New("Hepta response content-type must be exactly application/json")
	}
	return nil
}
