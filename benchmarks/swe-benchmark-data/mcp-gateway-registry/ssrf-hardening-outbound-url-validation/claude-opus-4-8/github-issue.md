# GitHub Issue: SSRF hardening for agent-card fetch and server health-check outbound requests

## Title
Harden agent-card fetch and health-check paths against SSRF by reusing the existing `_is_safe_url` guard

## Labels
- security
- enhancement
- backend

(Only labels that already exist in the upstream repo should be applied. If a dedicated `ssrf` or `security-hardening` label would help triage, suggest it in a comment rather than creating it during issue filing.)

## Description

### Problem Statement

The registry makes outbound HTTP requests to URLs that are supplied by users at agent/server registration time. A working SSRF guard (`_is_safe_url`) already exists in `registry/services/skill_service.py` and protects the SKILL.md fetch path, but it is **not reused** on two other outbound paths that also fetch user-supplied URLs:

1. **Agent-card reachability probe** - `registry/utils/agent_validator.py::_check_endpoint_reachability` (line 196) builds `f"{url}/.well-known/agent-card.json"` and calls synchronous `httpx.get(...)`. Triggered by `POST /agents/register` (via `verify_endpoint=True`). The `AgentCard.url` is a required free-form string validated only for scheme/hostname format - no private-IP, loopback, or cloud-metadata checks.

2. **Server/agent health checks** - `registry/health/service.py` makes numerous outbound `httpx` GET/POST/HEAD requests to the user-supplied `proxy_pass_url` (and derived `mcp_endpoint`/`sse_endpoint`) both on a periodic background loop and via the immediate "check now" path (`perform_immediate_health_check`). Reachable from `POST /agents/{path}/health`, server register/edit/refresh/toggle endpoints. No SSRF validation is applied, and `follow_redirects=True` is set on nearly every request, so even a permitted host can redirect to an internal target. Configured credentials are injected into these requests, so an SSRF to an internal service can also leak credentials.

Because these URLs are never SSRF-validated, an attacker who can register an agent or server can drive the gateway to issue requests to internal-only addresses (e.g. `http://169.254.169.254/latest/meta-data/`, `http://10.0.0.5:8500/`, `http://localhost:6379/`), enabling internal network reconnaissance and cloud-metadata theft from the ECS task.

### Proposed Solution

1. **Promote the existing guard into a shared utility.** Move `_is_safe_url`, `_is_private_ip`, `_trusted_domains`, and `_DEFAULT_TRUSTED_DOMAINS` from `registry/services/skill_service.py` into a new `registry/utils/ssrf.py` module. Keep `skill_service._is_safe_url` as a thin re-export so all existing behavior and tests continue to pass (backwards-compatible).

2. **Apply the guard on the agent-card path.** Validate the agent URL in `_check_endpoint_reachability` before the `httpx.get` and re-validate the final URL after any redirect (mirroring the SKILL.md pattern). On a blocked URL, treat the endpoint as unreachable (non-blocking, preserving current behavior where reachability failures do not fail registration).

3. **Apply the guard on the health-check path.** Validate `proxy_pass_url`/resolved endpoints at the single choke point (`_check_server_endpoint_transport_aware` and/or `perform_immediate_health_check`) before any outbound request. A blocked URL yields an `unhealthy`/blocked status instead of an outbound request. Disable or re-validate redirects.

4. **Add a configurable allowlist.** Introduce a dedicated `SSRF_ALLOWED_HOSTS` setting (comma-separated), merged into the trusted-domain set alongside the existing `github_extra_hosts`, so operators can permit legitimate internal hosts (e.g. private MCP servers on internal IPs) without disabling protection globally. Behavior must remain backwards-compatible when the new setting is unset.

### User Stories

- As an **operator** running the gateway, I want outbound fetches to internal/private IPs and cloud-metadata endpoints blocked by default so a malicious registration cannot turn the gateway into an SSRF pivot.
- As an **operator** with legitimate internal MCP servers, I want an allowlist so I can permit specific internal hosts without weakening protection for everything else.
- As a **downstream team** registering an MCP server or agent, I want a clear error/status when my URL is rejected so I understand why the health check fails.

### Acceptance Criteria

- [ ] `_is_safe_url` and helpers live in a shared `registry/utils/ssrf.py` module; `skill_service` re-uses them with no behavior change.
- [ ] `_check_endpoint_reachability` (agent-card probe) validates the URL before fetching and re-validates after redirects; blocked URLs report unreachable without raising.
- [ ] The health-check path validates `proxy_pass_url`/derived endpoints before every outbound request; blocked URLs produce an `unhealthy` status and are logged, with no outbound request made.
- [ ] Requests to private, loopback, link-local, reserved IPs, and `169.254.169.254` are blocked by default on both paths.
- [ ] A new `SSRF_ALLOWED_HOSTS` env var (comma-separated) extends the allowlist; unset preserves prior behavior.
- [ ] `follow_redirects` is disabled or each redirect hop is re-validated on both hardened paths.
- [ ] All existing skill SSRF tests still pass; new unit tests cover the agent-card and health-check paths.
- [ ] Backwards-compatible: existing registrations with public URLs continue to be reachable and healthy; the new setting is optional.

### Out of Scope

- Rewriting the health-check transport logic beyond inserting validation.
- SSRF protection for federation clients (`asor_client`, `peer_registry_client`, `agentcore_client`) - these fetch operator-configured endpoints or inline content, not arbitrary user URLs.
- Blocking outbound traffic at the network/infrastructure layer (security groups, egress firewall) - complementary but separate.
- Changing the `AgentCard.url` / `proxy_pass_url` schema or making registration reject internal URLs outright (would break the local-runtime and internal-MCP use cases).

### Dependencies

- No new third-party dependencies. Uses the standard library `ipaddress`, `socket`, `urllib.parse` already used by the existing guard.

### Related Issues

- Reference: https://github.com/agentic-community/mcp-gateway-registry/issues/1282
