# api-parity

Fuzz test two URIs which supposedly follow the same OpenAPI specification to find out where they differ. Replay past failures once the parity is fixed to verify. Perfect for API rewrites.

## Why

Many tools exist for API testing, fuzzing, and contract validation. None focus specifically on the API migration use case: differential testing between two implementations with replayability to build a rich set of test fixtures over time. Existing tools solve pieces of the problem but don't combine them for migration workflows. This project will leverage prior efforts in known projects wherever possible rather than reinventing solved problems.

## How It Works

1. Parse an OpenAPI specification
2. Generate fuzzed requests covering endpoints, parameters, and edge cases
3. Send identical requests to both API implementations
4. Compare responses and report differences
5. Store failures for later replay

## License

MIT License - see [LICENSE](LICENSE) for details.
