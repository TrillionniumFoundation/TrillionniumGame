// SPDX-License-Identifier: Apache-2.0
// Command go_runtime_surface extracts syntactic runtime surfaces from pinned Go source.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Item struct {
	Class     string            `json:"class"`
	Symbol    string            `json:"symbol"`
	Signature string            `json:"signature"`
	Path      string            `json:"path"`
	StartLine int               `json:"start_line"`
	EndLine   int               `json:"end_line"`
	Metadata  map[string]string `json:"metadata,omitempty"`
}

type ManualContract struct {
	Class     string `json:"class"`
	Symbol    string `json:"symbol"`
	Path      string `json:"path"`
	StartLine int    `json:"start_line"`
	EndLine   int    `json:"end_line"`
	Reason    string `json:"reason"`
}

type Output struct {
	Schema          string           `json:"schema"`
	Items           []Item           `json:"items"`
	ManualContracts []ManualContract `json:"manual_contracts"`
}

func nodeString(fset *token.FileSet, node any) (string, error) {
	var builder strings.Builder
	if err := format.Node(&builder, fset, node); err != nil {
		return "", err
	}
	return strings.Join(strings.Fields(builder.String()), " "), nil
}

func lineRange(fset *token.FileSet, node ast.Node) (int, int) {
	return fset.Position(node.Pos()).Line, fset.Position(node.End()).Line
}

func exported(name string) bool { return ast.IsExported(name) }

func appendItem(items *[]Item, fset *token.FileSet, path, class, symbol string, node ast.Node, signature string, metadata map[string]string) {
	start, end := lineRange(fset, node)
	*items = append(*items, Item{Class: class, Symbol: symbol, Signature: signature, Path: path, StartLine: start, EndLine: end, Metadata: metadata})
}

func parseFile(root, path string, includeUnexported bool, items *[]Item, manual *[]ManualContract) error {
	absolute := filepath.Join(root, filepath.FromSlash(path))
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, absolute, nil, parser.ParseComments|parser.AllErrors)
	if err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}
	packageName := file.Name.Name
	for _, declaration := range file.Decls {
		switch decl := declaration.(type) {
		case *ast.FuncDecl:
			if decl.Recv != nil || (!includeUnexported && !exported(decl.Name.Name)) {
				continue
			}
			signature, err := nodeString(fset, decl.Type)
			if err != nil {
				return fmt.Errorf("format function %s: %w", decl.Name.Name, err)
			}
			appendItem(items, fset, path, "go_function", packageName+"."+decl.Name.Name, decl, signature, nil)
		case *ast.GenDecl:
			switch decl.Tok {
			case token.TYPE:
				for _, specNode := range decl.Specs {
					spec, ok := specNode.(*ast.TypeSpec)
					if !ok || (!includeUnexported && !exported(spec.Name.Name)) {
						continue
					}
					full := packageName + "." + spec.Name.Name
					signature, err := nodeString(fset, spec.Type)
					if err != nil {
						return fmt.Errorf("format type %s: %w", full, err)
					}
					class := "go_type"
					switch typed := spec.Type.(type) {
					case *ast.InterfaceType:
						class = "go_interface"
						appendItem(items, fset, path, class, full, spec, signature, nil)
						for _, field := range typed.Methods.List {
							fieldSignature, err := nodeString(fset, field.Type)
							if err != nil {
								return fmt.Errorf("format interface field %s: %w", full, err)
							}
							if len(field.Names) == 0 {
								appendItem(items, fset, path, "go_embedded_interface", full+"."+fieldSignature, field, fieldSignature, nil)
								continue
							}
							for _, name := range field.Names {
								if !includeUnexported && !exported(name.Name) {
									continue
								}
								appendItem(items, fset, path, "go_interface_method", full+"."+name.Name, field, fieldSignature, nil)
							}
						}
						continue
					case *ast.FuncType:
						class = "go_function_type"
					case *ast.StructType:
						class = "go_struct"
						appendItem(items, fset, path, class, full, spec, signature, nil)
						for _, field := range typed.Fields.List {
							fieldSignature, err := nodeString(fset, field.Type)
							if err != nil {
								return fmt.Errorf("format struct field %s: %w", full, err)
							}
							metadata := map[string]string{}
							if field.Tag != nil {
								metadata["tag"] = field.Tag.Value
							}
							if len(field.Names) == 0 {
								appendItem(items, fset, path, "go_embedded_field", full+"."+fieldSignature, field, fieldSignature, metadata)
								continue
							}
							for _, name := range field.Names {
								if !includeUnexported && !exported(name.Name) {
									continue
								}
								appendItem(items, fset, path, "go_struct_field", full+"."+name.Name, field, fieldSignature, metadata)
							}
						}
						continue
					}
					appendItem(items, fset, path, class, full, spec, signature, nil)
				}
			case token.CONST, token.VAR:
				class := "go_variable"
				if decl.Tok == token.CONST {
					class = "go_constant"
				}
				for _, specNode := range decl.Specs {
					spec, ok := specNode.(*ast.ValueSpec)
					if !ok {
						continue
					}
					typeSignature := ""
					if spec.Type != nil {
						typeSignature, err = nodeString(fset, spec.Type)
						if err != nil {
							return fmt.Errorf("format value type in %s: %w", path, err)
						}
					}
					for index, name := range spec.Names {
						if !includeUnexported && !exported(name.Name) {
							continue
						}
						valueSignature := typeSignature
						if index < len(spec.Values) {
							valueSignature, err = nodeString(fset, spec.Values[index])
							if err != nil {
								return fmt.Errorf("format value %s: %w", name.Name, err)
							}
						} else if len(spec.Values) == 1 {
							valueSignature, err = nodeString(fset, spec.Values[0])
							if err != nil {
								return fmt.Errorf("format value %s: %w", name.Name, err)
							}
						}
						appendItem(items, fset, path, class, packageName+"."+name.Name, spec, valueSignature, nil)
					}
				}
			}
		}
	}
	return nil
}

func main() {
	root := flag.String("root", "", "verified source root")
	output := flag.String("output", "", "output JSON path, or stdout")
	includeUnexported := flag.Bool("include-unexported", false, "include unexported declarations")
	flag.Parse()
	if *root == "" || flag.NArg() == 0 {
		fmt.Fprintln(os.Stderr, "usage: go_runtime_surface --root ROOT [--output PATH] FILE...")
		os.Exit(64)
	}
	items := []Item{}
	manual := []ManualContract{}
	for _, path := range flag.Args() {
		clean := filepath.ToSlash(filepath.Clean(path))
		if strings.HasPrefix(clean, "../") || filepath.IsAbs(path) {
			fmt.Fprintf(os.Stderr, "unsafe source path: %s\n", path)
			os.Exit(64)
		}
		if err := parseFile(*root, clean, *includeUnexported, &items, &manual); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Path != items[j].Path {
			return items[i].Path < items[j].Path
		}
		if items[i].Class != items[j].Class {
			return items[i].Class < items[j].Class
		}
		return items[i].Symbol < items[j].Symbol
	})
	result := Output{Schema: "trillionnium.go-runtime-surface.v1", Items: items, ManualContracts: manual}
	encoded, err := json.Marshal(result)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoded = append(encoded, '\n')
	if *output == "" {
		_, _ = os.Stdout.Write(encoded)
		return
	}
	if err := os.MkdirAll(filepath.Dir(*output), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := os.WriteFile(*output, encoded, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
