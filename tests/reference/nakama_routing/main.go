// Diagnostic harness for the pinned Nakama generated gateway, not a server.
// The runner appends the exact handleRoutingError function from upstream api.go.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"

	grpcgw "github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	"github.com/heroiclabs/nakama/v3/apigrpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
)

type input struct {
	Method string `json:"method"`
	Target string `json:"target"`
}
type result struct {
	Method  string `json:"method"`
	Target  string `json:"target"`
	Status  int    `json:"status"`
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func main() {
	mux := grpcgw.NewServeMux(
		grpcgw.WithRoutingErrorHandler(handleRoutingError),
		grpcgw.WithMarshalerOption(grpcgw.MIMEWildcard, &grpcgw.HTTPBodyMarshaler{
			Marshaler: &grpcgw.JSONPb{
				MarshalOptions:   protojson.MarshalOptions{UseProtoNames: true, UseEnumNumbers: true},
				UnmarshalOptions: protojson.UnmarshalOptions{DiscardUnknown: true},
			},
		}),
	)
	if err := apigrpc.RegisterNakamaHandlerServer(context.Background(), mux, &apigrpc.UnimplementedNakamaServer{}); err != nil {
		panic(err)
	}
	var inputs []input
	if err := json.NewDecoder(os.Stdin).Decode(&inputs); err != nil {
		panic(err)
	}
	results := make([]result, 0, len(inputs))
	for _, in := range inputs {
		request := httptest.NewRequest(in.Method, in.Target, strings.NewReader("invalid body"))
		request.Header.Set("Authorization", "Bearer invalid-credential")
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		mux.ServeHTTP(response, request)
		var body struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
			panic(err)
		}
		if response.Code != http.StatusNotImplemented || body.Code != 12 || body.Message != "Method Not Allowed" {
			panic(fmt.Sprintf("unexpected route result: %s %s: %d %s", in.Method, in.Target, response.Code, response.Body.String()))
		}
		results = append(results, result{in.Method, in.Target, response.Code, body.Code, body.Message})
	}
	if err := json.NewEncoder(os.Stdout).Encode(results); err != nil {
		panic(err)
	}
}
