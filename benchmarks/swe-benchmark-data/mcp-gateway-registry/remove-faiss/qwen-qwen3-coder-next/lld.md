# Low-Level Design: Remove FAISS from the codebase

*Created: 2026-06-05*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [Implementation Details](#implementation-details)
6. [File Changes](#file-changes)
7. [Testing Strategy](#testing-strategy)
8. [Rollout Plan](#rollout-plan)

## Overview

### Problem Statement
FAISS (Facebook AI Similarity Search) is an obsolete vector search library that has been replaced by a DocumentDB-based hybrid search implementation. The current codebase maintains both implementations, which creates:
- Unnecessary dependency (`faiss-cpu>=1.7.4`)
- Maintenance burden (two search implementations)
- Confusion for new developers

### Goals
- Remove FAISS as a dependency
- Delete FAISS-specific code paths
- Ensure DocumentDB/file backend search continues to work
- Update documentation and configurations
- Maintain backward compatibility for search API

### Non-Goals
- Changing the search API or behavior
- Updating the metrics-service (separate service)
- Modifying the DocumentDB search implementation

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to FAISS Removal |
|----------------|---------|--------------------------|
| `pyproject.toml` | Python dependencies | Contains `faiss-cpu>=1.7.4` dependency |
| `registry/search/service.py` | FAISS vector search service | Main FAISS implementation (998 lines) |
| `registry/repositories/file/search_repository.py` | File-based search with FAISS | Uses FaissService internally |
| `registry/repositories/documentdb/search_repository.py` | DocumentDB hybrid search | **This is the maintained alternative** |
| `registry/repositories/factory.py` | Repository factory | Routes to FaissSearchRepository for file backend |
| `registry/core/config.py` | Settings class | Contains `faiss_index_path` and `faiss_metadata_path` properties |
| `registry/core/schemas.py` | Pydantic models | Contains `FaissMetadata` model |
| `docker-compose*.yml` | Docker configuration | Comments mention FAISS |
| `build-config.yaml` | Build configuration | Comments mention FAISS |
| `tests/fixtures/mocks/mock_faiss.py` | Mock for testing | FAISS-specific test fixtures |
| `tests/unit/search/test_faiss_service.py` | Unit tests | Tests for FAISS service |

### Existing Patterns Identified
1. **Repository Factory Pattern**: `registry/repositories/factory.py` chooses between implementations based on `storage_backend`
   - File backend => `FaissSearchRepository`
   - DocumentDB backend => `DocumentDBSearchRepository`
   
2. **Search Interface**: All search repositories implement `SearchRepositoryBase` - this abstraction enables easy swapping

3. **Embedding Abstraction**: `embeddings/create_embeddings_client()` creates embeddings via a factory pattern, supporting multiple providers

4. **Hybrid Search in DocumentDB**: The DocumentDB implementation already has hybrid search (vector + keyword) with:
   - HNSW vector index
   - Keyword text boosting
   - Reciprocal Rank Fusion (RRF) for score combination

5. **Fallback Mechanisms**: DocumentDB search has fallback paths for:
   - MongoDB CE (no vector search) => client-side cosine similarity
   - Embedding model unavailable => lexical-only search

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `factory.py` | Factory pattern | Routes based on `settings.storage_backend` |
| `agent_routes.py` | API layer | Calls `get_search_repository().search()` |
| `search_routes.py` | API layer | Calls `get_search_repository().search()` |
| `cli/agent_mgmt.py` | CLI | Uses search repository for `search` command |

### Constraints and Limitations Discovered
- **File backend**: Currently depends on FAISS; after removal, file backend needs an alternative
- **MongoDB CE compatibility**: DocumentDB search already has client-side fallback for environments without vector search
- **Test mocks**: `tests/fixtures/mocks/mock_faiss.py` exists to avoid loading native FAISS library during tests
- **Metrics-service**: Also has FAISS references but is a separate service (out of scope per spec)

## Architecture

### Current Architecture (with FAISS)
```
┌─────────────────────────────────────────────────────────────────┐
│                        Search Service Layer                     │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐          ┌──────────────────────────────┐  │
│  │ DocumentDB      │          │  FAISS-based File            │  │
│  │ Hybrid Search   │          │  Search Repository           │  │
│  │                 │          │                              │  │
│  │ - HNSW Index    │          │  Uses FaissService:          │  │
│  │ - Keyword Boost │          │  - faiss.IndexFlatIP         │  │
│  │ - RRF Fusion    │          │  - Embedding model           │  │
│  └─────────────────┘          │  - Metadata store            │  │
│                               └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ^
                              │
              ┌───────────────┴───────────────┐
              │  registry/repositories/factory│
              └───────────────┬───────────────┘
                              │
              storage_backend = "documentdb" / "file"
```

### Target Architecture (without FAISS)
```
┌─────────────────────────────────────────────────────────────────┐
│                        Search Service Layer                     │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐          ┌──────────────────────────────┐  │
│  │ DocumentDB      │          │  File-based Search with      │  │
│  │ Hybrid Search   │          │  In-Memory Vector Search     │  │
│  │                 │          │  (Lightweight Implementation)│  │
│  │ - HNSW Index    │          │                              │  │
│  │ - Keyword Boost │          │  Alternative approaches:     │  │
│  │ - RRF Fusion    │          │  - In-memory FAISS (if      │  │
│  └─────────────────┘          │  needed temporarily)         │  │
│                               └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ^
                              │
              ┌───────────────┴───────────────┐
              │  registry/repositories/factory│
              └───────────────┬───────────────┘
                              │
              storage_backend = "documentdb" / "file"
```

### Key Changes
1. **Remove `FaissSearchRepository`**: File backend will use DocumentDB search with in-memory fallback, or implement a lightweight vector search
2. **Update factory.py**: Remove FAISS-specific routing - file backend should use DocumentDB search for consistency

## Data Models

### FAISS-Specific Models to Remove

#### `FaissMetadata` in `registry/core/schemas.py`
```python
class FaissMetadata(BaseModel):
    """FAISS metadata model."""
    id: int
    text_for_embedding: str
    full_server_info: ServerInfo
```
**Action**: Delete this model entirely

### New Models
None - no new models needed. Search behavior is preserved through DocumentDB implementation.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Remove FAISS dependency from pyproject.toml
**File:** `pyproject.toml`
**Lines:** ~23

Remove the faiss-cpu dependency:
```diff
-    "faiss-cpu>=1.7.4",
     "sentence-transformers>=3.0.0",
```

#### Step 2: Delete FaissSearchRepository
**File:** `registry/repositories/file/search_repository.py`
**Lines:** 1-137 (entire file)

**Action:** Delete the entire `FaissSearchRepository` class

#### Step 3: Update repository factory
**File:** `registry/repositories/factory.py`
**Lines:** 132-151

Current code:
```python
def get_search_repository() -> SearchRepositoryBase:
    global _search_repo
    
    if _search_repo is not None:
        return _search_repo
    
    backend = settings.storage_backend
    logger.info(f"Creating search repository with backend: {backend}")
    
    if backend in MONGODB_BACKENDS:
        from .documentdb.search_repository import DocumentDBSearchRepository
        _search_repo = DocumentDBSearchRepository()
    else:
        from .file.search_repository import FaissSearchRepository
        _search_repo = FaissSearchRepository()
    
    return _search_repo
```

**Action:** For file backend, use DocumentDB search repository (the maintained implementation). The DocumentDB implementation can work with any storage backend - it just needs a local MongoDB instance or in-memory mode.

#### Step 4: Update config.py to remove FAISS paths
**File:** `registry/core/config.py`
**Lines:** 995-1001

Remove these properties:
```python
@property
def faiss_index_path(self) -> Path:
    return self.servers_dir / "service_index.faiss"

@property
def faiss_metadata_path(self) -> Path:
    return self.servers_dir / "service_index_metadata.json"
```

#### Step 5: Delete FAISS service module
**File:** `registry/search/service.py`
**Lines:** 1-1202 (entire file)

**Action:** Delete the entire `FaissService` class and the global `faiss_service` instance

**Note:** The DocumentDB search implementation has its own embedding logic via `create_embeddings_client()` so no replacement is needed for embedding functionality

#### Step 6: Delete mock FAISS files
**Files to delete:**
- `tests/fixtures/mocks/mock_faiss.py`
- `tests/unit/search/test_faiss_service.py`
- `tests/conftest.py` - remove FAISS-related imports and fixtures
- `tests/unit/conftest.py` - remove FAISS-related fixtures
- `tests/unit/search/__init__.py` - remove FAISS comment

#### Step 7: Update test imports and references
**Files to modify:**
- `tests/README.md` - update documentation
- `tests/test_infrastructure.py` - update tests
- `tests/unit/api/test_agent_routes.py` - update mocks
- `tests/conftest.py` - remove mock_faiss module installation
- `tests/unit/test_safe_eval_arithmetic.py` - remove FAISS module patching

#### Step 8: Update documentation
**Files to modify:**
- `docs/embeddings.md` - update search architecture explanation
- `docs/design/` - review for FAISS references
- `docs/testing/` - update test documentation
- `docs/faq/` - update FAQ if FAISS mentioned

#### Step 9: Update configuration examples
**Files to modify:**
- `.env.example` - remove any FAISS-related comments
- `build-config.yaml` - remove FAISS mentions
- `docker-compose*.yml` - update comments

#### Step 10: Update Terraform
**Files to modify:**
- `terraform/aws-ecs/OPERATIONS.md` - remove FAISS from service description
- `terraform/aws-ecs/scripts/service_mgmt.sh` - remove FAISS verification functions
- `terraform/telemetry-collector/lambda/collector/schemas.py` - update validation pattern

#### Step 11: Update CLI
**File:** `cli/agent_mgmt.py`
**Lines:** ~1

The CLI references FAISS in documentation:
```python
"""The 'search' command performs natural language semantic search using FAISS vector index"""
```

**Action:** Update docstring to reference "vector search" or "hybrid search" instead of FAISS

#### Step 12: Verify remaining references
```bash
# Check for any remaining FAISS references
grep -ri "faiss" registry/ --include="*.py"
grep -ri "faiss" docs/ --include="*.md"
grep -ri "faiss" terraform/ --include="*.py" --include="*.tf" --include="*.sh"
grep -ri "faiss" .env.example
```

## Configuration Parameters

### Parameters to Remove
None - no new parameters are needed. The DocumentDB search repository configures itself based on existing settings.

### Settings Class Changes
The following properties will be removed (no replacement needed):
- `faiss_index_path` - was `Path("/app/registry/servers/service_index.faiss")`
- `faiss_metadata_path` - was `Path("/app/registry/servers/service_index_metadata.json")`

## New Dependencies
None - removing `faiss-cpu` reduces dependencies.

## Implementation Details (continued)

### Search Backend Strategy After Changes

| `storage_backend` | Search Implementation | Notes |
|-------------------|----------------------|-------|
| `file` | `DocumentDBSearchRepository` with local fallback | Uses same code path as documentdb |
| `documentdb` | `DocumentDBSearchRepository` | Standard DocumentDB search |
| `mongodb-ce` | `DocumentDBSearchRepository` | Uses client-side fallback if vector search not supported |
| `mongodb` | `DocumentDBSearchRepository` | Standard DocumentDB search |
| `mongodb-atlas` | `DocumentDBSearchRepository` | Uses HNSW vector index |

### Error Handling
- If `faiss` import fails during migration, it should be caught and the error message should guide users to update their dependencies
- If DocumentDB connection fails, fallback to lexical-only search (already implemented)

### Logging
Add logging at startup to confirm search backend initialization:
```python
logger.info(f"Search repository initialized with backend: {settings.storage_backend}")
```

## File Changes Summary

### Files to Delete
| File | Reason |
|------|--------|
| `registry/search/service.py` | FAISS service implementation |
| `tests/fixtures/mocks/mock_faiss.py` | Mock for FAISS testing |
| `tests/unit/search/test_faiss_service.py` | FAISS service unit tests |

### Files to Modify
| File | Lines | Change |
|------|-------|--------|
| `pyproject.toml` | ~23 | Remove `faiss-cpu>=1.7.4` |
| `registry/repositories/factory.py` | 132-151 | Remove FAISS routing path |
| `registry/core/config.py` | 995-1001 | Remove faiss path properties |
| `registry/core/schemas.py` | 505-510 | Remove FaissMetadata model |
| `tests/conftest.py` | Multiple | Remove FAISS mocks |
| `tests/unit/conftest.py` | Multiple | Remove FAISS fixtures |
| `tests/unit/search/__init__.py` | 1 | Update docstring |
| `tests/unit/api/test_agent_routes.py` | Multiple | Remove FAISS mocks |
| `tests/unit/core/test_config.py` | Multiple | Remove FAISS tests |
| `cli/agent_mgmt.py` | 1 | Update docstring |

### Files to Delete (Tests)
- `tests/unit/search/test_faiss_service.py`

### Files with Comments Only
- `docs/embeddings.md` - update explanation
- `docker-compose*.yml` - update comments
- `build-config.yaml` - update comments
- `terraform/aws-ecs/OPERATIONS.md` - update table
- `terraform/aws-ecs/scripts/service_mgmt.sh` - remove functions
- `terraform/telemetry-collector/lambda/collector/schemas.py` - update pattern

## Testing Strategy

### Unit Tests to Add
- Test that `get_search_repository()` returns correct type for each backend
- Test that DocumentDB search works without FAISS
- Test search functionality end-to-end

### Backwards Compatibility Tests
- Verify search API returns same structure without FAISS
- Verify `FaissSearchRepository` no longer exists
- Verify `faiss` import fails with proper error

### Test Commands
```bash
# Verify no FAISS imports remain
uv run pytest tests/ -k "search" --tb=short

# Test file backend
export STORAGE_BACKEND=file
uv run pytest tests/ -k "search" --tb=short

# Test documentdb backend
export STORAGE_BACKEND=documentdb
uv run pytest tests/ -k "search" --tb=short

# Full test suite
uv run pytest
```

## Alternatives Considered

### Alternative 1: Keep FAISS but mark as deprecated
**Description:** Keep the code but add deprecation warnings
**Pros:** Gradual migration path
**Cons:** More complex codebase, maintenance burden
**Why Rejected:** Spec calls for complete removal, not deprecation

### Alternative 2: Replace FaissSearchRepository with a new lightweight implementation
**Description:** Write a new in-memory vector search from scratch
**Pros:** Full control, no external dependencies
**Cons:** Significant development effort, potential bugs
**Why Rejected:** DocumentDB search is already maintained and works for both backends

### Alternative 3: Keep DocumentDB search for DB backend, use something else for file backend
**Description:** Two different search implementations based on backend
**Pros:** Optimized for each backend
**Cons:** More code to maintain, test doubles
**Why Rejected:** Single search implementation is simpler and easier to maintain

### Comparison Matrix

| Criteria | Chosen Approach | Keep FAISS | New Implementation |
|----------|----------------|------------|-------------------|
| Code Complexity | Low (unified) | Medium (dual) | High (new code) |
| Maintenance | Low (one codebase) | Med (two backends) | High (new features) |
| Testing | Medium (Docker tests) | Low (FAISS tests) | High (new tests) |
| Performance | High (HNSW in DB) | Med (local FAISS) | Unknown |

## Rollout Plan

### Phase 1: Code Changes (out of scope for this skill)
1. Remove FAISS dependency
2. Delete FAISS-specific code
3. Update repository factory
4. Update tests

### Phase 2: Testing
1. Run unit tests
2. Run integration tests
3. Test file backend
4. Test documentdb backend

### Phase 3: Documentation
1. Update README.md
2. Update docs/embeddings.md
3. Update terraform documentation

### Phase 4: Deployment
1. Update `.env.example`
2. Update docker-compose files
3. Deploy to staging
4. Deploy to production

## Open Questions

1. **Should the file backend still work?** The current implementation uses DocumentDBSearchRepository for all backends, but file backend might need a different approach if DocumentDB is not available.

2. **What about the metrics-service?** It also has FAISS references but is a separate service. Should it be handled in a separate issue?

3. **Should we keep any FAISS-related fixtures for backwards compatibility?** Tests that expect FAISS should be updated to expect DocumentDB search behavior.

## References
- `registry/search/service.py` - Current FAISS implementation (to be deleted)
- `registry/repositories/documentdb/search_repository.py` - DocumentDB hybrid search (maintained)
- `registry/repositories/file/search_repository.py` - FAISS repository (to be deleted)
- Issue #955 - Search optimization
