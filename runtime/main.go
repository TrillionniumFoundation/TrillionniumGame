package main

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/heroiclabs/nakama-common/runtime"
)

const registeredMatchName = "trnm_authoritative_v1"

// InitModule is the symbol loaded by Nakama's Go plugin runtime.
func InitModule(ctx context.Context, logger runtime.Logger, _ *sql.DB, _ runtime.NakamaModule, initializer runtime.Initializer) error {
	environment, _ := ctx.Value(runtime.RUNTIME_CTX_ENV).(map[string]string)
	module := &moduleRuntime{config: loadModuleConfig(environment), httpClient: newResearchHTTPClient()}
	if err := module.config.ready(); err != nil {
		// A live-but-unready server is intentional: operators can query the
		// readiness RPC and diagnose missing injected secrets without the module
		// silently falling back to fixture credentials.
		logger.Warn("Trillionnium authoritative runtime loaded unready: %s", err.Error())
	}

	registrations := []struct {
		name string
		fn   func(context.Context, runtime.Logger, *sql.DB, runtime.NakamaModule, string) (string, error)
	}{
		{name: rpcCreateMatch, fn: module.rpcCreateMatch},
		{name: rpcResumeMatch, fn: module.rpcResumeMatch},
		{name: rpcEvidence, fn: module.rpcEvidence},
		{name: rpcArchive, fn: module.rpcArchive},
		{name: rpcComplete, fn: module.rpcComplete},
		{name: rpcResearchCreate, fn: module.rpcResearchCreate},
		{name: rpcResearchResume, fn: module.rpcResearchResume},
		{name: rpcResearchEvidence, fn: module.rpcResearchEvidence},
		{name: rpcResearchArchive, fn: module.rpcResearchArchive},
		{name: rpcResearchComplete, fn: module.rpcResearchComplete},
		{name: rpcResearchReplace, fn: module.rpcResearchReplace},
		{name: rpcHealth, fn: module.rpcHealth},
		{name: rpcReady, fn: module.rpcReady},
	}
	for _, registration := range registrations {
		if err := initializer.RegisterRpc(registration.name, registration.fn); err != nil {
			return fmt.Errorf("register RPC %s: %w", registration.name, err)
		}
	}
	if err := initializer.RegisterMatch(registeredMatchName, func(context.Context, runtime.Logger, *sql.DB, runtime.NakamaModule) (runtime.Match, error) {
		return &authoritativeMatch{module: module}, nil
	}); err != nil {
		return fmt.Errorf("register match %s: %w", registeredMatchName, err)
	}
	if err := initializer.RegisterMatch(registeredResearchMatchName, func(context.Context, runtime.Logger, *sql.DB, runtime.NakamaModule) (runtime.Match, error) {
		return &researchMatch{module: module}, nil
	}); err != nil {
		return fmt.Errorf("register match %s: %w", registeredResearchMatchName, err)
	}

	logger.Info("Trillionnium authoritative runtime registered")
	return nil
}
