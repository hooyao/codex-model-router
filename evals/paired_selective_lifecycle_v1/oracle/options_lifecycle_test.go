package oci

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"oras.land/oras-go/v2/registry/remote/auth"
)

func TestLifecycleStaticOptionCompatibility(t *testing.T) {
	var options StoreOptions
	option, err := WithCredentials(AuthenticationTypeStatic, "static-user", "pass:with:colon")
	if err != nil {
		t.Fatal(err)
	}
	option(&options)
	for _, registry := range []string{"first.example", "second.example"} {
		call := options.auth(registry)
		value, err := call(context.Background(), registry)
		if err != nil || value.Username != "static-user" || value.Password != "pass:with:colon" {
			t.Fatalf("static credentials changed: %+v %v", value, err)
		}
		value, err = call(context.Background(), "other.example")
		if err != nil || value != auth.EmptyCredential {
			t.Fatalf("static credentials escaped registry scope: %+v %v", value, err)
		}
	}
}

func TestLifecycleAWSOptionSharesProvider(t *testing.T) {
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "AWS_") {
			t.Setenv(key, "")
		}
	}
	const registry = "111111111111.dkr.ecr.us-east-1.amazonaws.com"
	var calls atomic.Int32
	expiry := time.Now().Add(time.Hour)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/x-amz-json-1.1")
		_ = json.NewEncoder(w).Encode(map[string]any{"authorizationData": []map[string]any{{
			"authorizationToken": base64.StdEncoding.EncodeToString([]byte("AWS:option")),
			"proxyEndpoint":      "https://" + registry,
			"expiresAt":          float64(expiry.UnixNano()) / 1e9,
		}}})
	}))
	defer server.Close()
	dir := t.TempDir()
	t.Setenv("AWS_CONFIG_FILE", filepath.Join(dir, "config"))
	t.Setenv("AWS_SHARED_CREDENTIALS_FILE", filepath.Join(dir, "credentials"))
	t.Setenv("AWS_EC2_METADATA_DISABLED", "true")
	t.Setenv("AWS_ACCESS_KEY_ID", "lifecycle-test-key")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "lifecycle-test-secret")
	t.Setenv("AWS_REGION", "us-east-1")
	t.Setenv("AWS_MAX_ATTEMPTS", "1")
	t.Setenv("AWS_ENDPOINT_URL_ECR", server.URL)
	var options StoreOptions
	WithAWSECRCredentials()(&options)
	for i := 0; i < 3; i++ {
		value, err := options.auth(registry)(context.Background(), registry)
		if err != nil || value.Username != "AWS" || value.Password != "option" {
			t.Fatalf("AWS option credentials: %+v %v", value, err)
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("credential functions from one option fetched %d times", calls.Load())
	}
}
