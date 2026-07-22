# Expert Review: SSRF Hardening -- Outbound URL Validation

*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*
*Reviewed: 2026-07-21*

## Review Panel

| Role | Reviewer | Focus |
|------|----------|-------|
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

---

## Backend Engineer (Byte)

### Strengths
1. **Clean extraction pattern**: Promoting private functions to a shared module without changing the validation logic minimizes risk. The existing `_is_safe_url()` is battle-tested in production via `skill_service.py`.
2. **Consistent error handling**: Each call site returns an appropriate error type for its context (HTTP 422 at API boundaries, `None`/`False` at service boundaries, `ValueError` in scanners). This respects the existing error-handling contracts.
3. **Minimal API surface change**: No new endpoints, no schema changes. The only user-visible difference is a 422 response for invalid URLs -- which is an improvement, not a break.
4. **call_site label design**: Including the caller identity in the metric and log makes debugging straightforward without needing distributed tracing for this specific path.

### Concerns
1. **Synchronous DNS in async context**: `socket.getaddrinfo()` blocks the event loop. In `health/service.py` and `mcp_client.py`, this runs inside async functions. With hundreds of servers being health-checked in the same loop iteration, blocking DNS could stall the event loop for seconds.
2. **`detect_server_transport()` returns `"blocked"`**: Callers currently expect `"sse"` or `"streamable-http"`. The new `"blocked"` return value is undocumented and could cause downstream `if transport == "sse"` / `elif transport == "streamable-http"` branches to fall through to an `else` that logs an error but still proceeds.
3. **Race between registration check and health check**: If an attacker registers a public URL, waits for it to be stored, then changes DNS to a private IP, the registration-time check passes but the runtime health-check catch depends on the health loop running before the attacker exploits the window. The LLD acknowledges this (TOCTOU) but does not estimate the window size.

### Recommendations
1. Wrap `socket.getaddrinfo()` in `asyncio.to_thread()` at async call sites (mcp_client, health service) to avoid blocking the event loop. The skill_service already runs in an executor for heavy operations; apply the same pattern here.
2. Document the `"blocked"` return from `detect_server_transport()` and ensure callers handle it explicitly -- either by adding a check in `get_tools_from_server_with_transport()` (which already has its own `is_safe_url()` guard, so transport detection for blocked URLs should not even be reached) or by removing the early return in `detect_server_transport()` and relying solely on the check in the caller.
3. Consider running the health-check SSRF validation against `socket.getaddrinfo()` with a short TTL or disabling the OS resolver cache for these lookups (set `AI_NUMERICSERV` flag, or use `aiodns` for explicit TTL control) to shrink the TOCTOU window.

### Questions for Author
- Should `is_safe_url()` have an async variant (`async_is_safe_url()`) that uses `asyncio.to_thread()` internally, so callers do not need to handle threading themselves?
- What happens to a server that was previously healthy but now fails the SSRF check in the health loop? Does it get permanently marked unhealthy, or is there a recovery path?

### Verdict: APPROVED WITH CHANGES
The synchronous DNS concern is the only blocker. The rest is solid and well-reasoned.

---

## SRE/DevOps Engineer (Circuit)

### Strengths
1. **Single new env var**: `SSRF_ADDITIONAL_TRUSTED_DOMAINS` is the only operator knob. Simple to document, simple to configure, no complex interactions.
2. **Deployment surface checklist is thorough**: Helm, Terraform, Docker Compose, and `.env.example` are all listed. The reserved-env-names update prevents conflicts with user `extraEnv`.
3. **Backwards compatible by default**: Public URLs pass validation without operator action. Only operators with internal/private MCP servers need to configure the allowlist.
4. **Metric with useful labels**: `ssrf_blocked_total{call_site, reason}` enables alerting on specific attack vectors (e.g., `reason="private_ip"` spikes indicate active SSRF probing).

### Concerns
1. **No readiness/health impact on the registry itself**: If DNS is slow or flapping, `is_safe_url()` could cause health checks to time out for legitimate servers. The health service already has a 2-second timeout, but DNS resolution happens before that timer starts.
2. **Helm reserved-names update**: Adding `SSRF_ADDITIONAL_TRUSTED_DOMAINS` to the reserved list means the index-based positional assertions in `tests/extra_env_test.yaml` will need updating. The LLD does not mention this.
3. **No feature flag for gradual rollout**: The change is always-on. If a false positive blocks a critical production server, the only remediation is adding a domain to the allowlist and redeploying. Consider a `SSRF_VALIDATION_ENABLED=true` env var for the first release.

### Recommendations
1. Add DNS resolution to the health-check timeout budget. Wrap the `is_safe_url()` + HTTP call in a single timeout context so that slow DNS does not silently consume the entire health-check window.
2. Update `tests/extra_env_test.yaml` index assertions in the LLD file-changes table.
3. Add a `SSRF_VALIDATION_MODE` env var with values `enforce` (default, blocks requests) and `audit` (logs + emits metric but allows the request). This gives operators a safe rollout path for the first deployment.

### Questions for Author
- Should `is_safe_url()` cache DNS results per-hostname to avoid repeated resolution on the 5-minute health-check cycle?
- What is the expected latency impact of adding DNS resolution to the registration endpoint's critical path?

### Verdict: APPROVED WITH CHANGES
The missing Helm test update is a CI-blocker. The audit-mode suggestion is strongly recommended but not blocking.

---

## Security Engineer (Cipher)

### Strengths
1. **Defense-in-depth approach**: Validation at both registration time and runtime covers both immediate rejection and DNS rebinding scenarios. This is the industry-standard pattern recommended by OWASP.
2. **Cloud metadata protection is explicit**: The `169.254.169.254` check exists as both an `is_link_local` property check and a string comparison. Defense-in-depth against IPv4 metadata; also covers IPv6 link-local via `fe80::`.
3. **Trusted-domain bypass is allowlist-only**: Domains must be explicitly configured. There is no wildcard, no regex, and no subdomain matching -- `corp.example.com` does not implicitly trust `evil.corp.example.com`.
4. **Metric-based detection**: The `ssrf_blocked_total` counter enables SOC alerting. Attackers typically probe multiple private IPs before finding a valuable target; the metric catches this scanning phase.

### Concerns
1. **TOCTOU / DNS rebinding gap**: The design acknowledges this but does not mitigate it. Between `getaddrinfo()` and the actual TCP connect (inside httpx or MCP SDK), DNS can rebind. This is the most common SSRF bypass technique in modern attacks.
2. **IPv6 bypass vectors**: The `is_private_ip()` check uses `ipaddress.ip_address()` which handles IPv6, but attackers can use IPv4-mapped IPv6 addresses (e.g., `::ffff:127.0.0.1`) or IPv6 zone IDs to bypass naive checks. The Python `ipaddress` module handles most of these, but the LLD should explicitly test these vectors.
3. **Redirect following**: The existing `skill_scanner.py` uses `follow_redirects=True`. If the initial URL passes SSRF validation but redirects to a private IP, the protection is bypassed. The LLD does not address redirect validation.
4. **URL parsing ambiguities**: Different URL parsers (Python `urlparse` vs. httpx's internal parser) may disagree on the hostname for edge cases (e.g., `http://evil.com\@internal.host/`, backslash-as-separator on some platforms). An attacker could craft a URL that `urlparse` sees as `evil.com` but httpx connects to `internal.host`.

### Recommendations
1. **Redirect validation**: For call sites that follow redirects (`skill_scanner.py` with `follow_redirects=True`, any httpx client with redirect following), apply `is_safe_url()` to the redirect target as well. httpx supports `event_hooks` for this:
   ```python
   async def _ssrf_redirect_hook(response):
       if response.is_redirect:
           location = response.headers.get("location")
           if location and not is_safe_url(location, call_site="redirect"):
               raise httpx.TooManyRedirects("SSRF: redirect to blocked URL")
   ```
2. **IPv6 test coverage**: Add explicit test cases for `::ffff:127.0.0.1`, `::ffff:10.0.0.1`, `::ffff:169.254.169.254`, and `fe80::1%eth0` (zone ID). Verify that `ipaddress.ip_address()` correctly classifies all of them.
3. **URL normalization before validation**: Consider using the existing `registry/utils/url_normalize.py` to canonicalize the URL before passing to `urlparse`. This reduces the parser-disagreement attack surface.
4. **IMDSv2 awareness**: Document that this protection does not help if the ECS task role's IMDSv2 hop limit allows the container to reach the metadata endpoint with a PUT for a token. Recommend operators set `HttpPutResponseHopLimit=1` on the ECS task as a complementary control.

### Questions for Author
- Does the MCP SDK's `sse_client`/`streamablehttp_client` follow redirects internally? If so, the SSRF check before entering the context manager is insufficient -- the SDK could be redirected to a private IP after the initial connection.
- Is there a reason not to use `httpx`'s built-in `extensions["sni_hostname"]` or connect-via settings to pin the resolved IP for the actual connection?

### Verdict: APPROVED WITH CHANGES
The redirect-following bypass (concern 3) is a real gap that should be closed in this iteration. The TOCTOU gap (concern 1) is acceptable as documented future work given the complexity of connect-time pinning.

---

## SMTS / Overall (Sage)

### Strengths
1. **Proportionate complexity**: The design does not over-engineer. A single ~140-line module with one public function, applied at the right call sites, addresses a real security gap without introducing new abstractions or frameworks.
2. **Clear separation of concerns**: The SSRF module has no knowledge of HTTP clients, FastAPI, or MCP. It takes a URL string and returns a boolean. This makes it trivially testable and reusable.
3. **Backwards compatibility preserved**: No existing public API contracts change. The only new behavior is rejection of URLs that should never have been accepted.
4. **Comprehensive alternatives analysis**: The LLD honestly evaluates three alternatives and explains why each was rejected with specific technical reasons (MCP SDK limitation, DNS rebinding vulnerability, platform lock-in).
5. **Well-defined deployment surface**: Every place the new env var must appear is listed. This prevents the common "it works locally but not in staging" failure mode.

### Concerns
1. **Test migration could regress coverage**: Moving tests from `test_skill_service_ssrf_allowlist.py` to `test_ssrf.py` should be done carefully. The old test file should be updated to test the refactored `skill_service.py` (which now imports from the shared module), not deleted entirely -- the integration between `skill_service` and the shared module still needs coverage.
2. **Error response structure is not standardized**: The 422 response body uses a custom schema (`detail`, `field`, `url`, `reason`). If the project has a standard error envelope (e.g., RFC 7807 Problem Details), the SSRF errors should follow that format.
3. **`call_site` parameter is stringly-typed**: Using free-form strings for metrics labels means typos go undetected. Consider an enum or at least a module-level constant for each valid `call_site` value.

### Recommendations
1. Keep the old test file but update it to verify the `skill_service.py` -> `ssrf.py` integration (i.e., the import works and the function is called). The comprehensive unit tests live in the new `test_ssrf.py`.
2. Check whether the project uses a standard error response format. If yes, adopt it. If not, this is a reasonable ad-hoc format for a security validation error.
3. Define `call_site` constants in `registry/utils/ssrf.py`:
   ```python
   CALL_SITE_SKILL_SERVICE = "skill_service"
   CALL_SITE_MCP_CLIENT_TRANSPORT = "mcp_client.detect_transport"
   # ...
   ```

### Questions for Author
- Is there a project-wide error response standard that this should follow?
- Should the `is_safe_url()` function also be exposed as a FastAPI dependency (`Depends(validate_url_ssrf)`) so route handlers do not need boilerplate?

### Verdict: APPROVED WITH CHANGES
No blocking issues. The three recommendations improve code quality but are not hard requirements for shipping.

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Wrap DNS in `asyncio.to_thread()` at async call sites |
| SRE (Circuit) | APPROVED WITH CHANGES | 1 | Update Helm test assertions; consider audit mode |
| Security (Cipher) | APPROVED WITH CHANGES | 1 | Add redirect-target validation for `follow_redirects=True` sites |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Define call_site constants; keep old test file for integration coverage |

### Consensus
All reviewers approve with changes. Three blockers identified:
1. **Async DNS** -- synchronous `getaddrinfo()` in async call sites will block the event loop under load.
2. **Helm test update** -- missing from file-changes table; CI will fail.
3. **Redirect bypass** -- `follow_redirects=True` in `skill_scanner.py` (and potentially MCP SDK internals) can be exploited to redirect to private IPs after the initial check passes.

### Next Steps
1. Address the three blockers before implementation begins.
2. Add IPv6 edge-case tests (low effort, high value).
3. Consider audit mode for initial rollout (recommended but not blocking).
4. Track connect-time IP pinning as a follow-up issue for DNS rebinding mitigation.
