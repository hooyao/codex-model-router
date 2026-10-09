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
	"sync/atomic"
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
	stable   v1.Descriptor
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
	if key == "latest" {
		desc, ok = r.manifest, true
	} else if key == "stable" {
		desc = r.manifest
		if r.stable.Digest != "" {
			desc = r.stable
		}
		ok = true
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

// Distinct default and explicit manifests prevent a legitimate cache hit from
// bypassing the multi-layer acquisition under test.
func benchmarkG2MultiLayerRemote(t *testing.T) (*SnapshotStore, *fliptoci.Store,
	fliptoci.Reference, map[string]bool) {
	t.Helper()
	ctx := context.Background()
	target := memory.New()
	defaultLayer := layer("public-default", `{"namespace":"default"}`,
		fliptoci.MediaTypeFliptNamespace)(t, target)
	defaultManifest, err := oras.PackManifest(ctx, target, oras.PackManifestVersion1_1,
		fliptoci.MediaTypeFliptFeatures,
		oras.PackManifestOptions{Layers: []v1.Descriptor{defaultLayer}})
	require.NoError(t, err)
	layers := make([]v1.Descriptor, 0, 4)
	layerDigests := make(map[string]bool)
	for i := 0; i < 4; i++ {
		payload := fmt.Sprintf(`{"namespace":%q}`, fmt.Sprintf("public-layer-%d", i))
		desc := layer(fmt.Sprintf("public-layer-%d", i), payload,
			fliptoci.MediaTypeFliptNamespace)(t, target)
		layers = append(layers, desc)
		layerDigests[desc.Digest.String()] = true
	}
	stableManifest, err := oras.PackManifest(ctx, target, oras.PackManifestVersion1_1,
		fliptoci.MediaTypeFliptFeatures, oras.PackManifestOptions{Layers: layers})
	require.NoError(t, err)
	registry := &benchmarkG2Registry{manifest: defaultManifest, stable: stableManifest,
		blobs: map[string][]byte{},
		descs: map[string]v1.Descriptor{}}
	for _, manifest := range []v1.Descriptor{defaultManifest, stableManifest} {
		body, fetchErr := content.FetchAll(ctx, target, manifest)
		require.NoError(t, fetchErr)
		var parsed v1.Manifest
		require.NoError(t, json.Unmarshal(body, &parsed))
		for _, desc := range append([]v1.Descriptor{manifest, parsed.Config}, parsed.Layers...) {
			data, fetchErr := content.FetchAll(ctx, target, desc)
			require.NoError(t, fetchErr)
			registry.blobs[desc.Digest.String()] = data
			registry.descs[desc.Digest.String()] = desc
		}
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
	stableRef := ref
	stableRef.Reference.Reference = "stable"
	return source, backend, stableRef, layerDigests
}

type benchmarkG2LayerBody struct {
	io.ReadCloser
	ctx     context.Context
	closing chan<- struct{}
	settled *atomic.Int64
	release <-chan struct{}
	once    sync.Once
}

func (b *benchmarkG2LayerBody) Read([]byte) (int, error) {
	<-b.ctx.Done()
	return 0, b.ctx.Err()
}

func (b *benchmarkG2LayerBody) Close() error {
	var err error
	b.once.Do(func() {
		b.closing <- struct{}{}
		<-b.release
		err = b.ReadCloser.Close()
		b.settled.Add(1)
	})
	return err
}

type benchmarkG2LayerTransport struct {
	base     http.RoundTripper
	layers   map[string]bool
	opened   chan<- struct{}
	closing  chan<- struct{}
	acquired *atomic.Int64
	settled  *atomic.Int64
	release  <-chan struct{}
}

func (s *benchmarkG2LayerTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := s.base.RoundTrip(req)
	if err == nil && resp != nil && resp.StatusCode == http.StatusOK &&
		req.Method == http.MethodGet {
		key := req.URL.Path[strings.LastIndex(req.URL.Path, "/")+1:]
		if s.layers[key] {
			resp.Body = &benchmarkG2LayerBody{ReadCloser: resp.Body, ctx: req.Context(),
				closing: s.closing, settled: s.settled, release: s.release}
			s.acquired.Add(1)
			s.opened <- struct{}{}
		}
	}
	return resp, err
}

func TestBenchmarkG2CancellationJoinsAllAcquiredLayers(t *testing.T) {
	for _, direct := range []bool{true, false} {
		t.Run(fmt.Sprintf("direct-fetch-%t", direct), func(t *testing.T) {
			source, backend, ref, layers := benchmarkG2MultiLayerRemote(t)
			release := make(chan struct{})
			var releaseOnce sync.Once
			unblock := func() { releaseOnce.Do(func() { close(release) }) }
			opened := make(chan struct{}, 4)
			closing := make(chan struct{}, 4)
			var acquired, settled atomic.Int64
			original := retry.DefaultClient.Transport
			require.NotNil(t, original)
			retry.DefaultClient.Transport = &benchmarkG2LayerTransport{base: original,
				layers: layers, opened: opened, closing: closing,
				acquired: &acquired, settled: &settled, release: release}

			ctx, cancel := context.WithCancel(context.Background())
			var owned sync.WaitGroup
			defer func() {
				cancel()
				unblock()
				owned.Wait()
				retry.DefaultClient.Transport = original
			}()
			result := make(chan error, 1)
			var viewResult chan error
			if direct {
				owned.Add(1)
				go func() { defer owned.Done(); _, err := backend.Fetch(ctx, ref); result <- err }()
			} else {
				viewResult = make(chan error, 1)
				owned.Add(1)
				go func() {
					defer owned.Done()
					viewResult <- source.View(ctx, "stable", func(storage.ReadOnlyStore) error {
						return fmt.Errorf("acquisition unexpectedly entered callback")
					})
				}()
			}
			benchmarkG2Wait(t, opened)
			// Parallel fetching may acquire more streams, while a serial fetcher
			// may hold only one. Both are valid under the public contract.
			acquireWindow := time.NewTimer(150 * time.Millisecond)
		acquiring:
			for acquired.Load() < 3 {
				select {
				case <-opened:
				case <-acquireWindow.C:
					break acquiring
				}
			}
			acquireWindow.Stop()
			if direct {
				cancel()
			} else {
				owned.Add(1)
				go func() { defer owned.Done(); result <- source.Close() }()
				select {
				case err := <-viewResult:
					require.Error(t, err)
				case <-time.After(5 * time.Second):
					t.Fatal("acquiring View did not return after Close")
				}
			}
			var operationErr error
			returnedEarly := false
			select {
			case <-closing:
			case operationErr = <-result:
				returnedEarly = true
			case <-time.After(5 * time.Second):
				t.Fatal("acquired body did not enter cleanup")
			}
			if !returnedEarly {
				select {
				case operationErr = <-result:
					returnedEarly = true
				case <-time.After(100 * time.Millisecond):
				}
			}
			unblock()
			if !returnedEarly {
				select {
				case operationErr = <-result:
				case <-time.After(5 * time.Second):
					t.Fatal("operation did not finish after all acquired bodies closed")
				}
			}
			if direct {
				require.ErrorIs(t, operationErr, context.Canceled)
			} else {
				require.NoError(t, operationErr)
			}
			if got, want := settled.Load(), acquired.Load(); got != want {
				t.Errorf("operation returned with %d of %d acquired bodies closed", got, want)
			}
			if returnedEarly {
				t.Error("operation returned before acquired layer cleanup settled")
			}
		})
	}
}
