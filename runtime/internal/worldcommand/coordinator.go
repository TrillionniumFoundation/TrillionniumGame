package worldcommand

import (
	"context"
	"errors"
	"fmt"
	"time"
)

type Coordinator struct {
	Store    *Store
	Executor Executor
	Clock    func() time.Time
}

func (c Coordinator) Execute(ctx context.Context, request PrepareRequest) (Receipt, error) {
	if c.Store == nil || c.Executor == nil {
		return Receipt{}, fmt.Errorf("world command coordinator is not configured")
	}
	now := c.now()
	prepared, err := c.Store.Prepare(ctx, request, now)
	if err != nil {
		return Receipt{}, err
	}
	if prepared.Receipt != nil {
		return *prepared.Receipt, nil
	}
	if prepared.Reservation == nil {
		return Receipt{}, fmt.Errorf("world command prepare returned neither reservation nor receipt")
	}
	reservation := *prepared.Reservation

	if err := ctx.Err(); err != nil {
		failure := &ExecutionError{Kind: FailureCancelled, Retryable: true, Err: err}
		_ = c.Store.RecordFailure(context.WithoutCancel(ctx), reservation, failure, c.now())
		return Receipt{}, failure
	}

	reservation, err = c.Store.BeginAttempt(ctx, reservation, c.now())
	if err != nil {
		return Receipt{}, err
	}
	rawResult, executeErr := c.Executor.Execute(ctx, append([]byte(nil), reservation.Transition.CanonicalRequest...))
	if executeErr != nil {
		failure := classifyExecutionError(ctx, executeErr)
		if recordErr := c.Store.RecordFailure(context.WithoutCancel(ctx), reservation, failure, c.now()); recordErr != nil {
			return Receipt{}, errors.Join(failure, recordErr)
		}
		return Receipt{}, failure
	}

	verified, verifyErr := c.Store.codec.Verify(reservation.Transition, reservation.Context, rawResult)
	if verifyErr != nil {
		failure := &ExecutionError{Kind: FailureInvalidResult, Retryable: false, Err: verifyErr}
		if recordErr := c.Store.RecordFailure(context.WithoutCancel(ctx), reservation, failure, c.now()); recordErr != nil {
			return Receipt{}, errors.Join(failure, recordErr)
		}
		return Receipt{}, failure
	}
	if verified.Disposition == DispositionRejected && verified.Retryable != nil && *verified.Retryable {
		failure := &ExecutionError{Kind: FailureRemoteRetryable, Retryable: true, Err: ErrRetryable}
		if recordErr := c.Store.RecordFailure(context.WithoutCancel(ctx), reservation, failure, c.now()); recordErr != nil {
			return Receipt{}, errors.Join(failure, recordErr)
		}
		return Receipt{}, failure
	}

	// A verified external result must still pass exact stale fencing and
	// cleanup even when the caller disconnects after World has responded.
	receipt, commitErr := c.Store.Commit(context.WithoutCancel(ctx), reservation, verified, c.now())
	if commitErr != nil {
		return Receipt{}, commitErr
	}
	return receipt, nil
}

func (c Coordinator) now() time.Time {
	if c.Clock != nil {
		return c.Clock().UTC()
	}
	return time.Now().UTC()
}

func classifyExecutionError(ctx context.Context, err error) *ExecutionError {
	var classified *ExecutionError
	if errors.As(err, &classified) {
		copyError := *classified
		if copyError.Err == nil {
			copyError.Err = err
		}
		return &copyError
	}
	if errors.Is(err, context.Canceled) || errors.Is(ctx.Err(), context.Canceled) {
		return &ExecutionError{Kind: FailureCancelled, Retryable: true, Err: err}
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return &ExecutionError{Kind: FailureAmbiguousCommit, Retryable: true, Err: err}
	}
	return &ExecutionError{Kind: FailureTransport, Retryable: true, Err: err}
}
