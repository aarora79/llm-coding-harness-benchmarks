# Expert Review: SSRF Hardening - Outbound URL Validation

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Review Summary

This design addresses a genuine security vulnerability in the mcp-gateway-registry. The registry makes outbound HTTP requests to user-supplied URLs in four code paths, none of which validate the destination IP address. The proposed solution is appropriate, uses only stdlib modules, and follows the existing patterns in the codebase (particularly `request_utils.py`'s use of `ipaddress`).

---

## Frontend Engineer: Pixel

### Strengths
- Error messages are clear and user-friendly.
- HTTP 403 response code is appropriate for blocked URLs.

### Concerns
- N/A for this change - no UI impact.

### Recommendations
- None.

### Questions for Author
- None.

### Verdict: APPROVED

---

## Backend Engineer: Byte

### Strengths
- Single utility function approach is clean and maintainable.
- Validation happens once before the HTTP request, not per-redirect.
- Uses only stdlib modules - no new dependencies.
- Follows the existing `ipaddress` usage pattern from `request_utils.py`.

### Concerns
1. **Redirect handling is under-specified.** The LLD mentions `follow_redirects=False` as an option but does not commit to it. The current health checks use `follow_redirects=True`. If a redirect follows to a blocked IP, the validation is bypassed. This should be explicitly decided.
2. **MCP client connection functions have multiple code paths.** The LLD mentions `get_mcp_connection_result`, `get_tools_from_server_with_server_info`, and `detect_server_transport_aware` but validation should also be applied in `_get_tools_streamable_http` and `_get_tools_sse` because those functions construct modified URLs (e.g., appending `/mcp` or `/sse`).
3. **Socket.gaierror handling in URL validation.** The function re-raises `socket.gaierror` which could propagate to the FastAPI endpoint layer as a 500 instead of a 400/403. The endpoint wrappers should catch this explicitly.

### Recommendations
1. Set `follow_redirects=False` on ALL httpx.AsyncClient instantiations in health checks and MCP client. Handle redirects manually if needed, validating each redirect URL.
2. Apply `validate_outbound_url(base_url)` at the TOP of `get_tools_from_server_with_server_info` AND validate every constructed endpoint URL (`mcp_url`, `sse_url`) in `_get_tools_streamable_http` and `_get_tools_sse`.
3. Wrap all `validate_outbound_url` calls in try/except catching `SSRFBlockedError`, `ValueError`, and `socket.gaierror`.

### Questions for Author
- Why not use `follow_redirects=False` everywhere? What URLs would we miss by not following redirects?
- In `_check_server_endpoint_transport_aware`, the function calls `get_endpoint_url_from_server_info` which appends `/mcp` or `/sse` to the base URL. Should we validate the base URL or the constructed endpoint URL?

### Verdict: APPROVED WITH CHANGES
Requires redirect handling decision and MCP client URL validation coverage.

---

## SRE / DevOps Engineer: Circuit

### Strengths
- No configuration parameters, no deployment surface changes.
- No new dependencies to manage.
- DNS resolution overhead is negligible (10-50ms per call).
- Health check batching already adds delays, so the validation overhead is absorbed.

### Concerns
1. **DNS resolution failures could cascade.** If a DNS server is temporarily unavailable, many health checks could fail with `socket.gaierror`, potentially causing all health statuses to show as "blocked: ssrf" or "validation error." The health check error handling should distinguish between "blocked by SSRF guard" and "DNS resolution failed."
2. **No metrics for blocked requests.** Operators need visibility into how often SSRF blocks are triggered. Without metrics, they cannot assess the attack surface or tune their system.

### Recommendations
1. Add a separate health status string for DNS failures: "error: DNS resolution failed" (separate from "blocked: ssrf").
2. Consider adding a Prometheus counter for SSRF blocks (e.g., `ssrf_blocks_total`) using the existing metrics infrastructure (`registry/metrics/`). Check if the project already has an OpenTelemetry or Prometheus client that can be leveraged.
3. Test the change in a staging environment with URLs that have slow DNS resolution to verify the timeout behavior.

### Questions for Author
- Does the existing metrics infrastructure support custom counters? If so, a simple `ssrf_blocks_total` counter would be valuable.
- What is the timeout on `socket.getaddrinfo`? By default it can block for several seconds if DNS is unreachable.

### Verdict: APPROVED WITH CHANGES
Requires better differentiation between blocked and DNS-failed health checks, and consideration of metrics.

---

## Security Engineer: Cipher

### Strengths
- Comprehensive blocked IP ranges: covers RFC 1918 private, loopback, link-local (EC2 IMDS), multicast, and reserved.
- Checks ALL resolved IPs, not just the first one (important for round-robin DNS).
- No toggle / whitelist - security control cannot be accidentally disabled.
- Uses `ipaddress` module which is well-tested and audited.
- Explicit IPv6 coverage.

### Concerns
1. **Redirect bypass is a real attack vector.** If the attacker registers a server with a public IP that redirects (301/302) to `169.254.169.254`, the SSRF guard is bypassed. This is the most likely attack path in production.
2. **DNS rebinding is not addressed.** An attacker who controls a domain can register it with a public IP (passes validation) and later change the DNS record to point to `169.254.169.254`. Between the validation call and the actual httpx request, the IP could change.
3. **The `0.0.0.0/8` blocked range.** This is technically correct (RFC 1122), but some systems resolve `localhost` to `127.0.0.1` which is already blocked separately. Confirming this is intentional.
4. **Error messages may leak information.** The `SSRFBlockedError.__init__` includes the blocked IPs in the error message. While this is useful for debugging, it reveals internal network information to users who trigger the block. The error message for HTTP responses should be generic ("URL is not accessible"), while the log message can include details.

### Recommendations
1. **CRITICAL**: Use `follow_redirects=False` for ALL outbound httpx requests from the four entry points. Validate the response URL if a redirect is received, and reject if the redirect target is blocked.
2. Document the DNS rebinding limitation in the LLD's "Out of Scope" section and note that infrastructure-level controls (VPC security groups) provide defense-in-depth against DNS rebinding.
3. Separate the log message (detailed) from the user-facing error message (generic). HTTP 403 response should say "The URL cannot be accessed" not "blocked IPs: 169.254.169.254".
4. Add `socket.setdefaulttimeout(5)` or use `socket.getaddrinfo` with a timeout parameter to prevent DNS resolution from blocking for extended periods.

### Questions for Author
- What is the plan for handling redirects? The LLD leaves this as an open question.
- Is there a plan to add VPC-level blocking as defense-in-depth?
- For the error message: should the API response include the specific blocked IPs, or a generic message?

### Verdict: NEEDS REVISION
Requires explicit redirect handling and separation of log vs. user-facing messages before approval.

---

## SMTS (Overall): Sage

### Strengths
- Well-scoped change: one new file, four integration points, no dependencies.
- Clear separation of concerns: validation utility is independent of the callers.
- Good existing pattern reference (`request_utils.py`).
- Comprehensive blocked IP ranges covering all standard private/reserved ranges.

### Concerns
1. **Redirects are the Achilles' heel.** The LLD and issue specification both treat redirect handling as an open question. This is not acceptable - the design MUST decide this. `follow_redirects=True` with no redirect validation is an automatic SSRF bypass.
2. **The MCP client has more code paths than accounted for.** The LLD identifies four functions to patch but the MCP client constructs modified URLs in several functions. Each constructed URL must be validated.
3. **Test plan reference is thin.** The LLD says "See testing.md" but the testing plan must include tests for: blocked IPs, allowed public IPs, DNS failure, malformed URLs, redirect URLs, and integration-level tests for each of the four entry points.

### Recommendations
1. Make `follow_redirects=False` a requirement, not an option. This should be stated in the acceptance criteria of the GitHub issue.
2. Add explicit validation for every URL constructed by `get_endpoint_url()`, not just the base URL.
3. Ensure the test plan covers the redirect case: mock `httpx.get()` to return a 302 redirect to a blocked IP, verify the request is not followed.

### Questions for Author
- Will `follow_redirects=False` break any legitimate health checks? Some MCP servers return redirects for canonical URL handling.

### Verdict: APPROVED WITH CHANGES
Requires explicit redirect handling requirement in acceptance criteria.

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | N/A - no UI impact |
| Backend (Byte) | APPROVED WITH CHANGES | 3 | Use follow_redirects=False everywhere; validate constructed endpoint URLs; catch socket.gaierror at endpoint layer |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Separate DNS-failed from blocked health status; consider metrics counter |
| Security (Cipher) | NEEDS REVISION | 4 | Use follow_redirects=False (CRITICAL); separate log vs. user-facing messages; document DNS rebinding limit |
| SMTS (Sage) | APPROVED WITH CHANGES | 3 | Make follow_redirects=False a requirement; validate constructed endpoint URLs; expand test plan |

**Overall Verdict: NEEDS REVISION** (Security reviewer flagged redirect handling as CRITICAL)

### Next Steps

1. **Revise the LLD** to explicitly require `follow_redirects=False` on all httpx.AsyncClient instantiations in the four affected code paths.
2. **Revise the GitHub Issue** acceptance criteria to include: "All httpx.AsyncClient calls in the affected code paths use `follow_redirects=False` to prevent redirect-based SSRF bypass."
3. **Expand MCP client coverage** in the LLD to list every function that constructs endpoint URLs.
4. **Clarify error message strategy**: log messages include details (blocked IPs), HTTP responses use generic messages ("URL cannot be accessed").
5. **Address SRE concerns**: differentiate "blocked: ssrf" from "error: DNS failed" health status strings.