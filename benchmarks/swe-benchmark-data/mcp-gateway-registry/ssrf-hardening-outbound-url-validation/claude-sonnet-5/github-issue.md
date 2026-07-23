# GitHub Issue: SSRF hardening - validate outbound URLs on agent-card fetch and health-check paths

## Title
Harden outbound URL fetching against SSRF on agent-card and health-check paths

## Labels
- security
- enhancement

## Description

### Problem Statement
The registry makes outbound HTTP requests to URLs supplied by users when they register or manage MCP servers and A2A agents. Two of these paths - fetching an agent card and running server health checks - currently issue outbound requests without any validation of the target URL. This exposes the registry to Server-Side Request Forgery (SSRF): a malicious or careless registrant could point a server's URL or agent-card URL at an internal address (e.g. cloud metadata endpoints, internal admin APIs, loopback services, other services on the same ECS cluster/VPC) and use the registry as a proxy to reach it, or to fingerprint the internal network via response timing/content differences.

A guard function, `_is_safe_url()`, already exists in the codebase and is applied to the SKILL.md fetch path, but it is not reused on the agent-card fetch or health-check paths. This issue tracks closing that gap by promoting the existing guard into a shared utility and applying it consistently everywhere the registry performs outbound requests to user-supplied URLs.

### Proposed Solution
- Extract `_is_safe_url()` into a shared, testable utility module so it is not duplicated or reimplemented per call site.
- Apply the shared guard to the agent-card fetch path and the server health-check path (both scheduled/background and on-demand, if both exist).
- Deny requests to private/internal/loopback/link-local IP ranges and non-HTTP(S) schemes by default, resolving hostnames before connecting to prevent DNS-rebinding bypasses.
- Add configuration for an operator-controlled allowlist so legitimate internal deployments (e.g. an MCP server intentionally running on a private subnet the registry is meant to reach) can opt back in without code changes.
- Preserve current behavior for the existing SKILL.md path; do not change its semantics, only its implementation location.
- Ensure the change is backwards-compatible: existing registered servers with URLs that were previously allowed keep working unless an operator opts into stricter enforcement.

### User Stories
- As a gateway operator, I want outbound requests the registry makes on my behalf to be restricted from reaching internal-only addresses, so that a malicious server/agent registration cannot be used to pivot into my private network.
- As a downstream team registering an MCP server, I want a clear, actionable error message when my server's URL is rejected, so I can understand why and, if legitimate, request it be allowlisted.
- As a gateway operator, I want to configure an allowlist of internal hosts/CIDRs that are permitted, so that intentional internal deployments are not broken by the new default-deny behavior.

### Acceptance Criteria
- [ ] `_is_safe_url()` (or an equivalent shared function) lives in a single shared module and is imported by every call site that performs an outbound HTTP request to a user-supplied URL.
- [ ] The agent-card fetch path validates the target URL with the shared guard before making the outbound request.
- [ ] The health-check path (all triggers: on-demand and/or scheduled) validates the target URL with the shared guard before making the outbound request.
- [ ] The guard denies private, loopback, link-local, and reserved IP ranges (RFC 1918, RFC 4193, RFC 3927, IPv6 equivalents) and denies non-HTTP(S) schemes.
- [ ] The guard resolves DNS before connecting and validates the resolved IP (not just the literal hostname), to prevent DNS-rebinding bypasses.
- [ ] An allowlist of hosts/CIDRs can be configured via environment variable and takes precedence over the deny rules.
- [ ] Requests rejected by the guard return a clear, actionable error (not a silent failure or generic 500).
- [ ] Existing behavior for the SKILL.md fetch path is unchanged.
- [ ] Existing registered servers/agent cards that only fail health checks due to the new guard are logged clearly so operators can diagnose and allowlist them.
- [ ] Unit tests cover: private IP rejection, loopback rejection, link-local rejection, allowlist override, DNS-rebinding style resolution check, and the previously-passing SKILL.md path continuing to pass.
- [ ] No new required configuration - the feature ships with a safe default and does not require operators to take action to remain functional for their existing deployments (any breaking edge case is opt-in stricter enforcement, not opt-in safety).

### Out of Scope
- Rewriting the SKILL.md fetch path's calling code beyond swapping in the shared utility import.
- Adding SSRF protection to internal service-to-service calls that do not originate from user-supplied input (e.g. calls to the registry's own database or internal auth server).
- A UI for managing the allowlist (initial version is environment/config-file driven only).
- Rate limiting or abuse detection beyond URL validation.

### Dependencies
- None expected beyond Python's standard library (`ipaddress`, `socket`) for IP-range checks; to be confirmed during design based on what the existing `_is_safe_url()` already uses.

### Related Issues
- #1282 (reference issue driving this work)
