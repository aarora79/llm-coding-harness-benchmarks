# Low-Level Design: SSRF Protection for Federation Client

*Created: 2026-06-24*
*Author: Claude (moonshotai-kimi-k2-thinking)*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [Implementation Details](#implementation-details)
8. [Observability](#observability)
9. [File Changes](#file-changes)
10. [Testing Strategy](#testing-strategy)
11. [Alternatives Considered](#alternatives-considered)
12. [Rollout Plan](#rollout-plan)

## Overview

### Problem Statement
The federation client in the MCP Gateway Registry makes HTTP requests to configurable peer registry endpoints without Server-Side Request Forgery (SSRF) validation. This poses a security risk where malicious or misconfigured peer registry URLs could direct requests to internal network endpoints, potentially accessing sensitive services or metadata endpoints.

### Goals
- Adopt existing SSRF protection mechanisms for federation client HTTP requests
- Reuse the proven `_is_safe_url()` validation logic from `skill_service.py`
- Maintain backwards compatibility with legitimate peer registry configurations
- Provide security audit logging for blocked requests
- Support both global and federation-specific trusted domain configurations

### Non-Goals
- Change existing skill service SSRF validation (already implemented)
- Add new database schemas or models
- Modify federation API contracts
- Change peer registry endpoint configuration format

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/federation/base_client.py` | Base federation client with shared HTTP request logic | Primary location to add SSRF validation |
| `registry/services/federation/peer_registry_client.py` | Peer registry client implementation | Uses BaseFederationClient for HTTP requests |
| `registry/services/federation/asor_client.py` | ASOR federation client | Uses BaseFederationClient for HTTP requests |
| `registry/services/skill_service.py` | Contains existing SSRF protection (`_is_safe_url()`) | Source of proven validation logic |
| `registry/core/config.py` | Configuration settings management | Destination for new SSRF-related settings |
| `registry/services/federation/federation_auth.py` | Federation authentication manager | Ensures federation token handling remains unaffected |

### Existing Patterns Identified

1. **Skill Service SSRF Protection**
   - Function: `_is_safe_url(url: str) -> bool` in `skill_service.py`
   - Features:
     - Scheme validation (http/https only)
     - IP address validation (blocks private/loopback/link-local)
     - Cloud metadata endpoint blocking (169.254.169.254)
     - Trusted domain allowlist with GHES support
     - DNS resolution and validation
   - Configuration: Uses `settings.github_extra_hosts` for trusted domains
   - Logging: Comprehensive logging for security auditing

2. **Federation Client Architecture**
   - Base class: `BaseFederationClient` with common HTTP logic `_make_request()`
   - Derived classes: `PeerRegistryClient` and `AsorClient`
   - Configuration: Instantiated with `endpoint` URLs from `PeerRegistryConfig`
   - Authentication: Support for token-based auth with `FederationAuthManager`

3. **Configuration Pattern**
   - Settings defined in `registry/core/config.py` with Pydantic `BaseSettings`
   - Environment variables as primary configuration source
   - Convention: `UPPER_CASE_SETTINGS_NAME` translates to env var
   - Used by: `_trusted_domains()` in skill service for trusted host management

## Architecture

### System Context Diagram

```
┌───────────────────────────────────────────────────────────┐
│                       Federation Sync Job               │
└────────────────────┬──────────────────────────────────────┘
                     │
                     │ calls
                     ▼
┌───────────────────────────────────────────────────────────┐
│              PeerRegistryClient.fetch_agents()           │
└────────────────────┬──────────────────────────────────────┘
                     │
                     │ delegates to
                     ▼
┌───────────────────────────────────────────────────────────┐
│          BaseFederationClient._make_request()            │
│                                                          │
│  NEW: Add SSRF validation here before HTTP request       │
│       ┌───────────────────────────────────────────┐     │
│       │  _is_safe_url(url) validation              │     │
│       │  - Resolves hostname                       │     │
│       │  - Checks IP is not private/loopback      │     │
│       │  - Validates scheme (http/https)         │     │
│       └───────────────────────────────────────────┘     │
└────────────────────┬──────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                       │
        ▼                       ▼
   ┌─────────────┐        ┌─────────────┐
   │   Blocked   │        │   Allowed   │
   │   (log &    │        │   (proceed  │
   │   return    │        │   to HTTP   │
   │   None)     │        │   request)  │
   └─────────────┘        └─────────────┘
```

### Sequence Diagram

```
Federation Sync Process with SSRF Validation

1. Peer Sync Request
   ┌────────────────────────────────────────────┐
   │ Registry Service                           │
   │─────────────────────────────────────────────│
   │ schedule_federation_sync()                 │
   └────────────────┬───────────────────────────┘
                    │
                    │ create
                    ▼
   ┌────────────────────────────────────────────┐
   │ PeerRegistryClient                         │
   │─────────────────────────────────────────────│
   │ endpoint = "http://internal.service:8080"  │
   └────────────────┬───────────────────────────┘
                    │
                    │ fetch_agents()
                    │
                    ├──► Build request URL
                    │    url = endpoint + "/api/federation/agents"
                    │
                    ├──► SSRF Validation (NEW)
                    │    is_safe = _is_safe_url(url)
                    │
                    │ ┌──────────────────────────────┐
                    │ │ if not is_safe:             │
                    │ │    log.warning("SSRF uh oh")│
                    │ │    return None             │
                    │ └──────────────────────────────┘
                    │
                    └──► _make_request(url)
                         │
                         ├──► httpx.request()
                         │
                         ├──► Response
                         └──► Return agents
```

### Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      registry.services.federation                        │
│                                                                          │
│  ┌────────────────────┐               ┌──────────────────────┐            │
│  │ FederationConfig   │               │ BaseFederationClient │            │
│  │ (settings)         │               │                      │            │
│  │ - trusted_domains  │◄─────────────►│ - endpoint (URL)     │            │
│  │ - github_extra_    │   config     │ - timeout            │            │
│  │   hosts            │              │ - retry_attempts     │            │
│  └────────────────────┘              │ - _make_request()    │            │
│                                     │   (with NEW SSRF     │            │
│                                     │    validation)       │            │
│                                     └──────────┬───────────┘            │
│                                                │ inherits                │
│                                       ┌────────┴────────┐              │
│                                       │                   │              │
│                     ┌─────────────────┴─────────┐       │              │
│                     │                           │       │              │
│  ┌──────────────────▼─────────┐    ┌─────────────▼────┐   │              │
│  │ PeerRegistryClient         │    │ AsorClient       │   │              │
│  │                            │    │                  │   │              │
│  │ - fetch_agents()           │    │ - fetch_agent()  │   │              │
│  │ - fetch_servers()          │    │ - fetch_all_     │   │              │
│  │ - fetch_security_scans()   │    │   agents()       │   │              │
│  └────────────────────────────┘    └──────────────────┘   │              │
└──────────────────────────────────────────────────────────────────────────┘
```

## Data Models

### New Configuration Model

```python
# In registry/core/config.py

class Settings(BaseSettings):
    # ... existing settings ...
    
    # Federation SSRF Protection Settings
    federation_trusted_domains: str = Field(
        default="",
        description="Comma-separated list of trusted registry domains that skip IP validation"
    )
    
    federation_validation_enabled: bool = Field(
        default=True,
        description="Enable SSRF validation for federation client requests"
    )
```

### Reused Validation Model

```python
# From registry/services/skill_service.py (already exists)

def _is_safe_url(url: str, allowlist: frozenset[str] | None = None) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).
    
    Args:
        url: URL to validate
        allowlist: Optional override for trusted domains
        
    Returns:
        True if the URL is safe to fetch, False otherwise
    """
    # Existing implementation handles:
    # - Scheme validation (http/https only)
    # - IP resolution and private IP blocking
    # - Cloud metadata endpoint blocking
    # - Trusted domain allowlist matching
```

## API / CLI Design

### No API Changes Required
The federation client operates internally, initiated by background sync jobs. No REST API or CLI changes are needed.

### Configuration Updates
New environment variables:

```bash
# Federation trusted domains (similar to GITHUB_EXTRA_HOSTS)
FEDERATION_TRUSTED_DOMAINS="registry.corp.com,internal.registry.local"

# Disable SSRF validation (NOT recommended for production)
FEDERATION_VALIDATION_ENABLED=false
```

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `FEDERATION_TRUSTED_DOMAINS` | string | `""` | No | Comma-separated list of trusted registry domains that skip IP validation |
| `FEDERATION_VALIDATION_ENABLED` | bool | `true` | No | Enable/disable SSRF validation for federation requests |

### Settings Class Updates

```python
# registry/core/config.py

from pydantic import Field
from typing import frozenset

class Settings(BaseSettings):
    # ... existing settings ...
    
    federation_trusted_domains: str = Field(
        default="",
        description="Comma-separated list of trusted registry domains that skip IP validation"
    )
    
    federation_validation_enabled: bool = Field(
        default=True,
        description="Enable SSRF validation for federation client requests"
    )
    
    @property
    def federation_allowed_domains(self) -> frozenset[str]:
        """Return parsed trusted domains from settings."""
        if not self.federation_trusted_domains:
            return frozenset()
        return frozenset(
            h.strip().lower() 
            for h in self.federation_trusted_domains.split(",") 
            if h.strip()
        )
```

### Deployment Surface Checklist
- [ ] `.env.example` - Add federation SSRF config examples
- [ ] `docker-compose.yml` - Document federation environment variables
- [ ] Helm charts - Add federation trusted domains configuration
- [ ] Terraform - Add federation SSRF settings
- [ ] Documentation - Explain federation SSRF configuration

## Implementation Details

### Step-by-Step Implementation Plan

#### Step 1: Extract and Generalize SSRF Validation
**File**: `registry/services/ssrf_utils.py` (NEW)

Create a standalone SSRF validation module to share logic between skill service and federation clients.

```python
"""
SSRF (Server-Side Request Forgery) protection utilities.

Shared validation logic for federation clients, skill service, and other HTTP clients.
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Default trusted domains (similar to skill service)
_DEFAULT_TRUSTED_DOMAINS: frozenset = frozenset({
    "github.com",
    "gitlab.com",
    "raw.githubusercontent.com",
    "bitbucket.org",
})


@lru_cache(maxsize=1)
def _get_trusted_domains(extra_domains: str = "") -> frozenset[str]:
    """Return combined trusted domains with extra domains from config."""
    if not extra_domains:
        return _DEFAULT_TRUSTED_DOMAINS
    
    extra = frozenset(
        h.strip().lower() 
        for h in extra_domains.split(",") 
        if h.strip()
    )
    return _DEFAULT_TRUSTED_DOMAINS | extra


def _is_private_ip(ip_str: str) -> bool:
    """
    Check if an IP address is private, loopback, or link-local.
    
    Args:
        ip_str: IP address string to check
        
    Returns:
        True if the IP is private/loopback/link-local, False otherwise
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        
        # Check for private, loopback, link-local, or reserved addresses
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
        
        # Check for cloud metadata endpoint
        if ip_str == "169.254.169.254":
            return True
        
        return False
    except ValueError:
        # Invalid IP address format - treat as unsafe
        return True


def is_safe_url(url: str, trusted_domains: str | frozenset[str] = "") -> bool:
    """
    Check if a URL is safe to fetch (SSRF protection).
    
    Validates that a URL:
    1. Uses http or https scheme
    2. Does not resolve to private/loopback/link-local IP addresses
    3. Does not target cloud metadata endpoints
    4. Allows trusted domains to skip IP validation
    
    Args:
        url: URL to validate
        trusted_domains: Comma-separated string or frozenset of trusted domains
                        that can skip IP validation
                        
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
        
        # Normalize trusted domains
        if isinstance(trusted_domains, str):
            allowlist = _get_trusted_domains(trusted_domains)
        elif isinstance(trusted_domains, frozenset):
            allowlist = trusted_domains
        else:
            allowlist = _DEFAULT_TRUSTED_DOMAINS
        
        # Check if hostname is in trusted domains allowlist
        hostname_lower = hostname.lower()
        if hostname_lower in allowlist:
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

#### Step 2: Update Base Federation Client
**File**: `registry/services/federation/base_client.py`

Add SSRF validation to `_make_request()` before making HTTP requests.

```python
"""
Base federation client interface with SSRF protection.

Provides common functionality for all federation clients with URL validation.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..core.config import settings
from ..utils.ssrf_utils import is_safe_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class BaseFederationClient(ABC):
    """Base class for federation clients with SSRF protection."""
    
    def __init__(self, endpoint: str, timeout_seconds: int = 30, retry_attempts: int = 3):
        """
        Initialize federation client with SSRF protection.
        
        Args:
            endpoint: Base URL for the federation API
            timeout_seconds: HTTP request timeout
            retry_attempts: Number of retry attempts for failed requests
        """
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = retry_attempts
        self.client = httpx.Client(timeout=timeout_seconds)
        
        logger.debug(
            f"Initialized BaseFederationClient for endpoint '{self.endpoint}' "
            f"with SSRF validation enabled={settings.federation_validation_enabled}"
        )
    
    def __del__(self):
        """Clean up HTTP client."""
        if hasattr(self, "client"):
            self.client.close()
    
    @abstractmethod
    def fetch_server(self, server_name: str, **kwargs) -> dict[str, Any] | None:
        """Fetch a single server from the federated registry."""
        pass
    
    @abstractmethod
    def fetch_all_servers(self, server_names: list[str], **kwargs) -> list[dict[str, Any]]:
        """Fetch multiple servers from the federated registry."""
        pass
    
    def _validate_url(self, url: str) -> bool:
        """
        Validate URL for SSRF safety.
        
        Only validates if federation_validation_enabled is True.
        
        Args:
            url: URL to validate
            
        Returns:
            True if safe or validation disabled, False otherwise
        """
        if not settings.federation_validation_enabled:
            logger.debug(f"SSRF validation disabled, allowing URL: {url}")
            return True
        
        try:
            is_safe = is_safe_url(url, settings.federation_allowed_domains)
            if not is_safe:
                logger.warning(f"SSRF protection: Blocked federation request to unsafe URL: {url}")
            return is_safe
        except Exception as e:
            logger.error(f"SSRF validation error for URL {url}: {e}")
            # Fail closed - treat validation errors as unsafe
            return False
    
    def _make_request(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Make HTTP request with SSRF validation and retry logic.
        
        Args:
            url: Full URL to request
            method: HTTP method (GET, POST, etc.)
            headers: HTTP headers
            params: Query parameters
            data: Request body data
            
        Returns:
            Response JSON or None if request fails
        """
        # SSRF validation - fail fast before making HTTP request
        if not self._validate_url(url):
            return None
        
        # Validate redirect URLs during request (handled by Upper compatible if needed)
        
        for attempt in range(self.retry_attempts):
            try:
                logger.debug(
                    f"Making {method} request to {url} (attempt {attempt + 1}/{self.retry_attempts})"
                )
                
                response = self.client.request(
                    method=method, url=url, headers=headers, params=params, json=data
                )
                
                response.raise_for_status()
                return response.json()
                
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code} for {url}: {e}")
                if e.response.status_code in [404, 401, 403]:
                    # Don't retry for these errors
                    return None
                if attempt == self.retry_attempts - 1:
                    return None
                    
            except httpx.RequestError as e:
                logger.error(f"Request error for {url}: {e}")
                if attempt == self.retry_attempts - 1:
                    return None
                    
            except Exception as e:
                logger.error(f"Unexpected error for {url}: {e}")
                if attempt == self.retry_attempts - 1:
                    return None
        
        return None
```

#### Step 3: Update Configuration Settings
**File**: `registry/core/config.py`

Add federation SSRF protection settings.

```python
# Add to existing Settings class

from pydantic import Field

class Settings(BaseSettings):
    # ... existing settings ...
    
    # Federation SSRF Protection Settings
    federation_trusted_domains: str = Field(
        default="",
        description="Comma-separated list of trusted registry domains that skip IP validation"
    )
    
    federation_validation_enabled: bool = Field(
        default=True,
        description="Enable SSRF validation for federation client requests"
    )
    
    @property
    def federation_allowed_domains(self) -> frozenset[str]:
        """Return parsed trusted domains for federation clients."""
        if not self.federation_trusted_domains:
            return frozenset()
        return frozenset(
            h.strip().lower()
            for h in self.federation_trusted_domains.split(",")
            if h.strip()
        )
```

#### Step 4: Update Peer Registry Client
**File**: `registry/services/federation/peer_registry_client.py`

Update import comments and add SSRF info to logging.

```python
"""
Peer registry federation client with SSRF protection.

Fetches servers and agents from peer registries using the standard
federation API endpoints with JWT authentication and SSRF URL validation.
"""

# ... existing imports ...

# Add to __init__ documentation
"""
itialize the federation client.

Args:
    peer_config: Configuration for the peer registry
    timeout_seconds: HTTP request timeout
    retry_attempts: Number of retry attempts for failed requests

Federation requests are validated for SSRF protection using the same
logic as skill service URL validation. Trusted domains can be configured
via the FEDERATION_TRUSTED_DOMAINS setting.
"""
```

#### Step 5: Update ASOR Client
**File**: `registry/services/federation/asor_client.py`

Similar updates to add SSRF protection documentation.

```python
"""
ASOR (Agent Server Orchestration Registry) federation client.

Fetches agent configurations from ASOR with SSRF-protected requests.
"""
```

## Observability

### Logging Points

**SSRF Validation Blocked**: Warning level
```
logger.warning(f"SSRF protection: Blocked federation request to unsafe URL: {url}")
```

**SSRF Validation Disabled**: Debug level (development only)
```
logger.debug(f"SSRF validation disabled, allowing URL: {url}")
```

**SSRF Validation Error**: Error level
```
logger.error(f"SSRF validation error for URL {url}: {e}")
```

**Federation Client Initialized**: Debug level
```
logger.debug(f"Initialized BaseFederationClient for endpoint '{endpoint}' with SSRF validation enabled={settings.federation_validation_enabled}")
```

### Metrics

Consider adding the following metrics if supported by the application's telemetry:
- `federation.ssrf.blocked_total`: Counter of blocked federation requests
- `federation.request.total`: Counter of total federation requests
- `federation.request.errors`: Counter of federation request errors

## Implementation Details

### Error Handling

**Validation Failure**: 
- Return `None` from `_make_request()` to indicate failure
- Let consumers handle `None` responses (already supported)
- Log warning with URL and validation reason

**DNS Resolution Failure**:
- Treat as validation failure (blocked)
- Log warning with hostname and error details

**IP Validation Failure**:
- Log private IP address and hostname
- Include reason code if possible

**Unexpected Validation Errors**:
- Fail closed (treat as unsafe)
- Log error with exception details
- Do not proceed with HTTP request

### Testing

See [Testing Strategy](#testing-strategy) below for detailed test cases.

## File Changes

### New Files

| File Path | Description | Estimated Lines | Priority |
|-----------|-------------|-----------------|----------|
| `registry/utils/ssrf_utils.py` | Standalone SSRF validation utilities | ~120 | High |

### Modified Files

| File Path | Lines Modified | Description | Priority |
|-----------|----------------|-------------|----------|
| `registry/services/federation/base_client.py` | 30-40 | Add `_validate_url()` and SSRF check in `_make_request()` | High |
| `registry/core/config.py` | 20-30 | Add federation SSRF settings | High |
| `registry/services/federation/peer_registry_client.py` | 5-10 | Update docstrings and logging | Medium |
| `registry/services/federation/asor_client.py` | 5-10 | Update docstrings and logging | Medium |
| `.env.example` | 5-10 | Document new settings | Low |
| `docker-compose.yml` | 5-10 | Document new environment variables | Low |
| `helm/values.yaml` | 5-10 | Add federation SSRF config | Low |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~130 |
| Modified code | ~80 |
| Test code | ~200 |
| **Total** | **~410** |

## Testing Strategy

See comprehensive testing plan in `testing.md`.

## Alternatives Considered

### Alternative 1: Duplicate SSRF Logic in Federation
**Description**: Copy the `_is_safe_url()` function directly into federation client module.

**Pros**:
- Simple implementation
- No new module needed
- Self-contained solution

**Cons**:
- Code duplication
- Drift risk between implementations
- Changes to skill service won't apply to federation

**Decision**: **Rejected** - Prefer shared utilities over duplication.

### Alternative 2: Individual Validation in Each Federation Client
**Description**: Add `_validate_url()` only in `PeerRegistryClient` and `AsorClient`, not in base class.

**Pros**:
- Base class remains simple
- Each client controls its own validation

**Cons**:
- Code duplication across clients
- Easier to forget validation for new clients
- Inconsistent validation rules

**Decision**: **Rejected** - Prefer centralized validation in base class.

### Alternative 3: Middleware-based SSRF Protection
**Description**: Use external library or proxy that intercepts all HTTP requests and validates them.

**Pros**:
- Global protection
- No code changes needed
- Transparent to application

**Cons**:
- External dependency complexity
- Limited configuration per request type
- Harder to customize per use case
- Performance overhead

**Decision**: **Rejected** - Built-in validation provides better control and transparency.

### Chosen Solution: (**Selected**)
- Extract shared SSRF utilities module
- Centralize validation in base class
- Use existing, proven validation logic
- Maintain configuration flexibility

**Rationale**: 
- Reuses existing, battle-tested validation logic
- Prevents code duplication
- Central validation ensures consistency
- Proper separation of concerns
- Easier to test and maintain

## Rollout Plan

### Phase 1: Implementation
- Create `ssrf_utils.py` with extracted validation logic
- Update `base_client.py` with SSRF validation
- Add configuration settings
- Update documentation

### Phase 2: Testing
- Unit tests for validation logic
- Integration tests for federation sync
- Manual testing with real peer registries
- Security scanning

### Phase 3: Deployment
- Deploy with monitoring enabled
- Monitor logs for blocked requests
- Review configuration for legitimate use cases
- Document any exceptions in allowlist

### Phase 4: Verification
- Confirm federation still works in production
- Verify SSRF logs show expected security checks
- Validate that legitimate peer registries still work
- Update runbooks if needed

## Open Questions
- Should federation URLs support non-standard ports? If yes, add port validation.
- How should we handle empty endpoint URLs provided by configuration? Currently fails validation.
- Should we add a mode for "validation=warn-only" for gradual rollout?

## References
- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- Related in codebase: `registry/services/skill_service.py:_is_safe_url()`
- Federation design: `docs/design/federation-system-architecture.md`
