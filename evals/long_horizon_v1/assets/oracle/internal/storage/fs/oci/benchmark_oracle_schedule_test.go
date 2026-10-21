package oci

import (
	"context"
	"encoding/json"
	"fmt"
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
)

// This registry holds responses at the HTTP boundary. It does not inspect the
// candidate's acquisition, cache, or poller implementation.
type benchmarkOracleRegistry struct {
	mu    sync.Mutex
	tags  map[string]v1.Descriptor
	blobs map[string][]byte
	descs map[string]v1.Descriptor
	hook  func(*http.Request)
}

func benchmarkOracleRemote(t *testing.T, interval time.Duration) (*benchmarkOracleRegistry, *SnapshotStore) {
	t.Helper()
	r := &benchmarkOracleRegistry{tags: map[string]v1.Descriptor{}, blobs: map[string][]byte{}, descs: map[string]v1.Descriptor{}}
	srv := httptest.NewServer(http.HandlerFunc(r.serve))
	t.Cleanup(srv.Close)
	old := r.bundle(t, `{"namespace":"production","flags":[{"key":"old","name":"Old"}]}`)
	r.tag("latest", old)
	r.tag("stable", old)
	r.tag("canary", old)
	backend, err := fliptoci.NewStore(zaptest.NewLogger(t), t.TempDir())
	require.NoError(t, err)
	ref, err := fliptoci.ParseReference(srv.URL + "/fixture:latest")
	require.NoError(t, err)
	source, err := NewSnapshotStore(context.Background(), zaptest.NewLogger(t), backend, ref,
		WithPollOptions(storagefs.WithInterval(interval)))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, source.Close()) })
	return r, source
}

func (r *benchmarkOracleRegistry) bundle(t *testing.T, payloads ...string) v1.Descriptor {
	t.Helper()
	target := memory.New()
	var layers []v1.Descriptor
	for i, payload := range payloads {
		layers = append(layers, layer(fmt.Sprintf("oracle-%d", i), payload, fliptoci.MediaTypeFliptNamespace)(t, target))
	}
	desc, err := oras.PackManifest(context.Background(), target, oras.PackManifestVersion1_1,
		fliptoci.MediaTypeFliptFeatures, oras.PackManifestOptions{Layers: layers})
	require.NoError(t, err)
	data, err := content.FetchAll(context.Background(), target, desc)
	require.NoError(t, err)
	var manifest v1.Manifest
	require.NoError(t, json.Unmarshal(data, &manifest))
	for _, item := range append([]v1.Descriptor{desc, manifest.Config}, manifest.Layers...) {
		body, fetchErr := content.FetchAll(context.Background(), target, item)
		require.NoError(t, fetchErr)
		r.mu.Lock()
		r.blobs[item.Digest.String()], r.descs[item.Digest.String()] = body, item
		r.mu.Unlock()
	}
	return desc
}

func (r *benchmarkOracleRegistry) tag(name string, desc v1.Descriptor) {
	r.mu.Lock()
	r.tags[name] = desc
	r.mu.Unlock()
}

func (r *benchmarkOracleRegistry) setHook(hook func(*http.Request)) {
	r.mu.Lock()
	r.hook = hook
	r.mu.Unlock()
}

func (r *benchmarkOracleRegistry) serve(w http.ResponseWriter, req *http.Request) {
	key := req.URL.Path[strings.LastIndex(req.URL.Path, "/")+1:]
	r.mu.Lock()
	desc, ok := r.descs[key]
	if !ok {
		desc, ok = r.tags[key]
	}
	data, hook := r.blobs[desc.Digest.String()], r.hook
	r.mu.Unlock()
	if !ok {
		http.NotFound(w, req)
		return
	}
	if hook != nil {
		hook(req)
	}
	w.Header().Set("Docker-Content-Digest", desc.Digest.String())
	w.Header().Set("Content-Type", desc.MediaType)
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	if req.Method != http.MethodHead {
		_, _ = w.Write(data)
	}
}

func benchmarkOracleFlag(source *SnapshotStore, ref, key string) error {
	ctx := context.Background()
	return source.View(ctx, storage.Reference(ref), func(snapshot storage.ReadOnlyStore) error {
		_, err := snapshot.GetFlag(ctx, storage.NewResource("production", key))
		return err
	})
}

func benchmarkOracleWithin(t *testing.T, result <-chan error) error {
	t.Helper()
	select {
	case err := <-result:
		return err
	case <-time.After(5 * time.Second):
		t.Fatal("operation did not finish after barrier release")
		return nil
	}
}

func benchmarkOracleSignal(t *testing.T, event <-chan struct{}) {
	t.Helper()
	select {
	case <-event:
	case <-time.After(5 * time.Second):
		t.Fatal("registry or callback did not reach barrier")
	}
}

// A concurrent second resolution may finish while the first response is held.
// Consume its single result once, whether it arrives before or after release.
func benchmarkOracleSecondAroundRelease(t *testing.T, first, second <-chan error, release func()) (error, bool) {
	t.Helper()
	var secondErr error
	secondCompleted := false
	select {
	case secondErr = <-second:
		secondCompleted = true
	case <-time.After(150 * time.Millisecond):
	}
	release()
	require.NoError(t, benchmarkOracleWithin(t, first)) // An entered old View may show D1.
	if !secondCompleted {
		secondErr = benchmarkOracleWithin(t, second) // Coalescing may hand out D1 once.
	}
	return secondErr, secondCompleted
}

func TestBenchmarkOracleSecondAlreadyCompletedBeforeRelease(t *testing.T) {
	first, second := make(chan error, 1), make(chan error, 1)
	second <- nil // D2 was published before the held D1 response was released.
	released := false
	err, early := benchmarkOracleSecondAroundRelease(t, first, second, func() {
		released = true
		first <- nil
	})
	require.True(t, released)
	require.True(t, early)
	require.NoError(t, err)
}

func TestBenchmarkOracleResolutionOrderingAndIsolation(t *testing.T) {
	r, source := benchmarkOracleRemote(t, time.Hour)
	old := r.tags["stable"]
	newer := r.bundle(t, `{"namespace":"production","flags":[{"key":"new","name":"New"}]}`)
	entered, release := make(chan struct{}), make(chan struct{})
	var firstRequest atomic.Bool
	var releaseOnce sync.Once
	defer releaseOnce.Do(func() { close(release) })
	r.setHook(func(req *http.Request) {
		if (req.Method == http.MethodGet || req.Method == http.MethodHead) && strings.HasSuffix(req.URL.Path, "/stable") && firstRequest.CompareAndSwap(false, true) {
			close(entered)
			<-release
		}
	})
	first := make(chan error, 1)
	go func() { first <- benchmarkOracleFlag(source, "stable", "old") }()
	benchmarkOracleSignal(t, entered)
	r.tag("stable", newer)
	// A held tag fetch must not block unrelated references.
	other := make(chan error, 1)
	go func() { other <- benchmarkOracleFlag(source, "canary", "old") }()
	require.NoError(t, benchmarkOracleWithin(t, other))
	second := make(chan error, 1)
	go func() { second <- benchmarkOracleFlag(source, "stable", "new") }()
	// If a second fetch may run concurrently, it can publish D2 first. If the
	// implementation serializes or coalesces, release D1 and check a fresh View.
	err, early := benchmarkOracleSecondAroundRelease(t, first, second, func() {
		releaseOnce.Do(func() { close(release) })
	})
	if early {
		require.NoError(t, err, "a completed newer resolution must show D2")
	}
	r.setHook(nil)
	require.NoError(t, benchmarkOracleFlag(source, "stable", "new"))
	require.NoError(t, benchmarkOracleFlag(source, "canary", "old"))
	require.NoError(t, benchmarkOracleFlag(source, old.Digest.String(), "old"))
}

func TestBenchmarkOracleCancellationDuringAcquisition(t *testing.T) {
	for _, cancelFirst := range []bool{true, false} {
		t.Run(fmt.Sprintf("cancel-initiator-%t", cancelFirst), func(t *testing.T) {
			r, source := benchmarkOracleRemote(t, time.Hour)
			entered, release := make(chan struct{}), make(chan struct{})
			var enteredOnce, releaseOnce sync.Once
			defer releaseOnce.Do(func() { close(release) })
			r.setHook(func(req *http.Request) {
				if (req.Method == http.MethodGet || req.Method == http.MethodHead) && strings.HasSuffix(req.URL.Path, "/stable") {
					enteredOnce.Do(func() { close(entered) })
					select {
					case <-release:
					case <-req.Context().Done():
					}
				}
			})
			firstCtx, cancelOne := context.WithCancel(context.Background())
			defer cancelOne()
			secondCtx, cancelTwo := context.WithCancel(context.Background())
			defer cancelTwo()
			call := func(ctx context.Context) <-chan error {
				result := make(chan error, 1)
				go func() {
					result <- source.View(ctx, "stable", func(snapshot storage.ReadOnlyStore) error {
						_, err := snapshot.GetFlag(context.Background(), storage.NewResource("production", "old"))
						return err
					})
				}()
				return result
			}
			first := call(firstCtx)
			benchmarkOracleSignal(t, entered)
			second := call(secondCtx)
			if cancelFirst {
				cancelOne()
				require.ErrorIs(t, benchmarkOracleWithin(t, first), context.Canceled)
			} else {
				cancelTwo()
				require.ErrorIs(t, benchmarkOracleWithin(t, second), context.Canceled)
			}
			releaseOnce.Do(func() { close(release) })
			if cancelFirst {
				require.NoError(t, benchmarkOracleWithin(t, second))
			} else {
				require.NoError(t, benchmarkOracleWithin(t, first))
			}
		})
	}
}

func TestBenchmarkOracleHeldCallbackPublicationAndShutdown(t *testing.T) {
	r, source := benchmarkOracleRemote(t, 20*time.Millisecond)
	entered, release := make(chan struct{}), make(chan struct{})
	var releaseOnce sync.Once
	defer releaseOnce.Do(func() { close(release) })
	held := make(chan error, 1)
	go func() {
		held <- source.View(context.Background(), "stable", func(snapshot storage.ReadOnlyStore) error {
			close(entered)
			<-release
			_, err := snapshot.GetFlag(context.Background(), storage.NewResource("production", "old"))
			return err
		})
	}()
	benchmarkOracleSignal(t, entered)
	for i := 0; i < 8; i++ {
		key := fmt.Sprintf("retained-%d", i)
		tag := fmt.Sprintf("inactive-%d", i)
		desc := r.bundle(t, fmt.Sprintf(`{"namespace":"production","flags":[{"key":%q,"name":"Flag"}]}`, key))
		r.tag(tag, desc)
		require.NoError(t, benchmarkOracleFlag(source, tag, key))
	}
	newer := r.bundle(t, `{"namespace":"production","flags":[{"key":"new","name":"New"}]}`)
	r.tag("latest", newer)
	require.Eventually(t, func() bool { return benchmarkOracleFlag(source, "", "new") == nil }, 5*time.Second, 10*time.Millisecond)
	closed := make(chan error, 2)
	go func() { closed <- source.Close() }()
	go func() { closed <- source.Close() }()
	require.NoError(t, benchmarkOracleWithin(t, closed))
	require.NoError(t, benchmarkOracleWithin(t, closed))
	releaseOnce.Do(func() { close(release) })
	require.NoError(t, benchmarkOracleWithin(t, held))
}

func TestBenchmarkOracleCloseCancelsAcquisition(t *testing.T) {
	r, source := benchmarkOracleRemote(t, time.Hour)
	entered, abandoned := make(chan struct{}), make(chan struct{})
	var once sync.Once
	var requests atomic.Int32
	r.setHook(func(req *http.Request) {
		if (req.Method == http.MethodGet || req.Method == http.MethodHead) && strings.HasSuffix(req.URL.Path, "/stable") {
			requests.Add(1)
			once.Do(func() { close(entered) })
			<-req.Context().Done()
			close(abandoned)
		}
	})
	called := make(chan bool, 1)
	view := make(chan error, 1)
	go func() {
		view <- source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
			called <- true
			return nil
		})
	}()
	benchmarkOracleSignal(t, entered)
	closed := make(chan error, 1)
	go func() { closed <- source.Close() }()
	require.Error(t, benchmarkOracleWithin(t, view))
	require.NoError(t, benchmarkOracleWithin(t, closed))
	benchmarkOracleSignal(t, abandoned)
	select {
	case <-called:
		t.Fatal("closed acquisition entered callback")
	default:
	}
	before := requests.Load()
	err := source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
		t.Fatal("post-close View entered callback")
		return nil
	})
	require.Error(t, err)
	require.Equal(t, before, requests.Load(), "post-close View started a registry request")
}

func TestBenchmarkOracleFailedPollRetainsGoodAndRecovers(t *testing.T) {
	r, source := benchmarkOracleRemote(t, 20*time.Millisecond)
	broken := r.bundle(t, `{"namespace":"production","flags":[{"key":"partial","name":"Partial"}]}`, `not-json`)
	seen := make(chan struct{})
	var once sync.Once
	r.setHook(func(req *http.Request) {
		if (req.Method == http.MethodGet || req.Method == http.MethodHead) && strings.HasSuffix(req.URL.Path, "/latest") {
			once.Do(func() { close(seen) })
		}
	})
	r.tag("latest", broken)
	benchmarkOracleSignal(t, seen)
	// Repeat after a full poll interval; a partial namespace must never be
	// published, and an explicit failed reference must report its own error.
	require.Eventually(t, func() bool { return benchmarkOracleFlag(source, "latest", "old") != nil }, 5*time.Second, 10*time.Millisecond)
	require.NoError(t, benchmarkOracleFlag(source, "", "old"))
	require.Error(t, benchmarkOracleFlag(source, "", "partial"))
	require.NoError(t, benchmarkOracleFlag(source, "canary", "old"))
	r.setHook(nil)
	newer := r.bundle(t, `{"namespace":"production","flags":[{"key":"recovered","name":"Recovered"}]}`)
	r.tag("latest", newer)
	require.Eventually(t, func() bool { return benchmarkOracleFlag(source, "", "recovered") == nil }, 5*time.Second, 10*time.Millisecond)
}
