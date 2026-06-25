# GitHub Issue: SSRF Hardening - Validate Outbound URLs on Agent Card Fetch

## Title
SSRF Hardening: Validate Outbound URLs on Agent Card Fetch (Health Check + Pull-Card Endpoints)

## Labels
- security
- enhancement
- vulnerability
- SSRF
- hardening

## Description

### Problem Statement
The MCP Gateway Registry is vulnerable to Server-Side Request Forgery (SSRF) attacks due to insufficient validation of user-supplied URLs when performing agent health checks and pulling agent cards. The vulnerable endpoints make outbound HTTP requests to URLs that can be controlled by authenticated users, potentially allowing attackers to access internal services, scan internal networks, or interact with internal systems.

### Vulnerable Endpoints
1. **POST `/api/agents/{path:path}/health`** - The health check endpoint fetches agent cards from URLs constructed from agent metadata. The `base_url` from agent registration is used to construct URLs that are fetched via HTTP GET/HEAD requests without validation.

2. **CLI command in `cli/agent_mgmt.py`** - The `test` and `test-all` commands perform health checks by fetching agent cards from user-provided URLs. While this requires authentication, authenticated users can still register malicious agent URLs.

### Attack Scenario
1. An authenticated attacker registers an agent with a malicious URL pointing to an internal service (e.g., `http://169.254.169.254/`, `http://localhost/`, or `http://internal-service/`)
2. When health checks are performed (either via the API endpoint or CLI commands), the system makes HTTP requests to these internal addresses
3. The attacker can potentially:
   - Access metadata services (AWS IMDS, cloud provider metadata)
   - Scan internal network services
   - Interact with internal APIs that should not be exposed
   - Exfiltrate sensitive data from internal systems

### Proposed Solution
Implement URL validation and allowlist/denylist mechanisms for all outbound HTTP requests made to fetch agent cards:

1. **URL Validation**: Validate that URLs use HTTP/HTTPS protocols, have valid hostnames, and do not point to internal/private IP addresses
2. **Denylist**: Block known dangerous URLs (localhost, loopback addresses, link-local addresses, private IP ranges)
3. **Allowlist**: Optionally allow only pre-approved domains (sudo-enabled)
4. **DNS Resolution**: Validate DNS names resolve to public IP addresses
5. **Logging & Alerting**: Log all blocked SSRF attempts and alert administrators
6. **Configuration**: Make validation configurable with appropriate defaults

## User Stories
- As a **Security Engineer**, I want all outbound URLs to be validated before being fetched so that SSRF vulnerabilities are prevented
- As a **Platform Administrator**, I want to be alerted when SSRF attempts are detected so that I can investigate potential attacks
- As a **Developer**, I want URL validation to be configurable so that I can test with different domains during development
- As a **Deployment Engineer**, I want SSRF protection to be enabled by default so that our production instances are secure out-of-the-box

## Acceptance Criteria
- [ ] Identify all endpoints that make outbound HTTP requests based on user-supplied URLs
- [ ] Implement comprehensive URL validation that blocks internal/private IP addresses
- [ ] Add configuration options for denylist, allowlist, and validation strictness
- [ ] Validate URLs before making HTTP requests in vulnerable endpoints
- [ ] Return appropriate error messages when invalid URLs are detected
- [ ] Log all blocked SSRF attempts with relevant context
- [ ] Add unit and integration tests to ensure validation works correctly
- [ ] Update documentation to describe SSRF protection mechanisms
- [ ] Ensure backward compatibility with legitimate agent URLs

## Out of Scope
- Input validation for other types of endpoints (not making outbound requests)
- Rate limiting or authentication improvements (separate security concerns)
- Agent registration validation (focus is on URL fetching, not registration)
- Other types of injection attacks (SQLi, XSS - separate concerns)
- Network-level firewalls or security groups (infra-level controls)

## Dependencies
- Python `httpx` library (already used for async HTTP requests)
- Python `ipaddress` standard library module for IP validation
- Pydantic for URL parsing and validation
- FastAPI for error handling and response formatting
- FastAPI Lock for dependency injection

## Related Issues
- N/A (new security finding)
