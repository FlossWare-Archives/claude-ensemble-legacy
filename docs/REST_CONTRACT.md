# Claude Ensemble REST Contract

Status: **Initial compatibility slice**  
Applies to: the client-facing gateway at `127.0.0.1:8080` and its `/api/v1/` routes.

## Compatibility rules

- Existing resource paths, methods, success payloads, and default error payloads remain unchanged.
- `/api/v1/` is the current URI version boundary. Do not remove or rename it as part of media-type work.
- JSON request and response representations use `application/json`.
- Error responses continue to use the existing JSON envelope by default.
- Clients may opt into RFC 9457 Problem Details for gateway-generated errors by including `application/problem+json` in `Accept`. The HTTP status is unchanged. The response adds the standard `type`, `title`, `status`, `detail`, and `instance` members while retaining existing error-envelope fields as extension members.
- A `q=0` media-range does not opt a client into Problem Details.
- Successful responses remain `application/json`; `application/problem+json` is an error representation, not a replacement success representation.

## Why this is opt-in

CE has existing scripts, service clients, and integrations that may inspect the current `ok`, `error`, or `error_code` fields. Changing every error body unconditionally would create an avoidable compatibility break. Opt-in Problem Details lets clients migrate and test against a standard representation without forcing a coordinated cutover.

## Scope of this first slice

This implementation applies to errors emitted through the canonical gateway's `_send` helper. Forwarded service response-header handling and the independently hosted Graph and Memory services need separate audits before claiming end-to-end conformance. This document does not claim that all CE endpoints already implement complete HTTP content negotiation.

## Next steps

1. Inventory gateway and service routes, methods, status codes, request schemas, and response envelopes.
2. Add contract tests for forwarded responses and independently hosted services.
3. Decide whether to standardize all error responses on Problem Details in a future breaking release or retain the opt-in compatibility path.
4. Add explicit request media-type validation and success-response negotiation only after documenting current clients and the intended compatibility behavior.

## References

- [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110)
- [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457)
- [ADR-0031: REST Representation Contracts and API Evolution](https://github.com/FlossWare/engineering-standards/blob/main/adr/ADR-0031-rest-representation-contracts-and-api-evolution.md)
