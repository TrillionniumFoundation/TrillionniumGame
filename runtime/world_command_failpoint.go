package main

import (
	"os"

	"github.com/TrillionniumFoundation/TrillionniumGame/runtime/internal/worldcommand"
)

const (
	worldExitAfterReservation = 85
	worldExitAfterVerify      = 86
)

func worldCommandFaultHooks(config worldCommandRuntimeConfig) (
	func(worldcommand.Reservation),
	func(worldcommand.Reservation, worldcommand.VerifiedTransition),
) {
	if !config.faultLab {
		return nil, nil
	}
	switch config.failpoint {
	case worldFailpointAfterReservation:
		return func(worldcommand.Reservation) {
			os.Exit(worldExitAfterReservation)
		}, nil
	case worldFailpointAfterVerify:
		return nil, func(worldcommand.Reservation, worldcommand.VerifiedTransition) {
			os.Exit(worldExitAfterVerify)
		}
	default:
		return nil, nil
	}
}
