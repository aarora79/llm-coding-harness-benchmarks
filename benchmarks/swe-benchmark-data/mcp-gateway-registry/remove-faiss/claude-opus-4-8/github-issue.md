# GitHub Issue: Remove FAISS from the codebase and documentation

## Title
Remove FAISS and rely solely on DocumentDB hybrid search

## Labels
- refactor
- infra
- docs
- tech-debt

## Description

### Problem Statement
FAISS (`faiss-cpu`) is an obsolete dependency in this repository. It is the search engine only for the legacy `file` storage backend, implemented in `registry/search/service.py` (the sole `import faiss` in the codebase) and wired in via `registry/repositories/file/search_repository.py` (`FaissSearchRepository`).

The project has already moved on: `.env.example` ships `STORAGE_BACKEND=mongodb-ce`, the Terraform default is `documentdb`, `docker-compose.yml` bundles a `mongodb` service with an init job, and the design docs describe the file/FAISS backend as "Legacy" and "deprecated". All production search now runs through `DocumentDBSearchRepository`, a maintained hybrid-search implementation (HNSW vector search + keyword search fused with Reciprocal Rank Fusion) that persists embeddings in the database.

Keeping FAISS costs us:
- A heavy native dependency chain (`faiss-cpu`, and the `torch` / `sentence-transformers` stack used to bake a local embeddings model) that complicates image builds and inflates image size (the registry image is ~4.6 GB).
- A build-time model bake step (`docker/Dockerfile.registry-cpu` downloads `all-MiniLM-L6-v2`).
- An entire parallel search code path plus its test doubles (`tests/fixtures/mocks/mock_faiss.py`, `sys.modules["faiss"]` injection in `tests/conftest.py`, `tests/unit/search/test_faiss_service.py`).
- Documentation that describes FAISS as the current search mechanism, which is now misleading.

### Proposed Solution
Remove FAISS entirely and make DocumentDB hybrid search the single search path.

1. Delete the FAISS service (`registry/search/service.py`) and the FAISS-backed search repository (`registry/repositories/file/search_repository.py`).
2. In `registry/repositories/factory.py`, stop importing `FaissSearchRepository`. Semantic search now requires a MongoDB-compatible backend; selecting the `file` backend must fail fast with an actionable error rather than silently returning no results.
3. Change the code default `storage_backend` from `file` to `mongodb-ce` (aligning `registry/core/config.py` and the compose env fallback with the already-shipped `.env.example` and Terraform defaults) so a default `docker-compose up` continues to have working search.
4. Remove the FAISS dependency from `pyproject.toml`, regenerate `uv.lock`, and drop the build-time embeddings-model bake and any FAISS-only Docker steps. Remove `torch` / `sentence-transformers` / `scikit-learn` only if nothing else uses them (see LLD).
5. Simplify `registry/main.py` startup by deleting the FAISS-only in-memory re-index block (DocumentDB persists embeddings across restarts).
6. Delete or repoint every FAISS test (delete FAISS-only tests and the `faiss` `sys.modules` mock; repoint `faiss_service` patches to the search repository).
7. Rewrite documentation that describes FAISS as the current mechanism to describe DocumentDB hybrid search; strip incidental FAISS mentions from comments and image descriptions; leave historical release notes untouched.

The shared, backend-neutral embeddings abstraction under `registry/embeddings/` is retained unchanged, because DocumentDB hybrid search depends on it.

### User Stories
- As an operator, I want to deploy the registry without FAISS native libraries or a build-time model download, so that images are smaller and builds are simpler and more reliable.
- As a developer, I want a single search code path (DocumentDB hybrid search), so that the codebase is easier to understand, test, and maintain.
- As an operator upgrading, I want a clear startup error if I am still on `STORAGE_BACKEND=file`, so that I know to switch to a MongoDB-compatible backend instead of silently losing search.

### Acceptance Criteria
- [ ] `grep -rn "import faiss"` and `grep -rn "faiss_service\|search.service import"` return no matches under `registry/` (the second gate catches the ~22 direct call sites that a bare `import faiss` grep would miss); `grep -ri "faiss"` returns no matches in `pyproject.toml`, `uv.lock`, `docker/`, and `terraform/` (excluding historical release notes, the persisted metric column name if that migration is deferred, and the telemetry collector schema which keeps accepting `faiss` during rollout).
- [ ] `registry/search/service.py` and `registry/repositories/file/search_repository.py` are deleted; all direct `faiss_service.*` call sites in `server_routes.py`, `agent_routes.py`, and `agent_batch_item_processor.py` are migrated onto `SearchRepositoryBase`.
- [ ] `faiss-cpu` is removed from `pyproject.toml` and `uv.lock`; `uv sync` succeeds and `uv run python -c "import faiss"` fails with `ModuleNotFoundError`.
- [ ] The default `docker-compose up` (which ships `mongodb`, once the six env lines are flipped to `mongodb-ce`) starts the registry with working semantic search via DocumentDB hybrid search.
- [ ] `POST /api/search/semantic` and `GET /api/search/tags` keep identical request/response schemas (and `search_mode: "hybrid"`) on a MongoDB-compatible backend. Result ranking/scores may differ from FAISS (RRF vs multiplicative boost); a search-quality eval (`scripts/evaluate_search.py`) shows no material regression, rather than byte-identical output.
- [ ] Server/agent registration, toggle, and deletion still update the DocumentDB search index (write-path coverage, not just read).
- [ ] Selecting `STORAGE_BACKEND=file` boots successfully, logs a prominent warning that semantic search is disabled, and returns empty search results (file storage CRUD still works).
- [ ] The telemetry `search_backend` field no longer emits `faiss`; the Lambda collector schema continues to accept `faiss` during the rollout window (narrowed only after the fleet is upgraded).
- [ ] All FAISS-specific tests are deleted; all remaining tests pass (`uv run pytest tests/`) with no regressions.
- [ ] Documentation describing FAISS as the current search mechanism is rewritten to describe DocumentDB hybrid search; `mkdocs build` succeeds with no broken links.

### Out of Scope
- Removing the `file` storage backend's non-search repositories (`FileServerRepository`, `FileAgentRepository`, etc.). File-based *storage* remains; only file-based *search* (FAISS) is removed.
- Changing the DocumentDB hybrid search algorithm, HNSW parameters, or fusion method.
- Removing the `registry/embeddings/` abstraction (it is backend-neutral and required by DocumentDB search).
- Renaming the persisted metric column `faiss_search_time_ms` in the metrics-service database schema (a separate, backward-incompatible DB migration; the LLD proposes deprecating rather than renaming it).

### Dependencies
- Requires a running MongoDB-compatible backend (already shipped in `docker-compose.yml` as the `mongodb` service; provisioned by Terraform for `documentdb`).

### Related Issues
- #1285 (reference issue for this task)
