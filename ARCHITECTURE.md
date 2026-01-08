# Architecture

High-level architecture of api-parity, optimized for AI agents to quickly understand the codebase.

## Project Purpose

api-parity is a differential fuzzing tool that compares two API implementations against a shared OpenAPI specification. It detects behavioral differences between implementations, making it ideal for API rewrites and migrations.

## Core Concepts

### Differential Testing
Send identical requests to two API endpoints and compare responses. Any difference indicates a potential parity issue.

### OpenAPI Parsing
Read OpenAPI specifications to understand available endpoints, request schemas, and expected response formats.

### Fuzz Generation
Generate test cases by fuzzing valid request parameters within constraints defined by the OpenAPI spec.

### Failure Storage
Persist failed test cases so they can be replayed after fixes to verify resolution.

## Data Flow

```
OpenAPI Spec → Parser → Fuzz Generator → Request Builder
                                              ↓
                              ┌───────────────┴───────────────┐
                              ↓                               ↓
                         API Endpoint A                 API Endpoint B
                              ↓                               ↓
                              └───────────────┬───────────────┘
                                              ↓
                                    Response Comparator
                                              ↓
                                    ┌─────────┴─────────┐
                                    ↓                   ↓
                               Pass/Fail           Failure Store
```
