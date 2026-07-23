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

## Overview

### Problem Statement

The registry (`registry/` FastAPI app, tag 1.24.4) issues outbound HTTP requests to URLs that are ultimately supplied by whoever registers a server or agent. Two of these paths make outbound requests with **no SSRF validation whatsoever**:

1. **Agent-card fetch** - `registry/utils/agent_validator.py::_check_endpoint_reachability()` issues a synchronous `httpx.get(f"{url}/.well-known/agent-card.json", timeout=5.0)` during `POST /agents/register` (called from `validate_agent_card(check_reachability=True)` -> `AgentValidator.validate_agent_card(verify_endpoint=True)` -> `agent_routes.py` line 577-580). The `url` is `AgentRegistrationRequest.url`, fully user-controlled, freshly submitted with every registration call - the highest-value SSRF target in the codebase, since a caller gets to point it anywhere on every request.
2. **Health checks** - `registry/health/service.py` performs outbound `GET`/`POST`/`HEAD` requests against `server_info["proxy_pass_url"]` (and derived MCP/SSE endpoint URLs) from both a background `asyncio` loop (`_run_health_checks`, every `settings.health_check_interval_seconds`, default 300s) and an on-demand path (`perform_immediate_health_check`, invoked from server enable/toggle flows). All of these calls set `follow_redirects=True` with zero pre-flight or post-redirect validation. A separate agent-specific health endpoint, `POST /agents/{path}/health` in `registry/api/agent_routes.py` (`_build_agent_health_urls` + the fetch loop at lines 920-983), has the exact same gap for the agent-card URL stored at registration time.

A working SSRF guard, `_is_safe_url()`, already exists - but it is private to `registry/services/skill_service.py` (lines 128-192) and used only for the SKILL.md-fetch feature (`_validate_skill_md_url`, `_parse_skill_md_content`, `_check_skill_health`, `_fetch_authenticated_content`). It denies non-HTTP(S) schemes, resolves the hostname via `socket.getaddrinfo()`, and rejects any resolved IP that is private/loopback/link-local/reserved or the literal cloud-metadata address `169.254.169.254`, unless the hostname is in a small allowlist (`_trusted_domains()`: `github.com`, `gitlab.com`, `raw.githubusercontent.com`, `bitbucket.org`, plus `settings.github_extra_hosts`). Every skill call site also re-validates `response.url` after redirects, closing the classic "safe URL redirects to metadata endpoint" bypass.

This design promotes that guard into a shared, general-purpose utility and applies it - with the same pre-flight-plus-post-redirect pattern - to the agent-card fetch and both health-check paths, while adding an operator-controlled allowlist so legitimate internal deployments are not broken.

### Goals
- Extract the private `_is_safe_url()` / `_is_private_ip()` / `_trusted_domains()` trio out of `skill_service.py` into a shared module, with no behavior change for the existing SKILL.md callers.
- Apply the shared guard, pre-flight and post-redirect, to:
  - `registry/utils/agent_validator.py::_check_endpoint_reachability()` (agent-card fetch at registration).
  - `registry/api/agent_routes.py`'s `/agents/{path}/health` endpoint (agent-card fetch at health-check time).
  - `registry/health/service.py`'s server health-check engine (`_check_server_endpoint_transport_aware` and its helpers `_initialize_mcp_session`, `_try_ping_without_auth`), covering both the background loop and the on-demand path.
- Add a general-purpose allowlist configuration (host/CIDR) that is independent of the skills-specific `github_extra_hosts` knob, so operators can permit intentional internal deployments without widening the GitHub-auth trust list.
- Keep the change backwards-compatible: no new required configuration, and no previously-working registration/health-check flow breaks for a deployment that does not opt into stricter settings.
- Return clear, actionable errors (not silent failures or generic 500s) when a URL is rejected.

### Non-Goals
- Changing the SKILL.md fetch call sites' behavior or their `github_extra_hosts`-based allowlist semantics (only their implementation moves to a shared module).
- Adding SSRF protection to `registration_webhook_url` / `registration_gate_url` (operator-configured, not attacker-controlled from a downstream team's perspective) - flagged as a follow-up, not in scope here.
- Adding SSRF protection to `proxy_pass_url` validation at server-registration time (a static field-format check, not an outbound fetch) - the *registration-time* MCP tool-discovery client (`registry/core/mcp_client.py`) and the *security scanner* (`registry/services/security_scanner.py`) are noted as follow-up candidates but are not modified by this design; scope is limited to the two paths named in the task (agent-card fetch, health checks).
- A UI for managing the allowlist.
- Rate limiting, abuse detection, or network-layer egress controls (VPC security groups, NACLs) - defense in depth, but out of scope for this application-layer change.
- Fixing the TOCTOU gap between the DNS resolution done inside the guard and the DNS resolution `httpx` performs when actually connecting (see Alternatives Considered - accepted as a known limitation shared with the existing SKILL.md guard, not newly introduced).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/skill_service.py` | SKILL.md fetch/parse/health service | Owns the existing `_is_safe_url()`, `_is_private_ip()`, `_trusted_domains()` guard (lines 60-192) to be promoted into a shared module |
| `registry/utils/agent_validator.py` | A2A agent-card structural + reachability validation | Contains `_check_endpoint_reachability()` (lines 196-230), the unguarded agent-card fetch at registration time |
| `registry/api/agent_routes.py` | Agent registration/update/health API routes | `register_agent()` (calls `verify_endpoint=True`, line 577-580); `_build_agent_health_urls()` (lines 186-205) and the `/agents/{path}/health` fetch loop (lines 920-983), the second unguarded agent-card fetch path |
| `registry/health/service.py` | `HealthMonitoringService` - background + on-demand MCP server health checks | `_check_server_endpoint_transport_aware()` (lines 674-957) and helpers `_initialize_mcp_session`, `_try_ping_without_auth` - all outbound calls use `follow_redirects=True` with no validation |
| `registry/core/config.py` | Pydantic Settings (`BaseSettings`) | Existing `github_extra_hosts` field (lines 292-299) is the only precedent for an SSRF-allowlist config knob; new settings are added alongside it |
| `registry/exceptions.py` | Domain exception hierarchy | `SkillUrlValidationError`, `SkillContentSSRFError` (lines 58-68, 194-205) are the pattern to mirror for new exception types |
| `registry/schemas/agent_models.py` | Agent Pydantic models | `_validate_url_format()` (lines 120-153) is a syntax-only check on `AgentCard.url`/`AgentRegistrationRequest.url` - no SSRF awareness, left as-is (format check happens before the new safety check) |
| `tests/unit/services/test_skill_service_ssrf_allowlist.py` | Existing SSRF unit tests | Demonstrates the mocking pattern (`patch(".socket.getaddrinfo")`, `_trusted_domains.cache_clear()`) to reuse for the new call sites |

### Existing Patterns Identified

1. **Pre-flight + post-redirect validation**: every SKILL.md call site calls `_is_safe_url(url)` before dispatching the request, sets `follow_redirects=True` on the `httpx` call, then re-checks `_is_safe_url(str(response.url))` after the response comes back, blocking if the final URL (post-redirect) is unsafe. Files: `skill_service.py` lines 595/616, 681/707, 866/896, 1042/1071. A future implementer must replicate this exact two-phase shape at every new call site rather than only doing the pre-flight check, or a redirect-based bypass remains open.
2. **`lru_cache`-backed allowlist**: `_trusted_domains()` is decorated `@lru_cache(maxsize=1)` because `settings` is immutable per-process; tests explicitly call a cache-clear helper between cases. Any new allowlist function must follow the same caching + test-clearing convention.
3. **Domain exception + route-level HTTPException translation**: `skill_routes.py` catches `SkillContentSSRFError`/`SkillUrlValidationError` and translates to `HTTPException(status_code=400, ...)` (`skill_routes.py` lines 574-588). New SSRF rejections at the agent/health paths should raise a comparable typed exception and be translated the same way, rather than ad hoc inline `HTTPException` raises.
4. **`"SSRF protection: ..."` log prefix**: every log line inside `_is_safe_url()`/`_is_private_ip()` is prefixed exactly `"SSRF protection: "` at `WARNING` level. New call sites must keep this prefix verbatim so existing log-based alerting/searching continues to work.
5. **`logging.basicConfig` at module import**: `agent_validator.py`, `agent_routes.py`, and `health/service.py` (implicitly, via `logging.getLogger(__name__)`, though `health/service.py` does not call `basicConfig` itself - it is configured elsewhere in `registry/main.py`) all use `logger = logging.getLogger(__name__)`. The new shared module follows the same `basicConfig` + `getLogger(__name__)` pattern used in `skill_service.py`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/utils/ssrf_protection.py` (new) | Extends | Receives the promoted `_is_safe_url`, `_is_private_ip`, `_trusted_domains` logic, generalized to accept an additional operator-configured allowlist independent of `github_extra_hosts` |
| `registry/services/skill_service.py` | Uses | Its three private functions are removed and replaced with `from ..utils.ssrf_protection import is_safe_url` (public name, no underscore, since it now has multiple external callers); all five existing call sites swap to the shared import with no behavior change |
| `registry/utils/agent_validator.py` | Uses | `_check_endpoint_reachability()` gains a pre-flight `is_safe_url()` check before its `httpx.get()`, and a post-redirect check on `response.url` |
| `registry/api/agent_routes.py` | Uses | The `/agents/{path}/health` fetch loop (lines 920-983) gains the same pre-flight + post-redirect checks per URL attempted in `_build_agent_health_urls()` |
| `registry/health/service.py` | Uses | `_check_server_endpoint_transport_aware()` and its two helper methods (`_initialize_mcp_session`, `_try_ping_without_auth`) each gain a pre-flight check on the endpoint URL before their `client.get`/`client.post` calls, plus a post-redirect check on `response.url` |
| `registry/core/config.py` | Extends | New `Settings` fields: `ssrf_allowed_hosts` (comma-separated hostnames) and `ssrf_allowed_cidrs` (comma-separated CIDR blocks), independent of `github_extra_hosts` |
| `registry/exceptions.py` | Extends | New `UnsafeUrlError(RegistryError)` exception, raised by the shared guard's calling code (not by the guard function itself, which stays a boolean predicate matching the existing `_is_safe_url` contract) |

### Constraints and Limitations Discovered

- **`_check_endpoint_reachability()` uses a synchronous `httpx.get()`** inside code reachable from an `async def` FastAPI route (`register_agent`). This blocks the event loop for up to 5 seconds per registration call. This is a pre-existing issue unrelated to SSRF; the design does not fix it (out of scope) but flags it in Alternatives Considered since the natural point to touch this function is also a natural point to fix it, and a reviewer may ask why it was left as-is.
- **`health/service.py` sets `follow_redirects=True` on every outbound call with no existing post-redirect check.** This is different from `skill_service.py`, which already had post-redirect logic to build on. The new code must add the check net-new here, not just relocate existing logic.
- **DNS-rebinding TOCTOU**: `_is_safe_url()` resolves DNS once via `socket.getaddrinfo()`, then the actual `httpx` client performs its own independent DNS resolution when connecting. Between those two resolutions, a malicious DNS server could return a different (private) IP for the second lookup. This gap exists today in the SKILL.md path and is not fixed by this design (see Alternatives Considered) - flagged here since it is a limitation an implementer should be aware of, not a defect they introduced.
- **Python 3.14, `httpx>=0.27.0`** are the only relevant runtime constraints; no new third-party dependency is available or needed - `ipaddress` and `socket` are stdlib and already used.
- **`ruff`/`bandit` pre-commit hooks are enforced locally** (not CI-blocking today per `continue-on-error: true` in `.github/workflows/registry-test.yml`, but required to pass in the local pre-commit hook) - new code must stay within the existing `ruff` rule set (`E, W, F, I, B, C4, UP`) and pass `bandit -c pyproject.toml`.

## Architecture

### System Context Diagram

```
                          +-------------------------------+
                          |   registry/utils/             |
                          |   ssrf_protection.py (NEW)     |
                          |                                |
                          |  is_safe_url(url) -> bool      |
                          |  _is_private_ip(ip) -> bool    |
                          |  _allowed_hosts() -> frozenset  |
                          |  _allowed_networks() -> tuple   |
                          +---------------+----------------+
                                          ^
              +---------------------------+---------------------------+
              |                           |                           |
   +----------+---------+     +-----------+----------+     +----------+-----------+
   | skill_service.py    |     | agent_validator.py    |     | health/service.py    |
   | (existing, relocated)|    | _check_endpoint_      |     | _check_server_       |
   | _validate_skill_md_  |    | reachability()        |     | endpoint_transport_   |
   | url, _parse_skill_   |    | (agent-card fetch at   |     | aware() + helpers      |
   | md_content,          |    | registration)          |     | (health checks, bg +   |
   | _check_skill_health, |    +-----------+------------+     | on-demand)             |
   | _fetch_authenticated_|                |                  +----------+-------------+
   | content              |                |                             |
   +----------------------+                |                             |
                                            v                             v
                              +----------------------------+  +----------------------------+
                              | agent_routes.py             |  | server_routes.py (toggle/  |
                              | POST /agents/register        |  | enable) + background       |
                              | POST /agents/{path}/health    |  | asyncio health-check loop  |
                              +------------------------------+  +----------------------------+
```

### Sequence Diagram - Agent-card fetch at registration (new behavior)

```
Client            agent_routes.py         agent_validator.py        ssrf_protection.py       httpx (network)
  |  POST /agents/register  |                    |                          |                       |
  |------------------------->|                    |                          |                       |
  |                          | validate_agent_card(verify_endpoint=True)     |                       |
  |                          |------------------->|                          |                       |
  |                          |                    | _check_endpoint_reachability(url)                |
  |                          |                    |------------------------->|                       |
  |                          |                    |   is_safe_url(url)?      |                       |
  |                          |                    |<-------------------------|                       |
  |                          |                    |  [unsafe] -> return (False, "blocked: unsafe URL")|
  |                          |                    |  [safe]  -> proceed                               |
  |                          |                    |------------------------------------------------->|
  |                          |                    |                          |    GET well-known URL |
  |                          |                    |<-------------------------------------------------|
  |                          |                    | is_safe_url(response.url) [post-redirect check]   |
  |                          |                    |------------------------->|                       |
  |                          |                    |  [unsafe] -> warning logged, reachable=False       |
  |                          |<-------------------|                          |                       |
  |<-------------------------|  (registration still succeeds; reachability is advisory, see below)    |
```

Note: `_check_endpoint_reachability()` already treats an unreachable endpoint as a **non-blocking warning**, not a hard registration failure (`validate_agent_card` appends to `warnings`, not `errors`). The new SSRF check preserves this: a URL that fails the safety check is reported the same way an unreachable URL is today - a warning on the registration response, not a 400. This keeps the change backwards-compatible for agents that were previously registered against internal URLs that happened to still return 200 (registration is not blocked outright; see Rollout Plan for the stricter opt-in mode).

### Sequence Diagram - MCP server health check (background loop, new behavior)

```
_run_health_checks (asyncio loop)      _check_single_service      ssrf_protection.py        httpx.AsyncClient
  | every health_check_interval_seconds |                          |                          |
  |------------------------------------->|                          |                          |
  |                                      | _check_server_endpoint_transport_aware(proxy_pass_url)|
  |                                      |------------------------->|                          |
  |                                      |  is_safe_url(proxy_pass_url)?                         |
  |                                      |<--------------------------                          |
  |                                      |  [unsafe] -> return (False, UNHEALTHY_UNSAFE_URL)     |
  |                                      |  [safe]  -> proceed to client.get/post(..., follow_redirects=True)
  |                                      |------------------------------------------------------->|
  |                                      |<-------------------------------------------------------|
  |                                      | is_safe_url(response.url) [post-redirect check]        |
  |                                      |------------------------->|                          |
  |                                      |  [unsafe] -> return (False, UNHEALTHY_UNSAFE_REDIRECT)|
  |                                      |  [safe]  -> continue existing status-code logic        |
```

### Component Diagram

```
+----------------------------------------------------------------+
| registry/utils/ssrf_protection.py                                |
|                                                                    |
|  is_safe_url(url: str) -> bool          [public, was _is_safe_url]|
|  _is_private_ip(ip_str: str) -> bool    [private, unchanged logic]|
|  _allowed_hosts() -> frozenset[str]     [private, @lru_cache]     |
|      = _DEFAULT_TRUSTED_DOMAINS | settings.github_extra_hosts     |
|        | settings.ssrf_allowed_hosts                              |
|  _allowed_networks() -> tuple[ip_network, ...] [private, @lru_cache]|
|      = parsed settings.ssrf_allowed_cidrs                         |
|  clear_ssrf_caches() -> None            [test-only helper]        |
+----------------------------------------------------------------+
```

## Data Models

This change is primarily behavioral (validation added to existing outbound-call sites); it does not introduce new Pydantic request/response models. It does add:

### New Exception

```python
class UnsafeUrlError(RegistryError):
    """Raised when a URL fails SSRF validation before an outbound fetch."""

    def __init__(
        self,
        url: str,
        reason: str,
    ):
        self.url = url
        self.reason = reason
        super().__init__(f"URL failed SSRF validation '{url}': {reason}")
```

Added to `registry/exceptions.py` directly below the existing `SkillContentSSRFError` (line 194-205), following the same constructor shape (`url`, `reason` -> formatted message). `SkillContentSSRFError` itself is left unchanged - it continues to be used by the skill fetch paths that already raise it; `UnsafeUrlError` is the general-purpose type used by the two new call sites (agent-card fetch, health checks) added in this design.

### Model Changes

No changes to `AgentCard`, `AgentRegistrationRequest`, `ServerInfo`, or `ServiceRegistrationRequest`. The design deliberately does not add a Pydantic validator that calls `is_safe_url()` on these models' URL fields at parse time, because:
- Pydantic validators run synchronously and would need to perform a blocking DNS resolution inside model construction, which happens in request-deserialization code paths not intended for I/O.
- The task scope is the *fetch* paths (agent-card fetch, health checks), not a blanket validation of every stored URL field at every touch point (see Non-Goals).

## API / CLI Design

No new endpoints or CLI commands are introduced. Existing endpoints change behavior as follows:

### `POST /agents/register` and `PUT /agents/{path}` (existing, behavior change only)

**Description:** When `verify_endpoint=True` reachability checking is performed (unconditionally today - `register_agent()` calls `validate_agent_card(agent_card, verify_endpoint=True)` at line 577-580), the endpoint URL is now validated for SSRF safety before the outbound GET, and the final URL is re-validated after any redirect.

**Behavior on unsafe URL:** identical HTTP status/response shape as today's "endpoint unreachable" case - a warning appended to `ValidationResult.warnings`, registration still succeeds (unless the new strict-mode setting described in Configuration is enabled). No response schema change.

**Error Cases:**
- No new error status codes for the default configuration. In strict mode (`ssrf_reject_unsafe_registration=True`, disabled by default), an unsafe agent URL causes `POST /agents/register` to return `400 Bad Request` with `detail` describing the rejection.

### `POST /agents/{path:path}/health` (existing, behavior change only)

**Description:** Each candidate URL produced by `_build_agent_health_urls()` (agent-card URL, then the raw registered URL) is now checked with `is_safe_url()` before being fetched; an unsafe URL is skipped (treated the same as a failed fetch attempt, falling through to the next candidate URL or to the final HEAD-based fallback) rather than being requested.

**Expected Response:** unchanged shape - `{"status": "healthy" | "unhealthy", "response_time_ms": ..., "checked_at": ...}` (existing fields). If all candidate URLs are unsafe, the endpoint returns the existing "unhealthy" response, now with `status_detail` reflecting `"unsafe_url"` rather than a generic connection failure.

**Error Cases:** No new HTTP-level errors; unsafe URLs surface as an unhealthy status, consistent with how unreachable URLs already surface today.

### Server health checks (`registry/health/service.py`, background loop + `perform_immediate_health_check`)

**Description:** `_check_server_endpoint_transport_aware()` and its two request-issuing helpers now validate `proxy_pass_url` / the resolved MCP/SSE endpoint URL before dispatching, and re-validate `response.url` after the call returns.

**Expected Response:** No API response shape change - health status is surfaced the same way today (`HealthStatus` enum value stored per server, broadcast over the existing WebSocket channel). A new enum member is added: `HealthStatus.UNHEALTHY_UNSAFE_URL` (see Data Models note above - this lives in `registry/constants.py`, not a new Pydantic model, so it is described here rather than in Data Models).

**Error Cases:** None new at the HTTP layer; unsafe URLs are reported as `unhealthy` with the new status detail, exactly like every other existing unhealthy reason (timeout, connection error, etc.).

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|--------------|
| `SSRF_ALLOWED_HOSTS` | str (comma-separated) | `""` | No | Extra hostnames that bypass the private-IP check for the agent-card fetch and health-check paths, independent of `GITHUB_EXTRA_HOSTS`. Use for internal MCP servers/agents intentionally deployed on private networks that the registry must be able to reach. |
| `SSRF_ALLOWED_CIDRS` | str (comma-separated) | `""` | No | Extra IP ranges (CIDR notation, e.g. `10.20.0.0/16`) that are exempted from the private-IP denial, for cases where allowlisting by hostname is impractical (e.g. many ephemeral internal hosts on a known subnet). Validated as network resolves before use are still performed - only the private-IP rejection is skipped for these ranges. |
| `SSRF_REJECT_UNSAFE_REGISTRATION` | bool | `false` | No | When `true`, `POST /agents/register` and `PUT /agents/{path}` return `400 Bad Request` if the agent URL fails the SSRF check, instead of the default backwards-compatible behavior of registering with a warning. Operators who want strict enforcement opt in explicitly; default preserves today's "warn but allow" behavior. |

None of these are required - the feature ships enabled by default (guard applied, deny-by-default for private/internal IPs) with empty allowlists, which is a strictly safer superset of today's behavior (today there is no guard at all on these two paths) and does not require any operator action to keep existing registrations/health checks of *public* URLs working.

### Settings / Config Class Updates

Added to `registry/core/config.py`, in the same settings block as `github_extra_hosts` (after line 299), so the three SSRF-related knobs stay visually grouped:

```python
ssrf_allowed_hosts: str = Field(
    default="",
    description=(
        "Comma-separated extra hostnames that bypass the private-IP SSRF check "
        "for agent-card fetch and health-check requests. Independent of "
        "github_extra_hosts (which is skill/GitHub-auth specific). Keep the list tight."
    ),
)
ssrf_allowed_cidrs: str = Field(
    default="",
    description=(
        "Comma-separated CIDR ranges (e.g. 10.20.0.0/16) exempted from the "
        "private-IP SSRF denial for agent-card fetch and health-check requests. "
        "Use when allowlisting by hostname is impractical. Keep the list tight."
    ),
)
ssrf_reject_unsafe_registration: bool = Field(
    default=False,
    description=(
        "If true, reject agent registration (400) when the agent URL fails SSRF "
        "validation, instead of registering with a warning (default, backwards compatible)."
    ),
)
```

### Deployment Surface Checklist

| Surface | File | Action |
|---------|------|--------|
| Environment template | `.env.example` | Add `SSRF_ALLOWED_HOSTS`, `SSRF_ALLOWED_CIDRS`, `SSRF_REJECT_UNSAFE_REGISTRATION` with the same comment-block style used for `GITHUB_EXTRA_HOSTS` (lines 613-623) |
| Docker Compose | `docker-compose.yml`, `docker-compose.prebuilt.yml`, `docker-compose.dhi.yml`, `docker-compose.podman.yml` | Add the three new env vars to the `registry` service's `environment:` block, unset/empty by default, matching how `GITHUB_EXTRA_HOSTS` is (or is not) already wired - confirm during implementation whether `GITHUB_EXTRA_HOSTS` is explicitly listed in these files or relies on `.env` pass-through, and follow the same convention |
| Terraform (ECS) | `terraform/` (AWS ECS stack) | Add optional Terraform variables mirroring however `github_extra_hosts`/similar optional string settings are surfaced today (as an ECS task-definition environment variable with an empty-string default) |
| Helm | `charts/` | Add the three keys to `values.yaml` under the registry deployment's environment section, empty-string/`false` defaults |
| Settings class | `registry/core/config.py` | As shown above |

## New Dependencies

This change uses only existing dependencies. `ipaddress` and `socket` are Python stdlib (already imported in `skill_service.py`); `httpx` is already a project dependency used by every call site this design touches. No new package is added to `pyproject.toml`.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create the shared SSRF utility module

**File:** `registry/utils/ssrf_protection.py`
**Lines:** new file

Move `_is_private_ip`, `_trusted_domains`, `_is_safe_url` out of `skill_service.py` verbatim, rename the allowlist function and public entry point, and generalize the allowlist to merge in the two new settings:

```python
"""Shared SSRF protection utility for outbound URL fetches.

Used by skill fetching, agent-card fetching, and MCP server / agent health
checks - anywhere the registry makes an outbound HTTP request to a
user-supplied or previously-registered URL.
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

from ..core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


CLOUD_METADATA_IP: str = "169.254.169.254"

_DEFAULT_TRUSTED_DOMAINS: frozenset = frozenset(
    {
        "github.com",
        "gitlab.com",
        "raw.githubusercontent.com",
        "bitbucket.org",
    }
)


@lru_cache(maxsize=1)
def _allowed_hosts() -> frozenset[str]:
    """Return the SSRF hostname allowlist: built-in defaults plus configured hosts.

    Merges settings.github_extra_hosts (skill/GitHub-auth specific) and
    settings.ssrf_allowed_hosts (general-purpose) so either config knob can
    grant a host bypass of the private-IP check. Cached per-process since
    settings are immutable at runtime.
    """
    github_hosts = settings.github_extra_hosts or ""
    ssrf_hosts = settings.ssrf_allowed_hosts or ""
    extra = frozenset(
        h.strip().lower()
        for raw in (github_hosts, ssrf_hosts)
        for h in raw.split(",")
        if h.strip()
    )
    return _DEFAULT_TRUSTED_DOMAINS | extra


@lru_cache(maxsize=1)
def _allowed_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Return configured CIDR ranges exempted from the private-IP denial."""
    raw = settings.ssrf_allowed_cidrs or ""
    networks = []
    for cidr in (c.strip() for c in raw.split(",") if c.strip()):
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning(f"SSRF protection: Ignoring invalid CIDR in ssrf_allowed_cidrs: '{cidr}'")
    return tuple(networks)


def clear_ssrf_caches() -> None:
    """Clear cached allowlist state. Test-only helper - settings do not change at runtime."""
    _allowed_hosts.cache_clear()
    _allowed_networks.cache_clear()


def _is_private_ip(
    ip_str: str,
) -> bool:
    """Check if an IP address is private, loopback, link-local, or otherwise denied.

    Args:
        ip_str: IP address string to check.

    Returns:
        True if the IP is private/loopback/link-local/reserved (and not covered
        by an explicit CIDR allowlist entry), False otherwise.
    """
    try:
        ip = ipaddress.ip_address(ip_str)

        is_denied = ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        is_denied = is_denied or ip_str == CLOUD_METADATA_IP

        if is_denied and any(ip in network for network in _allowed_networks()):
            return False

        return is_denied
    except ValueError:
        # Invalid IP address format - fail closed.
        return True


def is_safe_url(
    url: str,
) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).

    This function validates that a URL:
    1. Uses http or https scheme.
    2. Does not resolve to a private/loopback/link-local/reserved IP address,
       unless the hostname or resolved IP is explicitly allowlisted.
    3. Does not target the cloud metadata endpoint.

    Callers MUST additionally re-validate the final URL after following any
    redirects (e.g. response.url from httpx with follow_redirects=True) to
    close the "safe URL redirects to an unsafe target" bypass - this function
    only validates the URL it is given, not any subsequent redirect chain.

    Args:
        url: URL to validate.

    Returns:
        True if the URL is safe to fetch, False otherwise.
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

        hostname_lower = hostname.lower()
        if hostname_lower in _allowed_hosts():
            logger.debug(f"SSRF protection: Trusted domain '{hostname_lower}'")
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

Note the function is renamed `is_safe_url` (public, no leading underscore) since it now has callers outside its owning module - matching this codebase's convention that private/module-local helpers get the underscore prefix and shared utilities do not.

#### Step 2: Update `skill_service.py` to use the shared module

**File:** `registry/services/skill_service.py`
**Lines:** 60-192 (remove), 24 and import block (update), five call sites (update)

- Delete `_is_private_ip`, `_trusted_domains`, `_is_safe_url`, `_DEFAULT_TRUSTED_DOMAINS` (lines 67-192).
- Add `from ..utils.ssrf_protection import is_safe_url` to the import block (near line 45-49).
- Replace all five call sites (`_is_safe_url(...)` at former lines 595, 616, 681, 707, 866, 896, 1042, 1071) with `is_safe_url(...)`, no other change to surrounding logic.
- `URL_VALIDATION_TIMEOUT` and `MAX_GITLAB_TREE_PAGES` constants stay in `skill_service.py` - they are not part of the SSRF guard itself, just skill-fetch-specific timeouts.
- Update the two test files that patch `registry.services.skill_service._is_safe_url` / `.socket.getaddrinfo` / `._trusted_domains` (`tests/unit/test_skill_service_github_auth.py`, `tests/unit/test_skill_routes_github_auth.py`, `tests/unit/api/test_skill_inline_content.py`, `tests/unit/services/test_skill_service_ssrf_allowlist.py`) to patch `registry.utils.ssrf_protection.is_safe_url` / `.socket.getaddrinfo` / `._allowed_hosts` instead, and to call `clear_ssrf_caches()` instead of directly clearing `_trusted_domains.cache_clear()`. This is a mechanical rename with no behavior change - see Testing Strategy.

#### Step 3: Add the `UnsafeUrlError` exception

**File:** `registry/exceptions.py`
**Lines:** insert after line 205 (after `SkillContentSSRFError`)

```python
class UnsafeUrlError(RegistryError):
    """Raised when a URL fails SSRF validation before an outbound fetch."""

    def __init__(
        self,
        url: str,
        reason: str,
    ):
        self.url = url
        self.reason = reason
        super().__init__(f"URL failed SSRF validation '{url}': {reason}")
```

#### Step 4: Add `HealthStatus.UNHEALTHY_UNSAFE_URL`

**File:** `registry/constants.py`
**Lines:** inside the existing `HealthStatus` enum (lines 11-32)

Add one member, following the existing naming convention (`UNHEALTHY_TIMEOUT`, `UNHEALTHY_CONNECTION_ERROR`, `UNHEALTHY_MISSING_PROXY_URL`):

```python
UNHEALTHY_UNSAFE_URL = "unhealthy: unsafe url blocked by SSRF protection"
```

#### Step 5: Harden the agent-card reachability check

**File:** `registry/utils/agent_validator.py`
**Lines:** 196-230 (`_check_endpoint_reachability`)

```python
from ..utils.ssrf_protection import is_safe_url


def _check_endpoint_reachability(
    url: str,
) -> tuple[bool, str | None]:
    """
    Check if agent endpoint is reachable.

    Attempts HTTP GET request to the well-known endpoint. Validates the URL
    (and the final URL after any redirect) against SSRF protection before
    fetching. Does not block validation if unreachable or unsafe - callers
    decide whether to hard-fail via settings.ssrf_reject_unsafe_registration.

    Args:
        url: Agent endpoint URL to check.

    Returns:
        Tuple of (is_reachable, error_message).
    """
    well_known_url = f"{url}/.well-known/agent-card.json"

    if not is_safe_url(well_known_url):
        logger.warning(f"SSRF protection: Blocked agent-card fetch for {url}")
        return (False, "URL failed SSRF validation - private/internal addresses are not allowed")

    try:
        response = httpx.get(
            well_known_url,
            timeout=5.0,
            follow_redirects=True,
        )

        final_url = str(response.url)
        if final_url != well_known_url and not is_safe_url(final_url):
            logger.warning(
                f"SSRF protection: Blocked redirect from {well_known_url} to unsafe URL {final_url}"
            )
            return (False, f"Redirect to unsafe URL blocked: {final_url}")

        if response.status_code == 200:
            return (True, None)

        return (False, f"Endpoint returned status {response.status_code}")

    except httpx.TimeoutException:
        logger.warning(f"Endpoint timeout for {url}")
        return (False, "Endpoint request timed out")

    except Exception as e:
        logger.warning(f"Could not reach endpoint {url}: {e}")
        return (False, str(e))
```

Note `follow_redirects=True` is added here (previously absent/default-False on the sync `httpx.get()` shortcut) since a post-redirect check now exists to make following redirects safe; this is a minor behavior change (redirects on agent-card fetch now succeed instead of silently returning the redirect response) but is strictly additive to what already worked.

#### Step 6: Enforce strict-mode rejection at registration (optional setting)

**File:** `registry/api/agent_routes.py`
**Lines:** near 577-580, inside `register_agent()`

```python
validation_result = await agent_validator.validate_agent_card(
    agent_card,
    verify_endpoint=True,
)

if settings.ssrf_reject_unsafe_registration:
    unsafe_warnings = [w for w in validation_result.warnings if "SSRF validation" in w]
    if unsafe_warnings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent URL rejected: {unsafe_warnings[0]}",
        )
```

Placed immediately after the existing `validate_agent_card` call and before the rest of `register_agent()`'s existing error-handling logic (which already inspects `validation_result.errors`) so it composes with, rather than replaces, existing validation-error handling.

#### Step 7: Harden the agent health-check endpoint

**File:** `registry/api/agent_routes.py`
**Lines:** 920-983 (the per-URL fetch loop inside the `/agents/{path}/health` handler)

```python
from ..utils.ssrf_protection import is_safe_url

    for url in health_urls:
        if not is_safe_url(url):
            logger.warning(f"SSRF protection: Skipping unsafe agent health-check URL {url}")
            continue

        health_check_url = url
        start_time = datetime.now(UTC)

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(url)

            final_url = str(response.url)
            if final_url != url and not is_safe_url(final_url):
                logger.warning(
                    f"SSRF protection: Blocked redirect from {url} to unsafe URL {final_url}"
                )
                continue

            status_code = response.status_code
            # ... existing status-code handling continues unchanged ...
```

The HEAD-based fallback on `base_url` (further down in the same function) gets the identical pre-flight + post-redirect pair before its `client.head(base_url)` call.

#### Step 8: Harden the MCP server health-check engine

**File:** `registry/health/service.py`
**Lines:** 674-957 (`_check_server_endpoint_transport_aware`), 560-625 (`_initialize_mcp_session`), 627-672 (`_try_ping_without_auth`)

Add a single guard at the top of `_check_server_endpoint_transport_aware`, immediately after the existing `if not proxy_pass_url:` check (line 682-683):

```python
from ..utils.ssrf_protection import is_safe_url
from ..constants import HealthStatus  # already imported

    async def _check_server_endpoint_transport_aware(
        self, client: httpx.AsyncClient, proxy_pass_url: str, server_info: dict
    ) -> tuple[bool, str]:
        """Check server endpoint using transport-aware logic.

        Returns:
            tuple[bool, str]: (is_healthy, status_detail)
        """
        if not proxy_pass_url:
            return False, HealthStatus.UNHEALTHY_MISSING_PROXY_URL

        if not is_safe_url(proxy_pass_url):
            logger.warning(
                f"SSRF protection: Blocked health check for unsafe URL {proxy_pass_url}"
            )
            return False, HealthStatus.UNHEALTHY_UNSAFE_URL

        # ... existing transport-detection logic unchanged from here ...
```

This single check at the top of the function covers every branch below it (the `/mcp`/`/sse`-in-URL shortcut, the streamable-http path via `get_endpoint_url_from_server_info`, and the plain SSE path) since they all derive their target from `proxy_pass_url` or an endpoint computed from the same registered server record - the endpoint-resolution logic (`get_endpoint_url_from_server_info`) only appends a known suffix (`/mcp`) to an already-validated host, so it cannot itself introduce an unsafe target.

Add the post-redirect check after each of the three `follow_redirects=True` response points (lines ~713, ~738, and inside the streamable-http/SSE branches further down, plus inside `_initialize_mcp_session` and `_try_ping_without_auth`). Representative example for the plain-MCP branch (was line 736-738):

```python
                    response = await client.get(
                        proxy_pass_url, headers=headers, follow_redirects=True
                    )

                    final_url = str(response.url)
                    if final_url != proxy_pass_url and not is_safe_url(final_url):
                        logger.warning(
                            f"SSRF protection: Blocked redirect from {proxy_pass_url} "
                            f"to unsafe URL {final_url}"
                        )
                        return False, HealthStatus.UNHEALTHY_UNSAFE_URL
```

Apply the same `final_url` pattern at each of the other four response points in this file that use `follow_redirects=True` (SSE branch ~line 710, streamable-http ping in `_initialize_mcp_session`, the ping in `_try_ping_without_auth`, and the SSE-transport branch further down in `_check_server_endpoint_transport_aware`). `_initialize_mcp_session` and `_try_ping_without_auth` both receive `endpoint`/`proxy_pass_url` as a parameter already validated by the pre-flight check in the caller, but since they issue their own separate HTTP requests to that same (already-validated) URL, only the post-redirect check needs to be added inside them - not a second pre-flight check, to avoid a redundant `socket.getaddrinfo()` call per health-check attempt.

### Error Handling

- `is_safe_url()` never raises - it is a boolean predicate, matching the existing `_is_safe_url` contract, so callers keep their existing `if not is_safe_url(...): <handle>` control flow without introducing new exception types into hot paths that did not have one before.
- The one new exception, `UnsafeUrlError`, is defined for future callers that want fail-fast semantics (e.g. if a follow-up change adds SSRF validation to `proxy_pass_url` at registration time) but is not raised by any of the code touched in this design - the agent-card and health-check paths use the boolean-return-plus-warning pattern to stay backwards-compatible with their existing "warn, don't block" behavior.
- Registration-time strict-mode rejection (Step 6) uses the existing `HTTPException(400, ...)` idiom already used elsewhere in `agent_routes.py`, not a new exception-translation layer.

### Logging

- All new log lines use the exact existing prefix `"SSRF protection: "` at `WARNING` level, matching `skill_service.py`'s convention, so a single log-based alert/search continues to cover every SSRF rejection across skills, agent-card fetches, and health checks.
- Debug-level logging (`logger.debug`) is used for the allowlist-hit case (`"SSRF protection: Trusted domain '...'"`), unchanged from today, so normal operation does not add noise at INFO level.

## Observability

### Tracing / Metrics / Logging Points

- Every rejection logs at WARNING with the hostname/IP and reason, as described above - this is the primary observability surface (no new metrics system is introduced, consistent with this codebase not having a dedicated metrics-emission pattern inside `skill_service.py`/`health/service.py` beyond logging and the existing `metrics-service/` telemetry pipeline, which is out of scope for this change).
- The new `HealthStatus.UNHEALTHY_UNSAFE_URL` value flows through the existing health-status broadcast mechanism (`HighPerformanceWebSocketManager` in `registry/health/service.py`) with no additional wiring - it is treated exactly like any other `HealthStatus` string value already broadcast today, so operators see "unsafe url blocked" show up in the existing health dashboard/WebSocket feed without any new UI work.
- Agent registration warnings (Step 5/6) surface through the existing `ValidationResult.warnings` list already returned in the `POST /agents/register` response body - no new response field.

## Scaling Considerations

- **Per-request DNS resolution cost**: `is_safe_url()` performs a synchronous `socket.getaddrinfo()` call. In `_check_endpoint_reachability()` this was already synchronous (blocking) before this change; in the async health-check paths (`health/service.py`, the `/agents/{path}/health` endpoint), this adds one blocking DNS call per health check per server/agent. At the default `health_check_interval_seconds=300` and typical registry sizes (tens to low hundreds of registered servers, per the batch-of-10 staggering already present in `_perform_health_checks`), this is a small, bounded addition (single-digit milliseconds per lookup in the common case, OS-level DNS cache typically absorbing repeat lookups) and does not change the existing batching/staggering strategy.
- **`lru_cache(maxsize=1)` on `_allowed_hosts()`/`_allowed_networks()`**: identical caching strategy to the existing `_trusted_domains()` - settings are read once per process lifetime, so scaling the number of registered servers/agents does not increase allowlist-lookup cost.
- **No new connection pooling changes**: this design does not alter the `httpx.AsyncClient` instances or their pool settings in `health/service.py`; it only adds validation before requests already being made.
- **Horizontal scaling**: the registry's health-check background loop and the guard's in-memory caches are per-process; running multiple registry replicas (as is already the case on ECS) means each replica independently resolves and caches its own allowlist - no shared state is required, consistent with how `github_extra_hosts`/`_trusted_domains()` already behaves today.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/ssrf_protection.py` | Shared SSRF guard: `is_safe_url()`, `_is_private_ip()`, `_allowed_hosts()`, `_allowed_networks()`, `clear_ssrf_caches()` |
| `tests/unit/utils/test_ssrf_protection.py` | Unit tests for the relocated/generalized guard (private-IP, loopback, link-local, allowlist-by-host, allowlist-by-CIDR, invalid-IP-fail-closed cases) |
| `tests/unit/utils/test_agent_validator_ssrf.py` | Unit tests for `_check_endpoint_reachability()`'s new SSRF pre-flight and post-redirect checks |
| `tests/unit/health/test_health_service_ssrf.py` | Unit tests for `_check_server_endpoint_transport_aware()`'s new SSRF checks |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|---------------------|
| `registry/services/skill_service.py` | ~135 removed, ~5 changed | Remove `_is_private_ip`/`_trusted_domains`/`_is_safe_url`/`_DEFAULT_TRUSTED_DOMAINS`; import and call `is_safe_url` from the new shared module at all five existing call sites |
| `registry/utils/agent_validator.py` | ~20 | Add `is_safe_url` import and pre-flight + post-redirect checks to `_check_endpoint_reachability()`; add `follow_redirects=True` |
| `registry/api/agent_routes.py` | ~25 | Add `is_safe_url` checks to the `/agents/{path}/health` fetch loop and HEAD fallback; add strict-mode rejection block in `register_agent()` |
| `registry/health/service.py` | ~40 | Add pre-flight check in `_check_server_endpoint_transport_aware()`; add post-redirect checks at each of the five `follow_redirects=True` response points across that method plus `_initialize_mcp_session`/`_try_ping_without_auth` |
| `registry/exceptions.py` | ~12 | Add `UnsafeUrlError` |
| `registry/constants.py` | ~1 | Add `HealthStatus.UNHEALTHY_UNSAFE_URL` |
| `registry/core/config.py` | ~20 | Add `ssrf_allowed_hosts`, `ssrf_allowed_cidrs`, `ssrf_reject_unsafe_registration` settings |
| `.env.example` | ~12 | Document the three new environment variables |
| `tests/unit/test_skill_service_github_auth.py`, `tests/unit/test_skill_routes_github_auth.py`, `tests/unit/api/test_skill_inline_content.py`, `tests/unit/services/test_skill_service_ssrf_allowlist.py` | ~15 total | Update patch targets from `registry.services.skill_service._is_safe_url`/`._trusted_domains`/`.socket` to `registry.utils.ssrf_protection.is_safe_url`/`._allowed_hosts`/`.socket`; use `clear_ssrf_caches()` |
| `docker-compose.yml` and variants, `charts/` values, `terraform/` variables | small | Wire the three new env vars per the Deployment Surface Checklist above |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~180 |
| New tests | ~220 |
| Modified code | ~145 |
| **Total** | **~545** |

## Testing Strategy

See `./testing.md` for the full plan. In summary: unit tests for the relocated/generalized guard (private IP ranges, loopback, link-local, reserved, cloud-metadata IP, host allowlist, CIDR allowlist, invalid-IP fail-closed, scheme rejection, DNS-resolution-failure rejection), unit tests for each of the three hardened call sites (agent-card reachability check, agent health endpoint, MCP server health-check engine) covering both the pre-flight and post-redirect paths with mocked `socket.getaddrinfo`/`httpx` responses, a regression pass on the existing SKILL.md SSRF tests after the relocation, and backwards-compatibility tests confirming that registering/health-checking a server or agent with a public URL behaves identically before and after the change.

## Alternatives Considered

### Alternative 1: Add a Pydantic validator on `AgentCard.url` / `ServerInfo.proxy_pass_url` that calls the guard at model-construction time

**Description:** Instead of guarding at the point of outbound fetch, validate the URL field itself whenever an `AgentCard` or `ServerInfo` model is constructed, using a `@field_validator`.

**Pros / Cons:** Would catch unsafe URLs earlier (at parse time) and centralize the check in one place per model. However, it performs a blocking DNS resolution inside Pydantic model construction, which happens on every read of stored data (e.g. every time a server record is loaded from the repository, not just at registration), not only when an outbound request is about to be made - this is both a performance problem (DNS lookups on every deserialization) and a correctness mismatch (a URL that was safe at registration time but whose DNS record changed later would fail to even *load* the stored record, rather than just failing its next health check).

**Why Rejected:** Guarding at the fetch call site (this design's approach) matches the existing SKILL.md precedent exactly, avoids surprise failures on unrelated read paths, and keeps the DNS-resolution cost tied to actual network activity.

### Alternative 2: Re-resolve and pin the IP immediately before the httpx call (fix the DNS-rebinding TOCTOU gap)

**Description:** Instead of only checking `_is_safe_url()` before calling `httpx`, resolve the hostname once, validate the IP, then connect directly to that pinned IP (e.g. via `httpx.AsyncClient` with a custom transport/`socket_options`, or an explicit `Host` header against the resolved IP) to eliminate the gap between validation and connection where a second DNS lookup by `httpx` itself could return a different (unsafe) IP.

**Pros / Cons:** Closes a genuine, if narrow, DNS-rebinding attack window. However, it requires either a custom `httpx` transport or bypassing `httpx`'s normal connection handling (complicating TLS SNI/hostname verification, redirect handling, and connection pooling), a materially more invasive change than the task's stated scope ("promote the existing `_is_safe_url()` into a shared utility, apply it").

**Why Rejected:** The existing SKILL.md implementation has the same gap today and it is not called out as a known incident or exploited issue; closing it is valuable but is a separate, larger piece of work (custom transport, careful TLS-verification handling) better scoped as its own follow-up issue rather than bundled into this hardening pass. Flagged explicitly in Non-Goals and as an Open Question below so it is not silently dropped.

### Alternative 3: Hard-block (never warn-and-allow) any unsafe URL at agent registration, with no `ssrf_reject_unsafe_registration` opt-in

**Description:** Make SSRF rejection at registration time unconditional (400 Bad Request) rather than gated behind a setting.

**Pros / Cons:** Simpler (one code path, no new setting) and arguably "more secure by default." However, the task's explicit constraint is "must be backwards-compatible" - some existing deployments may have legitimately registered agents whose URL resolves to a private IP intentionally (e.g. an internal agent reachable only from within the registry's VPC), and unconditionally rejecting registration would break those without warning on upgrade.

**Why Rejected:** The opt-in `ssrf_reject_unsafe_registration` setting (default `false`) preserves today's "warn but allow" behavior for anyone who has not explicitly asked for stricter enforcement, while still giving operators who want it a supported path to full enforcement - directly satisfying the backwards-compatibility requirement from the clarifying answers.

### Comparison Matrix

| Criteria | Chosen (fetch-site guard + opt-in strict mode) | Alt 1 (model-level validator) | Alt 2 (IP-pinned connection) | Alt 3 (unconditional hard-block) |
|----------|--------------------------------------------------|-------------------------------|-------------------------------|-----------------------------------|
| Backwards compatible | Yes | No (breaks stored-record loads) | Yes | No (breaks existing internal agents) |
| Matches existing precedent | Yes (mirrors SKILL.md pattern) | No | No | Partially |
| Closes DNS-rebinding TOCTOU | No (same as today) | No | Yes | No |
| Implementation complexity | Low-Medium | Medium | High | Low |
| Scope match to task | Exact | Exceeds scope | Exceeds scope | Slightly exceeds ("must be backwards-compatible") |

## Rollout Plan

- Phase 1: Implementation (out of scope for this skill) - land the shared `ssrf_protection.py` module, migrate `skill_service.py`, harden the three named call sites, add settings/exceptions/constants, update the four existing skill-SSRF test files' patch targets.
- Phase 2: Testing - run the full existing test suite to confirm the SKILL.md relocation is behavior-preserving (see `testing.md` Section 2, Backwards Compatibility), then exercise the new agent-card and health-check SSRF tests.
- Phase 3: Deployment - ship with all three new settings at their safe defaults (`ssrf_allowed_hosts=""`, `ssrf_allowed_cidrs=""`, `ssrf_reject_unsafe_registration=false`); no operator action required for the change to take effect (the guard denies private/internal IPs by default, which is strictly safer than today's no-guard state, without requiring any config). Operators who discover legitimate internal agents/servers now generating SSRF warnings in logs can add them to `SSRF_ALLOWED_HOSTS`/`SSRF_ALLOWED_CIDRS`; operators who want hard enforcement can set `SSRF_REJECT_UNSAFE_REGISTRATION=true` once they have confirmed no legitimate internal registrations remain unaddressed.

## Open Questions

- Should the DNS-rebinding TOCTOU gap (Alternative 2) be tracked as an immediate follow-up issue, or left as an accepted risk shared with the existing SKILL.md guard? This design leaves it as an accepted, pre-existing risk and does not fix it, per the task's stated scope.
- Should `registration_webhook_url` and `registration_gate_url` (operator-configured, not attacker-controlled) also route through `is_safe_url()` for defense in depth, given they are also outbound fetches to configurable URLs? Left as a Non-Goal since the task specifically names agent-card fetch and health checks; flagged here for a follow-up issue.
- Should the CLI's own agent-card fetch (`cli/agent_mgmt.py`, using `requests` rather than `httpx`, identified during codebase analysis) also be hardened? It runs outside the FastAPI process as a standalone tool; this design does not touch it, since the task scope is the registry service's own outbound requests, but it shares the identical vulnerability pattern and is worth a follow-up.

## References

- `registry/services/skill_service.py` lines 60-192 - the existing `_is_safe_url()` implementation this design generalizes.
- `tests/unit/services/test_skill_service_ssrf_allowlist.py` - existing SSRF test suite and mocking pattern to replicate for new call sites.
- Reference issue: https://github.com/agentic-community/mcp-gateway-registry/issues/1282
- `docs/design/a2a-protocol-integration.md` - background on the A2A agent-card fetch flow this design hardens.
