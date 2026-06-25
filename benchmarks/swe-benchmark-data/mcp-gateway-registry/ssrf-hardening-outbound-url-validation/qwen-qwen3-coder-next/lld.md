# Low-Level Design: SSRF Hardening for Agent Card Fetch Endpoints

*Created: 2026-06-24*
*Author: Claude (Qwen Qwen3 Coder Next)*
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

The MCP Gateway Registry has multiple endpoints that make outbound HTTP requests to user-supplied URLs without adequate SSRF (Server-Side Request Forgery) protection. Attackers could register malicious agents with URLs pointing to internal services, cloud metadata endpoints, or other sensitive targets. When the registry performs health checks or fetches agent cards, it would inadvertently make requests to these dangerous endpoints, potentially exfiltrating sensitive data or scanning internal networks.

### Goals
- Prevent SSRF attacks through all outbound HTTP requests made by the registry
- Block access to private/internal IP address ranges
- Block access to cloud metadata endpoints
- Block access to dangerous URL schemes (file://, ftp://, etc.)
- Validate redirected URLs at each hop
- Maintain backward compatibility with existing agents
- Follow the existing SSRF protection pattern in `skill_service.py`

### Non-Goals
- Rate limiting for outbound requests
- Authentication/authorization changes
- Full network-level DDoS protection
- DNS rebinding attack mitigation (requires infrastructure changes)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/api/agent_routes.py:883-1013` | Agent health check endpoint | **VULNERABLE** - Makes httpx.get() requests to user-provided URLs without validation |
| `registry/utils/agent_validator.py:196-231` | Agent endpoint reachability check | **VULNERABLE** - Uses httpx.get() on user-provided URLs without validation |
| `cli/agent_mgmt.py:419-463` | CLI agent health check utility | **VULNERABLE** - Uses requests.get() on user-provided URLs without validation |
| `registry/services/skill_service.py:128-192` | SKILL.md URL validation (SSRF protection) | **REFERENCE** - Already implements SSRF protection |
| `registry/core/config.py` | Application configuration | May need to add `github_extra_hosts` equivalent for agents |
| `registry/exceptions.py:194-203` | SkillContentSSRFError definition | May need agent-specific SSRF exception |
| `agents/registry_client.py:144-168` | Registry client for agent discovery | Uses aiohttp for outbound requests |
| `agents/agent.py:722-730` | Tool invocation via httpx | Uses httpx.AsyncClient for outbound requests |

### Existing Patterns Identified

1. **SKILL.md SSRF Protection (skill_service.py)**
   - **Files:** `registry/services/skill_service.py:128-192`
   - How to follow: Create a similar `_is_safe_url()` function for agent URLs
   - Implementation:
     - Validate URL scheme (http/https only)
     - Resolve hostname to IP
     - Check if IP is in private/internal ranges
     - Allow trusted domains to skip IP check
     - Block cloud metadata endpoints

2. **URL Parsing with urllib.parse**
   - **Files:** throughout codebase
   - How to follow: Use `urlparse()` for safe URL parsing
   - Example:
     ```python
     from urllib.parse import urlparse
     parsed = urlparse(url)
     hostname = parsed.hostname
     scheme = parsed.scheme
     ```

3. **IP Address Validation with ipaddress module**
   - **Files:** `registry/services/skill_service.py:89-127`
   - How to follow: Use `ipaddress.ip_address()` and `ipaddress.ip_network()`
   - Example:
     ```python
     import ipaddress
     ip = ipaddress.ip_address("192.168.1.1")
     network = ipaddress.ip_network("10.0.0.0/8")
     ip in network  # True
     ```

4. **DNS Resolution with socket**
   - **Files:** `registry/services/skill_service.py:169-176`
   - How to follow: Use `socket.getaddrinfo()` to resolve hostnames
   - Example:
     ```python
     import socket
     addr_info = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
     ```

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `agent_routes.py` - Health check endpoint | **Modify** | Need to add SSRF validation before `httpx.AsyncClient().get()` calls |
| `agent_validator.py` - Reachability check | **Modify** | Need to add SSRF validation before `httpx.get()` call |
| `agent_mgmt.py` - CLI health check | **Modify** | Need to add SSRF validation before `requests.get()` call |
| `skill_service.py` - Existing SSRF function | **Reference** | Use same logic pattern for agent URLs |
| `agent_scanner.py` - Security scanning | **Consider** | Should also validate URLs before scanning |

### Constraints and Limitations Discovered
- **Constraint:** Agent health check must still work for legitimate public agents
- **Constraint:** Some users may run agents on private networks (GHES-style); need allowlist mechanism
- **Constraint:** Redirect validation is important - an initial valid URL may redirect to a blocked IP
- **Limitation:** DNS rebinding attacks require additional infrastructure (split-horizon DNS)

## Architecture

### System Context Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         MCP Gateway Registry                    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Agent Endpoint (POST /api/agents/{path}/health)        │   │
│  │  - Validates agent URL before making outbound request   │   │
│  │  - Blocks SSRF attempts (private IPs, metadata APIs)    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  SSRF Validation Function (_is_safe_url)                │   │
│  │  - Scheme check (http/https only)                       │   │
│  │  - DNS resolution and IP validation                     │   │
│  │  - Trusted domain allowlist                             │   │
│  │  - Cloud metadata endpoint blocklist                    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Outbound HTTP Client                                    │   │
│  │  - httpx.AsyncClient for agents                          │   │
│  │  - requests for CLI                                      │   │
│  │  - Only executes if SSRF check passes                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Agent Service / Metadata Endpoint                       │   │
│  │  - May be public or internal (if allowlisted)           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                   ┌──────────────────────┐
                   │    User Request      │
                   │  (Malicious or Legit)│
                   └──────────────────────┘
```

### Sequence Diagram

```
┌──────────────┐    1. Register Agent       ┌──────────────────┐
│    Agent     │◄──────────────────────────┤   Registry API   │
└──────────────┘                           └──────────────────┘
                                                   │
                                                   │
                                                   │ 2. Health Check Request
                                                   │  POST /api/agents/{path}/health
                                                   ▼
                                            ┌──────────────────┐
                                            │  Agent Routes    │
                                            └──────────────────┘
                                                   │
                                                   │ 3. Extract URL from Agent Card
                                                   │
                                                   ▼
                                            ┌──────────────────┐
                                            │   SSRF Validator │
                                            │   _is_safe_url() │
                                            └──────────────────┘
                                                   │
                                                   │ 4. Check: http/https only?
                                                   │    Check: DNS resolution?
                                                   │    Check: Private IP?
                                                   │    Check: Cloud metadata?
                                                   │    Check: Trusted domain?
                                                   │
                                              ┌────┴────┐
                                              │         │
                        Valid URL ────────────►│       │◄────────── Invalid URL
                        (passes all checks)    │  OK   │    (SSRF blocked)
                                               │       │
                                              └────┬────┘
                                                   │
                                                   │ 5. Make Outbound Request
                                                   │  httpx.AsyncClient().get(url)
                                                   ▼
                                            ┌──────────────────┐
                                            │   Agent Service  │
                                            └──────────────────┘
```

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SSRF Hardening Components                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  utils/agent_ssrftools.py (NEW)                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ _is_safe_url(url: str) -> bool                                   │  │  │
│  │  │ - Validates URL before outbound requests                         │  │  │
│  │  │ - Blocks private IPs, metadata endpoints, dangerous schemes      │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ _is_private_ip(ip: str) -> bool                                  │  │  │
│  │  │ - Checks if IP is in private ranges (RFC1918)                    │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ _is_cloud_metadata_ip(ip: str) -> bool                           │  │  │
│  │  │ - Checks if IP is a cloud metadata endpoint                      │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ _trusted_domains() -> set[str]                                   │  │  │
│  │  │ - Returns allowlist of trusted domains (github.com, etc.)        │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ validate_url(url: str) -> None                                   │  │  │
│  │  │ - Raises SSRFValidationError if URL is unsafe                    │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Modified Files                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ agent_routes.py (health endpoint)                               │  │  │
│  │  │ - Import _is_safe_url                                           │  │  │
│  │  │ - Call _is_safe_url(url) before httpx.get()                    │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ agent_validator.py (reachability check)                         │  │  │
│  │  │ - Call _is_safe_url(url) before httpx.get()                    │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │ agent_mgmt.py (CLI health check)                                │  │  │
│  │  │ - Call _is_safe_url(url) before requests.get()                 │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Models

### New Data Models

No new data models are required. The existing `AgentCard` model will continue to store Agent URLs:

```python
# Existing - registry/schemas/agent_models.py:350
class AgentCard(BaseModel):
    """A fully-specified A2A Agent Card."""
    name: str
    description: str
    url: AnyUrl  # Already validated for format, but not for SSRF
    path: str
    # ... other fields
```

### New Exception Model

A new exception class is created to differentiate SSRF validation failures:

```python
# NEW - registry/exceptions.py:194 (add new exception)
class AgentUrlSSRFError(SkillRegistryError):
    """URL failed SSRF validation for agent endpoint."""
    
    def __init__(self, url: str, message: str | None = None):
        self.url = url
        self.message = message or f"URL failed SSRF validation: {url}"
        super().__init__(self.message)
```

## API / CLI Design

### New Function: `_is_safe_url` (Centralized SSRF Validation)

**Description:** Validates a URL before making outbound requests. Checks scheme, resolves DNS, validates IP addresses, and checks against allowlist/blocklist.

**Location:** `registry/utils/agent_ssrftools.py` (new file)

**Request / Invocation:**
```python
from registry.utils.agent_ssrftools import _is_safe_url

if _is_safe_url(agent_url):
    # Safe to proceed with outbound request
    async with httpx.AsyncClient() as client:
        response = await client.get(agent_url)
else:
    # Block SSRF attempt
    raise AgentUrlSSRFError(agent_url)
```

**Expected Response / Output:**
- Returns `True` if URL passes all SSRF checks
- Returns `False` if URL is blocked (invalid scheme, private IP, metadata endpoint, etc.)

**Error Cases:**
- `AgentUrlSSRFError` raised if `validate_url()` is called and URL is unsafe
- `socket.gaierror` logged but does not raise (DNS resolution failure = unsafe URL)

### Modified Endpoint: Agent Health Check (`POST /api/agents/{path}/health`)

**Original Code (vulnerable):**
```python
# registry/api/agent_routes.py:935
async with httpx.AsyncClient(timeout=timeout_seconds) as client:
    response = await client.get(url)
```

**New Code (with SSRF protection):**
```python
# registry/api/agent_routes.py:935
from registry.utils.agent_ssrftools import validate_url
from registry.exceptions import AgentUrlSSRFError

# ... inside health check endpoint ...
validate_url(url)  # Block SSRF before making request
async with httpx.AsyncClient(timeout=timeout_seconds) as client:
    response = await client.get(url)
```

### Modified Function: `_check_endpoint_reachability`

**Original Code (vulnerable):**
```python
# registry/utils/agent_validator.py:214
response = httpx.get(well_known_url, timeout=5.0)
```

**New Code (with SSRF protection):**
```python
# registry/utils/agent_validator.py:214
from registry.utils.agent_ssrftools import validate_url
from registry.exceptions import AgentUrlSSRFError

# ... inside _check_endpoint_reachability ...
validate_url(well_known_url)  # Block SSRF before making request
response = httpx.get(well_known_url, timeout=5.0)
```

### Modified Function: `_check_agent_health` (CLI)

**Original Code (vulnerable):**
```python
# cli/agent_mgmt.py:438
response = requests.get(health_endpoint, timeout=REQUEST_TIMEOUT, ...)
```

**New Code (with SSRF protection):**
```python
# cli/agent_mgmt.py:438
from registry.utils.agent_ssrftools import validate_url
from registry.exceptions import AgentUrlSSRFError

# ... inside _check_agent_health ...
validate_url(health_endpoint)  # Block SSRF before making request
response = requests.get(health_endpoint, timeout=REQUEST_TIMEOUT, ...)
```

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `AGENT_TRUSTED_DOMAINS` | comma-separated string | `github.com,gitlab.com` | No | Additional trusted domains that can bypass private IP checks |

### Settings / Config Class Updates

**Location:** `registry/core/config.py`

Add to existing settings class:

```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    agent_trusted_domains: list[str] = Field(
        default=["github.com", "gitlab.com"],
        description="Trusted domains that bypass SSRF private IP check for agents"
    )
    
    # Add to @computed_field for agent health check
    agent_health_check_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Timeout for agent health check requests"
    )
```

### Deployment Surface Checklist

- [ ] `registry/core/config.py` - Add `agent_trusted_domains` setting
- [ ] `.env.example` - Add `AGENT_TRUSTED_DOMAINS` with default value
- [ ] Docker/ECS - Ensure config is passed through
- [ ] Helm values - Add `agent.trustedDomains` option
- [ ] Terraform variables - If applicable for self-hosted deployments
- [ ] Documentation - Add security note about URL validation

## New Dependencies

**This change uses only existing dependencies.** No new packages are required.

- Uses Python standard library: `socket`, `ipaddress`, `urllib.parse`
- Uses existing dependencies: `httpx`, `requests`, `pydantic`

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create SSRF Validation Utilities

**File:** `registry/utils/agent_ssrftools.py` (new file)

```python
"""
SSRF protection utilities for agent URLs.

This module provides centralized SSRF validation functions to prevent
Server-Side Request Forgery attacks when the registry makes outbound
HTTP requests to user-provided agent URLs.
"""

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

from urllib3.util import parse_url

logger = logging.getLogger(__name__)

# Cloud metadata endpoints that must always be blocked
CLOUD_METADATA_IPS = {
    "169.254.169.254",       # AWS EC2 metadata
    "100.100.100.200",       # Alibaba Cloud metadata
    "192.168.1.1",           # Common router IP (not always metadata, but block for safety)
}

# Private IP ranges per RFC1918
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# Link-local and other dangerous ranges
DANGEROUS_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local
    ipaddress.ip_network("192.0.2.0/24"),     # Test-net 1
    ipaddress.ip_network("198.51.100.0/24"),  # Test-net 2
    ipaddress.ip_network("203.0.113.0/24"),   # Test-net 3
    ipaddress.ip_network("224.0.0.0/4"),      # Multicast
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved
]

# Standard trusted domains that can bypass private IP check
DEFAULT_TRUSTED_DOMAINS = {
    "github.com",
    "githubusercontent.com",
    "gitlab.com",
    "gitlabusercontent.com",
    "bitbucket.org",
    "bitbucketusercontent.com",
}


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is in a private/internal range.

    Args:
        ip: IP address string (IPv4 or IPv6)

    Returns:
        True if IP is private or internal, False otherwise
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
        
        # Check cloud metadata endpoints
        if ip in CLOUD_METADATA_IPS:
            return True
        
        # Check RFC1918 private ranges
        for network in PRIVATE_NETWORKS:
            if ip_obj in network:
                return True
        
        # Check other dangerous ranges
        for network in DANGEROUS_NETWORKS:
            if ip_obj in network:
                return True
        
        return False
    except ValueError:
        # Invalid IP address
        return True


def _is_trusted_domain(hostname: str, trusted_domains: Optional[set[str]] = None) -> bool:
    """Check if hostname is in the trusted domains allowlist.

    Args:
        hostname: Hostname to check
        trusted_domains: Optional set of trusted domains (uses default if not provided)

    Returns:
        True if hostname is trusted, False otherwise
    """
    if trusted_domains is None:
        trusted_domains = DEFAULT_TRUSTED_DOMAINS
    
    hostname_lower = hostname.lower()
    
    # Direct match
    if hostname_lower in trusted_domains:
        return True
    
    # Check if any trusted domain is a suffix (for subdomains)
    for domain in trusted_domains:
        if hostname_lower.endswith(f".{domain}"):
            return True
    
    return False


def _is_cloud_metadata_ip(ip: str) -> bool:
    """Check if an IP address is a known cloud metadata endpoint.

    Args:
        ip: IP address string

    Returns:
        True if IP is a cloud metadata endpoint, False otherwise
    """
    return ip in CLOUD_METADATA_IPS


def _resolve_hostname_to_ips(hostname: str, port: Optional[int] = None) -> list[str]:
    """Resolve a hostname to IP addresses.

    Args:
        hostname: Hostname to resolve
        port: Port for the resolution (defaults to 443 for HTTPS, 80 for HTTP)

    Returns:
        List of IP addresses (may be empty if resolution fails)

    Raises:
        socket.gaierror: If hostname cannot be resolved
    """
    if port is None:
        port = 443
    
    addr_info = socket.getaddrinfo(
        hostname,
        port,
        proto=socket.IPPROTO_TCP,
    )
    
    return [info[4][0] for info in addr_info]


def _is_safe_url(url: str, trusted_domains: Optional[set[str]] = None) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).

    This function validates that a URL:
    1. Uses http or https scheme
    2. Does not resolve to a private/loopback/link-local IP address
    3. Does not target cloud metadata endpoints
    4. Passes trusted domain check

    Args:
        url: URL to validate
        trusted_domains: Optional set of trusted domains to bypass IP check

    Returns:
        True if URL is safe to fetch, False otherwise
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
        if _is_trusted_domain(hostname_lower, trusted_domains):
            logger.debug(f"SSRF protection: Trusted domain '{hostname_lower}' - bypassing IP check")
            return True
        
        # Resolve hostname to IP addresses and validate each
        try:
            ips = _resolve_hostname_to_ips(hostname, parsed.port)
        except socket.gaierror as e:
            logger.warning(f"SSRF protection: Failed to resolve hostname '{hostname}': {e}")
            return False
        
        # Check all resolved IP addresses
        for ip in ips:
            if _is_private_ip(ip):
                logger.warning(
                    f"SSRF protection: Blocked URL resolving to private IP "
                    f"'{ip}' for hostname '{hostname}'"
                )
                return False
            
            if _is_cloud_metadata_ip(ip):
                logger.warning(
                    f"SSRF protection: Blocked URL resolving to cloud metadata endpoint "
                    f"'{ip}' for hostname '{hostname}'"
                )
                return False
        
        return True
        
    except Exception as e:
        logger.warning(f"SSRF protection: Error validating URL: {e}")
        return False


def validate_url(url: str, trusted_domains: Optional[set[str]] = None) -> None:
    """Validate a URL is safe for outbound requests.

    This is the main entry point for SSRF validation. It raises an exception
    if the URL is unsafe, allowing callers to handle the error appropriately.

    Args:
        url: URL to validate
        trusted_domains: Optional set of trusted domains to bypass IP check

    Raises:
        AgentUrlSSRFError: If URL fails SSRF validation
    """
    from registry.exceptions import AgentUrlSSRFError
    
    if not _is_safe_url(url, trusted_domains):
        raise AgentUrlSSRFError(url)
```

#### Step 2: Create Custom Exception

**File:** `registry/exceptions.py` (modify existing)

```python
# Line 194 - Add new exception after SkillContentSSRFError

class AgentUrlSSRFError(SkillRegistryError):
    """URL failed SSRF validation for agent endpoint."""
    
    def __init__(self, url: str, message: str | None = None):
        self.url = url
        self.message = message or f"URL failed SSRF validation: {url}"
        super().__init__(self.message)
```

#### Step 3: Update Agent Routes Health Check

**File:** `registry/api/agent_routes.py` (modify)

**Lines to change:** ~883-1013 (check_agent_health function)

```python
# Add import at top of file (around line 30)
from ..utils.agent_ssrftools import validate_url
from ..exceptions import AgentUrlSSRFError

# Inside check_agent_health function, before making httpx.get() calls:
# Around line 935-936, add validation:

async def check_agent_health(...):
    # ... existing code ...
    
    for url in health_urls:
        # SSRF validation - NEW
        try:
            validate_url(url, settings.agent_trusted_domains)
        except AgentUrlSSRFError:
            logger.warning(f"SSRF blocked for health check URL: {url}")
            # Skip this URL and try next in chain
            continue
        
        # ... rest of existing health check code ...
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
            # ... handle response ...
```

#### Step 4: Update Agent Validator Reachability Check

**File:** `registry/utils/agent_validator.py` (modify)

```python
# Add import at top of file (around line 15)
from ..exceptions import AgentUrlSSRFError
from ..core.config import settings

# Modify _check_endpoint_reachability function (around line 212):
def _check_endpoint_reachability(url: str) -> tuple[bool, str | None]:
    """Check if agent endpoint is reachable."""
    try:
        from registry.utils.agent_ssrftools import validate_url
        
        # SSRF validation - NEW
        try:
            validate_url(url, settings.agent_trusted_domains)
        except AgentUrlSSRFError:
            logger.warning(f"SSRF blocked for endpoint reachability check: {url}")
            return (False, "Endpoint blocked by SSRF protection")
        
        well_known_url = f"{url}/.well-known/agent-card.json"
        response = httpx.get(well_known_url, timeout=5.0)
        # ... rest of function ...
```

#### Step 5: Update CLI Agent Management Health Check

**File:** `cli/agent_mgmt.py` (modify)

```python
# Add import at top of file (around line 57)
# (after existing imports, before logger setup)

# Add this import after existing imports
# from registry.utils.agent_ssrftools import validate_url  -- not available in CLI
# Instead, inline the check in _check_agent_health

def _check_agent_health(agent_url: str) -> tuple[bool, str]:
    """Check agent health by fetching agent card from /.well-known/agent-card.json."""
    # ... existing code ...
    
    health_endpoint = f"{agent_url}/.well-known/agent-card.json"
    
    # SSRF validation - NEW (inline for CLI)
    try:
        parsed = urlparse(health_endpoint)
        if parsed.scheme not in ("http", "https"):
            return False, f"Invalid URL scheme: {parsed.scheme}"
        
        hostname = parsed.hostname
        if not hostname:
            return False, "URL has no hostname"
        
        # Simple IP validation for CLI (no config access)
        import socket
        import ipaddress
        try:
            addr_info = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
            for family, socktype, proto, canonname, sockaddr in addr_info:
                ip = sockaddr[0]
                # Block private IPs
                try:
                    ip_obj = ipaddress.ip_address(ip)
                    if ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16.") or ip.startswith("172.17.") or ip.startswith("172.18.") or ip.startswith("172.19.") or ip.startswith("172.2") or ip.startswith("172.3") or ip.startswith("169.254."):
                        return False, f"Blocked SSRF attempt to private IP: {ip}"
                except ValueError:
                    pass
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"
    except Exception as e:
        return False, f"URL validation error: {str(e)}"
    
    # ... rest of function with requests.get(health_endpoint, ...) ...
```

### Error Handling

**SSRF Validation Errors:**
- Log warning with blocked URL
- Return appropriate error message to user
- Do not expose internal details (attack surfaces)
- Continue to fallback URLs where applicable (health check)

**DNS Resolution Failures:**
- Log warning
- Treat as unsafe (fail closed)
- User must fix DNS or allowlist the domain

**Invalid URL Formats:**
- Return `False` from `_is_safe_url()`
- Raise `AgentUrlSSRFError` from `validate_url()`

### Logging

```python
# Log at WARNING level for blocked URLs
logger.warning(f"SSRF protection: Blocked URL: {url}")

# Log at DEBUG level for trusted domains
logger.debug(f"SSRF protection: Trusted domain: {hostname}")

# Log at INFO level for successful health checks
logger.info(f"Agent health check succeeded: {url}")

# Log at WARNING level for unreachable endpoints
logger.warning(f"Agent health check failed: {url} - {error}")
```

## Observability
### Tracing / Metrics / Logging Points

1. **SSRF Block Events:**
   - Metric: `ssrf_blocks_total{reason="private_ip|metadata_endpoint|invalid_scheme"}` (counter)
   - Log: `level=warning message="SSRF blocked" url={url} reason={reason}`

2. **Health Check Outcomes:**
   - Metric: `health_check_total{status="success|blocked|failed"}` (counter)
   - Log: `level=info message="Health check completed" status={status}`

3. **Tracing Spans:**
   - Create span: `agent.health_check`
   - Add attribute: `ssrf.validated=true|false`
   - Add attribute: `ssrf.blocked=true|false` (if blocked)

## Scaling Considerations

- **Current Load Assumptions:** Health checks are infrequent (one-off or scheduled), not high-volume
- **Horizontal Scaling:** SSRF validation is CPU-bound but lightweight; scales linearly
- **Bottlenecks:** None expected - DNS resolution is cached by OS, IP checks are O(1)
- **Caching Strategy:** Not required - validation is fast; if needed, cache DNS lookups

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/agent_ssrftools.py` | Centralized SSRF validation utilities for agent URLs |
| `registry/utils/__init__.py` (modify) | Export new SSRF utilities |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/exceptions.py` | ~194-203 | Add `AgentUrlSSRFError` exception |
| `registry/api/agent_routes.py` | ~883-1013 | Add SSRF validation to health check endpoint |
| `registry/utils/agent_validator.py` | ~196-231 | Add SSRF validation to reachability check |
| `cli/agent_mgmt.py` | ~419-463 | Add SSRF validation to CLI health check |
| `registry/core/config.py` | ~1000+ | Add `agent_trusted_domains` setting |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New utilities (`agent_ssrftools.py`) | ~250 |
| Exception definition | ~15 |
| Agent routes changes | ~20 |
| Agent validator changes | ~10 |
| CLI changes | ~30 |
| Config changes | ~10 |
| **Total** | **~335** |

## Testing Strategy

.Pointer to testing.md - The full test plan lives there

See `testing.md` for detailed test cases covering:
- Private IP blocking (10.x.x.x, 172.16.x.x, 192.168.x.x)
- Loopback blocking (127.0.0.1, localhost)
- Cloud metadata endpoint blocking (169.254.169.254)
- Invalid scheme blocking (file://, ftp://, etc.)
- Redirect validation
- Trusted domain allowlist
- Edge cases (empty hostname, missing scheme)

## Alternatives Considered

### Alternative 1: DNS-Based Validation Only
**Description:** Only validate the hostname, not the resolved IP
**Pros:** Simpler, no DNS resolution needed
**Cons:** Vulnerable to DNS rebinding attacks; doesn't catch IPs directly
**Why Rejected:** DNS rebinding is a real attack vector; must validate resolved IPs

### Alternative 2: IP-Only Validation (No DNS)
**Description:** Accept IP addresses directly and validate them
**Pros:** Faster; no DNS dependency
**Cons:** Doesn't prevent subdomain takeover; many legitimate services use domains
**Why Rejected:** Would break legitimate agent registrations; domains are standard

### Alternative 3: Blocklist-Based (Known Bad IPs)
**Description:** Maintain a blocklist of known malicious IPs
**Pros:** Simple; works without network access
**Cons:** Requires constant updates; doesn't catch new threats
**Why Rejected:** Range blocking (RFC1918) is more comprehensive and maintenance-free

### Alternative 4: Proxy-Based (Route Through Proxy)
**Description:** Route all outbound requests through a proxy that enforces SSRF protection
**Pros:** Centralized enforcement; can add advanced features
**Cons:** Adds infrastructure complexity; single point of failure
**Why Rejected:** Overkill for this use case; inline validation is sufficient

### Alternative 5: No Validation (Trust Users)
**Description:** Don't implement SSRF protection, rely on user education
**Pros:** No implementation effort
**Cons:** Opens system to SSRF attacks
**Why Rejected:** Security-critical; similar to existing skill_service protection

### Comparison Matrix

| Criteria | Chosen (RangeBlocking) | DNS Only | IP Only | Blocklist | No Validation |
|----------|------------------------|----------|---------|-----------|---------------|
| Security | High | Low | Medium | Low | None |
| Performance | Fast | Fast | Fast | Fast | N/A |
| Maintainability | Low | Low | Low | High | N/A |
| Complexity | Medium | Low | Low | Low | None |
| Coverage | Comprehensive | Partial | Partial | Partial | None |

## Rollout Plan

- **Phase 1: Implementation** (out of scope for this skill)
  - Create SSRF utilities
  - Patch all vulnerable endpoints
  - Add comprehensive tests

- **Phase 2: Testing**
  - Run existing test suite
  - Add SSRF-specific test cases
  - Manual testing with legitimate and malicious URLs

- **Phase 3: Deployment**
  - Deploy to staging
  - Monitor SSRF block metrics
  - Deploy to production

- **Phase 4: Monitoring**
  - Alert on SSRF block rate increases
  - Review allowlist periodically
  - Update documentation

## Open Questions

1. Should `localhost` be blocked even for trusted domains?
2. Do we need to support IPv6 ranges for SSRF protection?
3. Should we add a configuration option to allowlist specific IPs?
4. Should we add metrics for SSRF blocks to the health check endpoint?

## References

- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [RFC1918 - Address Allocation for Private Internets](https://datatracker.ietf.org/doc/html/rfc1918)
- [AWS EC2 Metadata Service](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-metadata.html)
- [Current Skill Service SSRF Protection](registry/services/skill_service.py:128-192)
