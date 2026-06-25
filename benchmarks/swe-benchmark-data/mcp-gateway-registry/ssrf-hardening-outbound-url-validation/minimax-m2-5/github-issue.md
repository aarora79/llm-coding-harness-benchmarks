# GitHub Issue: SSRF Hardening - Validate Outbound URLs

## Title
[Security] Add SSRF protection to outbound HTTP requests in health check and agent card fetch endpoints

## Labels
- security
- enhancement
- ssrf
- hardening

## Description

### Problem Statement
The MCP Gateway Registry currently makes outbound HTTP requests to user-supplied URLs without proper validation, exposing the service to Server-Side Request Forgery (SSRF) attacks. An attacker could:
- Probe internal network services (e.g., `http://localhost:8080`, `http://169.254.169.254/`)
- Access cloud metadata endpoints (AWS, GCP, Azure)
- Scan internal infrastructure
- Bypass network firewalls

Two key endpoints are affected:
1. **Health Check Endpoint** (`POST /agents/{path}/health`) - fetches agent card from `/.well-known/agent-card.json` on user-supplied URLs
2. **Health Service** (`registry/health/service.py`) - performs batch health checks against registered server URLs

### Proposed Solution
Implement URL validation middleware/functions that:
1. Block requests to private IP ranges (localhost, RFC 1918 addresses)
2. Block requests to cloud metadata endpoints
3. Block requests to internal network addresses
4. Provide a configurable allowlist for trusted domains
5. Use a well-tested SSRF protection library rather than rolling custom validation

### User Stories
- As a security engineer, I want to prevent attackers from using the gateway to probe internal infrastructure so that our production systems are not exposed to SSRF attacks.
- As an operator, I want to configure a list of allowed domains for health checks so that I can control which external services are reachable.
- As a developer, I want clear error messages when a URL fails validation so that debugging is straightforward.

### Acceptance Criteria
- [ ] Health check endpoint validates URLs before making outbound requests
- [ ] Cloud metadata endpoints (169.254.169.254, etc.) are blocked
- [ ] Private IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) are blocked
- [ ] Link-local addresses (169.254.0.0/16) are blocked
- [ ] Configuration option to allowlist specific domains
- [ ] Validation errors are logged with the blocked URL (without sensitive data)
- [ ] Existing functionality works when health check URLs are public (no breaking changes)
- [ ] Tests cover validation logic with both positive and negative cases

### Out of Scope
- Changes to the agent registration flow (URLs are already stored in the database)
- Changes to the nginx configuration or reverse proxy settings
- Implementing IP reputation filtering or rate limiting
- Adding WAF rules (handled at infrastructure level)

### Dependencies
- None required (use Python standard library `ipaddress` module)
- Consider optional `ssrf-filter` library for comprehensive protection

### Related Issues
- Related to security hardening efforts for MCP Gateway
- May relate to existing security scanning in `registry/services/agent_scanner.py`