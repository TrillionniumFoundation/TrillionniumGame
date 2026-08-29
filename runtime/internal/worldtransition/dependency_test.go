package worldtransition

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"testing"
)

func TestPackageImportsRemainPure(t *testing.T) {
	forbidden := map[string]struct{}{
		"net": {}, "net/http": {}, "database/sql": {}, "crypto/ed25519": {},
		"os/exec": {}, "time": {}, "math/rand": {}, "math/rand/v2": {},
	}
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	set := token.NewFileSet()
	for _, file := range files {
		parsed, err := parser.ParseFile(set, file, nil, ast.ImportsOnly)
		if err != nil {
			t.Fatal(err)
		}
		for _, imported := range parsed.Imports {
			path, err := strconv.Unquote(imported.Path.Value)
			if err != nil {
				t.Fatal(err)
			}
			if _, denied := forbidden[path]; denied {
				t.Fatalf("forbidden capability import %q in %s", path, file)
			}
		}
	}
}
