# Testing Plan: SSRF Hardening - Outbound URL Validation

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan covers testing for the new SSRF guard utility that validates outbound URLs against blocked IP ranges, and its integration into four code paths: server health checks, agent health checks, agent card validation, and MCP client connections.

### Prerequisites
- [ ] Registry running locally (docker compose or local dev)
- [ ] Access token available at `.oauth-tokens/ingress.json`
- [ ] Test server/agent registered with public URLs

### Shared Variables
```bash
export REGISTRY_URL="http://localhost:8000"
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)
```

## 1. Functional Tests

### 1.1 Unit Tests for `validate_outbound_url()`

Run these with:
```bash
uv run pytest tests/unit/utils/test_url_security.py -v
```

**Test cases for `tests/unit/utils/test_url_security.py`:**

```python
"""Tests for registry/utils/url_security.py - SSRF validation utility."""

import socket
import pytest
from registry.utils.url_security import (
    SSRFBlockedError,
    validate_outbound_url,
)


class TestBlockedIPv4Ranges:
    """Each test verifies that a blocked IP raises SSRFBlockedError."""

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
        with pytest.raises(SSRFBlockedError, match="SSRF protection"):
            validate_outbound_url(url)


class TestBlockedIPv6Ranges:
    """Each test verifies that a blocked IPv6 raises SSRFBlockedError."""

    @pytest.mark.parametrize("url", [
        "http://[::1]/health",
        "http://[0000::0000]/unspecified",
        "http://[fc00::1]/private",
        "http://[fdff::ffff]/private",
        "http://[fe80::1]/link-local",
        "http://[feff:ffff:ffff:ffff:ffff:ffff:ffff:ffff]/link-local",
        "http://[ff00::1]/multicast",
        "http://[ffff::1]/reserved",
    ])
    def test_blocked_ipv6(self, url: str) -> None:
        with pytest.raises(SSRFBlockedError, match="SSRF protection"):
            validate_outbound_url(url)


class TestAllowedPublicIPs:
    """Each test verifies that a public IP does NOT raise an exception."""

    @pytest.mark.parametrize("url", [
        "http://8.8.8.8/dns",
        "http://1.1.1.1/dns",
        "http://203.0.113.1/example",
        "http://198.51.100.1/test",
        "https://93.184.216.34/example-com",
    ])
    def test_allowed_public_ipv4(self, url: str) -> None:
        # Public IPs should pass validation (no exception)
        validate_outbound_url(url)

    @pytest.mark.parametrize("url", [
        "http://[2001:db8::1]/doc",
        "http://[2606:4700:4700::1111]/cloudflare",
    ])
    def test_allowed_public_ipv6(self, url: str) -> None:
        validate_outbound_url(url)


class TestAllowedHostnames:
    """Hostnames that resolve to public IPs should pass (mock DNS)."""

    @pytest.fixture(autouse=True)
    def mock_getaddrinfo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock socket.getaddrinfo to return a public IP for any hostname."""
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
        validate_outbound_url(url)

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
        with pytest.raises(SSRFBlockedError):
            validate_outbound_url(url)


class TestEdgeCases:
    """Edge cases and error conditions."""

    def test_empty_url(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract hostname"):
            validate_outbound_url("")

    def test_no_scheme(self) -> None:
        with pytest.raises(ValueError):
            validate_outbound_url("example.com/path")

    def test_invalid_url(self) -> None:
        with pytest.raises(ValueError):
            validate_outbound_url("not://a/valid/url/[invalid")

    def test_dns_failure_raises_gaierror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            raise socket.gaierror("Name or service not known")
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        with pytest.raises(socket.gaierror):
            validate_outbound_url("http://nonexistent.domain.xyz/path")

    def test_multiple_ips_all_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET, 0, 0, "", ("93.184.216.35", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        validate_outbound_url("https://example.com/path")

    def test_mixed_ips_one_blocked_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_gai(*args, **kwargs):
            return [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET, 0, 0, "", ("10.0.0.1", 0)),
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        with pytest.raises(SSRFBlockedError):
            validate_outbound_url("https://example.com/path")
```

### 1.2 Integration Tests: Health Check Endpoint

**Test the server health check endpoint is blocked for private IPs:**

```bash
# Register a server with a private IP URL
curl -X POST "${REGISTRY_URL}/api/servers/register" \
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
# Wait for the next health check interval, then check status
curl -s "${REGISTRY_URL}/ws/health_status" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Server health status shows "blocked: ssrf" (not "healthy" or connection errors).

### 1.3 Integration Tests: Agent Health Check Endpoint

```bash
# Register an agent with a private IP URL
curl -X POST "${REGISTRY_URL}/api/agents/register" \
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

### 1.4 Integration Tests: Agent Card Validation

```bash
# Register an agent with EC2 IMDS URL - reachability check should be blocked
curl -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-imds-agent",
    "url": "http://169.254.169.254/",
    "skills": [{"id": "test", "name": "test", "description": "test skill"}]
  }'
```

Expected: Agent card validation does not raise an error, but the reachability check returns a warning about blocked URL. Agent registration should still succeed (reachability is a warning, not an error).

### 1.5 Negative Tests

```bash
# Test with a legitimate external URL - should work
curl -s -X POST "${REGISTRY_URL}/api/servers/register" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "legit-test-server",
    "path": "/legit-test",
    "url": "https://httpbin.org/status/200",
    "proxy_pass_url": "https://httpbin.org/"
  }'

# Health check should proceed normally (not blocked)
curl -s -X POST "${REGISTRY_URL}/api/servers/legit-test/health-check" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected: Health check proceeds (result depends on httpbin.org availability, but NOT "blocked: ssrf").

## 2. Backwards Compatibility Tests

**Not Applicable** - This change does not modify any API contract, schema, CLI command, or default behavior. The validation only adds a rejection path for URLs that resolve to blocked IPs, which legitimate users should not be using. If a legitimate user has registered a server/agent with a private IP URL (e.g., internal service), the health check will return "blocked: ssrf" instead of attempting the connection. The registration itself is not affected.

## 3. UX Tests

**Not Applicable** - This change does not modify any UI surface. The only user-visible changes are health check status strings ("blocked: ssrf") and HTTP 403 responses on agent health checks, both of which are machine-readable.

## 4. Deployment Surface Tests

### 4.1 Docker wiring

**Not Applicable** - No environment variables, config parameters, or Dockerfile changes are needed.

### 4.2 Terraform / ECS wiring

**Not Applicable** - No Terraform changes needed. The SSRF guard is code-only with no configuration.

### 4.3 Helm / EKS wiring

**Not Applicable** - No Helm values changes needed.

### 4.4 Deploy and verify

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

### 4.5 Rollback verification

The change is self-contained (one new file, four integration points). Rollback is:

```bash
# Revert the git commit and redeploy
git revert <commit-hash>
docker compose up -d registry
```

Expected: All health checks return to previous behavior (including connections to private IPs).

## 5. End-to-End API Tests

### 5.1 Full SSRF Attack Simulation

Simulate an attacker who registers an agent with an EC2 IMDS URL and then triggers a health check to steal metadata tokens:

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

# Step 2: Trigger health check
curl -s -X POST "${REGISTRY_URL}/api/agents/evil-agent/health" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Step 3: Check that no metadata was returned
# The response should be HTTP 403, NOT the IMDS response
```

Expected: HTTP 403 with blocked URL detail. No metadata returned.

### 5.2 Redirect-based SSRF (if follow_redirects is enabled)

```bash
# Register a server that redirects to EC2 IMDS
# This tests whether redirects are properly validated
# Requires a test HTTP server that returns 302 to 169.254.169.254

# If follow_redirects=True: health check should still be blocked
# because the redirect target is validated before connection
# (verify that the LLD's redirect handling decision is correct)
```

## 6. Test Execution Checklist

- [ ] Section 1.1 (Unit tests for `validate_outbound_url`): All 20+ test cases pass
- [ ] Section 1.2 (Server health check integration): Private IP URL is blocked
- [ ] Section 1.3 (Agent health check integration): Private IP URL returns HTTP 403
- [ ] Section 1.4 (Agent card validation): Reachability check warns but does not block registration
- [ ] Section 1.5 (Negative tests): Legitimate URLs are not blocked
- [ ] Section 2 (Backwards Compat): Verified Not Applicable
- [ ] Section 3 (UX): Verified Not Applicable
- [ ] Section 4 (Deployment): Verified Not Applicable
- [ ] Section 5.1 (E2E attack simulation): IMDS URL is blocked
- [ ] Full test suite passes: `uv run pytest tests/` has no regressions