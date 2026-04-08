---
paths:
  - "**/*.kt"
  - "**/*.kts"
---

# Kotlin

## Testing
- `@BeforeAll` must be in a companion object at the top of the class, also annotated with `@JvmStatic`
- Use `shouldNotBeNull()` instead of `!!`
- Use Kotest assertions and matchers
- Use MockK instead of Mockito
- Integration tests should be suffixed with `IT` not `IntegrationTest`

## Code Style
- Companion objects should be after private functions
- Keep private functions at the top of the class after property declarations
