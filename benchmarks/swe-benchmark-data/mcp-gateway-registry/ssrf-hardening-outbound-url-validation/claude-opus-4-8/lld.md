# Low-Level Design: SSRF Hardening -- Outbound URL Validation

*Created: 2026-07-21*
*Author: Claude (claude-opus-4-8)*
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
The MCP Gateway Registry makes outbound HTTP requests to URLs supplied by users during server/agent/skill registration. Only one service (`skill_service.py`) validates these URLs against SSRF attacks. Nine other call sites connect to user-originated URLs without any SSRF protection, allowing an attacker to probe internal networks or exfiltrate cloud metadata credentials via `169.254.169.254`.

### Goals
- Promote the existing private `_is_safe_url()` logic to a shared, tested utility module
- Apply SSRF validation uniformly at all user-originated outbound call sites
- Add early rejection at registration time so downstream consumers never see unsafe URLs
- Provide operator-configurable trusted-domain allowlist independent of GitHub-specific settings
- Emit metrics for blocked attempts to support alerting

### Non-Goals
- DNS rebinding / connect-time IP pinning (future work)
- Egress network controls (security groups, NAT gateway restrictions)
- Backfill validation of previously-registered URLs
- Rate limiting of outbound requests

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/skill_service.py:70-192` | SSRF protection (private functions) | Source of `_is_safe_url()` and `_is_private_ip()` to extract |
| `registry/core/mcp_client.py:225-451` | MCP transport detection + tool fetch | HIGH-risk: connects to `proxy_pass_url` with no SSRF check |
| `registry/health/service.py:429-460` | Health checks against registered servers | HIGH-risk: hits `proxy_pass_url` from DB |
| `registry/utils/agent_validator.py:211-230` | Agent reachability probe | HIGH-risk: `httpx.get()` to user-supplied agent URL |
| `registry/services/skill_scanner.py:253-264` | Skill content download | HIGH-risk: bare `httpx.get()` on SKILL.md URL |
| `auth_server/server.py:~4024` | MCP proxy streaming | HIGH-risk: streams to `X-Upstream-Url` header value |
| `registry/core/config.py` | Pydantic Settings class | Integration point for new env var |
| `registry/utils/` | Shared utilities (24 modules) | Destination for new `ssrf.py` |
| `tests/unit/services/test_skill_service_ssrf_allowlist.py` | Existing SSRF unit tests | Migrate to test shared module |
| `cli/mcp_utils.py:35-51` | Scheme-only CLI validation | Reference only; CLI stays as-is |

### Existing Patterns Identified

1. **Private-function-per-service pattern**: Each service defines private helpers prefixed with `_`. The SSRF functions follow this pattern today but should be promoted to a shared module since the logic is needed across multiple services.
   - Files: `registry/services/skill_service.py`
   - How a future implementer should follow this: Create public functions in `registry/utils/ssrf.py` (no underscore prefix), then have `skill_service.py` import from there.

2. **Settings via pydantic_settings**: All configuration lives in `registry/core/config.py` as fields on the `Settings` class with env-var defaults. New settings follow this pattern.
   - Files: `registry/core/config.py`
   - How a future implementer should follow this: Add the new field to the `Settings` class with a `Field(...)` descriptor, document in `.env.example`.

3. **httpx as primary HTTP client**: All async outbound calls use `httpx.AsyncClient`. Sync calls use `httpx.get()` (agent_validator) or `httpx.Client` (federation). SSRF check must happen before either.
   - Files: all call sites listed above
   - How a future implementer should follow this: Call `is_safe_url()` before constructing the httpx client or calling `httpx.get()`.

4. **Structured logging with module logger**: Each module creates `logger = logging.getLogger(__name__)` and logs at appropriate levels.
   - Files: all modules
   - How a future implementer should follow this: Use `logger.warning()` for blocked URLs, `logger.debug()` for trusted-domain bypasses.

5. **Metrics via OTel counter**: The codebase uses OpenTelemetry for metrics emission (see `registry/metrics/client.py`).
   - Files: `registry/metrics/client.py`
   - How a future implementer should follow this: Create a counter instrument in the ssrf module for `ssrf_blocked_total`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/core/config.py` | Extends | Add `ssrf_additional_trusted_domains` field |
| `registry/services/skill_service.py` | Depends on | Replace private functions with imports from `registry/utils/ssrf.py` |
| `registry/core/mcp_client.py` | Uses | Call `is_safe_url()` before transport detection and tool fetch |
| `registry/health/service.py` | Uses | Call `is_safe_url()` before health check HTTP calls |
| `registry/utils/agent_validator.py` | Uses | Call `is_safe_url()` before agent reachability probe |
| `registry/services/skill_scanner.py` | Uses | Call `is_safe_url()` before content download |
| `auth_server/server.py` | Uses | Call `is_safe_url()` before proxying |
| Registration API routes | Uses | Validate URL fields at submission time |

### Constraints and Limitations Discovered
- **MCP SDK wraps httpx internally**: `mcp.client.sse.sse_client` and `streamablehttp_client` open connections without an interception hook. The SSRF check must happen before entering these context managers, not inside them.
- **Trusted domains skip DNS resolution**: The existing `_is_safe_url()` returns `True` immediately for trusted domains without resolving IPs. This is by design (GHES instances on private networks), and the new module preserves this behavior.
- **Health checks run in background loops**: The check happens asynchronously every 5 minutes. A blocking DNS lookup in `is_safe_url()` is acceptable because the existing code already does `socket.getaddrinfo` synchronously and the health check runs in a dedicated async task.
- **`169.254.169.254` is already link-local**: The `ipaddress` module's `is_link_local` catches it, but the code also has an explicit string check as defense-in-depth.

## Architecture

### System Context Diagram

```
+------------------+       +-------------------+       +------------------+
|   User / Client  | ----> | Registry API      | ----> | MongoDB          |
+------------------+       | (FastAPI)         |       +------------------+
                           +-------------------+
                                   |
                    +--------------+--------------+
                    |              |              |
               +--------+   +--------+   +--------+
               | Health |   |  MCP   |   | Skill  |
               | Svc    |   | Client |   | Svc    |
               +--------+   +--------+   +--------+
                    |              |              |
                    v              v              v
           +------------------------------------------------+
           |         registry/utils/ssrf.py                  |
           |   is_safe_url() -- gate before ANY outbound     |
           +------------------------------------------------+
                    |              |              |
                    v              v              v
           +------------------+  +------------------+
           | External MCP     |  | GitHub/GitLab    |
           | Servers (user)   |  | (trusted)        |
           +------------------+  +------------------+
```

### Sequence Diagram (Registration-Time Validation)

```
Client              API Route              ssrf.py            MongoDB
  |                    |                      |                  |
  |  POST /servers     |                      |                  |
  | {proxy_pass_url}   |                      |                  |
  |------------------->|                      |                  |
  |                    |  is_safe_url(url)    |                  |
  |                    |--------------------->|                  |
  |                    |                      |-- resolve DNS    |
  |                    |                      |-- check IPs      |
  |                    |    True/False        |                  |
  |                    |<--------------------|                  |
  |                    |                      |                  |
  |           [if False: 422 error]           |                  |
  |<-------------------| (blocked)            |                  |
  |                    |                      |                  |
  |           [if True: persist]              |                  |
  |                    |------------------------------------------->|
  |                    |                      |                  |
  |  201 Created       |                      |                  |
  |<-------------------|                      |                  |
```

### Sequence Diagram (Runtime Validation -- Health Check)

```
HealthService         ssrf.py           httpx          External Server
     |                   |                |                  |
     | is_safe_url(url)  |                |                  |
     |------------------>|                |                  |
     |                   |-- resolve DNS  |                  |
     |   True            |                |                  |
     |<------------------|                |                  |
     |                                    |                  |
     |  GET /health                       |                  |
     |----------------------------------->|                  |
     |                                    |----------------->|
     |                                    |   200 OK        |
     |                                    |<-----------------|
     |   response                         |                  |
     |<-----------------------------------|                  |
```

### Component Diagram

```
registry/utils/ssrf.py
+-----------------------------------------------+
|                                               |
|  is_safe_url(url: str) -> bool                |
|    |-- _validate_scheme(parsed)               |
|    |-- _check_trusted_domains(hostname)       |
|    |-- _resolve_and_check_ips(hostname, port) |
|                                               |
|  is_private_ip(ip_str: str) -> bool           |
|                                               |
|  get_trusted_domains() -> frozenset[str]      |
|    (cached, merges defaults + config)         |
|                                               |
|  _DEFAULT_TRUSTED_DOMAINS: frozenset          |
|                                               |
|  _ssrf_blocked_counter (OTel)                 |
+-----------------------------------------------+
```

## Data Models

### New Models

No new Pydantic models are required. The SSRF module operates on plain strings and returns booleans.

### Model Changes

The `Settings` class in `registry/core/config.py` gains one field:

```python
ssrf_additional_trusted_domains: str = Field(
    default="",
    description=(
        "Comma-separated list of additional hostnames to trust for SSRF "
        "validation (e.g., 'internal-git.corp.example.com,registry.internal'). "
        "These skip the private-IP resolution check. Merged with built-in "
        "defaults (github.com, gitlab.com, etc.) and github_extra_hosts."
    ),
)
```

## API / CLI Design

### New Endpoints / Commands

No new endpoints. Existing registration endpoints gain validation logic.

### Changed Error Responses

**Existing:** `POST /servers/register` (and similar for agents/skills) returns 400 for generic validation errors.

**New behavior:** Returns HTTP 422 with a structured body when a URL field fails SSRF validation:

```json
{
  "detail": "URL validation failed: proxy_pass_url resolves to a private IP address",
  "field": "proxy_pass_url",
  "url": "http://10.0.0.5:8080/mcp",
  "reason": "private_ip"
}
```

Possible `reason` values: `invalid_scheme`, `no_hostname`, `dns_resolution_failed`, `private_ip`, `metadata_endpoint`.

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `SSRF_ADDITIONAL_TRUSTED_DOMAINS` | str (comma-separated) | `""` | No | Additional hostnames that bypass private-IP checks (for corporate GHES, internal registries) |

### Settings / Config Class Updates

```python
ssrf_additional_trusted_domains: str = Field(
    default="",
    description=(
        "Comma-separated list of additional hostnames to trust for SSRF "
        "validation (e.g., 'internal-git.corp.example.com,registry.internal'). "
        "These skip the private-IP resolution check. Merged with built-in "
        "defaults (github.com, gitlab.com, etc.) and github_extra_hosts."
    ),
)
```

### Deployment Surface Checklist

| Surface | File | Action |
|---------|------|--------|
| `.env.example` | `.env.example` | Add commented entry |
| Docker Compose | `docker-compose.yml` | No change (picked up via `.env`) |
| Helm values | `charts/registry/values.yaml` | Add under `env:` section |
| Helm reserved names | `charts/registry/templates/_helpers.tpl` | Add to reserved list |
| Terraform variables | `terraform/aws-ecs/variables.tf` | Add variable definition |
| Terraform task def | `terraform/aws-ecs/main.tf` | Wire into container env |
| docs | `docs/unified-parameter-reference.md` | Document the parameter |

## New Dependencies

This change uses only existing dependencies. No new packages are required.

- `socket` (stdlib) -- DNS resolution
- `ipaddress` (stdlib) -- IP classification
- `urllib.parse` (stdlib) -- URL parsing
- `functools.lru_cache` (stdlib) -- trusted-domain caching
- `logging` (stdlib) -- structured logs
- `opentelemetry.metrics` (already in deps) -- counter metric

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create `registry/utils/ssrf.py`

**File:** `registry/utils/ssrf.py` (new file)

```python
"""Shared SSRF protection utilities.

Validates outbound URLs against private-IP, metadata-endpoint, and
scheme restrictions. All services that make HTTP requests to
user-supplied URLs must call is_safe_url() before connecting.
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

from opentelemetry import metrics

from registry.core.config import settings

logger = logging.getLogger(__name__)

meter = metrics.get_meter(__name__)
_ssrf_blocked_counter = meter.create_counter(
    name="ssrf_blocked_total",
    description="Number of outbound URLs blocked by SSRF validation",
    unit="1",
)

_DEFAULT_TRUSTED_DOMAINS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "raw.githubusercontent.com",
        "bitbucket.org",
    }
)


@lru_cache(maxsize=1)
def get_trusted_domains() -> frozenset[str]:
    """Return the merged set of trusted domains.

    Combines built-in defaults, github_extra_hosts, and
    ssrf_additional_trusted_domains. Cached because settings are
    immutable per-process.
    """
    extra_github = settings.github_extra_hosts or ""
    extra_ssrf = settings.ssrf_additional_trusted_domains or ""
    combined_raw = f"{extra_github},{extra_ssrf}"
    extra = frozenset(h.strip().lower() for h in combined_raw.split(",") if h.strip())
    return _DEFAULT_TRUSTED_DOMAINS | extra


def is_private_ip(
    ip_str: str,
) -> bool:
    """Check if an IP address is private, loopback, link-local, or reserved.

    Args:
        ip_str: IP address string to check.

    Returns:
        True if the IP is in a non-routable range, False otherwise.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_private:
            return True
        if ip.is_loopback:
            return True
        if ip.is_link_local:
            return True
        if ip.is_reserved:
            return True
        if ip_str == "169.254.169.254":
            return True
        return False
    except ValueError:
        return True


def is_safe_url(
    url: str,
    call_site: str = "unknown",
) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).

    Validates that a URL:
    1. Uses http or https scheme
    2. Has a resolvable hostname
    3. Does not resolve to a private/loopback/link-local IP address
    4. Does not target cloud metadata endpoints

    Trusted domains (built-in + configured) skip the IP resolution check.

    Args:
        url: URL to validate.
        call_site: Label for metrics/logging identifying the caller.

    Returns:
        True if the URL is safe to fetch, False otherwise.
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            logger.warning(
                "SSRF protection: Blocked URL with scheme '%s' at %s",
                parsed.scheme,
                call_site,
            )
            _ssrf_blocked_counter.add(1, {"call_site": call_site, "reason": "invalid_scheme"})
            return False

        hostname = parsed.hostname
        if not hostname:
            logger.warning("SSRF protection: URL has no hostname at %s", call_site)
            _ssrf_blocked_counter.add(1, {"call_site": call_site, "reason": "no_hostname"})
            return False

        hostname_lower = hostname.lower()
        if hostname_lower in get_trusted_domains():
            logger.debug("SSRF protection: Trusted domain '%s'", hostname_lower)
            return True

        try:
            addr_info = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as e:
            logger.warning(
                "SSRF protection: Failed to resolve hostname '%s' at %s: %s",
                hostname,
                call_site,
                e,
            )
            _ssrf_blocked_counter.add(
                1, {"call_site": call_site, "reason": "dns_resolution_failed"}
            )
            return False

        for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
            ip_address = sockaddr[0]
            if is_private_ip(ip_address):
                logger.warning(
                    "SSRF protection: Blocked URL resolving to private IP '%s' "
                    "for hostname '%s' at %s",
                    ip_address,
                    hostname,
                    call_site,
                )
                _ssrf_blocked_counter.add(
                    1, {"call_site": call_site, "reason": "private_ip"}
                )
                return False

        return True

    except Exception as e:
        logger.warning("SSRF protection: Error validating URL at %s: %s", call_site, e)
        _ssrf_blocked_counter.add(1, {"call_site": call_site, "reason": "validation_error"})
        return False
```

#### Step 2: Add configuration field to `registry/core/config.py`

**File:** `registry/core/config.py`
**Lines:** Insert after the `github_extra_hosts` field (search for `github_extra_hosts` in the Settings class and add the new field immediately after it).

```python
ssrf_additional_trusted_domains: str = Field(
    default="",
    description=(
        "Comma-separated list of additional hostnames to trust for SSRF "
        "validation (e.g., 'internal-git.corp.example.com,registry.internal'). "
        "These skip the private-IP resolution check. Merged with built-in "
        "defaults (github.com, gitlab.com, etc.) and github_extra_hosts."
    ),
)
```

#### Step 3: Refactor `registry/services/skill_service.py`

**File:** `registry/services/skill_service.py`
**Lines:** 70-192 (replace the private functions)

Remove:
- `_DEFAULT_TRUSTED_DOMAINS` (lines 71-78)
- `_trusted_domains()` (lines 81-91)
- `_is_private_ip()` (lines 94-125)
- `_is_safe_url()` (lines 128-192)

Replace with import at the top of the file:
```python
from registry.utils.ssrf import is_safe_url
```

Update all internal call sites from `_is_safe_url(url)` to `is_safe_url(url, call_site="skill_service")`. There are four such calls in the file:
- `_validate_skill_url()` (~line 605)
- `_parse_skill_md_content()` (~line 687)
- `_check_skill_health()` (~line 884)
- `_fetch_authenticated_content()` (~line 1062)

#### Step 4: Add SSRF check to `registry/core/mcp_client.py`

**File:** `registry/core/mcp_client.py`
**Lines:** Add import at top, guard at entry of `detect_server_transport()` (~line 210) and `get_tools_from_server_with_transport()` (~line 253).

```python
from registry.utils.ssrf import is_safe_url
```

In `detect_server_transport()`:
```python
async def detect_server_transport(base_url: str) -> str:
    if not is_safe_url(base_url, call_site="mcp_client.detect_transport"):
        logger.warning("SSRF: Blocked transport detection for %s", base_url)
        return "blocked"
    # ... existing logic unchanged ...
```

In `get_tools_from_server_with_transport()`:
```python
async def get_tools_from_server_with_transport(
    base_url: str, transport: str = "auto"
) -> list[dict] | None:
    if not is_safe_url(base_url, call_site="mcp_client.get_tools"):
        logger.warning("SSRF: Blocked tool fetch for %s", base_url)
        return None
    # ... existing logic unchanged ...
```

#### Step 5: Add SSRF check to `registry/health/service.py`

**File:** `registry/health/service.py`
**Lines:** ~429 (inside `_check_single_service()`, after extracting `proxy_pass_url` and before the transport-aware check)

```python
from registry.utils.ssrf import is_safe_url
```

Insert after the `proxy_pass_url = server_info.get("proxy_pass_url")` line:
```python
if proxy_pass_url and not is_safe_url(proxy_pass_url, call_site="health_service"):
    logger.warning("SSRF: Skipping health check for blocked URL: %s", proxy_pass_url)
    new_status = HealthStatus.UNHEALTHY
    self.server_health_status[service_path] = new_status
    return previous_status != new_status
```

#### Step 6: Add SSRF check to `registry/utils/agent_validator.py`

**File:** `registry/utils/agent_validator.py`
**Lines:** ~211 (at the top of `_check_agent_reachability()`)

```python
from registry.utils.ssrf import is_safe_url
```

Insert at the start of the function body:
```python
def _check_agent_reachability(url: str) -> tuple[bool, str | None]:
    if not is_safe_url(url, call_site="agent_validator"):
        return (False, "URL blocked by SSRF protection")
    # ... existing try/except logic unchanged ...
```

#### Step 7: Add SSRF check to `registry/services/skill_scanner.py`

**File:** `registry/services/skill_scanner.py`
**Lines:** ~253 (at the top of `_download_skill_content()`)

```python
from registry.utils.ssrf import is_safe_url
```

Insert at the start of the method body:
```python
def _download_skill_content(self, skill_md_url: str, headers: dict | None = None) -> str:
    if not is_safe_url(skill_md_url, call_site="skill_scanner"):
        raise ValueError(f"URL blocked by SSRF protection: {skill_md_url}")
    # ... existing logic unchanged ...
```

#### Step 8: Add SSRF check to `auth_server/server.py` (MCP proxy)

**File:** `auth_server/server.py`
**Lines:** ~4024 (in `mcp_proxy()`, after extracting the upstream URL from the `X-Upstream-Url` header)

```python
from registry.utils.ssrf import is_safe_url
```

Insert after extracting `upstream_url`:
```python
if not is_safe_url(upstream_url, call_site="auth_server.mcp_proxy"):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Upstream URL blocked by SSRF protection",
            "field": "X-Upstream-Url",
            "url": upstream_url,
            "reason": "private_ip",
        },
    )
```

#### Step 9: Add registration-time validation

**File:** `registry/api/server_routes.py`

In the server registration handler, after parsing the request body and extracting `proxy_pass_url`:

```python
from registry.utils.ssrf import is_safe_url

# After extracting proxy_pass_url from the request:
if proxy_pass_url and not is_safe_url(proxy_pass_url, call_site="server_registration"):
    raise HTTPException(
        status_code=422,
        detail={
            "detail": "URL validation failed: proxy_pass_url resolves to a private IP address",
            "field": "proxy_pass_url",
            "url": proxy_pass_url,
            "reason": "private_ip",
        },
    )
```

Apply the same pattern to:
- `registry/api/agent_routes.py` -- validate agent URL field
- `registry/api/skill_routes.py` -- validate `skill_md_url` field

#### Step 10: Migrate existing tests

**File:** `tests/unit/utils/test_ssrf.py` (new file)

Move and expand tests from `tests/unit/services/test_skill_service_ssrf_allowlist.py`:

```python
import socket
from unittest.mock import patch

import pytest

from registry.utils.ssrf import get_trusted_domains, is_private_ip, is_safe_url


class TestIsPrivateIp:
    @pytest.mark.parametrize(
        "ip,expected",
        [
            ("127.0.0.1", True),
            ("10.0.0.1", True),
            ("172.16.0.1", True),
            ("192.168.1.1", True),
            ("169.254.169.254", True),
            ("::1", True),
            ("fe80::1", True),
            ("fd00::1", True),
            ("8.8.8.8", False),
            ("1.1.1.1", False),
            ("93.184.216.34", False),
            ("2606:4700::1", False),
            ("invalid", True),
            ("", True),
        ],
    )
    def test_ip_classification(self, ip: str, expected: bool) -> None:
        assert is_private_ip(ip) == expected


class TestIsSafeUrl:
    def test_blocks_non_http_schemes(self) -> None:
        assert is_safe_url("ftp://example.com/file") is False
        assert is_safe_url("file:///etc/passwd") is False
        assert is_safe_url("gopher://evil.com") is False
        assert is_safe_url("javascript:alert(1)") is False

    def test_blocks_empty_and_malformed(self) -> None:
        assert is_safe_url("") is False
        assert is_safe_url("http://") is False
        assert is_safe_url("not-a-url") is False

    def test_allows_trusted_domains(self) -> None:
        assert is_safe_url("https://github.com/org/repo") is True
        assert is_safe_url("https://raw.githubusercontent.com/file") is True
        assert is_safe_url("https://gitlab.com/group/project") is True
        assert is_safe_url("https://bitbucket.org/team/repo") is True

    def test_trusted_domains_case_insensitive(self) -> None:
        assert is_safe_url("https://GitHub.COM/org/repo") is True
        assert is_safe_url("https://GITLAB.com/group/project") is True

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_private_ip_resolution(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))
        ]
        assert is_safe_url("http://evil.com/path") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_loopback_resolution(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ]
        assert is_safe_url("http://localhost-alias.com/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_allows_public_ip(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        assert is_safe_url("https://example.com/") is True

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_metadata_endpoint(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ]
        assert is_safe_url("http://metadata.internal/latest") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_dns_failure(self, mock_dns) -> None:
        mock_dns.side_effect = socket.gaierror("Name or service not known")
        assert is_safe_url("http://nonexistent.invalid/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_if_any_resolved_ip_is_private(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80)),
        ]
        assert is_safe_url("http://dual-homed.com/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_allows_multiple_public_ips(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
        ]
        assert is_safe_url("https://cdn.example.com/") is True


class TestGetTrustedDomains:
    def test_includes_defaults(self) -> None:
        get_trusted_domains.cache_clear()
        domains = get_trusted_domains()
        assert "github.com" in domains
        assert "gitlab.com" in domains
        assert "raw.githubusercontent.com" in domains
        assert "bitbucket.org" in domains
```

### Error Handling

| Call Site | On SSRF Block | User-Facing Error |
|-----------|---------------|-------------------|
| Registration endpoints | Raise `HTTPException(422)` | JSON body with field, URL, reason |
| `mcp_client.py` | Return `None` / `"blocked"` | Server marked unhealthy on next health check |
| `health/service.py` | Mark `UNHEALTHY` | Server shows unhealthy in dashboard |
| `agent_validator.py` | Return `(False, message)` | Validation warning in registration response |
| `skill_scanner.py` | Raise `ValueError` | Scanner reports failure in scan result |
| `auth_server` proxy | Return 422 JSON | Client sees clear error |

### Logging

| Level | When | Example Message |
|-------|------|-----------------|
| WARNING | URL blocked | `SSRF protection: Blocked URL resolving to private IP '10.0.0.5' for hostname 'evil.com' at health_service` |
| WARNING | DNS failure | `SSRF protection: Failed to resolve hostname 'nonexistent.invalid' at mcp_client: [Errno -2] Name or service not known` |
| DEBUG | Trusted domain allowed | `SSRF protection: Trusted domain 'github.com'` |

## Observability

### Metrics

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `ssrf_blocked_total` | Counter | `call_site`, `reason` | Incremented each time a URL is blocked |

Label values for `reason`: `invalid_scheme`, `no_hostname`, `dns_resolution_failed`, `private_ip`, `validation_error`.

Label values for `call_site`: `skill_service`, `mcp_client.detect_transport`, `mcp_client.get_tools`, `health_service`, `agent_validator`, `skill_scanner`, `auth_server.mcp_proxy`, `server_registration`, `agent_registration`, `skill_registration`.

### Alerting Recommendation

```yaml
# Prometheus alert rule (for documentation, not implemented by this change)
- alert: SSRFBlockRateHigh
  expr: rate(ssrf_blocked_total[5m]) > 5
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "High rate of SSRF-blocked requests"
```

## Scaling Considerations

- **DNS resolution overhead**: `socket.getaddrinfo()` is synchronous but completes in <10ms for cached entries. The OS resolver cache means repeated checks for the same hostname are fast. For the health check loop (runs every 5 minutes per server), this is negligible.
- **LRU cache on trusted domains**: The `get_trusted_domains()` function is cached with `maxsize=1`. Since settings are immutable per-process, this never evicts.
- **No additional network calls**: The SSRF check uses DNS only, not HTTP pre-flights. The actual outbound request still happens exactly once.
- **Registration-time validation**: Adds one DNS lookup per registration request. At current registration volumes (low single-digit per minute), this is not a bottleneck.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/ssrf.py` | Shared SSRF validation module (public API) |
| `tests/unit/utils/test_ssrf.py` | Unit tests for SSRF module |
| `tests/integration/test_ssrf_registration.py` | Integration test for registration-time rejection |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/core/config.py` | +6 | Add `ssrf_additional_trusted_domains` setting |
| `registry/services/skill_service.py` | -120, +5 | Remove private SSRF functions, import from shared module |
| `registry/core/mcp_client.py` | +12 | Add `is_safe_url()` checks at two entry points |
| `registry/health/service.py` | +8 | Add `is_safe_url()` check before health check HTTP call |
| `registry/utils/agent_validator.py` | +5 | Add `is_safe_url()` check at function entry |
| `registry/services/skill_scanner.py` | +5 | Add `is_safe_url()` check before download |
| `auth_server/server.py` | +12 | Add `is_safe_url()` check in MCP proxy |
| `registry/api/server_routes.py` | +12 | Registration-time URL validation |
| `registry/api/agent_routes.py` | +12 | Registration-time URL validation |
| `registry/api/skill_routes.py` | +12 | Registration-time URL validation |
| `.env.example` | +3 | Document new env var |
| `charts/registry/values.yaml` | +2 | Add Helm value |
| `charts/registry/templates/_helpers.tpl` | +1 | Add to reserved env names |
| `terraform/aws-ecs/variables.tf` | +8 | Add Terraform variable |
| `terraform/aws-ecs/main.tf` | +4 | Wire env var into container |
| `tests/unit/services/test_skill_service_ssrf_allowlist.py` | ~20 modified | Update imports to test shared module |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (ssrf.py) | ~140 |
| New tests | ~180 |
| Modified code (call sites + config) | ~90 |
| Config/deployment wiring | ~30 |
| Removed code (skill_service.py privates) | ~-120 |
| **Net new** | **~320** |

## Testing Strategy

See `testing.md` for the comprehensive testing plan including functional, backwards-compat, deployment, and E2E tests.

## Alternatives Considered

### Alternative 1: Middleware-Based SSRF Proxy

**Description:** Create a centralized outbound HTTP proxy (e.g., a custom `httpx.Transport`) that validates every request automatically, rather than checking at each call site.

**Pros:**
- Single enforcement point -- impossible to forget a call site
- Catches URLs constructed dynamically after registration

**Cons:**
- The MCP SDK (`mcp.client.sse.sse_client`, `streamablehttp_client`) creates its own httpx clients internally; there is no hook to inject a custom transport
- Would require forking/patching the MCP SDK
- Admin-configured URLs (webhooks, gates) would need an exemption mechanism

**Why Rejected:** The MCP SDK limitation makes this infeasible without upstream changes. The call-site approach, combined with registration-time validation, provides equivalent coverage for user-originated URLs.

### Alternative 2: Network-Layer Enforcement Only

**Description:** Rely solely on AWS security groups and VPC configuration to block outbound access to private ranges and metadata endpoints.

**Pros:**
- Zero application code changes
- Works regardless of application bugs

**Cons:**
- Does not protect against DNS rebinding (hostname resolves to public IP at SG evaluation time, then rebinds to private at connection time)
- Provides no observability (no logs, no metrics)
- Cannot provide user-facing error messages at registration time
- Platform-specific (not portable to non-AWS deployments)

**Why Rejected:** Defense-in-depth requires application-layer validation. Network controls are complementary but insufficient alone.

### Alternative 3: Validate Only at Registration Time

**Description:** Check URLs once when they are submitted and trust the stored value thereafter.

**Pros:**
- Simpler implementation (fewer call sites)
- No runtime overhead for health checks or tool fetches

**Cons:**
- Vulnerable to DNS rebinding: an attacker registers `evil.com` pointing to `8.8.8.8`, then changes the DNS record to `169.254.169.254` after registration
- Does not protect against URLs already in the database from before this feature shipped

**Why Rejected:** Runtime checks provide defense against DNS rebinding. The combined approach (registration-time + runtime) is the industry standard.

### Comparison Matrix

| Criteria | Chosen (Call-site + Registration) | Alt 1 (Proxy Transport) | Alt 2 (Network-only) | Alt 3 (Registration-only) |
|----------|-----------------------------------|------------------------|----------------------|--------------------------|
| Completeness | High | Highest | Medium | Medium |
| Feasibility | High | Low (MCP SDK) | High | High |
| Observability | High | High | None | Partial |
| DNS Rebinding | Protected | Protected | Vulnerable | Vulnerable |
| Complexity | Medium | High | None | Low |

## Rollout Plan

- **Phase 1: Implementation** -- Create `registry/utils/ssrf.py`, wire into all call sites, add tests.
- **Phase 2: Testing** -- Run full test suite (`uv run pytest tests/ -n 8`); verify no regressions. Manual testing with private-IP URLs.
- **Phase 3: Deployment** -- Deploy with default configuration. `SSRF_ADDITIONAL_TRUSTED_DOMAINS` allows operators to allowlist internal hosts if false positives arise. No separate feature flag required since the protection is always-on and backwards-compatible for valid public URLs.
- **Phase 4: Monitoring** -- Watch `ssrf_blocked_total` metric for false positives in the first week. Operators can add domains to the allowlist without code changes.

## Open Questions

1. **Should we add connect-time IP pinning?** The current approach resolves DNS before connecting but does not pin the resolved IP for the actual connection. A TOCTOU gap exists where DNS could rebind between the check and the connect. This is a known limitation shared with most SSRF libraries; connect-time pinning would require a custom httpx transport (feasible for our own clients, not for MCP SDK clients). Recommend tracking as a follow-up.

2. **Should previously-registered private-IP URLs be flagged?** A migration script could scan all stored `proxy_pass_url` values and flag those that fail the SSRF check. This is out of scope for the initial implementation but may be valuable for operators.

## References

- OWASP SSRF Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- AWS IMDS protection: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html
- Existing implementation in `registry/services/skill_service.py:70-192`
- Existing tests in `tests/unit/services/test_skill_service_ssrf_allowlist.py`
