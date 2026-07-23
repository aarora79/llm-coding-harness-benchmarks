# Expert Review: Remove FAISS from the codebase and documentation

*Created: 2026-07-23*
*Reviewed artifacts: `./github-issue.md`, `./lld.md`*

This review was conducted by five reviewer personas independently, then synthesized. The most significant finding, raised by three of five reviewers, is that the LLD's premise that "search consumers are already backend-agnostic" is false: `faiss_service` is called directly from ~22 production sites in `registry/api/server_routes.py`, `registry/api/agent_routes.py`, and `registry/services/agent_batch_item_processor.py`. The LLD has been corrected after this review to address the confirmed blockers (see the "Post-Review LLD Corrections" section at the end).

---

## 1. Frontend Engineer (Pixel)

### Strengths
- The change is genuinely frontend-transparent. `frontend/src/hooks/useSemanticSearch.ts` posts to `/api/search/semantic` and types the response as `SemanticSearchResponse` without declaring `search_mode`, so response-shape stability means zero UI impact.
- A case-insensitive grep for `faiss` across all of `frontend/` returns nothing. No user-facing copy, label, or fixture mentions FAISS.
- The UI already treats search as a generic backend: `SemanticSearchResults.tsx` renders results and errors with no engine name; the duplicate-check flow already models graceful degradation via `similarity_search_available`.

### Concerns
- `useSemanticSearch.ts` surfaces `err.response?.data?.detail` verbatim into a red banner. The reworded generic 503 handler must return user-appropriate `detail`, not a raw RuntimeError/allowlist string. Pre-existing, but the design's 503 emphasis touches it.
- On a search error, `DiscoverTab.tsx` shows only the error banner with no local fallback (the Dashboard does compensate). Pre-existing; unchanged by this work.

### New libraries / infra dependencies
None. No new API fields consumed, no endpoints, no build-tooling changes touch `frontend/`.

### Better alternatives considered
From a frontend lens the backend alternatives are equivalent. Fail-fast is actually the friendliest to the SPA: either search works or the app is not deployed, so users never see an intermittently-503ing search box.

### Recommendations
- No frontend code changes required; do not block on the UI.
- Ensure the generic 503 `detail` is human-readable.
- Optionally add a `useSemanticSearch` test asserting graceful 503 handling (parallels existing duplicate-check tests).

### Questions for author
- Post-removal, can `/api/search/semantic` ever 503 to a live SPA, or is search guaranteed available whenever the process is up (fail-fast at boot + lexical fallback)?
- Will the response keep `search_mode: "hybrid"`?
- Does `similarity_search_available` remain accurate (driven by embedder reachability, not FAISS presence)?

### Verdict: APPROVED

---

## 2. Backend Engineer (Byte)

### Strengths
- Correctly identifies `registry/search/service.py:8` as the sole `import faiss` and `faiss_service` (`:1201`) as the singleton.
- `DocumentDBSearchRepository.search()` genuinely covers all five entity types (`search_repository.py:2064-2069`, emits servers/tools/agents/skills/virtual_servers at 2250-2256) and degrades gracefully (`_lexical_only_search` at 2028-2036; `_client_side_search` on MongoDB CE at 2409-2442).
- `search_by_tags`/`get_all_tags` return the same grouped-dict shape the route consumes.
- Fail-fast over silent-reroute is well reasoned.

### Concerns
- **C1 (Blocker):** The LLD claim that consumers are backend-agnostic is false. `faiss_service` is called directly at 44 occurrences across `server_routes.py` (e.g. 847, 1343, 1643, 1752, 2386, 2630, 2760, 1827, 4178, 3808), `agent_routes.py` (631, 1152, 1601, 1855), and `agent_batch_item_processor.py` (228, 340). Function-local imports mean deletion breaks these at request time, not import time. LOC/effort is materially understated.
- **C2 (Blocker):** Several sites index ONLY into FAISS with no `search_repo.index_*` counterpart (e.g. `server_routes.py:1343`, `1643`, `2630`, `2760`; `agent_routes.py:631`, `1152`, `1601`). Deleting them silently stops updating the DocumentDB index. There is likely already a latent inconsistency on mongodb-ce today.
- **C3 (Major):** "Behave identically" is not literally achievable. FAISS uses a multiplicative keyword boost; DocumentDB uses RRF (k=60) plus soft caps and a display floor. Ranking/scores/returned set will differ. Reword the acceptance criterion to schema-compatibility with possible ranking differences.
- **C4 (Minor):** `_validate_storage_backend` still coerces empty/None to `"file"` (`config.py:825-826`); update it or document why it stays.
- **C5 (Minor):** The `main.py` else-branch preservation is correct; note the fresh-DB-with-file-data edge is handled by the migrate script (out of scope).

### New libraries / infra dependencies
None added. `faiss-cpu` removal safe. `sentence-transformers`/`torch` correctly retained (`embeddings/client.py:89`, `scripts/evaluate_search.py:134`). `scikit-learn` has zero imports; removal plausible but correctly gated on verification.

### Better alternatives considered
A preparatory commit that refactors the ~40 direct `faiss_service.*` sites onto `get_search_repository()` before deleting `service.py`, avoiding a broken intermediate state. The method-name mismatch (`add_or_update_service`/`save_data` vs `index_server`) means the repoint is not a rename; a temporary shim mapping the old surface onto the repository would minimize churn.

### Recommendations
1. Re-scope Step 2 with an explicit inventory of the ~22-44 production `faiss_service` sites and a per-site migration plan.
2. Audit FAISS-only index sites and add the `search_repo.index_*`/`remove_entity` equivalent.
3. Sequence: commit 1 refactors call sites (behaviour-preserving on both backends); commit 2 deletes FAISS.
4. Reword acceptance criteria (drop "behave identically"); add a search-quality eval using `scripts/evaluate_search.py`.
5. Fix the validator fallback.
6. Keep the CPU-image model bake.

### Questions for author
- Were the ~40 direct call sites intentionally omitted or missed?
- Are the FAISS-only index sites already stale on mongodb-ce, and is fixing that in scope?
- Does the dedup flow rely on FAISS-specific scoring (the `dedup_score_threshold` at `config.py:728`)? RRF normalization could shift which duplicates cross 0.7.
- Should call-site refactor be a preparatory PR?

### Verdict: NEEDS REVISION

---

## 3. SRE / DevOps Engineer (Circuit)

### Strengths
- `factory.py:132-151` is exactly as described; fail-fast in this one function is the right chokepoint, reached at boot via `main.py:496`.
- The startup simplification is well-targeted; the DocumentDB path already skips re-index (`main.py:548-552`).
- Confirms the dead `rebuild_index()` bug.
- The default-compose safety argument holds: all three stacks bundle `mongodb` + `mongodb-init` with `depends_on` gating (`docker-compose.yml:333`, prebuilt 227-228, podman 220-221).
- Terraform already defaults to `documentdb` (`variables.tf:399`); EFS already removed.

### Concerns
- **High:** The code-default change is largely inert for compose; the real switch is the six `STORAGE_BACKEND=${STORAGE_BACKEND:-file}` env lines (`docker-compose.yml:198,454`; prebuilt 105,319; podman 95,318). Flip all six together or the default stack boots mongodb but injects `file` and crash-loops.
- **High:** A real Terraform deployment boots on `file` today with NO database provisioned (`documentdb_admin_password` "Not required when using file storage backend", `variables.tf:331`). Post-upgrade it crash-loops; remediation is provisioning DocumentDB (cost, password, shards), not a one-line flip. The rollout plan undersells this.
- **High:** The telemetry schema tightening (`schemas.py:267` `^(faiss|documentdb)$`) is backward-incompatible. Older agents still emit `faiss` (`telemetry.py:731`). If the collector is redeployed with `^documentdb$` before the fleet upgrades, their telemetry is rejected/dropped. Widen, never narrow.
- **Medium:** Removing the model bake (`Dockerfile.registry-cpu:71-75`) degrades cold-start and breaks air-gapped installs; `registry-entrypoint.sh:250-264` only warns, does not download. The issue/LLD are contradictory ("remove/gate" vs Open Question "keep"). Resolve to keep on the CPU image.
- **Low:** No dedicated FAISS volume exists; `.faiss` files lived under the generic `servers` bind mount. This is comment/config cleanup, not a volume removal.
- **Low:** Split validation surface: pydantic passes `file`, then the factory dies with a different message style. Make the factory message excellent.

### New libraries / infra dependencies
None added. `scikit-learn` appears unused but is likely transitive via `sentence-transformers`; only remove after `uv lock` confirms.

### Better alternatives considered
A deprecation-window soft-fail: log a WARNING and 503 only `/api/search/semantic` for one release rather than hard-crashing a whole registry over the search subsystem. For telemetry, widen-never-narrow the ingest pattern.

### Recommendations
1. Make the six compose env edits the primary deliverable; add a CI grep asserting no compose injects `:-file`.
2. Do NOT tighten `schemas.py:267`; only stop emitting `faiss`.
3. Keep the model bake on the CPU image; update entrypoint messaging.
4. Escalate the Terraform `file`-break in release notes with a migration runbook; consider one-release soft-fail.
5. Document a rollback procedure.
6. Make the factory `RuntimeError` operator-grade.

### Questions for author
- What is the documented rollback command, and does anything block a clean downgrade?
- How many deployments run `file` today (especially Terraform/ECS with no DocumentDB)?
- Is the telemetry Lambda deployed independently, and can it stay permissive until the last agent upgrades?
- Are there air-gapped operators relying on the baked model?
- Does metrics-service ingest reject payloads missing `faiss_search_time_ms`, or accept `search_time_ms` alongside?

### Verdict: NEEDS REVISION (approve with conditions)

---

## 4. Security Engineer (Cipher)

### Strengths
- Genuine attack-surface reduction: removes a native C++/BLAS library and on-disk index deserialization (`service_index.faiss`).
- Removes dead/broken `rebuild_index()`.
- Fail-fast over silent degradation is the correct security choice (avoids masking a misconfiguration that operators might believe enforces access control).
- The factory error echoes only non-secret config, matching the reviewed precedent at `config.py:822-823`.
- TLS-on default preserved (`documentdb_use_tls=True`, `config.py:841`).
- Route auth untouched and verified: `nginx_proxied_auth` (`search_routes.py:381`), `_user_can_access_server`, `filter_tools_for_user`, and the `include_*` flags pass through unchanged.

### Concerns
- **Blocker (availability):** Under-counted `faiss_service` imports in `server_routes.py` (16), `agent_routes.py` (4), `agent_batch_item_processor.py` (2). Function-local imports mean the app boots then 500s on registration/indexing paths. The acceptance grep (`import faiss`) would miss them.
- **Insecure-by-default MongoDB:** the default flip steers more operators onto the shipped compose Mongo, which runs with empty `DOCUMENTDB_USERNAME`/`PASSWORD` (`docker-compose.yml:54-55`), no `--auth`, and published port 27017 (`:20-22`). Pre-existing, but the LLD should document it and warn in release notes; consider enabling auth by default.
- **503 handler `exc_info`:** the reworded handler must not log full pymongo exceptions that could embed a `user:pass@host` URI (parity with the URI-scrubbing at `system_routes.py:213-222`).
- **Telemetry tightening** can reject legacy payloads; make legacy acceptance mandatory, not optional.

### New libraries / infra dependencies
None added. `numpy` is retained via `embeddings/client.py:16`, independent of FAISS. `scikit-learn` removal is a further small reduction, gated on verification. `torch` correctly retained but remains the dominant native/CVE surface, so the supply-chain win is bounded, not total.

### Better alternatives considered
Pair the default flip with mandatory MongoDB auth and generated credentials in the shipped compose. For telemetry, accept-on-read rather than reject.

### Recommendations
1. **Blocker:** repoint all `faiss_service` sites; add a CI grep gate broader than `import faiss` (also match `search.service import faiss_service`).
2. Document the insecure-by-default MongoDB posture; consider enabling auth.
3. Drop `exc_info=True` on the raw exception in the reworded 503, or scrub URIs.
4. Keep `faiss` accepted on read in the collector during rollout.
5. Confirm `scikit-learn` is not a required transitive dep before removing.
6. State plainly that torch remains the dominant retained native/CVE surface.

### Questions for author
- What is the repoint target for the inline `faiss_service` imports, and is it tested?
- Will the default flip enable MongoDB auth in compose, or is unauthenticated-localhost intentional for dev only?
- Any PII/secrets in embedded text now that persistence moves from on-disk index to the DB (data-at-rest classification)?
- Does the `Semantic search: {query[:50]}` audit log change exposure now that all search is DB-backed?

### Verdict: APPROVED WITH CHANGES

---

## 5. SMTS / Overall (Sage)

### Strengths
- Problem correctly framed; the replacement is real and already the production default.
- `SearchRepositoryBase` (`interfaces.py:1001-1123`) is genuinely clean; deleting the FAISS implementation in isolation is low-risk.
- The factory rewrite is idiomatic; alternatives are honest; the `main.py` agent-state detail is right; line references are accurate.

### Concerns
- **The single most load-bearing claim is false** and invalidates the file-change list: ~22 production `faiss_service` call sites across three files the table never lists. Deleting `service.py` makes them `ImportError` at request time.
- **The codebase currently dual-writes** (`server_routes.py:847` FAISS then `853-854` `search_repo.index_server`) but some sites are FAISS-only (`1343`, `1643`, `4178`, `3808` `save_data()`, `agent_routes.py:631`/`1855`, batch `228`/`340`). Deleting FAISS-only lines silently drops indexing for register/delete/agent flows.
- **Fail-fast has a wider blast radius than "search only."** `get_search_repository()` is called in service constructors (`server_service.py:22`, `agent_service.py:27`, `semantic_search_service.py:27`, `skill_service.py:1263`) built at startup, so raising in the factory bricks the whole `file` backend, contradicting the "file storage remains" scope. Defer the raise to first search use, or use a `NullSearchRepository`.
- **`RuntimeError` for a config error** is the wrong type; the repo already raises `ValueError` in `_validate_storage_backend`. Also, the `search_routes.py:439-444` `except RuntimeError` could swallow the fail-fast message into a generic 503.
- **Metrics sequencing:** ingest must accept `search_time_ms` before the app emits it.

### New libraries / infra dependencies
None added; net removal. `scikit-learn` unused under `registry/` but gate removal on transitive need. `sentence-transformers`/`torch` correctly retained.

### Better alternatives considered
Sequence the work: (1) migrate the ~22 call sites onto `SearchRepositoryBase`, (2) add a `NullSearchRepository` for `file` (honors "keep file storage", keeps it bootable, returns empty results with a warning), (3) delete FAISS code + dep, (4) flip the default separately. A `NullSearchRepository` fits the base-class default pattern better than raising.

### Recommendations
1. Add `server_routes.py`, `agent_routes.py`, `agent_batch_item_processor.py` to the File Changes table with per-site direction; enumerate FAISS-only sites needing a functional replacement.
2. Make search-repo acquisition lazy or provide a `NullSearchRepository`; if bricking `file` is intended, say so and update Non-Goals.
3. Raise `ValueError` via the existing validator pattern; re-examine the 503 handler.
4. Add an acceptance criterion/test that registration and deletion still update the search index.
5. Sequence the metrics ingest change before emit.

### Questions for author
- How were the three files missed given the LLD's own claim? Was the grep run against the current tree?
- For FAISS-only sites, delete or replace with `search_repo`?
- Is disabling the `file` backend end-to-end intended? If yes, why keep `"file"` in `ALLOWED_STORAGE_BACKENDS`?
- Was `NullSearchRepository` considered?

### Verdict: NEEDS REVISION

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | No UI changes; keep 503 `detail` human-readable |
| Backend (Byte) | NEEDS REVISION | 2 | Inventory + migrate ~22-44 direct `faiss_service` sites; reword "behave identically" |
| SRE (Circuit) | NEEDS REVISION | 3 | Flip all six compose env lines; do not narrow telemetry schema; keep CPU model bake; Terraform migration runbook |
| Security (Cipher) | APPROVED WITH CHANGES | 1 | Repoint call sites + broaden grep gate; document insecure-default Mongo; scrub 503 `exc_info` |
| SMTS (Sage) | NEEDS REVISION | 1 | NullSearchRepository or lazy acquisition; `ValueError` not `RuntimeError`; per-site migration plan |

**Consensus blocker (4 of 5 reviewers):** the LLD's "backend-agnostic consumers" premise is factually wrong. There are ~22 direct `faiss_service.*` production call sites in `server_routes.py`, `agent_routes.py`, and `agent_batch_item_processor.py`, some of which index only into FAISS with no DocumentDB counterpart. Deleting `service.py` as written breaks server/agent registration, toggle, refresh, and deletion at request time and silently drops DocumentDB indexing for the FAISS-only sites.

## Next Steps
The following blockers were fed back into the LLD before delivery (see below):
1. Add the three missed files with a per-site migration plan and enumerate FAISS-only index sites.
2. Replace the `RuntimeError`-in-factory fail-fast with a `NullSearchRepository` for `file` (honors the "keep file storage" scope, keeps the backend bootable) plus a startup warning, and defer any hard failure decision explicitly.
3. Sequence the change: preparatory call-site refactor onto `SearchRepositoryBase`, then deletion, then the default flip.
4. Reword the acceptance criterion from "behave identically" to schema-compatible with possible ranking differences; add a search-quality eval.
5. Widen-not-narrow the telemetry collector schema; only stop emitting `faiss`.
6. Keep the CPU-image model bake; add the Terraform/`file` migration runbook to release notes; sequence the metrics ingest change.

---

## Post-Review LLD Corrections

In response to the consensus blockers, `lld.md` was updated as follows (this section records the delta so reviewers can confirm the gaps were closed):
- **Codebase Analysis / Integration Points:** added the ~22 direct `faiss_service` call sites in `server_routes.py`, `agent_routes.py`, and `agent_batch_item_processor.py`, including the dual-write vs FAISS-only distinction (`server_routes.py:847` dual-writes; `:1343` is FAISS-only).
- **Architecture:** replaced the "factory raises RuntimeError for file" decision with a `NullSearchRepository` returned for the `file` backend, plus a startup WARNING, so file storage stays bootable (honors the stated scope). The hard-fail is documented as a rejected alternative.
- **Implementation Details:** added a preparatory Step 0 that refactors all direct `faiss_service.*` sites onto `get_search_repository()` before deletion, with per-site guidance for dual-write vs FAISS-only sites; changed the config validator note; sequenced the work into ordered commits.
- **File Changes:** added `server_routes.py`, `agent_routes.py`, `agent_batch_item_processor.py`, and `NullSearchRepository` (new file) to the tables; updated the LOC estimate.
- **Deployment:** made the six compose env-line edits mandatory and primary; kept the CPU-image model bake; specified widen-not-narrow for the telemetry schema; added the Terraform `file`-backend migration runbook to the rollout plan.
- **Acceptance / Testing:** reworded "behave identically" to schema-compatibility with possible ranking differences; added index-write coverage and a search-quality eval; broadened the removal grep gate.
