# Low-Level Design: SSRF Hardening for Agent Card Fetch and Health Checks

*Created: 2026-06-24*
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
15. [Open Questions](#open-questions)
16. [References](#references)

## Overview

### Problem Statement
The registry persists user-supplied URLs at registration time and later issues outbound HTTP requests to them with no SSRF validation:

- **Agent health check** (`POST /agents/{path}/health`, `registry/api/agent_routes.py:883`) fetches `agent_card.url` (and a derived `/.well-known/agent-card.json`) via `httpx` GET/HEAD.
- **Server health check** (`registry/health/service.py:674`) requests `proxy_pass_url` via GET/POST with `follow_redirects=True`, injecting stored credentials.

A complete SSRF guard already exists for SKILL.md fetches (`_is_safe_url()` in `registry/services/skill_service.py:128`). It is not reused on these two surfaces. This design promotes that guard into a shared utility and applies it everywhere the registry fetches a user-supplied URL.

### Goals
- Block outbound requests to private/loopback/link-local/reserved IPs and the cloud metadata address `169.254.169.254` on the agent-card and server health-check paths.
- Enforce http/https-only schemes and re-validate redirect targets.
- Reuse the existing, proven validation logic (one source of truth) without changing SKILL.md behavior.
- Provide a feature flag and host allowlist for operators on private networks.

### Non-Goals
- Federation client hardening (separate issue; endpoints are config-driven).
- Webhook / registration-gate callbacks (config-driven, admin-set).
- Network-layer egress controls.
- Any change to the health-check transport selection or agent-card schema.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/services/skill_service.py` (94-192) | SKILL.md fetch with SSRF guard | Source of `_is_safe_url()`/`_is_private_ip()` to extract and reuse |
| `registry/api/agent_routes.py` (186-205, 883-1013) | Agent health check + URL builder | Primary unprotected path (GET/HEAD on `agent_card.url`) |
| `registry/health/service.py` (674-957, tool-fetch task ~1072) | Server health + tool discovery | Second unprotected path (GET/POST on `proxy_pass_url`, injects credentials) |
| `registry/exceptions.py` (194-204) | `SkillContentSSRFError` | Pattern for an SSRF exception; will generalize naming |
| `registry/core/config.py` (53, 173-177, 292-296) | `Settings`, health timeout, `github_extra_hosts` | Where new flags live; existing allowlist precedent |
| `registry/utils/` | Shared helpers (`path_utils`, `url_utils`, `agent_validator`) | Natural home for `ssrf.py` |
| `tests/unit/health/test_health_service.py`, `tests/unit/core/test_endpoint_utils.py` | Existing unit tests | Test conventions (pytest, async, monkeypatch) |

### Existing Patterns Identified
1. **SSRF guard for SKILL.md**: `_is_safe_url(url)` parses with `urlparse`, enforces http/https, allowlists trusted domains, resolves via `socket.getaddrinfo`, and rejects private IPs through `_is_private_ip()`. Redirect targets are re-checked after the fetch.
   - Files: `registry/services/skill_service.py`
   - How to follow: extract verbatim into `registry/utils/ssrf.py`, then import it back into `skill_service.py` so behavior is byte-for-byte identical.
2. **Module-level functions over classes for stateless helpers**: utils are plain functions with `lru_cache` for config-derived sets (`_trusted_domains()`).
   - How to follow: keep `is_safe_url()` a pure function; cache the merged allowlist.
3. **Pydantic `Settings` with env-var binding**: every config knob is a `Field(default=..., description=...)` on `Settings` in `core/config.py`.
   - How to follow: add `ssrf_protection_enabled` and `ssrf_extra_allowed_hosts` as `Field`s.
4. **Health checks degrade, never 500**: both health paths catch exceptions and return an `unhealthy` status string rather than raising.
   - How to follow: a blocked URL maps to `status: "unhealthy"` with an SSRF detail, not an HTTP 500.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `agent_routes.check_agent_health` | Uses | Call `is_safe_url()` before each GET and before the HEAD fallback |
| `health.service._check_server_endpoint_transport_aware` | Uses | Validate `proxy_pass_url` and resolved `endpoint` before each request |
| `health.service` tool-fetch background task | Uses | Validate `proxy_pass_url` before `get_mcp_connection_result()` |
| `skill_service._is_safe_url` | Replaced-by re-export | Re-export from `registry/utils/ssrf.py`; no behavior change |
| `core.config.Settings` | Extends | Two new fields |

### Constraints and Limitations Discovered
- **TOCTOU / DNS rebinding**: `is_safe_url()` resolves the hostname, then `httpx` resolves it again at request time; a rebinding attacker could return a public IP to the guard and a private IP to httpx. The existing SKILL.md guard has the same limitation. This design documents it and recommends a follow-up (resolve-and-pin or an httpx transport that re-checks the connected IP) as out of scope but noted.
- **Trusted-domain allowlist bypasses the IP check** (by design, for GHES). Reusing the same allowlist means an allowlisted host on a private IP is reachable - acceptable and intended, but the agent/server paths get a *separate* allowlist (`ssrf_extra_allowed_hosts`) so health-check trust is not silently coupled to GitHub auth trust.
- **`follow_redirects=True`** on the server health path: must re-validate the final URL, exactly as SKILL.md does.
- **Health timeout is very low** (`health_check_timeout_seconds: int = 2`). `getaddrinfo` is synchronous; calling it inside an async handler adds a small blocking DNS lookup. Acceptable (SKILL.md already does this), but noted under Scaling.

## Architecture

### System Context Diagram
```
            register (user-supplied url / proxy_pass_url)
 Client  ───────────────────────────────────────────────►  Registry (FastAPI)
                                                              stores AgentCard.url
                                                              stores proxy_pass_url
                                                                     │
 Client  ── POST /agents/{path}/health ─────────────────────────────┤
 Client  ── (background) server health / tool fetch ────────────────┤
                                                                     ▼
                                                          ┌──────────────────────┐
                                                          │ is_safe_url(url)      │  ◄── NEW shared guard
                                                          │  scheme http/https?   │
                                                          │  host in allowlist?   │
                                                          │  resolve -> private?  │
                                                          └──────────┬───────────┘
                                                            block    │  allow
                                                          (unhealthy)│
                                                                     ▼
                                                          httpx GET/HEAD/POST ─────►  Agent / MCP server
                                                                                      (now only public hosts
                                                                                       or allowlisted hosts)
```

### Sequence Diagram (agent health check)
```
caller -> agent_routes.check_agent_health(path)
  check_agent_health -> agent_service.get_agent_info(path)  => agent_card
  check_agent_health -> _build_agent_health_urls(base_url)  => [card_url, base_url]
  loop url in health_urls:
      check_agent_health -> is_safe_url(url)
          alt unsafe:
              log WARNING "SSRF blocked {host}"
              continue (skip fetch; detail = "blocked by SSRF policy")
          else safe:
              check_agent_health -> httpx.get(url)
  opt all GET failed and base_url safe:
      check_agent_health -> is_safe_url(base_url)
      check_agent_health -> httpx.head(base_url)
  check_agent_health -> agent_service.update_agent(health_status)
  check_agent_health --> caller {status, detail, ...}
```

### Component Diagram
```
registry/utils/ssrf.py            (NEW)
  ├─ is_private_ip(ip_str) -> bool
  ├─ is_safe_url(url, *, extra_allowed_hosts=None) -> bool
  └─ _allowed_hosts() -> frozenset           (lru_cache)

registry/services/skill_service.py
  └─ from ..utils.ssrf import is_safe_url    (re-export shim: _is_safe_url = is_safe_url)

registry/api/agent_routes.py
  └─ from ..utils.ssrf import is_safe_url    (guard GET + HEAD)

registry/health/service.py
  └─ from ..utils.ssrf import is_safe_url    (guard proxy_pass_url + endpoint)

registry/core/config.py
  └─ Settings.ssrf_protection_enabled, Settings.ssrf_extra_allowed_hosts
```

## Data Models

### New Models
No new request/response Pydantic models. The agent health response keeps its existing shape; a blocked URL is surfaced through the existing `detail` string field:

```python
# Existing response shape from check_agent_health (unchanged keys)
{
    "agent_path": str,
    "health_check_url": str,
    "status": "healthy" | "unhealthy",
    "status_code": int | None,
    "detail": str | None,          # e.g. "Blocked by SSRF policy: host resolves to private IP"
    "response_time_ms": int | None,
    "last_checked_iso": str,
}
```

### Model Changes
None. `AgentCard.url` and `proxy_pass_url` keep their current types. (Optional hardening at registration time is listed under Alternatives, not adopted here to keep scope tight and avoid rejecting legitimate private-network registrations.)

## API / CLI Design

### Modified Endpoint: `POST /agents/{path}/health`
**Description:** Unchanged contract. New behavior: when the agent-card URL or fallback URL fails SSRF validation, the endpoint does not issue the request; it records `status: "unhealthy"` with an SSRF `detail`.

**Invocation:**
```bash
curl -sS -X POST "$REGISTRY_URL/api/agents/my-agent/health" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

**Expected Response (safe URL, reachable):**
```json
{ "agent_path": "my-agent", "status": "healthy", "status_code": 200, "detail": null }
```

**Expected Response (URL resolves to private IP):**
```json
{ "agent_path": "my-agent", "status": "unhealthy", "status_code": null,
  "detail": "Blocked by SSRF policy: 127.0.0.1" }
```

**Error Cases (unchanged):**
- 404: agent not found.
- 403: caller lacks access.
- 400: agent disabled.

### Server health check / tool fetch
Internal (background task and service method). No HTTP contract change. A blocked `proxy_pass_url` yields `is_healthy=False` with detail `unhealthy: blocked by SSRF policy`.

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `SSRF_PROTECTION_ENABLED` | bool | `true` | No | Master switch for outbound URL validation on health/agent-card paths. When false, behavior reverts to pre-change (no validation) - intended only for trusted closed networks. |
| `SSRF_EXTRA_ALLOWED_HOSTS` | str | `""` | No | Comma-separated hostnames that bypass the private-IP check for health/agent-card fetches (e.g. internal agent hosts). Mirrors `github_extra_hosts` for SKILL.md. |

### Settings / Config Class Updates
```python
# registry/core/config.py, inside class Settings (near github_extra_hosts ~line 292)
ssrf_protection_enabled: bool = Field(
    default=True,
    description=(
        "Validate outbound URLs (agent-card fetch, server/agent health checks) "
        "against the SSRF policy. Disable only on fully trusted closed networks."
    ),
)
ssrf_extra_allowed_hosts: str = Field(
    default="",
    description=(
        "Comma-separated hostnames allowed to bypass the SSRF private-IP check "
        "for health and agent-card fetches (e.g. internal agent hosts)."
    ),
)
```

### Deployment Surface Checklist
Every surface where the two new variables may be set (all optional, safe defaults):
- [ ] `.env.example` (document both, default true / empty)
- [ ] `docker-compose.yml` / `docker-compose.*.yml` registry service `environment:`
- [ ] Helm `values.yaml` and the registry Deployment env block (if a chart exists)
- [ ] Terraform/ECS task definition environment for the registry service
- [ ] `docs/unified-parameter-reference.md` (parameter table)

## New Dependencies
This change uses only existing dependencies (`httpx`, and the `ipaddress` / `socket` / `urllib.parse` standard-library modules already imported by `skill_service.py`). No new packages.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create the shared SSRF utility
**File:** `registry/utils/ssrf.py` (new file)

Move the existing logic out of `skill_service.py` verbatim, parameterizing the allowlist so different call sites can pass their own extra hosts. Keep the function names public (no leading underscore) since this is now a shared module.

```python
"""Shared SSRF (Server-Side Request Forgery) URL validation.

Single source of truth for outbound-URL safety checks. Used by SKILL.md
fetches, agent-card fetches, and server/agent health checks. Logic is
preserved verbatim from the original skill_service implementation.
"""

import ipaddress
import logging
import socket
from functools import lru_cache
from urllib.parse import urlparse

from ..core.config import settings

logger = logging.getLogger(__name__)

# Built-in trusted domains that skip the IP check (used by SKILL.md fetches).
_DEFAULT_TRUSTED_DOMAINS: frozenset[str] = frozenset(
    {"github.com", "gitlab.com", "raw.githubusercontent.com", "bitbucket.org"}
)


def _split_hosts(raw: str | None) -> frozenset[str]:
    """Parse a comma-separated host list into a lowercase frozenset."""
    if not raw:
        return frozenset()
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


@lru_cache(maxsize=1)
def skill_trusted_domains() -> frozenset[str]:
    """SKILL.md allowlist: built-in defaults plus configured GHES hosts."""
    return _DEFAULT_TRUSTED_DOMAINS | _split_hosts(settings.github_extra_hosts)


@lru_cache(maxsize=1)
def health_allowed_hosts() -> frozenset[str]:
    """Health/agent-card allowlist: hosts that bypass the private-IP check."""
    return _split_hosts(settings.ssrf_extra_allowed_hosts)


def is_private_ip(ip_str: str) -> bool:
    """True if the IP is private/loopback/link-local/reserved or metadata."""
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
        if ip_str == "169.254.169.254":  # cloud metadata endpoint
            return True
        return False
    except ValueError:
        return True  # unparseable -> treat as unsafe


def is_safe_url(url: str, *, allowed_hosts: frozenset[str] | None = None) -> bool:
    """Return True if the URL is safe to fetch.

    Enforces http/https, then either matches an allowlisted host (skips IP
    check) or resolves the hostname and rejects any private/metadata IP.

    Args:
        url: URL to validate.
        allowed_hosts: hostnames that bypass the private-IP check. Defaults
            to the SKILL.md trusted domains for backward compatibility.
    """
    allow = skill_trusted_domains() if allowed_hosts is None else allowed_hosts
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"SSRF protection: blocked scheme '{parsed.scheme}'")
            return False
        hostname = parsed.hostname
        if not hostname:
            logger.warning("SSRF protection: URL has no hostname")
            return False
        if hostname.lower() in allow:
            logger.debug(f"SSRF protection: trusted host '{hostname.lower()}'")
            return True
        try:
            addr_info = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as e:
            logger.warning(f"SSRF protection: cannot resolve '{hostname}': {e}")
            return False
        for *_unused, sockaddr in addr_info:
            ip = sockaddr[0]
            if is_private_ip(ip):
                logger.warning(
                    f"SSRF protection: blocked '{hostname}' -> private IP '{ip}'"
                )
                return False
        return True
    except Exception as e:  # defensive: never let validation crash the caller
        logger.warning(f"SSRF protection: error validating URL: {e}")
        return False
```

Note for implementer: keep `is_safe_url`'s default allowlist equal to the SKILL.md trusted domains so the re-export in Step 2 is a pure refactor.

#### Step 2: Re-point `skill_service.py` at the shared module (no behavior change)
**File:** `registry/services/skill_service.py` (lines ~60-192)

Delete the local `_DEFAULT_TRUSTED_DOMAINS`, `_trusted_domains`, `_is_private_ip`, and `_is_safe_url` definitions and import the shared functions, aliasing to the old private names so the rest of the file (lines 595, 616, 681, 707, 866, 896, 1042, 1071) is untouched.

```python
from ..utils.ssrf import is_safe_url as _is_safe_url  # noqa: F401  (re-export shim)
# (is_private_ip / trusted-domains now live in registry.utils.ssrf)
```

#### Step 3: Guard the agent health check
**File:** `registry/api/agent_routes.py` (lines 920-983)

Add a helper that wraps `is_safe_url()` with the feature flag and health allowlist, then call it before every GET and the fallback HEAD.

```python
from ..utils.ssrf import is_safe_url, health_allowed_hosts

def _outbound_url_allowed(url: str) -> bool:
    """Apply the SSRF policy to a health-check URL (honors the feature flag)."""
    if not settings.ssrf_protection_enabled:
        return True
    return is_safe_url(url, allowed_hosts=health_allowed_hosts())
```

In the `for url in health_urls:` loop, before `httpx.AsyncClient(...).get(url)`:

```python
for url in health_urls:
    health_check_url = url
    if not _outbound_url_allowed(url):
        detail = "Blocked by SSRF policy"
        logger.warning(f"Agent health check for {path} blocked by SSRF policy: {url}")
        continue
    start_time = datetime.now(UTC)
    ...
```

Before the fallback HEAD (line ~962):

```python
if status_label == "unhealthy" and _outbound_url_allowed(base_url):
    logger.info(f"Agent {path} GET checks failed, falling back to HEAD ping on {base_url}")
    ...
```

(If `base_url` is blocked, skip the HEAD entirely; `detail` already reflects the SSRF block.)

#### Step 4: Guard the server health check and tool-fetch task
**File:** `registry/health/service.py`

At the top of `_check_server_endpoint_transport_aware()` (after the empty-URL guard, line ~682):

```python
from ..utils.ssrf import is_safe_url, health_allowed_hosts

if settings.ssrf_protection_enabled and not is_safe_url(
    proxy_pass_url, allowed_hosts=health_allowed_hosts()
):
    logger.warning(f"Health check blocked by SSRF policy: {proxy_pass_url}")
    return False, "unhealthy: blocked by SSRF policy"
```

Also validate the **resolved** transport endpoint (the `get_endpoint_url_from_server_info(...)` result at lines ~777 and ~860) before its request, since `mcp_endpoint`/`sse_endpoint` can be user-supplied overrides distinct from `proxy_pass_url`:

```python
endpoint = get_endpoint_url_from_server_info(server_info, transport_type="streamable-http")
if settings.ssrf_protection_enabled and not is_safe_url(endpoint, allowed_hosts=health_allowed_hosts()):
    logger.warning(f"Health check endpoint blocked by SSRF policy: {endpoint}")
    return False, "unhealthy: blocked by SSRF policy"
```

In the tool-fetch background task (around line ~1072), validate `proxy_pass_url` before `mcp_client_service.get_mcp_connection_result(...)` and skip the fetch (leave tools unchanged) if blocked.

#### Step 5: Add settings
**File:** `registry/core/config.py` (near line 292, beside `github_extra_hosts`)

Add the two `Field`s from [Settings / Config Class Updates](#settings--config-class-updates).

### Error Handling
- Validation never raises into the caller; `is_safe_url()` catches all exceptions and returns `False` (fail-closed).
- A blocked URL is a normal, expected outcome on health paths: surface it as `unhealthy` with an SSRF `detail`, not an HTTP 5xx.
- DNS resolution failure is treated as unsafe (`False`), consistent with SKILL.md.

### Logging
- WARNING on every block, including the hostname/IP and the asset path/url, for audit trails.
- DEBUG when an allowlisted host bypasses the IP check.
- No change to existing INFO health-status logging.

## Observability

### Tracing / Metrics / Logging Points
- **Log event** `SSRF protection: blocked ...` (WARNING) is the audit signal; operators can alert on its rate.
- **Suggested metric (optional, low effort):** increment a counter `registry_ssrf_blocked_total{surface="agent_health|server_health|tool_fetch"}` if the project already exposes Prometheus/OTel counters. The repo uses OpenTelemetry (`registry/core/telemetry.py`); a span attribute `ssrf.blocked=true` on the health span is a lightweight option.
- **Health status persistence** already records `health_status`/`last_health_check`; a blocked check correctly persists `unhealthy`, which is visible in the UI.

## Scaling Considerations
- `socket.getaddrinfo` is a synchronous call inside async handlers. The same pattern is already used by SKILL.md fetches. With the default 2s health timeout and per-asset checks, DNS overhead is negligible relative to the HTTP request it gates.
- The allowlist sets are `lru_cache`d, so parsing happens once per process. `settings` is immutable per process, matching the existing `_trusted_domains()` cache assumption. (Implementer note: any test that mutates `settings.ssrf_extra_allowed_hosts` must call `.cache_clear()` on the cached functions, mirroring how SKILL.md tests handle `github_extra_hosts`.)
- No new persistent state, no new network round-trips beyond the DNS lookup that gates an existing request.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/utils/ssrf.py` | Shared SSRF validation (`is_safe_url`, `is_private_ip`, allowlist helpers) |
| `tests/unit/utils/test_ssrf.py` | Unit tests for the shared guard |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/services/skill_service.py` | ~60-192 | Remove local SSRF code; import shared `is_safe_url` (alias `_is_safe_url`) |
| `registry/api/agent_routes.py` | ~920-983 | Add `_outbound_url_allowed`; guard GET loop and HEAD fallback |
| `registry/health/service.py` | ~682, ~777, ~860, ~1072 | Validate `proxy_pass_url` and resolved endpoint before requests; guard tool fetch |
| `registry/core/config.py` | ~292 | Add `ssrf_protection_enabled`, `ssrf_extra_allowed_hosts` |
| `.env.example` | n/a | Document the two new variables |
| `docs/unified-parameter-reference.md` | n/a | Add the two new parameters |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (`ssrf.py` + guards + settings) | ~140 |
| New tests | ~160 |
| Modified code (refactor + call sites) | ~60 |
| Docs | ~20 |
| **Total** | **~380** |

## Testing Strategy
See `./testing.md`. In brief: unit tests for `is_safe_url` (private/loopback/link-local/reserved/metadata IPs, non-http scheme, no-hostname, DNS-resolves-to-private via monkeypatched `getaddrinfo`, allowlist bypass, feature-flag off), a regression run of the existing SKILL.md SSRF tests (must pass unchanged), and functional curl tests against `POST /agents/{path}/health` for an agent registered with a private-IP URL (expect `unhealthy` + SSRF detail) versus a public URL.

## Alternatives Considered

### Alternative 1: Validate at registration time (reject private URLs on register/update)
**Description:** Run `is_safe_url()` in the `AgentCard.url` / `proxy_pass_url` validators so bad URLs never get stored.
**Pros:** Fails fast; no stored landmines.
**Cons:** Rejects legitimate private-network deployments at registration even when the operator intends them; DNS at registration may differ from check time; does not protect already-stored assets. **Why rejected:** Too aggressive for a registry explicitly designed to front private MCP servers; the request-time guard plus allowlist is the right trust boundary. Could be added later as an opt-in.

### Alternative 2: Network-layer egress controls only (firewall / NAT)
**Description:** Block private ranges at the container/VPC level instead of in code.
**Pros:** Defense in depth; language-agnostic.
**Cons:** Not portable across deployments (local docker-compose, ECS, EKS, bare metal); does not block loopback within the same host/container; invisible to the app for auditing. **Why rejected:** Belongs as complementary defense, not a substitute. Out of scope.

### Alternative 3: Per-call bespoke validation inline (no shared module)
**Description:** Copy the check into each call site.
**Pros:** No refactor of `skill_service.py`.
**Cons:** Three divergent copies; drift risk; violates DRY. **Why rejected:** A single shared utility is safer and is the explicit acceptance criterion.

### Comparison Matrix

| Criteria | Chosen (shared guard at request time) | Alt 1 (register-time) | Alt 2 (network) | Alt 3 (inline copies) |
|----------|----------------------------------------|------------------------|------------------|------------------------|
| Protects stored assets | Yes | No | Yes | Yes |
| Allows legit private hosts | Yes (allowlist) | Hard | Hard | Yes |
| Single source of truth | Yes | Partial | n/a | No |
| Deployment portability | Yes | Yes | No | Yes |
| Implementation cost | Low | Low | Medium | Low (but high maintenance) |

## Rollout Plan
- **Phase 1 - Implementation** (out of scope for this skill): create `ssrf.py`, refactor `skill_service.py`, add guards and settings.
- **Phase 2 - Testing**: unit + functional tests; confirm SKILL.md regression suite passes.
- **Phase 3 - Deployment**: ship with `ssrf_protection_enabled=true` by default. Communicate to operators with private agents to populate `ssrf_extra_allowed_hosts`. Monitor WARNING-level SSRF-block logs for the first deployment window; a spike of legitimate blocks indicates an allowlist gap rather than an attack.
- **Rollback**: set `SSRF_PROTECTION_ENABLED=false` to revert to prior behavior without a code change.

## Open Questions
- Should `ssrf_protection_enabled=false` also disable SKILL.md SSRF validation, or only the new health/agent-card surfaces? **Recommendation:** only the new surfaces; SKILL.md validation should remain always-on (it predates this flag). The shared `is_safe_url` stays unconditional; the flag is applied only at the new call sites.
- Should the agent/server allowlist default to the SKILL.md trusted domains too? **Recommendation:** no - keep them independent so GitHub trust does not implicitly grant health-check trust.
- Do we want a metric/counter now, or is the WARNING log sufficient for v1? **Recommendation:** log-only for v1; add a counter if alerting need arises.

## References
- Existing SKILL.md SSRF guard: `registry/services/skill_service.py:94-192`
- `SkillContentSSRFError`: `registry/exceptions.py:194`
- Trusted-host allowlist precedent: `settings.github_extra_hosts` (`registry/core/config.py:292`)
- OWASP SSRF Prevention Cheat Sheet
- A2A agent card discovery (`/.well-known/agent-card.json`)
