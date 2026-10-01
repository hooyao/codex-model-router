package oci

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	fliptoci "go.flipt.io/flipt/internal/oci"
	"go.flipt.io/flipt/internal/storage"
)

func TestBenchmarkG1AliasAndMove(t *testing.T) {
	source, target := benchmarkSource(t)
	ctx := context.Background()
	d1, err := target.Resolve(ctx, "stable")
	require.NoError(t, err)
	require.NoError(t, target.Tag(ctx, d1, "canary"))
	require.True(t, benchmarkHasFlag(t, source, "canary", "old"))
	require.True(t, benchmarkHasFlag(t, source, "stable", "old"))
	d2, err := target.Resolve(ctx, "latest")
	require.NoError(t, err)
	require.NoError(t, target.Tag(ctx, d2, "stable"))
	require.True(t, benchmarkHasFlag(t, source, "stable", "new"))
	require.True(t, benchmarkHasFlag(t, source, "canary", "old"))
	require.True(t, benchmarkHasFlag(t, source, storage.Reference(d1.Digest.String()), "old"))
	// The failed explicit reference must not fall back to latest or last-good.
	require.NoError(t, target.Tag(ctx, d1, "separate"))
	updateRepoContents(t, target,
		layer("production", `{"namespace":"production"}`, fliptoci.MediaTypeFliptNamespace),
		layer("broken", `not-json`, fliptoci.MediaTypeFliptNamespace))
	failed := false
	err = source.View(ctx, "latest", func(_ storage.ReadOnlyStore) error { failed = true; return nil })
	require.Error(t, err)
	require.False(t, failed)
	require.True(t, benchmarkHasFlag(t, source, "canary", "old"))
}

func TestBenchmarkG1ConcurrentAliasViews(t *testing.T) {
	source, target := benchmarkSource(t)
	ctx := context.Background()
	old, err := target.Resolve(ctx, "stable")
	require.NoError(t, err)
	require.NoError(t, target.Tag(ctx, old, "canary"))
	newer, err := target.Resolve(ctx, "latest")
	require.NoError(t, err)
	require.NoError(t, target.Tag(ctx, newer, "stable"))
	type observed struct {
		ref string
		key string
		err error
	}
	ready := make(chan struct{}, 2)
	start := make(chan struct{})
	results := make(chan observed, 2)
	for _, test := range []struct{ ref, key string }{{"stable", "new"}, {"canary", "old"}} {
		go func(ref, key string) {
			ready <- struct{}{}
			<-start
			err := source.View(ctx, storage.Reference(ref), func(snapshot storage.ReadOnlyStore) error {
				_, lookupErr := snapshot.GetFlag(ctx, storage.NewResource("production", key))
				return lookupErr
			})
			results <- observed{ref: ref, key: key, err: err}
		}(test.ref, test.key)
	}
	for i := 0; i < 2; i++ {
		select {
		case <-ready:
		case <-time.After(5 * time.Second):
			close(start)
			t.Fatal("alias View did not reach start barrier")
		}
	}
	close(start)
	for i := 0; i < 2; i++ {
		select {
		case result := <-results:
			require.NoError(t, result.err, "%s must show %s", result.ref, result.key)
		case <-time.After(5 * time.Second):
			t.Fatal("concurrent alias View did not finish")
		}
	}
}
