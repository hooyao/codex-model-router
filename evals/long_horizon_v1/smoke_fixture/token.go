package smoke

import "strings"

func NonemptyToken(value string) bool {
	return strings.TrimSpace(value) != ""
}
