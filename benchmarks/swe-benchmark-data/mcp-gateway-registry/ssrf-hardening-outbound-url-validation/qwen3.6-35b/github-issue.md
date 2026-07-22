# GitHub Issue: SSRF Hardening - Promote Shared URL Validation to Agent Card and Health Check Paths

## Title
SSRF hardening: promote existing `_is_safe_url()` into a shared utility and apply it to agent-card fetch and server health-check outbound URLs

## Labels
- security
- enhancement
- backend

## Description

### Problem Statement

The MCP Gateway Registry makes outbound HTTP requests to user-supplied URLs in several code paths without SSRF protection:

1. **Agent card fetch during registration** - `registry/utils/agent_validator.py` calls `httpx.get()` on a well-known agent-card URL from a user-supplied `url` field. The function `_check_endpoint_reachability()` has no SSRF guard.

2. **Agent health check endpoint** - `registry/api/agent_routes.py` exposes `POST /api/agents/{path:path}/health` which fetches `/.well-known/agent-card.json` and the agent's base URL via httpx GET/HEAD. Neither URL is validated before the request.

3. **Server health check service** - `registry/health/service.py` has `HealthMonitoringService._check_single_service()` and `perform_immediate_health_check()` that make transport-aware httpx requests to the server's `proxy_pass_url` without any SSRF guard.

4. **MCP client connections** - `registry/core/mcp_client.py` connects to user-supplied base URLs via streamable-http and SSE transports without SSRF validation.

An SSRF guard already exists in `registry/services/skill_service.py` as the `_is_safe_url()` function. It correctly validates URLs by checking scheme (http/https only), resolving hostnames to IPs, checking against a trusted domains allowlist, and blocking private/loopback/link-local/reserved addresses. However, this guard is **not reused** outside the skill-fetch code path.

An attacker who can register an agent or MCP server can cause the registry to:
- Access the EC2 Instance Metadata Service at `169.254.169.254`
- Reach internal services on private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- Scan internal network topology

Platform operators running this service on AWS ECS Fargate are especially exposed, since the registry runs in a VPC with direct access to internal services.

### Proposed Solution

1. Promote the existing `_is_safe_url()` function from `registry/services/skill_service.py` into a shared utility module (e.g., `registry/utils/url_security.py`). This avoids code duplication and ensures all callers use the same validation logic. The function signature and behaviour remain identical.

2. Create a companion exception class (e.g., `SSRFBlockedError`) so callers can distinguish SSRF blocks from other errors.

3. Apply the promoted `_is_safe_url()` at every new outbound-fetch boundary:
   - `registry/api/agent_routes.py`: In `check_agent_health()` before any httpx call on the agent URL. Also in the agent registration flow so the `url` field is validated before the reachability check.
   - `registry/utils/agent_validator.py`: In `_check_endpoint_reachability()` before calling `httpx.get()`.
   - `registry/health/service.py`: In `_check_single_service()` and `perform_immediate_health_check()` before calling `_check_server_endpoint_transport_aware()` with `proxy_pass_url`.

4. Add a configuration setting `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` (or reuse the existing `github_extra_hosts` pattern) to allowlist specific hostnames that skip the private-IP check. This follows the existing pattern where `settings.github_extra_hosts` is merged into `_trusted_domains()` at runtime.

### User Stories

- As a registry operator running on AWS ECS, I want all outbound HTTP requests validated so that the registry cannot be abused for SSRF attacks.
- As a downstream team registering an MCP server, I want the registry to reject requests to private IPs so that misconfigured server URLs fail fast with a clear error.
- As a security auditor, I want consistent SSRF protection across all code paths so there are no gaps.

### Acceptance Criteria

- [ ] `_is_safe_url()` is moved from `registry/services/skill_service.py` into `registry/utils/url_security.py` (or equivalent) and re-exported from `skill_service.py` for backward compatibility.
- [ ] A new exception `SSRFBlockedError` is added and raised by `_is_safe_url()` when a URL is blocked.
- [ ] Agent registration endpoint validates the agent `url` with `_is_safe_url()` before storing or fetching the card.
- [ ] Agent health check endpoint (`POST /api/agents/{path}/health`) validates the URL with `_is_safe_url()` before any httpx request.
- [ ] `_check_endpoint_reachability()` in `agent_validator.py` validates the URL before calling `httpx.get()`.
- [ ] `HealthMonitoringService._check_single_service()` validates `proxy_pass_url` before the transport-aware check.
- [ ] `HealthMonitoringService.perform_immediate_health_check()` validates `proxy_pass_url` before starting.
- [ ] Redirect targets are validated after every httpx request that follows redirects (same pattern already used in `skill_service.py`).
- [ ] A configuration setting `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` is added to `Settings` in `registry/core/config.py` for operator-supplied hostname allowlist entries.
- [ ] All httpx.AsyncClient instantiations in the affected code paths use `follow_redirects=False` to prevent redirect-based SSRF bypass.
- [ ] New unit tests cover SSRF blocking for agent and server health check paths.
- [ ] All existing tests continue to pass.

### Out of Scope

- Changing the existing `_is_safe_url()` validation algorithm.
- Adding SSRF protection to webhook calls or federation peer-sync calls (different trust model).
- Modifying the httpx client library, timeout settings, or redirect policy.
- DNS rebinding protection (infrastructure-level control).

### Dependencies

- Self-contained within `registry/`. No new Python packages.

### Related Issues

- This is a security audit finding addressing gaps identified in the registry's outbound request handling.
- Reference issue: #1282