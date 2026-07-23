# Low-Level Design: Remove FAISS from Codebase and Documentation

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

## Overview
### Problem Statement
FAISS is an unnecessary dependency that complicates deployment and maintenance. It has been replaced by DocumentDB native hybrid search in this repository. Removing FAISS will simplify the codebase, reduce deployment complexity, and eliminate potential native library compatibility issues.

### Goals
- Remove all FAISS imports, dependencies, configuration, and references in documentation
- Replace any remaining vector-search needs with the maintained DocumentDB hybrid search alternative
- Ensure existing search functionality continues to work unchanged
- Simplify the codebase for operators and developers

### Non-Goals
- Change the underlying search algorithm or behavior
- Add new search features
- Modify the API contract

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/search/service.py` | FAISS service implementation | Central location where FAISS is imported and used |
| `registry/repositories/file/search_repository.py` | File-based search repository using FAISS | Repository that directly uses FAISS service |
| `registry/repositories/documentdb/search_repository.py` | DocumentDB-based search repository with hybrid search | Replacement that should be used instead of FAISS |
| `registry/api/search_routes.py` | Search API endpoints | Depends on search repository implementation |
| `pyproject.toml` | Python dependencies | Contains FAISS dependency |
| `docker/Dockerfile.registry` | Docker build configuration | Contains FAISS installation steps |
| `docker-compose.yml` | Development environment configuration | Contains FAISS-related environment variables |

### Existing Patterns Identified
1. **Pattern Name**: Dependency Injection with Factory Pattern
   - Files: `registry/repositories/factory.py`, `registry/repositories/interfaces.py`
   - How a future implementer should follow this: Use the factory pattern to switch between FAISS and DocumentDB repositories based on configuration

2. **Pattern Name**: Repository Pattern for Data Access
   - Files: `registry/repositories/file/search_repository.py`, `registry/repositories/documentdb/search_repository.py`
   - How a future implementer should follow this: Implement repository interfaces for different backends (FAISS vs DocumentDB)

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Search Service | Uses FAISS Service | Direct import in `registry/search/service.py` |
| Search Repository | Depends on Storage Backend | Implemented differently for FAISS vs DocumentDB backends |
| API Routes | Calls Search Repository | `registry/api/search_routes.py` uses search repository |
| Docker Build | Installs FAISS | `docker/Dockerfile.registry` installs FAISS package |
| Configuration | Sets Storage Backend | `registry/core/config.py` has `storage_backend` setting |

### Constraints and Limitations Discovered
- FAISS is currently used for all storage backends when `storage_backend` is set to `file` 
- DocumentDB hybrid search is already implemented and working for MongoDB backends
- The repository factory pattern already supports switching between backends
- FAISS is only used for the file-based search repository

## Architecture

### System Context Diagram
```
┌─────────────────────┐
│   MCP Gateway       │
│   Registry App      │
└─────────┬───────────┘
          │
┌─────────▼───────────┐
│   Search API        │
│   (registry/api)    │
└─────────┬───────────┘
          │
┌─────────▼───────────┐
│   Search Repository │
│   (registry/repo)   │
│   ┌───────────────┐ │
│   │ File-Based    │ │
│   │ FAISS         │◄┤
│   └───────────────┘ │
│   ┌───────────────┐ │
│   │ DocumentDB    │ │
│   │ Hybrid Search │◄┤
│   └───────────────┘ │
└─────────┬───────────┘
          │
┌─────────▼───────────┐
│   Storage Backend   │
│   (MongoDB/File)    │
└─────────────────────┘
```

### Sequence Diagram
```
User Request → Search API → Search Repository → Storage Backend
                              ↑
                      FAISS Service (Removed)
```

### Component Diagram
```
┌─────────────────────────────────────────────────────────────┐
│                     Registry App                            │
├─────────────────────────────────────────────────────────────┤
│  Search API (registry/api/search_routes.py)                 │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Search Repository (registry/repositories/factory.py)    │ │
│  │  ┌────────────────────┐  ┌─────────────────────────┐  │ │
│  │  │ File-Based         │  │ DocumentDB Hybrid       │  │ │
│  │  │ FAISS Repository   │◄─┤ Search Repository       │  │ │
│  │  └────────────────────┘  │  (uses DocumentDB)      │  │ │
│  │                         └─────────────────────────┘  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                             │
│  Search Service (registry/search/service.py)                │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ FAISS Service (removed)                                 │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Data Models

### New Models
No new models required for this change.

### Model Changes
No model changes required.

## API / CLI Design

### New Endpoints / Commands
**Description:** No new endpoints or commands required

**Request / Invocation:**
N/A

**Expected Response / Output:**
N/A

**Error Cases:**
N/A

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| None | - | - | - | No new environment variables needed |

### Settings / Config Class Updates
```python
# No changes needed to Settings class
```

### Deployment Surface Checklist
List every surface where this parameter must appear (`.env.example`, `docker-compose.yml`, Terraform vars, Helm values, etc.) so an implementer can tick them off later.
- [ ] `.env.example` - No changes needed
- [ ] `docker-compose.yml` - Remove FAISS-related environment variables  
- [ ] `pyproject.toml` - Remove FAISS dependency
- [ ] `docker/Dockerfile.registry` - Remove FAISS installation steps
- [ ] Documentation files - Update references to FAISS to DocumentDB hybrid search

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `faiss-cpu` | Removed | No longer needed |
| `scikit-learn` | Removed | No longer needed |
| `torch` | Removed | No longer needed |

If no new dependencies are required, explicitly state: "This change uses only existing dependencies."

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Remove FAISS imports and usage from search service
**File:** `registry/search/service.py`
**Lines:** 8, 142

```python
# Remove import
# import faiss

# Remove FAISS service implementation
# class FaissService:
#     ...
```

#### Step 2: Remove FAISS repository implementation
**File:** `registry/repositories/file/search_repository.py`
**Lines:** All lines

```python
# Remove entire file content
# """File-based search repository using FAISS."""
# 
# import logging
# from typing import Any
# 
# from ..interfaces import SearchRepositoryBase
# 
# logger = logging.getLogger(__name__)
# 
# 
# class FaissSearchRepository(SearchRepositoryBase):
#     ...
```

#### Step 3: Update repository factory to always use DocumentDB search repository
**File:** `registry/repositories/factory.py`
**Lines:** 142-149

```python
# Change from:
# if backend in MONGODB_BACKENDS:
#     from .documentdb.search_repository import DocumentDBSearchRepository
#     _search_repo = DocumentDBSearchRepository()
# else:
#     from .file.search_repository import FaissSearchRepository
#     _search_repo = FaissSearchRepository()

# To:
# from .documentdb.search_repository import DocumentDBSearchRepository
# _search_repo = DocumentDBSearchRepository()
```

#### Step 4: Remove FAISS dependencies from pyproject.toml
**File:** `pyproject.toml`
**Lines:** 23, 26, 27

```python
# Remove these lines:
# "faiss-cpu>=1.7.4",
# "scikit-learn>=1.3.0",
# "torch>=1.6.0",
```

#### Step 5: Remove FAISS installation from Dockerfile
**File:** `docker/Dockerfile.registry`
**Lines:** 47 (uv sync --frozen --no-dev)

```python
# Remove the faiss-cpu dependency from pyproject.toml and uv.lock
# No additional changes needed to Dockerfile since uv sync handles dependencies
```

#### Step 6: Remove FAISS-related environment variables from docker-compose.yml
**File:** `docker-compose.yml`
**Lines:** 198-221 (Storage Backend Configuration section)

```python
# Remove any FAISS-related environment variables that were used for configuration
```

#### Step 7: Update documentation references
**File:** Various documentation files
**Purpose:** Replace references to FAISS with DocumentDB hybrid search

#### Step 8: Update tests to remove FAISS references
**File:** Test files in `tests/unit/search/`
**Purpose:** Remove tests that specifically test FAISS functionality

### Error Handling
No specific error handling changes needed. The system will fall back to the DocumentDB implementation.

### Logging
Logging will remain the same - no FAISS-specific logging statements to remove.

## Observability
### Tracing / Metrics / Logging Points
- All existing logging points will remain unchanged
- No FAISS-specific metrics or tracing points to remove
- Search service will continue to log as before using DocumentDB hybrid search

## Scaling Considerations
- Current load assumptions: FAISS was used for file-based storage, which is not the primary deployment mode
- Horizontal scaling: No impact as DocumentDB hybrid search is already scalable
- Bottlenecks: None related to FAISS removal
- Caching strategy: No changes needed as DocumentDB hybrid search handles caching internally

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| None | No new files needed |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/search/service.py` | 8, 142 | Remove FAISS imports and service implementation |
| `registry/repositories/file/search_repository.py` | All lines | Remove entire file |
| `registry/repositories/factory.py` | 142-149 | Update factory to always use DocumentDB search repository |
| `pyproject.toml` | 23, 26, 27 | Remove FAISS, scikit-learn, and torch dependencies |
| `docker/Dockerfile.registry` | 47 | No direct changes needed (uv handles dependencies) |
| `docker-compose.yml` | 198-221 | Remove FAISS-related environment variables |
| `registry/api/search_routes.py` | 319-320 | Remove FAISS-related code paths |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~0 |
| New tests | ~0 |
| Modified code | ~50 |
| **Total** | **~50** |

## Testing Strategy
This change will be tested through:
1. Functional tests to ensure search still works with DocumentDB hybrid search
2. Backwards compatibility tests to ensure existing API contracts are unchanged
3. Deployment surface tests to verify Docker builds without FAISS work correctly
4. E2E tests to ensure full search workflow continues to function

## Alternatives Considered

### Alternative 1: Conditional removal based on storage backend
**Description:** Keep FAISS code but only use it when `storage_backend=file` and switch to DocumentDB for other backends

**Pros / Cons:** 
- Pro: More gradual migration
- Con: Still maintains unnecessary complexity and dependencies

**Why Rejected:** The goal is to completely remove FAISS, not conditionally use it.

### Comparison Matrix

| Criteria | Chosen | Alt 1 |
|----------|--------|-------|
| Complexity | Low | Medium |
| Risk | Low | Medium |
| Maintainability | High | Medium |
| Impact on deployment | Minimal | Minimal |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill)
- Phase 2: Testing (ensure all search functionality works with DocumentDB hybrid search)
- Phase 3: Deployment (update all environments to remove FAISS)

## Open Questions
- Should we also remove the FAISS-related tests?
- Are there any other documentation files that need updating?

## References
- DocumentDB hybrid search implementation in `registry/repositories/documentdb/search_repository.py`
- Repository factory pattern in `registry/repositories/factory.py`
- Storage backend configuration in `registry/core/config.py`