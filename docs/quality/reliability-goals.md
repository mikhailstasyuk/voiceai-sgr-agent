# Reliability Goals

## Availability and Session Stability
- Session startup should be predictable and error states should be visible to the client.
- Session teardown paths should clean up resources on stop/disconnect/error.

## Latency Orientation
- Prioritize low time from user end-of-speech to first assistant audio.
- Prefer small, incremental fixes that reduce latency regressions in critical paths.

## Diagnostics
- Backend errors should be logged with enough context to identify failure point.
- Client status transitions should make failure/recovery state visible.

## Practical Acceptance
- No new known crash path in start/stop/turn flows.
- Health endpoint remains functional.
- Build and core runtime commands remain usable.

