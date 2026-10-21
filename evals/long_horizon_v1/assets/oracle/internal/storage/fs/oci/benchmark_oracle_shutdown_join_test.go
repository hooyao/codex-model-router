package oci

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.flipt.io/flipt/internal/storage"
	"oras.land/oras-go/v2/registry/remote/retry"
)

// A response body remains an acquired resource until its Close returns. The
// latch is at the HTTP boundary so the test works whether a candidate fetches
// the tag directly or resolves it before fetching a manifest by digest.
type benchmarkShutdownBody struct {
	io.ReadCloser
	entered chan struct{}
	release <-chan struct{}
	settled chan struct{}
	once    sync.Once
	err     error
}

func (b *benchmarkShutdownBody) Close() error {
	b.once.Do(func() {
		close(b.entered)
		<-b.release
		b.err = b.ReadCloser.Close()
		close(b.settled)
	})
	return b.err
}

type benchmarkShutdownTransport struct {
	base           http.RoundTripper
	manifestDigest string
	entered        chan struct{}
	release        <-chan struct{}
	settled        chan struct{}
	cancelled      chan struct{}
	stopWatch      <-chan struct{}
	once           sync.Once
}

func (s *benchmarkShutdownTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := s.base.RoundTrip(req)
	if err != nil || resp == nil || resp.StatusCode != http.StatusOK || req.Method != http.MethodGet {
		return resp, err
	}
	const manifestPath = "/v2/fixture/manifests/"
	if !strings.HasPrefix(req.URL.Path, manifestPath) {
		return resp, nil
	}
	requested := strings.TrimPrefix(req.URL.Path, manifestPath)
	if requested != "stable" && requested != s.manifestDigest {
		return resp, nil
	}
	s.once.Do(func() {
		resp.Body = &benchmarkShutdownBody{
			ReadCloser: resp.Body, entered: s.entered, release: s.release, settled: s.settled,
		}
		go func() {
			select {
			case <-req.Context().Done():
				close(s.cancelled)
			case <-s.stopWatch:
			}
		}()
	})
	return resp, nil
}

func TestBenchmarkOracleCloseWaitsForAcquisitionCleanup(t *testing.T) {
	r, source := benchmarkOracleRemote(t, time.Hour)
	manifest := r.bundle(t, `{"namespace":"production","flags":[{"key":"shutdown","name":"Shutdown"}]}`)
	r.tag("stable", manifest)

	cleanupEntered := make(chan struct{})
	releaseCleanup := make(chan struct{})
	cleanupSettled := make(chan struct{})
	requestCancelled := make(chan struct{})
	stopWatch := make(chan struct{})
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(releaseCleanup) }) }
	// The common ORAS client is scoped to this non-parallel test and restored
	// before benchmarkOracleRemote's cleanup closes the source again.
	original := retry.DefaultClient.Transport
	require.NotNil(t, original)
	retry.DefaultClient.Transport = &benchmarkShutdownTransport{
		base: original, manifestDigest: manifest.Digest.String(),
		entered: cleanupEntered, release: releaseCleanup, settled: cleanupSettled,
		cancelled: requestCancelled, stopWatch: stopWatch,
	}
	defer func() {
		release()
		close(stopWatch)
		retry.DefaultClient.Transport = original
	}()

	callbackEntered := make(chan struct{}, 1)
	viewResult := make(chan error, 1)
	go func() {
		viewResult <- source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
			callbackEntered <- struct{}{}
			return nil
		})
	}()
	benchmarkOracleSignal(t, cleanupEntered)
	select {
	case <-callbackEntered:
		t.Fatal("callback entered before acquisition cleanup")
	default:
	}

	closeResult := make(chan error, 1)
	go func() { closeResult <- source.Close() }()
	benchmarkOracleSignal(t, requestCancelled)
	var closeErr error
	closedEarly := false
	select {
	case closeErr = <-closeResult:
		closedEarly = true
	case <-time.After(250 * time.Millisecond):
	}
	release()
	benchmarkOracleSignal(t, cleanupSettled)
	if !closedEarly {
		closeErr = benchmarkOracleWithin(t, closeResult)
	}
	require.NoError(t, closeErr)
	viewErr := benchmarkOracleWithin(t, viewResult)
	require.Error(t, viewErr)
	// Learn the store's documented closed error through the public View
	// contract. Its exported identifier is not part of the task requirement.
	postCloseErr := source.View(context.Background(), "stable", func(storage.ReadOnlyStore) error {
		callbackEntered <- struct{}{}
		return nil
	})
	require.Error(t, postCloseErr)
	select {
	case <-callbackEntered:
		t.Fatal("cancelled or post-close View entered callback")
	default:
	}
	require.True(t, errors.Is(viewErr, context.Canceled) || errors.Is(viewErr, postCloseErr),
		"acquisition error %v must be cancellation or match post-close error %v", viewErr, postCloseErr)
	if closedEarly {
		t.Error("Close returned before acquisition cleanup settled")
	}
}
