package worldtransition

import (
	"fmt"
	"reflect"
	"sort"
)

type Divergence struct {
	FixtureID string `json:"fixture_id"`
	Field     string `json:"field"`
	World     any    `json:"world"`
	Candidate any    `json:"candidate"`
}

type Comparison struct {
	Status              string       `json:"status"`
	FixtureCount        int          `json:"fixture_count"`
	MatchedCount        int          `json:"matched_count"`
	Divergences         []Divergence `json:"divergences"`
	CutoverAuthorized   bool         `json:"cutover_authorized"`
	PublicOnlineEnabled bool         `json:"public_online_enabled"`
}

func CompareObservations(world, candidate []Observation) (Comparison, error) {
	worldByID, err := indexObservations(world, "world")
	if err != nil {
		return Comparison{}, err
	}
	candidateByID, err := indexObservations(candidate, "candidate")
	if err != nil {
		return Comparison{}, err
	}
	idsMap := make(map[string]struct{}, len(worldByID)+len(candidateByID))
	for id := range worldByID {
		idsMap[id] = struct{}{}
	}
	for id := range candidateByID {
		idsMap[id] = struct{}{}
	}
	ids := make([]string, 0, len(idsMap))
	for id := range idsMap {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	result := Comparison{Status: "matched", FixtureCount: len(ids), Divergences: []Divergence{}}
	for _, id := range ids {
		left, hasLeft := worldByID[id]
		right, hasRight := candidateByID[id]
		if !hasLeft {
			result.Divergences = append(result.Divergences, Divergence{FixtureID: id, Field: "unexpected_fixture", Candidate: right})
			continue
		}
		if !hasRight {
			result.Divergences = append(result.Divergences, Divergence{FixtureID: id, Field: "missing_fixture", World: left})
			continue
		}
		before := len(result.Divergences)
		compareField := func(field string, a, b any) {
			if !reflect.DeepEqual(a, b) {
				result.Divergences = append(result.Divergences, Divergence{FixtureID: id, Field: field, World: a, Candidate: b})
			}
		}
		compareField("authority_context_fingerprint", left.AuthorityContextFingerprint, right.AuthorityContextFingerprint)
		compareField("canonical_result_sha256", left.CanonicalResultSHA256, right.CanonicalResultSHA256)
		compareField("disposition", left.Disposition, right.Disposition)
		compareField("request_hash", left.RequestHash, right.RequestHash)
		compareField("previous_state_hash", left.PreviousStateHash, right.PreviousStateHash)
		compareField("next_tick", left.NextTick, right.NextTick)
		compareField("next_state_hash", left.NextStateHash, right.NextStateHash)
		compareField("replay_hash", left.ReplayHash, right.ReplayHash)
		compareField("world_outcome_hash", left.WorldOutcomeHash, right.WorldOutcomeHash)
		compareField("world_transition_hash", left.WorldTransitionHash, right.WorldTransitionHash)
		compareField("error_code", left.ErrorCode, right.ErrorCode)
		compareField("retryable", left.Retryable, right.Retryable)
		if len(result.Divergences) == before {
			result.MatchedCount++
		}
	}
	if len(result.Divergences) != 0 {
		result.Status = "diverged"
	}
	return result, nil
}

func indexObservations(values []Observation, label string) (map[string]Observation, error) {
	result := make(map[string]Observation, len(values))
	for _, value := range values {
		if value.ContractVersion != ObservationContract {
			return nil, fmt.Errorf("%w: %s observation contract mismatch", ErrContract, label)
		}
		if _, duplicate := result[value.FixtureID]; duplicate {
			return nil, fmt.Errorf("%w: duplicate %s fixture %q", ErrContract, label, value.FixtureID)
		}
		result[value.FixtureID] = value
	}
	return result, nil
}
