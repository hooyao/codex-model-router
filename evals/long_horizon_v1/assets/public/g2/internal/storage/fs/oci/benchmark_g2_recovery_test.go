package oci

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	v1 "github.com/opencontainers/image-spec/specs-go/v1"
	"github.com/stretchr/testify/require"
	fliptoci "go.flipt.io/flipt/internal/oci"
	"go.flipt.io/flipt/internal/storage"
	storagefs "go.flipt.io/flipt/internal/storage/fs"
	"go.uber.org/zap/zaptest"
	"oras.land/oras-go/v2"
	"oras.land/oras-go/v2/content"
	"oras.land/oras-go/v2/content/memory"
	"oras.land/oras-go/v2/registry/remote/retry"
)

// The public registry exercises the documented Close/acquisition contract at
// the HTTP boundary without depending on a candidate's worker or cache type.
type benchmarkG2Registry struct {
	manifest v1.Descriptor
	blobs    map[string][]byte
	descs    map[string]v1.Descriptor
}

func benchmarkG2Remote(t *testing.T) (*SnapshotStore, string) {
	t.Helper()
	ctx := context.Background()
	target := memory.New()
	layerDesc := layer("public-recovery", `{"namespace":"production"}`,
		fliptoci.MediaTypeFliptNamespace)(t, target)
	manifest, err := oras.PackManifest(ctx, target, oras.PackManifestVersion1_1,
		fliptoci.MediaTypeFliptFeatures, oras.PackManifestOptions{Layers: []v1.Descriptor{layerDesc}})
	require.NoError(t, err)
	body, err := content.FetchAll(ctx, target, manifest)
	require.NoError(t, err)
	var parsed v1.Manifest
	require.NoError(t, json.Unmarshal(body, &parsed))
	registry := &benchmarkG2Registry{manifest: manifest, blobs: map[string][]byte{},
		descs: map[string]v1.Descriptor{}}
	for _, desc := range append([]v1.Descriptor{manifest, parsed.Config}, parsed.Layers...) {
		data, fetchErr := content.FetchAll(ctx, target, desc)
		require.NoError(t, fetchErr)
		registry.blobs[desc.Digest.String()] = data
		registry.descs[desc.Digest.String()] = desc
	}
	server := httptest.NewServer(http.HandlerFunc(registry.serve))
	t.Cleanup(server.Close)
	backend, err := fliptoci.NewStore(zaptest.NewLogger(t), t.TempDir())
	require.NoError(t, err)
	ref, err := fliptoci.ParseReference(server.URL + "/fixture:latest")
	require.NoError(t, err)
	source, err := NewSnapshotStore(ctx, zaptest.NewLogger(t), backend, ref,
		WithPollOptions(storagefs.WithInterval(time.Hour)))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, source.Close()) })
	return source, manifest.Digest.String()
}

func (r *benchmarkG2Registry) serve(w http.ResponseWriter, req *http.Request) {
	key := req.URL.Path[strings.LastIndex(req.URL.Path, "/")+1:]
	desc, ok := r.descs[key]
	if key == "latest" || key == "stable" {
		desc, ok = r.manifest, true
	}
	if !ok {
		http.NotFound(w, req)
		return
	}
	data := r.blobs[desc.Digest.String()]
	w.Header().Set("Docker-Content-Digest", desc.Digest.String())
	w.Header().Set("Content-Type", desc.MediaType)
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	if req.Method != http.MethodHead {
		_, _ = w.Write(data)
	}
}

type benchmarkG2ClosingBody struct {
	io.ReadCloser
	entered chan struct{}
	release <-chan struct{}
	settled chan struct{}
	once    sync.Once
}

func (b *benchmarkG2ClosingBody) Close() error {
	var err error
	b.once.Do(func() {
		close(b.entered)
		<-b.release
		err = b.ReadCloser.Close()
		close(b.settled)
	})
	return err
}

type benchmarkG2ClosingTransport struct {
	base   http.RoundTripper
	digest string
	body   *benchmarkG2ClosingBody
	once   sync.Once
}

func (s *benchmarkG2ClosingTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := s.base.RoundTrip(req)
	if err != nil || resp == nil || resp.StatusCode != http.StatusOK ||
		req.Method != http.MethodGet || !strings.Contains(req.URL.Path, "/fixture/manifests/") {
		return resp, err
	}
	key := req.URL.Path[strings.LastIndex(req.URL.Path, "/")+1:]
	if key != "stable" && key != s.digest {
		return resp, nil
	}
	s.once.Do(func() { s.body.ReadCloser = resp.Body; resp.Body = s.body })
	return resp, nil
}

func benchmarkG2Wait(t *testing.T, event <-chan struct{}) {
	t.Helper()
	select {
	case <-event:
	case <-time.After(5 * time.Second):
		t.Fatal("public acquisition barrier timed out")
	}
}

func TestBenchmarkG2CloseJoinsAcquisitionCleanup(t *testing.T) {
	source, digest := benchmarkG2Remote(t)
	entered, release, settled := make(chan struct{}), make(chan struct{}), make(chan struct{})
	var releaseOnce sync.Once
	defer releaseOnce.Do(func() { close(release) })
	original := retry.DefaultClient.Transport
	require.NotNil(t, original)
	retry.DefaultClient.Transport = &benchmarkG2ClosingTransport{base: original, digest: digest,
		body: &benchmarkG2ClosingBody{entered: entered, release: release, settled: settled}}
	defer func() { retry.DefaultClient.Transport = original }()

	viewResult := make(chan error, 1)
	go func() {
		viewResult <- source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
			return fmt.Errorf("acquisition unexpectedly reached callback")
		})
	}()
	benchmarkG2Wait(t, entered)
	closeResult := make(chan error, 1)
	go func() { closeResult <- source.Close() }()
	closedEarly := false
	var closeErr error
	select {
	case closeErr = <-closeResult:
		closedEarly = true
	case <-time.After(200 * time.Millisecond):
	}
	releaseOnce.Do(func() { close(release) })
	benchmarkG2Wait(t, settled)
	if !closedEarly {
		select {
		case closeErr = <-closeResult:
		case <-time.After(5 * time.Second):
			t.Fatal("Close did not complete after cleanup")
		}
	}
	require.NoError(t, closeErr)
	select {
	case err := <-viewResult:
		require.Error(t, err)
	case <-time.After(5 * time.Second):
		t.Fatal("acquiring View did not finish")
	}
	if closedEarly {
		t.Error("Close returned before acquired response cleanup settled")
	}
}
