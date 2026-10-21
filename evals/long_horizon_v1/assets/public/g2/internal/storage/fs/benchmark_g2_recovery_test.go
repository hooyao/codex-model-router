package fs

import (
	"errors"
	"io"
	iofs "io/fs"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

// These streams are supplied to SnapshotFromFiles before parsing begins.
// Even a stream after the failing one is already owned by the constructor.
type benchmarkG2Stream struct {
	io.Reader
	name    string
	closed  int
	statErr error
}

func (f *benchmarkG2Stream) Close() error { f.closed++; return nil }
func (f *benchmarkG2Stream) Stat() (iofs.FileInfo, error) {
	return benchmarkG2Info(f.name), f.statErr
}

type benchmarkG2Info string

func (i benchmarkG2Info) Name() string        { return string(i) }
func (i benchmarkG2Info) Size() int64         { return 0 }
func (i benchmarkG2Info) Mode() iofs.FileMode { return 0444 }
func (i benchmarkG2Info) ModTime() time.Time  { return time.Time{} }
func (i benchmarkG2Info) IsDir() bool         { return false }
func (i benchmarkG2Info) Sys() any            { return nil }

func TestBenchmarkG2SuppliedStreamsReleasedAfterStatFailure(t *testing.T) {
	cause := errors.New("public fixture stat failure")
	files := []*benchmarkG2Stream{
		{Reader: strings.NewReader(`{"namespace":"first"}`), name: "first.json"},
		{Reader: strings.NewReader(`{"namespace":"middle"}`), name: "middle.json", statErr: cause},
		{Reader: strings.NewReader(`{"namespace":"later"}`), name: "later.json"},
	}
	owned := []iofs.File{files[0], files[1], files[2]}
	_, err := SnapshotFromFiles(zap.NewNop(), owned)
	require.ErrorIs(t, err, cause)
	for _, file := range files {
		require.Equal(t, 1, file.closed, "%s must be closed exactly once", file.name)
	}
}
