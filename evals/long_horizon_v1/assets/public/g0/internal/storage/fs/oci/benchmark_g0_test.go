package oci

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	fliptoci "go.flipt.io/flipt/internal/oci"
	"go.flipt.io/flipt/internal/storage"
	storagefs "go.flipt.io/flipt/internal/storage/fs"
	"go.uber.org/zap/zaptest"
	"oras.land/oras-go/v2"
)

// The interface assertion is deliberately dynamic: the seed builds and fails
// on behavior, so the public receipt identifies the missing capability.
func benchmarkSource(t *testing.T) (storagefs.ReferencedSnapshotStore, oras.Target) {
	t.Helper()
	target, dir, repo := testRepository(t,
		layer("production", `{"namespace":"production","flags":[{"key":"old","name":"Old"}]}`, fliptoci.MediaTypeFliptNamespace))
	ctx := context.Background()
	old, err := target.Resolve(ctx, "latest")
	require.NoError(t, err)
	require.NoError(t, target.Tag(ctx, old, "stable"))
	updateRepoContents(t, target,
		layer("production", `{"namespace":"production","flags":[{"key":"new","name":"New"}]}`, fliptoci.MediaTypeFliptNamespace))
	store, err := fliptoci.NewStore(zaptest.NewLogger(t), dir)
	require.NoError(t, err)
	ref, err := fliptoci.ParseReference(fmt.Sprintf("flipt://local/%s:latest", repo))
	require.NoError(t, err)
	parent, cancel := context.WithCancel(ctx)
	t.Cleanup(cancel)
	source, err := NewSnapshotStore(parent, zaptest.NewLogger(t), store, ref,
		WithPollOptions(storagefs.WithInterval(time.Hour)))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, source.Close()) })
	view, ok := any(source).(storagefs.ReferencedSnapshotStore)
	require.True(t, ok, "OCI store must implement ReferencedSnapshotStore")
	return view, target
}

func benchmarkHasFlag(t *testing.T, source storagefs.ReferencedSnapshotStore, ref storage.Reference, key string) bool {
	t.Helper()
	ctx := context.Background()
	found := false
	err := source.View(ctx, ref, func(snapshot storage.ReadOnlyStore) error {
		_, lookupErr := snapshot.GetFlag(ctx, storage.NewResource("production", key))
		found = lookupErr == nil
		return nil
	})
	require.NoError(t, err)
	return found
}

func TestBenchmarkG0ReferenceSelection(t *testing.T) {
	source, target := benchmarkSource(t)
	ctx := context.Background()
	require.True(t, benchmarkHasFlag(t, source, "", "new"))
	require.False(t, benchmarkHasFlag(t, source, "", "old"))
	require.True(t, benchmarkHasFlag(t, source, "stable", "old"))
	require.False(t, benchmarkHasFlag(t, source, "stable", "new"))
	desc, err := target.Resolve(ctx, "stable")
	require.NoError(t, err)
	require.True(t, benchmarkHasFlag(t, source, storage.Reference(desc.Digest.String()), "old"))
	called := false
	err = source.View(ctx, "https://other.example/repo:tag", func(storage.ReadOnlyStore) error { called = true; return nil })
	require.Error(t, err)
	require.False(t, called)
}
