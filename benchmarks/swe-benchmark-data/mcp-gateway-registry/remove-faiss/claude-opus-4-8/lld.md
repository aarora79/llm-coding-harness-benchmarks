# Low-Level Design: Remove FAISS from the codebase and documentation

*Created: 2026-07-23*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)
15. [Open Questions](#open-questions)

## Overview

### Problem Statement
FAISS (`faiss-cpu`) is an obsolete dependency. It is the search engine only for the legacy `file` storage backend: `registry/search/service.py` holds the single `import faiss` in the whole codebase (class `FaissService`, module singleton `faiss_service`), and `registry/repositories/file/search_repository.py` (`FaissSearchRepository`) adapts it to the `SearchRepositoryBase` interface. Every other consumer of search (`registry/api/search_routes.py`, `registry/main.py`, telemetry) is already backend-agnostic and talks only to `SearchRepositoryBase`.

The maintained replacement, `DocumentDBSearchRepository` (`registry/repositories/documentdb/search_repository.py`), is a full hybrid-search engine (HNSW vector search via `$search.vectorSearch` + keyword search, fused with Reciprocal Rank Fusion) that persists embeddings in the database. It is already the production default: `.env.example` ships `STORAGE_BACKEND=mongodb-ce`, Terraform defaults to `documentdb`, and `docker-compose.yml` bundles a `mongodb` service.

> **Important correction (post-review):** the search *read* path is backend-agnostic (the route talks only to `SearchRepositoryBase`), but the search *write/index* path is NOT. `faiss_service` is imported and called directly from ~22 production sites outside the repository abstraction: `registry/api/server_routes.py` (register, toggle, update, refresh, delete, `save_data`), `registry/api/agent_routes.py` (register/update/delete), and `registry/services/agent_batch_item_processor.py` (batch add/remove). Some of these dual-write (FAISS + `search_repo.index_server`, e.g. `server_routes.py:847` then `:853-854`); others are **FAISS-only** with no DocumentDB counterpart (e.g. `server_routes.py:1343`, `:1643`, `:2630`, `:2760`; `agent_routes.py:631`, `:1152`, `:1601`, `:1855`; batch `228`, `340`). These must be migrated onto `SearchRepositoryBase` **before** `service.py` is deleted, or those endpoints raise `ImportError` at request time. This drives the ordered-commit plan and the `NullSearchRepository` decision below.

The task is to delete all FAISS code, dependencies, configuration, Docker build steps, tests, and documentation references, and make DocumentDB hybrid search the single search path, without breaking existing search behaviour.

### Goals
- Migrate all direct `faiss_service.*` production call sites (server/agent/batch indexing) onto the `SearchRepositoryBase` interface, then delete the sole `import faiss` and all FAISS code (`registry/search/service.py`, `registry/repositories/file/search_repository.py`).
- Remove `faiss-cpu` from `pyproject.toml` and `uv.lock`.
- Remove FAISS-only Docker build steps; keep the build-time embeddings-model bake on the CPU image (it also serves DocumentDB search and protects air-gapped/cold-start).
- Give the `file` backend a `NullSearchRepository` (no-op index, empty results, startup WARNING) so file *storage* stays bootable while file *search* is gone; align the code default and the six compose env lines to `mongodb-ce` so a default `docker-compose up` keeps working search.
- Delete FAISS-only tests and the `faiss` `sys.modules` mock; repoint remaining tests onto the search repository.
- Rewrite docs that describe FAISS as the current mechanism to describe DocumentDB hybrid search; strip incidental mentions; keep historical release notes.

### Non-Goals
- Removing the `file` storage backend's non-search repositories (server/agent/scope/etc.). File-based *storage* stays; only file-based *search* (FAISS) is removed.
- Changing the DocumentDB hybrid search algorithm, HNSW parameters, or fusion method.
- Removing `sentence-transformers` / `torch`: they back the default embeddings provider that DocumentDB search also uses, so they are retained.
- Renaming the persisted metric column `faiss_search_time_ms` in the metrics-service DB (separate, backward-incompatible migration; here we only stop populating/deprecate it).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/search/service.py` | `FaissService` + `faiss_service` singleton; sole `import faiss` (lines 8-9) | DELETE entirely |
| `registry/repositories/file/search_repository.py` | `FaissSearchRepository`, thin adapter over `faiss_service` | DELETE entirely |
| `registry/repositories/factory.py` | `get_search_repository()` picks FAISS vs DocumentDB by `storage_backend` (lines 132-151) | Remove FAISS branch; make `file` fail fast for search |
| `registry/repositories/interfaces.py` | `SearchRepositoryBase` (lines 1001-1123); docstring says "using FAISS or DocumentDB" | Backend-agnostic; only fix the docstring |
| `registry/api/search_routes.py` | `POST /semantic`, `GET /tags`; depend on `get_search_repo()` | Backend-agnostic; fix FAISS comments and the dead `RuntimeError`->503 branch |
| `registry/main.py` | Startup: `get_search_repository()`, FAISS-only in-memory re-index block (lines 496-549) | Delete the FAISS re-index branch; keep agent-state load |
| `registry/core/config.py` | `storage_backend` default `file` (742-752); `faiss_index_path`/`faiss_metadata_path` (996-1001); embeddings + DocumentDB settings | Change default; delete FAISS path properties |
| `registry/core/schemas.py` | `FaissMetadata` model (505-510), unused by service | DELETE the model |
| `registry/core/telemetry.py` | `search_backend = "documentdb" if ... else "faiss"` (730-731) | Emit `"documentdb"` unconditionally |
| `registry/metrics/client.py` | `faiss_search_time_ms` metric field (111, 126) | Rename to generic `search_time_ms` at the app layer; deprecate old field |
| `registry/embeddings/` | Backend-neutral embeddings client (`SentenceTransformersClient`, `LiteLLMClient`) | KEEP unchanged; only reword README FAISS prose |
| `registry/repositories/documentdb/search_repository.py` | The hybrid-search replacement | No change; this becomes the only search path |
| `pyproject.toml` | `faiss-cpu>=1.7.4` (line 23) | Remove line; `scikit-learn` (line 26) is a removal candidate |
| `uv.lock` | `faiss-cpu` pinned (692-709, 1608, 1710) | Regenerate with `uv lock` |
| `docker/Dockerfile.registry-cpu` | Bakes `all-MiniLM-L6-v2` at build (71-75) | Remove or gate the bake (see Open Questions) |
| `terraform/telemetry-collector/lambda/collector/schemas.py` | `search_backend` pattern `^(faiss|documentdb)$` (265-269) | Drop `faiss` from the pattern (accept legacy on read if needed) |
| `terraform/aws-ecs/scripts/service_mgmt.sh`, `cli/service_mgmt.sh` | `verify_faiss_metadata()` greps `service_index_metadata.json` | Delete the function and its call sites |

### Existing Patterns Identified
1. **Repository factory pattern**: `registry/repositories/factory.py` has one `get_<x>_repository()` per domain, each a cached singleton selecting DocumentDB vs file by `settings.storage_backend in MONGODB_BACKENDS`. A future implementer must follow this exact shape when editing `get_search_repository()`.
2. **Backend-agnostic route DI**: `search_routes.py` injects `search_repo: SearchRepositoryBase = Depends(get_search_repo)` and never touches FAISS. Tests exploit this by passing an `AsyncMock` repo as the 4th arg. This is the mocking convention to standardize on.
3. **Fail-fast config validation**: `config.py::_validate_storage_backend` raises `ValueError` at startup with the full allowlist for unknown values. The new "file has no search" error should follow the same fail-fast, actionable-message style.
4. **Embeddings abstraction**: both backends call `create_embeddings_client(...)` from `registry/embeddings`. Removing FAISS must not touch this shared client.
5. **Startup persistence asymmetry**: `main.py` re-indexes into FAISS on every boot because FAISS is in-memory; DocumentDB skips re-index because embeddings persist. Removing FAISS deletes the entire re-index branch.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/api/server_routes.py` (13 import sites, ~12 calls) | Migrates | Replace direct `faiss_service.add_or_update_service`/`remove_service`/`save_data` with `get_search_repository().index_server`/`remove_entity`; drop `save_data` (DocumentDB persists inline) |
| `registry/api/agent_routes.py` (4 sites) | Migrates | Replace `faiss_service.add_or_update_entity`/`remove_entity` with `search_repo.index_agent`/`remove_entity` |
| `registry/services/agent_batch_item_processor.py` (2 sites) | Migrates | Same as agent_routes; batch add/remove |
| `get_search_repository()` factory | Modifies | Remove `FaissSearchRepository` import; return `NullSearchRepository` for `file` (no hard raise) |
| `registry/main.py` startup | Simplifies | Delete the `if settings.storage_backend not in MONGODB_BACKENDS:` re-index block; retain the agent-state load that lived in the `else` branch |
| `registry/api/search_routes.py` | Cleans up | Remove FAISS docstring/comment; the `except RuntimeError -> 503` branch becomes dead (DocumentDB never raises that FAISS-specific error) but keep a generic 503 guard |
| `registry/core/config.py` | Modifies | Change `storage_backend` default; delete `faiss_index_path`/`faiss_metadata_path` |
| Telemetry + metrics | Modifies | Stop emitting `"faiss"`; deprecate `faiss_search_time_ms` |
| Docker / compose / Terraform / CLI | Cleans up | Remove FAISS build/verify steps, index volume expectations, and comments |

### Constraints and Limitations Discovered
- **The search write path bypasses the repository abstraction.** ~22 production sites import `faiss_service` directly (see the correction in Overview). `get_search_repository()` is also constructed eagerly inside service constructors built at startup (`server_service.py`, `agent_service.py`, `semantic_search_service.py`, `skill_service.py`), so a factory that *raises* for `file` would brick the entire `file` backend at boot, not just search. This is why the design uses a `NullSearchRepository` rather than a hard raise.
- **The `file` backend has no non-FAISS search engine.** DocumentDB hybrid search requires a live MongoDB connection, so there is no in-process drop-in for FAISS. Removing FAISS necessarily means the `file` backend loses semantic search; `NullSearchRepository` returns empty results and logs a warning so file storage stays usable for CRUD.
- **DocumentDB search is not behaviourally identical to FAISS.** FAISS applies a multiplicative keyword boost to cosine similarity; DocumentDB fuses independent vector and keyword rankings with RRF (k=60) plus soft caps and a display floor. Request/response *schemas* are identical, but result ordering and scores differ. The acceptance criterion is schema-compatibility, not byte-identical ranking.
- **`sentence-transformers` and `torch` must stay.** `scripts/evaluate_search.py:134` and `registry/embeddings/client.py:89` import `sentence_transformers`; it is the default `EMBEDDINGS_PROVIDER` used by DocumentDB search. Only `faiss-cpu` is unambiguously FAISS-only.
- **`scikit-learn` has no imports anywhere** (only in `pyproject.toml`/`uv.lock`); it is a removal candidate but must be verified before deletion (see Open Questions).
- **`FaissSearchRepository.rebuild_index()`** calls a nonexistent `faiss_service.rebuild_index()` (dead/broken) and is never invoked; deleting the file removes this latent bug.
- **The metric column `faiss_search_time_ms`** is persisted in the metrics DB schema; renaming it is a separate migration, so this change deprecates it rather than renaming the column.

## Architecture

### System Context Diagram (after removal)
```
                         POST /api/search/semantic
                         GET  /api/search/tags
                                  |
                                  v
                    registry/api/search_routes.py
                                  |
                    Depends(get_search_repo)
                                  |
                                  v
        registry/repositories/factory.py :: get_search_repository()
                                  |
             storage_backend in MONGODB_BACKENDS ?
                     |  yes                    |  no ("file")
                     v                         v
     DocumentDBSearchRepository        NullSearchRepository
     (HNSW vector + keyword,           (no-op index, empty results,
      RRF fusion, embeddings            startup WARNING: "file backend
      persisted in DB)                  has no semantic search")
                     |
                     v
          registry/embeddings (SentenceTransformers / LiteLLM)  [UNCHANGED]

     [DELETED]  registry/search/service.py  (FaissService, import faiss)
     [DELETED]  registry/repositories/file/search_repository.py
```

### Sequence Diagram: startup after removal
```
main.py lifespan
  -> server_service.load_servers_and_state()
  -> search_repo = get_search_repository()      # DocumentDBSearchRepository (or fail-fast for "file")
  -> await search_repo.initialize()             # ensures HNSW vector index exists
  -> (no FAISS re-index; embeddings persist in DB)
  -> await agent_service.load_agents_and_state()
  -> health_service.initialize()
```

### Sequence Diagram: search request (unchanged externally)
```
client -> POST /api/search/semantic {query, entity_types, max_results, ...}
  route -> search_repo.search(query, entity_types, max_results, include_*)
    DocumentDBSearchRepository.search:
      embed query -> $search.vectorSearch (HNSW) + keyword find -> RRF fuse -> normalize
      (fallbacks: _client_side_search for MongoDB CE, _lexical_only_search if embeddings unavailable)
  route <- {servers, tools, agents, skills, virtual_servers, search_mode: "hybrid"}
```

### Component Diagram
```
[search_routes] --Depends--> [get_search_repo] --> [get_search_repository (factory)]
                                                        |
                                                        +--> [DocumentDBSearchRepository] --> [embeddings client] --> [MongoDB/DocumentDB]
                                                        +--> (file) --> [NullSearchRepository] (empty results + startup WARNING)
```

## Data Models

### New Models
This change introduces no new models. It **removes** one:

```python
# registry/core/schemas.py, lines 505-510 -- DELETE
class FaissMetadata(BaseModel):
    """Metadata for FAISS index entries."""
    id: int
    text_for_embedding: str
    full_server_info: ServerInfo
```
Confirm `FaissMetadata` is unreferenced (`grep -rn FaissMetadata registry/ tests/`) before deletion; the FAISS service builds metadata dicts inline and does not use this model.

### Model Changes
The public request/response models in `search_routes.py` (`SemanticSearchRequest`, `SemanticSearchResponse`, `ServerSearchResult`, `ToolSearchResult`, `AgentSearchResult`, `SkillSearchResult`, `VirtualServerSearchResult`) are **unchanged**. This preserves the external contract.

## API / CLI Design

### Endpoints (unchanged contract)
No endpoints are added, removed, or changed in shape. `POST /api/search/semantic` and `GET /api/search/tags` keep identical request/response schemas and the default `search_mode: "hybrid"`. On a MongoDB-compatible backend, result *ranking and scores* may differ from FAISS (RRF vs multiplicative boost); the schema is unchanged.

**Behavioural change (file backend):** starting the registry with `STORAGE_BACKEND=file` no longer serves semantic search. Instead of FAISS, the factory returns a `NullSearchRepository` that logs a prominent one-time WARNING at startup and returns empty results (`{servers:[], tools:[], agents:[], skills:[], virtual_servers:[]}`) and an empty tag list. This keeps file *storage* fully functional (register/toggle/delete servers and agents still work) while making the loss of search explicit and non-crashing. The startup log reads:

```
WARNING: STORAGE_BACKEND='file' has no semantic search after FAISS removal.
Semantic search will return empty results. Set STORAGE_BACKEND to one of
mongodb-ce, mongodb, mongodb-atlas, documentdb for hybrid search.
```

### CLI (doc-only changes)
`cli/agent_mgmt.py:34` help text ("using FAISS vector index") and the `verify_faiss_metadata()` shell functions in `cli/service_mgmt.sh` (166-187, 613, 705) and `terraform/aws-ecs/scripts/service_mgmt.sh` (184-206, 631, 723) are removed. The `search` subcommand behaviour is unchanged (it calls the HTTP API).

## Configuration Parameters

### Changed Defaults

| Variable | Old Default | New Default | Notes |
|----------|-------------|-------------|-------|
| `STORAGE_BACKEND` (`settings.storage_backend`, config.py 742-752) | `file` | `mongodb-ce` | Aligns code with `.env.example` (`mongodb-ce`) and Terraform (`documentdb`). Ensures default `docker-compose up` has working search. |

`ALLOWED_STORAGE_BACKENDS` keeps `"file"` (file-based *storage* is still valid), but the search factory rejects it. Alternatively, drop `"file"` from `ALLOWED_STORAGE_BACKENDS` entirely if file storage is also being retired -- out of scope here, so we keep it and fail only on search.

### Removed Config

| Item | File | Lines | Notes |
|------|------|-------|-------|
| `faiss_index_path` property | `registry/core/config.py` | 996-997 | `servers_dir / "service_index.faiss"` |
| `faiss_metadata_path` property | `registry/core/config.py` | 999-1001 | `servers_dir / "service_index_metadata.json"` |

### Retained Config (shared by DocumentDB search; do NOT remove)
`EMBEDDINGS_PROVIDER`, `EMBEDDINGS_MODEL_NAME`, `EMBEDDINGS_MODEL_DIMENSIONS`, `EMBEDDINGS_API_KEY`, `EMBEDDINGS_API_BASE`, `EMBEDDINGS_AWS_REGION`, `VECTOR_SEARCH_EF_SEARCH`, `SEARCH_FUSION_METHOD`, and all `DOCUMENTDB_*` / `MONGODB_CONNECTION_STRING` / `DOCUMENTDB_NAMESPACE` settings.

### Deployment Surface Checklist
The **compose env lines are load-bearing, not the code default**: an explicit `STORAGE_BACKEND=${...:-file}` overrides `config.py`. All six must flip together, or a default stack boots `mongodb` but injects `file` and search silently no-ops. Add a CI grep asserting no compose file injects `:-file`.
- [ ] `registry/core/config.py` -- default `mongodb-ce`
- [ ] `docker-compose.yml` -- `STORAGE_BACKEND=${STORAGE_BACKEND:-file}` (lines 198, 454) -> `:-mongodb-ce`; comment line 71; note `service_index.faiss` under `servers` mount (316-318, 583-584) is just a comment cleanup (no dedicated FAISS volume exists)
- [ ] `docker-compose.prebuilt.yml` (lines 105, 319), `docker-compose.podman.yml` (lines 95, 318) -- flip env lines; comments (14/4), mounts
- [ ] `.env.example` -- already `mongodb-ce`; strip any FAISS narrative
- [ ] `terraform/aws-ecs/variables.tf` -- already defaults `documentdb`; keep allowlist in sync with code
- [ ] `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` -- comment line 587
- [ ] `terraform/telemetry-collector/lambda/collector/schemas.py` -- pattern 265-269
- [ ] `build-config.yaml` -- description/comment (25, 30)
- [ ] `docker/Dockerfile.registry-cpu` -- model bake (71-75)
- [ ] `charts/` (Helm) -- verify no FAISS default or index volume (grep before edit)

## New Dependencies
This change adds no new dependencies. It **removes** `faiss-cpu` (and, pending verification, `scikit-learn`). See [File Changes](#file-changes) for the dependency table.

## Implementation Details

### Ordered Commits (to avoid a broken intermediate state)
1. **Commit A (behaviour-preserving):** Step 0 - migrate all direct `faiss_service.*` call sites onto `get_search_repository()`. Works on both backends; no deletion yet.
2. **Commit B:** Steps 1-6 - add `NullSearchRepository`, change the factory, delete FAISS code, simplify startup, config/schema/telemetry/route cleanup.
3. **Commit C:** Step 7 - remove `faiss-cpu`, regenerate `uv.lock`, Docker/infra cleanup.
4. **Commit D:** Steps 8-10 - tests, docs, scripts, regenerate `openapi.json`.

### Step-by-Step Plan (for a future implementer)

#### Step 0: Migrate direct `faiss_service` call sites onto the repository (do this FIRST)
The search *write* path bypasses `SearchRepositoryBase`. Before anything is deleted, replace every direct `faiss_service` call with the repository equivalent so behaviour is preserved on both backends.

**Files and sites:**
- `registry/api/server_routes.py`: imports at 774, 1113, 1413, 1716, 1769, 1876, 2100, 2502, 2675, 3504, 3989, 4100; calls to `add_or_update_service` (847, 1343, 1643, 1752, 1949, 2386, 2630, 2760, 4041), `remove_service` (1827, 4178), `save_data` (3808).
- `registry/api/agent_routes.py`: `add_or_update_entity` (631, 1152, 1601), `remove_entity` (1855).
- `registry/services/agent_batch_item_processor.py`: `add_or_update_entity` (228), `remove_entity` (340).

**Mapping (method signatures differ; this is not a rename):**

| Old (`faiss_service`) | New (`search_repo = get_search_repository()`) |
|-----------------------|-----------------------------------------------|
| `add_or_update_service(path, info, enabled)` | `index_server(path, info, enabled)` |
| `add_or_update_entity(path, data, "a2a_agent", enabled)` | `index_agent(path, data, enabled)` |
| `remove_service(path)` / `remove_entity(path)` | `remove_entity(path)` |
| `save_data()` | delete the call (DocumentDB persists inline; `NullSearchRepository.save_data` is a no-op) |

- **Dual-write sites** (e.g. `server_routes.py:847` is immediately followed by `search_repo.index_server` at 853-854): delete the `faiss_service` line; the paired `search_repo` call already exists.
- **FAISS-only sites** (e.g. `server_routes.py:1343`, `1643`, `2630`, `2760`; `agent_routes.py:631`, `1152`, `1601`, `1855`; batch 228, 340): replace the `faiss_service` call with the mapped `search_repo` call so DocumentDB indexing is preserved. Fixing these also closes a pre-existing bug where these mutations never updated the DocumentDB index on mongodb-ce.

After Step 0, `grep -rn "faiss_service" registry/` should match only `search/service.py`, `repositories/file/search_repository.py`, and the factory - all removed in Step 2.

#### Step 1: Add `NullSearchRepository` and update the factory
**New file:** `registry/repositories/null_search_repository.py`

```python
"""No-op search repository for backends without semantic search (e.g. file)."""

import logging
from typing import Any

from .interfaces import SearchRepositoryBase

logger = logging.getLogger(__name__)

_EMPTY: dict[str, list[dict[str, Any]]] = {
    "servers": [], "tools": [], "agents": [], "skills": [], "virtual_servers": [],
}


class NullSearchRepository(SearchRepositoryBase):
    """Returns empty search results. Used when STORAGE_BACKEND has no search engine.

    File storage remains fully functional for CRUD; only semantic search is disabled.
    """

    async def initialize(self) -> None:
        logger.warning(
            "STORAGE_BACKEND has no semantic search after FAISS removal; "
            "search will return empty results. Set STORAGE_BACKEND to one of "
            "mongodb-ce, mongodb, mongodb-atlas, documentdb for hybrid search."
        )

    async def index_server(self, *args: Any, **kwargs: Any) -> None: ...
    async def index_agent(self, *args: Any, **kwargs: Any) -> None: ...
    async def remove_entity(self, *args: Any, **kwargs: Any) -> None: ...

    async def search(self, *args: Any, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
        return {k: list(v) for k, v in _EMPTY.items()}

    async def search_by_tags(self, *args: Any, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
        return {k: list(v) for k, v in _EMPTY.items()}

    async def get_all_tags(self) -> list[str]:
        return []
```
(Match the exact `SearchRepositoryBase` signatures; `index_skill`/`index_virtual_server` inherit the base no-ops.)

**File:** `registry/repositories/factory.py` (lines 132-151, `get_search_repository`)

```python
def get_search_repository() -> SearchRepositoryBase:
    """Get search repository singleton.

    MongoDB-compatible backends use DocumentDB hybrid search. FAISS (the former
    file-backend search engine) has been removed; the file backend now has no
    semantic search and gets a NullSearchRepository so file storage stays usable.
    """
    global _search_repo
    if _search_repo is not None:
        return _search_repo

    backend = settings.storage_backend
    logger.info(f"Creating search repository with backend: {backend}")

    if backend in MONGODB_BACKENDS:
        from .documentdb.search_repository import DocumentDBSearchRepository

        _search_repo = DocumentDBSearchRepository()
    else:
        from .null_search_repository import NullSearchRepository

        _search_repo = NullSearchRepository()

    return _search_repo
```
Remove the `from .file.search_repository import FaissSearchRepository` import path entirely. Using a null object (rather than raising) keeps the eagerly-constructed startup services bootable on the `file` backend and honors the "keep file storage" scope.

#### Step 2: Delete the FAISS service and file search repository
- Delete `registry/search/service.py` (whole file; sole `import faiss`).
- Delete `registry/repositories/file/search_repository.py` (whole file).
- `grep -rn "from .*search.service\|faiss_service\|FaissService\|FaissSearchRepository" registry/` must return no matches after Step 0 + Step 1. If any remain, migrate them (Step 0 mapping) before deleting.

#### Step 3: Simplify startup
**File:** `registry/main.py`
**Lines:** 496-549

Replace the FAISS/DocumentDB branch with the DocumentDB-only path. Keep `search_repo.initialize()` and the agent-state load; delete the whole `if settings.storage_backend not in MONGODB_BACKENDS:` re-index block:

```python
search_repo = get_search_repository()
logger.info("Initializing DocumentDB hybrid search service...")
await search_repo.initialize()
logger.info("Search index is persistent; skipping startup re-index")

logger.info("Loading agent cards and state...")
await agent_service.load_agents_and_state()
```
(Note: on the `file` backend `get_search_repository()` now returns `NullSearchRepository`, whose `initialize()` logs the Step 1 WARNING. Startup succeeds; search returns empty results rather than crashing.)

#### Step 4: Config changes
**File:** `registry/core/config.py`
- Change `storage_backend` default (742-752) from `"file"` to `"mongodb-ce"`.
- Update the empty/None fallback in `_validate_storage_backend` (825-826) from `"file"` to `"mongodb-ce"` so an explicit `STORAGE_BACKEND=""` does not silently revert to a search-less backend.
- Delete `faiss_index_path` (996-997) and `faiss_metadata_path` (999-1001) properties.
- Keep `_validate_storage_backend`; keep `"file"` in `ALLOWED_STORAGE_BACKENDS` (file storage is still valid; `file` now yields `NullSearchRepository` for search).

#### Step 5: Schema, telemetry, metrics
- `registry/core/schemas.py`: delete `FaissMetadata` (505-510) after confirming it is unused.
- `registry/core/telemetry.py` (730-731): stop emitting `"faiss"` (emit `"documentdb"`, or `"none"` for the `file`/null backend). Do NOT narrow the Lambda collector pattern in `terraform/telemetry-collector/lambda/collector/schemas.py:265-269` in the same release; keep it accepting `faiss` (widen, never narrow) so older agents' telemetry is not rejected during rollout. Removing `faiss` from the pattern is a later, post-fleet-upgrade change.
- `registry/metrics/client.py` (111, 126): rename the app-layer field `faiss_search_time_ms` -> `search_time_ms`; keep emitting under the old key only if the metrics-service ingest still requires it (see Open Questions). Prefer adding `search_time_ms` and deprecating the old name.

#### Step 6: Route cleanup
**File:** `registry/api/search_routes.py`
- Fix docstring (385) and comment (510-512) to say "DocumentDB hybrid search".
- The `except RuntimeError:` -> 503 handler (439-444) previously caught the FAISS "not initialized" error. Keep a generic 503 guard for repository failures but reword the log message; do not rely on the FAISS-specific message.

#### Step 7: Dependencies and build
- `pyproject.toml`: remove `"faiss-cpu>=1.7.4",` (line 23). Evaluate `"scikit-learn>=1.3.0",` (line 26) for removal (no imports found; verify). Do NOT remove `sentence-transformers` or `torch`.
- Regenerate the lock: `uv lock` (do not hand-edit `uv.lock`).
- `docker/Dockerfile.registry-cpu` (71-75): **keep** the `SentenceTransformer(...).save(...)` bake step. It serves the DocumentDB embeddings provider too (not just FAISS), and removing it degrades cold-start and breaks air-gapped installs (`registry-entrypoint.sh:250-264` only warns, does not download). Do not remove it as part of FAISS removal. Leave `build-essential` unless proven unnecessary.
- `docker/registry-entrypoint.sh` (241, 250-264): update or remove the local-model presence check tied to the FAISS/local-embeddings path.

#### Step 8: Tests (see testing.md for the full plan)
- DELETE: `tests/unit/search/test_faiss_service.py`, `tests/fixtures/mocks/mock_faiss.py`, `tests/integration/test_search_integration.py` (currently skipped and patches a dead `faiss_service` symbol), and `tests/unit/search/__init__.py` if the dir empties.
- conftest: remove `create_mock_faiss_module` import and the `sys.modules["faiss"]` injection (`tests/conftest.py` 51, 146-149); delete the unused `mock_faiss_service` fixture in `tests/unit/conftest.py` (15-28). Add `search_by_tags`/`get_all_tags` to the `mock_search_repository` fixture (371-386).
- REPOINT `patch("registry.search.service.faiss_service")` -> the search repository / indexing method in: `test_server_routes.py`, `test_server_get_endpoint.py`, `test_skill_inline_content.py`, `test_agent_routes.py`, `test_server_lifecycle.py`, `test_tool_level_access.py`, `test_agent_batch_item_processor.py`.
- DELETE specific tests: `test_config.py::test_faiss_index_path` / `test_faiss_metadata_path`; `test_infrastructure.py::test_mock_faiss_index` + its import.
- UPDATE telemetry expectations: `test_telemetry.py:343`, `test_telemetry_e2e.py:335`, `test_collector.py:458` (`search_backend` expected value -> `documentdb`).
- UPDATE `test_factory_aliases.py` (197-199): drop the `FaissSearchRepository` allowance; for `file`, assert the search factory now raises.
- RENAME-only cosmetic FAISS strings in `test_search_routes.py`, `test_search_routes_local_server.py`, `test_check_duplicates_endpoints.py`, `test_duplicate_check_service.py`.

#### Step 9: Documentation
Rewrite current-mechanism docs to DocumentDB hybrid search; strip incidental mentions; leave release notes. See the doc table in [File Changes](#file-changes).

#### Step 10: Infra scripts and generated specs
- Delete `verify_faiss_metadata()` and calls in `cli/service_mgmt.sh` and `terraform/aws-ecs/scripts/service_mgmt.sh`.
- Simplify `scripts/migrate-file-to-mongodb.py` (216, 220) once `.faiss`/metadata files no longer exist (safe to leave the exclusion, but tidy it).
- Regenerate `api/openapi.json` from the app after code changes (do not hand-edit the FAISS strings at 3869, 4420).

### Error Handling
- The `file` backend logs a prominent startup WARNING (via `NullSearchRepository.initialize`) rather than raising, so file storage stays bootable. The loss of search is explicit in logs and in empty results, not a crash-loop.
- The search route keeps a generic 503 for repository/embedding failures; DocumentDB already degrades gracefully to `_lexical_only_search` when embeddings are unavailable, so 503s should be rare. When rewording the former FAISS-specific handler (`search_routes.py:439-444`), do NOT log the raw exception with `exc_info=True` if it could carry a MongoDB connection URI (`user:pass@host`); scrub the URI as `system_routes.py:213-222` already does. Note the old `except RuntimeError` branch is no longer the fail-fast path (the factory no longer raises), so it will not swallow a config error into a 503.

### Logging
- Remove FAISS-specific log strings ("Rebuilding in-memory FAISS index", "FAISS search service unavailable").
- Startup logs: "Initializing DocumentDB hybrid search service", "Search index is persistent; skipping startup re-index".
- Keep INFO-level backend logging in the factory so operators can see the resolved backend.

## Observability
- **Telemetry**: `search_backend` emits only `documentdb` (or a repo-derived value). Update the Lambda collector schema pattern so ingestion does not reject the new-only value; optionally accept legacy `faiss` on read for old payloads.
- **Metrics**: prefer a generic `search_time_ms`; deprecate `faiss_search_time_ms`. The persisted metrics-service column rename is a separate migration (out of scope) -- until then, either keep writing the old column or add the new field alongside.
- **Logs**: startup and per-request search logs are backend-neutral after this change.

## Scaling Considerations
- DocumentDB hybrid search persists embeddings in the DB and uses an HNSW index (`m=16`, `efConstruction=128`, `efSearch` from `VECTOR_SEARCH_EF_SEARCH=100`), so it scales with the database rather than app memory. Removing FAISS removes the per-boot full re-index and the in-process memory footprint of the FAISS index and metadata store.
- Image size and cold-start improve: dropping `faiss-cpu` (and possibly `scikit-learn`) shrinks the dependency closure. If the build-time model bake is also removed, first-request latency may increase while the model downloads once; mitigate by keeping the bake for the CPU image or pre-warming.
- No new bottlenecks introduced; the search path already existed and is the production default.

## File Changes

### Deleted Files

| File Path | Reason |
|-----------|--------|
| `registry/search/service.py` | Sole `import faiss`; `FaissService` |
| `registry/repositories/file/search_repository.py` | `FaissSearchRepository` |
| `tests/unit/search/test_faiss_service.py` | 100% FAISS-specific |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS mock module |
| `tests/integration/test_search_integration.py` | Skipped; patches dead `faiss_service` symbol |
| `tests/unit/search/__init__.py` | Only if the dir empties |

### New Files

| File Path | Reason |
|-----------|--------|
| `registry/repositories/null_search_repository.py` | `NullSearchRepository` no-op search for the `file` backend |

### Modified Files (code)

| File Path | Lines | Change |
|-----------|-------|--------|
| `registry/api/server_routes.py` | 774, 847, 1113, 1343, 1413, 1643, 1716, 1752, 1769, 1827, 1876, 1949, 2100, 2386, 2502, 2630, 2675, 2760, 3504, 3808, 3989, 4041, 4100, 4178 | Migrate direct `faiss_service.*` calls to `get_search_repository()` (Step 0) |
| `registry/api/agent_routes.py` | 628, 631, 1150, 1152, 1598, 1601, 1853, 1855 | Migrate `faiss_service` calls to `search_repo` |
| `registry/services/agent_batch_item_processor.py` | 225, 228, 338, 340 | Migrate `faiss_service` calls to `search_repo` |
| `registry/repositories/factory.py` | 132-151 | Remove FAISS branch; return `NullSearchRepository` for `file` |
| `registry/main.py` | 496-549 | Delete FAISS re-index block; keep init + agent-state load |
| `registry/core/config.py` | 742-752, 825-826, 996-1001 | Default `mongodb-ce`; fix empty/None fallback; delete FAISS path properties |
| `registry/core/schemas.py` | 505-510 | Delete `FaissMetadata` |
| `registry/core/telemetry.py` | 730-731 | Emit `documentdb` |
| `registry/metrics/client.py` | 111, 126 | Add `search_time_ms`; deprecate `faiss_search_time_ms` |
| `registry/api/search_routes.py` | 385, 439-444, 510-512 | Reword FAISS docstring/comments; generic 503 |
| `registry/repositories/interfaces.py` | 1002 | Fix `SearchRepositoryBase` docstring |
| `pyproject.toml` | 23, 26 | Remove `faiss-cpu`; evaluate `scikit-learn` |
| `uv.lock` | 692-709, 1608, 1710 | Regenerate via `uv lock` |

### Modified Files (build / infra / scripts)

| File Path | Lines | Change |
|-----------|-------|--------|
| `docker/Dockerfile.registry-cpu` | 71-75 | KEEP model bake (serves DocumentDB embeddings; air-gapped) |
| `docker/registry-entrypoint.sh` | 241, 250-264 | Reword local-model check (not FAISS-specific) |
| `docker-compose.yml` | 71, 198, 454, 316-318, 583-584 | Comment; flip env `:-mongodb-ce` (198, 454); comment cleanup for index (no volume) |
| `docker-compose.prebuilt.yml` | 14, 105, 319, 211-213, 436-437 | Comment; flip env (105, 319); mounts |
| `docker-compose.podman.yml` | 4, 95, 318, 204-206, 436-437 | Comment; flip env (95, 318); mounts |
| `build-config.yaml` | 25, 30 | Strip FAISS from comment/description |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | 587 | Comment |
| `terraform/aws-ecs/scripts/service_mgmt.sh` | 184-206, 631, 723 | Delete `verify_faiss_metadata` + calls |
| `terraform/telemetry-collector/lambda/collector/schemas.py` | 265-269 | Keep accepting `faiss` during rollout; narrow only post-fleet-upgrade |
| `cli/service_mgmt.sh` | 166-187, 613, 705 | Delete `verify_faiss_metadata` + calls |
| `cli/agent_mgmt.py` | 34 | Reword help text |
| `scripts/migrate-file-to-mongodb.py` | 216, 220 | Tidy FAISS-file exclusions |
| `api/registry_client.py` | 2617 | Reword docstring |
| `api/openapi.json` | 3869, 4420 | Regenerate from app |

### Modified Files (documentation)

| File | Lines | Action |
|------|-------|--------|
| `docs/embeddings.md` | 13, 19, 176-186, 251-261, 413 | REWRITE to DocumentDB hybrid search |
| `docs/dynamic-tool-discovery.md` | 3, 33, 53, 69, 210, 262-279, 303, 360 | REWRITE section, code, diagram node |
| `docs/service-management.md` | 45, 52, 222, 249, 252 | REWRITE to DocumentDB indexing |
| `docs/api-reference.md` | 334 | REWRITE mention |
| `docs/server-versioning-operations.md` | 196, 318, 325 | REWRITE to DocumentDB index |
| `docs/registry-auth-detailed.md` | 649-650, 666 | Rewrite mermaid node label |
| `docs/design/a2a-protocol-integration.md` | many (69-1122) | REWRITE throughout (largest doc change) |
| `docs/design/server-versioning.md` | 429 | REWRITE mention |
| `registry/embeddings/README.md` | 13, 252-260 | REWRITE integration section |
| `docs/configuration.md` | 295 | Update deprecation note / drop FAISS clause |
| `docs/database-design.md` | 11, 39, 57 | Mark file/FAISS search removed; update table |
| `docs/design/storage-architecture-mongodb-documentdb.md` | 23, 53, 59, 72 | Note removal; update diagram/table |
| `docs/design/database-abstraction-layer.md` | many (50-843) | Remove FAISS search repo refs; update diagrams/tables |
| `docs/llms.txt` | 66, 260, 327, 714-839 | Delete FAISS-specific subsections/legacy rows |
| `docs/TELEMETRY.md` | 46 | Remove `faiss` from accepted values |
| `docs/testing/QUICK-START.md` | 16, 56, 116 | Remove FAISS auto-mock refs |
| `docs/testing/memory-management.md` | 13, 18, 163 | Remove FAISS mock refs |
| `docs/testing/test-categories.md` | 39, 48, 65, 83-90, 110 | Replace `real_faiss_service`/`mock_faiss_service` docs |
| `tests/README.md` | 18, 27, 50, 93-105, 289-297 | Remove FAISS mock docs; document repo-mock convention |
| `docs/prebuilt-images.md` | 9 | Strip "FAISS" from image description |
| `terraform/aws-ecs/OPERATIONS.md` | 136 | Strip "FAISS" from image description |
| `release-notes/v1.0.17.md` | 206, 242, 269 | LEAVE (historical) |
| `docs/faq/configuring-mongodb-atlas-backend.md` | 7 | LEAVE or lightly reword (historical caveat) |
| `docs/OBSERVABILITY-LEGACY.md`, `metrics-service/docs/*` | metric column refs | LEAVE unless column renamed |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted code (service.py ~1200 + file search_repo ~137 + FaissMetadata) | ~1,350 |
| Deleted tests (test_faiss_service ~1132 + mock_faiss ~254 + test_search_integration ~1100) | ~2,490 |
| New code (`null_search_repository.py`) | ~45 |
| Migrated call sites (server_routes ~24, agent_routes ~8, batch ~4) | ~80 changed |
| Modified code (factory, main, config, telemetry, metrics, routes) | ~120 |
| Modified tests (repoint patches, rename, delete cases) | ~250 |
| Modified docs / infra / scripts | ~400 |
| **Net** | **~ -4,900 (a large net deletion)** |

## Testing Strategy
See `./testing.md`. In summary: grep-based removal assertions (both `import faiss` and the ~22 `faiss_service` call sites); backwards-compat tests that the semantic-search and tags endpoints keep identical schemas on a MongoDB backend; write-path tests that registration/toggle/delete still update the DocumentDB index; a test that `STORAGE_BACKEND=file` boots, warns, and returns empty search (does NOT crash); dependency assertion that `import faiss` fails; full `uv run pytest tests/` with no regressions.

## Alternatives Considered

### Alternative 1: Keep the `file` backend by giving it a pure-Python in-process vector search
**Description:** Replace `FaissService` with a NumPy cosine-similarity index so `file` retains semantic search with no MongoDB.
**Pros:** No behavioural break for `file` users; no default change.
**Cons:** Reintroduces exactly the in-process, non-persistent, per-boot-reindex model FAISS had; adds new code to maintain; contradicts the goal of a single search path; `numpy` scan is O(n) with no HNSW benefit.
**Why Rejected:** The task is to consolidate on the maintained DocumentDB hybrid search, not to build a second engine. DocumentDB is already the default everywhere but the code default.

### Alternative 2: Silently route `file` search to DocumentDB
**Description:** In the factory, if backend is `file`, still return `DocumentDBSearchRepository`.
**Pros:** No boot failure.
**Cons:** DocumentDB needs a live MongoDB connection; a genuine file-only deployment has none, so search would fail at query time with a confusing connection error.
**Why Rejected:** Confusing deferred connection error; a `NullSearchRepository` returning empty results with a clear warning is safer.

### Alternative 4: Hard-fail (raise) in the factory for the `file` backend
**Description:** `get_search_repository()` raises `RuntimeError`/`ValueError` when `storage_backend` is `file`.
**Pros:** Loudest possible signal that search is gone.
**Cons:** `get_search_repository()` is constructed eagerly inside service constructors that run at startup (`server_service`, `agent_service`, `semantic_search_service`, `skill_service`), so raising bricks the *entire* `file` backend at boot, not just search - contradicting the "keep file storage" scope. It would also let the `search_routes.py:439-444` `except RuntimeError` swallow the config error into a generic 503.
**Why Rejected:** Blast radius and scope violation. `NullSearchRepository` gives an explicit warning + empty results while keeping file storage bootable. (This was the design's original choice; corrected after expert review.)

### Alternative 3: Remove the `file` backend entirely
**Description:** Drop `"file"` from `ALLOWED_STORAGE_BACKENDS` and delete all `File*Repository` classes.
**Pros:** Maximally simple single backend.
**Cons:** Much larger blast radius (storage, not just search); breaks file-storage deployments; out of scope for a FAISS-removal task.
**Why Rejected:** Scope. This change removes FAISS *search* only; file *storage* stays.

### Comparison Matrix

| Criteria | Chosen (NullSearchRepository + default mongodb-ce) | Alt 1 (numpy search) | Alt 2 (silent reroute) | Alt 3 (drop file) | Alt 4 (hard raise) |
|----------|-----------------------------------------------------|----------------------|------------------------|-------------------|--------------------|
| Complexity | Low | Medium | Low | High | Low |
| Maintains single search path | Yes | No | Yes | Yes | Yes |
| Clear failure mode | Yes (warning + empty) | N/A | No | Yes | Yes (but crashes) |
| Blast radius | Search only | Search only | Search only | Storage + search | Storage + search (boot crash) |
| Breaks file storage | No | No | No | Yes | Yes |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill) -- ordered commits A-D above.
- Phase 2: Testing -- run `uv run pytest tests/`; verify `import faiss` fails; verify default compose stack search works; verify `file` boots and returns empty search with a warning (does NOT crash); verify server/agent registration still updates the DocumentDB index on mongodb-ce.
- Phase 3: Deployment sequencing (order matters):
  1. **Telemetry collector:** keep the Lambda schema pattern permissive (`^(faiss|documentdb)$`, or add mongodb aliases). Do NOT narrow it ahead of the fleet, or not-yet-upgraded agents' telemetry is dropped. Only stop *emitting* `faiss` from the app.
  2. **Metrics ingest:** ensure metrics-service accepts `search_time_ms` before the app emits it.
  3. **Registry app + compose:** flip all six compose `STORAGE_BACKEND` env lines to `${STORAGE_BACKEND:-mongodb-ce}` together with the code default.
  4. **Operator migration:** operators on `STORAGE_BACKEND=file` keep running (search returns empty with a warning) but should migrate to a MongoDB-compatible backend for search. Terraform `file` installs have NO provisioned database; the release notes must include a runbook: provision AWS DocumentDB (`storage_backend="documentdb"`, set `documentdb_admin_password`, choose shard capacity/count -- note the cost) or point `mongodb_connection_string` / `_secret_arn` at an external MongoDB, then re-index (embeddings persist thereafter).
- Rollback: redeploy the previous image tag. DocumentDB-persisted embeddings and the widened telemetry schema do not block a clean downgrade; keep the prior tag available.

## Open Questions
- **`scikit-learn` removal:** no imports found; confirm it is not a required transitive/runtime dep before removing from `pyproject.toml`.
- **Build-time model bake (`docker/Dockerfile.registry-cpu` 71-75):** keep it for offline/air-gapped operation, or remove and let the model download on first use? Recommendation: keep for the CPU image, drop from images that always use a remote embeddings provider.
- **`faiss_search_time_ms` metric column:** rename now (backward-incompatible DB migration) or deprecate and add `search_time_ms` alongside? Recommendation: deprecate, add the new field, schedule the column rename separately.
- **`ALLOWED_STORAGE_BACKENDS`:** keep `"file"` (file storage valid, search rejected) vs remove it. Recommendation: keep, since file storage is out of scope.

## References
- `registry/repositories/documentdb/search_repository.py` -- the retained hybrid-search implementation
- `registry/repositories/factory.py` -- backend selection pattern
- `registry/core/config.py` -- `ALLOWED_STORAGE_BACKENDS`, `MONGODB_BACKENDS`, `_validate_storage_backend`
- `docs/design/storage-architecture-mongodb-documentdb.md` -- storage architecture (marks file/FAISS legacy)
- Reference issue #1285
