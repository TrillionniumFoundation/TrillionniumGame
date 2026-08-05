package researchcontract

// The server-generated payloads are canonical binary frames too. Keeping
// their encoders in the contract package lets an archive consumer verify the
// semantic envelope instead of treating authority-created bytes as opaque.

func ParticipantJoinedPayload(rosterVersion uint64, slot uint32, userID, authorizationID, agentID string) ([]byte, error) {
	return newFrame("trnm_research_session_participant_joined_v1").u64(rosterVersion).u32(slot).
		string(userID).string(authorizationID).string(agentID).result()
}

func ParticipantDisconnectedPayload(rosterVersion uint64, slot uint32, userID, authorizationID string) ([]byte, error) {
	return newFrame("trnm_research_session_participant_disconnected_v1").u64(rosterVersion).u32(slot).
		string(userID).string(authorizationID).result()
}

func ParticipantReconnectedPayload(rosterVersion uint64, slot uint32, userID, authorizationID string) ([]byte, error) {
	return newFrame("trnm_research_session_participant_reconnected_v1").u64(rosterVersion).u32(slot).
		string(userID).string(authorizationID).result()
}

func RosterReplacedPayload(oldVersion, newVersion uint64, slot uint32, oldRoot, newRoot Digest, oldAuthorizationID, newAuthorizationID string) ([]byte, error) {
	return newFrame("trnm_research_session_roster_replaced_v1").u64(oldVersion).u64(newVersion).u32(slot).
		digest(oldRoot).digest(newRoot).string(oldAuthorizationID).string(newAuthorizationID).result()
}
