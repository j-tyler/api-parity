// CEL Evaluator subprocess for api-parity.
//
// This program reads CEL evaluation requests from stdin and writes results to stdout.
// It uses newline-delimited JSON (NDJSON) for communication.
//
// Protocol:
//   Startup: writes {"ready":true}\n
//   Request: {"id":"<uuid>","expr":"a == b","data":{"a":1,"b":1}}\n
//   Response: {"id":"<uuid>","ok":true,"result":true}\n
//   Error: {"id":"<uuid>","ok":false,"error":"..."}\n
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/google/cel-go/cel"
	"github.com/google/cel-go/common/types"
)

// Request is the JSON structure received from Python.
type Request struct {
	ID   string         `json:"id"`
	Expr string         `json:"expr"`
	Data map[string]any `json:"data"`
}

// Response is the JSON structure sent back to Python.
type Response struct {
	ID     string `json:"id"`
	OK     bool   `json:"ok"`
	Result *bool  `json:"result,omitempty"`
	Error  string `json:"error,omitempty"`
}

// evaluationTimeout catches pathological expressions without blocking Python indefinitely
const evaluationTimeout = 5 * time.Second

func main() {
	writer := bufio.NewWriter(os.Stdout)
	reader := bufio.NewScanner(os.Stdin)

	// 10 MB buffer for large API response payloads in "data" field
	const maxTokenSize = 10 * 1024 * 1024
	reader.Buffer(make([]byte, 64*1024), maxTokenSize)

	// Send ready signal
	if err := writeJSON(writer, map[string]bool{"ready": true}); err != nil {
		fmt.Fprintf(os.Stderr, "failed to write ready: %v\n", err)
		os.Exit(1)
	}

	// Process requests
	for reader.Scan() {
		line := reader.Text()
		if line == "" {
			continue
		}

		var req Request
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			// Malformed JSON - send error with empty ID
			resp := Response{ID: "", OK: false, Error: fmt.Sprintf("invalid JSON: %v", err)}
			writeJSON(writer, resp)
			continue
		}

		resp := evaluate(req)
		if err := writeJSON(writer, resp); err != nil {
			fmt.Fprintf(os.Stderr, "failed to write response for %s: %v\n", req.ID, err)
		}
	}

	if err := reader.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "scanner error: %v\n", err)
		os.Exit(1)
	}
}

// writeJSON marshals v to JSON and writes it as a single line to w (with flush).
func writeJSON(w *bufio.Writer, v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if _, err := w.Write(data); err != nil {
		return err
	}
	if err := w.WriteByte('\n'); err != nil {
		return err
	}
	return w.Flush()
}

// evaluate wraps evaluateSync with a timeout.
func evaluate(req Request) Response {
	ctx, cancel := context.WithTimeout(context.Background(), evaluationTimeout)
	defer cancel()

	resultCh := make(chan Response, 1)

	go func() {
		resultCh <- evaluateSync(req)
	}()

	select {
	case <-ctx.Done():
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("evaluation timeout exceeded (%v)", evaluationTimeout)}
	case resp := <-resultCh:
		return resp
	}
}

// evaluateSync compiles and runs a CEL expression with the given data.
func evaluateSync(req Request) Response {
	// DynType for all variables since JSON values can be any type
	opts := []cel.EnvOption{
		cel.DefaultUTCTimeZone(true),
	}

	for key := range req.Data {
		opts = append(opts, cel.Variable(key, cel.DynType))
	}

	env, err := cel.NewEnv(opts...)
	if err != nil {
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL environment creation failed: %v", err)}
	}

	ast, issues := env.Compile(req.Expr)
	if issues != nil && issues.Err() != nil {
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL compile error in expression %q: %v", req.Expr, issues.Err())}
	}

	prg, err := env.Program(ast)
	if err != nil {
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL program creation failed: %v", err)}
	}

	out, _, err := prg.Eval(req.Data)
	if err != nil {
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL evaluation error: %v", err)}
	}

	// CEL expressions in api-parity must return boolean (true = values match).
	if out.Type() != types.BoolType {
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL expression must return boolean, got %v", out.Type())}
	}

	result, ok := out.Value().(bool)
	if !ok {
		return Response{ID: req.ID, OK: false, Error: "internal error: bool type but non-bool value"}
	}
	return Response{ID: req.ID, OK: true, Result: &result}
}
