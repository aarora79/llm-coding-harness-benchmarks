# Testing Plan: SSRF Hardening for Agent Card Fetch and Health Checks

*Created: 2026-06-24*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
Verify that outbound URLs derived from user-supplied input (agent-card fetch on `POST /agents/{path}/health`, and server health / tool-fetch on `proxy_pass_url`) are validated by the shared SSRF guard: private/loopback/link-local/reserved IPs and `169.254.169.254` are blocked, only http/https is allowed, redirect targets are re-validated, and the existing SKILL.md SSRF behavior is unchanged. Also verify the feature flag and host allowlist, and that blocked checks degrade to `unhealthy` rather than 5xx.

### Prerequisites
- [ ] Registry service running locally (FastAPI app from `registry/main.py`).
- [ ] An access token for an authenticated user with access to the test agent/server.
- [ ] Ability to register an agent/server with an arbitrary `url` / `proxy_pass_url`.
- [ ] `pytest` available for unit tests (`uv run pytest`).

### Shared Variables
```bash
export REGISTRY_URL="http://localhost:7860"            # adjust to your local registry port
export ACCESS_TOKEN="$(jq -r '.access_token' .oauth-tokens/ingress.json 2>/dev/null || echo "$REGISTRY_API_TOKEN")"
export AUTH=( -H "Authorization: Bearer $ACCESS_TOKEN" )
```

---

## 1. Functional Tests

### 1.1 curl / HTTP Tests

#### 1.1.1 Agent health check blocks a loopback URL
Register (or have) an agent whose `url` points at loopback, then health-check it.

```bash
# Assume agent registered with url = http://127.0.0.1:9/  (discard port)
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/ssrf-loopback/health" | jq .
```
- **Expected status:** HTTP 200 (the endpoint itself succeeds; the *check* fails closed).
- **Expected body:** `.status == "unhealthy"`, `.status_code == null`, `.detail` contains `SSRF` (e.g. "Blocked by SSRF policy").
- **Assertion:** the registry made **no** outbound connection to 127.0.0.1 (verify via the WARNING log line `SSRF protection: blocked ... 127.0.0.1`).

#### 1.1.2 Agent health check blocks the cloud metadata IP
```bash
# Agent registered with url = http://169.254.169.254/
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/ssrf-metadata/health" | jq .
```
- **Expected:** `.status == "unhealthy"`, `.detail` contains `SSRF`.
- **Assertion:** WARNING log shows the metadata IP was blocked; no GET to `169.254.169.254`.

#### 1.1.3 Agent health check blocks a non-http scheme
```bash
# Agent registered with url = file:///etc/passwd  (if registration allows it)
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/ssrf-file-scheme/health" | jq .
```
- **Expected:** `.status == "unhealthy"`, `.detail` contains `SSRF`.

#### 1.1.4 Agent health check allows a public URL (happy path)
```bash
# Agent registered with a reachable public url, e.g. https://example.com/a2a
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/public-agent/health" | jq .
```
- **Expected:** `.status` is `healthy` or `unhealthy` based on the real endpoint, but `.detail` does **not** contain `SSRF`, and the outbound request **was** attempted (no SSRF block log).

#### 1.1.5 Allowlisted private host passes
```bash
# Set SSRF_EXTRA_ALLOWED_HOSTS=internal-agent.local and restart registry.
# Agent registered with url = http://internal-agent.local/  (resolves to a private IP)
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/internal-agent/health" | jq .
```
- **Expected:** no SSRF block; outbound request attempted; status reflects reachability.

#### 1.1.6 Feature flag off restores legacy behavior
```bash
# Set SSRF_PROTECTION_ENABLED=false and restart registry.
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/ssrf-loopback/health" | jq .
```
- **Expected:** the registry attempts the loopback request (no SSRF block log). Confirms the flag is wired and rollback works.

#### 1.1.7 Negative / auth cases (unchanged contract)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST "$REGISTRY_URL/api/agents/does-not-exist/health" "${AUTH[@]}"   # expect 404
curl -sS -o /dev/null -w "%{http_code}\n" -X POST "$REGISTRY_URL/api/agents/ssrf-loopback/health"                  # no token -> expect 401/403
```

### 1.2 CLI Tests
**Not Applicable** - this change touches HTTP endpoints and a background task, not any CLI command. (Unit tests are run via `uv run pytest`, covered in section 6.)

---

## 2. Backwards Compatibility Tests

- **2.1 SKILL.md SSRF regression:** the existing SKILL.md fetch must behave identically after `_is_safe_url` is moved to `registry/utils/ssrf.py`.
  ```bash
  uv run pytest tests/ -k "skill and (ssrf or safe_url or url)" -v
  ```
  - **Expected:** all pre-existing SKILL.md SSRF/URL tests pass unchanged. (`skill_service._is_safe_url` is re-exported, so any test importing it still resolves.)

- **2.2 Health response shape unchanged:** `POST /agents/{path}/health` still returns the same keys (`agent_path`, `health_check_url`, `status`, `status_code`, `detail`, `response_time_ms`, `last_checked_iso`). A pre-change client parsing these keys still works. If the optional `reason_code` field from the review is added, it is additive and absent-by-default-safe.

- **2.3 Public-host health checks unaffected:** any server/agent previously registered with a public URL behaves exactly as before (section 1.1.4).

- **2.4 Default-on behavior is a known, intentional change:** servers registered with private URLs that were previously reported `healthy` will report `unhealthy` after upgrade unless allowlisted or the flag is disabled. This is expected; verify it is documented in release notes (see section 4).

---

## 3. UX Tests

- **3.1 Health badge in the web UI:** trigger a health check on an agent with a private URL and confirm the UI renders the `unhealthy` state without error. The `detail` text should be visible (tooltip / status panel) and read as a policy block, not a stack trace.
- **3.2 Error message clarity:** confirm `detail` is a clear, non-leaking message (e.g. "Blocked by SSRF policy" or "Blocked by SSRF policy: <host>") and does not expose internal stack traces or resolved internal IPs beyond what is needed for the operator.
- **3.3 (If `reason_code` adopted):** confirm the UI can distinguish `ssrf_blocked` from `timeout`/`http_error` and surfaces guidance to allowlist legitimate private hosts.

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring
- Confirm `.env.example` documents `SSRF_PROTECTION_ENABLED` (default `true`) and `SSRF_EXTRA_ALLOWED_HOSTS` (default empty).
- Confirm the registry service in `docker-compose.yml` passes these through (or relies on the documented defaults).
  ```bash
  grep -n "SSRF_PROTECTION_ENABLED\|SSRF_EXTRA_ALLOWED_HOSTS" .env.example docker-compose*.yml
  ```
- **Local-dev note:** if compose services reach each other by private DNS, verify the dev compose either sets `SSRF_PROTECTION_ENABLED=false` or pre-populates `SSRF_EXTRA_ALLOWED_HOSTS` so in-cluster health checks do not all flip to `unhealthy`.

### 4.2 Terraform / ECS wiring
- If the registry ECS task definition enumerates environment variables, confirm the two new variables can be set there (they are optional with safe defaults, so absence is acceptable).
  ```bash
  grep -rn "SSRF_PROTECTION_ENABLED\|SSRF_EXTRA_ALLOWED_HOSTS" docs/ *.tf 2>/dev/null
  ```
- **Expected:** documented in `docs/unified-parameter-reference.md`; no required Terraform change.

### 4.3 Helm / EKS wiring
- If a Helm chart exists, confirm `values.yaml` and the registry Deployment env block can carry the two variables.
- **Expected:** additive optional values; defaults preserve behavior when unset.

### 4.4 Deploy and verify
- After deploy with defaults, run section 1.1.1 (loopback blocked) and 1.1.4 (public allowed) against the deployed instance.
- Tail logs and confirm WARNING-level `SSRF protection: blocked ...` entries appear only for blocked hosts.

### 4.5 Rollback verification
- Set `SSRF_PROTECTION_ENABLED=false`, redeploy/restart, and re-run section 1.1.6. Confirm legacy (no-validation) behavior returns with zero code changes.

---

## 5. End-to-End API Tests

Full workflow: register an agent with a private URL, health-check it (blocked), allowlist the host, health-check again (attempted).

```bash
# Step 1: register an agent pointing at a private host (exact register payload per agent schema)
curl -sS "${AUTH[@]}" -H "Content-Type: application/json" \
  -X POST "$REGISTRY_URL/api/agents/register" \
  -d '{ "name": "e2e-ssrf", "url": "http://10.0.0.5/a2a", "...": "..." }' | jq .

# Step 2: health check -> expect SSRF block
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/e2e-ssrf/health" | jq '.status, .detail'
#   -> "unhealthy", detail contains "SSRF"

# Step 3: allowlist the host and restart (SSRF_EXTRA_ALLOWED_HOSTS=10.0.0.5 — or its hostname)
# Step 4: health check again -> outbound now attempted (status reflects reachability, no SSRF block log)
curl -sS "${AUTH[@]}" -X POST "$REGISTRY_URL/api/agents/e2e-ssrf/health" | jq '.status, .detail'
```
- **Assertions:** Step 2 produces a block log and no outbound connection; Step 4 produces an outbound attempt and no block log.

Server-side E2E (background health): register a server with `proxy_pass_url` on a private IP, trigger a health refresh, and confirm the stored `health_status` becomes `unhealthy` with an SSRF detail and that **no stored credentials were sent** (verify via the absence of an outbound request in logs / a mock collector).

---

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes - loopback/metadata/scheme blocked; public + allowlisted attempted; flag-off reverts.
- [ ] Section 2 (Backwards Compat) verified - SKILL.md SSRF tests pass unchanged; health response keys unchanged.
- [ ] Section 3 (UX) verified - blocked state renders cleanly; message is clear and non-leaking.
- [ ] Section 4 (Deployment) verified - both env vars documented; defaults safe; rollback works.
- [ ] Section 5 (E2E) verified - register -> block -> allowlist -> attempt; server path sends no credentials when blocked.
- [ ] Unit tests added under `tests/unit/utils/test_ssrf.py`:
  - [ ] private `10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12` blocked
  - [ ] loopback `127.0.0.1`, `[::1]` blocked
  - [ ] link-local `169.254.x.x`, `fe80::/10` blocked
  - [ ] reserved + metadata `169.254.169.254` blocked
  - [ ] IPv6 ULA `fd00::/8` (AWS IPv6 IMDS `fd00:ec2::254`) blocked
  - [ ] IPv4-mapped IPv6 `::ffff:127.0.0.1` blocked
  - [ ] decimal/octal/hex IP literals (`2130706433`, `0177.0.0.1`, `0x7f.1`) blocked
  - [ ] `0.0.0.0` blocked
  - [ ] non-http scheme (`file://`, `gopher://`, `ftp://`) blocked
  - [ ] missing hostname blocked
  - [ ] DNS-resolves-to-private (monkeypatch `socket.getaddrinfo`) blocked
  - [ ] public host allowed
  - [ ] allowlisted host with private IP allowed; `lru_cache` cleared between cases
- [ ] Unit tests for call sites:
  - [ ] agent health: blocked URL -> `unhealthy` + SSRF detail, no outbound (mock httpx)
  - [ ] agent health: HEAD fallback also guarded
  - [ ] server health: blocked `proxy_pass_url` -> `(False, "unhealthy: blocked by SSRF policy")`
  - [ ] server health: resolved `mcp_endpoint`/`sse_endpoint` override is validated (bypass test)
  - [ ] feature-flag off bypasses validation at all call sites
- [ ] `uv run pytest tests/` passes with no regressions.
