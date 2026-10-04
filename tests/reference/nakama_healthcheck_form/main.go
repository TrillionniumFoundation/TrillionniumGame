// Diagnostic only. The runner appends the exact pinned routing callback and
// health handler, changing only the health handler's receiver type.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"unicode"

	grpcgw "github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	"github.com/heroiclabs/nakama/v3/apigrpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/emptypb"
)

type referenceServer struct {
	apigrpc.UnimplementedNakamaServer
}
type fixture struct {
	ID       string            `json:"id"`
	Method   string            `json:"method"`
	Target   string            `json:"target"`
	Headers  map[string]string `json:"headers"`
	BodyHex  string            `json:"body_hex"`
	Status   int               `json:"status"`
	Response json.RawMessage   `json:"response"`
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
	if err := apigrpc.RegisterNakamaHandlerServer(context.Background(), mux, &referenceServer{}); err != nil {
		panic(err)
	}
	var fixtures []fixture
	if err := json.NewDecoder(io.LimitReader(os.Stdin, 1<<20)).Decode(&fixtures); err != nil {
		panic(err)
	}
	if len(fixtures) != 48 {
		panic("fixture denominator changed")
	}
	for i := range fixtures {
		row := &fixtures[i]
		body, err := hex.DecodeString(row.BodyHex)
		if err != nil {
			panic(err)
		}
		request := httptest.NewRequest(row.Method, row.Target, bytes.NewReader(body))
		for name, value := range row.Headers {
			request.Header.Set(name, value)
		}
		response := httptest.NewRecorder()
		mux.ServeHTTP(response, request)
		row.Status = response.Code
		row.Response = append(json.RawMessage(nil), response.Body.Bytes()...)
	}
	var corpus bytes.Buffer
	for first := 0; first < 256; first++ {
		for second := 0; second < 256; second++ {
			fmt.Fprintf(&corpus, "%x\n", strconv.Quote(string([]byte{'%', byte(first), byte(second)})))
		}
	}
	mappings := []map[string]string{}
	for value := rune(128); value <= unicode.MaxRune; value++ {
		upper := unicode.ToUpper(value)
		if strings.ContainsRune("GETPOSTHEAD", upper) {
			mappings = append(mappings, map[string]string{"from": string(value), "to": string(upper)})
		}
	}
	result := map[string]any{
		"fixtures":                 fixtures,
		"go_quote_corpus":          map[string]any{"cases": 65536, "bytes": corpus.Len(), "sha256": fmt.Sprintf("%x", sha256.Sum256(corpus.Bytes()))},
		"nonascii_method_mappings": mappings,
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		panic(err)
	}
}
