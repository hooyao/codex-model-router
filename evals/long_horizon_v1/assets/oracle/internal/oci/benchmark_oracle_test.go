package oci

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path"
	"strings"
	"testing"
	"time"

	v1 "github.com/opencontainers/image-spec/specs-go/v1"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap/zaptest"
	"oras.land/oras-go/v2"
	"oras.land/oras-go/v2/content"
	"oras.land/oras-go/v2/content/oci"
)

// Fetch.Digest may remain an annotation-insensitive change detector. The
// registry manifest identity is checked through digest-addressed fetches.
func TestBenchmarkOracleManifestDigestIncludesAnnotations(t *testing.T) {
	ctx := context.Background()
	dir := testRepository(t)
	target, err := oci.New(path.Join(dir, repo))
	require.NoError(t, err)
	target.AutoSaveIndex = true
	contentLayer := layer("alpha", `{"namespace":"alpha"}`, MediaTypeFliptNamespace)(t, target)
	pack := func(note string) v1.Descriptor {
		desc, err := oras.PackManifest(ctx, target, oras.PackManifestVersion1_1_RC4,
			MediaTypeFliptFeatures, oras.PackManifestOptions{
				ManifestAnnotations: map[string]string{"benchmark.note": note},
				Layers:              []v1.Descriptor{contentLayer},
			})
		require.NoError(t, err)
		return desc
	}
	first, second := pack("first"), pack("second")
	require.NotEqual(t, first.Digest, second.Digest)
	ref, err := ParseReference(fmt.Sprintf("flipt://local/%s:latest", repo))
	require.NoError(t, err)
	store, err := NewStore(zaptest.NewLogger(t), dir)
	require.NoError(t, err)
	for _, manifest := range []v1.Descriptor{first, second} {
		ref.Reference.Reference = manifest.Digest.String()
		response, err := store.Fetch(ctx, ref)
		require.NoError(t, err)
		require.Len(t, response.Files, 1)
		payload, err := io.ReadAll(response.Files[0])
		require.NoError(t, err)
		require.JSONEq(t, `{"namespace":"alpha"}`, string(payload))
		require.NoError(t, response.Files[0].Close())
	}
}

type benchmarkOracleStream struct {
	io.Reader
	closed int
}

func (s *benchmarkOracleStream) Close() error { s.closed++; return nil }

type benchmarkOracleTarget struct {
	oras.ReadOnlyTarget
	fetches int
	failAt  int
	failure error
	streams []*benchmarkOracleStream
}

func (s *benchmarkOracleTarget) Fetch(context.Context, v1.Descriptor) (io.ReadCloser, error) {
	s.fetches++
	if s.fetches == s.failAt {
		return nil, s.failure
	}
	stream := &benchmarkOracleStream{Reader: strings.NewReader(`{"namespace":"alpha"}`)}
	s.streams = append(s.streams, stream)
	return stream, nil
}

func TestBenchmarkOracleFetchClosesAcquiredStreamsAndRetries(t *testing.T) {
	sourceErr := errors.New("second layer unavailable")
	target := &benchmarkOracleTarget{failAt: 2, failure: sourceErr}
	manifest := v1.Manifest{
		Annotations: map[string]string{v1.AnnotationCreated: time.Now().UTC().Format(time.RFC3339)},
		Layers: []v1.Descriptor{
			content.NewDescriptorFromBytes(MediaTypeFliptNamespace, []byte(`{"namespace":"alpha"}`)),
			content.NewDescriptorFromBytes(MediaTypeFliptNamespace, []byte(`{"namespace":"alpha"}`)),
		},
	}
	store, err := NewStore(zaptest.NewLogger(t), t.TempDir())
	require.NoError(t, err)
	files, err := store.fetchFiles(context.Background(), target, manifest)
	require.ErrorIs(t, err, sourceErr)
	require.Nil(t, files)
	require.Len(t, target.streams, 1)
	require.Equal(t, 1, target.streams[0].closed)
	target.fetches, target.failAt = 0, 0
	files, err = store.fetchFiles(context.Background(), target, manifest)
	require.NoError(t, err)
	require.Len(t, files, 2)
	for _, file := range files {
		require.NoError(t, file.Close())
	}
	for _, stream := range target.streams {
		require.Equal(t, 1, stream.closed)
	}
}
