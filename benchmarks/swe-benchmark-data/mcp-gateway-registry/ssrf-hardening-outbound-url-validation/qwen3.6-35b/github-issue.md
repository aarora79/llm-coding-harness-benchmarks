# GitHub Issue: SSRF Hardening - Block Private/Reserved IPs on Outbound HTTP Requests

## Title
SSRF hardening: add URL-to-IP validation to block outbound requests to private/reserved IP ranges

## Labels
- enhancement
- security

## Description

### Problem Statement

The mcp-gateway-registry makes outbound HTTP requests to URLs supplied by users in multiple places:

1. **Server health checks** (`registry/health/service.py`): Background and immediate health checks use httpx to probe `proxy_pass_url` from registered MCP server configurations.
2. **Agent health checks** (`registry/api/agent_routes.py`): The `POST /api/agents/{path}/health` endpoint fetches URLs from `agent_card.url` via httpx GET/HEAD.
3. **Agent card validation** (`registry/utils/agent_validator.py`): `_check_endpoint_reachability` calls `httpx.get()` on the well-known agent card URL.
4. **MCP client connections** (`registry/core/mcp_client.py`): Connects to MCP servers at user-supplied base URLs via streamable-http and SSE transports.

None of these code paths validate the resolved IP address of the destination URL before making the request. An attacker who can register servers or agents with URLs pointing to private IP ranges can use the registry server as an SSRF proxy to reach:

- AWS EC2 Instance Metadata Service (`169.254.169.254`)
- Internal services on `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- Localhost services (`127.0.0.0/8`, `::1`)
- Link-local addresses (`169.254.0.0/16`)
- Documentation/multicast ranges (`224.0.0.0/4`, `240.0.0.0/4`)

Platform operators running this service on AWS ECS Fargate are especially exposed, since the registry runs in a VPC with direct network access to internal services and the metadata endpoint.

### Proposed Solution

Add a URL validation utility that resolves the hostname to an IP address and rejects URLs whose resolved IP falls within private, reserved, or otherwise unsafe ranges. Apply this validation at the boundaries where outbound HTTP requests originate.

The validation function should:

1. Parse the URL to extract the hostname.
2. Resolve the hostname to one or more IP addresses (both IPv4 and IPv6).
3. Check each resolved address against a deny list of private/reserved ranges.
4. Raise an exception or return a failure result if any resolved address is blocked.

Use only Python standard library modules (`urllib.parse`, `socket`, `ipaddress`) to avoid new dependencies.

### User Stories

- As a platform operator running mcp-gateway-registry in AWS, I want the registry to block outbound connections to private IPs so that I am protected against SSRF attacks via user-supplied server/agent URLs.
- As a platform operator, I want the change to not break legitimate external URLs so that existing registered servers and agents continue to work.
- As a developer, I want clear error messages when a URL is blocked so that I can understand why the request was rejected.

### Acceptance Criteria

- [ ] A new utility function `validate_outbound_url(url: str) -> None` exists in `registry/utils/url_security.py` (or similar) and uses only stdlib (`ipaddress`, `socket`, `urllib.parse`).
- [ ] The function blocks requests to these IP ranges:
  - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (private)
  - `127.0.0.0/8` (loopback)
  - `169.254.0.0/16` (link-local, includes EC2 IMDS)
  - `0.0.0.0/8` (nowhere)
  - `224.0.0.0/4` (multicast)
  - `240.0.0.0/4` (reserved)
  - IPv6 equivalents: `::1`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96` (IPv4-mapped)
- [ ] The validation is applied at all outbound HTTP entry points:
  - `registry/health/service.py`: `_perform_health_checks` and `perform_immediate_health_check`
  - `registry/api/agent_routes.py`: `check_agent_health`
  - `registry/utils/agent_validator.py`: `_check_endpoint_reachability`
  - `registry/core/mcp_client.py`: connection functions
- [ ] The validation is called once before the HTTP client is created, not on every redirect. If a redirect follows a URL to a blocked IP, the validation should run on the redirect target as well (or httpx `follow_redirects=False` should be used).
- [ ] Unit tests exist for the validation utility covering all blocked ranges and a set of allowed public IPs.
- [ ] Existing tests continue to pass (update test mocks to use allowed test URLs like `http://example.com`).
- [ ] No new Python dependencies are added.

### Out of Scope

- Rate limiting or blocking of repeated SSRF attempts (future work).
- URL allowlisting (operators who need allowlists can deploy a network-level control).
- Changes to the Docker Compose, Terraform, or Helm deployment manifests (no config parameter needed).
- Handling of hostname resolution failures that cause DNS rebinding (future enhancement).

### Dependencies

- This is a self-contained change with no external dependencies.
- Existing dependency: `httpx` (already in pyproject.toml).

### Related Issues

- Security hardening related to outbound requests from server-hosted applications.