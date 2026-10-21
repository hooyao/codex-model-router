package store

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path"
	"strconv"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	v1 "github.com/opencontainers/image-spec/specs-go/v1"
	"github.com/stretchr/testify/require"
	"go.flipt.io/flipt/internal/config"
	"go.flipt.io/flipt/internal/oci"
	"go.flipt.io/flipt/internal/storage"
	"go.uber.org/zap"
	"oras.land/oras-go/v2/content"
	ociLayout "oras.land/oras-go/v2/content/oci"
)

// Exercise the configured factory rather than constructing a snapshot store
// directly. The authenticated registry is local and uses no external service.
func TestBenchmarkOracleFactoryReferenceAndStaticAuthentication(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	dir := t.TempDir()
	builder, err := oci.NewStore(zap.NewNop(), dir)
	require.NoError(t, err)
	build := func(tag, key string) {
		ref, parseErr := oci.ParseReference("flipt://local/features:" + tag)
		require.NoError(t, parseErr)
		_, buildErr := builder.Build(ctx, fstest.MapFS{"features.yml": &fstest.MapFile{
			Data: []byte(`{"namespace":"production","flags":[{"key":"` + key + `","name":"Flag"}]}`), Mode: 0444,
		}}, ref)
		require.NoError(t, buildErr)
	}
	build("stable", "old")
	build("latest", "new")
	layout, err := ociLayout.New(path.Join(dir, "features"))
	require.NoError(t, err)
	layout.AutoSaveIndex = true
	old, err := layout.Resolve(ctx, "stable")
	require.NoError(t, err)
	descriptors := map[string]v1.Descriptor{}
	for _, tag := range []string{"stable", "latest"} {
		manifestDesc, resolveErr := layout.Resolve(ctx, tag)
		require.NoError(t, resolveErr)
		descriptors[tag], descriptors[manifestDesc.Digest.String()] = manifestDesc, manifestDesc
		manifestBytes, fetchErr := content.FetchAll(ctx, layout, manifestDesc)
		require.NoError(t, fetchErr)
		var manifest v1.Manifest
		require.NoError(t, json.Unmarshal(manifestBytes, &manifest))
		for _, item := range append([]v1.Descriptor{manifest.Config}, manifest.Layers...) {
			descriptors[item.Digest.String()] = item
		}
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		user, password, ok := req.BasicAuth()
		if !ok || user != "fixture-user" || password != "fixture-password" {
			w.Header().Set("WWW-Authenticate", `Basic realm="benchmark"`)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		key := req.URL.Path[strings.LastIndex(req.URL.Path, "/")+1:]
		desc, found := descriptors[key]
		if !found {
			http.NotFound(w, req)
			return
		}
		body, fetchErr := content.FetchAll(req.Context(), layout, desc)
		if fetchErr != nil {
			http.Error(w, fetchErr.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Docker-Content-Digest", desc.Digest.String())
		w.Header().Set("Content-Type", desc.MediaType)
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		if req.Method != http.MethodHead {
			_, _ = w.Write(body)
		}
	}))
	defer server.Close()
	var cfg config.Config
	cfg.Storage.Type = config.OCIStorageType
	cfg.Storage.OCI = &config.OCI{
		Repository: server.URL + "/features:latest", BundlesDirectory: dir,
		PollInterval: time.Hour,
		Authentication: &config.OCIAuthentication{Type: oci.AuthenticationTypeStatic,
			Username: "fixture-user", Password: "fixture-password"},
	}
	store, err := NewStore(ctx, zap.NewNop(), &cfg)
	require.NoError(t, err)
	for _, test := range []struct{ ref, key string }{
		{"", "new"}, {"stable", "old"}, {old.Digest.String(), "old"},
	} {
		request := storage.NewResource("production", test.key)
		request.Reference = storage.Reference(test.ref)
		flag, getErr := store.GetFlag(ctx, request)
		require.NoError(t, getErr, "reference %q", test.ref)
		require.Equal(t, test.key, flag.Key)
	}
	invalid := storage.NewResource("production", "new")
	invalid.Reference = "another/repository:latest"
	_, err = store.GetFlag(ctx, invalid)
	require.Error(t, err)
	bad := cfg
	bad.Storage.OCI = &config.OCI{
		Repository: cfg.Storage.OCI.Repository, BundlesDirectory: dir,
		PollInterval: time.Hour,
		Authentication: &config.OCIAuthentication{Type: oci.AuthenticationTypeStatic,
			Username: "wrong-user", Password: "wrong-password"},
	}
	_, err = NewStore(ctx, zap.NewNop(), &bad)
	require.Error(t, err, "configured credentials must be used for initial acquisition")
}
