---
paths:
  - "**/*.go"
---

# Go

## Modern Syntax
- Use `maps.Copy` instead of a map assignment loop

## Errors
- Extract a gRPC status with `status.FromError()` — it unwraps through `errors.As` internally as of grpc-go v1.53, so a hand-rolled `errors.As` plus a `GRPCStatus()` interface assertion reimplements what the call already does and drifts from it on the next release

## Control Flow
- When a multi-branch check (if/switch, a type assertion feeding a switch) would add a nesting level, lift it into a named predicate or helper — keep nesting to 2 levels inside any block. The name is the point as much as the depth: a reader learns what the branch decides without evaluating it
