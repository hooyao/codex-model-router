package oci

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.flipt.io/flipt/internal/storage"
)

func TestBenchmarkG2CancelledAndClosed(t *testing.T) {
	source, _ := benchmarkSource(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	entered := false
	err := source.View(ctx, "stable", func(storage.ReadOnlyStore) error { entered = true; return nil })
	require.ErrorIs(t, err, context.Canceled)
	require.False(t, entered)
	closer, ok := source.(interface{ Close() error })
	require.True(t, ok)
	callbackEntered := make(chan struct{})
	release := make(chan struct{})
	var releaseOnce sync.Once
	releaseCallback := func() { releaseOnce.Do(func() { close(release) }) }
	defer releaseCallback()
	viewResult := make(chan error, 1)
	go func() {
		viewResult <- source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
			close(callbackEntered)
			<-release
			return nil
		})
	}()
	select {
	case <-callbackEntered:
	case <-time.After(5 * time.Second):
		t.Fatal("held View did not enter")
	}
	closeResults := make(chan error, 2)
	for i := 0; i < 2; i++ {
		go func() { closeResults <- closer.Close() }()
	}
	for i := 0; i < 2; i++ {
		select {
		case closeErr := <-closeResults:
			require.NoError(t, closeErr)
		case <-time.After(5 * time.Second):
			t.Fatal("Close blocked on a held View")
		}
	}
	releaseCallback()
	select {
	case viewErr := <-viewResult:
		require.NoError(t, viewErr)
	case <-time.After(5 * time.Second):
		t.Fatal("held View did not finish after release")
	}
	err = source.View(context.Background(), "", func(storage.ReadOnlyStore) error { entered = true; return nil })
	require.Error(t, err)
	require.False(t, entered)
}
