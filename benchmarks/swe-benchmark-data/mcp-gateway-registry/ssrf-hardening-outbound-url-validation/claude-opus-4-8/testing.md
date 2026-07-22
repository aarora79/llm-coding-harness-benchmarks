# Testing Plan: SSRF Hardening -- Outbound URL Validation

*Created: 2026-07-21*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
Validates that the shared SSRF utility (`registry/utils/ssrf.py`) correctly blocks private IPs, cloud metadata, non-http schemes, and unresolvable hostnames while allowing trusted domains and public URLs. Also validates that all call sites integrate the check correctly, that registration endpoints reject unsafe URLs with 422, and that existing functionality is preserved for valid public URLs.

### Prerequisites
- [ ] MongoDB running locally (`docker ps | grep mongo`)
- [ ] Registry service running (`uv run python -m registry`)
- [ ] Auth server running (for proxy tests)
- [ ] Test environment configured (test settings auto-applied by conftest.py)

### Shared Variables
```bash
export REGISTRY_URL="http://localhost:8080"
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)
export AUTH_HEADER="Authorization: Bearer $ACCESS_TOKEN"
```

## 1. Functional Tests

### 1.1 Unit Tests -- `is_private_ip()`

**File:** `tests/unit/utils/test_ssrf.py`

```python
import pytest
from registry.utils.ssrf import is_private_ip


class TestIsPrivateIp:
    @pytest.mark.parametrize(
        "ip,expected",
        [
            # IPv4 private ranges
            ("10.0.0.1", True),
            ("10.255.255.255", True),
            ("172.16.0.1", True),
            ("172.31.255.255", True),
            ("192.168.0.1", True),
            ("192.168.255.255", True),
            # Loopback
            ("127.0.0.1", True),
            ("127.255.255.255", True),
            # Link-local
            ("169.254.0.1", True),
            ("169.254.169.254", True),
            # Reserved
            ("0.0.0.0", True),
            ("255.255.255.255", True),
            # Cloud metadata (explicit check)
            ("169.254.169.254", True),
            # IPv6 private/loopback/link-local
            ("::1", True),
            ("fe80::1", True),
            ("fd00::1", True),
            ("fc00::1", True),
            # IPv4-mapped IPv6
            ("::ffff:127.0.0.1", True),
            ("::ffff:10.0.0.1", True),
            ("::ffff:169.254.169.254", True),
            ("::ffff:192.168.1.1", True),
            # Public IPs -- should NOT be private
            ("8.8.8.8", False),
            ("1.1.1.1", False),
            ("93.184.216.34", False),
            ("104.16.132.229", False),
            ("2606:4700::1", False),
            ("2001:db8::1", True),  # documentation range, reserved
            # Invalid input -- treated as private (fail-closed)
            ("invalid", True),
            ("", True),
            ("not.an.ip", True),
        ],
    )
    def test_ip_classification(self, ip: str, expected: bool) -> None:
        assert is_private_ip(ip) == expected
```

### 1.2 Unit Tests -- `is_safe_url()`

**File:** `tests/unit/utils/test_ssrf.py`

```python
import socket
from unittest.mock import patch

import pytest
from registry.utils.ssrf import is_safe_url, get_trusted_domains


class TestIsSafeUrl:
    """Unit tests for the shared SSRF URL validation function."""

    # --- Scheme validation ---

    def test_blocks_ftp_scheme(self) -> None:
        assert is_safe_url("ftp://example.com/file") is False

    def test_blocks_file_scheme(self) -> None:
        assert is_safe_url("file:///etc/passwd") is False

    def test_blocks_gopher_scheme(self) -> None:
        assert is_safe_url("gopher://evil.com") is False

    def test_blocks_javascript_scheme(self) -> None:
        assert is_safe_url("javascript:alert(1)") is False

    def test_blocks_data_scheme(self) -> None:
        assert is_safe_url("data:text/html,<script>alert(1)</script>") is False

    def test_blocks_empty_scheme(self) -> None:
        assert is_safe_url("://example.com") is False

    # --- Hostname validation ---

    def test_blocks_empty_url(self) -> None:
        assert is_safe_url("") is False

    def test_blocks_no_hostname(self) -> None:
        assert is_safe_url("http://") is False

    def test_blocks_malformed_url(self) -> None:
        assert is_safe_url("not-a-url") is False

    # --- Trusted domains ---

    def test_allows_github_com(self) -> None:
        assert is_safe_url("https://github.com/org/repo") is True

    def test_allows_raw_githubusercontent(self) -> None:
        assert is_safe_url("https://raw.githubusercontent.com/org/repo/main/file") is True

    def test_allows_gitlab_com(self) -> None:
        assert is_safe_url("https://gitlab.com/group/project") is True

    def test_allows_bitbucket_org(self) -> None:
        assert is_safe_url("https://bitbucket.org/team/repo") is True

    def test_trusted_domains_case_insensitive(self) -> None:
        assert is_safe_url("https://GitHub.COM/org/repo") is True
        assert is_safe_url("https://GITLAB.com/group/project") is True

    def test_trusted_domain_not_subdomain_match(self) -> None:
        """Ensure evil.github.com is NOT treated as trusted."""
        with patch("registry.utils.ssrf.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))
            ]
            assert is_safe_url("https://evil.github.com/") is False

    # --- DNS resolution and IP checks ---

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_private_ip_10_range(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))
        ]
        assert is_safe_url("http://evil.com/path") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_private_ip_172_range(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.0.1", 443))
        ]
        assert is_safe_url("https://evil.com/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_loopback(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ]
        assert is_safe_url("http://localhost-alias.com/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_metadata_endpoint(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ]
        assert is_safe_url("http://metadata.internal/latest/meta-data/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_allows_public_ip(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        assert is_safe_url("https://example.com/") is True

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_dns_resolution_failure(self, mock_dns) -> None:
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
    def test_allows_all_public_ips(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
        ]
        assert is_safe_url("https://cdn.example.com/") is True

    # --- IPv6 edge cases ---

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_ipv6_loopback(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 80, 0, 0))
        ]
        assert is_safe_url("http://ipv6-loopback.com/") is False

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_blocks_ipv4_mapped_ipv6_private(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:10.0.0.1", 80, 0, 0))
        ]
        assert is_safe_url("http://mapped-ipv6.com/") is False

    # --- call_site parameter ---

    @patch("registry.utils.ssrf.socket.getaddrinfo")
    def test_call_site_passed_to_metric(self, mock_dns) -> None:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80))
        ]
        # Should not raise; call_site is informational
        assert is_safe_url("http://evil.com/", call_site="test_caller") is False


class TestGetTrustedDomains:
    def test_includes_builtin_defaults(self) -> None:
        get_trusted_domains.cache_clear()
        domains = get_trusted_domains()
        assert "github.com" in domains
        assert "gitlab.com" in domains
        assert "raw.githubusercontent.com" in domains
        assert "bitbucket.org" in domains

    def test_merges_github_extra_hosts(self) -> None:
        with patch(
            "registry.utils.ssrf.settings"
        ) as mock_settings:
            mock_settings.github_extra_hosts = "ghes.corp.com,git.internal"
            mock_settings.ssrf_additional_trusted_domains = ""
            get_trusted_domains.cache_clear()
            domains = get_trusted_domains()
            assert "ghes.corp.com" in domains
            assert "git.internal" in domains

    def test_merges_ssrf_additional_domains(self) -> None:
        with patch(
            "registry.utils.ssrf.settings"
        ) as mock_settings:
            mock_settings.github_extra_hosts = ""
            mock_settings.ssrf_additional_trusted_domains = "corp.example.com,internal.dev"
            get_trusted_domains.cache_clear()
            domains = get_trusted_domains()
            assert "corp.example.com" in domains
            assert "internal.dev" in domains

    def test_handles_empty_and_whitespace(self) -> None:
        with patch(
            "registry.utils.ssrf.settings"
        ) as mock_settings:
            mock_settings.github_extra_hosts = " , , "
            mock_settings.ssrf_additional_trusted_domains = ""
            get_trusted_domains.cache_clear()
            domains = get_trusted_domains()
            # Should only contain builtins, no empty strings
            assert "" not in domains
```

### 1.3 curl / HTTP Tests -- Registration-Time Validation

#### 1.3.1 Server Registration Blocked (private IP)

```bash
# Register a server with a private IP URL -- expect 422
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-private",
    "description": "SSRF test server",
    "proxy_pass_url": "http://10.0.0.5:8080/mcp"
  }'

# Expected: HTTP 422
# Expected body contains: "reason": "private_ip"
```

#### 1.3.2 Server Registration Blocked (metadata endpoint)

```bash
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-metadata",
    "description": "SSRF metadata test",
    "proxy_pass_url": "http://169.254.169.254/latest/meta-data/"
  }'

# Expected: HTTP 422
# Expected body contains: "reason": "private_ip" or "metadata_endpoint"
```

#### 1.3.3 Server Registration Blocked (non-http scheme)

```bash
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-ftp",
    "description": "SSRF scheme test",
    "proxy_pass_url": "ftp://evil.com/payload"
  }'

# Expected: HTTP 422
# Expected body contains: "reason": "invalid_scheme"
```

#### 1.3.4 Server Registration Allowed (public URL)

```bash
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-public",
    "description": "Valid public server",
    "proxy_pass_url": "https://mcp.example.com:443/mcp"
  }'

# Expected: HTTP 201 (or 200 depending on endpoint convention)
```

#### 1.3.5 Agent Registration Blocked (loopback)

```bash
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/agents/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-agent",
    "description": "SSRF agent test",
    "url": "http://127.0.0.1:9090"
  }'

# Expected: HTTP 422
```

#### 1.3.6 Skill Registration Blocked (private IP)

```bash
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/skills/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ssrf-test-skill",
    "description": "SSRF skill test",
    "skill_md_url": "http://192.168.1.100/SKILL.md"
  }'

# Expected: HTTP 422
```

### 1.4 Unit Tests -- Call Site Integration

```python
"""Tests verifying each call site correctly integrates is_safe_url()."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestMcpClientIntegration:
    @pytest.mark.asyncio
    @patch("registry.core.mcp_client.is_safe_url", return_value=False)
    async def test_detect_transport_returns_blocked(self, mock_ssrf) -> None:
        from registry.core.mcp_client import detect_server_transport

        result = await detect_server_transport("http://10.0.0.1:8080")
        assert result == "blocked"
        mock_ssrf.assert_called_once()

    @pytest.mark.asyncio
    @patch("registry.core.mcp_client.is_safe_url", return_value=False)
    async def test_get_tools_returns_none(self, mock_ssrf) -> None:
        from registry.core.mcp_client import get_tools_from_server_with_transport

        result = await get_tools_from_server_with_transport("http://10.0.0.1:8080")
        assert result is None
        mock_ssrf.assert_called_once()


class TestHealthServiceIntegration:
    @pytest.mark.asyncio
    @patch("registry.health.service.is_safe_url", return_value=False)
    async def test_health_check_marks_unhealthy(self, mock_ssrf) -> None:
        # Verify that a blocked URL causes the server to be marked UNHEALTHY
        # (specific setup depends on HealthService constructor mocking)
        pass  # Implementation depends on test fixtures


class TestAgentValidatorIntegration:
    @patch("registry.utils.agent_validator.is_safe_url", return_value=False)
    def test_reachability_returns_false(self, mock_ssrf) -> None:
        from registry.utils.agent_validator import _check_agent_reachability

        is_reachable, error = _check_agent_reachability("http://10.0.0.1:9090")
        assert is_reachable is False
        assert "SSRF" in error


class TestSkillScannerIntegration:
    @patch("registry.services.skill_scanner.is_safe_url", return_value=False)
    def test_download_raises_value_error(self, mock_ssrf) -> None:
        from registry.services.skill_scanner import SkillScanner

        scanner = SkillScanner()
        with pytest.raises(ValueError, match="SSRF"):
            scanner._download_skill_content("http://10.0.0.1/SKILL.md")
```

## 2. Backwards Compatibility Tests

### 2.1 Existing Servers with Public URLs Continue Working

```bash
# Register a server with a valid public URL
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "backwards-compat-test",
    "description": "Public server for compat test",
    "proxy_pass_url": "https://httpbin.org/get"
  }'

# Expected: HTTP 201 -- registration succeeds as before

# Verify health check runs successfully
sleep 10
curl -s "$REGISTRY_URL/servers/backwards-compat-test/health" \
  -H "$AUTH_HEADER"

# Expected: healthy status (URL is reachable and passes SSRF check)
```

### 2.2 Trusted Domains Still Work

```bash
# Register a skill pointing to GitHub (trusted domain)
curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/skills/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "trusted-domain-test",
    "description": "GitHub skill for compat test",
    "skill_md_url": "https://raw.githubusercontent.com/org/repo/main/SKILL.md"
  }'

# Expected: HTTP 201 -- trusted domain bypasses IP check
```

### 2.3 Configuration Defaults Preserve Prior Behavior

```python
"""Verify that with default settings, behavior matches pre-change."""

def test_default_ssrf_additional_trusted_domains_is_empty() -> None:
    from registry.core.config import Settings

    s = Settings()
    assert s.ssrf_additional_trusted_domains == ""


def test_existing_github_extra_hosts_still_honored() -> None:
    """If GITHUB_EXTRA_HOSTS was set before, those domains still bypass."""
    import os
    os.environ["GITHUB_EXTRA_HOSTS"] = "ghes.corp.com"
    from registry.utils.ssrf import get_trusted_domains

    get_trusted_domains.cache_clear()
    domains = get_trusted_domains()
    assert "ghes.corp.com" in domains
    del os.environ["GITHUB_EXTRA_HOSTS"]
```

### 2.4 Existing skill_service.py Behavior Unchanged

```python
"""Verify that skill_service still blocks private IPs after refactoring."""
from unittest.mock import patch
import socket


@patch("registry.utils.ssrf.socket.getaddrinfo")
def test_skill_service_still_blocks_private_via_shared_module(mock_dns) -> None:
    mock_dns.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))
    ]
    from registry.utils.ssrf import is_safe_url

    # This is what skill_service now calls internally
    assert is_safe_url("https://evil.com/SKILL.md", call_site="skill_service") is False
```

## 3. UX Tests

### 3.1 Error Message Clarity

```bash
# Verify error messages are actionable
RESPONSE=$(curl -s -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ux-test",
    "proxy_pass_url": "http://10.0.0.5:8080/mcp"
  }')

echo "$RESPONSE" | jq .

# Expected structure:
# {
#   "detail": "URL validation failed: proxy_pass_url resolves to a private IP address",
#   "field": "proxy_pass_url",
#   "url": "http://10.0.0.5:8080/mcp",
#   "reason": "private_ip"
# }

# Assertions:
# - "detail" is a human-readable sentence
# - "field" identifies which input field failed
# - "url" echoes back the problematic URL
# - "reason" is a machine-readable slug
```

### 3.2 Health Dashboard Shows Correct Status

```bash
# Register a server that will be blocked at health-check time
# (simulates DNS rebinding: registered with public IP, then DNS changes)
# In practice, mock the DNS in test environment

# After SSRF block in health loop, verify status endpoint shows UNHEALTHY
curl -s "$REGISTRY_URL/servers/rebind-test/health" \
  -H "$AUTH_HEADER" | jq '.status'

# Expected: "unhealthy"
```

## 4. Deployment Surface Tests

### 4.1 Docker Compose -- Environment Variable Picked Up

```bash
# Create extra_env file with the new variable
mkdir -p extra_env
echo "SSRF_ADDITIONAL_TRUSTED_DOMAINS=internal.corp.com,git.private.net" > extra_env/registry.env

# Start the stack
./build_and_run.sh

# Verify the variable is set inside the container
docker exec mcp-registry env | grep SSRF_ADDITIONAL_TRUSTED_DOMAINS

# Expected: SSRF_ADDITIONAL_TRUSTED_DOMAINS=internal.corp.com,git.private.net

# Verify the trusted domains include the configured values
curl -s "$REGISTRY_URL/skills/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "internal-corp-skill",
    "skill_md_url": "https://internal.corp.com/skills/SKILL.md"
  }'

# Expected: HTTP 201 (internal.corp.com is now trusted)

# Cleanup
rm extra_env/registry.env
```

### 4.2 Docker Compose -- Preflight Validates Reserved Name

```bash
# Attempt to set the reserved name via extra_env (should be blocked by preflight)
echo "SSRF_ADDITIONAL_TRUSTED_DOMAINS=evil.com" > extra_env/registry.env

# If preflight validator is updated to include this name:
./build_and_run.sh 2>&1 | grep -i "reserved"

# Note: This test only applies AFTER the reserved-name list is updated.
# If the preflight validator does not block it, the variable will be set
# (which is the intended behavior -- this variable IS user-configurable).
# The reserved-name list should NOT include SSRF_ADDITIONAL_TRUSTED_DOMAINS
# because operators are expected to set it.

rm extra_env/registry.env
```

### 4.3 Terraform -- Variable Wiring

```bash
# Verify Terraform variable exists
cd terraform/aws-ecs
grep -A 5 "ssrf_additional_trusted_domains" variables.tf

# Expected: variable definition with type = string and default = ""

# Verify it's wired into the ECS task definition
grep "SSRF_ADDITIONAL_TRUSTED_DOMAINS" main.tf

# Expected: environment variable block referencing the Terraform variable
```

### 4.4 Helm -- Values and Reserved Names

```bash
# Verify Helm value exists
grep -A 2 "ssrfAdditionalTrustedDomains\|SSRF_ADDITIONAL_TRUSTED_DOMAINS" charts/registry/values.yaml

# Verify it's NOT in reserved names (operators SHOULD be able to set it)
grep "SSRF_ADDITIONAL_TRUSTED_DOMAINS" charts/registry/templates/_helpers.tpl

# Expected: present in reserved names list (because it's chart-managed, not user-overridable via extraEnv)

# Run Helm unit tests
helm unittest charts/registry
```

### 4.5 Rollback Verification

```bash
# Deploy the new version
# Then rollback to the previous version (without ssrf.py)

# After rollback, verify:
# 1. Registry starts without errors (no import errors for missing ssrf module)
#    This is automatically handled because the old code has its own _is_safe_url()
# 2. Existing servers still work
curl -s "$REGISTRY_URL/health"
# Expected: HTTP 200
```

## 5. End-to-End API Tests

### 5.1 Full Registration-to-Health-Check Flow (Valid URL)

```bash
# Step 1: Register a server with a valid public URL
REGISTER_RESPONSE=$(curl -s -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "e2e-ssrf-valid",
    "description": "E2E test with valid URL",
    "proxy_pass_url": "https://httpbin.org/get"
  }')

echo "$REGISTER_RESPONSE" | jq .
# Expected: 201 Created

# Step 2: Wait for health check cycle
sleep 15

# Step 3: Check server health status
HEALTH_RESPONSE=$(curl -s "$REGISTRY_URL/servers/e2e-ssrf-valid/health" \
  -H "$AUTH_HEADER")

echo "$HEALTH_RESPONSE" | jq '.status'
# Expected: "healthy"

# Step 4: Cleanup
curl -s -X DELETE "$REGISTRY_URL/servers/e2e-ssrf-valid" \
  -H "$AUTH_HEADER"
```

### 5.2 Full Registration Rejection Flow (Private URL)

```bash
# Step 1: Attempt to register with private URL
REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "e2e-ssrf-private",
    "description": "E2E test with private URL",
    "proxy_pass_url": "http://10.0.0.5:8080/mcp"
  }')

HTTP_CODE=$(echo "$REGISTER_RESPONSE" | tail -1)
BODY=$(echo "$REGISTER_RESPONSE" | head -1)

# Assert HTTP 422
[ "$HTTP_CODE" = "422" ] && echo "PASS: Got 422" || echo "FAIL: Got $HTTP_CODE"

# Assert reason in body
echo "$BODY" | jq -e '.reason' && echo "PASS: Has reason field" || echo "FAIL: Missing reason"

# Step 2: Verify server was NOT registered
LIST_RESPONSE=$(curl -s "$REGISTRY_URL/servers" \
  -H "$AUTH_HEADER")

echo "$LIST_RESPONSE" | jq '.[] | select(.name == "e2e-ssrf-private")'
# Expected: empty (server not found)
```

### 5.3 Trusted Domain Override Flow

```bash
# Step 1: Set SSRF_ADDITIONAL_TRUSTED_DOMAINS (requires restart or env injection)
# In test environment, inject via conftest.py or direct settings override

# Step 2: Register a server on the newly-trusted domain
REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "e2e-trusted-override",
    "description": "Test trusted domain override",
    "proxy_pass_url": "http://internal.corp.com:8080/mcp"
  }')

HTTP_CODE=$(echo "$REGISTER_RESPONSE" | tail -1)

# If SSRF_ADDITIONAL_TRUSTED_DOMAINS includes "internal.corp.com":
# Expected: HTTP 201 (domain is trusted, skips IP check)

# If not configured:
# Expected: HTTP 422 (private IP or DNS failure)
```

### 5.4 Metrics Emission Verification

```bash
# Trigger a blocked request
curl -s -X POST "$REGISTRY_URL/servers/register" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "metrics-test",
    "proxy_pass_url": "http://10.0.0.1:8080/mcp"
  }'

# Check metrics endpoint for ssrf_blocked_total
curl -s "$REGISTRY_URL/metrics" | grep "ssrf_blocked_total"

# Expected output contains:
# ssrf_blocked_total{call_site="server_registration",reason="private_ip"} 1
```

## 6. Test Execution Checklist

- [ ] Section 1.1 (is_private_ip unit tests) passes
- [ ] Section 1.2 (is_safe_url unit tests) passes
- [ ] Section 1.3 (curl registration tests) passes
- [ ] Section 1.4 (call site integration tests) passes
- [ ] Section 2 (backwards compatibility) verified
- [ ] Section 3 (UX / error messages) verified
- [ ] Section 4.1 (Docker Compose) verified
- [ ] Section 4.3 (Terraform variable) verified
- [ ] Section 4.4 (Helm values) verified
- [ ] Section 4.5 (Rollback) verified
- [ ] Section 5 (E2E flows) verified
- [ ] Unit tests added under `tests/unit/utils/test_ssrf.py`
- [ ] Integration tests added under `tests/integration/test_ssrf_registration.py`
- [ ] `uv run pytest tests/ -n 8` passes with no regressions
- [ ] Existing `test_skill_service_ssrf_allowlist.py` still passes after refactoring
- [ ] No new Bandit findings (`uv run bandit -r registry/`)
- [ ] No new mypy errors (`uv run mypy registry/`)
