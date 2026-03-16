# Dependency Rules

These are documented boundaries for contributors and agents.

## Backend Direction
- Delivery layer may depend on orchestration, protocol, config, and provider adapters.
- Orchestration layer may depend on business layer, provider adapters, and protocol models.
- Business layer may depend on provider adapters and local business/data models.
- Provider adapters may depend on config and external SDKs.
- Protocol/schema layer should not depend on delivery or provider adapters.

## Frontend Direction
- UI components may depend on transport/audio modules.
- Transport/audio modules should not depend on React UI components.

## Cross-App Rules
- Frontend and backend communicate only through explicit WebSocket/HTTP contracts.
- Do not import backend code into frontend or frontend code into backend.

## Data Boundary Rules
- Parse and validate config on load.
- Validate/normalize inbound client payloads before use.
- Treat external provider responses as untrusted until validated against expected shape.
