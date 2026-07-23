# GitHub Issue: Remove FAISS and consolidate on DocumentDB hybrid search

## Title
Remove FAISS from the codebase and documentation; DocumentDB hybrid search becomes the sole search backend

## Labels
- enhancement
- refactor
- infra
- docs

## Description

### Problem Statement
The registry currently ships two parallel implementations of semantic/hybrid search:

1. `FaissService` (`registry/search/service.py`) plus `FaissSearchRepository` (`registry/repositories/file/search_repository.py`), used only when `STORAGE_BACKEND=file` (the current default).
2. `DocumentDBSearchRepository` (`registry/repositories/documentdb/search_repository.py`), used for `documentdb`, `mongodb`, `mongodb-ce`, and `mongodb-atlas` storage backends, and already the recommended/production path per `docs/database-design.md` and `docs/configuration.md`.

FAISS is an in-memory, single-process vector index with no native support for true vector removal, no persistence guarantees beyond a local index file, and no hybrid (vector + keyword) fusion of its own — the registry's own `FaissService._calculate_keyword_boost` reimplements a cruder version of ranking logic that DocumentDB's Reciprocal Rank Fusion (`_reciprocal_rank_fusion`) already does more robustly. Maintaining both paths means:

- `faiss-cpu` is a hard dependency (`pyproject.toml`) even for deployments that only ever use DocumentDB/MongoDB.
- `registry/api/server_routes.py` and `registry/api/agent_routes.py` contain roughly a dozen call sites that read or write to the `faiss_service` singleton directly, in addition to (and sometimes instead of) the abstract `SearchRepositoryBase` used for DocumentDB. Several of these call sites (e.g. the `/register` form endpoint, `internal_register_service`, `refresh_service`, `remove_service_api`) call **only** `faiss_service` with no DocumentDB-repository equivalent, meaning the two code paths have already drifted and are not functionally equivalent today.
- Docker images, `build_and_run.sh`, and operational scripts (`cli/service_mgmt.sh`, `terraform/aws-ecs/scripts/service_mgmt.sh`) carry FAISS-specific file-existence checks and verification steps (`service_index.faiss`, `service_index_metadata.json`) that only apply to the file/FAISS backend.
- Twenty-plus documentation files describe FAISS as if it is still a supported or primary backend, which is confusing for new operators who are steered toward DocumentDB/MongoDB everywhere else in the docs.

FAISS is obsolete in this repo: DocumentDB hybrid search is the maintained, documented, production-recommended alternative and already implements everything FAISS provides (vector search, keyword boosting, result fusion) plus capabilities FAISS lacks (true removal, persistent embeddings, admin re-index tooling via `find_missing_embeddings`/`reindex_paths`).

### Proposed Solution
Remove FAISS entirely:

- Delete `FaissService`, `FaissSearchRepository`, the `faiss-cpu` dependency, and all FAISS-specific config/schema surface (`Settings.faiss_index_path`, `Settings.faiss_metadata_path`, `FaissMetadata`).
- Simplify `registry/repositories/factory.py::get_search_repository()` to unconditionally return `DocumentDBSearchRepository`, following the precedent already set by `get_skill_repository()` and `get_virtual_server_repository()`, which have no file-backend counterpart.
- Replace the ~14 direct `faiss_service` call sites in `server_routes.py`/`agent_routes.py`/`cli/agent_mgmt.py` with calls to `get_search_repository()` (the abstraction that already exists and is already used in about half of these call sites today), so that indexing/removal behavior is preserved on every registration, update, toggle, and delete path regardless of `STORAGE_BACKEND`.
- Change the default `storage_backend` (currently `"file"`) so that a fresh install with no explicit `STORAGE_BACKEND` env var gets DocumentDB/MongoDB-backed search out of the box, and document the required MongoDB/DocumentDB connection settings clearly wherever the default is described.
- Remove FAISS-specific build/ops steps (`build_and_run.sh`'s FAISS-file cleanup and verification blocks, `verify_faiss_metadata()` in both `cli/service_mgmt.sh` and `terraform/aws-ecs/scripts/service_mgmt.sh`) and replace them with DocumentDB-appropriate equivalents (e.g. querying the search collection or hitting `/api/search/semantic` for verification).
- Delete or rewrite the FAISS-specific test suite (`tests/unit/search/test_faiss_service.py`, `tests/fixtures/mocks/mock_faiss.py`, the FAISS auto-mock in `tests/conftest.py`) and update every test file that patches `registry.search.service.faiss_service` to instead patch the `SearchRepositoryBase` abstraction via `get_search_repository()`.
- Update all documentation (`docs/embeddings.md`, `registry/embeddings/README.md`, `docs/database-design.md`, `docs/design/database-abstraction-layer.md`, `docs/design/storage-architecture-mongodb-documentdb.md`, `docs/configuration.md`, `docs/api-reference.md`, `docs/service-management.md`, `docs/server-versioning-operations.md`, `docs/registry-auth-detailed.md`, `docs/testing/*.md`, `docs/prebuilt-images.md`, `terraform/aws-ecs/OPERATIONS.md`, `build-config.yaml`) to remove FAISS references. `docs/design/hybrid-search-architecture.md` already documents the target DocumentDB-only architecture with zero FAISS mentions and needs no changes — it is the reference doc other docs should be aligned with.

### User Stories
- As an operator, I want to deploy the registry without installing or troubleshooting a native FAISS dependency, so that my Docker builds are smaller and simpler.
- As a developer, I want a single search implementation to read, test, and extend, so that I don't have to reason about two divergent code paths with different feature sets.
- As an existing user of the file storage backend, I want my search behavior (registering, searching, removing servers/agents) to keep working exactly as before, even though the underlying implementation changes to DocumentDB/MongoDB.

### Acceptance Criteria
- [ ] No `import faiss` remains anywhere in `registry/`, `cli/`, `scripts/`, `tests/`, or `servers/`.
- [ ] `faiss-cpu` is removed from `pyproject.toml` and `uv.lock` is regenerated; no other dependency is removed unless it is proven unused elsewhere (numpy, scikit-learn, torch, sentence-transformers all remain, since they are shared by non-FAISS code paths).
- [ ] `registry/repositories/factory.py::get_search_repository()` always returns `DocumentDBSearchRepository`; the `FaissSearchRepository` import and file no longer exist.
- [ ] Every route that previously indexed/removed entities via `faiss_service` now does so via `get_search_repository()`, with no regression in which paths get indexed (verified against the call-site inventory in the LLD).
- [ ] `registry/main.py`'s FAISS-specific "rebuild in-memory index on every boot" branch is removed; startup always follows the DocumentDB-persistent path.
- [ ] `Settings.faiss_index_path`, `Settings.faiss_metadata_path`, and `FaissMetadata` are deleted.
- [ ] `build_and_run.sh`, `cli/service_mgmt.sh`, and `terraform/aws-ecs/scripts/service_mgmt.sh` no longer reference `service_index.faiss` / `service_index_metadata.json`; their verification steps are replaced with DocumentDB-appropriate checks.
- [ ] All FAISS-specific tests are deleted or rewritten against `DocumentDBSearchRepository`/the `SearchRepositoryBase` abstraction; the full test suite passes with no FAISS-mocking infrastructure left in `tests/conftest.py`.
- [ ] All FAISS references are removed or rewritten in the documentation files enumerated in the LLD; `docs/design/hybrid-search-architecture.md` is left untouched as the reference architecture doc.
- [ ] Existing search behavior (functional semantics of `/api/search/semantic`, `search_by_tags`, `get_all_tags`) is unchanged from a client's perspective; the acceptance test plan in `testing.md` passes.
- [ ] `search_backend` telemetry field and `faiss_search_time_ms` metrics field are resolved with an explicit backward-compatibility decision (documented in the LLD), not silently dropped.

### Out of Scope
- Changing the DocumentDB/MongoDB hybrid search ranking algorithm itself (RRF weighting, soft-cap distribution, score normalization) — this issue only removes FAISS and consolidates on the existing DocumentDB implementation as-is.
- Migrating existing file-backend deployments' server/agent JSON data into MongoDB — `scripts/migrate-file-to-mongodb.py` already exists and is unaffected by this change.
- Changing the mcpgw MCP server's `intelligent_tool_finder` tool behavior — it is already deprecated (removal planned for v1.26.0) and already delegates to the registry's `/api/search/semantic` HTTP endpoint with no direct FAISS dependency of its own; only its stale documentation in `docs/dynamic-tool-discovery.md` needs updating.
- Any change to the embeddings generation layer (`registry/embeddings/client.py`, `EmbeddingsClient`, `SentenceTransformersClient`, `LiteLLMClient`) — this layer is already backend-agnostic and used identically by both FAISS and DocumentDB paths today.

### Dependencies
- Requires a running MongoDB/DocumentDB instance for any environment that previously relied on the default `file` storage backend for search, once the default changes. Docker Compose already provides a bundled `mongodb` service and `mongodb-init` job (`docker-compose.yml` lines 17-67) that can be reused/defaulted-to.

### Related Issues
- Upstream reference: https://github.com/agentic-community/mcp-gateway-registry/issues/1285
