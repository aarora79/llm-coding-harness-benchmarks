# Testing Plan: SSRF Hardening for Agent-Card Fetch and Health-Check Paths

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing

Verify that outbound HTTP requests on the agent-card reachability probe and the server/agent health-check paths refuse URLs that resolve to private/loopback/link-local/reserved IPs, the cloud-metadata/container-credentials range (`169.254.0.0/16`, covering `169.254.169.254` and the Fargate `169.254.170.2`), IPv4-mapped IPv6 forms, and non-http(s) schemes; that public URLs and explicitly allowlisted hosts still work; that the existing SKILL.md SSRF behavior is unchanged after the guard is promoted to `registry/utils/ssrf.py`; and that monitor-only mode (`SSRF_ENFORCE=false`) logs/counts without blocking.

### Prerequisites

- [ ] Python env with dev deps installed (`uv sync`).
- [ ] Registry service running for functional tests (`docker compose up` or local run), reachable at `$REGISTRY_URL`.
- [ ] A valid access token for authenticated registration/health endpoints.
- [ ] Network access to a public test host (e.g. `https://example.com`) for positive cases.

### Shared Variables

```bash
export REGISTRY_URL="http://localhost:7860"
# Obtain per the repo's auth flow; adjust path to your token file.
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json 2>/dev/null)
```

---

## 1. Functional Tests

### 1.1 Unit Tests

Unit tests are the primary verification surface for this change (the guard logic is pure and deterministic). Place new files under `tests/unit/utils/` and `tests/unit/health/`, mirroring the existing `tests/unit/services/test_skill_service_ssrf_allowlist.py` conventions (patch settings, clear the `lru_cache`, mock `socket.getaddrinfo`).

#### 1.1.1 Shared guard: `tests/unit/utils/test_ssrf.py`

```python
"""Unit tests for the promoted shared SSRF guard (registry/utils/ssrf.py)."""
from unittest.mock import patch

import pytest


def _clear_cache() -> None:
    from registry.utils.ssrf import _trusted_domains
    _trusted_domains.cache_clear()


class TestScheme:
    @pytest.mark.parametrize("url", [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_",
        "not-a-url",
        "",
    ])
    @patch("registry.utils.ssrf.settings")
    def test_non_http_schemes_blocked(self, mock_settings, url):
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        from registry.utils.ssrf import is_safe_url
        assert is_safe_url(url) is False


class TestPrivateAndMetadataIPs:
    @pytest.mark.parametrize("resolved_ip", [
        "127.0.0.1",           # loopback
        "10.0.0.5",            # private A
        "192.168.1.10",        # private C
        "172.16.0.1",          # private B
        "169.254.169.254",     # EC2 IMDS
        "169.254.170.2",       # Fargate task-role credentials endpoint
        "0.0.0.0",             # unspecified
        "::1",                 # IPv6 loopback
        "fe80::1",             # IPv6 link-local
        "fd00::1",             # IPv6 ULA (private)
        "::",                  # IPv6 unspecified
        "::ffff:169.254.169.254",  # IPv4-mapped IPv6 metadata (must be unwrapped)
        "::ffff:10.0.0.1",         # IPv4-mapped IPv6 private
    ])
    @patch("registry.utils.ssrf.settings")
    def test_blocked_addresses(self, mock_settings, resolved_ip):
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        with patch("registry.utils.ssrf.socket.getaddrinfo") as m:
            m.return_value = [(None, None, None, None, (resolved_ip, 443))]
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url("https://attacker-controlled.example/") is False

    @patch("registry.utils.ssrf.settings")
    def test_public_ip_allowed(self, mock_settings):
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        with patch("registry.utils.ssrf.socket.getaddrinfo") as m:
            m.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url("https://example.com/") is True

    @patch("registry.utils.ssrf.settings")
    def test_any_private_in_multi_answer_blocks(self, mock_settings):
        """If ANY resolved address is private, the URL is blocked."""
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        with patch("registry.utils.ssrf.socket.getaddrinfo") as m:
            m.return_value = [
                (None, None, None, None, ("93.184.216.34", 443)),
                (None, None, None, None, ("10.0.0.5", 443)),
            ]
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url("https://mixed.example/") is False


class TestAllowlistMerge:
    @patch("registry.utils.ssrf.settings")
    def test_ssrf_allowed_hosts_bypasses_dns(self, mock_settings):
        """A host in SSRF_ALLOWED_HOSTS is trusted without DNS resolution."""
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = "internal-mcp.corp"
        _clear_cache()
        # getaddrinfo must NOT be called for an allowlisted host.
        with patch("registry.utils.ssrf.socket.getaddrinfo",
                   side_effect=AssertionError("getaddrinfo called for allowlisted host")):
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url("https://internal-mcp.corp/mcp") is True

    @patch("registry.utils.ssrf.settings")
    def test_github_and_ssrf_hosts_both_merged(self, mock_settings):
        mock_settings.github_extra_hosts = "ghes.corp"
        mock_settings.ssrf_allowed_hosts = "mcp.corp"
        _clear_cache()
        from registry.utils.ssrf import _DEFAULT_TRUSTED_DOMAINS, _trusted_domains
        assert _trusted_domains() == _DEFAULT_TRUSTED_DOMAINS | {"ghes.corp", "mcp.corp"}

    @patch("registry.utils.ssrf.settings")
    def test_unconfigured_internal_host_blocked(self, mock_settings):
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        with patch("registry.utils.ssrf.socket.getaddrinfo") as m:
            m.return_value = [(None, None, None, None, ("10.0.0.5", 443))]
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url("https://internal.example.com/foo") is False


class TestResolutionFailure:
    @patch("registry.utils.ssrf.settings")
    def test_unresolvable_host_fails_closed(self, mock_settings):
        import socket as _socket
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        with patch("registry.utils.ssrf.socket.getaddrinfo",
                   side_effect=_socket.gaierror("no such host")):
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url("https://does-not-resolve.invalid/") is False
```

Assertions:
- Non-http(s) schemes and malformed URLs return `False`.
- Every private/loopback/link-local/reserved/unspecified/metadata address (IPv4, IPv6, and IPv4-mapped IPv6) returns `False`.
- The Fargate `169.254.170.2` and IPv4-mapped `::ffff:169.254.169.254` are blocked (regression guard for the metadata-bypass finding).
- A public IP returns `True`; a mixed answer with any private IP returns `False`.
- `SSRF_ALLOWED_HOSTS` bypasses DNS (asserted by making `getaddrinfo` raise); the merged allowlist equals defaults + both configured sets.
- Resolution failure fails closed.

#### 1.1.2 Alternate IP-encoding vectors (resolver-dependent, mark clearly)

```python
class TestAlternateEncodings:
    """urlparse does not normalize these; glibc getaddrinfo does. These assert
    the guard blocks them when the resolver expands them to a blocked IP."""
    @pytest.mark.parametrize("host,resolved", [
        ("2130706433", "127.0.0.1"),   # decimal loopback
        ("0177.0.0.1", "127.0.0.1"),   # octal
        ("0x7f000001", "127.0.0.1"),   # hex
    ])
    @patch("registry.utils.ssrf.settings")
    def test_encoded_loopback_blocked(self, mock_settings, host, resolved):
        mock_settings.github_extra_hosts = ""
        mock_settings.ssrf_allowed_hosts = ""
        _clear_cache()
        with patch("registry.utils.ssrf.socket.getaddrinfo") as m:
            m.return_value = [(None, None, None, None, (resolved, 80))]
            from registry.utils.ssrf import is_safe_url
            assert is_safe_url(f"http://{host}/") is False
```

#### 1.1.3 Agent-card probe: `tests/unit/utils/test_agent_validator_ssrf.py`

```python
from unittest.mock import patch


class TestAgentCardReachabilitySSRF:
    def test_blocked_url_returns_unreachable_without_fetch(self):
        from registry.utils import agent_validator
        with patch("registry.utils.agent_validator.is_safe_url", return_value=False), \
             patch("registry.utils.agent_validator.httpx.get") as mock_get:
            ok, reason = agent_validator._check_endpoint_reachability("http://169.254.169.254")
            assert ok is False
            assert "SSRF" in reason
            mock_get.assert_not_called()   # no outbound request made

    def test_safe_url_proceeds_to_fetch(self):
        from registry.utils import agent_validator
        mock_resp = type("R", (), {"status_code": 200, "is_redirect": False})()
        with patch("registry.utils.agent_validator.is_safe_url", return_value=True), \
             patch("registry.utils.agent_validator.httpx.get", return_value=mock_resp) as mock_get:
            ok, reason = agent_validator._check_endpoint_reachability("https://example.com")
            assert ok is True
            mock_get.assert_called_once()
            # redirects must not be followed
            _, kwargs = mock_get.call_args
            assert kwargs.get("follow_redirects") is False
```

#### 1.1.4 Health path: `tests/unit/health/test_health_service_ssrf.py`

```python
import pytest
from unittest.mock import AsyncMock, patch

from registry.constants import HealthStatus


class TestHealthCheckSSRF:
    @pytest.mark.asyncio
    async def test_private_proxy_url_blocked_no_request(self):
        from registry.health.service import health_service
        client = AsyncMock()   # any outbound call would show up here
        with patch("registry.health.service.is_safe_url", return_value=False):
            healthy, status = await health_service._check_server_endpoint_transport_aware(
                client, "http://10.0.0.5:8000", {"supported_transports": ["streamable-http"]}
            )
        assert healthy is False
        assert status == HealthStatus.UNHEALTHY_SSRF_BLOCKED
        client.post.assert_not_awaited()
        client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_proxy_url_still_returns_missing_status(self):
        """Existing behavior preserved: empty URL -> UNHEALTHY_MISSING_PROXY_URL, not SSRF."""
        from registry.health.service import health_service
        client = AsyncMock()
        healthy, status = await health_service._check_server_endpoint_transport_aware(
            client, "", {}
        )
        assert healthy is False
        assert status == HealthStatus.UNHEALTHY_MISSING_PROXY_URL
```

Assertions:
- A private/metadata `proxy_pass_url` yields `UNHEALTHY_SSRF_BLOCKED` and makes no outbound `get`/`post`/`head`.
- The pre-existing missing-URL path is untouched.
- (Add a case asserting the guard is invoked via `asyncio.to_thread` if the implementation exposes it; at minimum assert no event-loop blocking by mocking `is_safe_url`.)

### 1.2 curl / HTTP Tests

Run against a live registry. These require the service and a valid token.

#### 1.2.1 Agent registration with an internal URL (agent-card probe)

```bash
curl -s -X POST "$REGISTRY_URL/agents/register" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "ssrf-probe-test",
        "path": "/agents/ssrf-probe-test",
        "url": "http://169.254.169.254",
        "description": "SSRF test",
        "version": "1.0.0"
      }' | jq .
```

Expected: registration succeeds (reachability is non-blocking) but the reachability field / server logs show the URL was blocked by SSRF protection. Grep the registry logs:

```bash
docker compose logs registry 2>&1 | grep -i "SSRF protection: refusing to probe agent endpoint"
```

Negative/positive control: register with `"url": "https://example.com"` and confirm no SSRF warning is logged and reachability is attempted.

#### 1.2.2 Agent health check on an internal URL

```bash
# After an agent with an internal URL is registered (path from 1.2.1):
curl -s -X POST "$REGISTRY_URL/agents/agents/ssrf-probe-test/health" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq .
```

Expected: `status` is `"unhealthy: blocked by SSRF protection"` and no outbound request reaches `169.254.169.254`. Assert:

```bash
STATUS=$(curl -s -X POST "$REGISTRY_URL/agents/agents/ssrf-probe-test/health" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq -r '.status')
[ "$STATUS" = "unhealthy: blocked by SSRF protection" ] && echo PASS || echo "FAIL: $STATUS"
```

#### 1.2.3 Server registration + immediate health check on an internal URL

```bash
curl -s -X POST "$REGISTRY_URL/register" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "name=ssrf-server-test" \
  -F "path=/ssrf-server-test" \
  -F "proxy_pass_url=http://10.0.0.5:8000" \
  -F "supported_transports=streamable-http" | jq .
```

Expected: the server is registered, and the immediate health check (dispatched on registration) reports `UNHEALTHY_SSRF_BLOCKED`. Verify via the service list / health endpoint and the log line `SSRF protection: blocked health check for proxy_pass_url 'http://10.0.0.5:8000'`.

### 1.3 CLI Tests

**Not Applicable** - this change adds no new CLI command and modifies no existing CLI invocation. The `cli/` tooling registers agents/servers through the same API paths already covered by the curl tests above; no CLI flag or output changes.

---

## 2. Backwards Compatibility Tests

### 2.1 Existing SKILL.md SSRF behavior unchanged (with repointed patch targets)

The guard moved to `registry/utils/ssrf.py` and `skill_service` re-exports it. The existing behavior must not change, but `tests/unit/services/test_skill_service_ssrf_allowlist.py` must be updated to patch the new module location (see LLD Step 2). Run:

```bash
uv run pytest tests/unit/services/test_skill_service_ssrf_allowlist.py -v
uv run pytest tests/unit/test_skill_service_github_auth.py -v
uv run pytest tests/unit/test_skill_routes_github_auth.py -v
uv run pytest tests/unit/api/test_skill_inline_content.py -v
```

Expected: all pass. The eight files that patch `registry.services.skill_service._is_safe_url` directly pass unchanged (the re-exported name still exists). The allowlist file passes after its `settings`/`getaddrinfo`/`cache_clear` targets are repointed to `registry.utils.ssrf`.

Assert the re-export symbols still resolve:

```bash
uv run python -c "from registry.services.skill_service import _is_safe_url, _is_private_ip, _trusted_domains, _DEFAULT_TRUSTED_DOMAINS; print('re-export OK')"
uv run python -c "from registry.utils.ssrf import is_safe_url, _is_safe_url; assert is_safe_url is _is_safe_url; print('alias OK')"
```

### 2.2 Public-URL registrations and health checks behave as before

- Registering an agent/server with a public `url`/`proxy_pass_url` produces the same reachability/health outcome as pre-change (positive control in 1.2).
- With `SSRF_ALLOWED_HOSTS` and `SSRF_ENFORCE` unset, only private/internal targets change behavior; public deployments see no difference. Verify by diffing the health status of a known-good public server before and after the change.

### 2.3 Monitor-only mode preserves prior behavior

```bash
# Deploy with SSRF_ENFORCE=false
export SSRF_ENFORCE=false
```

Expected: an internal `proxy_pass_url` is still contacted (behavior identical to pre-change) but a `WARNING` log and `ssrf_blocked_total` increment are emitted. This is the safety valve for fleets with internal servers.

### 2.4 Default preserves the trusted GitHub/GitLab domains

Confirm `github.com`, `gitlab.com`, `raw.githubusercontent.com`, `bitbucket.org` remain trusted (SKILL.md fetches to GHES via `github_extra_hosts` still work). Covered by `test_github_and_ssrf_hosts_both_merged` and the existing allowlist suite.

---

## 3. UX Tests

The change touches user-visible surfaces only indirectly (health status rendering and registration feedback).

- **Health dashboard rendering:** a server/agent that is SSRF-blocked shows a red "Unhealthy" indicator in `ServerCard`/`AgentCard`. Verify the frontend does not crash on the new `"unhealthy: blocked by SSRF protection"` status value - it is normalized by prefix (`healthStatus.ts`) to the `unhealthy` bucket. Load the dashboard with such a server present and confirm the card renders.
- **Reason legibility (recommended follow-up, per review):** if the tooltip enhancement is implemented, hovering the status dot should show "blocked by SSRF protection". If not implemented, this is a known gap documented in the LLD Observability section - the reason is only visible in logs/metrics.
- **Registration feedback:** confirm the behavior when registering an internal agent URL. Today the toast shows success (reachability is non-blocking); the recommended change is a warning toast. Note which behavior is in effect.
- **Error-message clarity:** the log line `SSRF protection: blocked ...` includes the offending URL and the path, and the health status string is human-readable.

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring

Confirm both new vars are passed through in list form (matching `GITHUB_EXTRA_HOSTS`):

```bash
grep -n "SSRF_ALLOWED_HOSTS\|SSRF_ENFORCE" docker-compose.yml docker-compose.podman.yml docker-compose.prebuilt.yml
# Expect: - SSRF_ALLOWED_HOSTS=${SSRF_ALLOWED_HOSTS:-}  and  - SSRF_ENFORCE=${SSRF_ENFORCE:-true}
```

Bring the stack up with an allowlist set and confirm the process sees it:

```bash
SSRF_ALLOWED_HOSTS="internal-mcp.corp" docker compose up -d registry
docker compose exec registry python -c "from registry.core.config import settings; print(settings.ssrf_allowed_hosts, settings.ssrf_enforce)"
# Expect: internal-mcp.corp True
```

### 4.2 Terraform / ECS wiring

Confirm the var flows through the four Terraform files that carry `GITHUB_EXTRA_HOSTS`:

```bash
grep -rn "ssrf_allowed_hosts\|SSRF_ALLOWED_HOSTS\|ssrf_enforce\|SSRF_ENFORCE" \
  terraform/aws-ecs/variables.tf \
  terraform/aws-ecs/main.tf \
  terraform/aws-ecs/modules/mcp-gateway/variables.tf \
  terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf
```

Expected: a root variable declaration, root->module wiring in `main.tf`, a module variable, and a container env injection in `ecs-services.tf`, mirroring `github_extra_hosts`. Validate the plan:

```bash
cd terraform/aws-ecs && terraform validate
```

(Do not run `terraform apply` here; validation only.)

### 4.3 Helm / EKS wiring

```bash
grep -n "ssrfAllowedHosts\|SSRF_ALLOWED_HOSTS\|ssrfEnforce\|SSRF_ENFORCE" \
  charts/mcpgw/values.yaml \
  charts/mcpgw/templates/deployment.yaml \
  charts/mcpgw/reserved-env-names.txt
helm template charts/mcpgw --set ssrfAllowedHosts="internal-mcp.corp" | grep -A1 "SSRF_ALLOWED_HOSTS"
```

Expected: `values.yaml` default (`ssrfAllowedHosts: ""`, `ssrfEnforce: true`), the deployment template maps them to env vars, and the reserved-env-names guard includes `SSRF_ALLOWED_HOSTS` / `SSRF_ENFORCE`.

### 4.4 Deploy and verify (staged rollout)

1. Deploy with `SSRF_ENFORCE=false` (monitor-only). Confirm `ssrf_blocked_total` increments for any internal server without changing its health status.
2. Inventory blocked hosts from the metric/logs; add legitimate ones to `SSRF_ALLOWED_HOSTS`; restart the task (required due to `lru_cache`).
3. Deploy with `SSRF_ENFORCE=true`. Confirm allowlisted internal servers are healthy and non-allowlisted internal targets report `UNHEALTHY_SSRF_BLOCKED`.

### 4.5 Rollback verification

Set `SSRF_ENFORCE=false` (or unset the new vars entirely) and restart. Confirm behavior reverts to pre-change (internal servers contacted again). Since the feature is additive and gated, rollback is a config change plus task restart - no schema or data migration to undo.

---

## 5. End-to-End API Tests

Multi-step scenario spanning registration and health across both hardened paths:

```bash
set -e
# 1. Register an internal server (should register, health blocked)
curl -s -X POST "$REGISTRY_URL/register" -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "name=e2e-ssrf" -F "path=/e2e-ssrf" \
  -F "proxy_pass_url=http://169.254.170.2/" \
  -F "supported_transports=streamable-http" > /dev/null

# 2. Trigger an immediate refresh and read health
curl -s -X POST "$REGISTRY_URL/refresh/e2e-ssrf" -H "Authorization: Bearer $ACCESS_TOKEN" > /dev/null
HEALTH=$(curl -s "$REGISTRY_URL/api/servers" -H "Authorization: Bearer $ACCESS_TOKEN" \
  | jq -r '.[] | select(.path=="/e2e-ssrf") | .status')
echo "server health: $HEALTH"   # expect unhealthy: blocked by SSRF protection

# 3. Add the host to the allowlist, restart, and re-check (manual step)
echo "Set SSRF_ALLOWED_HOSTS to include the host and restart, then re-run step 2 to confirm it is contacted."

# 4. Register a public server (control) and confirm it is contacted/healthy
curl -s -X POST "$REGISTRY_URL/register" -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "name=e2e-public" -F "path=/e2e-public" \
  -F "proxy_pass_url=https://example.com/" \
  -F "supported_transports=streamable-http" > /dev/null
```

Expected flow: internal server blocked at health; public server contacted normally; the metadata/credentials endpoint (`169.254.170.2`) is never reached (verify with a network capture or the absence of a corresponding outbound connection in logs).

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes - unit tests for the guard, agent-card probe, and health path; curl tests against a live registry.
- [ ] Section 2 (Backwards Compat) verified - existing skill SSRF suite green (with repointed patch targets); public URLs unchanged; monitor-only mode preserves behavior; default trusted domains intact.
- [ ] Section 3 (UX) verified - dashboard renders the new status without error; registration/refresh feedback behavior noted.
- [ ] Section 4 (Deployment) verified - Docker/Terraform/Helm wiring present for `SSRF_ALLOWED_HOSTS` and `SSRF_ENFORCE`; staged rollout and rollback exercised.
- [ ] Section 5 (E2E) verified - internal blocked, public healthy, metadata endpoint never contacted.
- [ ] IPv6 / IPv4-mapped / alternate-encoding vectors covered in unit tests (regression guard for the metadata-bypass finding).
- [ ] Unit tests added under `tests/unit/utils/` and `tests/unit/health/`.
- [ ] `uv run pytest tests/` passes with no regressions.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` clean on new/modified files.
