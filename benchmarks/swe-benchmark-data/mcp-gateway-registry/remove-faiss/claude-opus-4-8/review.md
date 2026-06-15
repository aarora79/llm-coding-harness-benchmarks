# Expert Review: Remove FAISS and Consolidate Search

*Created: 2026-06-15*
*Reviewers: Pixel (Frontend), Byte (Backend), Circuit (SRE/DevOps), Cipher (Security), Sage (SMTS)*
*Artifacts reviewed: `./github-issue.md`, `./lld.md`*

---

## Frontend Engineer - Pixel

**Focus:** UI/UX, components, state, API integration

### Strengths
- The `/api/search`, tag search, and `/api/tags` response contracts are explicitly preserved, so the
  React search UI needs no changes. The LLD calls this out as an acceptance criterion.
- `relevance_score`, `match_context`, and `matching_tools` are kept, so result cards, score badges,
  and tool sub-lists render identically.

### Concerns
- **Score distribution may shift for the file backend.** FAISS used `IndexFlatIP` with a
  distance-to-relevance mapping and a keyword multiplier; the new path uses cosine + additive
  keyword boost (and possibly RRF normalization). Absolute `relevance_score` values, and therefore
  the visual ordering and any client-side score thresholds, can change even though the schema does
  not. If the UI hides results below a score cutoff, the visible set may differ.
- The LLD does not state whether the file backend will use `SEARCH_FUSION_METHOD=rrf` (which
  re-normalizes to [0,1]) or the legacy additive scoring. This determines what scores the UI sees.

### New libraries / infra dependencies
- None on the frontend.

### Better alternatives considered
- None for the UI.

### Recommendations
- Pin the file-backend scoring to the same `SEARCH_FUSION_METHOD` the Mongo path uses so scores are
  consistent across backends, and note expected score-range changes in the release notes.
- Add one UX test (already in `testing.md` section 3) that eyeballs the file-backend result ordering
  for a known query against the prior FAISS ordering.

### Questions for author
- Will the file backend honor `SEARCH_FUSION_METHOD`, or always use one method?

### Verdict: APPROVED

---

## Backend Engineer - Byte

**Focus:** API design, data models, business logic, performance

### Strengths
- Correctly identifies the real architectural defect: handlers import the `faiss_service` singleton
  directly and dual-write, bypassing the repository pattern. Routing everything through
  `get_search_repository()` is the right fix and pays down debt beyond just removing FAISS.
- Reusing `cosine_similarity` and the embeddings client mirrors the already-shipping MongoDB-CE
  `_client_side_search`, so the new file path is a known-good algorithm, not a novel one.
- The Step 1a extraction of shared scoring helpers into `search_scoring.py` is the correct DRY move
  and keeps the two backends behaviorally aligned.
- Net code deletion (~1900 lines) with no new dependency is a strong outcome.

### Concerns
- **`index_agent` signature mismatch.** The batch processor and agent routes currently call
  `faiss_service.add_or_update_entity(path, card.model_dump(), "a2a_agent", enabled)`, but
  `SearchRepositoryBase.index_agent(path, agent_card: AgentCard, ...)` expects an `AgentCard`
  object. The LLD flags this (Step 3) but the implementer must verify each call site passes the
  right type, or `AgentCard(**dict)` reconstruction is needed. This is the single most likely bug.
- **Dual-write de-duplication is error-prone.** Several `server_routes.py` sites already call
  `search_repo.index_server(...)` immediately after the FAISS call; others only call FAISS. The
  implementer must classify all 12 sites individually (delete-only vs replace) or risk either a
  missing index update or a double update. The LLD lists the line pairs, which helps, but this needs
  careful per-site diffing.
- **`search_mixed` vs `search` naming.** The old `FaissService.search_mixed` returned only
  `servers/tools/agents` (no skills/virtual_servers). The repository `search` returns five groups.
  Confirm no caller depended on the 3-group shape from the FAISS path (the read path already uses
  the repo, so this is likely fine, but worth a grep).
- **Tag search parity.** The old `FaissSearchRepository.search_by_tags` iterated
  `faiss_service.metadata_store`. The new repo must implement `search_by_tags` and `get_all_tags`
  over `self._docs` (the LLD includes them in the component list but the code sketch stops short of
  showing them). Make sure both are implemented, not inherited from the base (the base
  `search_by_tags` falls back to `search(" ".join(tags))`, which is exact-match-lossy).

### New libraries / infra dependencies
- None. Removes `faiss-cpu`. Good.

### Better alternatives considered
- The LLD's alternatives matrix is sound. Agree with rejecting a new embedded vector store.

### Recommendations
- Implement `search_by_tags` and `get_all_tags` explicitly on `FileSearchRepository` (exact tag
  match over `_docs`), matching the DocumentDB semantics, not the lossy base fallback.
- Add a unit test asserting `index_agent` accepts an `AgentCard` from the batch path.
- Re-grep for `search_mixed`, `metadata_store`, and `registry.search` before deleting the module.

### Questions for author
- Are there any non-HTTP callers of `faiss_service` (e.g. management scripts, the mcpgw MCP server
  tool implementation) beyond the three files listed?

### Verdict: APPROVED WITH CHANGES

---

## SRE / DevOps Engineer - Circuit

**Focus:** Deployment, monitoring, scaling, infrastructure

### Strengths
- Removing a native `faiss-cpu` wheel shrinks the image and removes a frequent source of
  platform/arch build friction (especially ARM64 builds noted in CLAUDE.md).
- Deployment Surface Checklist is concrete and references exact files (`build_and_run.sh`,
  `cli/service_mgmt.sh`, `terraform/aws-ecs/scripts/service_mgmt.sh`, `build-config.yaml`).
- Correctly notes no `docker-compose*.yml` change is needed (no FAISS volume mounts exist).

### Concerns
- **Startup re-index cost moves, not disappears.** For the file backend, every boot re-embeds all
  servers/agents/skills (already true with FAISS). With sentence-transformers this is CPU-bound and
  can be slow on cold start; the change does not alter this, but operators should not expect a
  speedup in boot time, only in image build time. Worth stating in release notes.
- **Telemetry/dashboard breakage.** `search_backend="faiss"` becomes `search_backend="file"`. Any
  Grafana/CloudWatch dashboard or alert filtering on the old value silently goes blank. This is an
  operational breaking change even though the API is not.
- **`faiss_search_time_ms` goes null.** Dashboards plotting that metric will flatline. The LLD's
  "keep the column" recommendation is the safe default, but the null-going-forward behavior must be
  documented so no one chases a phantom regression.
- **`verify_faiss_metadata` removal in service_mgmt scripts.** These functions gated success of
  add/remove operations on the `.faiss` metadata file existing. Removing them is correct, but the
  implementer must ensure the scripts still have a meaningful post-operation success check (or
  explicitly drop the check), not leave a dangling `if` / empty function.

### New libraries / infra dependencies
- None. Net removal.

### Better alternatives considered
- None.

### Recommendations
- Add explicit release-notes entries for: (1) `search_backend` telemetry value change, (2)
  `faiss_search_time_ms` no longer populated, (3) image no longer ships `faiss-cpu`.
- After editing the shell/Terraform scripts, run `bash -n` (per CLAUDE.md) and a smoke `terraform
  validate` on the ECS module.

### Questions for author
- Do any CI workflows or healthchecks assert the presence of `service_index.faiss` /
  `service_index_metadata.json`? Those would fail after removal.

### Verdict: APPROVED WITH CHANGES

---

## Security Engineer - Cipher

**Focus:** AuthN/AuthZ, validation, OWASP, data protection

### Strengths
- Pure refactor: no change to auth, scopes, or the access-control filtering in `search_routes.py`
  (`_user_can_access_server`). Result-level authorization is untouched.
- Removing a native dependency reduces the supply-chain attack surface and the CVE-tracking burden
  (`faiss-cpu` ships compiled binaries).
- No new secrets, env vars, or network calls. Embeddings client and AWS credential handling are
  unchanged.

### Concerns
- **Regex from user query.** Both the existing DocumentDB path and the new file path build a regex
  from query tokens (`token_regex = "|".join(re.escape(token) ...)`). Tokens are `re.escape`-d, so
  injection risk is low, but the file path runs Python `re` over in-memory strings per doc - confirm
  there is no ReDoS vector from pathological queries (token length is bounded by the tokenizer; keep
  it that way).
- **Embedding model download path.** `SentenceTransformersClient` may download from Hugging Face if
  the local model dir is absent. This is pre-existing behavior, not introduced here, but since the
  file backend now relies on it for all search (FAISS also did), ensure the offline/air-gapped story
  is documented (model is expected to be baked into the image).
- **`# nosec` hygiene.** The deleted FAISS file I/O may have carried `# nosec` annotations; ensure no
  Bandit suppressions are orphaned and that the new repo introduces none without justification.

### New libraries / infra dependencies
- None.

### Better alternatives considered
- None from a security standpoint.

### Recommendations
- Run `uv run bandit -r registry/` after the change and confirm no new findings and no orphaned
  `# nosec` comments referencing removed code.
- Add a unit test with a pathological/long query to confirm bounded tokenization (no ReDoS).

### Questions for author
- Is the embedding model guaranteed present in the deployed image, or can a file-backend deployment
  attempt an outbound HF download at search time?

### Verdict: APPROVED

---

## SMTS (Overall) - Sage

**Focus:** Architecture, code quality, maintainability

### Strengths
- The design treats FAISS removal as an opportunity to fix a genuine abstraction leak (direct
  singleton imports in HTTP handlers), not just a dependency deletion. That is the right altitude.
- Choosing "reuse the maintained alternative already in the repo" exactly matches the task intent
  and the existing MongoDB-CE client-side search, minimizing novel logic.
- Strong, ordered, compile-as-you-go implementation plan with explicit line references; an
  entry-level developer could execute it.
- Net deletion of ~1900 lines with one fewer dependency is a clear maintainability win.

### Concerns
- **Two-implementation drift risk if Step 1a is skipped.** If the shared scoring helpers are not
  extracted into `search_scoring.py` and the file repo inlines its own ranker, the two backends will
  drift over time. The LLD allows the inline shortcut; I would make the extraction mandatory, not
  optional, to lock the two paths together.
- **Scope breadth.** ~80 files touched (mostly docs). The doc edits are low-risk but easy to leave
  half-done; the grep-clean acceptance criterion is the right gate - enforce it in CI.
- **Behavioral test coverage for the file path.** The old `test_faiss_service.py` was 1131 lines;
  the new `test_file_search_repository.py` is budgeted at ~250. Ensure the cut is because the new
  repo is genuinely simpler (no index persistence, no ID management), not because coverage is being
  dropped. The file backend's coverage threshold (CLAUDE.md: ~35% min) must still hold.

### Recommendations
- Make `search_scoring.py` extraction a required step.
- Gate the PR on a `grep -riE 'faiss'` check (allowing only intentional "no-faiss" assertions in
  tests) wired into CI.
- Confirm the file backend honors `SEARCH_FUSION_METHOD` so the two backends are configurable the
  same way (also resolves Pixel's question).

### Questions for author
- Should `registry/search/` be fully removed, or retained as a namespace for future
  search-related, backend-agnostic utilities (e.g. the new `search_scoring.py` could live there
  instead of under `repositories/`)?

### Verdict: APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Pixel (Frontend) | APPROVED | 0 | Pin file backend to same `SEARCH_FUSION_METHOD`; note score-range change in release notes. |
| Byte (Backend) | APPROVED WITH CHANGES | 0 (1 must-fix) | Implement `search_by_tags`/`get_all_tags` explicitly; verify `index_agent` `AgentCard` typing; classify all 12 dual-write sites. |
| Circuit (SRE) | APPROVED WITH CHANGES | 0 | Release-notes for telemetry/metric changes; keep service_mgmt success checks coherent; `bash -n` + `terraform validate`. |
| Cipher (Security) | APPROVED | 0 | `bandit -r registry/`; bounded-tokenization ReDoS test; document offline model expectation. |
| Sage (SMTS) | APPROVED WITH CHANGES | 0 | Make `search_scoring.py` extraction mandatory; CI grep-clean gate; honor `SEARCH_FUSION_METHOD`. |

**Overall:** APPROVED WITH CHANGES. No hard blockers. The design is sound and well-scoped. The
must-address items before merge are: (1) explicit `search_by_tags`/`get_all_tags` on the file repo,
(2) correct `AgentCard` typing through `index_agent`, (3) careful per-site classification of the
dual-write call sites, (4) mandatory shared-scoring extraction to prevent backend drift, and (5)
operational release-notes for the telemetry value change.

## Next Steps
1. Author addresses the "APPROVED WITH CHANGES" items, primarily by tightening the file-repo method
   set and the dual-write site classification in the LLD.
2. Confirm no non-HTTP callers of `faiss_service` remain via a fresh grep.
3. Proceed to implementation (separate from this skill) following the LLD Steps 1-10, then execute
   `./testing.md`.
