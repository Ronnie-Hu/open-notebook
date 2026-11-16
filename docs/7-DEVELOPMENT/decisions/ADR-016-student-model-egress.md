# ADR-016: Restrict student model endpoints

- **Status**: Accepted for the TAgent fork
- **Date**: 2026-11
- **Related**: ADR-015

## Context

Open Notebook's single-user mode deliberately allows localhost and private-network model servers. TAgent allows students to self-register and configure their own providers. A student-supplied endpoint or Vertex credential file path could otherwise make the shared application server contact its private services or read a server file.

## Decision

In TAgent account mode, students may configure only external HTTPS model endpoints. Reject private, loopback, link-local, reserved, and unresolved addresses at save time and immediately before use; reject mixed DNS answers. Reject student-supplied server credential file paths. Administrators retain the self-hosted model options. Model-discovery requests using the existing pinned HTTP target recheck the final resolved address. Account mode stays disabled until outbound network policy also blocks private destinations for provider SDK calls, which cannot all pin DNS at connection time.

## Alternatives considered

- Allow all local/private endpoints: a self-registered student could target services reachable only from the server.
- Validate only when saving a credential: DNS can change between saving and using it.
- Remove custom model endpoints from the UI: this would unnecessarily remove a core Open Notebook feature.

## Consequences

- Students can keep personal hosted model keys and endpoints, but cannot point them at Ollama on the application host or other private services.
- A validation check alone cannot close DNS rebinding during every provider SDK connection. Production activation requires a server-side outbound network restriction and tests of redirects, IPv4/IPv6, and DNS changes.
