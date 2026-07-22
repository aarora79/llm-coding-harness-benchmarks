# Expert Review: Remove FAISS from the Codebase

*Created: 2026-07-22*
*Author: Claude*
*Related LLD: `./lld.md`*

## Review Personas

| Role | Reviewer | Focus |
|------|----------|-------|
| Frontend Engineer | Pixel | UI/UX, components, API integration |
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

---

## 1. Pixel (Frontend Engineer)

### Strengths
- No frontend changes required. The FAISS removal is entirely backend/infrastructure.
- The search API response shape is preserved, so the frontend (React/TypeScript UI)
  continues to work without modification.
- The LLD correctly identifies that no new API endpoints or signature changes are needed.

### Concerns
- None significant.

### Recommendations
- Verify the frontend E2E tests that exercise search still pass after this change.
  The LLD should explicitly mention running Playwright E2E tests.

### Questions for Author
- Does the frontend ever display "FAISS" in UI strings (e.g., "Powered by FAISS")?
  Review `frontend/src/` for any hardcoded FAISS references. The LLD should
  explicitly check for this.

### Verdict: APPROVED

---

## 2. Byte (Backend Engineer)

### Strengths
- The LLD correctly identifies the factory pattern as the key integration point
  for search backend routing.
- Reusing `DocumentDBSearchRepository` for the file-backend search path is a
  sensible choice that consolidates on a single tested implementation.
- The LLD provides detailed line-level guidance for factory.py changes.
- Graceful degradation (lexical-only fallback when DocumentDB is unavailable)
  matches the prior FAISS fallback behavior.

### Concerns
- **Concern 1 (Medium):** The LLD says `registry/repositories/file/search_repository.py`
  will be deleted entirely, and the factory will always route to
  `DocumentDBSearchRepository`. But the `file` storage backend is still used by
  non-search repositories (ServerRepository, AgentRepository, SkillRepository,
  etc.). The LLD should explicitly confirm that deleting
  `file/search_repository.py` does not break other file-based repositories that
  might import from the `file/` package.

  **Recommendation:** Before deleting the file, check what else (if anything) lives
  in `registry/repositories/file/`. If other repositories exist there (e.g.,
  `file/server_repository.py`), the directory should stay and only
  `search_repository.py` should be deleted.

- **Concern 2 (Low):** The LLD says `faiss-cpu` should be removed from
  `pyproject.toml`. It does not mention that `sentence-transformers` depends on
  `torch`, and `torch` is a heavy dependency (~2GB) that also complicates Docker
  builds. If the goal is build simplicity, the team might consider migrating
  embeddings to LiteLLM-only (cloud providers). This is out of scope for this
  task but worth noting.

- **Concern 3 (Low):** The LLD mentions updating `registry/servers/mcpgw.json`
  server schema comments. This is a JSON configuration file -- ensure the change
  does not affect runtime behavior. The LLD correctly notes these are comments
  only.

### Recommendations
- Before deleting `registry/repositories/file/search_repository.py`, run:
  ```bash
  grep -rn "from.*file.search_repository" registry/
  ```
  to confirm no other file in the `file/` directory imports from it.
- The LLD should verify that `registry/repositories/file/__init__.py` does not
  export `FaissSearchRepository`. If it does, the `__init__.py` needs updating
  (not deletion).

### Questions for Author
- Does `registry/repositories/file/` contain any files other than
  `search_repository.py`? (The LLD assumes it does but doesn't confirm.)
- After removing `faiss-cpu`, will `uv tree` show any transitive FAISS
  dependencies? If so, which package pulls it in?

### Verdict: APPROVED WITH CHANGES

**Required changes before merge:**
1. Verify `registry/repositories/file/` contents before deleting `search_repository.py`
2. Check `file/__init__.py` for exports
3. Run `uv tree` after `faiss-cpu` removal to check for transitive dependencies

---

## 3. Circuit (SRE/DevOps Engineer)

### Strengths
- Removing FAISS directly improves Docker build simplicity and reduces image size
  (FAISS native libraries are not needed).
- The LLD correctly identifies all Docker, Terraform, and Helm comment updates.
- The metrics service schema cleanup (`faiss_search_time_ms`) is handled.
- The LLD provides explicit verification commands including `uv sync` and
  dependency tree checks.

### Concerns
- **Concern 1 (Medium):** The LLD says operators using `storage_backend=file`
  (local development) will lose vector search unless they configure a MongoDB
  connection. The Docker Compose setup includes MongoDB, so this is not a
  regression for Docker Compose users. But operators who run the registry outside
  Docker Compose (e.g., `uv run` directly) will lose vector search. The LLD
  should recommend operators add `DATABASE_URL` or `DOCUMENTDB_*` environment
  variables for local development.

- **Concern 2 (Medium):** The LLD does not address the `uv.lock` regeneration
  process. After removing `faiss-cpu` from `pyproject.toml`, `uv lock` must be
  run and the resulting `uv.lock` committed. The LLD should explicitly mention
  this step.

- **Concern 3 (Low):** The Terraform variable `OPERATIONS.md` references the
  registry image size (~4.6GB). After removing FAISS (and potentially torch
  in the future), the image size may decrease. The LLD should note that this
  figure may need updating.

### Recommendations
- Add a migration guide section to the release notes: "For local development
  without Docker Compose, set `DATABASE_URL=mongodb://localhost:27017/registry`
  to retain vector search."
- Update the release note to explicitly state that `uv lock` must be run after
  the change.
- Consider adding a startup check that warns if `storage_backend=file` and no
  DocumentDB is configured (similar to the existing embedding-failure warning).

### Questions for Author
- What is the approximate Docker image size reduction from removing FAISS?
- Should the Dockerfile or docker-compose be updated to remove any FAISS-specific
  system dependencies? (The FAISS agent report did not find any native library
  installs in the Dockerfile, suggesting FAISS is installed purely via pip/uv.)

### Verdict: APPROVED WITH CHANGES

**Required changes before merge:**
1. Add migration guidance for local development operators in release notes
2. Explicitly document `uv lock` step in the implementation plan
3. Verify Dockerfile has no FAISS-specific system dependencies to remove

---

## 4. Cipher (Security Engineer)

### Strengths
- FAISS removal does not introduce any new security surface area.
- The LLD correctly preserves the search API authentication and authorization
  (all search endpoints remain behind existing auth middleware).
- Removing FAISS code reduces the attack surface by eliminating untrusted
  input paths (FAISS index files were written from application state, which is
  benign but adds surface area).

### Concerns
- **Concern 1 (Low):** The LLD removes `verify_faiss_metadata()` from shell
  scripts. This function verified FAISS index consistency, which included checks
  for stale or corrupted index files. The LLD should confirm that DocumentDB
  search does not have a similar consistency check that is being lost.

- **Concern 2 (Low):** The metrics service schema change (removing
  `faiss_search_time_ms`) means historical metrics data will have a NULL column
  for this field. If there are dashboards or alerting rules that query this
  column, they will need updating.

### Recommendations
- Verify that no `faiss` index files (`.faiss`, `.index`) are generated at
  runtime and stored in application directories. If they are, the cleanup
  procedure should delete them.
- Add a note to the release notes about the metrics service schema change for
  operators using Grafana dashboards or Prometheus queries that reference
  `faiss_search_time_ms`.

### Questions for Author
- Are there any `*.faiss` or `*.index` files generated at runtime that would
  need cleanup in deployment?
- Do any Grafana dashboards or Prometheus alerts reference `faiss_search_time_ms`?

### Verdict: APPROVED

---

## 5. Sage (SMTS - Overall)

### Strengths
- The LLD is thorough and specific, providing line-level guidance for every file
  change.
- The dependency analysis is accurate: `faiss-cpu` is the only Python dependency
  being removed; `sentence-transformers` and `torch` are correctly retained.
- The factory pattern change is minimal and correct: replacing one import with
  another in the `else` branch.
- The LLD correctly identifies that `SearchRepositoryBase` interface is
  unchanged, so no interface-level changes are needed.
- The comparison matrix for alternatives is fair and well-reasoned.

### Concerns
- **Concern 1 (Medium):** The LLD recommends deleting the entire
  `registry/search/service.py` file. This file contains not just `FaissService`
  but also the shared `faiss_service` singleton instance at module level.
  However, after reviewing the file, `faiss_service = FaissService()` is the
  only global. The LLD should explicitly note that no other classes or functions
  in this file need preservation.

  **Recommendation:** Before deleting, run:
  ```bash
  grep -c "^class \|^def " registry/search/service.py
  ```
  to confirm the file only contains `FaissService` and helper classes. If there
  are standalone utility functions, they should be migrated.

- **Concern 2 (Low):** The LLD says `cli/service_mgmt.sh` and
  `terraform/aws-ecs/scripts/service_mgmt.sh` should have `verify_faiss_metadata()`
  deleted. These are bash scripts -- verify they do not contain any critical
  operational logic beyond the FAISS verification that would be lost.

- **Concern 3 (Low):** The LLD does not address the `docs/llms.txt` file, which
  is a comprehensive reference that includes FAISS references. This file is
  likely consumed by LLM agents and should be updated to remove FAISS mentions.

### Recommendations
- Verify `registry/search/service.py` contains only FAISS-related code before
  deleting it.
- Update `docs/llms.txt` to replace FAISS references with DocumentDB.
- Consider whether `registry/search/` directory becomes empty after deleting
  `service.py`. If so, the entire directory can be removed (including
  `__init__.py` if it exists).

### Questions for Author
- What is the full content of `registry/search/__init__.py`? Does it export
  anything besides `faiss_service`?
- Does `registry/search/` contain any other files besides `service.py`?

### Verdict: APPROVED WITH CHANGES

**Required changes before merge:**
1. Verify `registry/search/service.py` has no preserve-worthy code before deletion
2. Verify `registry/search/__init__.py` contents and decide on directory removal
3. Update `docs/llms.txt`
4. Verify `service_mgmt.sh` scripts contain no critical non-FAISS logic

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Pixel (Frontend) | APPROVED | 0 | Verify frontend E2E tests still pass |
| Byte (Backend) | APPROVED WITH CHANGES | 2 | Check `file/` directory contents; verify no transitive FAISS deps |
| Circuit (SRE) | APPROVED WITH CHANGES | 2 | Add local dev migration guide; document `uv lock` step |
| Cipher (Security) | APPROVED | 0 | Note metrics schema change in release notes |
| Sage (SMTS) | APPROVED WITH CHANGES | 3 | Verify `service.py` contents; update `docs/llms.txt`; check `search/` dir |

**Total blockers across all reviewers: 7** (all are actionable, non-blocking for implementation but should be addressed before merging the PR)

## Next Steps

1. Address Byte's concern: verify `registry/repositories/file/` directory contents
2. Address Circuit's concern: add `uv lock` step to implementation plan
3. Address Sage's concern: verify `registry/search/service.py` and directory contents
4. Address Cipher's concern: note metrics schema change in release notes
5. Proceed with implementation per LLD step-by-step plan
6. Run full test suite post-implementation