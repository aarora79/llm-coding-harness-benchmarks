# GitHub Issue: SSRF Hardening - Validate Outbound URLs in Federation Client

## Title
Add SSRF protection to federation client HTTP requests

## Labels
- security
- enhancement
- federation
- ssr

## Description

### Problem Statement
The MCP Gateway Registry's federation feature allows fetching agents and servers from peer registries. The federation client (`PeerRegistryClient` and `BaseFederationClient`) makes HTTP requests to configured peer registry endpoints without Server-Side Request Forgery (SSRF) validation. This creates a security vulnerability where:

1. An attacker with appropriate permissions could configure a malicious peer registry URL pointing to internal network addresses
2. The federation client would make HTTP requests to these internal endpoints
3. This could allow access to internal services, metadata endpoints, or other sensitive internal resources

### Current State
- The codebase already has robust SSRF protection in `registry/services/skill_service.py` via the `_is_safe_url()` function
- This protection validates URLs before fetching SKILL.md files and performing health checks
- Similar protection is NOT applied to federation client HTTP requests

### Vulnerable Endpoints/Functionality
1. **Peer Federation Client**
   - `registry/services/federation/peer_registry_client.py:fetch_agents()`
   - `registry/services/federation/peer_registry_client.py:fetch_servers()`
   - `registry/services/federation/peer_registry_client.py:fetch_security_scans()`
   
2. **Base Federation Client**
   - `registry/services/federation/base_client.py:_make_request()`
   - Used by: `registry/services/federation/asor_client.py`

### Proposed Solution
Apply the existing SSRF protection mechanisms to federation client HTTP requests:

1. Reuse or adapt the existing `_is_safe_url()` function from `skill_service.py`
2. Apply URL validation in `BaseFederationClient._make_request()` before making HTTP requests
3. Support trusted domain allowlist for known registries (similar to trusted GitHub/GitLab domains)
4. Log blocked requests for security auditing
5. Provide configuration options to disable validation in development environments if needed

### User Stories
- As a security engineer, I want federation client requests to validate URLs before making HTTP requests, so that internal services are protected from SSRF attacks
- As a system administrator, I want to configure trusted peer registries via allowlist, so that legitimate registries on private networks can still be accessed
- As a security auditor, I want logs of blocked federation requests, so I can monitor for potential SSRF attack attempts

### Acceptance Criteria
- [ ] Federation client validates all URLs before making HTTP requests
- [ ] Internal IP addresses (private, loopback, link-local) are blocked
- [ ] Cloud metadata endpoints (169.254.169.254) are blocked
- [ ] HTTP/HTTPS schemes are enforced
- [ ] Trusted registry domains can be configured via settings
- [ ] Blocked requests are logged with appropriate warning level
- [ ] Unit tests verify SSRF protection effectiveness
- [ ] Integration tests confirm federation still works with legitimate registries

### Out of Scope
- Modifying existing SKILL.md URL validation in `skill_service.py` (already protected)
- Changing agent card validation logic
- Updating health check endpoints
- API schema changes

### Dependencies
- None - leverages existing validation logic and extends it to federation

### Related Issues
- N/A

### Additional Context
The MCP Gateway Registry is designed for secure deployment in enterprise environments. With federation features enabling cross-organization agent sharing, ensuring proper SSRF protection is critical for maintaining security boundaries.

This hardening effort aligns with security best practices and helps protect against common SSRF attack vectors that target internal services, metadata endpoints, and other cloud infrastructure components.
