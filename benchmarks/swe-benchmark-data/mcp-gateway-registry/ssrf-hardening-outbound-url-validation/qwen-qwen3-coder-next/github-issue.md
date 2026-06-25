# GitHub Issue: SSRF Hardening for Agent Card Fetch Endpoints

## Title
SSRF Hardening: Validate Outbound URLs on Agent Health Check and Pull-Card Endpoints

## Labels
- security
- SSRF
- bug
- hardening

## Description

### Problem Statement

The MCP Gateway Registry has multiple endpoints that make outbound HTTP requests to user-supplied URLs without adequate SSRF (Server-Side Request Forgery) protection. Attackers could potentially exploit these endpoints to:

1. Access internal services and metadata endpoints (e.g., 169.254.169.254, localhost, private networks)
2. Bypass firewall restrictions by using the registry as a proxy
3. Scan internal network topology
4. Access cloud provider metadata APIs to steal credentials

### Vulnerable Endpoints Identified

#### 1. Agent Health Check Endpoint (`POST /api/agents/{path}/health`)
- **Location:** `registry/api/agent_routes.py:883`
- **Issue:** The `_build_agent_health_urls` function constructs URLs from user-provided agent URLs and makes outbound HTTP requests using httpx without SSRF validation
- **Code Path:**
  - User registers agent with arbitrary `url` field
  - Health check endpoint fetches `/.well-known/agent-card.json` from that URL
  - No URL validation before making outbound request

#### 2. Agent Validator Endpoint Reachability Check
- **Location:** `registry/utils/agent_validator.py:212`
- **Issue:** `_check_endpoint_reachability` uses `httpx.get()` on user-supplied agent URLs without validation
- **Code Path:**
  - Agent validation calls `_check_endpoint_reachability(str(agent_card.url))`
  - User can register malicious agent URLs that trigger SSRF

#### 3. CLI Agent Health Check (`agent_mgmt.py`)
- **Location:** `cli/agent_mgmt.py:438`
- **Issue:** `_check_agent_health` uses `requests.get()` on user-provided agent URLs without SSRF validation

#### 4. Skill Service Health Check
- **Location:** `registry/services/skill_service.py`
- **Issue:** While SKILL.md URL validation exists, outbound fetches for resource discovery may still be vulnerable

### Proposed Solution

1. **Create centralized SSRF validation function** that validates URLs before making outbound requests
2. **Apply validation to all outbound HTTP requests** that use user-supplied URLs:
   - Agent health check endpoints
   - Agent card fetch operations
   - CLI health check utilities
3. **Block dangerous IP ranges:**
   - Private IPs: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
   - Loopback: 127.0.0.0/8
   - Link-local: 169.254.0.0/16
   - Documentation: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24
   - Cloud metadata: 169.254.169.254, 100.100.100.200 (Alibaba)
4. **Validate URL scheme** (only allow http/https)
5. **Check redirected URLs** (follow redirects but validate each hop)

### User Stories

- As a security engineer, I want to prevent SSRF attacks through agent health check endpoints so that internal services cannot be accessed by untrusted agents
- As a registry operator, I want to ensure agent URLs are validated before outbound requests so that the registry cannot be used as a proxy
- As a developer, I want to register agents without worrying about accidentally exposing internal endpoints so that the system handles validation automatically

### Acceptance Criteria

- [ ] Agent health check endpoint (`POST /api/agents/{path}/health`) blocks requests to private/internal IP addresses
- [ ] Agent validator (`_check_endpoint_reachability`) blocks requests to private/internal IP addresses
- [ ] CLI health check (`_check_agent_health`) blocks requests to private/internal IP addresses
- [ ] URLs that redirect to blocked IPs are also blocked
- [ ] Only http and https schemes are allowed
- [ ] Cloud metadata endpoints (169.254.169.254, etc.) are explicitly blocked
- [ ] Existing tests pass with new validation
- [ ] Documentation updated with security recommendations

### Out of Scope

- Rate limiting for health check endpoints (separate concern)
- Authentication/authorization for health check endpoints (already implemented)
- Full network-level DDoS protection
- Blocking DNS rebinding attacks (requires additional infrastructure)

### Dependencies

- No external dependencies required (uses Python standard library: socket, ipaddress, urllib.parse)

### Related Issues

- Skill service already has SSRF protection in `skill_service.py` as a reference implementation
