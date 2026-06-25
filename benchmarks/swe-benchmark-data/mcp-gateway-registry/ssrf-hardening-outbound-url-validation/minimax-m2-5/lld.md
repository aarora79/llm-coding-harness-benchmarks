# Low-Level Design: SSRF Protection for Outbound URLs

*Created: 2025-06-25*
*Author: Claude (minimax-m2.5)*
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
The MCP Gateway Registry makes outbound HTTP requests to URLs supplied by users (agent URLs stored in the database) without proper validation. These requests fetch agent cards and perform health checks, but could be exploited by attackers to:
- Access internal services (localhost, RFC 1918 addresses)
- Retrieve cloud metadata (AWS 169.254.169.254, GCP metadata.google.internal, etc.)
- Probe internal network infrastructure
- Bypass firewall restrictions

The vulnerability exists in the health check endpoint and health service that fetch from user-provided URLs.

### Goals
- Prevent SSRF attacks by validating all outbound URLs before HTTP requests
- Block access to private IP ranges, localhost, and cloud metadata endpoints
- Provide configurable allowlist for trusted domains
- Maintain backward compatibility with existing functionality
- Log blocked requests for security monitoring

### Non-Goals
- Not changing how agent URLs are stored or registered
- Not implementing IP reputation filtering or rate limiting
- Not adding WAF rules (infrastructure concern)
- Not modifying the nginx/reverse proxy configuration

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/api/agent_routes.py` | Agent API routes including health check | Contains `POST /agents/{path}/health` endpoint making outbound HTTP requests |
| `registry/health/service.py` | Health check service | Performs batch health checks using `httpx.AsyncClient` |
| `registry/core/config.py` | Configuration settings | Will add new SSRF protection settings |
| `registry/constants.py` | Constants including deployment types | May need SSRF-related constants |

### Existing Patterns Identified

1. **HTTP Client Pattern**: The codebase uses `httpx.AsyncClient` consistently for outbound HTTP requests.
   - Files: `registry/api/agent_routes.py` (line 935), `registry/health/service.py` (line 363)
   - How to follow: Use the same client pattern but wrap with URL validation

2. **Configuration Pattern**: Settings are defined in `registry/core/config.py` using Pydantic and environment variables.
   - Files: `registry/core/config.py`
   - How to follow: Add new settings with appropriate env var mappings

3. **Logging Pattern**: Security-related events are logged at appropriate levels.
   - How to follow: Log blocked URLs at WARNING level without sensitive data

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `/agents/{path}/health` endpoint | Uses | Validates URL from agent card before HTTP GET |
| `health_service._check_single_service` | Uses | Validates `proxy_pass_url` before HTTP request |
| `health_service.check_all_services` | Uses | Validates each server URL during batch checks |
| Settings | Configures | New config options for allowlist/blocklist |

### Constraints and Limitations Discovered
- **Backward Compatibility**: Must not break existing health checks for agents with valid public URLs
- **Timeout Handling**: Must integrate with existing httpx timeout configuration
- **Performance**: URL validation should add minimal latency (< 1ms per request)

## Architecture

### System Context Diagram

```
[External User]
       |
       v
[Agent Health Check API]
       |
       v
[URL Validation Layer] --> [SSRF Protection Module]
       |
       v (if valid)
[httpx.AsyncClient] --> [External Agent URL]
       |
       v
[Response Processing]
```

### Sequence Diagram

```
Client
  |
  | POST /agents/{path}/health
  v
Agent Routes (agent_routes.py:883)
  |
  | Get agent from DB (includes URL)
  v
Build Health Check URLs (_build_agent_health_urls)
  |
  | For each URL:
  v
SSRF Protection.validate_url(url)
  |
  |--- URL is blocked --> Return 400, log warning
  |
  |--- URL is allowed -->
  v
HTTP GET via httpx.AsyncClient
  |
  | Response handling...
  v
Return health status
```

## Data Models

### New Validation Result Model

```python
from dataclasses import dataclass
from enum import Enum

class BlockReason(Enum):
    """Reason why URL was blocked."""
    PRIVATE_IP = "private_ip"
    LOCALHOST = "localhost"
    CLOUD_METADATA = "cloud_metadata"
    LINK_LOCAL = "link_local"
    NOT_IN_ALLOWLIST = "not_in_allowlist"

@dataclass
class URLValidationResult:
    """Result of URL validation for SSRF protection."""
    is_valid: bool
    url: str
    block_reason: BlockReason | None = None
    blocked_ip: str | None = None

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "url": self.url,
            "block_reason": self.block_reason.value if self.block_reason else None,
            "blocked_ip": self.blocked_ip,
        }
```

### Configuration Model Updates

```python
from pydantic import Field
from pydantic_settings import BaseSettings

class SSRFProtectionSettings(BaseSettings):
    """Settings for SSRF protection."""

    ssrf_protection_enabled: bool = Field(
        default=True,
        description="Enable/disable SSRF protection for outbound URLs"
    )

    ssrf_block_private_ips: bool = Field(
        default=True,
        description="Block requests to private IP ranges (RFC 1918)"
    )

    ssrf_block_localhost: bool = Field(
        default=True,
        description="Block requests to localhost and 127.0.0.0/8"
    )

    ssrf_block_cloud_metadata: bool = Field(
        default=True,
        description="Block requests to cloud metadata endpoints"
    )

    ssrf_block_link_local: bool = Field(
        default=True,
        description="Block requests to link-local addresses (169.254.0.0/16)"
    )

    ssrf_allowed_domains: list[str] = Field(
        default_factory=list,
        description="Comma-separated list of allowed domains (no scheme)"
    )

    ssrf_log_blocked_requests: bool = Field(
        default=True,
        description="Log blocked URL validation attempts"
    )

    class Config:
        env_prefix = "MCP_GATEWAY_"
```

## API / CLI Design

### No New Public API Endpoints

The SSRF protection is implemented as middleware/wrapper functions, not new API endpoints. Validation happens internally before HTTP requests are made.

### Internal Validation API

**Function:** `validate_url(url: str) -> URLValidationResult`

**Description:** Validate a URL against SSRF protection rules.

**Parameters:**
- `url` (str): Full URL to validate (e.g., "https://agent.example.com/a2a")

**Returns:**
- `URLValidationResult` with validation status and block reason if invalid

**Usage in Health Check:**

```python
from registry.utils.ssrf_protection import validate_url

async def check_agent_health(request: Request, path: str):
    # ... get agent from DB ...
    base_url = str(agent_card.url).rstrip("/")
    health_urls = _build_agent_health_urls(base_url)

    for url in health_urls:
        # Validate URL before making HTTP request
        validation_result = validate_url(url)
        if not validation_result.is_valid:
            logger.warning(
                f"SSRF protection blocked URL: {url} - {validation_result.block_reason}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"URL validation failed: {validation_result.block_reason.value}"
            )

        # Proceed with HTTP request...
```

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `MCP_GATEWAY_SSRF_PROTECTION_ENABLED` | bool | `true` | No | Enable/disable SSRF protection |
| `MCP_GATEWAY_SSRF_BLOCK_PRIVATE_IPS` | bool | `true` | No | Block RFC 1918 addresses |
| `MCP_GATEWAY_SSRF_BLOCK_LOCALHOST` | bool | `true` | No | Block localhost |
| `MCP_GATEWAY_SSRF_BLOCK_CLOUD_METADATA` | bool | `true` | No | Block cloud metadata endpoints |
| `MCP_GATEWAY_SSRF_BLOCK_LINK_LOCAL` | bool | `true` | No | Block link-local addresses |
| `MCP_GATEWAY_SSRF_ALLOWED_DOMAINS` | string | "" | No | Comma-separated allowed domains |
| `MCP_GATEWAY_SSRF_LOG_BLOCKED_REQUESTS` | bool | `true` | No | Log blocked requests |

### Deployment Surface Checklist

- [ ] Update `.env.example` with new variables
- [ ] Update `docker-compose.yml` if needed
- [ ] Update Terraform variables if SSRF settings are managed via Terraform
- [ ] Update Helm values if deployed on Kubernetes
- [ ] Document in `CLAUDE.md` or `DEV_INSTRUCTIONS.md`

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `ipaddress` | Python stdlib | IP address validation (no new package needed) |

This change uses only existing dependencies. The Python standard library `ipaddress` module provides all necessary IP validation capabilities.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Create SSRF Protection Module
**File:** `registry/utils/ssrf_protection.py` (new file)

```python
"""SSRF protection utilities for validating outbound URLs."""

import ipaddress
import logging
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class BlockReason(Enum):
    """Reason why URL was blocked."""
    PRIVATE_IP = "private_ip"
    LOCALHOST = "localhost"
    CLOUD_METADATA = "cloud_metadata"
    LINK_LOCAL = "link_local"
    NOT_IN_ALLOWLIST = "not_in_allowlist"


# Cloud metadata IP addresses
CLOUD_METADATA_IPS = {
    "169.254.169.254",  # AWS, GCP, Azure, etc.
    "metadata.google.internal",
    "metadata.google",
}

# Private IP ranges (RFC 1918)
PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# Link-local range
LINK_LOCAL_RANGE = ipaddress.ip_network("169.254.0.0/16")

# Localhost range
LOCALHOST_RANGE = ipaddress.ip_network("127.0.0.0/8")


@dataclass
class URLValidationResult:
    """Result of URL validation for SSRF protection."""
    is_valid: bool
    url: str
    block_reason: BlockReason | None = None
    blocked_ip: str | None = None


def validate_url(
    url: str,
    allowed_domains: list[str] | None = None,
    block_private_ips: bool = True,
    block_localhost: bool = True,
    block_cloud_metadata: bool = True,
    block_link_local: bool = True,
    log_blocked: bool = True,
) -> URLValidationResult:
    """Validate a URL against SSRF protection rules.

    Args:
        url: Full URL to validate
        allowed_domains: Optional list of allowed domains
        block_private_ips: Whether to block private IP ranges
        block_localhost: Whether to block localhost
        block_cloud_metadata: Whether to block cloud metadata endpoints
        block_link_local: Whether to block link-local addresses
        log_blocked: Whether to log blocked requests

    Returns:
        URLValidationResult with validation status
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return URLValidationResult(
            is_valid=False,
            url=url,
            block_reason=BlockReason.PRIVATE_IP,
        )

    hostname = parsed.hostname
    if not hostname:
        return URLValidationResult(
            is_valid=False,
            url=url,
            block_reason=BlockReason.PRIVATE_IP,
        )

    # Check if hostname is an IP address
    try:
        ipaddress_obj = ipaddress.ip_address(hostname)
        ip_str = str(ipaddress_obj)

        # Check localhost
        if block_localhost and ipaddress_obj in LOCALHOST_RANGE:
            if log_blocked:
                logger.warning(f"SSRF: Blocked localhost URL: {url}")
            return URLValidationResult(
                is_valid=False,
                url=url,
                block_reason=BlockReason.LOCALHOST,
                blocked_ip=ip_str,
            )

        # Check private IP ranges
        if block_private_ips:
            for private_range in PRIVATE_RANGES:
                if ipaddress_obj in private_range:
                    if log_blocked:
                        logger.warning(f"SSRF: Blocked private IP URL: {url} ({ip_str})")
                    return URLValidationResult(
                        is_valid=False,
                        url=url,
                        block_reason=BlockReason.PRIVATE_IP,
                        blocked_ip=ip_str,
                    )

        # Check link-local
        if block_link_local and ipaddress_obj in LINK_LOCAL_RANGE:
            if log_blocked:
                logger.warning(f"SSRF: Blocked link-local URL: {url}")
            return URLValidationResult(
                is_valid=False,
                url=url,
                block_reason=BlockReason.LINK_LOCAL,
                blocked_ip=ip_str,
            )

        # Check cloud metadata
        if block_cloud_metadata and ip_str in CLOUD_METADATA_IPS:
            if log_blocked:
                logger.warning(f"SSRF: Blocked cloud metadata URL: {url}")
            return URLValidationResult(
                is_valid=False,
                url=url,
                block_reason=BlockReason.CLOUD_METADATA,
                blocked_ip=ip_str,
            )

    except ValueError:
        # Not an IP address, continue with domain checks

    # Check allowlist
    if allowed_domains:
        # Extract domain without TLD complexity - simple suffix match
        domain_lower = hostname.lower()
        allowed = False
        for allowed_domain in allowed_domains:
            allowed_domain = allowed_domain.lower().strip()
            if domain_lower == allowed_domain or domain_lower.endswith(f".{allowed_domain}"):
                allowed = True
                break

        if not allowed:
            if log_blocked:
                logger.warning(f"SSRF: Blocked non-allowed domain: {url}")
            return URLValidationResult(
                is_valid=False,
                url=url,
                block_reason=BlockReason.NOT_IN_ALLOWLIST,
            )

    return URLValidationResult(is_valid=True, url=url)
```

#### Step 2: Configure Settings
**File:** `registry/core/config.py`

Add SSRFProtectionSettings to the settings class. Reference existing patterns for how other settings groups are organized.

#### Step 3: Modify Health Check Endpoint
**File:** `registry/api/agent_routes.py` (around line 930)

Add URL validation before the HTTP GET loop:

```python
# At the top of the file, import:
from registry.utils.ssrf_protection import validate_url

# In check_agent_health function, before the URL loop:
for url in health_urls:
    validation_result = validate_url(
        url,
        allowed_domains=settings.ssrf_allowed_domains,
        block_private_ips=settings.ssrf_block_private_ips,
        block_localhost=settings.ssrf_block_localhost,
        block_cloud_metadata=settings.ssrf_block_cloud_metadata,
        block_link_local=settings.ssrf_block_link_local,
        log_blocked=settings.ssrf_log_blocked_requests,
    )
    if not validation_result.is_valid:
        status_label = "unhealthy"
        detail = f"URL blocked by SSRF protection: {validation_result.block_reason.value}"
        logger.warning(f"SSRF protection blocked health check for {path}: {url} - {validation_result.block_reason}")
        continue  # Try next URL in the list

    # ... existing HTTP request code ...
```

#### Step 4: Modify Health Service
**File:** `registry/health/service.py` (around line 360)

Add URL validation in the batch health check loop:

```python
# At the top of the file, import:
from registry.utils.ssrf_protection import validate_url

# In check_all_services function, after getting proxy_pass_url:
proxy_pass_url = server_info.get("proxy_pass_url")
if not proxy_pass_url:
    continue

# Validate URL before health check
validation_result = validate_url(
    proxy_pass_url,
    allowed_domains=settings.ssrf_allowed_domains,
    block_private_ips=settings.ssrf_block_private_ips,
    block_localhost=settings.ssrf_block_localhost,
    block_cloud_metadata=settings.ssrf_block_cloud_metadata,
    block_link_local=settings.ssrf_block_link_local,
    log_blocked=settings.ssrf_log_blocked_requests,
)
if not validation_result.is_valid:
    logger.warning(
        f"SSRF protection skipped health check for {service_path}: "
        f"{proxy_pass_url} - {validation_result.block_reason.value}"
    )
    # Mark as unhealthy or keep previous status
    continue
```

#### Step 5: Update .env.example
**File:** `.env.example`

Add the new environment variables to the example file.

### Error Handling

- **Invalid URL**: Return 400 Bad Request with clear message
- **Blocked URL**: Log with warning level, mark health check as failed
- **Allowlist mismatch**: Treat as blocked, don't expose allowlist in error messages
- **Exception in validation**: Fail closed (block the request) rather than open

### Logging

| Event | Level | Content |
|-------|-------|---------|
| URL blocked | WARNING | "SSRF: Blocked {reason} URL: {url}" (no full URL in logs for security) |
| URL allowed | DEBUG | Basic confirmation for debugging |

Log sanitization: Do not log full URLs in production to avoid accidentally logging credentials in URLs.

## Observability

### Metrics
- **Counter**: `ssrf_validation_total` with labels `result` (allowed/blocked), `reason`
- **Counter**: `ssrf_health_check_blocked_total`

### Key Log Events
- Blocked private IP: WARNING level
- Blocked localhost: WARNING level
- Blocked cloud metadata: WARNING level
- Blocked non-allowed domain: WARNING level

## Scaling Considerations

- URL validation is CPU-bound and fast (<1ms per URL)
- No significant impact on health check performance
- Can be parallelized if needed (validation is stateless)

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/ssrf_protection.py` | SSRF URL validation module with reusable functions |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/core/config.py` | ~20 | Add SSRF protection settings |
| `registry/api/agent_routes.py` | ~15 | Add URL validation in health check |
| `registry/health/service.py` | ~15 | Add URL validation in batch checks |
| `.env.example` | ~10 | Add new environment variables |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~200 |
| New tests | ~100 |
| Modified code | ~50 |
| **Total** | **~350** |

## Testing Strategy

See `testing.md` for the comprehensive test plan.

## Alternatives Considered

### Alternative 1: Use `ssrf-filter` Library
**Description:** Use the existing `ssrf-filter` Python library which provides comprehensive SSRF protection.

**Pros:**
- Battle-tested library
- More comprehensive protection
- Less maintenance burden

**Cons:**
- Additional dependency
- May have different defaults than desired

**Why Rejected:** Python stdlib `ipaddress` module provides sufficient functionality for this use case without adding external dependencies.

### Alternative 2: DNS Resolution Blocking
**Description:** Block URLs that resolve to private IPs.

**Pros:**
- Catches more attack vectors

**Cons:**
- Adds latency (DNS resolution)
- DNS cache poisoning concerns
- Complexity

**Why Rejected:** Primary attack vector is direct IP access, which is blocked. DNS-based blocking can be added as a future enhancement.

### Alternative 3: Application Firewall
**Description:** Implement at nginx/reverse proxy layer only.

**Pros:**
- Centralized protection

**Cons:**
- Doesn't protect against direct service access
- Not visible in application logs

**Why Rejected:** Defense in depth - need protection at both layers.

## Rollout Plan

- Phase 1: Implementation (out of scope for this design)
- Phase 2: Unit tests and integration tests
- Phase 3: Deploy to staging, verify health checks still work
- Phase 4: Monitor logs, adjust allowlist as needed
- Phase 5: Deploy to production

## Open Questions

- Should blocked URL health checks mark the agent as "unhealthy" or skip the check entirely?
- Should we provide an admin override for trusted internal endpoints?

## References

- OWASP SSRF Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- AWS SSRF Best Practices: https://docs.aws.amazon.com/waf/latest/developerguide/web-acl.html