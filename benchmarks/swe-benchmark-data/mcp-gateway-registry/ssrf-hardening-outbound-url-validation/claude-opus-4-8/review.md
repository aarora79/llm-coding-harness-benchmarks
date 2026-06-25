# Expert Review: SSRF Hardening for Agent Card Fetch and Health Checks

*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*
*Reviewed: 2026-06-24*

Five reviewers evaluate the design. Each lists strengths, concerns, new dependencies, better alternatives, recommendations, questions, and a verdict.

---

## 1. Frontend Engineer - "Pixel"
**Focus:** UI/UX, components, state, API integration

### Strengths
- The `POST /agents/{path}/health` response contract is unchanged; the existing health badge in the UI keeps working. A blocked check correctly surfaces as `unhealthy`, which the UI already renders.
- The `detail` string carries a human-readable reason ("Blocked by SSRF policy"), so the UI can show a tooltip without a schema change.

### Concerns
- **C1 (minor):** "Blocked by SSRF policy" reads as a scary internal error to a non-security user. A registrant who legitimately pointed an agent at a private host will see `unhealthy` with no obvious remediation. The UI cannot distinguish "agent down" from "blocked by policy" beyond parsing the free-text `detail`.
- **C2 (minor):** No structured field (e.g. `blocked_reason: "ssrf"`) means the frontend must string-match `detail` to style blocked-vs-unreachable differently. String matching is brittle.

### New libraries / infra
- None.

### Better alternatives
- Add an optional, additive `reason_code` field (e.g. `"ssrf_blocked"`, `"timeout"`, `"http_error"`) to the health response. Additive, backward compatible, and lets the UI render a distinct "Blocked by policy - allowlist this host" affordance.

### Recommendations
- Keep the contract additive: add `reason_code` rather than overloading `detail`.
- Provide copy guidance: when `reason_code == "ssrf_blocked"`, the UI should link to the `ssrf_extra_allowed_hosts` doc.

### Questions for author
- Can we add `reason_code` to the response without breaking the comparison benchmark's "no schema change" goal? (It is additive, so existing clients are unaffected.)

### Verdict: APPROVED WITH CHANGES (add an additive `reason_code`; non-blocking)

---

## 2. Backend Engineer - "Byte"
**Focus:** API design, data models, business logic, performance

### Strengths
- Promoting `_is_safe_url` to `registry/utils/ssrf.py` and re-exporting into `skill_service.py` is the correct DRY move and keeps SKILL.md behavior byte-for-byte identical.
- Validating the **resolved** transport endpoint (`get_endpoint_url_from_server_info`) in addition to `proxy_pass_url` is an important catch - `mcp_endpoint`/`sse_endpoint` overrides are a distinct user-supplied URL and a real bypass if missed.
- Fail-closed semantics (`is_safe_url` returns `False` on any exception, DNS failure treated as unsafe) are correct.

### Concerns
- **C1 (significant):** **TOCTOU / DNS rebinding.** `is_safe_url()` calls `getaddrinfo`, then `httpx` resolves the host *again* at request time. An attacker controlling DNS can return a public IP to the guard and a private IP to httpx. The LLD acknowledges this but defers it. For a security-titled change, the residual gap should be stated prominently in the issue and a follow-up filed, otherwise the fix gives false assurance.
- **C2 (significant):** **Redirect re-validation on the server path.** The LLD says "re-validate redirect targets" and the server health code uses `follow_redirects=True`, but the step-by-step only shows pre-request validation. With `follow_redirects=True`, httpx follows internally and the post-hoc check happens after the connection. The SKILL.md code re-checks `response.url` *after* the fetch - but by then the redirected request already executed (and for SSRF the request itself is the harm). Recommend setting `follow_redirects=False` on guarded health requests and validating each hop, or at minimum documenting that the existing SKILL.md redirect handling has the same post-hoc limitation.
- **C3 (minor):** `lru_cache` on `health_allowed_hosts()` means tests and any runtime settings reload must `cache_clear()`. The LLD notes this; ensure the test suite actually does it to avoid flaky cross-test leakage.
- **C4 (minor):** IPv6 and IPv4-mapped IPv6 (`::ffff:127.0.0.1`) - confirm `ipaddress.ip_address` + `is_loopback`/`is_private` cover the mapped form. `getaddrinfo` may return both families; the loop checks all `sockaddr[0]`, which is good, but mapped addresses deserve an explicit test.

### New libraries / infra
- None. Standard library only. Agreed.

### Better alternatives
- For C1/C2: a custom httpx transport that pins the validated IP (connect to the resolved-and-checked IP, send `Host` header) closes the rebinding window. Heavier; reasonable as a fast-follow, not v1.

### Recommendations
- Set `follow_redirects=False` on the guarded health/agent-card requests, or validate the final URL and accept the documented post-hoc limitation - but be explicit.
- Add explicit tests for IPv4-mapped IPv6 and for the resolved-endpoint (mcp_endpoint override) bypass.
- File a tracked follow-up for DNS-rebinding (resolve-and-pin).

### Questions for author
- Is `follow_redirects` actually needed on health checks? If not, disabling it is the simplest hardening.

### Verdict: APPROVED WITH CHANGES (address redirect handling C2 and document DNS-rebinding C1)

---

## 3. SRE / DevOps Engineer - "Circuit"
**Focus:** Deployment, monitoring, scaling, infrastructure

### Strengths
- Safe defaults: `ssrf_protection_enabled=true`, `ssrf_extra_allowed_hosts=""`. Secure by default.
- Instant rollback via `SSRF_PROTECTION_ENABLED=false` with no code change - excellent operational property.
- The deployment surface checklist enumerates `.env.example`, compose, Helm, ECS, and the parameter-reference doc.

### Concerns
- **C1 (significant):** **Deployment-time blast radius.** Many real deployments run agents/MCP servers on private addresses (docker-compose service names, in-cluster ClusterIP/service DNS, `127.0.0.1` sidecars). Turning this on by default will flip a swath of currently-"healthy" servers to "unhealthy" on upgrade. This is correct security behavior but a noisy operational event. Needs a prominent upgrade note and ideally a one-release "warn-only" mode.
- **C2 (moderate):** `getaddrinfo` is synchronous inside an async event loop. With many registered servers and the background health loop (`health_check_interval_seconds=300`), a slow/hanging resolver could stall the loop. The HTTP call has a 2s timeout, but the DNS lookup is not bounded by it. Consider a resolver timeout or running the lookup in a thread executor.
- **C3 (minor):** Observability is log-only in v1. For a security control, operators will want a metric to alert on block-rate. The LLD lists this as optional - recommend making the OTel span attribute `ssrf.blocked=true` non-optional since telemetry already exists.

### New libraries / infra
- None required. Optional: wire into the existing OpenTelemetry setup (`registry/core/telemetry.py`).

### Better alternatives
- Introduce a three-state mode (`enforce` / `warn` / `off`) instead of a boolean, so operators can run `warn` for one release to discover legitimate private hosts from the block logs before enforcing. This dramatically de-risks the rollout (C1).

### Recommendations
- Ship a `warn` mode (or document a staged rollout: deploy with `false`, collect would-be-block logs, populate allowlist, then enable).
- Bound DNS resolution (thread executor + timeout) for the background loop.
- Emit the OTel span attribute and a block counter in v1.
- Add an explicit upgrade note in release notes / CHANGELOG.

### Questions for author
- What is the expected default health behavior in docker-compose (services reach each other by private DNS)? Does the dev compose need `ssrf_extra_allowed_hosts` pre-populated, or `ssrf_protection_enabled=false` for local dev?

### Verdict: APPROVED WITH CHANGES (staged rollout / warn-mode for C1; bound DNS for C2)

---

## 4. Security Engineer - "Cipher"
**Focus:** AuthN/AuthZ, validation, OWASP, data protection

### Strengths
- Targets a real, high-impact SSRF: server health checks **inject stored credentials** into requests to user-supplied URLs, so this also closes a credential-exfiltration vector, not just internal probing. Good that the design calls this out.
- Reuses an already-reviewed guard rather than hand-rolling new validation. Blocks metadata IP `169.254.169.254`, private/loopback/link-local/reserved ranges, and non-http schemes (kills `file://`, `gopher://`, `ftp://`).
- Fail-closed on parse/resolve errors.

### Concerns
- **C1 (significant):** **DNS rebinding is the headline residual risk** for any resolve-then-fetch SSRF guard (same as Byte C1). For a security deliverable this must be explicit in the issue's "known limitations," not buried in Open Questions. Recommend the resolve-and-pin follow-up be filed at merge time.
- **C2 (significant):** **`169.254.169.254` is not the only metadata surface.** GCP/Azure use `metadata.google.internal` and `169.254.169.254` with required headers; Azure IMDS is `169.254.169.254` too; but IPv6 link-local (`fe80::/10`) and the GCP metadata hostname resolving to a link-local should be covered. `is_link_local` covers `fe80::/10` and `169.254.0.0/16`, which is good - but verify the alternate AWS metadata `fd00:ec2::254` (IPv6 IMDS) is caught (`is_private`/`is_reserved` for `fd00::/8` ULA - yes, `fd00::/8` is `is_private`). Add explicit tests.
- **C3 (moderate):** **Allowlist by hostname bypasses the IP check entirely.** A host on the allowlist that an attacker can influence (e.g. a registrant controls a DNS name you allowlisted) becomes an SSRF hole. Document that `ssrf_extra_allowed_hosts` must contain only operator-controlled names, never user-influenced ones.
- **C4 (moderate):** **Decimal/octal/hex IP literals and `0.0.0.0`.** `http://0177.0.0.1`, `http://2130706433` (decimal for 127.0.0.1), `http://0/`. `urlparse(...).hostname` plus `getaddrinfo` generally normalizes these, but `getaddrinfo("2130706433")` behavior is platform-dependent. Add tests for these encodings; they are classic guard-bypass payloads.
- **C5 (minor):** Port is not restricted. SSRF to `http://public-host:22` or `:6379` is still possible if the host is public. Out of scope for internal-SSRF, but worth a note - the threat here is internal targets, which IP checks cover.

### New libraries / infra
- None. Standard library is appropriate and auditable.

### Better alternatives
- Resolve-and-pin transport (connect only to the validated IP) is the robust fix for C1 and the octal/hex/rebinding family (C4) simultaneously. Strongly recommend as the fast-follow.

### Recommendations
- Promote DNS-rebinding and metadata-surface coverage from Open Questions into an explicit "Known Limitations" section in the issue.
- Add the bypass-payload test matrix (decimal/octal/hex IP, IPv4-mapped IPv6, IPv6 ULA/link-local, `0.0.0.0`, `[::1]`).
- Document the allowlist trust requirement (operator-controlled hosts only).
- File the resolve-and-pin follow-up.

### Questions for author
- Will `ssrf_extra_allowed_hosts` ever be settable by non-admins? (Must be admin/operator-only env config - confirm it is not exposed via any API.)

### Verdict: APPROVED WITH CHANGES (explicit limitations + bypass-payload tests; resolve-and-pin as tracked follow-up)

---

## 5. SMTS / Overall - "Sage"
**Focus:** Architecture, code quality, maintainability

### Strengths
- Correct architectural instinct: one shared utility, reused, with a pure refactor of the existing call site. Minimizes risk and divergence.
- Scope is tight and well-bounded: agent-card + server health, explicitly excluding federation/webhook (which are config-driven and tracked elsewhere). This makes it directly comparable to the sibling federation-focused benchmark.
- The feature flag + rollback story is mature, and Alternatives are honestly argued (register-time validation rejected for a good reason given this product fronts private MCP servers).

### Concerns
- **C1 (moderate):** The design has two allowlists now (`github_extra_hosts` for SKILL.md, `ssrf_extra_allowed_hosts` for health). Justified (different trust domains), but document the distinction clearly or future maintainers will "consolidate" them and silently widen trust.
- **C2 (moderate):** Consensus across Byte/Cipher: redirect handling (C2/Byte) and DNS-rebinding (C1/Cipher) are the two real gaps. Neither blocks v1 if documented, but the issue currently undersells them. A security change should be honest about residual risk.
- **C3 (minor):** Open Questions contains decisions that should be settled before implementation (whether the flag also gates SKILL.md - the LLD already recommends "no"; promote that to a decision).

### New libraries / infra
- None.

### Recommendations
- Settle the Open Questions into decisions (flag gates new surfaces only; allowlists stay independent).
- Add a "Known Limitations" section (rebinding, redirects, port) to the issue and LLD.
- Adopt SRE's staged-rollout / warn-mode suggestion to de-risk the upgrade.
- Proceed - the core design is sound and the gaps are documentation + a fast-follow, not redesign.

### Verdict: APPROVED WITH CHANGES (documentation of limitations + settle open questions; architecture is sound)

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED WITH CHANGES | 0 | Add additive `reason_code` so UI distinguishes blocked vs down |
| Backend (Byte) | APPROVED WITH CHANGES | 0 | Fix redirect handling (`follow_redirects=False` or validate hops); test mcp_endpoint bypass + mapped IPv6 |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | Staged rollout / warn-mode; bound DNS lookup; emit OTel block signal |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | Explicit limitations (rebinding, metadata surfaces); bypass-payload test matrix; resolve-and-pin follow-up |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Settle open questions; document dual-allowlist rationale; adopt staged rollout |

**Overall:** APPROVED WITH CHANGES. No reviewer raised a blocking objection. The architecture (shared guard reused on both unprotected surfaces, feature flag, allowlist, fail-closed) is sound and correctly scoped. The actionable changes are: (1) decide redirect handling explicitly, (2) document residual risks (DNS rebinding, metadata surfaces, ports) as Known Limitations, (3) expand the test matrix to bypass payloads and IPv6 forms, (4) consider a warn-mode for safer rollout, and (5) make the SSRF block observable via the existing OTel pipeline.

## Next Steps
1. Promote DNS-rebinding, redirect, and port limitations into a "Known Limitations" section in `github-issue.md` and `lld.md`; file a resolve-and-pin fast-follow.
2. Decide redirect strategy: prefer `follow_redirects=False` on guarded health requests.
3. Expand `testing.md` with the bypass-payload matrix (decimal/octal/hex IPs, IPv4-mapped IPv6, IPv6 ULA/link-local, `0.0.0.0`, `[::1]`) and the `mcp_endpoint`/`sse_endpoint` override case.
4. Add an additive `reason_code` to the health response (non-blocking, improves UX/observability).
5. Settle the LLD Open Questions into decisions; document the two-allowlist rationale.
6. Optional: introduce a three-state `ssrf_protection_mode` (enforce/warn/off) for staged rollout.
