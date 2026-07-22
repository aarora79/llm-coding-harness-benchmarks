# Expert Review: SSRF Hardening - Promote Shared URL Validation

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Review Summary

This design addresses a genuine security vulnerability in the mcp-gateway-registry. The registry makes outbound HTTP requests to user-supplied URLs in five code paths, none of which validate the destination IP address. The proposed solution promotes the existing `_is_safe_url()` function from `skill_service.py` into a shared utility, eliminating code duplication and ensuring consistent SSRF protection across all callers. The design is well-scoped, uses only stdlib modules, and follows existing patterns.

The LLD has been updated to address key findings from the previous review cycle:
- Redirect handling is now explicitly specified with `follow_redirects=False` on all affected httpx.AsyncClient instantiations.
- The trusted-hosts setting has been unified to a single `mcp_gateway_extra_trusted_hosts` parameter, removing the dependency on `github_extra_hosts`.
- IP checking has been consolidated to use `_is_private_ip()` as the single source of truth, removing the redundant `_is_blocked_ipv4()`/`_is_blocked_ipv6()` functions from the previous draft.
- The `SSRF_BLOCKED_STATUS` constant ensures consistent status strings across all three health-service locations.

---

## Backend Engineer: Byte

### Strengths
- Promoting `_is_safe_url()` into a shared module eliminates code duplication and is the right architectural decision.
- The `is_safe_url()` function signature matches the existing implementation, minimizing refactor risk.
- Clear step-by-step implementation plan with approximate line numbers for each file.
- The `_is_safe_url = is_safe_url` alias in skill_service.py ensures existing callers are unaffected.
- Good coverage of all five code paths: agent validator, agent routes, health service (3 functions), MCP client.
- The `follow_redirects=False` mandate in Step 7 is the correct security posture and is now shown with concrete code.

### Concerns
1. **MCP client code path depth.** The LLD identifies `get_mcp_connection_result()` but `mcp_client.py` also has `get_tools_from_server_with_server_info()`, `detect_server_transport_aware()`, `_get_tools_streamable_http()`, and `_get_tools_sse()`. The LLD should explicitly list every function that makes an outbound HTTP request. At minimum, validating `base_url` at the entry point of `get_mcp_connection_result()` should cover all downstream paths since they all start there.
2. **The `_is_private_ip()` consolidation.** The LLD now correctly uses only `_is_private_ip()` (using Python's built-in `is_private`, `is_loopback`, etc.) as the single source of truth. This is a significant improvement over the previous draft which defined overlapping `_is_blocked_ipv4()`/`_is_blocked_ipv6()` functions.
3. **The skill_service.py removal of ~130 lines of code must be done carefully.** If any skill-specific constants (like `URL_VALIDATION_TIMEOUT`) are co-located in that block, they must not be accidentally deleted. The LLD should note this explicitly.
4. **The LLD does not show code for `_check_server_endpoint_transport_aware()` validation of resolved endpoint URLs.** This function calls `get_endpoint_url_from_server_info()` which returns derived endpoints (e.g., `/mcp`, `/sse`). These derived URLs should also be validated.

### Recommendations
1. Add a bullet to Step 6 noting that `mcp_client.py` entry-point validation covers all downstream tool-fetch functions.
2. Add a validation step for `endpoint` and `sse_endpoint` URLs returned by `get_endpoint_url_from_server_info()` in `_check_server_endpoint_transport_aware()`.
3. Explicitly note that `URL_VALIDATION_TIMEOUT` is skill-specific and must be preserved during the skill_service.py refactor.

### Questions for Author
- In `register_agent()`, should SSRF-blocked URLs be a hard error (reject registration) or a warning (allow but flag)? The LLD shows it as a hard error (HTTP 422), which is the correct hardening choice.
- The LLD shows redirect handling as "treat as unhealthy without following." Is there a case where a legitimate 301/302 should be followed for a registered server? **Answer: The LLD notes that redirects can be followed on a case-by-case basis if needed in production.**

### Verdict: APPROVED WITH CHANGES

Requires explicit MCP client code path documentation and endpoint-URL validation in `_check_server_endpoint_transport_aware()`.

---

## SRE / DevOps Engineer: Circuit

### Strengths
- No new dependencies to manage.
- The `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` configuration parameter follows the existing `GITHUB_EXTRA_HOSTS` pattern, making it familiar to operators.
- DNS resolution overhead is negligible given existing batching.
- Health check status `"blocked: ssrf"` (via `SSRF_BLOCKED_STATUS` constant) provides clear visibility into SSRF blocks.
- The backward-compatible re-export in `skill_service.py` means zero runtime risk from the refactor.
- The unified trusted-hosts setting (reading `mcp_gateway_extra_trusted_hosts` instead of `github_extra_hosts`) is the correct design.

### Concerns
1. **No metrics for SSRF blocks.** Operators running at scale need to know how often SSRF blocks are triggered. Without a Prometheus/OTel counter, they cannot assess the attack surface or tune the system.
2. **The `follow_redirects=False` change may cause legitimate health checks to report "unhealthy"** for MCP servers that use 302 redirects for canonical URL handling. Operators may need to file bug reports when they see "redirect not followed" status. The LLD should document this explicitly.
3. **The `.env.example` file needs to be updated** to include the new `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` parameter. The LLD does not mention this.

### Recommendations
1. Consider adding a Prometheus counter `ssrf_blocks_total` using the existing metrics infrastructure in `registry/observability/meters.py` if one exists. Tag with `path` and `reason` (blocked_ip, blocked_scheme, dns_failure). If no existing counter infrastructure exists, note it as a follow-up task.
2. Update `.env.example` to include `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` with a clear comment.
3. The "redirect not followed" behavior should be documented in the deployment release notes so operators are not surprised.

### Questions for Author
- Does `registry/observability/meters.py` already define a custom counter type that we can reuse for SSRF metrics?
- Is there a `docker-compose.yml` or Dockerfile that needs `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` added to its environment section?

### Verdict: APPROVED WITH CHANGES

Requires `.env.example` update and consideration of SSRF metrics counter.

---

## Security Engineer: Cipher

### Strengths
- Promoting `_is_safe_url()` ensures one source of truth for SSRF logic, reducing the chance of divergence.
- Comprehensive blocked IP ranges: covers all RFC 1918 private, loopback, link-local (EC2 IMDS), multicast, CGNAT, and reserved ranges.
- Explicit IPv6 coverage (unique local, link-local, multicast, unspecified).
- The fail-closed approach (return False on any exception) is the correct security posture.
- The `follow_redirects=False` mandate in Step 7 directly addresses the critical redirect-bypass vector identified in the previous review cycle.
- The `SSRF_BLOCKED_STATUS` constant ensures machine-readable, consistent status strings.

### Concerns
1. **Redirect handling in the agent-validator reachability check.** The LLD shows `follow_redirects=False` for async calls but the reachability check uses synchronous `httpx.get()`. The LLD correctly shows `follow_redirects=False` there too, but the handling of redirects in the sync path should be tested specifically since it does not use the same retry/error handling as the async path.
2. **DNS rebinding is not addressed.** An attacker who controls a domain can register it with a public IP (passes validation) and later change the DNS record to point to `169.254.169.254`. Between the validation call and the actual httpx request, the IP could change. The LLD correctly documents this as out of scope, but the security team should ensure infrastructure-level controls (VPC security groups, egress firewall rules) are in place as defense-in-depth.
3. **The user-facing error message is generic** ("URL resolves to a private or reserved IP address"). This is correct - do NOT expose blocked IPs in user-facing responses as they reveal internal network topology.
4. **The `_DEFAULT_TRUSTED_DOMAINS` is now the single source of truth** in the shared module. The LLD correctly states that `skill_service.py` imports from the shared module rather than defining its own copy.

### Recommendations
1. Add a `follow_redirects=False` test case for the sync `httpx.get()` path in the agent-validator reachability check.
2. Add `socket.setdefaulttimeout(5)` at the module level in `url_security.py` to prevent DNS resolution from blocking for extended periods (default DNS timeout can be several seconds).
3. Document the DNS rebinding limitation in the GitHub Issue "Out of Scope" section and recommend infrastructure-level controls.
4. Consider adding a rate limit on SSRF-blocked URLs per-IP to mitigate high-frequency scanning attempts.

### Questions for Author
- Is `follow_redirects=False` tested with the sync `httpx.get()` in the agent-validator path? The test plan mentions redirect tests but should explicitly cover the sync path.
- Should the agent registration flow block registration for SSRF-blocked URLs, or only warn? The LLD shows blocking (HTTP 422), which is the correct hardening choice.

### Verdict: APPROVED WITH CHANGES

Requires explicit sync-path redirect handling test and DNS timeout mitigation.

---

## SMTS (Overall): Sage

### Strengths
- Well-scoped change: one new file (~120 lines), six integration points, no dependencies.
- Promoting `_is_safe_url()` from `skill_service.py` is the right decision - it eliminates ~80-130 lines of duplicated code.
- Clear step-by-step implementation plan with concrete code snippets.
- The backward-compatible re-export in `skill_service.py` means zero runtime risk from the refactor.
- Good consideration of edge cases: bare IPs, DNS failure, multiple IPs per hostname, IPv6 dual-stack.
- The `follow_redirects=False` mandate in Step 7 addresses the most critical security gap.
- The unified `mcp_gateway_extra_trusted_hosts` setting is the correct design (single env var for all SSRF trust decisions).
- IP-check consolidation to `_is_private_ip()` eliminates the overlapping `_is_blocked_ipv4()`/`_is_blocked_ipv6()` from the previous draft.

### Concerns
1. **The LLD does not show explicit validation of derived endpoint URLs in `_check_server_endpoint_transport_aware()`.** This function calls `get_endpoint_url_from_server_info()` which returns derived endpoints like `/mcp` and `/sse`. These derived URLs inherit the base URL's scheme and hostname, so they are implicitly covered by the base URL validation. But the LLD should state this explicitly rather than leaving it as an assumption.
2. **Test plan depth is adequate but the redirect bypass test in testing.md uses an external httpbin.org URL for legitimate-URL tests.** The test in Section 1.7 uses `https://httpbin.org/status/200` which introduces an external dependency. A local mock server would be more reliable.
3. **The LLD estimates ~120 lines for the new utility file but the code block shows ~160 lines.** The estimate should be revised to match.

### Recommendations
1. Add an explicit statement in Step 5 that endpoint URLs returned by `get_endpoint_url_from_server_info()` are derived from the validated `proxy_pass_url` and inherit its safety.
2. Replace the httpbin.org test URL with a local mock server or a well-known public URL (e.g., `https://example.com/`).
3. Update the LOC estimate to ~160 lines for the utility file.

### Questions for Author
- Will `follow_redirects=False` break any legitimate health checks? Some MCP servers return redirects for canonical URL handling. If so, how should we handle redirects that we DO want to follow? **Answer: The LLD states redirects should be treated as "unhealthy" without following. Operators can update individual server registrations to use the canonical URL directly.**
- Should the `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` setting also accept IP ranges (CIDR notation), or just hostnames? **Answer: Only hostnames. IP ranges are covered by the default private-IP blocking.**

### Verdict: APPROVED WITH CHANGES

Requires explicit endpoint-URL derivation documentation and test plan refinement.

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | N/A - no UI impact |
| Backend (Byte) | APPROVED WITH CHANGES | 3 | Document MCP client entry-point coverage; add endpoint-URL validation note; preserve URL_VALIDATION_TIMEOUT |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Update .env.example; consider SSRF metrics counter; document redirect-not-followed behavior |
| Security (Cipher) | APPROVED | 2 | Test sync-path redirect handling; add DNS timeout mitigation |
| SMTS (Sage) | APPROVED WITH CHANGES | 3 | Document endpoint-URL derivation; replace httpbin.org test URL; revise LOC estimate |

**Overall Verdict: APPROVED WITH CHANGES**

### Next Steps

1. Add explicit statement in the LLD that endpoint URLs returned by `get_endpoint_url_from_server_info()` inherit safety from the validated `proxy_pass_url`.
2. Replace the httpbin.org test URL in testing.md with a local mock server or well-known public URL.
3. Update the LOC estimate for the utility file to ~160 lines.
4. Add `MCP_GATEWAY_EXTRA_TRUSTED_HOSTS` to `.env.example` with a clear comment.
5. Add `socket.setdefaulttimeout(5)` at the module level in `url_security.py` for DNS timeout mitigation.
6. Add an explicit test case for redirect handling in the sync `httpx.get()` path of the agent-validator reachability check.
7. Note that `URL_VALIDATION_TIMEOUT` in `skill_service.py` is skill-specific and must be preserved during the refactor.