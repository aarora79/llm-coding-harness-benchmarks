# Low-Level Design: Remove FAISS from the Codebase

*Created: 2026-07-22*
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

## Overview

### Problem Statement

This repository uses FAISS (Facebook AI Similarity Search) as the vector search
backend for semantic search. FAISS is now obsolete because:

1. **Deployment complexity** -- FAISS requires native C/C++ libraries and
   platform-specific binary wheels, which complicate Docker builds and increase
   image size.
2. **Dual maintenance** -- The DocumentDB hybrid search implementation (BM25 +
   native vector k-NN) already provides equal or better search quality and is
   actively maintained.
3. **Test complexity** -- FAISS is mocked in 10+ test files, adding scaffolding
   that obscures real search behavior.
4. **Documentation drift** -- FAISS is referenced in 60+ documentation locations
   as the primary vector search backend.

FAISS remains the default backend for `storage_backend=file` (local development),
while DocumentDB hybrid search is used for `storage_backend=documentdb` and
MongoDB variants.

### Goals

- Remove FAISS entirely from the dependency graph, source code, tests, and
  documentation.
- Migrate the file-based storage backend search path to use the DocumentDB hybrid
  search repository, so local development still gets vector-capable search.
- Ensure no change to the search API response shape or behavior for existing
  callers.
- Clean up the metrics service's FAISS-specific timing field.

### Non-Goals

- Removing `sentence-transformers`, `torch`, or embedding providers (these are
  still needed for generating embeddings used by DocumentDB hybrid search).
- Removing the `file` storage backend for non-search repositories (servers,
  agents, skills, etc. continue to use file-based storage).
- Migrating existing FAISS index data to DocumentDB (operators must re-index).
- Changing the embeddings API or configuration schema.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/search/service.py` | `FaissService` class -- the sole FAISS vector search implementation (47K lines) | Primary file to delete |
| `registry/repositories/file/search_repository.py` | `FaissSearchRepository` -- wraps FaissService for the factory pattern | Delete; routing replaced |
| `registry/repositories/documentdb/search_repository.py` | `DocumentDBSearchRepository` -- hybrid search (BM25 + vector k-NN) | Replacement; already complete |
| `registry/repositories/interfaces.py` | `SearchRepositoryBase` -- abstract interface all search repos implement | No change; interface unchanged |
| `registry/repositories/factory.py` | Routes search backend by `storage_backend` setting | Modify to always use DocumentDB |
| `registry/core/config.py` | `ALLOWED_STORAGE_BACKENDS` set includes `"file"` | Add deprecation note; no removal |
| `pyproject.toml` | Declares `faiss-cpu>=1.7.4` | Remove from dependencies |
| `registry/api/server_routes.py` | 14+ lazy imports of `faiss_service` | Remove FAISS-specific code paths |
| `registry/api/agent_routes.py` | 4+ lazy imports of `faiss_service` | Remove FAISS-specific code paths |
| `registry/services/agent_batch_item_processor.py` | 2 imports of `faiss_service` | Update search path |
| `metrics-service/` | `faiss_search_time_ms` in schema and client | Remove FAISS timing field |
| `tests/fixtures/mocks/mock_faiss.py` | Full FAISS mock for tests | Delete entire file |
| `tests/unit/search/test_faiss_service.py` | 1000+ lines of FAISS unit tests | Delete entire file |
| `tests/conftest.py` | Auto-mocks `faiss` for all tests | Remove auto-mock |
| `tests/integration/test_search_integration.py` | Integration tests patching `faiss_service` | Rewrite to use DocumentDB mock |
| `tests/integration/test_server_lifecycle.py` | `mock_faiss_service()` fixture | Remove |
| `tests/integration/test_tool_level_access.py` | `fake_faiss` AsyncMock fixtures | Remove |
| `tests/integration/test_telemetry_e2e.py` | Asserts `search_backend == "faiss"` | Update assertion |
| `cli/service_mgmt.sh` | `verify_faiss_metadata()` function | Delete the function |
| `terraform/aws-ecs/scripts/service_mgmt.sh` | `verify_faiss_metadata()` function | Delete the function |
| `docs/embeddings.md` | 5+ FAISS references | Update to reference DocumentDB |
| `docs/configuration.md` | FAISS listed as file-backend con | Remove reference |
| `docs/database-design.md` | FAISS in backend comparison table | Update table |
| `docs/design/a2a-protocol-integration.md` | 15+ FAISS references in lifecycle docs | Update to DocumentDB |
| `docs/design/database-abstraction-layer.md` | 10+ FAISS references | Update architecture docs |

### Existing Patterns Identified

1. **Factory pattern for repository selection** -- `registry/repositories/factory.py`
   dispatches to concrete implementations based on `settings.storage_backend`.
   All production code calls factory functions, never imports concrete classes
   directly. This is the pattern to follow when modifying search routing.

2. **Lazy imports for service access** -- API route modules (`server_routes.py`,
   `agent_routes.py`) use lazy imports of `faiss_service` at the point of use
   (not at module level). This avoids circular imports. After FAISS removal,
   these code paths either route through the search repository or are removed.

3. **SearchRepositoryBase interface** -- `registry/repositories/interfaces.py`
   defines `SearchRepositoryBase` with methods: `search()`, `search_by_tags()`,
   `index_entity()`, `remove_entity()`, `rebuild_index()`, `initialize()`,
   `get_all_tags()`, `index_server()`, `index_agent()`. Both
   `FaissSearchRepository` and `DocumentDBSearchRepository` implement this
   interface.

4. **Graceful degradation** -- When embeddings are unavailable, DocumentDB hybrid
   search falls back to lexical-only search. The file-based FAISS search also
   falls back to lexical-only search. The LLD preserves this behavior.

5. **Embedding lifecycle** -- Embeddings are generated by `EmbeddingsClient`
   (either `SentenceTransformersClient` or `LiteLLMClient`) and stored in the
   search repository. After FAISS removal, the DocumentDB repository handles
   embedding generation natively.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/main.py` | Uses | Calls `search_repo.initialize()` at startup |
| `registry/api/search_routes.py` | Uses | `SearchRepositoryBase.search()` for `/search/agents`, `/search/servers`, etc. |
| `registry/api/server_routes.py` | Uses | FAISS-specific tool recommendation code paths |
| `registry/api/agent_routes.py` | Uses | FAISS-specific agent search and recommendation |
| `registry/services/semantic_search_service.py` | Uses | `SearchRepositoryBase` methods |
| `registry/services/agent_batch_item_processor.py` | Uses | FAISS indexing for batch agent registration |
| `registry/embeddings/client.py` | Used by | Embedding generation (kept, used by DocumentDB) |
| Metrics service | Receives | `faiss_search_time_ms` in metric metadata |
| CLI scripts | Call | `verify_faiss_metadata()` function |
| Docker / Terraform | Reference | FAISS in comments and build descriptions |

### Constraints and Limitations Discovered

1. **Factory routing cannot simply delete the `file` backend** -- Other
   repositories (server, agent, skill, etc.) continue to use `file` storage.
   Only the search repository routing changes. The `storage_backend=file` option
   remains valid but will use DocumentDB search when configured with a
   MongoDB-compatible connection string.

2. **DocumentDBSearchRepository requires a MongoDB connection** -- When
   `storage_backend=file`, `DocumentDBSearchRepository` may not have a valid
   MongoDB connection. The LLD addresses this by making the file backend
   explicitly fall back to lexical-only search when no DocumentDB is available,
   matching the prior behavior where embedding failures caused lexical-only
   fallback.

3. **Embeddings dependency is shared** -- `sentence-transformers` and
   `torch` are used both by FAISS and by DocumentDB hybrid search. These must
   stay. Removing them would break search entirely.

4. **Metrics schema change is backward-incompatible for historical data** --
   The `faiss_search_time_ms` column in the metrics service SQLite database
   will contain NULL after migration. Historical data with FAISS timing values
   will become orphaned fields. This is acceptable for a cleanup PR but should
   be noted in release notes.

## Architecture

### System Context Diagram (Before)

```
[API Routes] --> [FaissService] --vector search--> [FAISS Index (disk)]
                         |
                    [Embeddings Client]
                         |
              [sentence-transformers / LiteLLM]

[API Routes] --> [DocumentDBSearchRepository] --hybrid search--> [DocumentDB]
    (only when storage_backend=documentdb)
```

### System Context Diagram (After)

```
[API Routes] --> [DocumentDBSearchRepository] --hybrid search--> [DocumentDB]
                         |
                    [Embeddings Client]
                         |
              [sentence-transformers / LiteLLM]
    (when no DocumentDB available: lexical-only fallback)
```

### Search Routing After Change

```
factory.get_search_repository()
    |
    +-- storage_backend in MONGODB_BACKENDS (documentdb, mongodb-ce, mongodb, mongodb-atlas)
    |       --> DocumentDBSearchRepository (hybrid search)
    |
    +-- storage_backend == "file"
            --> DocumentDBSearchRepository (hybrid search if DB connected,
                     lexical-only fallback if not)
```

The key change: the `else` branch of the factory no longer returns
`FaissSearchRepository`. Instead it returns `DocumentDBSearchRepository` with
a graceful fallback when no MongoDB connection is configured.

## Data Models

No new data models are introduced. The `SearchRepositoryBase` interface
remains unchanged. The response shape from `search()` and `search_by_tags()`
is preserved.

### DocumentDBSearchRepository Search Result Schema (unchanged)

```python
{
    "servers": [
        {
            "path": str,
            "server_name": str,
            "description": str,
            "tags": list[str],
            "is_enabled": bool,
            "relevance_score": float,
            "match_context": str,
            "matching_tools": list[str],
            "num_tools": int,
        },
    ],
    "tools": [],
    "agents": [...],
    "skills": [...],
    "virtual_servers": [...],
}
```

## API / CLI Design

### API Changes

No new API endpoints. No endpoint signature changes. The response shape from
`/search/agents`, `/search/servers`, `/search/skills`, `/search/mixed` is
unchanged because they all go through `SearchRepositoryBase.search()` which
`DocumentDBSearchRepository` already implements with the same return type.

### CLI Changes

The `cli/service_mgmt.sh` script contains a `verify_faiss_metadata()` function
that checks FAISS index consistency. This function and all its callers must be
removed. The equivalent verification (checking that registered entities appear
in the search index) is not FAISS-specific and should be removed entirely rather
than rewritten, since DocumentDB search verification is handled by DocumentDB's
own consistency guarantees.

### Shell Scripts

| Script | Change |
|--------|--------|
| `cli/service_mgmt.sh` | Delete `verify_faiss_metadata()` function (lines ~166-200) and all callers |
| `terraform/aws-ecs/scripts/service_mgmt.sh` | Delete `verify_faiss_metadata()` function and all callers |

## Configuration Parameters

### Settings Changes

`registry/core/config.py`:

1. Keep `storage_backend` and `ALLOWED_STORAGE_BACKENDS` as-is (file backend
   still valid for non-search repositories).
2. Add a `DEPRECATED` note to the `"file"` backend in `ALLOWED_STORAGE_BACKENDS`
   for non-search repository operations. The search path now uses DocumentDB
   regardless.

### Removed Configuration

The following settings become irrelevant (no code references them after FAISS
removal):

| Setting | Reason Removed |
|---------|----------------|
| `faiss_index_path` (if any) | FAISS index file no longer written |
| `faiss_metadata_path` (if any) | FAISS metadata file no longer written |

Note: check `config.py` for any FAISS-specific settings and remove them.

### Environment Variables

No new environment variables needed. Existing `DOCUMENTDB_*` and `EMBEDDINGS_*`
variables remain unchanged.

## New Dependencies

| Package | Type | Required By | Notes |
|---------|------|-------------|-------|
| `faiss-cpu` | Runtime | -- | REMOVED |
| `sentence-transformers` | Runtime | DocumentDB hybrid search | KEPT |
| `torch` | Runtime | sentence-transformers | KEPT |
| `motor`, `pymongo` | Runtime | DocumentDBSearchRepository | KEPT |

This change uses only existing dependencies. The only dependency removed is
`faiss-cpu`.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Remove FAISS from dependency graph

**File:** `pyproject.toml`

Remove line: `"faiss-cpu>=1.7.4",`

Then run `uv sync` to regenerate `uv.lock`. Verify no other dependency
transitively requires `faiss-cpu`:

```bash
uv tree | grep faiss
```

If `faiss-cpu` appears as a transitive dependency, add a `tool.uv.sources`
override to exclude it or pin it to an empty resolution. In practice, no
other dependency should require it.

#### Step 2: Delete FaissService and FaissSearchRepository

**Files to delete:**

- `registry/search/service.py` -- Entire file (FaissService class, 1000+ lines)
- `registry/repositories/file/search_repository.py` -- Entire file
  (FaissSearchRepository class)
- `registry/repositories/file/__init__.py` -- If it exports FaissSearchRepository

**Files to modify:**

- `registry/repositories/factory.py` -- Replace the `else` branch in
  `get_search_repository()` (line ~146-149) to import and return
  `DocumentDBSearchRepository` instead of `FaissSearchRepository`.

```python
# Before (lines ~132-151 in factory.py):
def get_search_repository() -> SearchRepositoryBase:
    backend = settings.storage_backend
    if backend in MONGODB_BACKENDS:
        from .documentdb.search_repository import DocumentDBSearchRepository
        _search_repo = DocumentDBSearchRepository()
    else:
        from .file.search_repository import FaissSearchRepository
        _search_repo = FaissSearchRepository()
    return _search_repo

# After:
def get_search_repository() -> SearchRepositoryBase:
    global _search_repo
    if _search_repo is None:
        from .documentdb.search_repository import DocumentDBSearchRepository
        _search_repo = DocumentDBSearchRepository()
    return _search_repo
```

#### Step 3: Remove FAISS imports from API route modules

**Files and changes:**

1. `registry/api/server_routes.py` -- Remove all lazy imports of
   `from ..search.service import faiss_service` and any code that calls
   `faiss_service` methods directly. These code paths should instead go
   through `get_search_repository().search()` or `get_search_repository()
   .search_by_tags()`.

2. `registry/api/agent_routes.py` -- Same treatment. Remove `from
   ..search.service import faiss_service` and replace direct calls with
   repository method calls.

3. `registry/services/agent_batch_item_processor.py` -- Remove `faiss_service`
   imports. Batch indexing should use `get_search_repository().index_entity()
   ` or the existing repository indexing methods.

#### Step 4: Update SearchRepositoryBase docstring

**File:** `registry/repositories/interfaces.py`

Change the docstring on `SearchRepositoryBase` (around line 1002):

```
# Before:
"""Abstract base class for semantic/hybrid search using FAISS or DocumentDB."""

# After:
"""Abstract base class for semantic/hybrid search using DocumentDB."""
```

#### Step 5: Clean up the embeddings import in server_routes.py and agent_routes.py

After removing FAISS references, check whether `server_routes.py` or
`agent_routes.py` still import or reference embeddings. If not, remove any
stale embedding-related code that was only used to feed FAISS.

#### Step 6: Update metrics service

**Files to modify:**

1. `metrics-service/metrics_client.py` -- Remove `faiss_search_time_ms`
   parameter from function signatures and metadata dict.

2. `metrics-service/app/storage/database.py` -- Remove `faiss_search_time_ms`
   column from schema and queries.

3. `metrics-service/app/storage/migrations.py` -- Remove
   `faiss_search_time_ms` from migration statements.

4. `metrics-service/docs/database-schema.md` -- Update schema documentation.

5. `metrics-service/tests/test_database.py` -- Remove
   `faiss_search_time_ms` from test data.

#### Step 7: Delete FAISS test infrastructure

**Files to delete:**

- `tests/fixtures/mocks/mock_faiss.py` -- Entire file (FAISS mock)
- `tests/unit/search/test_faiss_service.py` -- Entire file (1000+ lines)
- `tests/unit/search/__init__.py` -- If it only exists for FAISS tests

**Files to modify:**

1. `tests/conftest.py` -- Remove:
   - `from tests.fixtures.mocks.mock_faiss import create_mock_faiss_module`
   - `sys.modules["faiss"] = mock_faiss` auto-mock block
   - The mock search repository fixture (update to mock DocumentDB instead)

2. `tests/integration/test_search_integration.py` -- Rewrite to mock
   `DocumentDBSearchRepository` instead of `FaissService`. The
   `mock_faiss_search_results()` and `setup_faiss_test_data()` helpers become
   `mock_documentdb_search_results()` and `setup_documentdb_test_data()`.

3. `tests/integration/test_server_lifecycle.py` -- Remove `mock_faiss_service()`
   fixture. Replace with `mock_search_repository()` that patches the factory.

4. `tests/integration/test_tool_level_access.py` -- Remove `fake_faiss` fixtures.
   Replace with search repository mocks.

5. `tests/integration/test_telemetry_e2e.py` -- Change assertion from
   `assert payload["search_backend"] == "faiss"` to
   `assert payload["search_backend"] == "documentdb"`.

6. `tests/unit/api/test_search_routes.py` -- Update comments referencing FAISS
   fallback data.

7. `tests/unit/api/test_server_routes.py` -- Update comment about patching
   `faiss_service` to reference search repository instead.

8. `tests/unit/test_safe_eval_arithmetic.py` -- Update comment about
   `importlib.util.find_spec("faiss")`.

9. `tests/unit/lambda/test_collector.py` -- Update `"search_backend": "faiss"`
   to `"search_backend": "documentdb"`.

#### Step 8: Update documentation

**Documentation files with FAISS references (update or remove):**

| File | Action |
|------|--------|
| `docs/embeddings.md` | Update FAISS-specific sections to reference DocumentDB hybrid search |
| `docs/configuration.md` | Remove FAISS from file-backend cons list |
| `docs/database-design.md` | Update backend comparison table (remove FAISS row) |
| `docs/service-management.md` | Update FAISS indexing descriptions |
| `docs/server-versioning-operations.md` | Update FAISS re-indexing references |
| `docs/design/server-versioning.md` | Update FAISS re-indexing references |
| `docs/design/a2a-protocol-integration.md` | Replace FAISS references with DocumentDB |
| `docs/design/storage-architecture-mongodb-documentdb.md` | Update legacy comparison |
| `docs/design/database-abstraction-layer.md` | Update architecture docs |
| `docs/OBSERVABILITY-LEGACY.md` | Update FAISS metrics references |
| `docs/api-reference.md` | Update "FAISS vector search" wording |
| `docs/dynamic-tool-discovery.md` | Update FAISS search references |
| `docs/testing/memory-management.md` | Update FAISS vector index references |
| `docs/testing/QUICK-START.md` | Update FAISS as dependency description |
| `docs/testing/test-categories.md` | Update FAISS auto-mock references |
| `docs/faq/configuring-mongodb-atlas-backend.md` | Update FAISS context |
| `tests/README.md` | Update mock FAISS documentation |
| `CLAUDE.md` | Update FAISS context references |
| `docs/llms.txt` | Update architecture reference |

#### Step 9: Update shell scripts and Terraform

**Files to modify:**

1. `cli/service_mgmt.sh` -- Delete the `verify_faiss_metadata()` function and
   all callers. The function (lines ~166-185) verifies FAISS index
   consistency; this check is not applicable to DocumentDB.

2. `terraform/aws-ecs/scripts/service_mgmt.sh` -- Same: delete
   `verify_faiss_metadata()` function.

3. `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` -- Update comment
   on line 587:
   ```
   # Before: ECS Service: Registry (Main service with nginx, SSL, FAISS, models)
   # After:  ECS Service: Registry (Main service with nginx, SSL, models)
   ```

4. `docker-compose.yml` -- Update comment on line 71:
   ```
   # Before: # Registry service (includes nginx, SSL, FAISS, models)
   # After:  # Registry service (includes nginx, SSL, models)
   ```

5. `docker-compose.prebuilt.yml` -- Update comment on line 14:
   ```
   # Before: # Registry service (includes nginx, SSL, FAISS, models) - using pre-built image
   # After:  # Registry service (includes nginx, SSL, models) - using pre-built image
   ```

6. `terraform/aws-ecs/OPERATIONS.md` -- Update line 136:
   ```
   # Before: | registry | MCP Gateway with nginx, FAISS, ML models | ~4.6GB | ~8 min |
   # After:  | registry | MCP Gateway with nginx, ML models | ~4.6GB | ~8 min |
   ```

7. `registry/servers/mcpgw.json` -- Update server schema comments:
   - Line 197: Replace "FAISS search" with "hybrid search"
   - Line 226: Replace "FAISS search" with "hybrid search"

8. `scripts/migrate-file-to-mongodb.py` -- The `.faiss` file exclusion on
   line 220 can be removed since there are no more `.faiss` files, but keeping
   it is harmless (it just won't match anything).

#### Step 10: Update release notes

Add a release note entry documenting the FAISS removal. The release note should
be placed under `release-notes/` with a name consistent with the current
versioning scheme (e.g., `1.24.5.md` or similar). The release note should
document:

- FAISS dependency removed from `pyproject.toml`
- Search backend now uses DocumentDB hybrid search exclusively
- `storage_backend=file` no longer provides vector search (lexical-only
  fallback)
- Operators using DocumentDB are unaffected
- Local development operators may lose vector search unless they configure a
  MongoDB-compatible backend

#### Step 11: Verification

Run the following verification commands:

```bash
# 1. No FAISS imports remain in production code
grep -rn "import faiss" registry/ auth_server/ || echo "PASS: No faiss imports"

# 2. No FAISS references in key source files
grep -rn "faiss_service" registry/api/ registry/services/ || echo "PASS: No faiss_service refs"

# 3. pyproject.toml clean
grep "faiss" pyproject.toml || echo "PASS: No faiss in pyproject.toml"

# 4. uv sync succeeds
uv sync

# 5. No FAISS in docs (excluding this LLD and release notes)
grep -rni "faiss" docs/ --include="*.md" | grep -v "llms.txt" || echo "PASS: No faiss in docs"

# 6. Test compilation
uv run python -m py_compile registry/repositories/factory.py
uv run python -m py_compile registry/repositories/documentdb/search_repository.py
```

### Error Handling

- If `uv sync` fails because some transitive dependency still pulls in `faiss-cpu`,
  add an explicit `tool.uv.sources` override or check the dependency tree:
  ```bash
  uv tree --invert faiss-cpu
  ```
- If `DocumentDBSearchRepository` fails to connect to MongoDB during file-backend
  startup, it should fall back to lexical-only search (which it already does via
  the `_embed_texts()` unavailability latch). No additional error handling is needed.

### Logging

No new logging needed. Existing `DocumentDBSearchRepository` logging is
sufficient:

- `logger.info("Initializing DocumentDB hybrid search on collection: ...")`
- Fallback messages when embeddings are unavailable
- Search result counts

## Observability

### Tracing / Metrics / Logging Points

Existing OpenTelemetry instrumentation for search operations in
`DocumentDBSearchRepository` continues to work. The `faiss_search_time_ms` metric
field in the metrics service is removed, but the DocumentDB search timing is
captured via `embedding_time_ms` and `hybrid_search_time_ms` (or similar DocumentDB-specific fields).

Key log events for search after FAISS removal:

```python
logger.info("Initializing DocumentDB hybrid search on collection: %s", collection_name)
logger.info("Hybrid search tokens for '%s': %s", query, tokens)
logger.info("Hybrid search completed: %d results across %d types", total_results, len(grouped_results))
```

When embeddings are unavailable (lexical-only fallback):

```python
logger.warning("Embeddings unavailable, falling back to lexical-only search")
```

## Scaling Considerations

### Current Load Assumptions

The existing DocumentDB hybrid search implementation already handles the production
load. FAISS was the legacy backend; DocumentDB is the current production backend.

### Horizontal Scaling

No change. The search repository is a singleton per application instance. Each
instance maintains its own search index (loaded from DocumentDB).

### Bottlenecks

The embedding model loading is the primary bottleneck during startup. This is
unchanged by FAISS removal -- `sentence-transformers` models still need to load
for embedding generation.

### Caching Strategy

The search repository caching behavior is unchanged. DocumentDB results are
cached at the Motor driver level.

## File Changes

### Files to Delete

| File Path | Approx. Lines | Description |
|-----------|---------------|-------------|
| `registry/search/service.py` | ~1100 | FaissService class (entire file) |
| `registry/repositories/file/search_repository.py` | ~137 | FaissSearchRepository (entire file) |
| `tests/fixtures/mocks/mock_faiss.py` | ~123 | FAISS mock implementation |
| `tests/unit/search/test_faiss_service.py` | ~1000+ | FAISS unit tests (entire file) |

### Files to Modify

| File Path | Approx. Lines Changed | Change Description |
|-----------|----------------------|---------------------|
| `pyproject.toml` | 1 | Remove `faiss-cpu>=1.7.4` dependency line |
| `uv.lock` | auto-generated | Regenerated by `uv sync` |
| `registry/repositories/factory.py` | ~5 | Replace FaissSearchRepository with DocumentDBSearchRepository |
| `registry/repositories/interfaces.py` | 1 | Update SearchRepositoryBase docstring |
| `registry/api/server_routes.py` | ~14 imports + code | Remove faiss_service imports and FAISS code paths |
| `registry/api/agent_routes.py` | ~4 imports + code | Remove faiss_service imports |
| `registry/services/agent_batch_item_processor.py` | ~2 imports | Remove faiss_service imports |
| `metrics-service/metrics_client.py` | ~2 | Remove faiss_search_time_ms parameter |
| `metrics-service/app/storage/database.py` | ~3 | Remove faiss_search_time_ms from schema/queries |
| `metrics-service/app/storage/migrations.py` | ~1 | Remove faiss_search_time_ms from migration |
| `metrics-service/docs/database-schema.md` | ~1 | Update schema doc |
| `metrics-service/tests/test_database.py` | ~1 | Remove faiss_search_time_ms from test data |
| `tests/conftest.py` | ~5 | Remove FAISS auto-mock |
| `tests/integration/test_search_integration.py` | ~200+ | Rewrite to use DocumentDB mock |
| `tests/integration/test_server_lifecycle.py` | ~40 | Remove mock_faiss_service fixture |
| `tests/integration/test_tool_level_access.py` | ~300+ | Remove fake_faiss fixtures |
| `tests/integration/test_telemetry_e2e.py` | ~3 | Update search_backend assertion |
| `tests/unit/api/test_search_routes.py` | ~2 | Update FAISS comments |
| `tests/unit/api/test_server_routes.py` | ~1 | Update FAISS comment |
| `tests/unit/test_safe_eval_arithmetic.py` | ~1 | Update FAISS comment |
| `tests/unit/lambda/test_collector.py` | ~1 | Update search_backend value |
| `cli/service_mgmt.sh` | ~40 | Remove verify_faiss_metadata function |
| `terraform/aws-ecs/scripts/service_mgmt.sh` | ~40 | Remove verify_faiss_metadata function |
| `registry/servers/mcpgw.json` | ~2 | Update schema comments |
| `docker-compose.yml` | ~1 | Update service comment |
| `docker-compose.prebuilt.yml` | ~1 | Update service comment |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~1 | Update comment |
| `terraform/aws-ecs/OPERATIONS.md` | ~1 | Update comment |
| Multiple docs files | ~30+ | Update/remove FAISS references |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted code | ~1300 (service.py + search_repo + mock_faiss + test_faiss_service) |
| Deleted tests | ~1123 (test_faiss_service + mock_faiss) |
| Modified source code | ~60 (factory + route files + config) |
| Modified test files | ~250 (integration + unit tests) |
| Modified docs | ~30 (FAISS references in doc strings/comments) |
| Modified infra/shell | ~80 (scripts + terraform + docker comments) |
| Modified metrics | ~10 |
| **Total impact** | **~2853** |

## Testing Strategy

See `testing.md` for the complete test plan. This section provides a pointer.

Key testing areas:
- Import removal: verify no `import faiss` remains in production code
- Factory routing: verify `get_search_repository()` returns DocumentDBSearchRepository
- Search API: verify search endpoints return same response shape
- Metrics: verify no `faiss_search_time_ms` in metrics schema
- Documentation: verify no stale FAISS references in docs

## Alternatives Considered

### Alternative 1: Keep FAISS as a deprecated but optional dependency

**Description:** Keep `faiss-cpu` in the dependency graph but deprecate it,
with a warning at startup when `storage_backend=file`.

**Pros:** No breaking change for operators using `storage_backend=file`.

**Cons:** FAISS native library still complicates builds. No incentive for
operators to migrate. Dual backend maintenance continues indefinitely.

**Why Rejected:** The task explicitly requires removing FAISS, not just
deprecating it. The DocumentDB backend already supports all search operations.

### Alternative 2: Create a FileSearchRepository that uses embeddings only (no FAISS)

**Description:** Build a lightweight file-based search repository that uses
local vector similarity (e.g., numpy cosine similarity) instead of FAISS,
keeping the vector search capability for file-backend operators.

**Pros:** File-backend operators retain vector search.

**Cons:** Adds a new code path that is not tested as thoroughly as DocumentDB
hybrid search. Introduces a new dependency on numpy for vector math. Duplicates
embedding generation logic that already exists in DocumentDBSearchRepository.

**Why Rejected:** Unnecessary complexity. Local development operators can use
Docker Compose which includes MongoDB. The value of vector search on disk files
is limited for a development workflow.

### Comparison Matrix

| Criteria | Chosen (use DocumentDB) | Alt 1 (keep FAISS) | Alt 2 (numpy file search) |
|----------|------------------------|---------------------|---------------------------|
| Complexity | Low | None (status quo) | Medium |
| Deployment simplicity | High (no native libs) | Low (FAISS native libs) | Medium (numpy) |
| Search quality | High (Hybrid BM25+vector) | High (FAISS vector) | Medium (numpy vector only) |
| Test coverage | High (existing tests) | Medium | Low (new code) |
| Maintenance burden | Low (single backend) | High (two backends) | Medium (three backends) |

## Rollout Plan

- Phase 1: Implementation -- Remove FAISS code, dependencies, and tests. Update
  factory routing.
- Phase 2: Testing -- Run full test suite, verify search endpoints, verify no
  import errors.
- Phase 3: Deployment -- Update Docker build, release notes, and migration
  guide for operators.

## Open Questions

- Should `storage_backend=file` continue to be a supported option after FAISS
  removal? The `file` backend is still used for non-search repositories
  (servers, agents, skills). The search path now requires DocumentDB even for
  `storage_backend=file`. This means local development operators must have a
  MongoDB-compatible instance running for vector search to work. The Docker
  Compose setup already includes MongoDB, so this is not a regression for
  Docker Compose users. For non-Docker local development, operators must
  configure a MongoDB connection.

## References

- Hybrid search architecture: `docs/design/hybrid-search-architecture.md`
- DocumentDB search repository: `registry/repositories/documentdb/search_repository.py`
- Search repository interface: `registry/repositories/interfaces.py`
- Embeddings client: `registry/embeddings/client.py`
- Repository factory: `registry/repositories/factory.py`
- GitHub Issue: `github-issue.md`