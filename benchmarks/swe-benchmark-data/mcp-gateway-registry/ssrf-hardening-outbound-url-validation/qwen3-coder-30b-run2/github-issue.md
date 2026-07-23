# GitHub Issue: SSRF Hardening - Validate Outbound URLs on Agent-Card Fetch and Health Check Endpoints

## Labels
- security
- enhancement

## Description

### Problem Statement
The MCP Gateway & Registry application has security vulnerabilities due to lack of SSRF (Server-Side Request Forgery) protection on outbound HTTP requests made to user-supplied URLs. Specifically, the agent-card fetch and health-check endpoints do not validate URLs before making HTTP requests, allowing attackers to potentially access internal services or resources.

### Proposed Solution
Implement comprehensive SSRF protection by promoting the existing `_is_safe_url()` utility function to a shared location and applying it to vulnerable endpoints that fetch user-supplied URLs for agent cards and server health checks.

### User Stories
- As an operator of the MCP Gateway & Registry, I want to prevent SSRF attacks that could allow attackers to access internal services through agent-card fetch and health-check endpoints.
- As a security auditor, I want to ensure that all outbound HTTP requests made to user-provided URLs are validated against known safe patterns.
- As a developer, I want to maintain backwards compatibility while strengthening security.

### Acceptance Criteria
- [ ] The `_is_safe_url()` function is promoted to a shared utility module
- [ ] Agent-card fetch endpoints validate URLs using the SSRF protection
- [ ] Server health-check endpoints validate URLs using the SSRF protection  
- [ ] Backwards compatibility is maintained for legitimate use cases
- [ ] The implementation follows existing security patterns in the codebase

### Out of Scope
- Changes to inbound API endpoints that accept user URLs (these already have SSRF protection)
- Modification of the internal authentication mechanisms
- Changes to the existing skill URL validation (already protected)

### Dependencies
- None

### Related Issues
- #1282 - Security audit finding for SSRF vulnerability in agent-card and health-check paths