# Testing Plan: SSRF Hardening for Agent Card Fetch Endpoints

*Created: 2026-06-24*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan covers the SSRF hardening implementation for all outbound HTTP requests made by the MCP Gateway Registry when interacting with agent endpoints. The testing validates that URLs are properly validated before making outbound requests, blocking access to private/internal IP addresses, cloud metadata endpoints, and other dangerous targets.

### Prerequisites
- [ ] Python 3.11+ environment with `uv` installed
- [ ] Local instance of the MCP Gateway Registry running
- [ ] Access to registry API endpoints
- [ ] Network access for testing (both internal and external)

### Shared Variables
```bash
# Registry configuration
export REGISTRY_URL="http://localhost"
export REGISTRY_API_BASE="${REGISTRY_URL}/api/agents"

# Test agent configuration
export TEST_AGENT_URL="https://example.com"
export TEST_AGENT_PATH="/test-agent"

# Authentication (for tests that require it)
export JWT_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json 2>/dev/null || echo "")

# SSRF test endpoints (internal/external)
export LOCALHOST_URL="http://127.0.0.1"
export PRIVATE_IP_URL="http://192.168.1.1"
export METADATA_URL="http://169.254.169.254"
export VALID_PUBLIC_URL="https://api.github.com"
```

## 1. Functional Tests

### 1.1 Unit Tests: SSRF Validation Functions

**File:** `tests/unit/utils/test_agent_ssrftools.py` (new file)

#### Test 1.1.1: Block Private IP Addresses
```python
def test_block_private_ip_10_range():
    """Private IP 10.x.x.x should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://10.0.0.1/health") is False
    assert _is_safe_url("http://10.255.255.255/health") is False
    assert _is_safe_url("http://10.1.2.3/.well-known/agent-card.json") is False


def test_block_private_ip_172_range():
    """Private IP 172.16.x.x - 172.31.x.x should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://172.16.0.1/health") is False
    assert _is_safe_url("http://172.31.255.255/health") is False
    # 172.15.x.x and 172.32.x.x should be allowed (not in private range)
    assert _is_safe_url("http://172.15.0.1/health") is True
    assert _is_safe_url("http://172.32.0.1/health") is True


def test_block_private_ip_192_168_range():
    """Private IP 192.168.x.x should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://192.168.0.1/health") is False
    assert _is_safe_url("http://192.168.1.1/health") is False
    assert _is_safe_url("http://192.168.255.255/health") is False
```

#### Test 1.1.2: Block Loopback and Link-Local IPs
```python
def test_block_loopback_ip():
    """Loopback addresses (127.x.x.x) should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://127.0.0.1/health") is False
    assert _is_safe_url("http://127.0.0.255/health") is False
    assert _is_safe_url("http://localhost/health") is False  # Resolves to 127.0.0.1


def test_block_link_local_ip():
    """Link-local addresses (169.254.x.x) should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://169.254.0.1/health") is False
    assert _is_safe_url("http://169.254.169.254/health") is False  # AWS metadata
```

#### Test 1.1.3: Block Cloud Metadata Endpoints
```python
def test_block_aws_metadata_endpoint():
    """AWS EC2 metadata endpoint should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False


def test_block_alibaba_metadata_endpoint():
    """Alibaba Cloud metadata endpoint should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://100.100.100.200/latest/meta-data/") is False
```

#### Test 1.1.4: Block Invalid URL Schemes
```python
def test_block_file_scheme():
    """file:// URLs should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("file:///etc/passwd") is False
    assert _is_safe_url("file:///var/log/secret.log") is False


def test_block_ftp_scheme():
    """ftp:// URLs should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("ftp://ftp.example.com/file.txt") is False


def test_block_data_scheme():
    """data:// URLs should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("data:text/plain;base64,SGVsbG8=") is False
```

#### Test 1.1.5: Trusted Domain Allowlist
```python
def test_allow_trusted_domain_github():
    """github.com should bypass IP check."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    # A DNS-resolvable GitHub IP should pass
    assert _is_safe_url("https://github.com/user/repo") is True


def test_allow_trusted_subdomain():
    """Subdomains of trusted domains should also pass."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("https://raw.githubusercontent.com/user/repo/main/README.md") is True


def test_allow_custom_trusted_domain():
    """Custom trusted domains (via config) should bypass IP check."""
    from registry.utils.agent_ssrftools import _is_safe_url
    from registry.core.config import settings
    
    custom_trusted = set(settings.agent_trusted_domains)
    # GHES instance on private network
    assert _is_safe_url("http://10.0.0.50/gitlab/group/project", trusted_domains=custom_trusted) is True
```

#### Test 1.1.6: URL Validation Function
```python
def test_validate_url_raises_on_invalid():
    """validate_url should raise exception for unsafe URLs."""
    from registry.utils.agent_ssrftools import validate_url
    from registry.exceptions import AgentUrlSSRFError
    
    with pytest.raises(AgentUrlSSRFError):
        validate_url("http://127.0.0.1/health")
    
    with pytest.raises(AgentUrlSSRFError):
        validate_url("file:///etc/passwd")
    
    with pytest.raises(AgentUrlSSRFError):
        validate_url("http://169.254.169.254/")


def test_validate_url_accepted_url():
    """validate_url should not raise for safe URLs."""
    from registry.utils.agent_ssrftools import validate_url
    from registry.exceptions import AgentUrlSSRFError
    
    # Should not raise
    validate_url("https://api.github.com")
    validate_url("http://example.com")
    validate_url("https://example.com/path?query=value")
```

#### Test 1.1.7: IPv6 Support
```python
def test_block_ipv6_loopback():
    """IPv6 loopback should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://[::1]/health") is False
    assert _is_safe_url("http://[0000:0000:0000:0000:0000:0000:0000:0001]/health") is False


def test_block_ipv6_link_local():
    """IPv6 link-local addresses should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://[fe80::1]/health") is False
    assert _is_safe_url("http://[fe80::ffff:ffff:ffff:ffff]/health") is False


def test_block_ipv6_unique_local():
    """IPv6 unique local addresses (fc00::/7) should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    assert _is_safe_url("http://[fc00::1]/health") is False
    assert _is_safe_url("http://[fdf8::1]/health") is False
```

### 1.2 curl / HTTP Tests: API Endpoints

**Test 1.2.1: Agent Health Check Blocks Private IPs**
```bash
# First, register an agent with a private IP (if allowed by validation)
# Then attempt health check - should be blocked at validation, not execution

# Expected: If agent is registered (validation allows it), health check should fail with 400/403
curl -X POST http://localhost/api/agents/${TEST_AGENT_PATH}/health \
  -H "Authorization: Bearer ${JWT_TOKEN}" 2>/dev/null

# Expected response indicates SSRF block or unreachable (not a successful connection to private IP)
```

**Test 1.2.2: Agent Health Check Allows Public URLs**
```bash
# Register agent with public URL
export VALID_AGENT_PATH="/valid-agent"
curl -X POST http://localhost/api/agents/register \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Valid Agent",
    "url": "https://example.com",
    "path": "'${VALID_AGENT_PATH}'",
    "description": "Test agent",
    "visibility": "public"
  }'

# Health check should work (may fail for connection, but not for SSRF)
curl -X POST http://localhost/api/agents/${VALID_AGENT_PATH}/health \
  -H "Authorization: Bearer ${JWT_TOKEN}"
```

**Test 1.2.3: Agent Card Fetch Validates URLs**
```bash
# Test that the agent validator uses SSRF protection
# This is typically tested via the health check endpoint path
```

### 1.3 CLI Tests

**Test 1.3.1: CLI Health Check Blocks Private IPs**
```bash
# Expected behavior: CLI should validate URL before making request
# The CLI test would mock the SSRF check or use test fixtures

# Test with mock to verify SSRF check is called
uv run python cli/agent_mgmt.py test /blocked-agent \
  --base-url ${REGISTRY_URL} \
  --token-file .oauth-tokens/ingress.json
```

**Test 1.3.2: CLI Lists Agents Successfully**
```bash
# Verify CLI still works after SSRF changes
uv run python cli/agent_mgmt.py list \
  --base-url ${REGISTRY_URL} \
  --token-file .oauth-tokens/ingress.json
```

## 2. Backwards Compatibility Tests

### Test 2.1: Pre-Change Request Shapes Still Accepted
```python
def test_existing_agent_urls_not_affected():
    """Agents registered before SSRF protection should still work if valid."""
    # Test with a valid agent URL that would have been registered before SSRF protection
    # The SSRF check should pass because it's a valid public URL
    pass


def test_health_check_response_format_unchanged():
    """Health check response format should remain the same."""
    from registry.api.agent_routes import check_agent_health
    
    # The response shape should be identical for both successful and blocked requests
    # Only the status and detail fields would differ
    pass
```

### Test 2.2: CLI Without New Flags Behaves As Before
```python
def test_cli_no_ssrf_flags():
    """CLI should work without new SSRF configuration."""
    # Run CLI commands without any new flags
    # Expected: Commands execute normally with default settings
    pass
```

### Test 2.3: Defaults Preserve Prior Behavior
```python
def test_default_trusted_domains():
    """Default trusted domains should maintain existing functionality."""
    from registry.core.config import settings
    
    assert "github.com" in settings.agent_trusted_domains
    assert "gitlab.com" in settings.agent_trusted_domains
    # These are the trusted domains that existed implicitly before
```

## 3. UX Tests

### Test 3.1: Web UI Flows
```python
def test_health_check_error_message():
    """Error messages should be clear and not leak internal details."""
    # When SSRF blocks a URL, error message should be generic:
    # "URL validation failed - access denied"
    # NOT: "Blocked private IP 127.0.0.1"
    
    # This is tested via API response parsing
    pass


def test_cli_output_clear():
    """CLI error messages should be clear."""
    # Test that CLI output for blocked SSRF attempts is user-friendly
    pass
```

### Test 3.2: CLI Output / Error Message Clarity
```bash
# Test error handling in CLI
uv run python cli/agent_mgmt.py test /invalid-agent \
  --base-url ${REGISTRY_URL} \
  --token-file .oauth-tokens/ingress.json
```

## 4. Deployment Surface Tests

### 4.1 Docker Wiring

**File:** `Dockerfile` (verify)

**Test 4.1.1: Environment Variables Propagated**
```bash
# Build and run Docker container with custom trusted domains
docker build -t mcp-gateway-test .
docker run -e AGENT_TRUSTED_DOMAINS="github.com,gitlab.com,internal.example.com" \
  mcp-gateway-test

# Expected: Container starts and uses custom trusted domains
```

**Test 4.1.2: Default Configuration Works**
```bash
# Run without custom environment variables
docker run mcp-gateway-test

# Expected: Container starts with default trusted domains
```

### 4.2 Terraform / ECS Wiring

**File:** `terraform/` (verify)

**Test 4.2.1: Terraform Variables Defined**
```hcl
# In terraform/variables.tf, add:
variable "agent_trusted_domains" {
  description = "Trusted domains for agent SSRF protection"
  type        = list(string)
  default     = ["github.com", "gitlab.com"]
}
```

**Test 4.2.2: ECS Task Definition**
```json
{
  "environment": [
    {
      "name": "AGENT_TRUSTED_DOMAINS",
      "value": "github.com,gitlab.com"
    }
  ]
}
```

### 4.3 Helm / EKS Wiring

**File:** `charts/mcp-gateway/values.yaml` (verify)

**Test 4.3.1: Helm Values Configuration**
```yaml
agent:
  trustedDomains:
    - github.com
    - gitlab.com
  healthCheckTimeout: 10
```

**Test 4.3.2: Helm Template Renders Correctly**
```bash
helm template mcp-gateway ./charts/mcp-gateway \
  --set agent.trustedDomains[0]=github.com \
  --set agent.trustedDomains[1]=gitlab.com

# Expected: Rendered YAML includes trusted domains in config
```

### 4.4 Deploy and Verify

```bash
# Deploy to staging environment
make deploy-staging

# Run SSRF test suite
make test-ssrf

# Verify health check endpoint works with valid URLs
curl -X POST ${REGISTRY_URL}/api/agents/${VALID_AGENT_PATH}/health \
  -H "Authorization: Bearer ${JWT_TOKEN}"

# Verify SSRF blocks are logged
kubectl logs -l app=mcp-gateway | grep "SSRF blocked"
```

### 4.5 Rollback Verification

```bash
# If SSRF protection causes issues, rollback is simple:
# 1. Remove new validation code from vulnerable endpoints
# 2. Remove agent_ssrftools.py module
# 3. Remove AgentUrlSSRFError exception

# Recovery steps documented in:
# docs/deployment/rollback-ssrf-protection.md
```

## 5. End-to-End API Tests

### Test 5.1: Full Agent Registration with SSRF Validation

```python
async def test_e2e_agent_registration_blocks_ssrf_url():
    """Agent registration should block SSRF URLs at validation time."""
    import httpx
    from registry.utils.agent_ssrftools import validate_url
    from registry.exceptions import AgentUrlSSRFError
    
    # Test that SSRF validation is called during registration
    malicious_url = "http://127.0.0.1/admin"
    
    # Validation should fail
    with pytest.raises(AgentUrlSSRFError):
        validate_url(malicious_url)
```

### Test 5.2: Health Check Chain with Blocked URL

```python
async def test_health_check_skips_blocked_url():
    """Health check should skip blocked URLs and try fallback."""
    # The health check endpoint tries multiple URLs in sequence
    # If primary is blocked by SSRF, it should try fallback (registered URL)
    
    # This is tested by checking that the endpoint handles SSRF blocks gracefully
    pass
```

### Test 5.3: Agent Card Fetch with Redirect to Blocked IP

```python
def test_redirect_to_blocked_ip():
    """Redirect chains should validate each hop."""
    import responses
    from registry.utils.agent_ssrftools import _is_safe_url
    from urllib.parse import urlparse
    
    # Simulate a server that redirects to a private IP
    # Step 1: Original URL is safe (public)
    # Step 2: Server redirects to 127.0.0.1 (blocked)
    
    # The validation should catch the final destination
    assert _is_safe_url("http://127.0.0.1/health") is False
```

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes - All SSRF validation tests pass
- [ ] Section 2 (Backwards Compat) verified - Existing behaviors preserved
- [ ] Section 3 (UX) verified - Error messages are clear and generic
- [ ] Section 4 (Deployment) verified - Configuration works in all environments
- [ ] Section 5 (E2E) verified - Full agent workflows work end-to-end
- [ ] Unit tests added under `tests/unit/utils/test_agent_ssrftools.py`
- [ ] Integration tests added under `tests/integration/agent_ssrftest.py`
- [ ] `uv run pytest tests/unit/utils/test_agent_ssrftools.py` passes with no regressions
- [ ] `uv run pytest tests/integration/agent_ssrftest.py` passes with no regressions
- [ ] `uv run pytest -x` (full test suite) passes with no regressions
- [ ] Docker build succeeds
- [ ] Helm chart renders without errors
- [ ] Terraform plan shows no unexpected changes

## 7. SSRF-Specific Test Scenarios

### Test 7.1: Metadata Endpoint Detection
```python
def test_aws_metadata_detection():
    """Verify AWS metadata endpoint is blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url, _is_cloud_metadata_ip
    
    assert _is_cloud_metadata_ip("169.254.169.254") is True
    assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False


def test_alibaba_metadata_detection():
    """Verify Alibaba metadata endpoint is blocked."""
    from registry.utils.agent_ssrftools import _is_cloud_metadata_ip
    
    assert _is_cloud_metadata_ip("100.100.100.200") is True
```

### Test 7.2: DNS Resolution with Malicious Hostname

```python
def test_dns_resolves_to_blocked_ip():
    """If domain resolves to blocked IP, should be blocked."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    # We can't control DNS in tests, so we mock:
    # - domain.com resolves to 127.0.0.1
    # - Validation should block based on resolved IP
    
    # Test with mocked socket.getaddrinfo to return blocked IP
    pass
```

### Test 7.3: URL Encoding Bypass Attempts

```python
def test_url_encoding_bypass_blocked():
    """URL encoding should not bypass SSRF checks."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    # These should all be blocked after decoding
    assert _is_safe_url("http://127.0.0.1/health") is False
    assert _is_safe_url("http://%31%32%37%2e%30%2e%30%2e%31/health") is False  # Percent encoding


def test_idn_bypass_blocked():
    """International domain names should be validated after conversion."""
    from registry.utils.agent_ssrftools import _is_safe_url
    
    # IDN domains should be converted to punycode before validation
    pass
```
