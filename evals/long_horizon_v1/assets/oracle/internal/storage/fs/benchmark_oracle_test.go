package fs

import (
	"errors"
	"io"
	"io/fs"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

type benchmarkFile struct {
	reader  io.Reader
	closed  int
	name    string
	statErr error
}

func (f *benchmarkFile) Read(p []byte) (int, error) { return f.reader.Read(p) }
func (f *benchmarkFile) Close() error               { f.closed++; return nil }
func (f *benchmarkFile) Stat() (fs.FileInfo, error) { return benchmarkInfo(f.name), f.statErr }

type benchmarkInfo string

func (i benchmarkInfo) Name() string       { return string(i) }
func (i benchmarkInfo) Size() int64        { return 0 }
func (i benchmarkInfo) Mode() fs.FileMode  { return 0444 }
func (i benchmarkInfo) ModTime() time.Time { return time.Time{} }
func (i benchmarkInfo) IsDir() bool        { return false }
func (i benchmarkInfo) Sys() any           { return nil }

func TestBenchmarkOracleAllSuppliedStreamsCloseOnFailure(t *testing.T) {
	first := &benchmarkFile{reader: strings.NewReader(`{"namespace":"alpha"}`), name: "a.json"}
	broken := &benchmarkFile{reader: strings.NewReader(`not-json`), name: "b.json"}
	later := &benchmarkFile{reader: strings.NewReader(`{"namespace":"gamma"}`), name: "c.json"}
	_, err := SnapshotFromFiles(zap.NewNop(), []fs.File{first, broken, later})
	require.Error(t, err)
	for _, file := range []*benchmarkFile{first, broken, later} {
		require.Equal(t, 1, file.closed, "%s must be closed once", file.name)
	}
	// Repeat at earlier failure stages. The unparsed third stream is already
	// owned by SnapshotFromFiles and must be closed in every case.
	sourceErr := errors.New("instrumented source failure")
	for _, stage := range []string{"stat", "partial-read", "validation", "success"} {
		t.Run(stage, func(t *testing.T) {
			first := &benchmarkFile{reader: strings.NewReader(`{"namespace":"alpha"}`), name: "a.json"}
			middle := &benchmarkFile{reader: strings.NewReader(`{"namespace":"beta"}`), name: "b.json"}
			last := &benchmarkFile{reader: strings.NewReader(`{"namespace":"gamma"}`), name: "c.json"}
			switch stage {
			case "stat":
				middle.statErr = sourceErr
			case "partial-read":
				middle.reader = benchmarkPartialReader{err: sourceErr}
			case "validation":
				middle.reader = strings.NewReader(`{"namespace":"beta","flags":[{"key":"incomplete"}]}`)
			}
			_, err := SnapshotFromFiles(zap.NewNop(), []fs.File{first, middle, last})
			if stage == "success" {
				require.NoError(t, err)
			} else {
				require.Error(t, err)
			}
			if stage == "stat" {
				require.ErrorIs(t, err, sourceErr)
			}
			if stage == "partial-read" {
				require.ErrorContains(t, err, sourceErr.Error())
			}
			for _, file := range []*benchmarkFile{first, middle, last} {
				require.Equal(t, 1, file.closed, "%s must be closed once", file.name)
			}
		})
	}
}

type benchmarkPartialReader struct {
	sent bool
	err  error
}

func (r benchmarkPartialReader) Read(p []byte) (int, error) {
	if !r.sent {
		return copy(p, []byte(`{"namespace":`)), r.err
	}
	return 0, r.err
}

var _ io.Closer = (*benchmarkFile)(nil)
var _ fs.File = (*benchmarkFile)(nil)
