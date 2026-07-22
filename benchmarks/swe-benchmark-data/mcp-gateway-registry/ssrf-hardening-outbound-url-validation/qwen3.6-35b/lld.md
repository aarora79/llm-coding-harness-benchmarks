# Low-Level Design: SSRF Hardening - Promote Shared URL Validation

*Created: 2026-07-22*
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

The MCP Gateway Registry makes outbound HTTP requests to user-supplied URLs in multiple code paths without SSRF protection. A guard function `_is_safe_url()` exists in `registry/services/skill_service.py` and is correctly applied to SKILL.md fetches (skill registration, health checks, content fetching), but is **not reused** in the agent-card fetch and server health-check paths.

The vulnerable endpoints:

| Code Path | File | Function | User-Supplied URL Field |
|-----------|------|----------|------------------------|
| Agent registration reachability check | `registry/utils/agent_validator.py` | `_check_endpoint_reachability()` | `url` from `AgentCard` |
| Agent health check endpoint | `registry/api/agent_routes.py` | `check_agent_health()` | `agent_card.url` |
| Server background health checks | `registry/health/service.py` | `_check_single_service()` | `proxy_pass_url` from server info |
| Server immediate health check | `registry/health/service.py` | `perform_immediate_health_check()` | `proxy_pass_url` from server info |
| MCP client connections | `registry/core/mcp_client.py` | `get_mcp_connection_result()` | `base_url` |

### Goals

- Promote the existing `_is_safe_url()` into a shared utility so all outbound-fetch callers use the same logic.
- Apply the promoted function to the five vulnerable code paths identified above.
- Add an operator-configurable hostname allowlist (`MCP_GATEWAY_EXTRA_TRUSTED_HOSTS`) for internal hosts that must remain reachable.
- Validate redirect targets after every HTTP request (same pattern already used in `skill_service.py`).
- Prevent redirect-based SSRF bypass by using `follow_redirects=False` on all affected httpx.AsyncClient instantiations.
- Zero new Python dependencies.

### Non-Goals

- Changing the `_is_safe_url()` validation algorithm (IP private-check, DNS resolution, trusted domains).
- Adding SSRF protection to webhook calls or federation peer-sync.
- Modifying httpx, timeout settings, or redirect policy beyond `follow_redirects=False`.
- DNS rebinding protection (infrastructure-level control only).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/skill_service.py` | Contains `_is_safe_url()`, `_is_private_ip()`, `_trusted_domains()` | Source of the existing SSRF guard to be promoted |
| `registry/api/agent_routes.py` | Agent CRUD endpoints, `check_agent_health()` at line 883 | Agent card fetch with NO SSRF guard |
| `registry/utils/agent_validator.py` | `_check_endpoint_reachability()` at line 196 | Agent registration reachability check with NO SSRF guard |
| `registry/health/service.py` | `HealthMonitoringService`, `_check_single_service()` at line 410 | Server health checks with NO SSRF guard |
| `registry/core/config.py` | Pydantic Settings (`Settings` class) | Pattern reference for new config parameter |
| `registry/core/mcp_client.py` | MCP client connections to user-supplied base URLs | Additional candidate for validation |
| `registry/utils/request_utils.py` | `get_client_ip()` using `ipaddress` module | Pattern reference for IP validation |
| `tests/unit/services/test_skill_service_ssrf_allowlist.py` | Existing tests for `_trusted_domains()` | Test pattern reference |

### Existing Patterns Identified

1. **SSRF guard in skill_service.py** (`_is_safe_url()`, lines 128-192):
   - Parses URL scheme (only `http`/`https` allowed).
   - Checks hostname against `_trusted_domains()` allowlist (built-in + `github_extra_hosts`).
   - Resolves hostname via `socket.getaddrinfo()` and checks each IP with `_is_private_ip()`.
   - `_is_private_ip()` blocks `is_private`, `is_loopback`, `is_link_local`, `is_reserved`, and `169.254.169.254`.
   - Returns `False` on any exception (fail-closed).

2. **Trusted domains pattern** (`_trusted_domains()`, lines 81-91):
   - Uses `@lru_cache(maxsize=1)` for immutability.
   - Reads `settings.github_extra_hosts` (comma-separated) and merges with `_DEFAULT_TRUSTED_DOMAINS`.
   - **Design decision for this change**: The new shared module will read `settings.mcp_gateway_extra_trusted_hosts` instead. The `github_extra_hosts` field is used only by GitHub-specific code paths (PAT/auth). A single `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` env var is the correct general-purpose setting for all SSRF trust decisions. This is a one-line change in `_trusted_domains()`.

3. **Redirect validation** (used in `_validate_skill_md_url`, `_parse_skill_md_content`, `_check_skill_health`, `_fetch_authenticated_content`):
   - After each httpx request with `follow_redirects=True`, the final URL is validated with `_is_safe_url()` again.
   - If the redirect target is unsafe, a validation error is raised.

4. **Pydantic Settings** (`registry/core/config.py`):
   - All settings are fields on the `Settings(BaseSettings)` class.
   - Configuration is loaded from environment variables (e.g., `GITHUB_EXTRA_HOSTS`).
   - Fields use `Field()` with `default`, `description`.
   - `model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)`.

5. **HTTP client pattern**:
   - `httpx.AsyncClient` is the standard client.
   - Timeouts set via `httpx.Timeout()`.
   - `follow_redirects=True` used as default in health checks. **This design mandates `follow_redirects=False` for all new call sites and existing call sites in the five affected code paths.**

6. **Logging pattern**:
   - `logging.basicConfig(level=logging.INFO, format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s")`.
   - Logger: `logger = logging.getLogger(__name__)`.
   - SSRF events use `logger.warning()`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `skill_service.py._is_safe_url()` | Source to extract | Promote to shared module |
| `skill_service.py._trusted_domains()` | Source to adapt | Read `mcp_gateway_extra_trusted_hosts` instead of `github_extra_hosts` |
| `agent_routes.py.check_agent_health()` | Add validation | Before httpx GET/HEAD on agent URL; use `follow_redirects=False` |
| `agent_validator.py._check_endpoint_reachability()` | Add validation | Before `httpx.get()` on well-known URL; use `follow_redirects=False` |
| `health/service.py._check_single_service()` | Add validation | Before `_check_server_endpoint_transport_aware()` |
| `health/service.py.perform_immediate_health_check()` | Add validation | Before httpx client creation |
| `mcp_client.py.get_mcp_connection_result()` | Add validation | Before establishing MCP connection |

### Constraints and Limitations Discovered

1. **DNS resolution latency**: `_is_safe_url()` performs DNS resolution on every call. Health checks are already batched (10 per batch, 0.5s between batches), so the extra ~10-50ms per validation is negligible.

2. **Multiple IP addresses per hostname**: A hostname can resolve to both public and private IPs (e.g., dual-stack with IPv6 mapped to private). The current code rejects if ANY resolved IP is private, which is the safe choice.

3. **Redirect handling in health checks**: The current `health/service.py` uses `follow_redirects=True` (inherited from httpx defaults). **This design mandates `follow_redirects=False` on all affected httpx.AsyncClient calls and manual redirect validation** (see Step 6 below).

4. **Local development**: Developers may register servers with `http://localhost:8000`. The guard will block these. The new `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` setting does NOT provide a localhost bypass - localhost should be accessed directly, not through the registry.

5. **Existing callers**: `skill_service.py` imports and uses `_is_safe_url()` and `_trusted_domains()` internally. After promotion, `skill_service.py` will import them from the shared module to avoid duplication.

## Architecture

### System Context Diagram

```
+------------------+         +-------------------+         +------------------+
|   User Client    |         | mcp-gateway-      |         |  External MCP    |
|                  |         | registry          |         |  / Agent Servers |
|  Registers       |-------->|                   |-------->|                  |
|  Server/Agent    |   URL   |  +-------------+  |   URL   |  Public IPs: OK  |
|  with URL        |-------->|  | SSRF Guard  |  |         |  Private IPs:    |
|                  |<--------|  | (shared)    |  |<--------|  BLOCKED         |
+------------------+  Result  +-------------+  |   Result  +------------------+
                               |         |         |
                               v         v         |
                              +-------------+      |
                              | httpx       |      |
                              | AsyncClient |      |
                              | (no redirects) |    |
                              +-------------+      |
                                             +------------------+
                                             |  Internal        |
                                             |  Services / IMDS |
                                             |  BLOCKED BY SSRF |
                                             +------------------+
```

### Flow Diagram

```
Outbound HTTP request handler
    |
    v
_is_safe_url(url)                       <-- Shared utility, imported everywhere
    |
    +---> Scheme not http/https --> return False
    |
    +---> Hostname in trusted domains --> return True
    |
    +---> Bare IP in blocked ranges --> return False
    |
    +---> Resolve hostname to IPs
    |       |
    |       +---> DNS failure --> return False
    |       |
    |       +---> ANY IP is private --> return False
    |       |
    |       +---> All IPs public --> return True
    |
    v
httpx.AsyncClient(follow_redirects=False)
    |
    v
Handle redirects manually:
    |
    +---> 3xx response with Location header
    |       |
    |       +---> Validate redirect target with _is_safe_url()
    |       |       |
    |       |       +---> Unsafe --> block, return 403
    |       |       |
    |       |       +---> Safe --> follow redirect
    |       |
    |       +---> 2xx response --> process
    |
    v
Process response
```

## Data Models

### New Exception Class

```python
class SSRFBlockedError(Exception):
    """Raised when a URL resolves to a blocked (private/reserved) IP address."""

    def __init__(self, url: str, blocked_ips: list[str] | None = None) -> None:
        self.url = url
        self.blocked_ips = blocked_ips or []
        parts = [f"SSRF protection: URL resolves to blocked IP range: {url}"]
        if self.blocked_ips:
            parts.append(f"blocked: {', '.join(self.blocked_ips)}")
        super().__init__(" ".join(parts))
```

### Shared Utility Module: `registry/utils/url_security.py`

This module centralizes all SSRF protection logic.

```python
"""SSRF protection: validate outbound URLs against blocked IP ranges.

Promoted from registry/services/skill_service.py to make the guard reusable
across all outbound-fetch code paths (agent cards, server health checks, etc.).
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

from registry.core.config import settings

logger = logging.getLogger(__name__)

SSRF_BLOCKED_STATUS = "blocked: ssrf"

# Built-in trusted domains that skip IP validation (SSRF allowlist).
_DEFAULT_TRUSTED_DOMAINS: frozenset = frozenset({
    "github.com",
    "gitlab.com",
    "raw.githubusercontent.com",
    "bitbucket.org",
})


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is private, loopback, link-local, or reserved.

    This uses Python's built-in ipaddress module checks, which cover
    RFC 1918 private ranges, loopback, link-local, reserved, and
    multicast. An explicit check for the cloud metadata endpoint
    (169.254.169.254) is included for defense-in-depth.

    Args:
        ip_str: IP address string to check.

    Returns:
        True if the IP is blocked, False otherwise.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return True

    if ip_str == "169.254.169.254":
        return True

    return False


def _is_trusted_hostname(hostname_lower: str) -> bool:
    """Check if a hostname is in the trusted domains allowlist.

    Trusted domains skip the IP-check because they are known good hosts.
    The allowlist includes built-in defaults plus any hosts configured
    via the MCP_GATEWAY_EXTRA_TRUSTED_HOSTS setting.

    Args:
        hostname_lower: Lowercase hostname to check.

    Returns:
        True if the hostname is trusted.
    """
    return hostname_lower in _trusted_domains()


@lru_cache(maxsize=1)
def _trusted_domains() -> frozenset[str]:
    """Return SSRF allowlist: built-in defaults plus configured extra hosts.

    Reads settings.mcp_gateway_extra_trusted_hosts (comma-separated) so a
    single config knob covers all SSRF trust decisions. Cached because
    settings are immutable per-process.

    Returns:
        Frozenset of allowed hostnames that skip IP validation.
    """
    extra_raw = getattr(settings, "mcp_gateway_extra_trusted_hosts", "") or ""
    extra = frozenset(
        h.strip().lower() for h in extra_raw.split(",") if h.strip()
    )
    return _DEFAULT_TRUSTED_DOMAINS | extra


def is_safe_url(url: str) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).

    This function validates that a URL:
    1. Uses http or https scheme.
    2. Does not resolve to a private/loopback/link-local IP address.
    3. Does not target cloud metadata endpoints.

    Trusted domains (github.com, gitlab.com, etc., plus any host configured
    via MCP_GATEWAY_EXTRA_TRUSTED_HOSTS) skip the IP check so internal hosts
    on private networks remain reachable.

    Args:
        url: URL to validate.

    Returns:
        True if the URL is safe to fetch, False otherwise.
    """
    try:
        parsed = urlparse(url)

        # Check scheme - only allow http and https
        if parsed.scheme not in ("http", "https"):
            logger.warning("SSRF protection: Blocked URL with scheme '%s'", parsed.scheme)
            return False

        hostname = parsed.hostname
        if not hostname:
            logger.warning("SSRF protection: URL has no hostname")
            return False

        # Check if hostname is in trusted domains allowlist
        hostname_lower = hostname.lower()
        if _is_trusted_hostname(hostname_lower):
            logger.debug("SSRF protection: Trusted domain '%s'", hostname_lower)
            return True

        # Block bare IPs that match blocked ranges (no DNS needed)
        try:
            addr = ipaddress.ip_address(hostname)
            if _is_private_ip(str(addr)):
                logger.warning(
                    "SSRF protection: Blocked bare %s address %s in URL %s",
                    type(addr).__name__, addr, url,
                )
                return False
        except ValueError:
            pass  # Not a bare IP, must be a hostname - resolve it

        # Resolve hostname to IP addresses
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addr_info = socket.getaddrinfo(
                hostname, port, proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            logger.warning(
                "SSRF protection: Failed to resolve hostname '%s': %s", hostname, exc,
            )
            return False

        # Check all resolved IP addresses
        blocked_ips: list[str] = []
        for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
            ip_address = sockaddr[0]
            if _is_private_ip(ip_address):
                blocked_ips.append(ip_address)

        if blocked_ips:
            logger.warning(
                "SSRF protection: Blocked URL resolving to private IP(s) %s "
                "for hostname '%s'",
                blocked_ips, hostname,
            )
            return False

        return True

    except Exception as exc:
        logger.warning("SSRF protection: Error validating URL: %s", exc)
        return False
```

### Backward-Compatible Re-exports in `skill_service.py`

After promotion, `skill_service.py` imports from the shared module instead of defining its own:

```python
# In registry/services/skill_service.py - replace the local definitions with:
from ..utils.url_security import (
    SSRF_BLOCKED_STATUS,
    SSRFBlockedError,
    _DEFAULT_TRUSTED_DOMAINS,
    _is_private_ip,
    _trusted_domains,
    is_safe_url,
)

# Keep aliases for existing callers within skill_service.py
_is_safe_url = is_safe_url
```

This ensures no internal import breakage.

### New Config Parameter

Add to `Settings` class in `registry/core/config.py`:

```python
# SSRF Trusted Hosts (applies to all outbound fetches)
mcp_gateway_extra_trusted_hosts: str = Field(
    default="",
    description=(
        "Comma-separated extra hostnames that skip the SSRF private-IP check. "
        "Use for internal service hosts that must be reachable by the registry. "
        "Example: 'internal.example.com,registry.internal.local'. "
        "Keep the list tight - only add hosts you explicitly trust."
    ),
)
```

Environment variable: `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS`.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create the shared URL security utility

**File:** `registry/utils/url_security.py` (new file)

Create the file with the content shown in the Data Models section above. This includes:
- `SSRF_BLOCKED_STATUS` constant
- `SSRFBlockedError` exception class
- `_is_private_ip()` helper (single source of truth for IP checking)
- `_is_trusted_hostname()` / `_trusted_domains()` functions
- Main `is_safe_url()` function

Verify syntax:
```bash
uv run python -m py_compile registry/utils/url_security.py
```

#### Step 2: Refactor skill_service.py to use the shared module

**File:** `registry/services/skill_service.py`

1. Remove the local definitions of `_is_safe_url()`, `_is_private_ip()`, `_trusted_domains()`, `_DEFAULT_TRUSTED_DOMAINS` (approximately lines 60-192, 71-91, 94-125).
2. Add imports from the shared module at the top of the file.
3. Add `_is_safe_url = is_safe_url` alias at the module level.
4. Verify existing callers in `skill_service.py` (lines 595, 616, 681, 707, 866, 896, 1042, 1071) still resolve correctly.

#### Step 3: Apply validation in agent validator

**File:** `registry/utils/agent_validator.py`

In `_check_endpoint_reachability()` (around line 211), add validation before the `httpx.get()` call:

```python
from ..utils.url_security import is_safe_url

def _check_endpoint_reachability(url: str) -> tuple[bool, str | None]:
    """Check if agent endpoint is reachable."""
    try:
        well_known_url = f"{url}/.well-known/agent-card.json"

        # SSRF guard - validate before making the request
        if not is_safe_url(well_known_url):
            logger.warning("SSRF protection: Blocked reachability check for %s", url)
            return (False, "SSRF protection: URL resolves to a blocked IP range")

        response = httpx.get(well_known_url, timeout=5.0)
        # ... rest of function unchanged
```

Also validate the base `url` in `validate_agent_card()` before calling `_check_endpoint_reachability()`:

```python
if check_reachability and agent_card.url:
    card_url = str(agent_card.url)
    if not is_safe_url(card_url):
        warnings.append("Agent endpoint URL blocked by SSRF guard")
        logger.warning("SSRF protection: Blocked agent URL %s", card_url)
    else:
        reachable, error_msg = _check_endpoint_reachability(card_url)
```

#### Step 4: Apply validation in agent routes health check

**File:** `registry/api/agent_routes.py`

In `check_agent_health()` (around line 920), validate URLs before making httpx requests:

```python
from ..utils.url_security import is_safe_url

@router.post("/agents/{path:path}/health")
async def check_agent_health(path: str, ...):
    # ... existing code until line 920 ...

    base_url = str(agent_card.url).rstrip("/")
    health_urls = _build_agent_health_urls(base_url)

    # SSRF guard - validate all candidate URLs before attempting connections
    for url in health_urls:
        if not is_safe_url(url):
            logger.warning(
                "Agent health check blocked for %s: URL %s failed SSRF validation",
                path, url,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "detail": "Health check blocked: URL resolves to a private or reserved IP address",
                },
            )

    # ... rest of function unchanged ...
```

In the agent registration endpoint (`register_agent`), validate the URL before `verify_endpoint=True`:

```python
from ..utils.url_security import is_safe_url

# In register_agent(), after building agent_card:
card_url = str(agent_card.url)
if not is_safe_url(card_url):
    logger.warning(
        "Agent registration blocked for '%s': URL %s failed SSRF validation",
        request.name, card_url,
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "message": "Agent card validation failed",
            "errors": ["Agent URL is blocked by SSRF protection"],
            "warnings": [],
        },
    )
```

#### Step 5: Apply validation in health monitoring service

**File:** `registry/health/service.py`

In `_check_single_service()` (around line 410), validate `proxy_pass_url` before calling `_check_server_endpoint_transport_aware()`:

```python
from ..utils.url_security import SSRF_BLOCKED_STATUS, is_safe_url

async def _check_single_service(self, client, service_path: str, server_info: dict) -> bool:
    """Check a single service and return True if status changed."""
    previous_status = self.server_health_status.get(service_path, HealthStatus.UNKNOWN)

    # Local (stdio) servers ...
    if server_info.get("deployment") == DeploymentType.LOCAL:
        # ... existing code ...

    proxy_pass_url = server_info.get("proxy_pass_url")
    new_status = previous_status

    # SSRF guard - validate proxy_pass_url before any health check
    if proxy_pass_url and not is_safe_url(proxy_pass_url):
        logger.warning(
            "Health check blocked for %s: proxy_pass_url %s failed SSRF validation",
            service_path, proxy_pass_url,
        )
        new_status = SSRF_BLOCKED_STATUS
        self.server_health_status[service_path] = new_status
        self.server_last_check_time[service_path] = datetime.now(UTC)
        return previous_status != new_status
```

In `perform_immediate_health_check()` (around line 1217), add the same validation:

```python
async def perform_immediate_health_check(self, service_path: str) -> tuple[str, datetime | None]:
    # ... existing code ...
    proxy_pass_url = server_info.get("proxy_pass_url")

    # SSRF guard
    if proxy_pass_url and not is_safe_url(proxy_pass_url):
        logger.warning(
            "Immediate health check blocked for %s: SSRF protection",
            service_path,
        )
        self.server_health_status[service_path] = SSRF_BLOCKED_STATUS
        last_checked_time = datetime.now(UTC)
        self.server_last_check_time[service_path] = last_checked_time
        return SSRF_BLOCKED_STATUS, last_checked_time
```

In `_perform_health_checks()` (around line 339), add validation at the batch level:

```python
async def _perform_health_checks(self):
    # ... existing code ...
    for service_path in enabled_services:
        server_info = await server_service.get_server_info(service_path, include_credentials=True)
        if server_info and server_info.get("proxy_pass_url"):
            # SSRF guard - skip entire service if URL is blocked
            if not is_safe_url(server_info["proxy_pass_url"]):
                logger.warning(
                    "Skipping health check for %s: URL failed SSRF validation",
                    service_path,
                )
                self.server_health_status[service_path] = SSRF_BLOCKED_STATUS
                self.server_last_check_time[service_path] = datetime.now(UTC)
                status_changed = True
                continue  # Skip this service, don't add to check_tasks
            check_tasks.append((service_path, server_info))
```

#### Step 6: Apply validation in MCP client

**File:** `registry/core/mcp_client.py`

Add validation at the start of `get_mcp_connection_result()`:

```python
from ..utils.url_security import is_safe_url

async def get_mcp_connection_result(base_url: str, server_info: dict) -> dict | None:
    if not base_url:
        logger.error("MCP Check Error: Base URL is empty.")
        return None

    if not is_safe_url(base_url):
        logger.warning("MCP connection blocked: %s failed SSRF validation", base_url)
        return {"error": "URL blocked by SSRF protection", "tools": None}
    # ... rest unchanged ...
```

Also validate resolved endpoint URLs in `_check_server_endpoint_transport_aware` (the `endpoint` and `sse_endpoint` returned by `get_endpoint_url_from_server_info`):

```python
# After resolving endpoint:
endpoint = get_endpoint_url_from_server_info(server_info, transport_type="streamable-http")

if not is_safe_url(endpoint):
    logger.warning("SSRF protection: Resolved endpoint %s blocked", endpoint)
    return False, "unhealthy: SSRF protection blocked resolved endpoint"
```

#### Step 7: Redirect handling - mandate `follow_redirects=False`

**IMPORTANT**: All httpx.AsyncClient instantiations in the five affected code paths must use `follow_redirects=False`. This prevents redirect-based SSRF bypass where an attacker controls a domain that returns a 302 redirect to a private IP.

The redirect validation pattern already exists in `skill_service.py` for skill fetches. Apply the same pattern to the new call sites:

```python
# In check_agent_health() and other affected functions:

# Replace:
#   response = await client.get(url)

# With:
response = await client.get(url, follow_redirects=False)
final_url = str(response.url)
if response.status_code in (301, 302, 303, 307, 308):
    location = response.headers.get("location", "")
    if location and not is_safe_url(location):
        logger.warning(
            "SSRF protection: Blocked redirect from %s to %s",
            url, location,
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": "Health check blocked: redirect target is not accessible",
            },
        )
    # Optionally follow the redirect manually if safe
    # (Most health checks can treat redirects as "not healthy" without
    # following, which is simpler and safer.)
```

For the health-service path, the simplest approach is to treat HTTP 3xx responses as "unhealthy" without following redirects:

```python
response = await client.get(url, follow_redirects=False)
if response.status_code >= 300 and response.status_code < 400:
    logger.warning(
        "Health check: URL %s returned redirect (%s) - not following",
        url, response.status_code,
    )
    return False, "unhealthy: redirect not followed"
```

For the agent-validator reachability check (synchronous `httpx.get`), apply the same:

```python
response = httpx.get(well_known_url, timeout=5.0, follow_redirects=False)
if response.status_code >= 300 and response.status_code < 400:
    location = response.headers.get("location", "")
    if location and not is_safe_url(location):
        logger.warning("SSRF protection: Blocked redirect to %s", location)
        return (False, "SSRF protection: redirect target is blocked")
```

#### Step 8: Add unit tests

**File:** `tests/unit/utils/test_url_security.py` (new file)

Create comprehensive tests covering:
- All blocked IPv4 ranges
- All blocked IPv6 ranges
- Allowed public IPv4/IPv6 addresses
- Hostname resolution (mocked)
- Redirect target validation
- Empty/malformed URLs
- DNS failure handling
- Integration-level tests for each of the five affected endpoints

#### Step 9: Update existing tests

Review and update existing tests that use private IPs or localhost in URL fields:
- `tests/unit/services/test_skill_service_ssrf_allowlist.py`
- `tests/unit/api/test_skill_routes_github_auth.py`
- `tests/unit/api/test_skill_inline_content.py`
- Health check tests in `tests/unit/health/`
- Agent validation tests

Where existing tests use private URLs for mocking, mock `socket.getaddrinfo` or use `http://example.com` instead.

### Error Handling

| Exception | Caught By | Response |
|-----------|-----------|----------|
| `SSRFBlockedError` | Agent routes | HTTP 403 with generic message |
| `is_safe_url() == False` | Agent routes | HTTP 403 with generic message |
| `is_safe_url() == False` | Health service | Status set to `"blocked: ssrf"` |
| `is_safe_url() == False` | Agent validator | Warning returned, registration not blocked (warn-only) |
| `is_safe_url() == False` | MCP client | Returns `{"error": "URL blocked", "tools": None}` |

### Logging

| Event | Level | Message |
|-------|-------|---------|
| URL blocked | WARNING | "SSRF protection: Blocked URL with scheme '%s'" or "SSRF protection: Blocked URL resolving to private IP(s) %s for hostname '%s'" |
| URL validated | DEBUG | "SSRF protection: Trusted domain '{hostname}'" or DNS resolution success |
| DNS failure | WARNING | "SSRF protection: Failed to resolve hostname '{hostname}': {error}" |
| Health check blocked | WARNING | "Health check blocked for {path}: proxy_pass_url {url} failed SSRF validation" |
| Agent health blocked | WARNING | "Agent health check blocked for {path}: URL {url} failed SSRF validation" |
| Redirect blocked | WARNING | "SSRF protection: Blocked redirect from {url} to {location}" |
| Redirect not followed | WARNING | "Health check: URL {url} returned redirect ({status_code}) - not following" |

## Observability

### Tracing / Metrics / Logging Points

| Event | Level | Message |
|-------|-------|---------|
| URL blocked | WARNING | "SSRF protection: Blocked URL resolving to private IP(s) {ips} for hostname '{host}'" |
| Trusted domain | DEBUG | "SSRF protection: Trusted domain '{hostname}'" |
| DNS failure | WARNING | "SSRF protection: Failed to resolve hostname '{hostname}': {error}" |
| Health check blocked | WARNING | "Health check blocked for {path}: URL failed SSRF validation" |
| Agent blocked | WARNING | "Agent health check blocked for {path}: URL {url} failed SSRF validation" |
| Redirect blocked | WARNING | "SSRF protection: Blocked redirect from {from_url} to {to_url}" |

No new metrics are needed. Existing health check status values (e.g., `"blocked: ssrf"`) provide visibility. The `SSRF_BLOCKED_STATUS` constant ensures consistent status strings across all three health-service locations.

## Scaling Considerations

- DNS resolution adds ~10-50ms per validation call (cached DNS is typically <10ms).
- Health checks are already batched (10 per batch with 0.5s delays), so the overhead is negligible.
- For agent health checks (user-driven), the additional latency is acceptable.
- For MCP client connections, the DNS lookup happens once during connection setup.
- No caching is needed beyond what `lru_cache` on `_trusted_domains()` provides.
- `follow_redirects=False` may cause health checks to report "unhealthy" for servers that legitimately use 302 redirects for canonical URL handling. This is an acceptable trade-off for SSRF protection.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/url_security.py` | Shared SSRF validation utility with `is_safe_url()`, `SSRFBlockedError`, helper functions |
| `tests/unit/utils/test_url_security.py` | Unit tests for the validation utility |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/services/skill_service.py` | ~60-192 | Remove local `_is_safe_url()`, `_is_private_ip()`, `_trusted_domains()` definitions; import from shared module |
| `registry/api/agent_routes.py` | ~920-945, ~577-580 | Import and call `is_safe_url()` in `check_agent_health()` and `register_agent()` |
| `registry/utils/agent_validator.py` | ~211-220, ~313-318 | Import and call `is_safe_url()` in `_check_endpoint_reachability()` and `validate_agent_card()` |
| `registry/health/service.py` | ~367-375, ~429-438, ~1217-1226 | Import and call `is_safe_url()` in `_perform_health_checks()`, `_check_single_service()`, `perform_immediate_health_check()` |
| `registry/core/mcp_client.py` | ~580-590 | Import and call `is_safe_url()` in `get_mcp_connection_result()` |
| `registry/core/config.py` | ~1150 | Add `mcp_gateway_extra_trusted_hosts` field to Settings |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New utility file | ~120 |
| New test file | ~100 |
| Modified skill_service.py (removal + re-import) | ~-80 net |
| Modified agent_routes.py | ~20 |
| Modified agent_validator.py | ~15 |
| Modified health/service.py | ~30 |
| Modified mcp_client.py | ~10 |
| Modified config.py | ~8 |
| **Total** | **~223** |

## Testing Strategy

See `testing.md` for the complete testing plan.

## Alternatives Considered

### Alternative 1: Create a brand-new SSRF utility from scratch

Create a completely new `validate_outbound_url()` function instead of promoting `_is_safe_url()`.

**Pros**: Clean slate, no risk of breaking existing skill_service.py callers.
**Cons**: Duplicates ~100 lines of working code. Two SSRF guards in the codebase increase maintenance burden and the risk of divergence.
**Why Rejected**: The existing `_is_safe_url()` is well-tested and working. Promoting it avoids code duplication and ensures consistent behavior.

### Alternative 2: httpx TrustEnv + Proxy Configuration

Use httpx's built-in proxy settings to route all outbound traffic through a proxy that filters requests.

**Pros**: Centralized control, no code changes.
**Cons**: Requires infrastructure changes (proxy deployment), adds latency, overkill for this use case.
**Why Rejected**: Code-level validation is simpler and has no infrastructure cost.

### Alternative 3: VPC Network Policies

Use VPC security groups or network ACLs to block outbound traffic to private IP ranges from the ECS task.

**Pros**: Infrastructure-level protection that covers all processes.
**Cons**: Requires Terraform changes, network policies may interfere with legitimate internal communication (e.g., ECS service discovery).
**Why Rejected**: Network policies are appropriate as defense-in-depth, but code-level validation is the primary control for application-specific SSRF protection.

### Comparison Matrix

| Criteria | Promote Existing (Chosen) | New From Scratch | Proxy | VPC Policies |
|----------|--------------------------|-------------------|-------|--------------|
| Complexity | Low | Medium | High | High |
| New dependencies | None | None | None | Terraform changes |
| Infrastructure changes | None | None | Yes | Yes |
| Code duplication | Eliminated | Created | None | None |
| Operational overhead | None | Medium (maintain two guards) | High | Medium |
| Bypass risk | Low | Medium (divergence) | Medium | Low |

## Rollout Plan

- Phase 1: Create `registry/utils/url_security.py` with the promoted `_is_safe_url()`.
- Phase 2: Refactor `skill_service.py` to import from the shared module.
- Phase 3: Apply `is_safe_url()` to the five vulnerable code paths (agent validator, agent routes, health service, MCP client).
- Phase 4: Apply `follow_redirects=False` and redirect validation to all new call sites.
- Phase 5: Add new unit tests and update existing tests.
- Phase 6: Add `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` config parameter.
- Phase 7: Run full test suite, verify no regressions.

## Open Questions

- Should we add an environment variable to allow operators to bypass the check in exceptional cases (e.g., internal URL whitelisting)? **Recommendation: No. Security controls should not be toggleable by operators. Network-level controls are the appropriate escape hatch. The `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` setting only adds hostnames to the trust allowlist, it does not bypass the guard entirely.**
- How do we handle DNS rebinding attacks where the IP changes between validation and connection? **Recommendation: Accept the residual risk. DNS rebinding is a sophisticated attack that requires infrastructure-level mitigation. The SSRF guard covers the vast majority of attack vectors.**
- Will `follow_redirects=False` break legitimate health checks that rely on 302 redirects? **Recommendation: Treat redirects as "unhealthy" rather than following them. This is a conservative but safe approach. If legitimate redirects are observed in production, the health check can be updated to follow redirects to a validated target on a case-by-case basis.**

## References

- OWASP SSRF: https://owasp.org/www-community/attacks/Server_Side_Request_Forgery
- Python ipaddress module: https://docs.python.org/3/library/ipaddress.html
- RFC 1918: Address Allocation for Private Internets
- RFC 5735: IANA Special-Purpose Address Registry
- Existing code: `registry/utils/request_utils.py` (get_client_ip pattern)
- Existing code: `registry/services/skill_service.py` (`_is_safe_url()` at lines 128-192)
- Existing code: `registry/services/skill_service.py` (`_trusted_domains()` at lines 81-91)