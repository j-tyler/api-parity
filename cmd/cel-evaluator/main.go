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
	"sort"
	"strings"
	"sync"
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

// maxCacheSize bounds the compiled-program cache. In practice, the number of
// unique (expression, variable-names) pairs in a single run equals the number
// of field_rules in the comparison config — typically well under 100. The cap
// is a safety net, not a performance knob.
const maxCacheSize = 256

// programCache caches compiled CEL programs keyed by (expression, variable names).
//
// WHY: Wildcard JSONPath expansion can produce thousands of evaluations of the
// same expression with the same variable names (e.g., "a == b" with vars {a, b})
// but different values. Without caching, each evaluation creates a new cel.Env,
// compiles the expression, and builds a program — causing heavy GC pressure that
// can OOM-kill the process on large responses. With caching, we compile once and
// call prg.Eval() with different data for subsequent hits.
//
// Thread safety: The timeout goroutine in evaluate() means a timed-out goroutine
// could still be reading the cache while the next request's goroutine writes to it.
// RWMutex allows concurrent reads (the common case) with exclusive writes.
type programCache struct {
	mu       sync.RWMutex
	programs map[string]cel.Program
}

func newProgramCache() *programCache {
	return &programCache{programs: make(map[string]cel.Program)}
}

// get returns a cached program and true if found, or nil and false if not.
func (c *programCache) get(key string) (cel.Program, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	prg, ok := c.programs[key]
	return prg, ok
}

// put stores a compiled program. If the cache is full, the entry is silently
// dropped — the caller will just recompile next time, which is the same cost
// as before caching existed.
func (c *programCache) put(key string, prg cel.Program) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.programs) >= maxCacheSize {
		return
	}
	c.programs[key] = prg
}

// cacheKey builds a lookup key from expression and sorted variable names.
// Uses \n as separator because NDJSON lines cannot contain literal newlines,
// so no expression received over the protocol can collide with the separator.
func cacheKey(expr string, varNames []string) string {
	sorted := make([]string, len(varNames))
	copy(sorted, varNames)
	sort.Strings(sorted)
	return expr + "\n" + strings.Join(sorted, ",")
}

func main() {
	writer := bufio.NewWriter(os.Stdout)
	reader := bufio.NewScanner(os.Stdin)
	cache := newProgramCache()

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

		resp := evaluate(req, cache)
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
func evaluate(req Request, cache *programCache) Response {
	ctx, cancel := context.WithTimeout(context.Background(), evaluationTimeout)
	defer cancel()

	resultCh := make(chan Response, 1)

	go func() {
		resultCh <- evaluateSync(req, cache)
	}()

	select {
	case <-ctx.Done():
		return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL evaluation timeout (%v)", evaluationTimeout)}
	case resp := <-resultCh:
		return resp
	}
}

// evaluateSync compiles and runs a CEL expression with the given data.
// Compiled programs are cached by (expression, variable names) so that wildcard
// expansions that evaluate the same expression thousands of times only compile once.
func evaluateSync(req Request, cache *programCache) Response {
	// Collect variable names for cache key
	varNames := make([]string, 0, len(req.Data))
	for key := range req.Data {
		varNames = append(varNames, key)
	}
	key := cacheKey(req.Expr, varNames)

	prg, cached := cache.get(key)
	if !cached {
		// Cache miss: create environment, compile expression, build program.
		// DynType for all variables since JSON values can be any type.
		opts := []cel.EnvOption{
			cel.DefaultUTCTimeZone(true),
		}
		for _, name := range varNames {
			opts = append(opts, cel.Variable(name, cel.DynType))
		}

		env, err := cel.NewEnv(opts...)
		if err != nil {
			return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL environment creation failed: %v", err)}
		}

		ast, issues := env.Compile(req.Expr)
		if issues != nil && issues.Err() != nil {
			return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL compile error in expression %q: %v", req.Expr, issues.Err())}
		}

		var prgErr error
		prg, prgErr = env.Program(ast)
		if prgErr != nil {
			return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL program creation failed: %v", prgErr)}
		}

		cache.put(key, prg)
	}

	// cel.Program is stateless and thread-safe per cel-go docs — safe to call
	// Eval() concurrently on a cached program from multiple timeout goroutines.
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
