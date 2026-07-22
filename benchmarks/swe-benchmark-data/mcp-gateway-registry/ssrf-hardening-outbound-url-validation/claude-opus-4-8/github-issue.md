# GitHub Issue: SSRF Hardening -- Outbound URL Validation

## Title
Harden outbound HTTP calls against SSRF by promoting `_is_safe_url()` to a shared utility and applying it at all user-originated URL call sites

## Labels
- security
- enhancement
- backend

## Description

### Problem Statement
The registry fetches user-supplied URLs (proxy_pass_url, agent URLs, SKILL.md URLs) from multiple services, but SSRF protection (`_is_safe_url()` with private-IP and cloud-metadata blocking) exists only inside `skill_service.py`. All other outbound call sites -- the MCP client, health check service, agent validator, skill scanner, and auth-server proxy -- connect to user-originated URLs with no SSRF guard. An attacker who registers a server with `proxy_pass_url=http://169.254.169.254/latest/meta-data/` can exfiltrate cloud credentials or probe internal networks.

### Proposed Solution
1. Extract `_is_safe_url()`, `_is_private_ip()`, and the trusted-domain allowlist into a new shared utility module (`registry/utils/ssrf.py`).
2. Add a new configuration parameter `SSRF_ADDITIONAL_TRUSTED_DOMAINS` (comma-separated) so operators can allowlist internal corporate hosts without coupling to the GitHub-specific `github_extra_hosts` setting.
3. Apply the shared `is_safe_url()` check at every call site where the URL originates from user input:
   - `registry/core/mcp_client.py` (transport detection + tool fetching)
   - `registry/health/service.py` (background + immediate health checks)
   - `registry/utils/agent_validator.py` (agent reachability probe)
   - `registry/services/skill_scanner.py` (skill content download)
   - `auth_server/server.py` (MCP proxy via `X-Upstream-Url`)
4. Validate at registration time: when a server, agent, or skill URL is first submitted, reject it early if it fails the SSRF check. This protects all downstream consumers in a single enforcement point.
5. Add structured logging and a counter metric (`ssrf_blocked_total`) for blocked requests.

### User Stories
- As a platform operator, I want all outbound requests to user-supplied URLs validated against private-IP and metadata-endpoint rules so that SSRF attacks cannot exfiltrate cloud credentials.
- As a downstream team consuming the registry API, I want a clear 422 error when I accidentally register a server with a private URL so that I can fix the configuration immediately.
- As an SRE, I want a metric (`ssrf_blocked_total`) counting blocked SSRF attempts so that I can alert on anomalous activity.

### Acceptance Criteria
- [ ] A new module `registry/utils/ssrf.py` exposes `is_safe_url(url: str) -> bool` and `is_private_ip(ip_str: str) -> bool` as public functions.
- [ ] `is_safe_url()` blocks: non-http(s) schemes, unresolvable hostnames, IPs in private/loopback/link-local/reserved ranges, and the cloud metadata endpoint `169.254.169.254`.
- [ ] `is_safe_url()` allows trusted domains (built-in defaults + `SSRF_ADDITIONAL_TRUSTED_DOMAINS` env var) without IP resolution.
- [ ] All HIGH-risk call sites listed above call `is_safe_url()` before making the outbound request. Blocked URLs raise/return an appropriate error (HTTP 422 at API boundaries, `False`/`None` at service boundaries).
- [ ] Registration endpoints (`POST /servers`, `POST /agents`, `POST /skills`) validate URL fields at submission time and reject with 422 + structured error body on failure.
- [ ] The existing `skill_service.py` is refactored to import from `registry/utils/ssrf.py` instead of using private functions -- no behavior change.
- [ ] Unit tests cover: private IPs (IPv4 + IPv6), cloud metadata IP, non-http schemes, unresolvable hostnames, trusted-domain bypass, valid public URLs.
- [ ] Integration test confirms end-to-end rejection at registration time.
- [ ] A Prometheus counter `ssrf_blocked_total` (labels: `call_site`, `reason`) is incremented on every block.
- [ ] `SSRF_ADDITIONAL_TRUSTED_DOMAINS` is documented in `.env.example`, Helm values, Terraform variables, and Docker Compose extra_env examples.
- [ ] All existing tests pass (`uv run pytest tests/ -n 8`); no regressions.
- [ ] Backwards compatible: previously-registered servers with public URLs continue to work without re-registration.

### Out of Scope
- Egress-layer network controls (security groups, VPC endpoints) -- those are complementary but separate.
- Scanning or re-validating URLs that were registered before this change ships (migration/backfill).
- Rate limiting or throttling of outbound requests (separate concern).
- DNS rebinding protection beyond the initial resolution check (would require connect-time pinning, tracked separately).
- Blocking admin-configured URLs (`registration_webhook_url`, `registration_gate_url`, etc.) -- those are operator-trusted.

### Dependencies
- No new external libraries required. The implementation uses only `socket`, `ipaddress`, and `urllib.parse` from the standard library.
- Depends on the existing `registry/core/config.py` Settings class for the new env var.

### Related Issues
- Existing SSRF allowlist tests at `tests/unit/services/test_skill_service_ssrf_allowlist.py` should be migrated to test the shared module.
