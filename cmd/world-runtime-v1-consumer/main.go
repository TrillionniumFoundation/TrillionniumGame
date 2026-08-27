package main

import (
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/worldruntimev1"
)

var inputFields = []string{
	"contract_version",
	"implementation_id",
	"implementation_revision",
	"duration_micros",
	"authority_context",
	"request",
	"response",
}

func main() {
	if err := run(); err != nil {
		writeError(err)
		os.Exit(64)
	}
}

func run() error {
	path, help, err := parseArgs(os.Args[1:])
	if err != nil {
		return err
	}
	if help {
		fmt.Println("world-runtime-v1-consumer [--input PATH]\n\nReads one strict trnm_nakama_world_runtime_consumer_input_v1 packet from PATH or stdin and emits an unsigned Nakama verification report.")
		return nil
	}
	var data []byte
	if path == "" {
		data, err = io.ReadAll(os.Stdin)
	} else {
		data, err = os.ReadFile(path)
	}
	if err != nil {
		return &worldruntimev1.ContractError{Code: "invalid_host_configuration", Message: err.Error()}
	}
	value, err := worldruntimev1.ParseStrict(data)
	if err != nil {
		return err
	}
	packet, ok := value.(map[string]any)
	if !ok {
		return &worldruntimev1.ContractError{Code: "invalid_contract", Message: "consumer input must be an object"}
	}
	if err := exactPacket(packet); err != nil {
		return err
	}
	version, ok := packet["contract_version"].(string)
	if !ok || version != worldruntimev1.NakamaConsumerInputV1 {
		return &worldruntimev1.ContractError{Code: "unsupported_contract", Message: "unsupported consumer input contract"}
	}
	implementationID, ok := packet["implementation_id"].(string)
	if !ok {
		return &worldruntimev1.ContractError{Code: "invalid_contract", Message: "implementation_id must be a string"}
	}
	implementationRevision, ok := packet["implementation_revision"].(string)
	if !ok {
		return &worldruntimev1.ContractError{Code: "invalid_contract", Message: "implementation_revision must be a string"}
	}
	duration, ok := packet["duration_micros"].(int64)
	if !ok {
		return &worldruntimev1.ContractError{Code: "invalid_contract", Message: "duration_micros must be an integer"}
	}
	context, err := worldruntimev1.ParseAuthorityContext(packet["authority_context"])
	if err != nil {
		return err
	}
	observation, verified, err := worldruntimev1.BuildObservation(
		context,
		implementationID,
		implementationRevision,
		packet["request"],
		packet["response"],
		duration,
	)
	if err != nil {
		return err
	}
	report := worldruntimev1.ConsumerReport(context, observation, verified)
	encoded, err := worldruntimev1.CanonicalBytes(report)
	if err != nil {
		return err
	}
	if _, err := os.Stdout.Write(append(encoded, '\n')); err != nil {
		return &worldruntimev1.ContractError{Code: "invalid_host_configuration", Message: err.Error()}
	}
	return nil
}

func parseArgs(args []string) (path string, help bool, err error) {
	for index := 0; index < len(args); index++ {
		switch args[index] {
		case "--help", "-h":
			help = true
		case "--input":
			if path != "" || index+1 >= len(args) {
				return "", false, &worldruntimev1.ContractError{Code: "invalid_host_configuration", Message: "--input requires one path"}
			}
			index++
			path = args[index]
		default:
			return "", false, &worldruntimev1.ContractError{Code: "invalid_host_configuration", Message: "unknown argument " + args[index]}
		}
	}
	return path, help, nil
}

func exactPacket(packet map[string]any) error {
	expected := make(map[string]struct{}, len(inputFields))
	for _, field := range inputFields {
		expected[field] = struct{}{}
	}
	for field := range packet {
		if _, ok := expected[field]; !ok {
			return &worldruntimev1.ContractError{Code: "invalid_contract", Message: "unknown consumer input field " + field}
		}
	}
	for _, field := range inputFields {
		if _, ok := packet[field]; !ok {
			return &worldruntimev1.ContractError{Code: "invalid_contract", Message: "missing consumer input field " + field}
		}
	}
	return nil
}

func writeError(err error) {
	code := "internal_error"
	message := err.Error()
	var contract *worldruntimev1.ContractError
	if errors.As(err, &contract) {
		code = contract.Code
		message = contract.Message
	}
	envelope := map[string]any{
		"contract_version": "trnm_nakama_world_runtime_consumer_error_v1",
		"error_code":       code,
		"error":            message,
	}
	encoded, encodeErr := worldruntimev1.CanonicalBytes(envelope)
	if encodeErr != nil {
		fmt.Fprintln(os.Stderr, err)
		return
	}
	fmt.Fprintln(os.Stderr, string(encoded))
}
