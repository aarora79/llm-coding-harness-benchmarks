# Expert Review: SSRF Protection for Outbound URLs

*Created: 2025-06-25*
*Author: Claude (minimax-m2.5)*
*Related LLD: `./lld.md`*

## Review Panel

| Role | Reviewer | Focus |
|------|----------|-------|
| Frontend Engineer | Pixel | UI/UX, components, state, API integration |
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

---

## Frontend Review (Pixel)

### Strengths
- The design provides clear error messages through HTTP 400 responses
- Block reasons are structured enums, making UI display straightforward
- No changes to public API surface, so no frontend changes required

### Concerns
- Error messages from the backend should be sanitized - attackers could learn about block reasons
- The validation happens server-side, which is correct, but health check behavior change could confuse operators

### New Libraries / Infra Dependencies Required
- None. Uses Python standard library `ipaddress` module.

### Better Alternatives Considered
- None - the approach is appropriate for this use case.

### Recommendations
1. Consider adding a health check status for "blocked_by_ssrf" distinct from "unhealthy" so operators know why an agent appears unhealthy
2. Ensure error messages don't leak internal allowlist configuration

### Questions for Author
- Will blocked agents show in listings with a specific health status, or be filtered out entirely?

### Verdict: APPROVED WITH CHANGES

---

## Backend Review (Byte)

### Strengths
- Clean separation of concerns with a dedicated `ssrf_protection.py` module
- Uses Pydantic settings pattern consistent with the rest of the codebase
- Validation function is stateless and easily testable
- Provides granular control via individual toggle flags for different protection types

### Concerns
- **Performance**: URL validation adds a small overhead, but it's negligible (<1ms)
- **Code placement**: The validation is imported at module level in `agent_routes.py` and `health/service.py`, which could cause circular import issues if not careful
- **Error handling in loop**: The code continues to the next URL on block, which might mask issues - consider failing fast

### New Libraries / Infra Dependencies Required
- None required. Uses Python standard library only.

### Better Alternatives Considered
1. **Middleware approach**: Could implement as FastAPI middleware to catch all outbound requests generically
   - Why not chosen: More complex, harder to configure per-endpoint

2. **DNS resolution validation**: Could resolve hostname to IP and validate both
   - Why not chosen: Adds latency and complexity; direct IP blocking covers most cases

### Recommendations
1. Add caching for repeated validations if the same URLs are checked frequently
2. Consider making the health check fail immediately (not continue to next URL) when SSRF protection triggers
3. Ensure `allowed_domains` configuration handles wildcards (e.g., `*.example.com`)

### Questions for Author
- How does this integrate with the existing timeout configuration for health checks?
- Should the IP ranges be configurable or hardcoded with known safe defaults?

### Verdict: APPROVED WITH CHANGES

---

## SRE/DevOps Review (Circuit)

### Strengths
- Configuration via environment variables matches existing patterns
- Logging of blocked requests enables security monitoring
- No new infrastructure components required
- Works with existing health check infrastructure

### Concerns
- **Observability**: Need to ensure the WARNING level logs are picked up by the monitoring stack
- **Deployment**: No explicit handling of what happens when SSRF is disabled vs. enabled during a deploy
- **Rollback**: Health check behavior change could cause false alarms during rollout

### New Libraries / Infra Dependencies Required
- None.

### Better Alternatives Considered
- None identified - standard library approach is appropriate.

### Recommendations
1. Add metrics `ssrf_validation_total{result="blocked"}` to Prometheus metrics
2. Document the behavior change in release notes ("blocked URLs now marked as unhealthy")
3. Consider adding ahealth check-specific setting to disable SSRF for internal-only deployments (with clear warning)
4. Ensure logs don't contain full URLs to avoid credential leakage

### Questions for Author
- How does this interact with existing health check timeout settings?
- Should there be a way to bypass SSRF protection for specific agents (e.g., via database flag)?

### Verdict: APPROVED WITH CHANGES

---

## Security Review (Cipher)

### Strengths
- Comprehensive coverage of SSRF attack vectors:
  - Private IP ranges (RFC 1918)
  - Localhost/loopback
  - Cloud metadata endpoints (169.254.169.254)
  - Link-local addresses
- Defense in depth: blocks both direct IP and domain-based access
- Configurable allowlist for controlled access
- Fails closed (invalid URLs are blocked by default)

### Concerns
- **Allowlist bypass**: If `allowed_domains` is used, ensure wildcard matching doesn't allow subdomains of internal services
- **Logging sensitivity**: Log messages must not contain full URLs with potential credentials
- **IPv6**: The design focuses on IPv4; verify IPv6 loopback (::1) and link-local (fe80::/10) are handled

### New Libraries / Infra Dependencies Required
- None. Goodsecurity practice to minimize dependencies.

### Better Alternatives Considered
1. **Use ssrf-filter library**: Well-maintained library with comprehensive protections
   - Why not chosen: Adds external dependency; stdlib suffices for core requirements

2. **Network-based blocking**: Block at firewall level
   - Why not chosen: App-level protection needed as defense in depth

### Recommendations
1. Add IPv6 support:
   - Block `::1` (loopback)
   - Block `fe80::/10` (link-local)
   - Block IPv6 equivalents of RFC 1918 ranges
2. Consider adding documentation about the security considerations in the README
3. Ensure error messages don't reveal internal allowlist details to attackers

### Questions for Author
- Are there any other cloud metadata endpoints beyond 169.254.169.254 that should be blocked?
- Should DNS resolution be validated to prevent DNS rebinding attacks?

### Verdict: APPROVED WITH CHANGES

---

## SMTS Review (Sage)

### Strengths
- Clean, maintainable design with single responsibility (validation module)
- Uses existing patterns (Pydantic settings, logging conventions)
- Appropriate scope - focuses on core SSRF vectors without over-engineering
- Backward compatible - existing functionality preserved

### Concerns
- The implementation requires changes to multiple files across different modules
- Testing coverage must be comprehensive to prevent regression
- Documentation must be clear for operators about behavior changes

### New Libraries / Infra Dependencies Required
- None - minimal dependency approach is correct.

### Better Alternatives Considered
1. All reasonable alternatives were evaluated in the LLD

### Recommendations
1. Add comprehensive test coverage focusing on edge cases:
   - IPv4 and IPv6 addresses
   - Domain names vs. IP addresses
   - Various URL formats (with/without port, with path, etc.)
2. Consider adding a simple integration test that verifies SSRF protection is active
3. Update documentation for operators about how agents are marked when blocked

### Questions for Author
- Are there any other endpoints in the codebase that make outbound HTTP requests that should have this protection?
- Would it make sense to create a decorator pattern for easier application to new endpoints?

### Verdict: APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED WITH CHANGES | 0 | Add distinct health status for SSRF-blocked; sanitize error messages |
| Backend (Byte) | APPROVED WITH CHANGES | 0 | Fix circular import risk; consider wildcard matching for allowlist |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | Add Prometheus metrics; document release notes; ensure logging is appropriate |
| Security (Cipher) | APPROVED WITH CHANGES | 2 | Add IPv6 support (loopback, link-local); verify wildcard allowlist safety |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Comprehensive test coverage; update operator documentation |

### Blocker Count: 2

1. **IPv6 support**: The current design only addresses IPv4. IPv6 loopback (::1) and link-local (fe80::/10) must be blocked.
2. **Allowlist wildcard matching**: The allowlist could be bypassed via subdomain attacks if wildcards are not handled correctly.

### Overall Verdict: APPROVED WITH CHANGES

The design is fundamentally sound. The identified blockers are important for production hardening but do not prevent the initial implementation. Address the blockers in v2 of the implementation.

---

## Next Steps

1. Add IPv6 blocking to `ssrf_protection.py` (loopback and link-local)
2. Fix allowlist wildcard matching to prevent subdomain bypass
3. Add Prometheus metrics as recommended by Circuit
4. Write comprehensive unit tests covering edge cases
5. Update `.env.example` with new configuration variables