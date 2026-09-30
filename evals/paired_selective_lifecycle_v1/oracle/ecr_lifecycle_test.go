package ecr_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	provider "go.flipt.io/flipt/internal/oci/ecr"
	"oras.land/oras-go/v2/registry/remote/auth"
)

const lifecycleA = "111111111111.dkr.ecr.us-east-1.amazonaws.com"
const lifecycleB = "222222222222.dkr.ecr.us-east-1.amazonaws.com"

type lifecycleResult struct {
	credential auth.Credential
	err        error
}

func lifecycleEndpoint(t *testing.T, handler http.HandlerFunc) *atomic.Int32 {
	t.Helper()
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "AWS_") {
			t.Setenv(key, "")
		}
	}
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("X-Amz-Target") != "AmazonEC2ContainerRegistry_V20150921.GetAuthorizationToken" {
			t.Errorf("unexpected ECR operation: %q", r.Header.Get("X-Amz-Target"))
		}
		handler(w, r)
	}))
	t.Cleanup(server.Close)
	dir := t.TempDir()
	t.Setenv("AWS_CONFIG_FILE", filepath.Join(dir, "config"))
	t.Setenv("AWS_SHARED_CREDENTIALS_FILE", filepath.Join(dir, "credentials"))
	t.Setenv("AWS_EC2_METADATA_DISABLED", "true")
	t.Setenv("AWS_ACCESS_KEY_ID", "lifecycle-test-key")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "lifecycle-test-secret")
	t.Setenv("AWS_REGION", "us-east-1")
	t.Setenv("AWS_MAX_ATTEMPTS", "1")
	t.Setenv("AWS_ENDPOINT_URL_ECR", server.URL)
	return &calls
}

func lifecycleRecord(host, password string, expiry *time.Time) map[string]any {
	record := map[string]any{
		"authorizationToken": base64.StdEncoding.EncodeToString([]byte("AWS:" + password)),
		"proxyEndpoint":      "https://" + host + "/",
	}
	if expiry != nil {
		record["expiresAt"] = float64(expiry.UnixNano()) / 1e9
	}
	return record
}

func lifecycleReply(w http.ResponseWriter, records ...map[string]any) {
	w.Header().Set("Content-Type", "application/x-amz-json-1.1")
	_ = json.NewEncoder(w).Encode(map[string]any{"authorizationData": records})
}

func lifecycleAsync(ctx context.Context, call auth.CredentialFunc, host string) <-chan lifecycleResult {
	result := make(chan lifecycleResult, 1)
	go func() {
		credential, err := call(ctx, host)
		result <- lifecycleResult{credential, err}
	}()
	return result
}

func lifecycleAwait(t *testing.T, result <-chan lifecycleResult) lifecycleResult {
	t.Helper()
	select {
	case value := <-result:
		return value
	case <-time.After(3 * time.Second):
		t.Fatal("credential lookup did not complete")
		return lifecycleResult{}
	}
}

func lifecycleWant(t *testing.T, value lifecycleResult, password string) {
	t.Helper()
	if value.err != nil || value.credential.Username != "AWS" || value.credential.Password != password {
		t.Fatalf("got credential=%+v error=%v; want AWS:%s", value.credential, value.err, password)
	}
}

func lifecycleCall(ctx context.Context, call auth.CredentialFunc, host string) lifecycleResult {
	credential, err := call(ctx, host)
	return lifecycleResult{credential, err}
}

func TestLifecycleReuseAndExpiry(t *testing.T) {
	expiry := time.Now().Add(1500 * time.Millisecond)
	var sequence atomic.Int32
	calls := lifecycleEndpoint(t, func(w http.ResponseWriter, _ *http.Request) {
		n := sequence.Add(1)
		until := expiry
		if n > 1 {
			until = time.Now().Add(time.Hour)
		}
		lifecycleReply(w, lifecycleRecord(lifecycleA, fmt.Sprintf("token-%d", n), &until))
	})
	p := &provider.ECR{}
	ctx := context.Background()
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleA), "token-1")
	lifecycleWant(t, lifecycleCall(ctx, p.CredentialFunc(lifecycleA), lifecycleA), "token-1")
	lifecycleWant(t, lifecycleCall(ctx, p.CredentialFunc(lifecycleA), lifecycleA), "token-1")
	if calls.Load() != 1 {
		t.Fatalf("valid credential was fetched %d times", calls.Load())
	}
	time.Sleep(time.Until(expiry.Add(80 * time.Millisecond)))
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleA), "token-2")
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleA), "token-2")
	if calls.Load() != 2 {
		t.Fatalf("expiry should cause one refresh; got %d requests", calls.Load())
	}
}

func TestLifecycleRegistrySelectionAndIsolation(t *testing.T) {
	expiry := time.Now().Add(time.Hour)
	calls := lifecycleEndpoint(t, func(w http.ResponseWriter, _ *http.Request) {
		lifecycleReply(w, lifecycleRecord(lifecycleB, "B-only", &expiry), lifecycleRecord(lifecycleA, "A-only", &expiry))
	})
	p := &provider.ECR{}
	ctx := context.Background()
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleA), "A-only")
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleB), "B-only")
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleA), "A-only")
	lifecycleWant(t, lifecycleCall(ctx, p.Credential, lifecycleB), "B-only")
	// A provider may retain all correctly scoped records from one response.
	if calls.Load() < 1 || calls.Load() > 2 {
		t.Fatalf("valid registry credentials were fetched redundantly: %d", calls.Load())
	}
	before := calls.Load()
	value := lifecycleCall(ctx, p.CredentialFunc(lifecycleA), lifecycleB)
	if value.err != nil || value.credential != auth.EmptyCredential || calls.Load() != before {
		t.Fatalf("bound function leaked credentials or performed a fetch: %+v", value)
	}
	value = lifecycleCall(ctx, p.Credential, "333333333333.dkr.ecr.us-east-1.amazonaws.com")
	if value.err == nil || value.credential != auth.EmptyCredential {
		t.Fatalf("unmatched registry received credentials: %+v", value)
	}
}

func TestLifecycleConcurrentRefresh(t *testing.T) {
	expiry := time.Now().Add(time.Hour)
	entered, release := make(chan struct{}, 32), make(chan struct{})
	var once sync.Once
	finish := func() { once.Do(func() { close(release) }) }
	calls := lifecycleEndpoint(t, func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		select {
		case <-release:
			lifecycleReply(w, lifecycleRecord(lifecycleA, "shared", &expiry))
		case <-r.Context().Done():
		}
	})
	defer finish()
	p := &provider.ECR{}
	results := make([]<-chan lifecycleResult, 16)
	for i := range results {
		call := p.CredentialFunc(lifecycleA)
		if i%2 == 0 {
			call = p.Credential
		}
		results[i] = lifecycleAsync(context.Background(), call, lifecycleA)
	}
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("refresh never reached AWS endpoint")
	}
	time.Sleep(100 * time.Millisecond)
	if calls.Load() != 1 {
		t.Errorf("overlapping requests started %d fetches", calls.Load())
	}
	finish()
	for _, result := range results {
		lifecycleWant(t, lifecycleAwait(t, result), "shared")
	}
	if calls.Load() != 1 {
		t.Fatalf("one refresh wave made %d AWS requests", calls.Load())
	}
}

func TestLifecycleIndependentRegistryProgress(t *testing.T) {
	expiry := time.Now().Add(time.Hour)
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	finish := func() { once.Do(func() { close(release) }) }
	var sequence atomic.Int32
	lifecycleEndpoint(t, func(w http.ResponseWriter, r *http.Request) {
		if sequence.Add(1) == 1 {
			close(entered)
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
		}
		lifecycleReply(w, lifecycleRecord(lifecycleA, "A", &expiry), lifecycleRecord(lifecycleB, "B", &expiry))
	})
	defer finish()
	p := &provider.ECR{}
	a := lifecycleAsync(context.Background(), p.Credential, lifecycleA)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("registry A fetch never began")
	}
	b := lifecycleAsync(context.Background(), p.Credential, lifecycleB)
	select {
	case value := <-b:
		lifecycleWant(t, value, "B")
	case <-time.After(time.Second):
		t.Error("registry B was blocked by registry A")
	}
	finish()
	lifecycleWant(t, lifecycleAwait(t, a), "A")
}

func TestLifecycleSharedFailureAndRetry(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	finish := func() { once.Do(func() { close(release) }) }
	var sequence atomic.Int32
	calls := lifecycleEndpoint(t, func(w http.ResponseWriter, r *http.Request) {
		if sequence.Add(1) == 1 {
			close(entered)
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
			w.Header().Set("Content-Type", "application/x-amz-json-1.1")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"__type":"InvalidParameterException","message":"shared failure"}`))
			return
		}
		expiry := time.Now().Add(time.Hour)
		lifecycleReply(w, lifecycleRecord(lifecycleA, "retry", &expiry))
	})
	defer finish()
	p := &provider.ECR{}
	results := make([]<-chan lifecycleResult, 8)
	for i := range results {
		results[i] = lifecycleAsync(context.Background(), p.Credential, lifecycleA)
	}
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("refresh never began")
	}
	time.Sleep(100 * time.Millisecond)
	finish()
	for _, result := range results {
		value := lifecycleAwait(t, result)
		if value.err == nil || value.credential != auth.EmptyCredential || !strings.Contains(value.err.Error(), "shared failure") {
			t.Errorf("overlapping caller did not share refresh error: %+v", value)
		}
	}
	if calls.Load() != 1 {
		t.Errorf("failed refresh wave made %d requests", calls.Load())
	}
	lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "retry")
	lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "retry")
	if calls.Load() != 2 {
		t.Errorf("subsequent retry was not cached; calls=%d", calls.Load())
	}
}

func TestLifecycleCancellation(t *testing.T) {
	for _, initiator := range []bool{false, true} {
		t.Run(fmt.Sprintf("cancel-initiator-%t", initiator), func(t *testing.T) {
			expiry := time.Now().Add(time.Hour)
			entered, release := make(chan struct{}, 8), make(chan struct{})
			var once sync.Once
			finish := func() { once.Do(func() { close(release) }) }
			calls := lifecycleEndpoint(t, func(w http.ResponseWriter, r *http.Request) {
				entered <- struct{}{}
				select {
				case <-release:
					lifecycleReply(w, lifecycleRecord(lifecycleA, "survivor", &expiry))
				case <-r.Context().Done():
				}
			})
			defer finish()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			p := &provider.ECR{}
			first, second := context.Background(), ctx
			if initiator {
				first, second = ctx, context.Background()
			}
			one := lifecycleAsync(first, p.CredentialFunc(lifecycleA), lifecycleA)
			select {
			case <-entered:
			case <-time.After(3 * time.Second):
				t.Fatal("refresh never began")
			}
			two := lifecycleAsync(second, p.Credential, lifecycleA)
			time.Sleep(100 * time.Millisecond)
			cancel()
			cancelled, survivor := two, one
			if initiator {
				cancelled, survivor = one, two
			}
			select {
			case value := <-cancelled:
				if !errors.Is(value.err, context.Canceled) || value.credential != auth.EmptyCredential {
					t.Errorf("cancelled caller got %+v", value)
				}
			case <-time.After(time.Second):
				t.Error("cancelled caller remained blocked by AWS request")
			}
			finish()
			lifecycleWant(t, lifecycleAwait(t, survivor), "survivor")
			lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "survivor")
			if calls.Load() != 1 {
				t.Errorf("cancelling one caller caused %d fetches", calls.Load())
			}
		})
	}
}

func TestLifecycleAlreadyCancelled(t *testing.T) {
	expiry := time.Now().Add(time.Hour)
	calls := lifecycleEndpoint(t, func(w http.ResponseWriter, _ *http.Request) {
		lifecycleReply(w, lifecycleRecord(lifecycleA, "cached", &expiry))
	})
	p := &provider.ECR{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	for _, warmed := range []bool{false, true} {
		if warmed {
			lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "cached")
		}
		before := calls.Load()
		value := lifecycleCall(ctx, p.Credential, lifecycleA)
		if !errors.Is(value.err, context.Canceled) || value.credential != auth.EmptyCredential || calls.Load() != before {
			t.Errorf("already cancelled call (warm=%t) returned %+v, requests %d -> %d", warmed, value, before, calls.Load())
		}
	}
}

func TestLifecycleFailedRefreshRecovery(t *testing.T) {
	expiry := time.Now().Add(1500 * time.Millisecond)
	var sequence atomic.Int32
	calls := lifecycleEndpoint(t, func(w http.ResponseWriter, _ *http.Request) {
		n := sequence.Add(1)
		if n == 2 {
			w.Header().Set("Content-Type", "application/x-amz-json-1.1")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"__type":"InvalidParameterException","message":"lifecycle failure"}`))
			return
		}
		until := expiry
		if n > 2 {
			until = time.Now().Add(time.Hour)
		}
		lifecycleReply(w, lifecycleRecord(lifecycleA, fmt.Sprintf("token-%d", n), &until))
	})
	p := &provider.ECR{}
	lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "token-1")
	time.Sleep(time.Until(expiry.Add(80 * time.Millisecond)))
	failed := lifecycleCall(context.Background(), p.Credential, lifecycleA)
	if failed.err == nil || failed.credential != auth.EmptyCredential {
		t.Fatalf("refresh failure exposed stale credentials: %+v", failed)
	}
	lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "token-3")
	lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "token-3")
	if calls.Load() != 3 {
		t.Fatalf("failure prevented successful caching, requests=%d", calls.Load())
	}
}

func TestLifecycleInvalidResponsesAndMissingExpiry(t *testing.T) {
	for _, kind := range []string{"missing-expiry", "expired", "malformed", "missing-records", "wrong-endpoint"} {
		t.Run(kind, func(t *testing.T) {
			var sequence atomic.Int32
			calls := lifecycleEndpoint(t, func(w http.ResponseWriter, _ *http.Request) {
				n := sequence.Add(1)
				expiry := time.Now().Add(time.Hour)
				record := lifecycleRecord(lifecycleA, "recovered", &expiry)
				if n == 1 {
					switch kind {
					case "missing-expiry":
						record = lifecycleRecord(lifecycleA, "one-shot", nil)
					case "expired":
						past := time.Now().Add(-time.Hour)
						record = lifecycleRecord(lifecycleA, "expired", &past)
					case "malformed":
						record["authorizationToken"] = "!invalid-base64"
					case "missing-records":
						lifecycleReply(w)
						return
					case "wrong-endpoint":
						record["proxyEndpoint"] = "https://" + lifecycleB
					}
				}
				lifecycleReply(w, record)
			})
			p := &provider.ECR{}
			first := lifecycleCall(context.Background(), p.Credential, lifecycleA)
			if kind == "missing-expiry" {
				lifecycleWant(t, first, "one-shot")
			} else if first.err == nil || first.credential != auth.EmptyCredential {
				t.Fatalf("invalid response accepted: %+v", first)
			}
			lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "recovered")
			lifecycleWant(t, lifecycleCall(context.Background(), p.Credential, lifecycleA), "recovered")
			if calls.Load() != 2 {
				t.Fatalf("recovered token was not cached; calls=%d", calls.Load())
			}
		})
	}
}
