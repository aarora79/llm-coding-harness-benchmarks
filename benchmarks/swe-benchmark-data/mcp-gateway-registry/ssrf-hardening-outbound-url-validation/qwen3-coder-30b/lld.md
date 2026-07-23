# Low-Level Design: SSRF Hardening - Validate Outbound URLs on Agent-Card Fetch and Health Check Endpoints

*Created: 2026-07-23*
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
The MCP Gateway & Registry application lacks SSRF (Server-Side Request Forgery) protection on outbound HTTP requests made to user-supplied URLs in two key areas:
1. Agent-card fetch endpoints (used for A2A agent discovery)
2. Server health-check endpoints (used to validate registered server URLs)

While the skill fetch functionality already implements robust SSRF protection via `_is_safe_url()`, these two paths do not reuse the existing validation logic, creating security gaps.

### Goals
- Implement consistent SSRF protection across all outbound HTTP requests to user-supplied URLs
- Promote existing `_is_safe_url()` utility to a shared location for reuse
- Maintain backwards compatibility with existing legitimate use cases
- Follow established security patterns in the codebase

### Non-Goals
- Modify inbound API endpoints that accept user URLs (already protected)
- Change the core authentication mechanisms
- Implement complex allowlisting for all possible internal addresses (focus on known dangerous patterns)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/skill_service.py` | Contains existing `_is_safe_url()` function | Reference for existing implementation and validation patterns |
| `registry/api/agent_routes.py` | Contains agent-card fetch endpoints | Target for SSRF protection |
| `registry/api/server_routes.py` | Contains server health-check endpoints | Target for SSRF protection |
| `registry/health/service.py` | Contains health check logic | May need URL validation for external endpoints |

### Existing Patterns Identified
1. **URL Validation Pattern**: The `_is_safe_url()` function in `skill_service.py` provides a proven pattern for validating URLs against SSRF threats
2. **Shared Utility Approach**: Functions like `_is_safe_url` are defined in service modules and used across different routes
3. **IP Address Filtering**: The codebase already implements IP address validation to prevent private/internal address access
4. **Domain Allowlist**: Trusted domains are already handled via `_trusted_domains()` function

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `skill_service.py` | Reuse | The existing `_is_safe_url()` function should be moved to a shared location |
| `agent_routes.py` | Extends | Add URL validation to agent-card fetch endpoints |
| `server_routes.py` | Extends | Add URL validation to server health-check endpoints |
| `health/service.py` | Depends | May need URL validation for external health check endpoints |

### Constraints and Limitations Discovered
- The existing `_is_safe_url()` function already implements comprehensive SSRF protection including IP validation and domain allowlists
- The codebase already has a pattern for handling HTTP requests to external URLs with proper error handling
- Need to ensure backwards compatibility with existing valid agent/server URLs

## Architecture

### System Context Diagram
```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Client Apps   │    │  MCP Gateway     │    │   External      │
│   (Agents)      │    │  & Registry      │    │   Services      │
│                 │    │                  │    │                 │
│  ┌───────────┐  │    │  ┌─────────────┐ │    │  ┌─────────────┐ │
│  │  Agent    │  │    │  │  Agent      │ │    │  │  Server     │ │
│  │  Card     │  │    │  │  Routes     │ │    │  │  Endpoint   │ │
│  └───────────┘  │    │  └─────────────┘ │    │  └─────────────┘ │
│                 │    │        ▲       │    │                 │
│  ┌───────────┐  │    │        │       │    │  ┌─────────────┐ │
│  │  Agent    │  │    │  ┌─────┴─────┐ │    │  │  Server     │ │
│  │  Discovery│  │    │  │  Health   │ │    │  │  Health     │ │
│  └───────────┘  │    │  │  Check    │ │    │  │  Check      │ │
│                 │    │  └───────────┘ │    │  └─────────────┘ │
│  ┌───────────┐  │    │        │       │    │                 │
│  │  Agent    │  │    │  ┌─────┴─────┐ │    │  ┌─────────────┐ │
│  │  Card     │  │    │  │  Server   │ │    │  │  Server     │ │
│  │  Fetch    │  │    │  │  Routes   │ │    │  │  Endpoint   │ │
│  └───────────┘  │    │  └───────────┘ │    │  └─────────────┘ │
└─────────────────┘    └────────────────┘    └─────────────────┘
```

### Sequence Diagram
```
Client → MCP Gateway: Request agent-card fetch
MCP Gateway → External Server: Fetch agent-card from user-provided URL
MCP Gateway → Client: Return agent-card or error

Client → MCP Gateway: Request server health-check
MCP Gateway → External Server: Health-check user-provided URL
MCP Gateway → Client: Return health status or error
```

### Component Diagram
```
┌─────────────────────────────────────────────────────────────┐
│                    MCP Gateway & Registry                     │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐      ┌──────────────────────────────┐   │
│  │  Agent       │      │  Server                      │   │
│  │  Routes      │      │  Routes                      │   │
│  │              │      │                              │   │
│  │  ┌─────────┐ │      │  ┌─────────────────┐         │   │
│  │  │Agent    │ │      │  │Server           │         │   │
│  │  │Card     │ │      │  │Health Check     │         │   │
│  │  │Fetch    │ │      │  │Endpoints        │         │   │
│  │  └─────────┘ │      │  └─────────────────┘         │   │
│  └──────────────┘      └──────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Shared SSRF Utilities                                  │ │
│  │  ┌─────────────────┐                                   │ │
│  │  │_is_safe_url()   │                                   │ │
│  │  │(moved from     │                                   │ │
│  │  │skill_service.py)│                                   │ │
│  │  └─────────────────┘                                   │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Data Models

### New Models
None required for this change.

### Model Changes
None required for this change.

## API / CLI Design

### New Endpoints / Commands
None - this is a security enhancement to existing functionality.

### Request / Invocation:
This change enhances existing endpoints without changing their interfaces:
- `GET /api/agents/{path}` - Agent card fetch endpoint
- `POST /api/agents/{path}/health` - Agent health check endpoint  
- `POST /api/servers/{path}/health` - Server health check endpoint

### Expected Response / Output:
Existing responses unchanged, but with additional security validation:
```json
{
  "agent_path": "/example-agent",
  "health_check_url": "https://example.com/.well-known/agent-card.json",
  "status": "healthy",
  "status_code": 200,
  "detail": null,
  "response_time_ms": 125,
  "last_checked_iso": "2026-07-22T10:30:00.000Z"
}
```

### Error Cases:
- 400 Bad Request: URL failed SSRF validation - private/internal addresses are not allowed
- 403 Forbidden: User lacks permission to access the resource

## Configuration Parameters

### New Environment Variables
None required for this change.

### Settings / Config Class Updates
None required for this change.

### Deployment Surface Checklist
None required for this change.

## New Dependencies

### New Packages
None required for this change.

If this change were to introduce new dependencies, they would be:
| Package | Version | Purpose |
|---------|---------|---------|
| `requests` | `latest` | For HTTP requests (if not already present) |
| `urllib3` | `latest` | For URL parsing and validation |

This change uses only existing dependencies.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Move `_is_safe_url()` to a shared utility location
**File:** `registry/utils/url_validation.py`  
**Lines:** New file

```python
"""
Shared URL validation utilities for SSRF protection.
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ..core.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

# Constants
# Built-in trusted domains that skip IP validation (SSRF protection allowlist).
# Enterprise GitHub hosts are merged in at runtime from settings.github_extra_hosts
# via _trusted_domains() so GHES instances on private IPs are reachable for
# SKILL.md fetches (matches the host allowlist used by the GitHub auth provider).
_DEFAULT_TRUSTED_DOMAINS: frozenset = frozenset(
    {
        "github.com",
        "gitlab.com",
        "raw.githubusercontent.com",
        "bitbucket.org",
    }
)


@lru_cache(maxsize=1)
def _trusted_domains() -> frozenset[str]:
    """Return SSRF allowlist: built-in defaults plus configured GHES hosts.

    Reads settings.github_extra_hosts (the same setting that authorises auth
    header injection) so a single config knob covers both trust decisions.
    Cached because settings are immutable per-process.
    """
    extra_raw = settings.github_extra_hosts or ""
    extra = frozenset(h.strip().lower() for h in extra_raw.split(",") if h.strip())
    return _DEFAULT_TRUSTED_DOMAINS | extra


def _is_private_ip(
    ip_str: str,
) -> bool:
    """Check if an IP address is private, loopback, or link-local.

    Args:
        ip_str: IP address string to check

    Returns:
        True if the IP is private/loopback/link-local, False otherwise
    """
    try:
        ip = ipaddress.ip_address(ip_str)

        # Check for private, loopback, link-local, or reserved addresses
        if ip.is_private:
            return True
        if ip.is_loopback:
            return True
        if ip.is_link_local:
            return True
        if ip.is_reserved:
            return True

        # Check for cloud metadata endpoint (169.254.169.254)
        if ip_str == "169.254.169.254":
            return True

        return False
    except ValueError:
        # Invalid IP address format
        return True


def _is_safe_url(
    url: str,
) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).

    This function validates that a URL:
    1. Uses http or https scheme
    2. Does not resolve to a private/loopback/link-local IP address
    3. Does not target cloud metadata endpoints

    Trusted domains (github.com, gitlab.com, etc., plus any host configured
    via settings.github_extra_hosts) skip the IP check so GHES instances on
    private networks remain reachable.

    Args:
        url: URL to validate

    Returns:
        True if the URL is safe to fetch, False otherwise
    """
    try:
        parsed = urlparse(url)

        # Check scheme - only allow http and https
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"SSRF protection: Blocked URL with scheme '{parsed.scheme}'")
            return False

        hostname = parsed.hostname
        if not hostname:
            logger.warning("SSRF protection: URL has no hostname")
            return False

        # Check if hostname is in trusted domains allowlist
        hostname_lower = hostname.lower()
        if hostname_lower in _trusted_domains():
            logger.debug(f"SSRF protection: Trusted domain '{hostname_lower}'")
            return True

        # Resolve hostname to IP addresses
        try:
            addr_info = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as e:
            logger.warning(f"SSRF protection: Failed to resolve hostname '{hostname}': {e}")
            return False

        # Check all resolved IP addresses
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_address = sockaddr[0]
            if _is_private_ip(ip_address):
                logger.warning(
                    f"SSRF protection: Blocked URL resolving to private IP "
                    f"'{ip_address}' for hostname '{hostname}'"
                )
                return False

        return True

    except Exception as e:
        logger.warning(f"SSRF protection: Error validating URL: {e}")
        return False
```

#### Step 2: Update `agent_routes.py` to use URL validation for agent-card fetch
**File:** `registry/api/agent_routes.py`  
**Lines:** ~930-950, ~960-980

Add import and modify the agent health check endpoint to validate URLs:

```python
# Add to imports at top of file:
from ..utils.url_validation import _is_safe_url

# In check_agent_health function, add URL validation before HTTP requests:
# ... existing code ...
base_url = str(agent_card.url).rstrip("/")
health_urls = _build_agent_health_urls(base_url)
timeout_seconds = max(1, settings.health_check_timeout_seconds)

# Validate the base URL before making any requests
if not _is_safe_url(base_url):
    logger.warning(f"SSRF protection: Blocked agent URL {base_url}")
    return {
        "agent_path": path,
        "health_check_url": base_url,
        "status": "unhealthy",
        "status_code": None,
        "detail": "URL failed SSRF validation - private/internal addresses are not allowed",
        "response_time_ms": 0,
        "last_checked_iso": datetime.now(UTC).isoformat(),
    }
# ... existing code continues ...
```

#### Step 3: Update `server_routes.py` to use URL validation for server health checks
**File:** `registry/api/server_routes.py`  
**Lines:** ~2270-2290

Modify the health check logic to validate URLs:

```python
# Add to imports at top of file:
from ..utils.url_validation import _is_safe_url

# In the health check endpoint, validate proxy_pass_url before making requests:
# ... existing code ...
proxy_pass_url = server_info.get("proxy_pass_url")
if not proxy_pass_url:
    # ... existing error handling ...

# Add URL validation
if not _is_safe_url(proxy_pass_url):
    logger.warning(f"SSRF protection: Blocked server URL {proxy_pass_url}")
    return {
        "status": "unhealthy",
        "status_code": None,
        "detail": "URL failed SSRF validation - private/internal addresses are not allowed",
        "response_time_ms": 0,
        "last_checked_iso": datetime.now(UTC).isoformat(),
    }

# ... existing code continues ...
```

### Error Handling
- When URL validation fails, return a 400 Bad Request error with descriptive message
- Log the failed validation attempt for security monitoring
- Continue with existing error handling flow for HTTP request failures

### Logging
- Log all URL validation attempts (success and failure)
- Log when SSRF protection blocks a URL
- Include the blocked URL in logs for forensic analysis

## Observability
### Tracing / Metrics / Logging Points
1. **URL validation attempts** - Log all URL validations for security monitoring
2. **SSRF protection blocks** - Log when URLs are blocked by SSRF protection
3. **Health check failures** - Log when health checks fail due to URL validation
4. **Successful URL validations** - Log successful validations for audit trail

## Scaling Considerations
- The `_is_safe_url()` function uses `@lru_cache` for DNS resolution, so it's already optimized for repeated calls
- URL validation adds minimal overhead to HTTP requests
- The validation is performed once per request, not per individual HTTP call
- No additional database or network calls are required

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/url_validation.py` | Shared utility for URL validation with SSRF protection |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/api/agent_routes.py` | ~930-940 | Add URL validation to agent health check |
| `registry/api/server_routes.py` | ~2270-2280 | Add URL validation to server health check |
| `registry/services/skill_service.py` | ~128-130 | Move `_is_safe_url` to shared location |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~100 |
| New tests | ~0 |
| Modified code | ~20 |
| **Total** | **~120** |

## Testing Strategy
This change will be tested through the existing testing framework in `testing.md`.

## Alternatives Considered

### Alternative 1: Implement custom URL validation logic
**Description:** Create new URL validation functions instead of reusing existing code
**Pros / Cons:** 
- Pros: More control over validation logic, can tailor specifically to this use case
- Cons: Duplicate code, harder to maintain, potential for inconsistencies with existing protections

### Alternative 2: Add SSRF validation only to the most critical endpoints
**Description:** Only validate URLs in the most security-sensitive paths
**Pros / Cons:** 
- Pros: Less code change, focused approach
- Cons: Inconsistent security posture, leaves other endpoints vulnerable

### Comparison Matrix

| Criteria | Chosen | Alt 1 | Alt 2 |
|----------|--------|-------|-------|
| Security Coverage | High | Medium | Medium |
| Code Reuse | High | Low | Low |
| Maintenance Burden | Low | High | High |
| Risk of Inconsistency | Low | High | High |

## Rollout Plan
- Phase 1: Implement shared utility and apply to agent-card fetch (out of scope for this skill)
- Phase 2: Apply to server health-checks (out of scope for this skill)  
- Phase 3: Testing and deployment (out of scope for this skill)

## Open Questions
- Should we also validate URLs in the agent-card fetch endpoint when retrieving agent cards?
- Should we add a configuration parameter to allow bypassing URL validation for testing?

## References
- Issue #1282 - Security audit finding for SSRF vulnerability
- Existing `_is_safe_url()` implementation in `skill_service.py`
- Existing health check patterns in `health/service.py`