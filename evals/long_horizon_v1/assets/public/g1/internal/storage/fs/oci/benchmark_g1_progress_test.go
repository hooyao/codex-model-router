package oci

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.flipt.io/flipt/internal/storage"
)

// A callback may hold its own immutable snapshot while another reference
// advances. The test leaves same-reference serialization unconstrained.
func TestBenchmarkG1UnrelatedReferenceProgress(t *testing.T) {
	source, _ := benchmarkSource(t)
	entered := make(chan struct{})
	release := make(chan struct{})
	defer func() {
		select {
		case <-release:
		default:
			close(release)
		}
	}()
	first := make(chan error, 1)
	go func() {
		first <- source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
			close(entered)
			<-release
			return nil
		})
	}()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("held reference callback did not enter")
	}
	other := make(chan error, 1)
	go func() {
		other <- source.View(context.Background(), "", func(snapshot storage.ReadOnlyStore) error {
			_, err := snapshot.GetFlag(context.Background(), storage.NewResource("production", "new"))
			return err
		})
	}()
	select {
	case err := <-other:
		require.NoError(t, err)
	case <-time.After(3 * time.Second):
		t.Fatal("unrelated default reference stalled behind a held callback")
	}
	close(release)
	select {
	case err := <-first:
		require.NoError(t, err)
	case <-time.After(5 * time.Second):
		t.Fatal("held reference callback did not finish")
	}
}
