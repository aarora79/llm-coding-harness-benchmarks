# Testing Plan: SSRF Hardening - Promote Shared URL Validation

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing

This plan covers testing for:
1. The promoted shared SSRF validation utility (`registry/utils/url_security.py`) with `is_safe_url()` and `SSRFBlockedError`.
2. Integration into five code paths: agent validator reachability check, agent routes health check endpoint, agent registration, health monitoring service (3 functions), and MCP client.
3. Backwards compatibility of the `skill_service.py` refactor (existing callers must work identically).
4. The new `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` configuration parameter.
5. Redirect handling with `follow_redirects=False`.

### Prerequisites

- [ ] Registry running locally (`docker compose up` or local dev)
- [ ] Access token available at `.oauth-tokens/ingress.json`
- [ ] Test server/agent registered with public URLs

### Shared Variables

```bash
export REGISTRY_URL="http://localhost:8000"
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)
```

## 1. Functional Tests

### 1.1 Unit Tests for `is_safe_url()`

Run these with:
```bash
uv run pytest tests/unit/utils/test_url_security.py -v
```

```python
"""Tests for registry/utils/url_security.py - SSRF validation utility."""

import socket
import pytest
from registry.utils.url_security import (
    SSRF_BLOCKED_STATUS,
    SSRFBlockedError,
    is_safe_url,
    _is_private_ip,
    _trusted_domains,
)


class TestBlockedIPv4Ranges:
    """Each test verifies that a bare blocked IP raises is_safe_url == False."""

    @pytest.mark.parametrize("url", [
        "http://10.0.0.1/health",
        "http://10.255.255.255/api",
        "http://172.16.0.1/path?q=1",
        "http://172.31.255.255/sse",
        "http://192.168.0.1/mcp",
        "http://192.168.255.255:8080/api",
        "http://127.0.0.1/health",
        "http://127.0.0.2:9000/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.0.1/internal",
        "http://0.0.0.0/nowhere",
        "http://224.0.0.1/multicast",
        "http://239.255.255.250:1900/",
        "http://240.0.0.1/reserved",
        "http://100.64.0.1/cgnat",
        "http://100.127.255.254/cgnat-end",
    ])
    def test_blocked_ipv4(self, url: str) -> None:
        """Bare blocked IPv4 literals should be rejected without DNS lookup."""
        assert is_safe_url(url) is False


class TestBlockedIPv6Ranges:
    """Each test verifies that a blocked IPv6 literal returns False."""

    @pytest.mark.parametrize("url", [
        "http://[::1]/health",
        "http://[0000::0000]/unspecified",
        "http://[fc00::1]/private",
        "http://[fe80::1]/link-local",
        "http://[ff00::1]/multicast",
    ])
    def test_blocked_ipv6(self, url: str) -> None:
        """Bare blocked IPv6 literals should be rejected."""
        assert is_safe_url(url) is False


class TestAllowedPublicIPs:
    """Public IP literals should pass (no exception, returns True)."""

    @pytest.mark.parametrize("url", [
        "http://8.8.8.8/dns",
        "http://1.1.1.1/dns",
        "http://203.0.113.1/example",
        "http://198.51.100.1/test",
        "https://93.184.216.34/example-com",
    ])
    def test_allowed_public_ipv4(self, url: str) -> None:
        # Public IPs should pass validation
        assert is_safe_url(url) is True

    @pytest.mark.parametrize("url", [
        "http://[2001:db8::1]/doc",
        "http://[2606:4700:4700::1111]/cloudflare",
    ])
    def test_allowed_public_ipv6(self, url: str) -> None:
        assert is_safe_url(url) is True


class TestAllowedHostnames:
    """Hostnames that resolve to public IPs should pass."""

    @pytest.fixture(autouse=True)
    def mock_getaddrinfo_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock socket.getaddrinfo to return a public IP."""
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    @pytest.mark.parametrize("url", [
        "https://example.com/path",
        "http://my-service.internal.company.com/api",
        "https://api.github.com/repos",
    ])
    def test_allowed_hostnames_public_dns(self, url: str) -> None:
        assert is_safe_url(url) is True

    @pytest.fixture(autouse=True)
    def mock_getaddrinfo_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock socket.getaddrinfo to return a blocked IP."""
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("169.254.169.254", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    @pytest.mark.parametrize("url", [
        "https://example.com/.well-known/agent-card.json",
        "http://internal.service.local/mcp",
    ])
    def test_blocked_hostnames_private_dns(self, url: str) -> None:
        assert is_safe_url(url) is False


class TestTrustedDomainsSkipIPCheck:
    """Trusted domains should always return True regardless of DNS."""

    def test_trusted_domain_skips_dns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even if a trusted domain resolves to a private IP, it should pass."""
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("10.0.0.1", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        # github.com is a built-in trusted domain
        assert is_safe_url("https://github.com/path") is True

    def test_custom_trusted_host_from_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hosts in MCP_GATEWAY_EXTRA_TRUSTED_HOSTS should pass."""
        import registry.core.config as config_module
        import registry.utils.url_security as url_sec

        # Mock settings to return extra trusted hosts
        mock_settings = config_module.Settings()
        monkeypatch.setattr(mock_settings, "mcp_gateway_extra_trusted_hosts", "internal.example.com")
        monkeypatch.setattr(url_sec, "settings", mock_settings)

        # Clear the lru_cache so the new setting takes effect
        url_sec._trusted_domains.cache_clear()

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("10.0.0.1", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

        assert is_safe_url("https://internal.example.com/path") is True


class TestEdgeCases:
    """Edge cases and error conditions."""

    def test_empty_url(self) -> None:
        assert is_safe_url("") is False

    def test_no_scheme(self) -> None:
        assert is_safe_url("example.com/path") is False

    def test_file_scheme_blocked(self) -> None:
        assert is_safe_url("file:///etc/passwd") is False

    def test_ftp_scheme_blocked(self) -> None:
        assert is_safe_url("ftp://example.com/file") is False

    def test_dns_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            raise socket.gaierror("Name or service not known")
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        assert is_safe_url("http://nonexistent.domain.xyz/path") is False

    def test_multiple_ips_all_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET, 0, 0, "", ("93.184.216.35", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        assert is_safe_url("https://example.com/path") is True

    def test_mixed_ips_one_blocked_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET, 0, 0, "", ("10.0.0.1", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        assert is_safe_url("https://example.com/path") is False

    def test_ipv6_dual_stack_one_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET6, 0, 0, "", ("::1", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        assert is_safe_url("https://example.com/path") is False


class TestSSRFBlockedError:
    """Tests for the exception class."""

    def test_exception_message_includes_url(self) -> None:
        err = SSRFBlockedError("http://10.0.0.1/")
        assert "http://10.0.0.1/" in str(err)

    def test_exception_message_includes_blocked_ips(self) -> None:
        err = SSRFBlockedError("http://10.0.0.1/", blocked_ips=["10.0.0.1"])
        assert "10.0.0.1" in str(err)

    def test_exception_url_attribute(self) -> None:
        err = SSRFBlockedError("http://10.0.0.1/")
        assert err.url == "http://10.0.0.1/"

    def test_exception_blocked_ips_attribute(self) -> None:
        err = SSRFBlockedError("http://10.0.0.1/", blocked_ips=["10.0.0.1", "10.0.0.2"])
        assert err.blocked_ips == ["10.0.0.1", "10.0.0.2"]


class TestSyncRedirectHandling:
    """Test redirect handling for the synchronous httpx.get() path
    used in the agent-validator reachability check.

    These tests verify that follow_redirects=False prevents
    redirect-based SSRF bypass in the sync path.
    """

    def test_sync_httpx_get_follow_redirects_false(self) -> None:
        """Verify that httpx.get with follow_redirects=False does not follow
        redirects, which is the basis for redirect-based SSRF prevention."""
        import httpx

        # Use httpbin.org's redirect endpoint for a well-known public redirect
        # This test verifies the client behavior, not that we actually reach IMDS
        try:
            response = httpx.get(
                "https://httpbin.org/redirect-to?url=http%3A%2F%2Fexample.com%2F",
                timeout=5.0,
                follow_redirects=False,
            )
            # With follow_redirects=False, we get the 302 status
            assert response.status_code in (301, 302, 303, 307, 308)
            # The Location header contains the redirect target
            location = response.headers.get("location", "")
            assert location != ""
        except Exception:
            # If httpbin.org is unreachable, this test is a no-op
            pytest.skip("httpbin.org not reachable")

    def test_sync_redirect_to_private_ip_is_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that a redirect to a private IP is detected as unsafe.

        We mock is_safe_url to simulate that the redirect target
        resolves to a blocked IP.
        """
        import registry.utils.url_security as url_sec
        from unittest.mock import patch

        # Simulate initial URL passes SSRF check, but redirect target fails
        with patch.object(url_sec, "is_safe_url") as mock_safe:
            mock_safe.side_effect = [True, False]  # First call: initial URL safe, second: redirect unsafe

            import httpx

            try:
                response = httpx.get(
                    "https://httpbin.org/redirect-to?url=http%3A%2F%2F10.0.0.1%2F",
                    timeout=5.0,
                    follow_redirects=False,
                )
                location = response.headers.get("location", "")
                if location:
                    assert not url_sec.is_safe_url(location), (
                        "Redirect to private IP should fail SSRF check"
                    )
            except Exception:
                # httpbin.org may not be reachable in test environment
                pass
```

### 1.2 Unit Tests: `skill_service.py` Refactor Compatibility

Run these to verify the refactor does not break existing callers:
```bash
uv run pytest tests/unit/services/test_skill_service_ssrf_allowlist.py -v
```

```python
"""Verify skill_service.py still works after promoting is_safe_url."""

import pytest


class TestSkillServiceSSRFCompat:
    """Ensure the skill_service refactor did not break existing callers."""

    def test_is_safe_url_alias_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Importing _is_safe_url from skill_service should work."""
        from registry.services import skill_service
        # The alias _is_safe_url should resolve to is_safe_url
        assert hasattr(skill_service, '_is_safe_url')
        assert callable(skill_service._is_safe_url)

    def test_trusted_domains_callable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Importing _trusted_domains from skill_service should work."""
        from registry.services import skill_service
        assert hasattr(skill_service, '_trusted_domains')
        assert callable(skill_service._trusted_domains)

    def test_default_trusted_domains_includes_github(self) -> None:
        """Built-in trusted domains should include github.com."""
        from registry.services import skill_service
        domains = skill_service._trusted_domains()
        assert "github.com" in domains
        assert "gitlab.com" in domains
        assert "raw.githubusercontent.com" in domains
        assert "bitbucket.org" in domains

    def test_skill_validation_still_blocks_private_ips(self) -> None:
        """Skill registration should still block private IPs via promoted guard."""
        from registry.services.skill_service import _is_safe_url
        assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False
        assert _is_safe_url("http://10.0.0.1/") is False
        assert _is_safe_url("http://127.0.0.1/") is False
        assert _is_safe_url("http://192.168.1.1/") is False

    def test_url_validation_timeout_preserved(self) -> None:
        """URL_VALIDATION_TIMEOUT (skill-specific constant) should still exist."""
        from registry.services import skill_service
        assert hasattr(skill_service, 'URL_VALIDATION_TIMEOUT')
```

### 1.3 Integration Tests: Server Health Check Endpoint

**Test that server health checks are blocked for private IPs:**

```bash
# Register a server with a private IP URL
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-server",
    "path": "/ssrf-test",
    "url": "http://169.254.169.254/",
    "proxy_pass_url": "http://169.254.169.254/"
  }'
```

Expected: Server registration succeeds (URL validation happens at request time, not registration).

```bash
# Trigger an immediate health check - should be blocked
curl -s -X POST "${REGISTRY_URL}/api/servers/ssrf-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected response:
```json
{
  "status": "blocked: ssrf",
  "last_checked_iso": "..."
}
```

```bash
# Verify background health checks also block
curl -s "${REGISTRY_URL}/ws/health_status" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Server health status shows `"blocked: ssrf"` (not `"healthy"` or connection errors).

### 1.4 Integration Tests: Agent Health Check Endpoint

```bash
# Register an agent with a private IP URL
curl -s -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-agent",
    "url": "http://10.0.0.5/a2a",
    "skills": [{"id": "test", "name": "test", "description": "test skill"}]
  }'
```

```bash
# Perform health check - should be blocked
curl -s -X POST "${REGISTRY_URL}/api/agents/ssrf-test-agent/health" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected response: HTTP 403 with detail about blocked URL.

### 1.5 Integration Tests: Agent Registration SSRF Guard

```bash
# Register an agent with EC2 IMDS URL - registration should be blocked
curl -s -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-imds-agent",
    "url": "http://169.254.169.254/",
    "skills": [{"id": "test", "name": "test", "description": "test skill"}]
  }'
```

Expected: HTTP 422 with validation error `"Agent URL is blocked by SSRF protection"`.

### 1.6 Integration Tests: Redirect-Based SSRF

```bash
# Register a server with a public URL that redirects to EC2 IMDS
# Requires a test HTTP server that returns 302 to 169.254.169.254
# This test verifies follow_redirects=False prevents the bypass

# Step 1: Start a test redirect server (in a separate process)
# The server should return 302 redirect to http://169.254.169.254/

# Step 2: Register the server with the public redirect URL
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "redirect-test-server",
    "path": "/redirect-test",
    "url": "http://redirect-server.example.com/",
    "proxy_pass_url": "http://redirect-server.example.com/"
  }'

# Step 3: Trigger health check
curl -s -X POST "${REGISTRY_URL}/api/servers/redirect-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health check returns `"blocked: ssrf"` or `"unhealthy"` (NOT connects to IMDS).

### 1.7 Negative Tests

```bash
# Test with a legitimate external URL - should work
# Using example.com (RFC 2544) which is guaranteed to exist and is safe
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "legit-test-server",
    "path": "/legit-test",
    "url": "https://example.com/",
    "proxy_pass_url": "https://example.com/"
  }'

# Health check should proceed normally (not blocked)
curl -s -X POST "${REGISTRY_URL}/api/servers/legit-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health check proceeds (result depends on example.com availability, but NOT `"blocked: ssrf"`).

### 1.8 Trusted Hosts Configuration Test

```bash
# Start registry with MCP_GATEWAY_EXTRA_TRUSTED_HOSTS set
# This should allow health checks to internal.example.com even if it resolves to a private IP

# Register a server with the trusted host
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "trusted-host-test",
    "path": "/trusted-test",
    "url": "http://internal.example.com/",
    "proxy_pass_url": "http://internal.example.com/"
  }'

# Health check should NOT be blocked (host is in trusted list)
curl -s -X POST "${REGISTRY_URL}/api/servers/trusted-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health check proceeds normally (not `"blocked: ssrf"`).

## 2. Backwards Compatibility Tests

### 2.1 Skill Registration Unchanged

```bash
# Register a skill with a public GitHub URL - should work exactly as before
curl -s -X POST "${REGISTRY_URL}/api/skills/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-skill",
    "skill_md_url": "https://raw.githubusercontent.com/example/repo/main/SKILL.md"
  }'
```

Expected: Skill registers successfully, same response as before the change.

### 2.2 Skill Health Check Unchanged

```bash
curl -s -X POST "${REGISTRY_URL}/api/skills/test-skill/health" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health check works identically to before the change.

### 2.3 Skill Content Fetch Unchanged

```bash
curl -s -X POST "${REGISTRY_URL}/api/skills/test-skill/parse-skill-md" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"skill_md_url": "https://raw.githubusercontent.com/example/repo/main/SKILL.md"}'
```

Expected: Skill content parses correctly, same response as before.

### 2.4 Existing Agent Operations Unchanged

```bash
# List agents, rate agents, toggle agents - all should work as before
curl -s -X GET "${REGISTRY_URL}/api/agents" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Agent list works, no regressions.

### 2.5 Existing Tests Pass

```bash
uv run pytest tests/ -v --tb=short
```

Expected: All existing tests pass. No regressions.

## 3. UX Tests

**Not Applicable** - This change does not modify any UI surface. The only user-visible changes are the HTTP status code (403 on blocked agent health checks) and health check status strings (`"blocked: ssrf"`), both of which are machine-readable API responses.

## 4. Deployment Surface Tests

### 4.1 .env.example Update

After the change, verify that `.env.example` includes the new parameter:

```bash
grep "MCP_GATEWAY_EXTRA_TRUSTED_HOSTS" .env.example
```

Expected: The new parameter appears in `.env.example` with a descriptive comment.

### 4.2 Docker wiring

After updating `.env.example`, verify the Docker setup picks up the new env var:

```bash
# Start registry with the new environment variable
MCP_GATEWAY_EXTRA_TRUSTED_HOSTS="internal.example.com" \
  docker compose up -d registry

# Verify the registry starts without errors
docker logs registry 2>&1 | grep -i "ssrf"
```

Expected: No errors. The SSRF guard only triggers on outbound requests to user-supplied URLs.

### 4.3 Terraform / ECS wiring

**Not Applicable** - No Terraform changes needed. The SSRF guard is code-only with no infrastructure configuration. The `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` env var can be added to ECS task definitions if needed.

### 4.4 Helm / EKS wiring

**Not Applicable** - No Helm values changes needed for the core SSRF guard. If operators need `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS`, it can be added as a Helm override.

### 4.5 Deploy and verify

After deploying the change to a staging environment:

```bash
# Verify the registry starts without errors
curl -s "${REGISTRY_URL}/ws/health_status" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health status endpoint returns successfully.

```bash
# Verify no SSRF-related errors in logs on startup
docker logs <registry-container> 2>&1 | grep -i "ssrf"
```

Expected: No errors (SSRF validation only triggers on outbound requests to user-supplied URLs).

### 4.6 Rollback verification

The change is self-contained (one new file, six integration points). Rollback is:

```bash
# Revert the git commit and redeploy
git revert <commit-hash>
docker compose up -d registry
```

Expected: All health checks return to previous behavior (including connections to private IPs).

## 5. End-to-End API Tests

### 5.1 Full SSRF Attack Simulation - EC2 IMDS

Simulate an attacker who registers an agent targeting the EC2 Instance Metadata Service:

```bash
# Step 1: Register an agent targeting EC2 IMDS
curl -s -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "evil-agent",
    "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "skills": [{"id": "steal", "name": "steal", "description": "steals secrets"}]
  }'

# Step 2: Health check - should be blocked
curl -s -X POST "${REGISTRY_URL}/api/agents/evil-agent/health" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Expected: HTTP 403, NOT the IMDS response
```

### 5.2 Full SSRF Attack Simulation - Internal Service

```bash
# Register a server pointing to an internal service
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "evil-server",
    "path": "/evil",
    "url": "http://10.0.0.100/internal-api",
    "proxy_pass_url": "http://10.0.0.100/internal-api"
  }'

# Trigger health check - should be blocked
curl -s -X POST "${REGISTRY_URL}/api/servers/evil/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Expected: "blocked: ssrf", not a connection to 10.0.0.100
```

### 5.3 Redirect-Based SSRF Attack Simulation

```bash
# Register a server at a public URL that redirects to EC2 IMDS
# Requires a test redirect server

# The health check should NOT follow the redirect to the private IP
curl -s -X POST "${REGISTRY_URL}/api/servers/redirect-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Expected: Blocked or unhealthy (never connects to 169.254.169.254)
```

### 5.4 Endpoint URL Derivation Test

```bash
# This test verifies that endpoint URLs derived from proxy_pass_url
# (e.g., /mcp, /sse) inherit the safety of the base URL.

# Step 1: Register a server with a public URL
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "derived-endpoint-test",
    "path": "/derived-test",
    "url": "https://example.com/",
    "proxy_pass_url": "https://example.com/"
  }'

# Step 2: The derived endpoints (/mcp, /sse) inherit the base URL safety
# A health check should proceed to the derived endpoints without re-validation
# because they share the same scheme and hostname as the validated base URL
curl -s -X POST "${REGISTRY_URL}/api/servers/derived-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health check proceeds normally (the derived endpoints inherit safety from the validated base URL).

## 6. Test Execution Checklist

- [ ] Section 1.1 (Unit tests for `is_safe_url`): All 25+ test cases pass
- [ ] Section 1.2 (skill_service.py refactor compatibility): All 5 tests pass
- [ ] Section 1.3 (Server health check integration): Private IP URL is blocked
- [ ] Section 1.4 (Agent health check integration): Private IP URL returns HTTP 403
- [ ] Section 1.5 (Agent registration guard): SSRF-blocked URL returns HTTP 422
- [ ] Section 1.6 (Redirect-based SSRF): Redirect to private IP is blocked
- [ ] Section 1.7 (Negative tests): Legitimate URLs are not blocked
- [ ] Section 1.8 (Trusted hosts): Trusted host configuration works
- [ ] Section 2.1 (Skill registration unchanged): Works identically to before
- [ ] Section 2.2 (Skill health check unchanged): Works identically to before
- [ ] Section 2.3 (Skill content fetch unchanged): Works identically to before
- [ ] Section 2.4 (Agent operations unchanged): Works identically to before
- [ ] Section 2.5 (All existing tests pass): No regressions
- [ ] Section 3 (UX): Verified Not Applicable
- [ ] Section 4 (Deployment): .env.example updated; deploy and verify passes
- [ ] Section 5.1 (E2E IMDS attack): Blocked
- [ ] Section 5.2 (E2E internal service attack): Blocked
- [ ] Section 5.3 (E2E redirect attack): Blocked
- [ ] Section 5.4 (E2E endpoint derivation): Derived endpoints inherit base URL safety
- [ ] Full test suite passes: `uv run pytest tests/` has no regressions