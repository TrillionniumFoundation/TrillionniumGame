package worldcommand

import (
	"fmt"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/worldtransition"
)

type WorldTransitionCodec struct{}

func (WorldTransitionCodec) DecodeCanonical(raw []byte, maximumBytes int) (any, error) {
	return worldtransition.ParseCanonical(raw, true, maximumBytes)
}

func (WorldTransitionCodec) EncodeCanonical(value any) ([]byte, error) {
	return worldtransition.CanonicalJSON(value, true)
}

func (WorldTransitionCodec) Prepare(
	context worldtransition.AuthorityContext,
	previousStateSchemaID string,
	previousState any,
	commandSchemaID string,
	command any,
) (PreparedTransition, error) {
	prepared, err := worldtransition.Prepare(context, previousStateSchemaID, previousState, commandSchemaID, command)
	if err != nil {
		return PreparedTransition{}, err
	}
	return PreparedTransition{
		CanonicalRequest:  append([]byte(nil), prepared.CanonicalRequest...),
		RequestHash:       prepared.RequestHash,
		TransitionID:      prepared.TransitionID,
		WorldCommandID:    prepared.CommandID,
		PreviousStateHash: prepared.PreviousStateHash,
	}, nil
}

func (WorldTransitionCodec) Restore(
	context worldtransition.AuthorityContext,
	canonicalRequest []byte,
) (PreparedTransition, error) {
	prepared, err := worldtransition.PreparedFromCanonicalRequest(context, canonicalRequest)
	if err != nil {
		return PreparedTransition{}, err
	}
	return PreparedTransition{
		CanonicalRequest:  append([]byte(nil), prepared.CanonicalRequest...),
		RequestHash:       prepared.RequestHash,
		TransitionID:      prepared.TransitionID,
		WorldCommandID:    prepared.CommandID,
		PreviousStateHash: prepared.PreviousStateHash,
	}, nil
}

func (WorldTransitionCodec) Verify(
	prepared PreparedTransition,
	context worldtransition.AuthorityContext,
	rawResult []byte,
) (VerifiedTransition, error) {
	restored, err := worldtransition.PreparedFromCanonicalRequest(context, prepared.CanonicalRequest)
	if err != nil {
		return VerifiedTransition{}, fmt.Errorf("restore prepared request: %w", err)
	}
	if restored.RequestHash != prepared.RequestHash || restored.TransitionID != prepared.TransitionID ||
		restored.CommandID != prepared.WorldCommandID || restored.PreviousStateHash != prepared.PreviousStateHash {
		return VerifiedTransition{}, fmt.Errorf("prepared transition identity drift")
	}
	verified, err := worldtransition.VerifyResult(restored, rawResult)
	if err != nil {
		return VerifiedTransition{}, err
	}
	out := VerifiedTransition{
		RequestHash:           verified.RequestHash,
		TransitionID:          verified.TransitionID,
		CanonicalResultSHA256: verified.CanonicalResultSHA256,
		NextTick:              cloneInt64(verified.NextTick),
		PreviousStateHash:     cloneString(verified.PreviousStateHash),
		WorldOutcomeHash:      cloneString(verified.WorldOutcomeHash),
		WorldTransitionHash:   cloneString(verified.WorldTransitionHash),
		ErrorCode:             cloneString(verified.ErrorCode),
		Retryable:             cloneBool(verified.Retryable),
	}
	switch verified.Disposition {
	case worldtransition.DispositionAccepted:
		out.Disposition = DispositionAccepted
		if verified.NextState == nil {
			return VerifiedTransition{}, fmt.Errorf("accepted World result has no next state")
		}
		out.NextStateSchemaID = verified.NextState.SchemaID
		out.NextStateCanonicalJSON = append([]byte(nil), verified.NextState.CanonicalJSON...)
		value := verified.NextState.SHA256
		out.NextStateHash = &value
		if verified.ReplayMaterial != nil {
			replay := verified.ReplayMaterial.SHA256
			out.ReplayHash = &replay
		}
	case worldtransition.DispositionRejected:
		out.Disposition = DispositionRejected
	default:
		return VerifiedTransition{}, fmt.Errorf("unsupported World disposition %q", verified.Disposition)
	}
	return out, nil
}

func cloneString(value *string) *string {
	if value == nil {
		return nil
	}
	copyValue := *value
	return &copyValue
}

func cloneInt64(value *int64) *int64 {
	if value == nil {
		return nil
	}
	copyValue := *value
	return &copyValue
}

func cloneBool(value *bool) *bool {
	if value == nil {
		return nil
	}
	copyValue := *value
	return &copyValue
}
