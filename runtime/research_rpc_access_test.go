package main

import (
	"context"
	"testing"

	"github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime/internal/researchcore"
	"github.com/heroiclabs/nakama-common/runtime"
)

func TestResearchParticipantCanReadBindsUserSessionsAndServerHTTPKeyCalls(t *testing.T) {
	view := researchcore.View{
		RosterVersion: 2,
		Participants: []researchcore.ParticipantView{
			{
				ParticipantSlot: 1,
				SubjectUserID:   "nakama-user-1",
				AuthorizationID: "00000000-0000-4000-8000-000000000201",
			},
			{
				ParticipantSlot: 2,
				SubjectUserID:   "nakama-user-2",
				AuthorizationID: "00000000-0000-4000-8000-000000000202",
			},
		},
	}

	userContext := context.WithValue(
		context.Background(),
		runtime.RUNTIME_CTX_USER_ID,
		"nakama-user-1",
	)
	if !researchParticipantCanRead(
		userContext,
		view,
		"00000000-0000-4000-8000-000000000201",
	) {
		t.Fatal("matching user-session authorization was rejected")
	}
	if researchParticipantCanRead(
		userContext,
		view,
		"00000000-0000-4000-8000-000000000202",
	) {
		t.Fatal("user session read another participant's authorization")
	}

	serverContext := context.Background()
	if !researchParticipantCanRead(
		serverContext,
		view,
		"00000000-0000-4000-8000-000000000201",
	) {
		t.Fatal("HTTP-key server call with a current authorization was rejected")
	}
	for _, authorizationID := range []string{
		"00000000-0000-4000-8000-000000000101", // retired roster epoch
		"00000000-0000-4000-8000-000000000999", // unknown authorization
		"",
	} {
		if researchParticipantCanRead(serverContext, view, authorizationID) {
			t.Fatalf("server call accepted non-current authorization %q", authorizationID)
		}
	}
}
