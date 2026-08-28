package main

import "os"

var worldCommandEnvironmentKeys = []string{
	envWorldProfile,
	envWorldURL,
	envWorldBearer,
	envWorldCAPath,
	envWorldCAPEMBase64,
	envWorldTimeoutMS,
	envWorldMaxResponseBytes,
	envWorldRulesetRevision,
	envWorldContentRevision,
	envWorldStateSchema,
	envWorldCommandSchema,
	envWorldInitialState,
	envWorldInitialTick,
	envWorldRulesetHash,
	envWorldContentHash,
	envWorldInitialStateHash,
	envWorldChallengeHash,
	envWorldFaultLab,
	envWorldFailpoint,
}

// worldCommandEnvironment merges only the explicit World command allowlist
// from the process environment. This avoids exposing unrelated container
// environment variables to the runtime contract while allowing mounted
// fault-lab and deployment secrets to remain outside Nakama's public config.
func worldCommandEnvironment(runtimeEnvironment map[string]string) map[string]string {
	merged := make(map[string]string, len(runtimeEnvironment)+len(worldCommandEnvironmentKeys))
	for key, value := range runtimeEnvironment {
		merged[key] = value
	}
	for _, key := range worldCommandEnvironmentKeys {
		if value, exists := os.LookupEnv(key); exists {
			merged[key] = value
		}
	}
	return merged
}
