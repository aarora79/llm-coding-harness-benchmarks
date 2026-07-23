# Low-Level Design: SSRF Hardening for Agent-Card Fetch and Health-Check Paths

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
15. [Open Questions](#open-questions)
16. [References](#references)

## Overview

### Problem Statement

The MCP Gateway Registry issues outbound HTTP requests to user-supplied URLs on several code paths. A correct SSRF guard, `_is_safe_url()`, already exists in `registry/services/skill_service.py` and protects the SKILL.md fetch path, but the same guard is not applied to two other outbound paths that also fetch user-controlled URLs:

1. **Agent-card reachability probe** in `registry/utils/agent_validator.py::_check_endpoint_reachability` - fetches `{agent_url}/.well-known/agent-card.json` on `POST /agents/register`.
2. **Server/agent health checks** in `registry/health/service.py` - repeatedly fetches the user-supplied `proxy_pass_url` (and derived `mcp_endpoint`/`sse_endpoint`) on a periodic background loop and on the immediate "check now" path. The agent health endpoint `POST /agents/{path}/health` (`registry/api/agent_routes.py`) fetches the stored agent URL the same way.

Neither path validates the URL against private/loopback/link-local/reserved IPs or the cloud-metadata address (`169.254.169.254`). An attacker who can register an agent or server can therefore drive the gateway - which runs on ECS with an instance role and metadata endpoint - to make requests to internal-only addresses. Because the health path also injects configured credentials and follows redirects, the impact includes internal reconnaissance, cloud-metadata credential theft, and credential leakage to attacker-controlled internal targets.

This design promotes the existing guard into a shared utility, applies it to both paths, and adds a configurable allowlist. It is strictly backwards-compatible: public URLs continue to work unchanged, and the new configuration is optional.

### Goals

- Reuse the existing, proven `_is_safe_url()` logic rather than reimplementing it.
- Block outbound requests to private/loopback/link-local/reserved IPs and the cloud-metadata endpoint on the agent-card and health-check paths.
- Re-validate URLs after redirects (or disable redirects) so a permitted host cannot 30x-redirect to an internal target.
- Add a dedicated, comma-separated `SSRF_ALLOWED_HOSTS` allowlist for operators with legitimate internal hosts.
- Preserve all current behavior when the new setting is unset (backwards-compatible).
- Keep existing skill SSRF tests green with zero behavior change.

### Non-Goals

- Rewriting the transport-aware health-check logic beyond inserting validation calls.
- Protecting federation clients (`asor_client`, `peer_registry_client`, `agentcore_client`) - they fetch operator-configured endpoints or inline content, not arbitrary user URLs.
- Network-layer egress controls (security groups, egress firewalls) - complementary but out of scope.
- Rejecting internal URLs at the registration schema layer (would break the local-runtime and internal-MCP-server use cases; validation is enforced only at the outbound-fetch boundary).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/skill_service.py` (lines 71-192) | Home of `_is_safe_url`, `_is_private_ip`, `_trusted_domains`, `_DEFAULT_TRUSTED_DOMAINS`; SKILL.md fetch + redirect re-validation | Source of the guard to promote; must keep working via re-export |
| `registry/utils/agent_validator.py` (lines 196-230) | `_check_endpoint_reachability`: sync `httpx.get` of `{url}/.well-known/agent-card.json`, timeout 5s, no redirect control | Agent-card sink to harden |
| `registry/api/agent_routes.py` (lines 186-205, 883-998) | `_build_agent_health_urls` and `check_agent_health`: async `httpx.AsyncClient` GET x2 + HEAD fallback of the stored agent URL | Agent health sink to harden |
| `registry/health/service.py` (lines 324-408, 560-957, 1195-1305) | `HealthMonitoringService`: periodic loop and `perform_immediate_health_check`; multiple GET/POST/HEAD to `proxy_pass_url`/derived endpoints, all `follow_redirects=True` | Server health sink to harden (primary choke point at `_check_server_endpoint_transport_aware`, line 674) |
| `registry/core/config.py` (lines 53-60, 292-299, 1209) | `Settings(BaseSettings)` pydantic-settings v2; `github_extra_hosts` field; global `settings` singleton | Where to add `SSRF_ALLOWED_HOSTS` |
| `registry/exceptions.py` (lines 9-12, 58-68, 194-204) | `RegistryError` base; `SkillUrlValidationError`, `SkillContentSSRFError` | Add a generic `UrlValidationError` for shared use |
| `registry/core/endpoint_utils.py` (lines 28-112) | `get_endpoint_url` / `get_endpoint_url_from_server_info`: builds `mcp`/`sse` endpoints from `proxy_pass_url` | Understand endpoint derivation feeding the health path |
| `tests/unit/services/test_skill_service_ssrf_allowlist.py` | SSRF allowlist test patterns (cache clear, settings patch, `getaddrinfo` mock) | Template for new tests; must keep passing |
| `.env.example`, `docker-compose*.yml`, `terraform/aws-ecs/**`, `charts/mcpgw/**`, `docs/configuration.md` | Deployment surfaces for `github_extra_hosts` | Where `SSRF_ALLOWED_HOSTS` must be wired |

### Existing Patterns Identified

1. **The SSRF guard (fail-closed, allowlist-bypass).** `_is_safe_url` (skill_service.py:128-192) checks scheme in `{http, https}`, requires a hostname, allows trusted hosts to skip DNS, resolves via `socket.getaddrinfo`, and rejects any resolved private IP. Any unexpected exception returns `False` (fail-closed). `_is_private_ip` (94-125) returns `True` on invalid IPs (fail-closed) and blocks private/loopback/link-local/reserved plus `169.254.169.254`.
   - Files: `registry/services/skill_service.py`.
   - How a future implementer should follow this: move these four symbols verbatim into `registry/utils/ssrf.py`, preserving fail-closed semantics; do not reimplement.

2. **Allowlist derivation via `@lru_cache` over a comma-separated setting.** `_trusted_domains()` (81-91) reads `settings.github_extra_hosts`, splits on comma, strips/lowercases, and unions with the built-in defaults; cached per-process. `github_auth.py::_build_allowed_hosts` uses the same parse.
   - Files: `registry/services/skill_service.py:81-91`, `registry/services/github_auth.py:59-63`.
   - How a future implementer should follow this: extend the same cached function to also merge `settings.ssrf_allowed_hosts`; tests must call `.cache_clear()`.

3. **Redirect re-validation.** Every skill fetch pre-validates the request URL, sets `follow_redirects=True`, then re-validates `str(response.url)` guarded by `final_url != original` (e.g. skill_service.py:616, 707, 896, 1071).
   - How a future implementer should follow this: apply the same pre-check + post-redirect re-check on the agent-card and health paths, or disable redirects where re-checking is impractical.

4. **Config as pydantic-settings v2.** New settings are `snake_case` fields with `Field(default=..., description=...)`; env var is the UPPER_SNAKE name; `case_sensitive=False`, `extra="ignore"`. CSV allowlist fields default to `""`.
   - Files: `registry/core/config.py:53-60, 292-299`.

5. **Logging.** `logger = logging.getLogger(__name__)`; SSRF blocks use `logger.warning(...)`, allowlist passes use `logger.debug(...)`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/utils/ssrf.py` (new) | Provides | Hosts `is_safe_url`, `_is_private_ip`, `_trusted_domains`, `_DEFAULT_TRUSTED_DOMAINS` |
| `registry/services/skill_service.py` | Uses (re-export) | Imports the four symbols from `registry.utils.ssrf`; keeps module-level names for test compatibility |
| `registry/utils/agent_validator.py` | Uses | Calls `is_safe_url` before/after the agent-card fetch |
| `registry/api/agent_routes.py` | Uses | Validates agent URL in `check_agent_health` before building/using health URLs |
| `registry/health/service.py` | Uses | Validates `proxy_pass_url`/resolved endpoint at `_check_server_endpoint_transport_aware` and `perform_immediate_health_check` |
| `registry/core/config.py` | Extends | Adds `ssrf_allowed_hosts` field |
| `registry/exceptions.py` | Extends | Adds `UrlValidationError(RegistryError)` |

### Constraints and Limitations Discovered

- **`@lru_cache` on the allowlist** means changing `SSRF_ALLOWED_HOSTS` requires a process restart. Acceptable and consistent with the existing `github_extra_hosts` behavior; document it.
- **`follow_redirects=True` everywhere on the health path** (service.py:598, 656, 711, 738, 808, 872, 911, 934). Host validation alone is insufficient; redirects must be disabled or re-validated per hop. Simplest safe choice for the health path: pre-validate the endpoint and set `follow_redirects=False` (health endpoints should not legitimately redirect). This is a minor behavior change bounded to the health path; call it out in review.
- **Health path is a hot loop** (every `health_check_interval_seconds`, default 300s, batched by 10). Validation must be cheap; `socket.getaddrinfo` adds a DNS lookup per non-allowlisted host per check. Mitigation options in [Scaling Considerations](#scaling-considerations).
- **Sync vs async.** `_check_endpoint_reachability` is sync (`httpx.get`); the health path is async. `is_safe_url` uses blocking `socket.getaddrinfo`; on the async path this must run without blocking the event loop for long. Given the existing skill async path already calls the sync `_is_safe_url` directly, we keep parity but note it in [Open Questions](#open-questions).
- **The `AgentCard.url` and `proxy_pass_url` are required free-form strings** validated only for scheme/hostname format. We must not tighten the schema (local-runtime and internal-MCP use cases rely on non-public URLs), so validation stays at the fetch boundary and is allowlist-overridable.

## Architecture

### System Context Diagram

```
                         +-------------------------------+
   register agent/server |     MCP Gateway Registry      |
   (user-supplied URL)    ------------------------------->|
                         |                               |
                         |  registry/utils/ssrf.py       |
                         |    is_safe_url(url) --------+  |
                         |                             |  |
   +----------------------------+   +------------------v-----------+
   | agent-card path            |   | health-check path            |
   | agent_validator            |   | health/service.py            |
   |  _check_endpoint_reach...  |   |  _check_server_endpoint...    |
   | agent_routes.check_health  |   |  perform_immediate_health...  |
   +-------------+--------------+   +---------------+--------------+
                 | is_safe_url? no -> unreachable   | is_safe_url? no -> unhealthy(blocked)
                 v yes                              v yes
        httpx.get(agent-card)              httpx GET/POST/HEAD(proxy_pass_url)
                 |                                  |
                 v                                  v
        +---------------------------------------------------------+
        | BLOCKED: private/loopback/link-local/reserved IPs,      |
        | 169.254.169.254 (cloud metadata), non-http(s) schemes   |
        | ALLOWED: public IPs + hosts in SSRF_ALLOWED_HOSTS       |
        +---------------------------------------------------------+
```

### Sequence Diagram - Agent-card reachability probe

```
Client                agent_routes         agent_validator        ssrf.is_safe_url      target
  |  POST /agents/register  |                     |                     |                 |
  |------------------------>|                     |                     |                 |
  |                         | validate_agent_card |                     |                 |
  |                         |  (verify_endpoint)  |                     |                 |
  |                         |-------------------->| _check_endpoint_reachability            |
  |                         |                     |-------------------->|                 |
  |                         |                     |  is_safe_url(url)?   |                 |
  |                         |                     |<---- False ---------|                 |
  |                         |                     |  (blocked: no fetch, return unreachable)|
  |                         |                     |-------------------->|  (True) httpx.get|
  |                         |                     |                     |---------------->|
  |                         |                     |  no redirect follow (SSRF-safe)         |
  |                         |<--------------------|                     |                 |
  |<------------------------|  register succeeds (reachability is non-blocking)            |
```

### Sequence Diagram - Health check

```
Scheduler/API        health/service         ssrf.is_safe_url        target
  | _perform_health_checks / perform_immediate_health_check          |
  |-------------------->|                        |                    |
  |                     | _check_server_endpoint_transport_aware       |
  |                     | is_safe_url(proxy_pass_url)?                  |
  |                     |----------------------->|                    |
  |                     |<--- False -------------|                    |
  |                     | status = UNHEALTHY_SSRF_BLOCKED (no request) |
  |                     |----------------------->|  (True)            |
  |                     |  httpx GET/POST/HEAD (follow_redirects=False)|
  |                     |------------------------------------------->  |
```

### Component Diagram

```
registry/utils/ssrf.py  (NEW - single source of truth)
  |-- _DEFAULT_TRUSTED_DOMAINS: frozenset
  |-- _trusted_domains() -> frozenset           # merges github_extra_hosts + ssrf_allowed_hosts
  |-- _is_private_ip(ip_str) -> bool
  |-- is_safe_url(url) -> bool                   # public name; _is_safe_url alias kept
        ^                 ^                  ^
        |                 |                  |
 skill_service      agent_validator     health/service + agent_routes
 (re-export)        (agent-card)        (health checks)
```

## Data Models

No new Pydantic request/response models are required. The change adds one configuration field and one exception class.

### New Models

None. (This is a security-hardening change on existing outbound paths, not a new API surface.)

### Model Changes

- `registry/core/config.py::Settings` gains two fields, `ssrf_allowed_hosts` and `ssrf_enforce` (see [Configuration Parameters](#configuration-parameters)).
- `registry/constants.py::HealthStatus` gains `UNHEALTHY_SSRF_BLOCKED`.
- No new exception class (the earlier `UrlValidationError` is dropped as dead code).

## API / CLI Design

No new endpoints or CLI commands. Two existing endpoints change their observable behavior only for URLs that resolve to blocked addresses:

### `POST /agents/register` (behavior refinement)

**Description:** When `verify_endpoint=True`, the agent-card reachability probe now refuses to fetch URLs that fail SSRF validation.

**Request / Invocation:** unchanged.

**Expected Response / Output:** For a public URL, unchanged. For a URL resolving to a private/loopback/link-local/metadata address (and not allowlisted), the reachability result is `(False, "URL blocked by SSRF protection")`. Registration still succeeds because reachability is non-blocking today; only the reachability field/warning changes.

**Error Cases:** No new hard failures. A blocked URL logs a warning and reports unreachable.

### `POST /agents/{path}/health` and server refresh/toggle (behavior refinement)

**Description:** The health check refuses to issue outbound requests to URLs that fail SSRF validation.

**Expected Response / Output:** For a blocked URL, the health status is `unhealthy` with a detail such as `blocked-by-ssrf-protection` (a new `HealthStatus` value; see below), and no outbound request is made.

**Error Cases:** No exception is surfaced to the caller; the status simply reports blocked/unhealthy.

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `SSRF_ALLOWED_HOSTS` | str (CSV) | `""` | No | Comma-separated hostnames that bypass the SSRF private-IP check on all guarded outbound paths (skill, agent-card, health). Use for legitimate internal MCP servers/agents. Empty means "no extra hosts trusted". |
| `SSRF_ENFORCE` | bool | `true` | No | When `true`, blocked URLs are refused (unreachable/unhealthy). When `false` (monitor-only), the block decision is logged and counted via `ssrf_blocked_total` but the request still proceeds. Used to observe blast radius before enforcing on a fleet with internal servers. |

### Settings / Config Class Updates

Add to `registry/core/config.py` `Settings` (near line 299, after `github_extra_hosts`):

```python
ssrf_allowed_hosts: str = Field(
    default="",
    description=(
        "Comma-separated hostnames that bypass the SSRF private-IP check for "
        "outbound fetches (skill, agent-card, and health-check paths). Use for "
        "legitimate internal MCP servers or agents on private networks. Empty "
        "means no extra hosts are trusted. Keep the list tight; each host here "
        "can be reached at private/internal addresses by the gateway."
    ),
)
```

```python
ssrf_enforce: bool = Field(
    default=True,
    description=(
        "When true, URLs that fail SSRF validation are refused (agent-card "
        "probe reports unreachable; health check reports UNHEALTHY_SSRF_BLOCKED). "
        "When false (monitor-only), the block decision is logged and counted but "
        "the request still proceeds - use to observe blast radius before enforcing."
    ),
)
```

Rationale for a **separate** setting rather than reusing `github_extra_hosts`: `github_extra_hosts` also grants GitHub auth-header injection (github_auth.py). Overloading it to trust internal MCP servers would leak GitHub credentials to those hosts. A dedicated `ssrf_allowed_hosts` keeps the two trust decisions independent. Both are merged in `_trusted_domains()`.

`ssrf_enforce` supports the staged rollout in [Rollout Plan](#rollout-plan). Each guarded call site checks `if not is_safe_url(url): <count/log>; if settings.ssrf_enforce: <block>`. The default `true` is safe for public-URL fleets; operators with internal servers deploy `SSRF_ENFORCE=false` first.

### Deployment Surface Checklist

Both new variables (`SSRF_ALLOWED_HOSTS`, `SSRF_ENFORCE`) must be added everywhere `GITHUB_EXTRA_HOSTS` appears so they are configurable end-to-end. Note the compose files use the **list** form (`- NAME=${NAME:-}`), not the map form:

- [ ] `.env.example` (near line 623 - add a documented, commented `SSRF_ALLOWED_HOSTS=` and `SSRF_ENFORCE=true` block)
- [ ] `docker-compose.yml` (near line 195 - `- SSRF_ALLOWED_HOSTS=${SSRF_ALLOWED_HOSTS:-}` and `- SSRF_ENFORCE=${SSRF_ENFORCE:-true}`)
- [ ] `docker-compose.podman.yml` (near line 92, list form)
- [ ] `docker-compose.prebuilt.yml` (near line 102, list form)
- [ ] `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` (container env injection, near line 1265)
- [ ] `terraform/aws-ecs/modules/mcp-gateway/variables.tf` (module var, near line 1280)
- [ ] `terraform/aws-ecs/variables.tf` (root var, near line 1327)
- [ ] `terraform/aws-ecs/main.tf` (root->module wiring, near line 298)
- [ ] `charts/mcpgw/values.yaml` (`ssrfAllowedHosts: ""`, near line 54)
- [ ] `charts/mcpgw/templates/deployment.yaml` (env mapping, near line 81)
- [ ] `charts/mcpgw/reserved-env-names.txt` (add `SSRF_ALLOWED_HOSTS`, near line 10)
- [ ] `docs/configuration.md` (table row + security note)
- [ ] `docs/unified-parameter-reference.md` (env/TF/Helm cross-reference row)
- [ ] `registry/api/config_routes.py` (expose under an appropriate config group, near line 321; mark non-secret)

## New Dependencies

This change uses only existing dependencies. `ipaddress`, `socket`, and `urllib.parse` are standard library and already imported by the current guard; `httpx` is already a dependency.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create the shared SSRF utility

**File:** `registry/utils/ssrf.py` (new file)

Move the four symbols verbatim from `skill_service.py` (lines 71-192). Rename the public entry point to `is_safe_url` (no leading underscore, since it is now a shared public utility) and keep an `_is_safe_url` alias for backwards compatibility. Extend `_trusted_domains()` to merge `settings.ssrf_allowed_hosts`.

```python
"""Shared SSRF protection utilities for outbound HTTP fetches.

Promoted from registry.services.skill_service so the agent-card fetch and
health-check paths can reuse the same private-IP / cloud-metadata guard.
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

from ..core.config import settings

logger = logging.getLogger(__name__)

# Built-in trusted domains that skip IP validation (SSRF allowlist).
_DEFAULT_TRUSTED_DOMAINS: frozenset = frozenset(
    {"github.com", "gitlab.com", "raw.githubusercontent.com", "bitbucket.org"}
)


@lru_cache(maxsize=1)
def _trusted_domains() -> frozenset[str]:
    """Return the SSRF allowlist: defaults + GHES hosts + explicit SSRF hosts.

    Merges settings.github_extra_hosts (GHES hosts, also used for auth-header
    injection) and settings.ssrf_allowed_hosts (internal MCP servers/agents).
    Cached because settings are immutable per-process; tests must call
    _trusted_domains.cache_clear() after patching settings.
    """

    def _parse(raw: str | None) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in (raw or "").split(",") if h.strip())

    return (
        _DEFAULT_TRUSTED_DOMAINS
        | _parse(settings.github_extra_hosts)
        | _parse(settings.ssrf_allowed_hosts)
    )


def _is_private_ip(ip_str: str) -> bool:
    """True if the IP is private/loopback/link-local/reserved/metadata; fail-closed on parse error.

    Unwraps IPv4-mapped IPv6 (::ffff:a.b.c.d) before classifying so a dual-stack
    getaddrinfo result cannot smuggle a private/metadata IPv4 past the checks.
    Blocks the whole 169.254.0.0/16 range, which covers both the EC2 IMDS
    address (169.254.169.254) and the Fargate task-role credentials endpoint
    (169.254.170.2), instead of a brittle string compare.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        # Unwrap IPv4-mapped IPv6 so ::ffff:169.254.170.2 is treated as IPv4.
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
        if ip.is_unspecified:  # 0.0.0.0, ::
            return True
        # Cloud metadata / container-credentials range (defensive; already
        # covered by is_link_local for 169.254.0.0/16, kept explicit).
        if ip.version == 4 and ip in ipaddress.ip_network("169.254.0.0/16"):
            return True
        return False
    except ValueError:
        return True


def is_safe_url(url: str) -> bool:
    """Return True if the URL is safe to fetch (SSRF protection). Fail-closed.

    Validates http/https scheme, requires a hostname, allows trusted hosts to
    skip DNS, resolves the hostname, and rejects any resolved private IP.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"SSRF protection: Blocked URL with scheme '{parsed.scheme}'")
            return False
        hostname = parsed.hostname
        if not hostname:
            logger.warning("SSRF protection: URL has no hostname")
            return False
        if hostname.lower() in _trusted_domains():
            logger.debug(f"SSRF protection: Trusted domain '{hostname.lower()}'")
            return True
        try:
            addr_info = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as e:
            logger.warning(f"SSRF protection: Failed to resolve hostname '{hostname}': {e}")
            return False
        for *_ignored, sockaddr in addr_info:
            if _is_private_ip(sockaddr[0]):
                logger.warning(
                    f"SSRF protection: Blocked URL resolving to private IP "
                    f"'{sockaddr[0]}' for hostname '{hostname}'"
                )
                return False
        return True
    except Exception as e:
        logger.warning(f"SSRF protection: Error validating URL: {e}")
        return False


# Backwards-compatible alias: skill_service and its tests reference _is_safe_url.
_is_safe_url = is_safe_url
```

#### Step 2: Re-export from `skill_service.py` (backwards compatibility)

**File:** `registry/services/skill_service.py`
**Lines:** replace the definitions at 67-192 with an import.

```python
from ..utils.ssrf import (  # noqa: F401  (re-exported for existing call sites/tests)
    _DEFAULT_TRUSTED_DOMAINS,
    _is_private_ip,
    _is_safe_url,
    _trusted_domains,
)
```

This keeps every existing call site (`_is_safe_url(...)` at lines 595, 616, 681, 707, 866, 896, 1042, 1071) and the eight test files that patch `registry.services.skill_service._is_safe_url` directly (a name that still exists as a re-export) working unchanged. Remove the now-duplicated `ipaddress`/`socket` imports only if they are unused elsewhere in the module (verify before deleting).

**Required test migration (do not skip).** `tests/unit/services/test_skill_service_ssrf_allowlist.py` patches `registry.services.skill_service.settings` (11 tests) and `registry.services.skill_service.socket.getaddrinfo`. After the move, `is_safe_url`/`_trusted_domains` execute inside `registry/utils/ssrf.py` and resolve the `settings` global from the `ssrf` module namespace, so the `settings` patches become no-ops (the `getaddrinfo` patches happen to survive because `skill_service.socket` and `ssrf.socket` are the same module object, but do not rely on that). This file MUST be updated to patch `registry.utils.ssrf.settings` and clear `registry.utils.ssrf._trusted_domains.cache_clear()`. Because the behavior under test is unchanged, this is a mechanical repoint of the patch target - but it is a required change, not "tests pass unchanged." Update the acceptance criterion accordingly: "existing skill SSRF *behavior* is unchanged; the allowlist test's patch targets are repointed to `registry.utils.ssrf`." New tests for the shared util also target `registry.utils.ssrf`.

#### Step 3: No new exception (both hardened paths are non-raising)

An earlier draft added a `UrlValidationError` to `registry/exceptions.py`. It is **dropped** as dead code (YAGNI): both hardened paths are deliberately non-raising - the agent-card probe returns `(False, reason)` (unreachable) and the health path returns an `unhealthy` status. The existing skill path already has its own raising types (`SkillUrlValidationError`, `SkillContentSSRFError`) and does not need a shared one. If a future raising call site appears, add the exception then. This keeps the change surface minimal.

#### Step 4: Harden the agent-card reachability probe

**File:** `registry/utils/agent_validator.py`
**Lines:** import near top; guard inside `_check_endpoint_reachability` (196-230).

```python
from .ssrf import is_safe_url  # near existing imports

def _check_endpoint_reachability(url: str) -> tuple[bool, str | None]:
    """Check if agent endpoint is reachable (SSRF-guarded, non-blocking)."""
    if not is_safe_url(url):
        logger.warning(f"SSRF protection: refusing to probe agent endpoint '{url}'")
        return (False, "URL blocked by SSRF protection")
    try:
        well_known_url = f"{url}/.well-known/agent-card.json"
        response = httpx.get(well_known_url, timeout=5.0, follow_redirects=False)
        if response.status_code == 200:
            return (True, None)
        # If a redirect was returned, do not follow it blindly.
        if response.is_redirect:
            return (False, "Endpoint returned a redirect (not followed for SSRF safety)")
        return (False, f"Endpoint returned status {response.status_code}")
    except httpx.TimeoutException:
        logger.warning(f"Endpoint timeout for {url}")
        return (False, "Endpoint request timed out")
    except Exception as e:
        logger.warning(f"Could not reach endpoint {url}: {e}")
        return (False, str(e))
```

Note: we validate `url` (the base agent URL) rather than the derived `well_known_url`; since we append only a fixed path to the same host, host-level validation is sufficient, and `follow_redirects=False` prevents cross-host redirection. Behavior is backwards-compatible for public agent URLs.

#### Step 5: Harden the agent health endpoint

**File:** `registry/api/agent_routes.py`
**Lines:** in `check_agent_health` (883-998), before `_build_agent_health_urls` is used (~line 920), and set `follow_redirects=False` on the GET/HEAD clients (~lines 934, 965).

```python
import asyncio
from ..utils.ssrf import is_safe_url  # near existing imports (line ~17)

# inside check_agent_health, after base_url is computed (~line 920):
base_url = str(agent_card.url).rstrip("/")
if not await asyncio.to_thread(is_safe_url, base_url):
    logger.warning(f"SSRF protection: blocked agent health check for '{base_url}'")
    # Emit the SAME status value the server-health path uses so the frontend
    # has one contract to normalize (see Step 6 / review feedback).
    return {
        "status": HealthStatus.UNHEALTHY_SSRF_BLOCKED,
        "url": base_url,
        "last_checked_iso": _now_iso(),  # match existing return keys
    }
health_urls = _build_agent_health_urls(base_url)
```

Match the existing response shape of `check_agent_health` (inspect the current return dict keys and mirror them exactly; the snippet above is illustrative). **Return-shape consistency:** the original draft used a separate `detail: "blocked-by-ssrf-protection"` slug here, which diverges from the server-health enum and is dropped by the frontend (`AgentCard.tsx` reads only `status`). Use the shared `HealthStatus.UNHEALTHY_SSRF_BLOCKED` value in the `status` field so both paths present one contract.

**Redirects on this path:** the existing GET (line 936) and HEAD (line 966) do **not** set `follow_redirects`, so httpx already defaults to `False` here - no change is needed (contrary to an earlier draft that said to flip True->False; that was inaccurate for `agent_routes.py`). `_build_agent_health_urls` only appends `/.well-known/agent-card.json` to the same host, so validating `base_url` covers both URLs. The `await asyncio.to_thread(...)` wrapper keeps the blocking DNS lookup off the event loop.

#### Step 6: Harden the server health-check path

**File:** `registry/health/service.py`
**Lines:** guard at the top of `_check_server_endpoint_transport_aware` (674-683) and set `follow_redirects=False` on the outbound calls (598, 656, 711, 738, 808, 872, 911, 934).

```python
import asyncio
from ..utils.ssrf import is_safe_url  # top-level import

async def _check_server_endpoint_transport_aware(
    self, client, proxy_pass_url, server_info
) -> tuple[bool, str]:
    if not proxy_pass_url:
        return False, HealthStatus.UNHEALTHY_MISSING_PROXY_URL
    # Run the blocking getaddrinfo off the event loop so a slow/hostile
    # resolver cannot stall the whole batched health-check cycle.
    if not await asyncio.to_thread(is_safe_url, proxy_pass_url):
        logger.warning(
            f"SSRF protection: blocked health check for proxy_pass_url '{proxy_pass_url}'"
        )
        return False, HealthStatus.UNHEALTHY_SSRF_BLOCKED
    ...  # existing logic, with the redirect handling described below
```

Add a new status constant `UNHEALTHY_SSRF_BLOCKED` (e.g. `"unhealthy: blocked by SSRF protection"`) to the `HealthStatus` enum/class in `registry/constants.py`, following the existing `UNHEALTHY_MISSING_PROXY_URL` pattern.

**Off-the-event-loop requirement.** `is_safe_url` calls blocking `socket.getaddrinfo`. On the async paths (`_check_server_endpoint_transport_aware`, `perform_immediate_health_check`, and `check_agent_health` in Step 5) it MUST be invoked via `await asyncio.to_thread(is_safe_url, url)` so a slow resolver does not block the event loop for the entire batch of 10. This supersedes the earlier "keep it synchronous for parity" stance; the skill path is a single per-registration fetch, but the health path is a periodic fleet-wide loop.

**Coverage.** Validating `proxy_pass_url` at this single choke point covers the periodic loop (`_check_single_service` -> here) and the immediate check (`perform_immediate_health_check` -> here). Also add the same guard at the top of `perform_immediate_health_check` (1195-1305) and confirm `_try_ping_without_auth` (line 627, its POST at 650 with `follow_redirects=True`) is only reached *after* this validation. `_update_tools_background` (line 1072/1091) opens a *separate* connection to `proxy_pass_url` via `mcp_client_service.get_mcp_connection_result` after a healthy result; that connection is only transitively protected (it trusts the earlier check) and re-resolves independently, leaving a residual rebinding window - see the DNS-rebinding note below. `mcp_endpoint`/`sse_endpoint` derive from the same host as `proxy_pass_url` via `endpoint_utils.get_endpoint_url`, so host-level validation of `proxy_pass_url` covers them provided redirects are handled at every site.

**Redirect handling (backwards-compat sensitive).** Eight outbound calls in `health/service.py` set `follow_redirects=True` (lines 597, 655, 711, 737, 808, 872, 911, 934), plus the `_try_ping_without_auth` POST at 650. A blanket flip to `False` is unsafe: MCP/Starlette servers commonly emit a 307 `/mcp` -> `/mcp/` (trailing slash) and http->https redirects, so flipping would mark currently-healthy servers `unhealthy` on upgrade. Instead, prefer **same-host redirect re-validation**: keep `follow_redirects=True` but pass an httpx event hook / re-check that re-runs `await asyncio.to_thread(is_safe_url, str(response.url))` on the final URL and rejects if the final host differs from the validated host or fails validation (mirroring the SKILL.md `final_url != original` pattern at skill_service.py:616). If per-hop re-validation is deemed too invasive for this change, gate the `follow_redirects=False` behavior behind a setting (default keeping current behavior) and roll it out in monitor-only mode first (see [Rollout Plan](#rollout-plan)). Do not assume "no server redirects."

#### Step 7: Add the config field and wire deployment surfaces

Add `ssrf_allowed_hosts` to `Settings` (see [Configuration Parameters](#configuration-parameters)) and tick off the Deployment Surface Checklist.

### Error Handling

- `is_safe_url` is fail-closed: any parse/resolution error returns `False`, so a malformed or unresolvable URL is treated as unsafe.
- The agent-card path returns `(False, reason)` (non-blocking) - it never raises, preserving current registration semantics.
- The health path returns an `unhealthy` status - it never raises to the scheduler or API caller.
- Blocked URLs are logged at `WARNING`; allowlist bypasses at `DEBUG`.

### DNS Rebinding / TOCTOU (residual risk and mitigation)

`is_safe_url` resolves the hostname with `getaddrinfo`, but `httpx` then opens its own connection and re-resolves the hostname independently. An attacker who controls a low-TTL domain can return a public IP during validation and rebind to a private/metadata address for the actual fetch. `follow_redirects=False` does **not** close this window; it is within a single request.

Two options, in order of preference:

1. **Resolve-once-and-pin (recommended, closes the gap).** Resolve the hostname once, validate *every* returned A/AAAA record with `_is_private_ip`, pick one validated IP, and connect to that IP while preserving the original hostname in the `Host` header and TLS SNI. In httpx this is done with a small custom transport (or by passing the resolved IP as the URL host and setting `headers={"Host": hostname}` for plaintext; for TLS, use an `AsyncHTTPTransport` with `server_hostname` preserved). No new dependency is required. This also removes the double-DNS cost.
2. **Document as accepted residual risk + compensating controls (minimum bar).** If (1) is out of scope for this change, explicitly record that the resolve-then-fetch guard does not stop rebinding, and require the infra-level compensating controls in [Rollout Plan](#rollout-plan): enforce **IMDSv2** (tokens required, hop limit 1) on the ECS Fargate task so a simple GET-based SSRF cannot read credentials, and restrict egress security groups. Blocking `169.254.0.0/16` still covers the Fargate credentials endpoint `169.254.170.2` for the non-rebinding case.

This design specifies option 1 for the primary fetch path where practical and mandates the option-2 controls regardless, because the health path injects credentials before the request, so any residual bypass is credential exfiltration, not just recon.

### Logging

- `WARNING` on every blocked outbound URL, including the offending URL and the reason (private IP, scheme, resolution failure). Do not log credentials or headers.
- `DEBUG` for trusted-domain bypasses.
- Follow the repo format: `logger = logging.getLogger(__name__)`.

## Observability

### Tracing / Metrics / Logging Points

- **Log event (WARNING):** `SSRF protection: blocked <path> for '<url>'` at each of the three sinks. These become greppable audit signals and support a CloudWatch metric filter on `SSRF protection: blocked`.
- **Metric (in scope for the staged rollout):** a counter `ssrf_blocked_total{path="agent_card|agent_health|server_health", reason="blocked-private|blocked-metadata|blocked-scheme|resolve-failed"}`. This is required (not optional) because `SSRF_ENFORCE=false` monitor-only mode depends on it to size blast radius, and because an on-call needs to distinguish `blocked-metadata`/`blocked-link-local` (likely attack) from `blocked-private` (likely a missing allowlist entry). Emit it from the shared guard or its call sites via the existing metrics-service pattern.
- **Health status surface (corrected):** `UNHEALTHY_SSRF_BLOCKED` collapses to the generic "Unhealthy" bucket in the current frontend (`healthStatus.ts`/`ServerStatsContext.tsx` normalize by prefix/substring), so operators do **not** get a distinct dashboard signal today - the earlier "visibility without reading logs" claim was inaccurate. Either surface the raw reason as a tooltip on the status indicator (small frontend follow-up recommended by review) or rely on the log/metric signals above. The status value is still safe to add: it renders without error.

## Scaling Considerations

- **Current load:** health checks run every `health_check_interval_seconds` (default 300s) in batches of 10; agent-card probes run once per registration. Validation adds at most one `socket.getaddrinfo` per non-allowlisted host per check.
- **DNS cost:** for large fleets (100+ servers) this is one extra resolution per server per cycle. `getaddrinfo` results are typically OS-cached; impact is small relative to the 2s per-check timeout. If it proves material, an implementer can add a short-TTL LRU around resolution - explicitly out of scope now.
- **Event-loop blocking (async paths):** `socket.getaddrinfo` is blocking. Unlike the skill path (a single per-registration fetch), the health path is a periodic fleet-wide loop batched by 10, so a slow or hostile resolver could stall the whole cycle. This design therefore invokes the guard via `await asyncio.to_thread(is_safe_url, url)` on all async sinks (`check_agent_health`, `_check_server_endpoint_transport_aware`, `perform_immediate_health_check`) - see Steps 5 and 6. The sync agent-card probe calls it directly.
- **Allowlist caching:** `_trusted_domains()` is `@lru_cache`d, so allowlist derivation is O(1) after first call.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/ssrf.py` | Shared SSRF guard (`is_safe_url`, `_is_private_ip`, `_trusted_domains`, `_DEFAULT_TRUSTED_DOMAINS`) |
| `tests/unit/utils/test_ssrf.py` | Unit tests for the shared guard (scheme, hostname, private IP, metadata, allowlist merge) |
| `tests/unit/utils/test_agent_validator_ssrf.py` | Unit tests for the agent-card probe guard |
| `tests/unit/health/test_health_service_ssrf.py` | Unit tests for the health-path guard |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/services/skill_service.py` | ~-120 net | Replace guard definitions (71-192) with a re-export import from `registry.utils.ssrf` |
| `registry/utils/agent_validator.py` | ~10 | Import `is_safe_url`; pre-check in `_check_endpoint_reachability`; `follow_redirects=False` |
| `registry/api/agent_routes.py` | ~12 | Import `is_safe_url`; pre-check in `check_agent_health`; `follow_redirects=False` on GET/HEAD |
| `registry/health/service.py` | ~15 | Import `is_safe_url`; pre-check in `_check_server_endpoint_transport_aware` and `perform_immediate_health_check`; `follow_redirects=False` on outbound calls |
| `registry/core/config.py` | ~10 | Add `ssrf_allowed_hosts` field |
| `registry/constants.py` | ~2 | Add `HealthStatus.UNHEALTHY_SSRF_BLOCKED` |
| `tests/unit/services/test_skill_service_ssrf_allowlist.py` | ~15 | Repoint `settings`/`getaddrinfo`/`cache_clear` patch targets to `registry.utils.ssrf` (behavior unchanged) |
| `registry/api/config_routes.py` | ~1 | Expose `ssrf_allowed_hosts` (non-secret) in config listing |
| `.env.example` | ~6 | Documented `SSRF_ALLOWED_HOSTS` block |
| `docker-compose.yml` / `.podman.yml` / `.prebuilt.yml` | ~1 each | Pass through `SSRF_ALLOWED_HOSTS` |
| `terraform/aws-ecs/**` (4 files) | ~4 | Var declarations + env injection + wiring |
| `charts/mcpgw/**` (3 files) | ~4 | Value default + env mapping + reserved-name entry |
| `docs/configuration.md`, `docs/unified-parameter-reference.md` | ~6 | Document the new var |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (ssrf.py, config, exception, constant, wiring) | ~180 |
| New tests | ~220 |
| Modified code (call-site guards, re-export) | ~60 |
| **Total** | **~460** |

## Testing Strategy

See `./testing.md` for the full plan. In summary:

- Unit tests for `registry/utils/ssrf.py` mirroring `test_skill_service_ssrf_allowlist.py` (cache clear, settings patch, `getaddrinfo` mock), including the new `ssrf_allowed_hosts` merge.
- Unit tests for the agent-card probe: blocked URL returns `(False, "URL blocked by SSRF protection")` and makes no HTTP call; safe URL proceeds.
- Unit tests for the health path: blocked `proxy_pass_url` returns `UNHEALTHY_SSRF_BLOCKED` and makes no HTTP call; `follow_redirects=False` asserted.
- Backwards-compat: all existing skill SSRF tests pass unchanged (re-export keeps symbol paths valid).
- Functional curl tests against `POST /agents/register` and `POST /agents/{path}/health` with internal URLs.

## Alternatives Considered

### Alternative 1: Duplicate the guard into each module
**Description:** Copy `_is_safe_url` into `agent_validator.py` and `health/service.py`.
**Pros:** No cross-module import; each path self-contained.
**Cons:** Three copies drift over time; a fix in one is missed in others; violates DRY and the task's explicit "promote to shared utility" scope.
**Why Rejected:** Directly contradicts the requirement and creates a maintenance/security hazard.

### Alternative 2: Reuse `github_extra_hosts` as the only allowlist
**Description:** Do not add `ssrf_allowed_hosts`; trust internal MCP hosts via `github_extra_hosts`.
**Pros:** One fewer setting.
**Cons:** `github_extra_hosts` also injects GitHub auth headers; trusting an internal MCP server there would leak GitHub credentials to it. Conflates two distinct trust decisions.
**Why Rejected:** Security regression. A dedicated setting keeps trust boundaries separate.

### Alternative 3: Enforce SSRF validation at the registration schema layer
**Description:** Reject internal URLs when the agent/server is registered.
**Pros:** Fails fast; no unsafe URL ever stored.
**Cons:** Breaks legitimate local-runtime and internal-MCP-server use cases; not backwards-compatible; DNS can change after registration (TOCTOU) so fetch-time validation is still required.
**Why Rejected:** Not backwards-compatible and insufficient on its own; validation belongs at the outbound-fetch boundary.

### Alternative 4: Network-layer egress control only
**Description:** Rely on ECS security groups / egress firewall to block internal traffic.
**Pros:** Defense in depth; catches all paths.
**Cons:** Out of the application's control; does not stop metadata access from the task itself in all configs; the task explicitly asks for application-level validation.
**Why Rejected:** Complementary, not a substitute; out of scope for this change.

### Comparison Matrix

| Criteria | Chosen (shared util + dedicated allowlist) | Alt 1 (duplicate) | Alt 2 (reuse github hosts) | Alt 3 (schema-time) |
|----------|---------|-------|-------|-------|
| Complexity | Low | Low | Low | Medium |
| Maintainability | High | Low | Medium | Medium |
| Security | High | Medium | Low | Medium |
| Backwards-compatible | Yes | Yes | Yes | No |
| Matches task scope | Yes | No | Partial | No |

## Rollout Plan

Because the default (`SSRF_ALLOWED_HOSTS=""`) blocks all private/internal addresses, fleets that legitimately run internal MCP servers would see those servers flip to `UNHEALTHY_SSRF_BLOCKED` on the first deploy. To keep the change genuinely backwards-compatible, enforcement is staged.

- **Phase 1 - Implementation (out of scope for this skill):** create `ssrf.py`, re-export + migrate the allowlist test, apply guards (with `asyncio.to_thread`), add config + `SSRF_ENFORCE` flag, add the `ssrf_blocked_total{path,reason}` metric, wire surfaces, add tests.
- **Phase 2 - Testing:** run `uv run pytest tests/`; verify existing skill SSRF behavior is green (with repointed patch targets); run new unit + functional tests from `testing.md`, including the IPv6/mapped/encoding vectors.
- **Phase 3 - Monitor-only rollout:** deploy with `SSRF_ENFORCE=false` (log/metric the block decision but still perform the request). Run for at least one full `health_check_interval_seconds` cycle across the fleet. Inventory registered `proxy_pass_url`/agent URLs that resolve to private space and pre-seed `SSRF_ALLOWED_HOSTS` for the legitimate internal ones. Do **not** flip `follow_redirects` in this phase.
- **Phase 4 - Enforce:** set `SSRF_ENFORCE=true`. Monitor `ssrf_blocked_total` and the `UNHEALTHY_SSRF_BLOCKED` status; a spike in `reason=blocked-private` indicates a missing allowlist entry, `reason=blocked-metadata` indicates likely attack traffic.
- **Companion infra controls (required, tracked separately):** enforce IMDSv2 on the ECS Fargate task and tighten egress security groups. These defend the metadata/credentials path even against DNS-rebinding bypass of the app-layer guard.
- **Config-change note:** `_trusted_domains()` is `@lru_cache`d, so changing `SSRF_ALLOWED_HOSTS` requires an ECS task restart (rolling deploy). Add this to the operator runbook.

## Open Questions

- Should the primary fetch adopt full resolve-once-and-pin (Option 1 in [DNS Rebinding](#dns-rebinding--toctou-residual-risk-and-mitigation)) in this change, or ship the guard + IMDSv2/egress compensating controls and pin in a follow-up? Recommendation: pin the agent-card sync fetch now (cheap) and stage pinning for the health cluster.
- Should the health-path redirect handling be same-host re-validation (keep `follow_redirects=True`, re-check the final URL) or a settings-gated `follow_redirects=False`? The design leans to same-host re-validation to avoid the 307 trailing-slash regression; confirm with an inventory of servers that redirect on `/mcp`.
- Should `perform_immediate_health_check` surface the blocked status to the API caller distinctly (e.g. HTTP 422) or only via the health status field? Current design keeps it in the status field to remain non-raising and backwards-compatible.
- Should the UI render the `UNHEALTHY_SSRF_BLOCKED` reason (tooltip on the status dot) rather than collapsing it to a generic "Unhealthy"? Frontend review recommends yes; tracked as a small follow-up.

## References

- Existing guard: `registry/services/skill_service.py:71-192`
- Existing allowlist tests: `tests/unit/services/test_skill_service_ssrf_allowlist.py`
- Config pattern: `registry/core/config.py:53-60, 292-299`
- OWASP SSRF Prevention Cheat Sheet (deny private ranges, validate post-redirect, allowlist)
- Reference issue: https://github.com/agentic-community/mcp-gateway-registry/issues/1282
