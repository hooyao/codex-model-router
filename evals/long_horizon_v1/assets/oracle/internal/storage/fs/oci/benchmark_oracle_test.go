package oci

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/opencontainers/go-digest"
	v1 "github.com/opencontainers/image-spec/specs-go/v1"
	"github.com/stretchr/testify/require"
	fliptoci "go.flipt.io/flipt/internal/oci"
	"go.flipt.io/flipt/internal/storage"
	storagefs "go.flipt.io/flipt/internal/storage/fs"
	"go.uber.org/zap/zaptest"
	"oras.land/oras-go/v2"
	"oras.land/oras-go/v2/content"
)

func TestBenchmarkOracleManifestIdentityViews(t *testing.T) {
	ctx := context.Background()
	target, dir, repository := testRepository(t)
	layerDesc := layer("alpha", `{"namespace":"alpha"}`, fliptoci.MediaTypeFliptNamespace)(t, target)
	pack := func(note string) v1.Descriptor {
		desc, err := oras.PackManifest(ctx, target, oras.PackManifestVersion1_1_RC4,
			fliptoci.MediaTypeFliptFeatures, oras.PackManifestOptions{
				ManifestAnnotations: map[string]string{"benchmark.note": note},
				Layers:              []v1.Descriptor{layerDesc},
			})
		require.NoError(t, err)
		return desc
	}
	first, second := pack("first"), pack("second")
	require.NotEqual(t, first.Digest, second.Digest)
	require.NoError(t, target.Tag(ctx, first, "latest"))
	store, err := fliptoci.NewStore(zaptest.NewLogger(t), dir)
	require.NoError(t, err)
	ref, err := fliptoci.ParseReference("flipt://local/" + repository + ":latest")
	require.NoError(t, err)
	parent, cancel := context.WithCancel(ctx)
	defer cancel()
	source, err := NewSnapshotStore(parent, zaptest.NewLogger(t), store, ref,
		WithPollOptions(storagefs.WithInterval(time.Hour)))
	require.NoError(t, err)
	defer source.Close()
	view, ok := any(source).(storagefs.ReferencedSnapshotStore)
	require.True(t, ok)
	for _, manifest := range []v1.Descriptor{first, second} {
		entered := false
		err := view.View(ctx, storage.Reference(manifest.Digest.String()), func(snapshot storage.ReadOnlyStore) error {
			entered = true
			_, err := snapshot.GetNamespace(ctx, storage.NewNamespace("alpha"))
			return err
		})
		require.NoError(t, err)
		require.True(t, entered)
	}
	// The annotation-insensitive content detector is not a registry address.
	manifestBytes, err := content.FetchAll(ctx, target, first)
	require.NoError(t, err)
	var stripped v1.Manifest
	require.NoError(t, json.Unmarshal(manifestBytes, &stripped))
	stripped.Annotations = map[string]string{}
	strippedBytes, err := json.Marshal(&stripped)
	require.NoError(t, err)
	contentDigest := digest.FromBytes(strippedBytes)
	require.NotEqual(t, first.Digest, contentDigest)
	require.NotEqual(t, second.Digest, contentDigest)
	entered := false
	err = view.View(ctx, storage.Reference(contentDigest.String()), func(storage.ReadOnlyStore) error {
		entered = true
		return nil
	})
	require.Error(t, err)
	require.False(t, entered)
}
