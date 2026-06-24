# Low-Level Design: FAISS Removal and Search Unification

*Created: 2026-06-15*
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
FAISS adds technical debt through binary complexity, maintenance burden, and inconsistent search behavior across deployments. The existing `DocumentDBSearchRepository` provides equivalent functionality without these drawbacks.

### Goals
- Eliminate FAISS dependency and all related code
- Unify search implementation on DocumentDB for all storage backends
- Preserve existing search functionality and API compatibility
- Reduce Docker image size and build time
- Simplify deployment and maintenance

### Non-Goals
- Improve search algorithm performance
- Change DocumentDB schema or indexing strategy
- Modify embedding model selection or configuration
- Optimize query execution time

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `pyproject.toml` | Project dependencies | Contains `faiss-cpu>=1.7.4` to remove |
| `registry/repositories/file/search_repository.py` | FAISS-based search implementation | To be deleted |
| `registry/repositories/documentdb/search_repository.py` | DocumentDB-based search implementation | Will become the default |
| `registry/repositories/factory.py` | Repository factory pattern | Need to update search factory method |
| `registry/search/service.py` | FAISS service implementation | To be deleted |
| `tests/unit/search/test_faiss_service.py` | FAISS service tests | To be deleted |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS mock for testing | To be deleted |
| `docker-compose.yml` | Docker configuration | Contains FAISS-related environment vars |
| Various config/docs files | Configuration and docs | Contain FAISS references to remove |

### Existing Patterns Identified

1. **Repository Factory Pattern**: Centralized factory creates concrete implementations based on configuration
   - Files: `registry/repositories/factory.py`
   - How a future implementer should follow this: Preserve the factory pattern but simplify it by removing the conditional that returns FAISS repository

2. **Search Interface Pattern**: Common interface with different implementations
   - Files: `registry/repositories/interfaces.py`, `registry/repositories/{file,documentdb}/search_repository.py`
   - How a future implementer should follow this: Maintain interface but ensure DocumentDB implementation supports all required methods

3. **Configuration-Based Behavior**: Different behavior based on `storage_backend` setting
   - Files: `registry/core/config.py`, various repository factories
   - How a future implementer should follow this: Remove storage_backend conditionality where it only determines FAISS vs DocumentDB search

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/main.py` | Uses/search factory | Import search repository from factory |
| `registry/api/search_routes.py` | Uses/search factory | Inject search repository dependency |
| Various test files | Uses/Faiss mocks | Import and use mock_faiss fixture |
| `docker-compose.yml` | References/FAISS env | Docker container environment variables |
| Documentation files | References/FAISS config | Configuration guides and examples |

### Constraints and Limitations Discovered

- **Storage Backend Compatibility**: When using file backend, DocumentDB repository still requires MongoDB connection
- **Testing Coverage**: Existing tests heavily rely on FAISS mocks and may need adjustment
- **Embedding Compatibility**: Must ensure `sentence-transformers` embeddings work with DocumentDB vector queries
- **Deployment Surface**: Must update multiple deployment configurations (Docker, ECS, Helm)

## Architecture

### System Context Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    mcp-gateway-registry                         │
├─────────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────┐    ┌─────────────────┐    ┌───────────────┐   │
│  │API Layer    │    │Search Service  │    │DocumentDB     │   │
│  │(FastAPI)     │───▶│Repository      │───▶│(MongoDB)      │   │
│  └─────────────┘    └─────────────────┘    └───────────────┘   │
│                                                               │
├─────────────────────────────────────────────────────────────────┤
│                      Deployment Options                        │
├─────────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │Docker/Compose│    │ECS Cluster  │    │Helm Charts  │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
│                                                               │
└─────────────────────────────────────────────────────────────────┘
```

### Sequence Diagram - Post Removal

```
classmethod This ensures all searches use the same underlying implementation regardless of storage backend.

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Search Architecture After                     │
├─────────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Unified Search Implementation 🎯                      │  │
│  │                                                    │  │
│  │  ┌───────────────────────────┐                      │  │
│  │  │ SearchRepositoryFactory  │                      │  │
│  │  └───────────────────────────┘                      │  │
│  │                                                    │  │
│  │                    ▼                                  │  │
│  │  ┌───────────────────────────┐                      │  │
│  │  │ DocumentDBSearchRepo      │                      │  │
│  │  │(always returns this)     │ <─────────────────┐   │  │
│  │  └───────────────────────────┘                      │  │
│  │                                                    │  │
│  └───────────────┬────────────────────────────────────┘  │
│                  │                                        │
│                  ▼                                        │
│  ┌───────────────────────────┐                              │
│  │  SearchController       │ <────────────────────────────┘
│  └───────────────────────────┘
│                         │
│                         ▼
│  ┌───────────────────────────┐                              │
│  │  FastAPI Endpoints       │                              │
│  └───────────────────────────┘                              │
│
│  Benefit: Consistent search behavior across all deployments │
│
└─────────────────────────────────────────────────────────────────┘
```

## Data Models

### New Models

None - No new data models are required. Existing models will be preserved.

### Model Changes

None - No changes to existing data models or schemas.

## API / CLI Design

### New Endpoints / Commands

**Not applicable** - This change does not add new endpoints or CLI commands. Existing APIs remain unchanged.

### Modified Endpoints

**Not applicable** - Search endpoints (`/v1/search`, `/v1/tools/search`, etc.) will maintain identical request/response shapes.

## Configuration Parameters

### New Environment Variables

None - No new environment variables are required.

### Settings / Config Class Updates

None - No changes to existing configuration classes. The `storage_backend` setting will continue to work, but it will no longer affect search repository selection.

### Deployment Surface Checklist

**To Verify After Implementation:**

- [ ] `.env.example` - Ensure no FAISS-specific configuration remains
- [ ] `docker-compose.yml` - Remove FAISS-related environment variables
- [ ] `docker-compose.podman.yml` - Remove FAISS-related environment variables
- [ ] `docker-compose.prebuilt.yml` - Remove FAISS-related environment variables
- [ ] `terraform/aws-ecs/*.tf` - Remove FAISS references from ECS task definitions
- [ ] `terraform/telemetry-collector/*` - Verify no FAISS dependencies
- [ ] Helm charts (if any) - Remove FAISS references
- [ ] Documentation files - Remove FAISS configuration examples

## New Dependencies

**None** - This change removes a dependency rather than adding one:

- Removes: `faiss-cpu>=1.7.4`
- Retains: `sentence-transformers>=3.0.0`, `motor>=3.3.0`, `pymongo>=4.6.0` (required for DocumentDB)

This change uses only existing dependencies and significantly reduces the dependency surface.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Update pyproject.toml
**File:** `pyproject.toml`
**Lines:** ~23

```python
# Remove this line from dependencies:
# "faiss-cpu>=1.7.4",  # REMOVED - no longer needed
```

#### Step 2: Delete FAISS Service Implementation
**File:** `registry/search/service.py`
**Lines:** Entire file (54.4KB)

Delete the entire file. This removes:
- `FaissService` class
- FAISS index management
- All FAISS-related business logic

#### Step 3: Delete FAISS Repository
**File:** `registry/repositories/file/search_repository.py`
**Lines:** Entire file

Delete the entire file. This removes:
- `FaissSearchRepository` class
- FAISS-specific search methods
- File-based search factory dependency

#### Step 4: Update Repository Factory
**File:** `registry/repositories/factory.py`
**Lines:** ~132-151

```python
def get_search_repository() -> SearchRepositoryBase:
    """Get search repository singleton."""
    global _search_repo

    if _search_repo is not None:
        return _search_repo

    # Always use DocumentDB search regardless of storage backend
    # FAISS has been removed - DocumentDB search is now the default
    from .documentdb.search_repository import DocumentDBSearchRepository

    _search_repo = DocumentDBSearchRepository()
    logger.info("Initialized unified DocumentDB search repository")

    return _search_repo
```

#### Step 5: Delete Test Files
**Files:** Test files to remove completely:
- `tests/unit/search/test_faiss_service.py` - FAISS service tests
- `tests/fixtures/mocks/mock_faiss.py` - FAISS mock

#### Step 6: Update Other FAISS References
**Files:** Update test imports and mocks:
- `tests/unit/conftest.py` - Remove FAISS-specific fixtures
- Files importing `FaissSearchRepository` directly should use factory instead
- Update any documentation files containing FAISS configuration examples

#### Step 7: Cleanup Docker Configuration
**Files:** Update container configurations:
- Remove FAISS-specific environment variables from Docker compose files
- Update any FAISS-related build configuration

### Error Handling

No new error handling required. The existing `DocumentDBSearchRepository` implementation already includes proper error handling for:
- Connection errors to MongoDB
- Invalid query parameters
- Missing collections or indexes
- Invalid embedding vectors

### Logging

Add informative logging to help operators verify the migration:

**File:** `registry/repositories/factory.py`
**Lines:** ~149-150

```python
# Add before starting DocumentDB search
logger.info("FAISS has been removed - using unified DocumentDB search backend")
```

## Observability

### Tracing / Metrics / Logging Points

Preserve all existing observability:

- **Metrics**: DocumentDB operation metrics (query time, result count)
- **Tracing**: Span for search operations via OpenTelemetry
- **Logging**:Search initialization, query execution, error handling
- **Health checks**: Existing database connectivity checks

Add these verification points for migration:

```python
# Add to search repository initialization
logger.info("DocumentDB Search Repository initialized - FAISS successfully removed")
```

## Scaling Considerations

### Current Load Assumptions

- DocumentDB already handles search load for MongoDB backend deployments
- No increase in database load expected - same queries, different source
- FAISS removal reduces memory footprint in containers
- MongoDB vector search performs equivalently for this use case

### Horizontal Scaling

No changes needed - existing mechanisms work:
- Multiple registry instances can connect to same DocumentDB
- Connection pooling handled by Motor async driver
- DocumentDB search is stateless unlike FAISS

### Bottlenecks

Remove the FAISS bottleneck:
- **Before**: FAISS binary loading during startup (100-200ms)
- **After**: Direct DocumentDB connection (no binary loading)

### Caching Strategy

Preserve existing caching:
- Query results caching (if implemented) continues to work
- Embedding caching remains unchanged
- No faiss index persistence needed

## File Changes

### New Files

None - No new files are required.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `pyproject.toml` | 23 | Remove `faiss-cpu>=1.7.4` dependency |
| `registry/repositories/factory.py` | 132-151 | Update search factory to always use DocumentDB |
| Various test files | Multiple | Remove FAISS-specific test code |
| Docker compose files | Multiple | Remove FAISS-related environment variables |
| Documentation files | Multiple | Remove FAISS configuration examples |

### Files to Delete

| File Path | Description |
|-----------|-------------|
| `registry/search/service.py` | FAISS service implementation |
| `registry/repositories/file/search_repository.py` | FAISS repository implementation |
| `tests/unit/search/test_faiss_service.py` | FAISS service tests |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS test mocks |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Removed code | ~1,200 |
| Removed tests | ~800 |
| Modified code | ~50 |
| Modified config | ~20 |
| **Total reduction** | **~2,070** |

## Testing Strategy

See dedicated `testing.md` file for comprehensive test plan.

## Alternatives Considered

### Alternative 1: Keep Both Backends
**Description:** Maintain conditional logic and let operators choose
**Pros:** Maximum flexibility, gradual migration possible
**Cons:** Continues maintenance burden, inconsistent behavior, defeats purpose of removal
**Why Rejected:** Contradicts the goal of eliminating FAISS dependencies

### Alternative 2: Inline FAISS Removal
**Description:** Remove only FAISS imports, keep stub implementations
**Pros:** Less disruptive change, existing code continues "working"
**Cons:** Dead code remains, testing doesn't catch failures, misleading complexity
**Why Rejected:** Violates clean removal principle

### Alternative 3: Transition Period with Warnings
**Description:** Keep FAISS but log warnings and plan migration
**Pros:** Gradual adoption, less risk for existing deployments
**Cons:** Extends technical debt timeline, maintenance burden continues
**Why Rejected:** Goal is immediate removal to eliminate binary complexity

### Comparison Matrix

| Criteria | Chosen (Complete Removal) | Alt 1 (Keep Both) | Alt 2 (Stub) | Alt 3 (Transitional) |
|----------|------------------------|-----------------|--------------|-------------------|
| Complexity | Low | High | Medium | Medium |
| Risk | Medium | Low | High | Medium |
| Maintenance | Lowest | Highest | Medium | Medium |
| Consistency | High | Low | Medium | Medium |
| Image Size | Smallest | Large | Large | Large |
| Build Time | Fastest | Slow | Slow | Slow |
| Migration Effort | Complete | Minimal | Partial | Gradual |

## Rollout Plan

### Phase 1: Implementation (Current Scope)
- [ ] Update pyproject.toml - remove FAISS dependency
- [ ] Delete FAISS service and repository implementations
- [ ] Update factory to always use DocumentDB search
- [ ] Remove FAISS-specific tests and fixtures
- [ ] Clean up configuration files and documentation

### Phase 2: Testing (Next Step)
- [ ] Run existing test suite to catch regressions
- [ ] Verify search endpoints return correct results
- [ ] Validate Docker builds succeed without FAISS
- [ ] Test ECS deployments with updated configuration
- [ ] Confirm Helm deployments work correctly

### Phase 3: Deployment
- [ ] Cut release with FAISS removal (e.g., v1.24.5)
- [ ] Update production environments
- [ ] Monitor for any search performance regressions
- [ ] Collect feedback from registry operators

## Open Questions

- Should we add a configuration flag to enable DocumentDB search during transition? [Decision: No - immediate cutover per requirements]
- Do we need migration scripts for any stored FAISS data? [Decision: No - FAISS data is ephemeral]
- Should we update release notes to explain the removal? [Decision: Yes - add to v1.24.5 release notes]

## References

- GitHub Issue #N/A (FAISS removal tracking issue)
- DocumentDB search implementation: `registry/repositories/documentdb/search_repository.py`
- Unified search documentation: `docs/design/database-abstraction-layer.md`
- MongoDB vector search documentation: https://www.mongodb.com/docs/atlas/atlas-search/vector-search