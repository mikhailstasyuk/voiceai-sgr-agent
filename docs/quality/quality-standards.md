# Quality Standards

## Correctness
- Changes should preserve existing behavior unless requirement updates are intentional and documented.
- Boundary inputs (client payloads, provider responses, config) should be handled defensively.

## Maintainability
- Keep changes local to relevant domain/layer.
- Prefer explicit names and straightforward control flow over implicit behavior.
- Update documentation when behavior or assumptions change.

## Test and Validation Expectations
- Run relevant existing checks before claiming completion.
- For backend changes: include test evidence or clear manual verification steps.
- For frontend changes: include manual workflow validation notes when automated tests are absent.

