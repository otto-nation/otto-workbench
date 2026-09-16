---
paths:
  - "**/*.go"
---

# Go

## Modern Syntax
- Use `maps.Copy` instead of a map assignment loop

## Errors
- Extract a gRPC status with `status.FromError()` — it unwraps through `errors.As` internally as of grpc-go v1.53, so a hand-rolled `errors.As` plus a `GRPCStatus()` interface assertion reimplements what the call already does and drifts from it on the next release
- Never call `.String()` on a protobuf wrapper message to read its value — it emits proto text format (`value:"..."`), not the inner string, and the result looks almost right when it lands in a workflow ID, log line, or key. Use `.GetValue()`

## Control Flow
- When a multi-branch check (if/switch, a type assertion feeding a switch) would add a nesting level, lift it into a named predicate or helper — keep nesting to 2 levels inside any block. The name is the point as much as the depth: a reader learns what the branch decides without evaluating it
- Never call `os.Exit` outside `main` — not from a helper, not from a goroutine. `os.Exit` skips every deferred function on the stack, so a `defer Close()` registered in `main` never runs. Return the error, or signal shutdown
