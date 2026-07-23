# Expert Review: SSRF Hardening for Agent-Card Fetch and Health-Check Paths

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

This document captures independent reviews from five expert personas. Each reviewed the GitHub issue and the low-level design against the actual repository at tag `1.24.4`. Reviews are critical by design: they identify real, verified issues rather than praise.

---

### Frontend Engineer Review (Pixel)

**Strengths**
- The frontend will not crash on the new backend status. Health strings are normalized through prefix/substring checks before reaching components: `frontend/src/utils/healthStatus.ts` (`raw.startsWith('unhealthy')`) and `frontend/src/contexts/ServerStatsContext.tsx::mapHealthStatus` (`healthStatus.includes('unhealthy')`). The LLD's `UNHEALTHY_SSRF_BLOCKED = "unhealthy: blocked by SSRF protection"` collapses to the `unhealthy` display bucket, so `ServerCard`, `AgentCard`, and `SkillCard` render a red "Unhealthy" indicator with no code change and no runtime error.
- No new TypeScript union members are strictly required; raw values are normalized at ingestion.
- Correctly scoped as backend-heavy; the bulk of the change has zero frontend footprint.

**Concerns**
- Overstated UI observability. The LLD Observability section claims `UNHEALTHY_SSRF_BLOCKED` gives operators dashboard visibility "without reading logs." Every surface collapses the granular status to the literal word "Unhealthy" (`ServerCard.tsx:779`, `AgentCard.tsx:586`, `SkillCard.tsx:595`); the raw reason string is discarded by `normalizeHealthStatus`/`mapHealthStatus` and never rendered. An operator cannot distinguish "SSRF-blocked" from "genuinely down." This undercuts the third user story.
- Silent registration block. Agent-card reachability is non-blocking, so `POST /agents/register` still returns success. `RegisterPage.tsx::performAgentRegistration` ignores the body and fires a success toast, then navigates away. A blocked URL is never surfaced at the moment the user could act on it.
- Two inconsistent representations for one concept: the server path uses the enum string `"unhealthy: blocked by SSRF protection"`, while the agent health endpoint returns `{"status": "unhealthy", "detail": "blocked-by-ssrf-protection"}`. `AgentCard.tsx::handleRefreshHealth` reads only `status` and drops `detail`.
- Misleading success toast on manual refresh: `AgentCard.tsx:274` / `ServerCard.tsx:293` show a green "refreshed successfully" toast whenever the HTTP call succeeds, regardless of the returned health, so an SSRF-blocked refresh shows success plus a red dot.

**New libraries / infra dependencies**
- None on the frontend. `SSRF_ALLOWED_HOSTS` is server-side only.

**Better alternatives considered**
- Pass the raw status through as a `title`/tooltip on the existing status dot (mirroring the `Local` status tooltip pattern in `ServerCard.tsx`), making "blocked by SSRF" legible without a new component.
- Return the reachability result on registration and show a warning toast instead of a flat success toast; delay auto-navigate so it is readable.
- Standardize on one status contract: have the agent endpoint emit the same `UNHEALTHY_SSRF_BLOCKED` value rather than a parallel `detail` slug.

**Recommendations**
1. Reconcile the agent-health response with the server enum (LLD Step 5 vs Step 6 diverge).
2. Add a lightweight display of the blocked reason (at minimum a `title` tooltip on the status indicator) or drop the "operator dashboard visibility" claim from Observability.
3. Give registration real feedback by reading the reachability result and downgrading the toast to a warning when blocked.
4. Do not show a green "refreshed successfully" toast when the refreshed status is unhealthy/blocked.

**Questions for author**
- Does this frontend consume a health-status WebSocket broadcast anywhere? I found only polling via `ServerStatsContext.fetchData`; the "WebSocket broadcast" visibility claim may reference a channel this UI never reads.
- Who consumes the `detail: "blocked-by-ssrf-protection"` field, given `AgentCard` ignores it?
- Should blocked-by-SSRF count toward the "With Issues" sidebar metric? It currently will.
- Should a blocked URL produce an inline field-level error on the URL input at registration?

**Verdict:** APPROVED WITH CHANGES

---

### Backend Engineer Review (Byte)

**Strengths**
- Correct diagnosis and choke-point placement. `_check_server_endpoint_transport_aware` (service.py:674) is genuinely the single funnel for the periodic loop and `perform_immediate_health_check` (which calls it at line 1241), so one guard there plus one at the top of `perform_immediate_health_check` covers the server-health surface. The agent-card and agent-health sinks are also correctly identified.
- Promoting the guard to `registry/utils/ssrf.py` and reusing the proven logic verbatim is the right DRY call and preserves fail-closed semantics.
- The separate `ssrf_allowed_hosts` setting is well-justified: `github_extra_hosts` also drives GitHub auth-header injection (github_auth.py), so conflating them would leak credentials to internal MCP hosts.
- Non-raising behavior on both paths preserves current semantics; `HealthStatus.UNHEALTHY_SSRF_BLOCKED` fits the existing `str, Enum` pattern.

**Concerns**
- **(CONFIRMED, blocking) The re-export breaks existing SSRF tests.** `tests/unit/services/test_skill_service_ssrf_allowlist.py` patches `registry.services.skill_service.settings` (11 tests) and `registry.services.skill_service.socket.getaddrinfo`. After the move, `is_safe_url`/`_trusted_domains` execute inside `registry/utils/ssrf.py` and resolve `settings` from the `ssrf` module namespace, so the `settings` patches become no-ops. `test_ghes_url_allowed_when_configured` sets `skill_service.settings.github_extra_hosts` but `ssrf._trusted_domains()` reads the real (empty) value, falls through to a real `getaddrinfo`, and returns `False` where the test asserts `True`. The `getaddrinfo` patches survive (same module object) but the `settings` patches do not. The LLD's claim that "tests will still work" is wrong; the acceptance criterion "all existing skill SSRF tests still pass" is not met.
- **(CONFIRMED) TOCTOU / DNS rebinding is unaddressed.** `is_safe_url` resolves the host, then httpx independently re-resolves at connect time. An attacker with a low-TTL domain returns a public IP during validation and a private/metadata IP during the fetch. `follow_redirects=False` does not close this. The LLD only cites TOCTOU to reject Alt 3 and never acknowledges it as a residual gap in the chosen design.
- **(CONFIRMED) Blocking `getaddrinfo` on the async event loop in the hot loop.** The health path is fully async and batched. A synchronous `socket.getaddrinfo` at service.py:674 serializes a blocking DNS call inside the event loop for every non-allowlisted server per cycle; under a slow resolver this stalls the whole batch. The "parity with the skill path" argument is weak (skill is one fetch per registration, not a batched loop). Use `await asyncio.to_thread(is_safe_url, url)` on the async paths; do not defer to an Open Question.
- **(Partially inaccurate) `follow_redirects` change scope.** Accurate for `health/service.py` (all 8 sites explicitly set `follow_redirects=True`), so each must be edited. But in `agent_routes.py::check_agent_health` the GET and HEAD do not set it, so httpx already defaults to `False`; the "change True->False" there is a no-op.
- **(Reasonable, with caveat) Validating only `proxy_pass_url`.** `mcp_endpoint`/`sse_endpoint` share the netloc, so host-level validation covers them - provided redirects are actually disabled at every one of the 8 sites. Also `_try_ping_without_auth` (service.py:627) issues its own POST at line 650 with `follow_redirects=True`; confirm it is reached only after the choke-point validation and also gets `follow_redirects=False`.

**New libraries / infra dependencies**
- None. One new config field, one enum value, one exception class. `UrlValidationError` is currently dead code (neither path raises); flag it as unused.

**Better alternatives considered**
- The matrix is adequate. The missing alternative is *how* to do the re-export safely: migrate the existing tests to patch `registry.utils.ssrf.settings`/`.socket`, rather than asserting no test changes are needed.

**Recommendations**
1. Do not claim zero test changes; migrate `test_skill_service_ssrf_allowlist.py` to patch `registry.utils.ssrf.*` and update the AC. (Gating item.)
2. Wrap the guard in `await asyncio.to_thread(is_safe_url, url)` on all async paths. Promote from Open Question to design decision.
3. Explicitly document DNS-rebinding/TOCTOU as accepted residual risk of resolve-then-fetch.
4. Enumerate all 8 `follow_redirects=True` sites (plus the `_try_ping_without_auth` POST) as must-change; drop the misleading True->False claim for `agent_routes.py`.
5. Confirm the guard runs before `_try_ping_without_auth` and before any endpoint derivation.

**Questions for author**
- Why does the LLD assert existing tests pass unchanged, given the confirmed patch-target break?
- Will `_try_ping_without_auth`'s POST be gated by the choke point and switched to `follow_redirects=False`?
- Is `to_thread`-wrapping acceptable for the health hot loop?
- Should `perform_immediate_health_check` return a distinct API signal for blocked URLs?

**Verdict:** NEEDS REVISION

---

### SRE/DevOps Engineer Review (Circuit)

**Strengths**
- The deployment-surface checklist is real and the line numbers check out. Every location the LLD claims `GITHUB_EXTRA_HOSTS` is wired exists within 1-2 lines of the cited numbers (verified: the three compose files, `.env.example:623`, four Terraform files, three Helm files, `config_routes.py:321`, `docs/configuration.md:235`, `docs/unified-parameter-reference.md:373`). The parallel `SSRF_ALLOWED_HOSTS` wiring is realistic and complete.
- Choke points are correctly identified; `HealthStatus` in `constants.py:11` follows the exact enum pattern the LLD extends.
- The `follow_redirects=True` inventory (8 sites) is accurate - a permitted host really can 30x to an internal target today.
- Separating `ssrf_allowed_hosts` from `github_extra_hosts` is correct (the latter drives auth-header injection in `github_auth.py:60`).
- Fail-closed guard is proven in-tree; the re-export is a low-risk refactor.

**Concerns**
- **Rollout blast radius contradicts "backwards-compatible" (highest severity).** The default `SSRF_ALLOWED_HOSTS=""` blocks all RFC-1918/loopback/link-local addresses. Internal/private MCP servers are a *supported* use case, yet on first deploy every server registered with a `10.x/192.168.x/127.x` `proxy_pass_url` flips to `UNHEALTHY_SSRF_BLOCKED` and every internal agent reports unreachable. "Backwards-compatible" holds only for public-URL fleets. There is no monitor-only/dry-run mode and no guidance to pre-seed the allowlist from inventory.
- **`socket.getaddrinfo` has no timeout and runs on the async event loop.** A slow/hostile resolver blocks the loop far longer than the 2s `health_check_timeout_seconds`, stalling the whole batch of 10. Should not be deferred on the health path.
- **Double DNS resolution + DNS-rebinding TOCTOU.** `is_safe_url` resolves, then httpx re-resolves: 2x DNS per check (negating the "one extra lookup" framing) and a classic rebinding bypass. `follow_redirects=False` does not close it.
- **`follow_redirects=True` -> `False` is riskier than framed.** Real MCP servers behind ALBs/SSO/ingress commonly 301/302 (http->https, trailing-slash, auth bounce). Flipping to `False` can mark currently-healthy servers unhealthy. Ship behind observation, not assumption.
- **Monitoring/alerting under-specified for a security control.** Only a WARNING log and the health status; the metric is "future/optional." No CloudWatch metric filter/alarm, no split between "blocked-private" (misconfig) and "blocked-metadata/link-local" (attack).
- **`@lru_cache` reload story is thin.** Adding one internal server to `SSRF_ALLOWED_HOSTS` requires a full ECS task replacement; no hot-reload. Must be documented.
- **docker-compose snippet syntax is wrong.** LLD shows map form `SSRF_ALLOWED_HOSTS: ${...}`, but the compose files use list form `- GITHUB_EXTRA_HOSTS=${...}`.

**Cloud-metadata risk verification (ECS)**
- The gateway runs on Fargate (`ecs-services.tf` FARGATE capacity provider). On Fargate, task-role credentials come from the ECS container-credentials endpoint at `169.254.170.2`, not EC2 IMDS `169.254.169.254`. The good news: `169.254.170.2` is inside `169.254.0.0/16`, so `_is_private_ip` blocks it via `is_link_local` even without the hardcoded special-case. The design should call out `169.254.170.2` as the primary Fargate exfil target. The task role grants `secretsmanager:GetSecretValue` (Keycloak/OIDC secrets, admin password) and `bedrock-agentcore:*` on `*`, so the exposure is significant - the hardening is defensible.

**New libraries / infra dependencies**
- None. Verified stdlib + existing `httpx`; no new package, image, sidecar, or IAM change.

**Better alternatives considered**
- Defense-in-depth the design under-weights: disable Fargate IMDS at the task boundary + restrictive egress SGs neutralizes credential theft regardless of app bugs. Keep app-layer validation primary, but the network control should be a parallel recommendation, not a rejected alternative.
- Resolve-then-pin (connect to the vetted IP) closes rebinding and the double-DNS cost in one move.
- Observe-only rollout flag as a first phase.

**Recommendations**
1. Add a monitor-only rollout phase (e.g. `SSRF_ENFORCE=false` first); do not flip enforcement and `follow_redirects=False` in the same deploy.
2. Pre-flight the allowlist: inventory registered URLs that resolve to private space and seed `SSRF_ALLOWED_HOSTS`.
3. Bound DNS and get it off the event loop (`asyncio.to_thread` + explicit resolution timeout).
4. Define alerting now: ship `ssrf_blocked_total{path,reason}` + a CloudWatch metric filter/alarm; split private vs metadata/link-local.
5. Fix the docker-compose snippet to list form; re-word the metadata section around `169.254.170.2`.
6. State the rebinding limitation explicitly; recommend disabling Fargate IMDS + egress SGs as complementary controls.
7. Add a runbook entry for `UNHEALTHY_SSRF_BLOCKED`.

**Questions for author**
- How many currently-registered servers/agents resolve to private space (to size blast radius)?
- Are there production health endpoints that legitimately return 3xx?
- Is a rolling restart per allowlist change acceptable given the `lru_cache` pin?
- Is Fargate IMDS disabled independently?
- Will the block decision be a metric before GA?

**Verdict:** APPROVED WITH CHANGES

---

### Security Engineer Review (Cipher)

**Strengths**
- Reuses the deployed guard rather than reimplementing it; fail-closed posture preserved.
- Correctly identifies the credential-injection risk: `_initialize_mcp_session` (service.py:592) posts caller-supplied auth headers to `proxy_pass_url` with `follow_redirects=True`, so an SSRF here leaks credentials, not just recon.
- Separating `SSRF_ALLOWED_HOSTS` from `github_extra_hosts` is the right trust-boundary call.
- Disabling `follow_redirects` closes the "permitted host 30x-redirects to internal target" bypass.

**Concerns**
1. **DNS rebinding / TOCTOU is not addressed - the most serious gap.** `is_safe_url` resolves via `getaddrinfo`, then httpx opens its own connection and re-resolves. A low-TTL domain returns a public IP during validation and rebinds to `169.254.169.254`/`10.x` for the fetch. Re-checking only after redirects does nothing for rebinding within a single request. The real fix is resolve-once-and-pin: resolve, validate every A/AAAA record, then connect to the pinned IP with the hostname preserved in `Host`/SNI. As written, the design does not stop the primary attack the issue describes.
2. **IPv4-mapped IPv6 bypass of the metadata check.** `_is_private_ip` does `ip_str == "169.254.169.254"`. If `getaddrinfo` returns `::ffff:169.254.169.254` (dual-stack), the string equality is `False`, and the mapped address may not classify as link-local. Must unwrap `ip.ipv4_mapped` and re-check, and replace the string comparison with `169.254.0.0/16` membership. Add IPv6 test vectors (`[::1]`, `[::ffff:169.254.169.254]`, `[::ffff:10.0.0.1]`, `fe80::1`, `fd00::1`, `[::]`).
3. **Allowlist bypasses DNS entirely.** Acceptable only if allowlisted hosts are operator-owned. Document that constraint; ideally pin allowlisted hosts to configured IPs. Exact-match lowercase comparison is good; trailing-dot FQDNs are not normalized by `urlparse` (fail-safe direction for the allowlist, but add a test).
4. **Redirect handling.** `follow_redirects=False` is the right simple choice for cross-host. Residual: a same-host redirect to a different port. Low risk since the target is a validated public IP, but if per-hop re-validation is ever chosen, re-run `is_safe_url` on the full `Location` including port and cap hops.
5. **Alternate IP encodings.** `urlparse.hostname` does not normalize decimal/octal/hex IP literals; glibc `getaddrinfo` does resolve them, so the resolved-IP check catches them - but this relies on the resolver and needs explicit test coverage. `0.0.0.0`, `[::]` should be in the matrix.
6. **Credential leakage remains possible if the guard is bypassed** (see #1/#2). Because auth headers are injected before the request, any residual bypass is credential exfiltration, not just recon.
7. **ECS metadata: blocking `169.254.169.254` is necessary but IMDSv2 is the real control.** Require IMDSv2 (tokens required, hop limit 1) as a companion control. The guard blocks `169.254.0.0/16` via `is_link_local`, covering `169.254.170.2` (ECS task-role creds) - good, but IMDSv2 is unmentioned and egress is marked out of scope, which is too dismissive for a metadata-theft threat model.
8. **Fail-open vs fail-closed is sound; caching is fine.** `lru_cache` on `_trusted_domains` is not poisonable (config input, not request data). Confirm `lru_cache` is not applied to `is_safe_url` itself (it is not - correct).

Minor: iterating all `getaddrinfo` records and blocking if any is private is correct.

**New libraries / infra dependencies**
- None proposed - and that is a weakness for #1: a rebinding-safe fetch needs a custom httpx transport (pinned resolved IP + Host header). Doable with stdlib + httpx (no new dep), but the "zero new deps, minimal change" framing pushes toward the insufficient re-resolve pattern.

**Better alternatives considered**
- The four listed alternatives are correctly rejected. **Missing:** "resolve-once-and-pin" (the OWASP-recommended pattern and the actual fix for the primary threat) and IMDSv2 enforcement as the authoritative metadata defense.

**Recommendations**
1. **Must-fix:** close the rebinding/TOCTOU window - resolve once, validate all addresses, connect to a pinned validated IP.
2. **Must-fix:** unwrap IPv4-mapped IPv6 and replace the `== "169.254.169.254"` string test with `169.254.0.0/16` membership.
3. **Should-fix:** add IPv6, mapped, decimal/octal/hex, `0.0.0.0`, `[::]`, trailing-dot test vectors.
4. **Should-add:** require IMDSv2 on the ECS task; document `169.254.170.2`; treat egress restrictions as a recommended compensating control.
5. **Keep:** `follow_redirects=False`, dedicated setting, fail-closed, single choke point - but confirm `perform_immediate_health_check` and the `_update_tools_background` follow-up route through the same guard.
6. Document that allowlist entries must be operator-owned; prefer pinning to configured IPs.

**Questions for author**
1. How does the design prevent rebinding between the `is_safe_url` resolution and httpx's own resolution?
2. Has `_is_private_ip` been tested against `::ffff:169.254.169.254` / `::ffff:10.0.0.1`?
3. Is the health/agent socket dual-stack (AAAA/mapped records)?
4. Is IMDSv2 already enforced on the ECS tasks?
5. Does any legitimate health endpoint rely on a 3xx redirect?
6. Are credentials attached before or after connection? Can injection be deferred until connecting to a pinned IP?

**Verdict:** NEEDS REVISION

---

### SMTS Review (Sage)

**Strengths**
- The design is grounded in the actual codebase; every claim spot-checked is accurate (guard at `skill_service.py:71-192`, agent-card probe at `agent_validator.py:196-230`, choke point at `health/service.py:674`, the eight `follow_redirects=True` sites, `HealthStatus` in `constants.py`, `github_extra_hosts` at `config.py:292`).
- Sink coverage is broader and more correct than the task literally asked: three user-URL outbound sinks identified (`_check_endpoint_reachability`, `check_agent_health`, the `health/service.py` cluster), each routed through one choke point.
- Choke-point selection is sound; `mcp_endpoint`/`sse_endpoint` share the host, so host-level validation is defensible.
- The dedicated `SSRF_ALLOWED_HOSTS` setting is the right call.
- Module placement (`registry/utils/ssrf.py`) is consistent - `agent_validator.py` already lives in `registry/utils/`.
- Fail-closed semantics and the "don't tighten the schema" backwards-compat stance are correct.

**Concerns**
- **Blocking: the re-export does NOT preserve the settings-patching tests, and the design claims the opposite.** After the move, `_trusted_domains()` is defined in `registry.utils.ssrf` and reads the `settings` global in that module's namespace; patching `registry.services.skill_service.settings` has no effect on it. `test_skill_service_ssrf_allowlist.py` uses `@patch("registry.services.skill_service.settings")` in 11 tests and will read the real (empty) settings and fail. (The `getaddrinfo` patches survive because it is the same module object; the `settings` patches do not.) This violates the acceptance criterion.
- **`follow_redirects=False` on the health path is a real backwards-compat risk.** MCP/Starlette servers commonly 307-redirect `/mcp` -> `/mcp/` (trailing slash) and http->https. Flipping all eight calls can turn healthy servers unhealthy on upgrade, contradicting the "existing healthy registrations stay healthy" AC. Deserves a concrete mitigation (per-hop re-validation or same-host-redirect allowance), not the "no such server exists" assumption.
- **`UrlValidationError` is dead code as designed.** Either use it or drop it (YAGNI).
- **One transitive sink is hand-waved.** `_update_tools_background` (service.py:1072) opens a new connection to `proxy_pass_url` via `mcp_client_service.get_mcp_connection_result`, scheduled with `asyncio.create_task`. It runs after a healthy result and the connection is never re-validated, leaving a rebinding/TOCTOU window on a credential-carrying path.

**New libraries / infra dependencies**
- None, correctly. The 14-surface deployment expansion is wiring, not a dependency, but is a large blast radius for a security fix and the line numbers are estimates; full surface wiring could be a follow-up.

**Better alternatives considered**
- The four alternatives are the right set and correctly rejected.
- On the re-export mechanism: the honest path is to update the allowlist tests to point at `registry.utils.ssrf` and drop the "unchanged" claim, or read settings through an indirection both modules share.

**Recommendations**
1. Fix the settings-patch claim: update `test_skill_service_ssrf_allowlist.py` to patch `registry.utils.ssrf.settings` and revise the AC/Step 2 note; do not ship the false "will still work" assertion.
2. Replace blanket `follow_redirects=False` with same-host redirect re-validation, or gate the flip behind a setting and document the trailing-slash 307 case.
3. Either wire `UrlValidationError` into a raising call site or remove it.
4. Add an explicit line stating `_update_tools_background`'s connection is only transitively protected; decide whether to re-validate there.
5. Consider de-scoping the 14-surface checklist into a follow-up; land `ssrf.py` + guards + config + tests first.

**Questions for author**
- How does `mock_settings.github_extra_hosts = "github.mycompany.com"` reach a `_trusted_domains` defined in `registry.utils.ssrf`?
- Do any registered MCP servers rely on a 307/308 redirect on their health/`/mcp` endpoint?
- Should `perform_immediate_health_check` surface blocked status distinctly (422) or only via the health field?

**Verdict:** APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED WITH CHANGES | 0 | Reconcile agent/server status shapes; surface the blocked reason or drop the dashboard-visibility claim; give registration real feedback |
| Backend (Byte) | NEEDS REVISION | 1 | Fix the re-export test-patch break; move `getaddrinfo` off the event loop; document TOCTOU |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | Monitor-only rollout phase decoupled from the redirect flip; pre-seed allowlist; define alerting |
| Security (Cipher) | NEEDS REVISION | 2 | Close DNS-rebinding/TOCTOU (resolve-and-pin); fix IPv4-mapped-IPv6 metadata bypass; add IMDSv2 companion control |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Fix the settings-patch claim; mitigate `follow_redirects=False` regression; remove dead `UrlValidationError` |

## Consolidated Blocking Items (must-fix before implementation)

1. **Re-export test-patch break (Byte, Sage).** Moving the guard invalidates `@patch("registry.services.skill_service.settings")` in `test_skill_service_ssrf_allowlist.py`. Migrate those tests to patch `registry.utils.ssrf.settings` (and clear `registry.utils.ssrf._trusted_domains` cache), and correct the LLD's "tests pass unchanged" claim and AC.
2. **DNS rebinding / TOCTOU (Cipher, Byte, Circuit).** The resolve-then-fetch pattern re-resolves independently in httpx. Either implement resolve-once-and-pin (validate all resolved IPs, connect to the pinned IP with Host preserved) or explicitly document it as accepted residual risk backed by IMDSv2 + egress controls.
3. **IPv4-mapped IPv6 metadata bypass (Cipher).** Replace `ip_str == "169.254.169.254"` with `169.254.0.0/16` membership and unwrap `ip.ipv4_mapped` before the private-IP checks. Add IPv6/mapped/alternate-encoding test vectors.
4. **Blocking `getaddrinfo` on the async health hot loop (Byte, Circuit).** Wrap the guard in `asyncio.to_thread` with an explicit resolution timeout on the async paths.

## Non-Blocking but Recommended

- Monitor-only rollout phase + allowlist pre-seeding + alerting metric (Circuit).
- Reconcile agent-health and server-health status contracts and surface the blocked reason in the UI (Pixel).
- Mitigate the `follow_redirects=False` trailing-slash 307 regression risk (Sage, Circuit).
- Remove or wire in the unused `UrlValidationError` (Byte, Sage).
- Document `169.254.170.2` as the Fargate credential endpoint and recommend IMDSv2 (Circuit, Cipher).

## Next Steps

1. Revise the LLD to address the four consolidated blocking items (owner: design author).
2. Update acceptance criteria and the testing plan to include IPv6/mapped/encoding vectors and the migrated allowlist tests.
3. Decide the rebinding stance (implement resolve-and-pin vs. document residual risk + infra controls) with the security owner.
4. Re-review the revised LLD before implementation begins.
