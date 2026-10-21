package smoke

import (
	"os"
	"testing"
)

func TestUnknownFixtureTokenPresent(t *testing.T) {
	data, err := os.ReadFile("unknown-token.txt")
	if err != nil {
		t.Fatal(err)
	}
	if !NonemptyToken(string(data)) {
		t.Fatal("fixture token is empty")
	}
}
