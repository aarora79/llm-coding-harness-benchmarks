# Low-Level Design: SSRF Hardening - Outbound URL Validation

*Created: 2026-07-06*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)

## Overview

### Problem Statement

The mcp-gateway-registry makes outbound HTTP requests to URLs supplied by users in multiple code paths:

1. **Server health checks** (`registry/health/service.py`): Background and immediate health checks probe `proxy_pass_url` from registered MCP server configurations using httpx.
2. **Agent health checks** (`registry/api/agent_routes.py`): The `POST /api/agents/{path}/health` endpoint fetches URLs from `agent_card.url` via httpx GET/HEAD.
3. **Agent card validation** (`registry/utils/agent_validator.py`): `_check_endpoint_reachability` calls `httpx.get()` on the well-known agent card URL during registration.
4. **MCP client connections** (`registry/core/mcp_client.py`): Connects to MCP servers at user-supplied base URLs via streamable-http and SSE transports.

None of these code paths validate the resolved IP address before making the outbound request. An attacker who can register servers or agents with URLs pointing to private or reserved IPs can use the registry server as an SSRF proxy to reach the AWS EC2 Instance Metadata Service (169.254.169.254), internal services on private IP ranges, or localhost services.

### Goals

- Block outbound HTTP requests to private, loopback, link-local, multicast, and reserved IP ranges.
- Use only Python standard library modules (`urllib.parse`, `socket`, `ipaddress`) - no new dependencies.
- Apply validation consistently across all four outbound HTTP entry points.
- Preserve functionality for legitimate external URLs (DNS-resolved hostnames pointing to public IPs).
- Provide clear error messages when a URL is rejected.
- Ensure health checks do not follow redirects to blocked IPs (use `follow_redirects=False` in httpx).

### Non-Goals

- Rate limiting or account locking for repeated SSRF attempts.
- URL allowlisting (operators who need allowlists should use network-level controls).
- DNS rebinding protection (future enhancement).
- Changes to deployment manifests (Docker Compose, Terraform, Helm).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/health/service.py` | HealthMonitoringService performs periodic and immediate health checks on registered servers | Primary SSRF vector: makes outbound HTTP requests to `proxy_pass_url` and resolved endpoints via httpx |
| `registry/api/agent_routes.py` | Agent CRUD endpoints including `check_agent_health` | SSRF vector: fetches `agent_card.url` via httpx GET/HEAD |
| `registry/utils/agent_validator.py` | Validates A2A agent cards during registration | SSRF vector: `_check_endpoint_reachability` makes `httpx.get()` to user-supplied URLs |
| `registry/core/mcp_client.py` | MCP client for connecting to external MCP servers | SSRF vector: connects to user-supplied base URLs via streamable-http and SSE transports |
| `registry/core/endpoint_utils.py` | Resolves endpoint URLs from server info | Helper that constructs URLs - validation should happen after resolution |
| `registry/utils/request_utils.py` | Contains `get_client_ip` for inbound IP extraction | Already uses `ipaddress` module - good pattern reference |
| `registry/core/config.py` | Pydantic Settings with all configuration | No config parameter needed for this change |
| `tests/unit/utils/test_url_normalize.py` | Tests for URL normalization utility | Pattern reference for testing URL utilities |
| `tests/unit/health/test_health_service.py` | Tests for health monitoring | Existing tests will need updates for validation |

### Existing Patterns Identified

1. **Pydantic Settings**: All configuration lives in `registry/core/config.py` using pydantic_settings BaseSettings. Fields use `Field()` with descriptions.

2. **HTTP Client**: httpx.AsyncClient is the standard HTTP client. Timeouts are configured via `httpx.Timeout()`. The `follow_redirects=True` parameter is used in health checks.

3. **Logging**: The codebase uses Python standard `logging` module with `logging.basicConfig()`. Logger names follow `logger = logging.getLogger(__name__)`.

4. **URL validation (format only)**: `registry/utils/agent_validator.py` has `_validate_agent_url()` which checks URL format with a regex but does NOT validate resolved IPs.

5. **IP address usage**: `registry/utils/request_utils.py` uses `ipaddress.ip_address()` for validating inbound client IPs. This is the pattern to follow for outbound validation.

6. **Error handling**: FastAPI endpoints raise `HTTPException` with appropriate status codes. Internal utilities raise custom exceptions or return tuples of (success, error_message).

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/health/service.py._perform_health_checks` | Calls validation before httpx request | Validate `proxy_pass_url` and each resolved endpoint URL before making the health check request |
| `registry/health/service.py.perform_immediate_health_check` | Calls validation before httpx request | Validate `proxy_pass_url` before the immediate check |
| `registry/api/agent_routes.py.check_agent_health` | Calls validation on agent_card.url | Validate the base URL and each URL in `_build_agent_health_urls` before making requests |
| `registry/utils/agent_validator.py._check_endpoint_reachability` | Calls validation on agent card URL | Validate the well_known_url before httpx.get() |
| `registry/core/mcp_client.py` functions | Calls validation on base URLs | Validate base_url before any transport detection or connection attempt |

### Constraints and Limitations Discovered

1. **DNS resolution needed for validation**: The function must resolve hostnames to IPs before checking, which adds latency (typically < 50ms for cached DNS). This is acceptable for health checks and registration validation.

2. **Multiple IP addresses**: A single hostname can resolve to multiple IPs (round-robin DNS, CDN). The validation should reject if ANY resolved IP is in a blocked range.

3. **Redirect handling**: httpx `follow_redirects=True` will automatically follow 3xx responses to new URLs. If a redirect target points to a blocked IP, the current code would connect without validation. Either:
   - Use `follow_redirects=False` and validate each redirect target manually, or
   - Accept that httpx will follow at most one redirect by default and validate the redirect URL in the response handler.

4. **Local development**: Developers may register servers with `http://localhost:8000` or `http://127.0.0.1:8000`. The validation must block these in production but there is no mechanism to whitelist localhost for dev environments. This is intentional - localhost access should be handled via direct access, not through the registry.

## Architecture

### System Context Diagram

```
+------------------+         +-------------------+         +------------------+
|   User Client    |         | mcp-gateway-      |         |  External MCP    |
|                  |         | registry          |         |  / Agent Servers |
|  Registers       |-------->|                   |-------->|                  |
|  Server/Agent    |   URL   |  +-------------+  |   URL   |  Public IPs: OK  |
|  with URL        |-------->|  | SSRF Guard  |  |         |  Private IPs:    |
|                  |         |  | (new)       |  |         |  BLOCKED         |
|                  |<--------|  +-------------+  |<--------|                  |
+------------------+  Result |         |         |   Result  +------------------+
                            |         v         |
                            |  +-------------+  |
                            |  | httpx       |  |
                            |  | AsyncClient |  |
                            |  +-------------+  |
                            +-------------------+
```

### Flow Diagram

```
User registers server/agent with URL
    |
    v
Outbound HTTP handler (health check / MCP connect / validation)
    |
    v
validate_outbound_url(url)
    |
    +---> URL cannot be parsed --> raise ValueError("Invalid URL")
    |
    +---> Hostname resolves to blocked IP --> raise SSRFBlockedError("Blocked")
    |
    +---> All IPs are public --> proceed with httpx request
    |
    v
httpx.AsyncClient.get/post(url, ...)
```

### Component: validate_outbound_url

The new utility function will:

1. Parse the URL using `urllib.parse.urlparse()` to extract the hostname.
2. Resolve the hostname using `socket.getaddrinfo()` to get all IP addresses.
3. Check each IP against the blocked ranges using `ipaddress.ip_address()`.
4. Raise `SSRFBlockedError` if any IP is in a blocked range.
5. Return `None` (void) if all IPs are allowed.

## Data Models

### New Exception Class

```python
class SSRFBlockedError(Exception):
    """Raised when a URL resolves to a blocked (private/reserved) IP address."""

    def __init__(self, url: str, blocked_ips: list[str] | None = None):
        self.url = url
        self.blocked_ips = blocked_ips or []
        message = f"SSRF protection: URL resolves to blocked IP range: {url}"
        if self.blocked_ips:
            message += f" (blocked: {', '.join(self.blocked_ips)})"
        super().__init__(message)
```

### Validation Utility: registry/utils/url_security.py

```python
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Single comprehensive IPv4 blocked network set
_BLOCKED_IPV4: list[ipaddress.IPv4Network] = [
    ipaddress.IPv4Network("0.0.0.0/8"),       # "This" network
    ipaddress.IPv4Network("10.0.0.0/8"),      # Private
    ipaddress.IPv4Network("100.64.0.0/10"),   # CGNAT
    ipaddress.IPv4Network("127.0.0.0/8"),     # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # Link-local (EC2 IMDS)
    ipaddress.IPv4Network("172.16.0.0/12"),   # Private
    ipaddress.IPv4Network("192.168.0.0/16"),  # Private
    ipaddress.IPv4Network("224.0.0.0/4"),     # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),     # Reserved
]

# IPv6 blocked networks
_BLOCKED_IPV6: list[ipaddress.IPv6Network] = [
    ipaddress.IPv6Network("::1/128"),              # Loopback
    ipaddress.IPv6Network("::/128"),               # Unspecified
    ipaddress.IPv6Network("::ffff:0:0/96"),        # IPv4-mapped
    ipaddress.IPv6Network("fc00::/7"),             # Unique local
    ipaddress.IPv6Network("fe80::/10"),            # Link-local
    ipaddress.IPv6Network("ff00::/8"),             # Multicast
]
```

### Configuration

No new configuration parameters are needed. The SSRF guard is always-on with no toggle. This is a security control that should never be disabled by operators.

## New Dependencies

No new dependencies. This change uses only Python standard library modules:
- `urllib.parse` - URL parsing
- `socket` - DNS resolution
- `ipaddress` - IP address validation

## Implementation Details

### Step 1: Create the validation utility

**File:** `registry/utils/url_security.py` (new file)

```python
"""SSRF protection: validate outbound URLs against blocked IP ranges.

This module provides a single public function, validate_outbound_url(), that
resolves a URL's hostname to IP addresses and rejects any that fall within
private, loopback, link-local, multicast, or otherwise reserved ranges.

Uses only Python standard library modules (urllib.parse, socket, ipaddress).
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BLOCKED_IPV4 = [
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("224.0.0.0/4"),
    ipaddress.IPv4Network("240.0.0.0/4"),
]

_BLOCKED_IPV6 = [
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("::ffff:0:0/96"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("ff00::/8"),
]


class SSRFBlockedError(Exception):
    """Raised when a URL resolves to a blocked IP range."""

    def __init__(self, url: str, blocked_ips: list[str] | None = None) -> None:
        self.url = url
        self.blocked_ips = blocked_ips or []
        parts: list[str] = [f"SSRF protection: URL resolves to blocked IP range: {url}"]
        if self.blocked_ips:
            parts.append(f"blocked: {', '.join(self.blocked_ips)}")
        super().__init__(" ".join(parts))


def _is_blocked_ipv4(addr: str) -> bool:
    """Return True if addr is in a blocked IPv4 range."""
    network = ipaddress.IPv4Address(addr)
    for blocked in _BLOCKED_IPV4:
        if network in blocked:
            return True
    return False


def _is_blocked_ipv6(addr: str) -> bool:
    """Return True if addr is in a blocked IPv6 range."""
    network = ipaddress.IPv6Address(addr)
    for blocked in _BLOCKED_IPV6:
        if network in blocked:
            return True
    return False


def validate_outbound_url(url: str) -> None:
    """Validate that a URL does not resolve to a blocked IP address.

    Resolves the hostname via DNS and checks every returned IP address
    against the standard private/reserved ranges.  Raises
    SSRFBlockedError if any address is blocked.

    Args:
        url: The full URL to validate (e.g. "https://example.com/path").

    Raises:
        ValueError: If the URL cannot be parsed.
        SSRFBlockedError: If the resolved IP is in a blocked range.
        socket.gaierror: If DNS resolution fails.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise ValueError(f"Cannot extract hostname from URL: {url}")

    # Block bare IPs directly (no DNS needed)
    try:
        addr = ipaddress.ip_address(hostname)
        if isinstance(addr, ipaddress.IPv4Address) and _is_blocked_ipv4(str(addr)):
            raise SSRFBlockedError(url, [str(addr)])
        if isinstance(addr, ipaddress.IPv6Address) and _is_blocked_ipv6(str(addr)):
            raise SSRFBlockedError(url, [str(addr)])
        return  # Public IP literal - allowed
    except ValueError:
        pass  # Not a bare IP, must be a hostname - resolve it

    # Resolve hostname to IPs
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        logger.warning("DNS resolution failed for %s: %s", url, exc)
        raise

    blocked_ips: list[str] = []
    for fam, _socktype, _proto, _canonical, sockaddr in infos:
        ip_str = sockaddr[0]
        if fam == socket.AF_INET and _is_blocked_ipv4(ip_str):
            blocked_ips.append(ip_str)
        elif fam == socket.AF_INET6 and _is_blocked_ipv6(ip_str):
            blocked_ips.append(ip_str)

    if blocked_ips:
        raise SSRFBlockedError(url, blocked_ips)

    logger.debug("Outbound URL validated: %s (resolved to public IPs)", url)
```

### Step 2: Apply validation in health check service

**File:** `registry/health/service.py`

In `_perform_health_checks` (around line 367), validate `proxy_pass_url` before creating the httpx client:

```python
from ..utils.url_security import validate_outbound_url, SSRFBlockedError

# Inside _perform_health_checks, for each service:
if server_info and server_info.get("proxy_pass_url"):
    try:
        validate_outbound_url(server_info["proxy_pass_url"])
    except SSRFBlockedError as exc:
        logger.warning("Health check blocked for %s: %s", service_path, exc)
        self.server_health_status[service_path] = "blocked: ssrf"
        continue  # Skip this service's health check
    except (ValueError, socket.gaierror) as exc:
        logger.warning("Health check validation error for %s: %s", service_path, exc)
        continue
```

In `perform_immediate_health_check` (around line 1217), add the same validation before creating the httpx client.

In `_check_single_service` (around line 429), add validation for `proxy_pass_url`.

For resolved endpoint URLs (via `get_endpoint_url_from_server_info`), validate the resolved endpoint URL before making the httpx request in `_check_server_endpoint_transport_aware`.

### Step 3: Apply validation in agent health check endpoint

**File:** `registry/api/agent_routes.py`

In `check_agent_health` (around line 920), validate URLs before making httpx requests:

```python
from ..utils.url_security import validate_outbound_url, SSRFBlockedError

base_url = str(agent_card.url).rstrip("/")
health_urls = _build_agent_health_urls(base_url)

# Validate all candidate URLs before attempting connections
for url in health_urls:
    try:
        validate_outbound_url(url)
    except SSRFBlockedError as exc:
        logger.warning("Agent health check blocked for %s: %s", path, exc)
        return JSONResponse(
            status_code=403,
            content={"detail": "Health check blocked: URL resolves to a private or reserved IP address"},
        )
```

### Step 4: Apply validation in agent validator

**File:** `registry/utils/agent_validator.py`

In `_check_endpoint_reachability` (around line 211), validate the well_known_url:

```python
from registry.utils.url_security import validate_outbound_url, SSRFBlockedError

try:
    validate_outbound_url(well_known_url)
except SSRFBlockedError as exc:
    logger.warning("Endpoint reachability blocked for %s: %s", url, exc)
    return (False, "SSRF protection: URL resolves to a blocked IP range")
```

### Step 5: Apply validation in MCP client

**File:** `registry/core/mcp_client.py`

In `get_mcp_connection_result` (around line 580), validate `base_url` at the start of the function:

```python
from ..utils.url_security import validate_outbound_url, SSRFBlockedError

if not base_url:
    logger.error("MCP Check Error: Base URL is empty.")
    return None

try:
    validate_outbound_url(base_url)
except SSRFBlockedError as exc:
    logger.warning("MCP connection blocked: %s", exc)
    return None
except (ValueError, socket.gaierror) as exc:
    logger.warning("MCP connection validation error: %s", exc)
    return None
```

Also validate in `get_tools_from_server_with_server_info` and `detect_server_transport_aware`.

### Error Handling

- `SSRFBlockedError`: Caught at the endpoint layer, returns HTTP 403 with a clear message. In background health checks, sets status to "blocked: ssrf". In MCP client, returns None (connection failure).
- `ValueError` (malformed URL): Treated as a validation error. Returns HTTP 400 or skips the health check.
- `socket.gaierror` (DNS failure): Logged as warning, health check skipped, MCP connection fails gracefully.

### Logging

- `DEBUG`: When a URL validates successfully (public IP).
- `WARNING`: When a URL is blocked or DNS fails.
- `INFO`: At endpoint level when a blocked URL is rejected.

## Observability

### Tracing / Metrics / Logging Points

| Event | Level | Message |
|-------|-------|---------|
| URL blocked | WARNING | "SSRF protection: URL resolves to blocked IP range: {url} (blocked: ...)" |
| URL validated | DEBUG | "Outbound URL validated: {url}" |
| DNS failure | WARNING | "DNS resolution failed for {url}: {error}" |
| Health check blocked | WARNING | "Health check blocked for {path}: SSRF protection..." |
| Endpoint blocked response | INFO | "Agent health check blocked for {path}: URL resolves to blocked IP" |
| MCP connection blocked | WARNING | "MCP connection blocked: SSRF protection..." |

No new metrics are needed. Existing health check status ("blocked: ssrf") provides visibility.

## Scaling Considerations

- DNS resolution adds ~10-50ms per validation call. Health checks are already batched with 10-item batches and 0.5s delays, so this overhead is negligible.
- For agent health checks (user-driven), the additional latency is acceptable.
- For MCP client connections, the DNS lookup happens once during connection setup, which is already slow (10-25s total).
- No caching is needed - DNS resolution is fast with OS and library-level caching.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/url_security.py` | SSRF validation utility with `validate_outbound_url()` and `SSRFBlockedError` |
| `tests/unit/utils/test_url_security.py` | Unit tests for the validation utility |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/health/service.py` | ~370-375, ~1220-1225, ~430-440 | Import and call `validate_outbound_url` before httpx requests in `_perform_health_checks`, `perform_immediate_health_check`, and `_check_single_service` |
| `registry/api/agent_routes.py` | ~920-940 | Import and call `validate_outbound_url` in `check_agent_health` before httpx requests |
| `registry/utils/agent_validator.py` | ~212-215 | Import and call `validate_outbound_url` in `_check_endpoint_reachability` before httpx.get() |
| `registry/core/mcp_client.py` | ~585-595 | Import and call `validate_outbound_url` in `get_mcp_connection_result` and related functions |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New utility file | ~100 |
| New test file | ~80 |
| Modified code (4 files) | ~40 |
| **Total** | **~220** |

## Testing Strategy

See `testing.md` for the complete testing plan.

## Alternatives Considered

### Alternative 1: httpx Trustenv + Proxy Configuration

Use httpx's built-in proxy settings to route all outbound traffic through a proxy that filters requests.

**Pros**: Centralized control, no code changes.
**Cons**: Requires infrastructure changes (proxy deployment), adds latency, overkill for this use case.
**Why Rejected**: Code-level validation is simpler and has no infrastructure cost.

### Alternative 2: DNS-Level Filtering

Configure the Docker container / ECS task to use a DNS resolver that returns blocked responses for private IPs.

**Pros**: Network-level protection.
**Cons**: DNS resolution for private IPs already fails (they are not resolvable via public DNS), but bare IP access bypasses this.
**Why Rejected**: Does not protect against bare IP URLs (169.254.169.254). Code-level validation is still needed.

### Alternative 3: VPC Network Policies

Use VPC security groups or network ACLs to block outbound traffic to private IP ranges from the ECS task.

**Pros**: Infrastructure-level protection that covers all processes.
**Cons**: Requires Terraform changes, network policies may interfere with legitimate internal communication (e.g., ECS service discovery).
**Why Rejected**: Network policies are appropriate as defense-in-depth, but code-level validation is the primary control for application-specific SSRF protection. Network policies may also block legitimate internal services that the registry needs to reach.

### Comparison Matrix

| Criteria | Code Validation (Chosen) | Proxy | DNS Filtering | VPC Policies |
|----------|-------------------------|-------|---------------|---------------|
| Complexity | Low | High | Medium | High |
| New dependencies | None | None | None | Terraform changes |
| Infrastructure changes | None | Yes | Yes | Yes |
| Protects bare IPs | Yes | Partial | No | Yes |
| Operational overhead | None | High | Low | Medium |
| Bypass risk | Low | Medium | High | Low |

## Rollout Plan

- Phase 1: Implementation - create url_security.py, apply to all four entry points.
- Phase 2: Testing - unit tests for validation utility, integration tests for each entry point.
- Phase 3: Deployment - no config changes required, deploy as-is.

## Open Questions

- Should we add an environment variable to allow operators to bypass the check in exceptional cases (e.g., internal URL whitelisting)? **Recommendation: No. Security controls should not be toggleable by operators. Network-level controls are the appropriate escape hatch.**
- How do we handle DNS rebinding attacks where the IP changes between validation and connection? **Recommendation: Accept the residual risk. DNS rebinding is a sophisticated attack that requires infrastructure-level mitigation (same-origin policy in browsers, not applicable here). The SSRF guard covers the vast majority of attack vectors.**

## References

- OWASP SSRF: https://owasp.org/www-community/attacks/Server_Side_Request_Forgery
- Python ipaddress module: https://docs.python.org/3/library/ipaddress.html
- RFC 1918: Address Allocation for Private Internets
- RFC 5735: IANA Special-Purpose Address Registry
- Existing code: `registry/utils/request_utils.py` (get_client_ip pattern)
- Existing code: `registry/utils/agent_validator.py` (_validate_agent_url - format-only validation)