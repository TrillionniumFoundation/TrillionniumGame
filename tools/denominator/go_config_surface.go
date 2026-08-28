// SPDX-License-Identifier: Apache-2.0
// Command go_config_surface extracts candidate Nakama config and CLI contracts.
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
	"reflect"
	"sort"
	"strconv"
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

type Output struct {
	Schema string `json:"schema"`
	Items  []Item `json:"items"`
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

func appendItem(items *[]Item, fset *token.FileSet, path, class, symbol string, node ast.Node, signature string, metadata map[string]string) {
	start, end := lineRange(fset, node)
	*items = append(*items, Item{Class: class, Symbol: symbol, Signature: signature, Path: path, StartLine: start, EndLine: end, Metadata: metadata})
}

func typeName(expr ast.Expr) string {
	switch value := expr.(type) {
	case *ast.Ident:
		return value.Name
	case *ast.SelectorExpr:
		return typeName(value.X) + "." + value.Sel.Name
	case *ast.StarExpr:
		return "*" + typeName(value.X)
	case *ast.IndexExpr:
		return typeName(value.X)
	case *ast.IndexListExpr:
		return typeName(value.X)
	default:
		return ""
	}
}

func selectorName(expr ast.Expr) string {
	switch value := expr.(type) {
	case *ast.Ident:
		return value.Name
	case *ast.SelectorExpr:
		prefix := selectorName(value.X)
		if prefix == "" {
			return value.Sel.Name
		}
		return prefix + "." + value.Sel.Name
	case *ast.StarExpr:
		return selectorName(value.X)
	case *ast.IndexExpr:
		return selectorName(value.X)
	default:
		return ""
	}
}

func parseTag(literal *ast.BasicLit) map[string]string {
	if literal == nil {
		return nil
	}
	unquoted, err := strconv.Unquote(literal.Value)
	if err != nil {
		return map[string]string{"raw_tag": literal.Value}
	}
	tag := reflect.StructTag(unquoted)
	metadata := map[string]string{"raw_tag": unquoted}
	for _, key := range []string{"yaml", "json", "usage", "env", "mapstructure"} {
		if value, ok := tag.Lookup(key); ok {
			metadata[key] = value
		}
	}
	return metadata
}

func hasConfigTag(structType *ast.StructType) bool {
	for _, field := range structType.Fields.List {
		metadata := parseTag(field.Tag)
		if metadata["yaml"] != "" || metadata["usage"] != "" {
			return true
		}
	}
	return false
}

func parseStructs(fset *token.FileSet, file *ast.File, path string, items *[]Item) error {
	for _, declaration := range file.Decls {
		gen, ok := declaration.(*ast.GenDecl)
		if !ok || gen.Tok != token.TYPE {
			continue
		}
		for _, specNode := range gen.Specs {
			spec, ok := specNode.(*ast.TypeSpec)
			if !ok {
				continue
			}
			full := file.Name.Name + "." + spec.Name.Name
			switch typed := spec.Type.(type) {
			case *ast.StructType:
				if !strings.HasSuffix(spec.Name.Name, "Config") && !hasConfigTag(typed) {
					continue
				}
				signature, err := nodeString(fset, typed)
				if err != nil {
					return err
				}
				appendItem(items, fset, path, "config_type", full, spec, signature, nil)
				for _, field := range typed.Fields.List {
					fieldType, err := nodeString(fset, field.Type)
					if err != nil {
						return err
					}
					metadata := parseTag(field.Tag)
					if len(field.Names) == 0 {
						appendItem(items, fset, path, "config_embedded_field", full+"."+fieldType, field, fieldType, metadata)
						continue
					}
					for _, name := range field.Names {
						if !ast.IsExported(name.Name) {
							continue
						}
						appendItem(items, fset, path, "config_field", full+"."+name.Name, field, fieldType, metadata)
					}
				}
			case *ast.InterfaceType:
				if spec.Name.Name != "Config" && !strings.HasSuffix(spec.Name.Name, "Config") {
					continue
				}
				signature, err := nodeString(fset, typed)
				if err != nil {
					return err
				}
				appendItem(items, fset, path, "config_interface", full, spec, signature, nil)
				for _, field := range typed.Methods.List {
					methodType, err := nodeString(fset, field.Type)
					if err != nil {
						return err
					}
					for _, name := range field.Names {
						appendItem(items, fset, path, "config_interface_method", full+"."+name.Name, field, methodType, nil)
					}
				}
			}
		}
	}
	return nil
}

func containsFatal(block *ast.BlockStmt) []*ast.CallExpr {
	calls := []*ast.CallExpr{}
	if block == nil {
		return calls
	}
	ast.Inspect(block, func(node ast.Node) bool {
		call, ok := node.(*ast.CallExpr)
		if !ok {
			return true
		}
		name := selectorName(call.Fun)
		if strings.HasSuffix(name, ".Fatal") || name == "panic" {
			calls = append(calls, call)
		}
		return true
	})
	return calls
}

func callSummary(fset *token.FileSet, call *ast.CallExpr) string {
	parts := []string{selectorName(call.Fun)}
	for _, argument := range call.Args {
		text, err := nodeString(fset, argument)
		if err == nil {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, " | ")
}

func parseFunctions(fset *token.FileSet, file *ast.File, path string, items *[]Item) error {
	for _, declaration := range file.Decls {
		fn, ok := declaration.(*ast.FuncDecl)
		if !ok || fn.Body == nil {
			continue
		}
		full := file.Name.Name + "." + fn.Name.Name
		if strings.HasPrefix(fn.Name.Name, "ValidateConfig") {
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				ifStatement, ok := node.(*ast.IfStmt)
				if !ok {
					return true
				}
				condition, err := nodeString(fset, ifStatement.Cond)
				if err != nil {
					return true
				}
				for _, call := range containsFatal(ifStatement.Body) {
					position := fset.Position(call.Pos())
					symbol := fmt.Sprintf("%s.validation@%d:%d", full, position.Line, position.Column)
					appendItem(items, fset, path, "config_validation", symbol, ifStatement, condition, map[string]string{"failure": callSummary(fset, call)})
				}
				return true
			})
		}
		if strings.Contains(fn.Name.Name, "NewConfig") || fn.Name.Name == "NewConfig" {
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				switch value := node.(type) {
				case *ast.CompositeLit:
					name := typeName(value.Type)
					if !strings.HasSuffix(name, "Config") {
						return true
					}
					for _, element := range value.Elts {
						pair, ok := element.(*ast.KeyValueExpr)
						if !ok {
							continue
						}
						key, ok := pair.Key.(*ast.Ident)
						if !ok {
							continue
						}
						defaultValue, err := nodeString(fset, pair.Value)
						if err == nil {
							position := fset.Position(pair.Pos())
							target := name + "." + key.Name
							appendItem(items, fset, path, "config_default", fmt.Sprintf("%s@%d:%d", target, position.Line, position.Column), pair, defaultValue, map[string]string{"function": full, "target": target})
						}
					}
				case *ast.AssignStmt:
					for index, left := range value.Lhs {
						selector := selectorName(left)
						if selector == "" || index >= len(value.Rhs) {
							continue
						}
						defaultValue, err := nodeString(fset, value.Rhs[index])
						if err == nil {
							position := fset.Position(value.Pos())
							appendItem(items, fset, path, "config_default_assignment", fmt.Sprintf("%s@%d:%d", selector, position.Line, position.Column), value, defaultValue, map[string]string{"function": full, "target": selector})
						}
					}
				}
				return true
			})
		}
		if fn.Name.Name == "ParseArgs" {
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				call, ok := node.(*ast.CallExpr)
				if !ok {
					return true
				}
				name := selectorName(call.Fun)
				if strings.HasSuffix(name, ".ReadFile") || strings.HasSuffix(name, ".Unmarshal") || strings.HasSuffix(name, ".ParseArgs") || strings.HasSuffix(name, ".ConfigFromJSON") || strings.Contains(name, "convertRuntimeEnv") {
					signature, err := nodeString(fset, call)
					if err == nil {
						position := fset.Position(call.Pos())
						symbol := fmt.Sprintf("%s.precedence@%d:%d", full, position.Line, position.Column)
						appendItem(items, fset, path, "config_precedence_event", symbol, call, signature, nil)
					}
				}
				return true
			})
		}

		ast.Inspect(fn.Body, func(node ast.Node) bool {
			switch value := node.(type) {
			case *ast.CaseClause:
				for _, expression := range value.List {
					literal, ok := expression.(*ast.BasicLit)
					if !ok || literal.Kind != token.STRING {
						continue
					}
					text, err := strconv.Unquote(literal.Value)
					if err == nil {
						position := fset.Position(literal.Pos())
						symbol := fmt.Sprintf("%s.case.%s@%d:%d", full, text, position.Line, position.Column)
						appendItem(items, fset, path, "cli_case_candidate", symbol, literal, text, nil)
					}
				}
			case *ast.CallExpr:
				name := selectorName(value.Fun)
				class := ""
				switch {
				case strings.HasSuffix(name, "flag.NewFlagSet") || name == "flag.NewFlagSet":
					class = "cli_flagset"
				case name == "os.Exit" || strings.HasSuffix(name, ".Fatal"):
					class = "cli_exit_path"
				case strings.HasSuffix(name, ".ParseArgs") || strings.HasSuffix(name, ".Parse"):
					class = "cli_parse_event"
				}
				if class != "" {
					signature, err := nodeString(fset, value)
					if err == nil {
						position := fset.Position(value.Pos())
						symbol := fmt.Sprintf("%s.%s@%d:%d", full, class, position.Line, position.Column)
						appendItem(items, fset, path, class, symbol, value, signature, nil)
					}
				}
			}
			return true
		})
	}
	return nil
}

func parseFile(root, path string, items *[]Item) error {
	absolute := filepath.Join(root, filepath.FromSlash(path))
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, absolute, nil, parser.ParseComments|parser.AllErrors)
	if err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}
	if err := parseStructs(fset, file, path, items); err != nil {
		return fmt.Errorf("config type extraction %s: %w", path, err)
	}
	if err := parseFunctions(fset, file, path, items); err != nil {
		return fmt.Errorf("config function extraction %s: %w", path, err)
	}
	return nil
}

func main() {
	root := flag.String("root", "", "verified source root")
	flag.Parse()
	if *root == "" || flag.NArg() == 0 {
		fmt.Fprintln(os.Stderr, "usage: go_config_surface --root ROOT FILE...")
		os.Exit(64)
	}
	items := []Item{}
	for _, path := range flag.Args() {
		clean := filepath.ToSlash(filepath.Clean(path))
		if strings.HasPrefix(clean, "../") || filepath.IsAbs(path) {
			fmt.Fprintf(os.Stderr, "unsafe source path: %s\n", path)
			os.Exit(64)
		}
		if err := parseFile(*root, clean, &items); err != nil {
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
	encoded, err := json.Marshal(Output{Schema: "trillionnium.go-config-cli-surface.v1", Items: items})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	_, _ = os.Stdout.Write(append(encoded, '\n'))
}
