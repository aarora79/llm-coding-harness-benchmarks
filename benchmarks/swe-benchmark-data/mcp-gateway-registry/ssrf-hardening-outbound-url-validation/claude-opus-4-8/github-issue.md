# GitHub Issue: SSRF Hardening - Validate Outbound URLs on Agent Card Fetch and Health Checks

## Title
Validate outbound URLs before agent-card fetch and server/agent health checks (SSRF hardening)

## Labels
- security
- enhancement
- registry
- health-check

## Description

### Problem Statement
The registry stores user-supplied URLs at registration time (`AgentCard.url` for A2A agents, `proxy_pass_url` for MCP servers) and later issues outbound HTTP requests to those URLs during health checks and agent-card fetches. These outbound requests perform **no Server-Side Request Forgery (SSRF) validation**, so an authenticated caller who registers an asset can steer the registry into requesting arbitrary internal endpoints.

Two concrete paths are unprotected today:

1. **Agent card fetch / agent health check** - `POST /agents/{path}/health` in `registry/api/agent_routes.py` (lines 883-1013). It reads `agent_card.url` (supplied at registration), builds `https://{netloc}/.well-known/agent-card.json` via `_build_agent_health_urls()` (lines 186-205), then issues `client.get(url)` and a fallback `client.head(base_url)` with no scheme, hostname, or private-IP checks.

2. **Server health check / tool discovery** - `HealthMonitoringService._check_server_endpoint_transport_aware()` in `registry/health/service.py` (lines 674-957) and the tool-fetch background task. These call `client.get/post(proxy_pass_url ...)` with `follow_redirects=True` and **inject stored credentials** (Bearer tokens, API keys) into the request headers.

### Current State
- A complete, well-tested SSRF guard already exists for SKILL.md fetches: `_is_safe_url()` and `_is_private_ip()` in `registry/services/skill_service.py` (lines 94-192). It enforces http/https, resolves the hostname with `socket.getaddrinfo`, blocks private/loopback/link-local/reserved IPs and the cloud metadata address `169.254.169.254`, and honors a trusted-domain allowlist (`github.com`, `gitlab.com`, etc., extensible via `settings.github_extra_hosts`).
- This guard is **not reused** by the agent health check or the server health check. The two surfaces that fetch user-supplied URLs have zero SSRF protection.

### Impact
- An attacker with registration rights can probe internal-only services, cloud metadata endpoints (`169.254.169.254`), and loopback admin ports.
- Because server health checks inject stored credentials, a malicious `proxy_pass_url` can also exfiltrate those credentials to an attacker-controlled host, and `follow_redirects=True` lets an allowlisted host redirect into an internal target.

### Proposed Solution
Promote the existing SKILL.md SSRF logic into a shared, reusable utility and apply it on every outbound request derived from a user-supplied URL:

1. Extract `_is_safe_url()` / `_is_private_ip()` into a new shared module `registry/utils/ssrf.py` (single source of truth), keeping `skill_service.py` behavior identical by re-exporting.
2. Call the guard before the GET/HEAD in the agent health check, and before the GET/POST in the server health check and tool-fetch task. On failure, return an `unhealthy` status with an SSRF detail (do not raise a 500).
3. Re-validate the final URL after any redirect (mirror the SKILL.md redirect re-check).
4. Add a feature flag (`ssrf_protection_enabled`, default `true`) and an additional host allowlist (`ssrf_extra_allowed_hosts`) so operators running registries on private networks can opt specific hosts back in.
5. Log every blocked request at WARNING with the destination host for auditing.

### User Stories
- As a security engineer, I want the registry to validate every outbound URL it derives from user input, so a registered asset cannot turn the registry into an SSRF proxy.
- As an operator running agents on a private network, I want to allowlist specific internal hosts, so legitimate private endpoints still pass health checks.
- As a security auditor, I want blocked outbound requests logged with the target host, so I can detect SSRF attempts.

### Acceptance Criteria
- [ ] A shared SSRF guard exists in `registry/utils/ssrf.py` and `skill_service.py` reuses it with no behavior change.
- [ ] `POST /agents/{path}/health` validates the agent-card URL and the fallback HEAD URL before fetching; blocked URLs yield `status: "unhealthy"` with an SSRF detail.
- [ ] Server health checks and the tool-fetch task validate `proxy_pass_url` (and the resolved transport endpoint) before requesting.
- [ ] Private, loopback, link-local, reserved IPs and `169.254.169.254` are blocked; only http/https schemes are allowed.
- [ ] Redirect targets are re-validated.
- [ ] `ssrf_protection_enabled` (default true) and `ssrf_extra_allowed_hosts` are honored.
- [ ] Blocked requests are logged at WARNING.
- [ ] Unit tests cover private IP, metadata IP, non-http scheme, DNS-resolves-to-private, allowlist bypass, and redirect-to-private.
- [ ] Existing SKILL.md SSRF tests still pass unchanged.

### Out of Scope
- Federation client SSRF hardening (tracked separately; config-driven endpoints).
- Webhook / registration-gate callback hardening (config-driven, not request-driven).
- Changing the health-check transport logic or the agent-card schema.
- Network-layer egress controls (firewall, NAT policy).

### Dependencies
- None. Reuses existing `_is_safe_url()` logic and the `ipaddress` / `socket` / `urllib.parse` standard library modules already in use.

### Related Issues
- SKILL.md SSRF protection (existing `_is_safe_url` in `skill_service.py`), `github_extra_hosts` allowlist setting.
