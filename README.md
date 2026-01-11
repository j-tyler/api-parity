# api-parity

Differential fuzzing tool for comparing two API implementations against an OpenAPI specification. Find where they differ, replay failures to verify fixes. Perfect for API rewrites.

## Status

**In development** — CEL evaluator subprocess implemented and tested. Core CLI pending. See ARCHITECTURE.md and DESIGN.md for technical details.

**Languages:** Python (primary), Go (CEL evaluator subprocess)

## Why

API migration is hard. You have a working API, you're rewriting it, and you need to know: does the new implementation behave exactly like the old one? Existing tools solve pieces of this problem but don't combine them for migration workflows. api-parity focuses specifically on differential testing between two implementations with replayable failure artifacts.

## How It Works

```
api-parity explore --spec openapi.yaml --target-a prod --target-b staging --out ./artifacts
```

1. Parse the OpenAPI specification
2. Generate requests (including stateful chains via OpenAPI links)
3. Send identical requests to both targets
4. Compare responses under user-defined rules
5. Write mismatch bundles for analysis and replay

## Documentation

- **ARCHITECTURE.md** — System structure, data models, component design
- **DESIGN.md** — Decisions and reasoning
- **TODO.md** — Planned work and open questions

## License

MIT License - see [LICENSE](LICENSE) for details.
