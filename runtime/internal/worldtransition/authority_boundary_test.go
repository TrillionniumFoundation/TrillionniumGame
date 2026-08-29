package worldtransition

import "testing"

func TestAuthorityContextNeverCrossesIntoWorldRequest(t *testing.T) {
	prepared := fixturePrepared(t)
	for _, forbidden := range []string{
		"match_id",
		"authorization_id",
		"participant_roster_hash",
		"match_version",
		"global_event_sequence",
		"command_idempotency_key",
	} {
		if _, present := prepared.Request[forbidden]; present {
			t.Fatalf("authority field %q crossed into World request", forbidden)
		}
	}
	if len(prepared.Request) != len(requestFields) {
		t.Fatalf("World request field count drifted: %d", len(prepared.Request))
	}
}
