package worldtransition

import "fmt"

const ObservationContract = "trnm_nakama_world_transition_observation_v1"

type Observation struct {
	ContractVersion             string  `json:"contract_version"`
	FixtureID                   string  `json:"fixture_id"`
	ImplementationID            string  `json:"implementation_id"`
	ImplementationRevision      string  `json:"implementation_revision"`
	AuthorityContextFingerprint string  `json:"authority_context_fingerprint"`
	CanonicalResultSHA256       string  `json:"canonical_result_sha256"`
	Disposition                 string  `json:"disposition"`
	RequestHash                 string  `json:"request_hash"`
	PreviousStateHash           *string `json:"previous_state_hash"`
	NextTick                    *int64  `json:"next_tick"`
	NextStateHash               *string `json:"next_state_hash"`
	ReplayHash                  *string `json:"replay_hash"`
	WorldOutcomeHash            *string `json:"world_outcome_hash"`
	WorldTransitionHash         *string `json:"world_transition_hash"`
	ErrorCode                   *string `json:"error_code"`
	Retryable                   *bool   `json:"retryable"`
	DurationMicros              int64   `json:"duration_micros"`
}

func Observe(fixtureID, implementationID, revision string, durationMicros int64, verified Verified) (Observation, error) {
	for label, value := range map[string]string{
		"fixture_id": fixtureID, "implementation_id": implementationID, "implementation_revision": revision,
	} {
		if err := requireIdentifier(value, label); err != nil {
			return Observation{}, err
		}
	}
	if durationMicros < 0 {
		return Observation{}, fmt.Errorf("%w: duration_micros must be non-negative", ErrContract)
	}
	return Observation{
		ContractVersion:             ObservationContract,
		FixtureID:                   fixtureID,
		ImplementationID:            implementationID,
		ImplementationRevision:      revision,
		AuthorityContextFingerprint: verified.AuthorityContextFingerprint,
		CanonicalResultSHA256:       verified.CanonicalResultSHA256,
		Disposition:                 string(verified.Disposition),
		RequestHash:                 verified.RequestHash,
		PreviousStateHash:           verified.PreviousStateHash,
		NextTick:                    verified.NextTick,
		NextStateHash:               verified.NextStateHash(),
		ReplayHash:                  verified.ReplayHash(),
		WorldOutcomeHash:            verified.WorldOutcomeHash,
		WorldTransitionHash:         verified.WorldTransitionHash,
		ErrorCode:                   verified.ErrorCode,
		Retryable:                   verified.Retryable,
		DurationMicros:              durationMicros,
	}, nil
}
