# GitHub Issue: Remove FAISS and consolidate on the maintained DocumentDB/MongoDB hybrid search

## Title
Remove FAISS from the codebase, dependencies, configuration, and docs; consolidate search on the DocumentDB/MongoDB backend

## Labels
- refactor
- tech-debt
- dependencies
- search

## Description

### Problem Statement
The registry carries two parallel search implementations:

1. **FAISS** (`faiss-cpu`), used only by the default `file` storage backend. It lives in
   `registry/search/service.py` (the `FaissService` singleton, ~1200 lines) plus
   `registry/repositories/file/search_repository.py`.
2. **DocumentDB/MongoDB hybrid search** (`registry/repositories/documentdb/search_repository.py`),
   the maintained path used by all MongoDB-compatible backends. It does vector + keyword search
   with Reciprocal Rank Fusion, an HNSW vector index when the server supports it, and a pure-Python
   cosine-similarity fallback (`_client_side_search`) for MongoDB Community Edition that uses
   `registry/utils/vector.py:cosine_similarity` and the provider-agnostic embeddings client.

FAISS is obsolete here for several reasons:

- The write paths (`server_routes.py`, `agent_routes.py`, `agent_batch_item_processor.py`) import
  `faiss_service` **directly** and dual-write to it alongside the repository abstraction
  (`get_search_repository().index_*`). This bypasses the repository pattern the rest of the codebase
  follows and couples HTTP handlers to a concrete engine.
- `faiss-cpu` is a heavy native wheel that inflates the image and the dependency surface.
- The MongoDB client-side fallback already proves we can do semantic search with nothing but NumPy
  math and the embeddings client. Nothing FAISS does is unavailable in the maintained path.

### Proposed Solution
Make the MongoDB/DocumentDB backend the single search implementation and delete FAISS:

1. Replace the `file`-backend search repository (`FaissSearchRepository`) with a lightweight
   in-memory repository that reuses the existing embeddings client and `cosine_similarity` helper
   (no `faiss` import, no `.faiss` index files). This preserves a working `STORAGE_BACKEND=file`
   default so existing deployments do not break.
2. Delete `registry/search/service.py` (the `FaissService` singleton) and rewrite the write paths
   in `server_routes.py`, `agent_routes.py`, and `agent_batch_item_processor.py` to go through
   `get_search_repository()` only (remove every `from ..search.service import faiss_service`).
3. Remove `faiss-cpu` from `pyproject.toml` and regenerate `uv.lock`.
4. Remove FAISS-specific config (`faiss_index_path`, `faiss_metadata_path`), the `FaissMetadata`
   Pydantic model, and the `.faiss` file handling in `build_and_run.sh`, `cli/service_mgmt.sh`,
   and `terraform/aws-ecs/scripts/service_mgmt.sh`.
5. Update all docs and comments that reference FAISS to describe the unified search engine.
6. Replace the FAISS test mock and the FAISS service test suite with tests against the new file
   search repository; keep the existing MongoDB search tests unchanged.

### User Stories
- As a registry operator, I want a smaller image and a smaller dependency surface so deployments
  build faster and have fewer native-wheel compatibility issues.
- As a maintainer, I want one search implementation behind the repository abstraction so search
  behavior is consistent across storage backends and easier to reason about.
- As an API consumer, I want `/api/search` and tag search to return the same response shape they do
  today, regardless of storage backend, with no breaking change.

### Acceptance Criteria
- [ ] `grep -ri faiss` over the repository returns no hits in production code, dependencies, config,
      Terraform, Docker, CLI scripts, or docs (test fixtures that intentionally assert "no faiss" may
      remain).
- [ ] `faiss-cpu` is absent from `pyproject.toml` and `uv.lock`; `import faiss` appears nowhere.
- [ ] `STORAGE_BACKEND=file` still serves `/api/search`, tag search, and `/api/tags` with the same
      response schema as before.
- [ ] `STORAGE_BACKEND` set to any MongoDB-compatible value is unchanged (no behavior diff).
- [ ] Write paths (server register/update/delete/toggle, agent create/update/delete, agent batch)
      index and remove entities through `get_search_repository()` only; no module imports
      `registry.search.service`.
- [ ] `registry/search/service.py` and the `.faiss` / `service_index_metadata.json` artifacts and
      their config paths are removed.
- [ ] `uv run pytest tests/` passes with no regressions; the FAISS service test suite is replaced
      by tests for the new file search repository.
- [ ] No `.faiss` or `service_index*.json` files are created at runtime by any backend.

### Out of Scope
- Changing the embeddings provider, model, or dimensions (`EMBEDDINGS_*` settings unchanged).
- Changing the DocumentDB/MongoDB search algorithm, RRF tuning, or the `/api/search` request and
  response schemas.
- Adding a new vector database (e.g. Atlas Vector Search config changes) beyond what already exists.
- Migrating data: the `file` backend re-indexes from source on startup, so there is no FAISS index
  to migrate.
- Removing the `metrics-service` historical `faiss_search_time_ms` column (a stored time-series
  schema field); this is handled as an optional, separately-gated cleanup in the LLD, not a blocker.

### Dependencies
- None external. This is an internal refactor that removes one runtime dependency (`faiss-cpu`).

### Related Issues
- Storage backend allowlist / repository abstraction (referenced in `registry/core/config.py`,
  issue #955) - this change extends the same repository-pattern consolidation to search.
