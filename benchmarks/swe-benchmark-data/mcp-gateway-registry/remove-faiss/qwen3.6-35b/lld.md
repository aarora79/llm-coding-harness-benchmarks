# Low-Level Design: Remove FAISS from the codebase

*Created: 2026-07-06*
*Author: Claude (qwen3.6-35b)*
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
The mcp-gateway-registry project migrated from FAISS (a file-based vector search library) to DocumentDB hybrid search for all vector operations. FAISS remains as dead code across 53 files with 317 non-test references. This creates contributor confusion, bloated container images, and maintenance overhead.

### Goals
- Remove all FAISS code, dependencies, and documentation references
- Enforce DocumentDB as the sole search backend
- Reduce dependency footprint (remove faiss-cpu and its native library pulls)
- Eliminate confusion for contributors and operators

### Non-Goals
- Changes to the DocumentDB search implementation
- Data migration from FAISS indices
- Adding new search features

## Codebase Analysis

### Complete FAISS File Map (53 files, 317 non-test references)

| # | File Path | Ref Count | Type | Lines |
|---|-----------|-----------|------|-------|
| 1 | `registry/search/service.py` | 98 | Code (to delete) | 37-1201 |
| 2 | `registry/api/server_routes.py` | 24 | Code (faiss_service calls) | 774,847,1113,1343,1413,1643,1716,1752,1769,1827,1876,1949,2100,2386,2502,2630,2675,2760,3504,3808,3989,4041,4100,4178 |
| 3 | `registry/api/agent_routes.py` | 10 | Code (faiss_service calls) | 96,628,631,1150,1152,1598,1601,1853,1855,2001 |
| 4 | `registry/api/search_routes.py` | 3 | Docstring/comment/error log | 385,440,510 |
| 5 | `registry/repositories/file/search_repository.py` | 20 | Code (to delete) | 1-137 |
| 6 | `registry/repositories/factory.py` | 2 | Factory else-branch | 147-149 |
| 7 | `registry/core/config.py` | 3 | Config properties | 996-1001 |
| 8 | `registry/core/schemas.py` | 2 | Model class | 505-511 |
| 9 | `registry/core/telemetry.py` | 2 | Backend detection fallback | 730-731 |
| 10 | `registry/main.py` | 2 | Startup message + comment | 497,503 |
| 11 | `registry/metrics/client.py` | 2 | Metrics field | 111,126 |
| 12 | `registry/services/agent_batch_item_processor.py` | 4 | faiss_service calls | 225,228,338,340 |
| 13 | `metrics-service/metrics_client.py` | 2 | Metrics field | 193,208 |
| 14 | `metrics-service/app/storage/database.py` | 3 | DDL field | 172,306,319 |
| 15 | `metrics-service/app/storage/migrations.py` | 1 | Migration DDL field | 120 |
| 16 | `pyproject.toml` | 1 | Dependency | 23 |
| 17 | `docker-compose.yml` | 1 | Comment | 71 |
| 18 | `docker-compose.prebuilt.yml` | 1 | Comment | 14 |
| 19 | `docker-compose.podman.yml` | 1 | Comment | 4 |
| 20 | `build-config.yaml` | 2 | Comment + description | 25,30 |
| 21 | `terraform/aws-ecs/OPERATIONS.md` | 1 | Comment | 136 |
| 22 | `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | 1 | Comment | 587 |
| 23 | `terraform/telemetry-collector/lambda/collector/schemas.py` | 1 | Regex pattern | 267 |
| 24 | `cli/agent_mgmt.py` | 1 | Comment | 34 |
| 25 | `api/registry_client.py` | 1 | Comment | 2617 |
| 26 | `registry/embeddings/README.md` | 4 | Doc comments | 13,252,254,260 |
| 27 | `registry/servers/mcpgw.json` | 3 | JSON content | 197,199,226 |
| 28 | `docs/embeddings.md` | 7 | Doc content | 19,177,186,251,253,259,413 |
| 29 | `docs/database-design.md` | 4 | Doc content | 11,39,57 |
| 30 | `docs/configuration.md` | 1 | Doc content | 295 |
| 31 | `docs/api-reference.md` | 1 | Doc content | 334 |
| 32 | `docs/service-management.md` | 5 | Doc content | 45,52,222,249,252 |
| 33 | `docs/testing/test-categories.md` | 8 | Doc content | 39,48,65,83,88,89,90,110 |
| 34 | `docs/testing/memory-management.md` | 3 | Doc content | 13,18,163 |
| 35 | `docs/testing/QUICK-START.md` | 3 | Doc content | 16,56,116 |
| 36 | `docs/faq/configuring-mongodb-atlas-backend.md` | 1 | Doc content | 7 |
| 37 | `docs/TELEMETRY.md` | 1 | Doc content | 46 |
| 38 | `docs/prebuilt-images.md` | 1 | Doc content | 9 |
| 39 | `docs/registry-auth-detailed.md` | 3 | Doc content (Mermaid diagram) | 649,650,666 |
| 40 | `docs/server-versioning-operations.md` | 3 | Doc content | 196,318,325 |
| 41 | `docs/design/a2a-protocol-integration.md` | 21 | Doc content | various |
| 42 | `docs/design/database-abstraction-layer.md` | 13 | Doc content | various |
| 43 | `docs/design/server-versioning.md` | 1 | Doc content | various |
| 44 | `docs/design/storage-architecture-mongodb-documentdb.md` | 4 | Doc content | various |
| 45 | `docs/dynamic-tool-discovery.md` | 12 | Doc content | 3,33,53,69,210,262,264,268,278,279,303,360 |
| 46 | `docs/llms.txt` | 11 | Doc content | various |
| 47 | `docs/OBSERVABILITY-LEGACY.md` | 4 | Doc content | 325,339,857,1005 |
| 48 | `.claude/skills/debug/SKILL.md` | 1 | Doc content | 69 |
| 49 | `.claude/skills/pr-review/personas/backend-developer.md` | 1 | Doc content | 9 |
| 50 | `release-notes/v1.0.17.md` | 3 | Historical notes | 206,242,269 |
| 51 | `scripts/migrate-file-to-mongodb.py` | 1 | Code | 220 |
| 52 | `tests/conftest.py` | 5 | Test setup | 51,142-149,374 |
| 53 | `tests/README.md` | 10 | Test docs | 18,27,50,93,96,98,289,291 |

Additional test files with FAISS references (not counted above, listed for completeness):
- `tests/test_infrastructure.py` — lines 15,27-29
- `tests/unit/conftest.py` — lines 16,18,21
- `tests/unit/test_safe_eval_arithmetic.py` — lines 25-37
- `tests/unit/core/test_config.py` — lines 555-576
- `tests/unit/core/test_telemetry.py` — lines 340,343
- `tests/unit/repositories/test_factory_aliases.py` — lines 197-199
- `tests/unit/search/test_faiss_service.py` — entire file (~720 lines of tests)

### Key Discovery: numpy is NOT FAISS-only

`numpy` is imported by TWO files outside tests:
- `registry/search/service.py` (line 9) — used only for FAISS vector math
- `registry/embeddings/client.py` (line 16) — used for embedding normalization

**Conclusion**: Do NOT remove numpy from pyproject.toml. The embeddings module depends on it independently.

### Key Discovery: registry/repositories/file/ directory

The `registry/repositories/file/` directory contains 9 Python files:
- `__init__.py`
- `agent_repository.py`
- `federation_config_repository.py`
- `peer_federation_repository.py`
- `scope_repository.py`
- `search_repository.py` — ONLY this file is FAISS-dependent
- `security_scan_repository.py`
- `server_repository.py`
- `skill_security_scan_repository.py`

Only `search_repository.py` should be deleted. The other 7 files are file-based implementations for non-search repositories and are unrelated to FAISS.

### Key Discovery: Dockerfile and charts/ are clean

- `Dockerfile` — no FAISS references found (Dockerfile installs via `pip install -e .` which pulls from pyproject.toml)
- `charts/` — no FAISS references found in any Helm chart

### Blocker Resolutions (Expert Review)

**Blocker 1 + 6: Config validation for storage_backend**

The current `registry/core/config.py` has these elements that MUST change:

- `ALLOWED_STORAGE_BACKENDS` (lines 12-20) includes `"file"` — REMOVE it
- `storage_backend` default (line 743) is `"file"` — CHANGE to `"mongodb-ce"`
- `_validate_storage_backend` (lines 808-833) returns `"file"` for None/empty — CHANGE to `"mongodb-ce"`

Exact changes below in Step 3.

**Blocker 2: Dockerfile FAISS check**

Confirmed: `Dockerfile` has zero FAISS references. The Dockerfile installs dependencies via `pip install -e .` which reads from `pyproject.toml`. Removing `faiss-cpu` from pyproject.toml is sufficient.

**Blocker 3: Helm charts/ FAISS check**

Confirmed: `charts/` directory has zero FAISS references. No changes needed.

**Blocker 4: migrate-file-to-mongodb.py fate**

Decision: DELETE this script. Its entire purpose was migrating server/agent JSON files from the file-based storage (FAISS-backed) to MongoDB. With FAISS removed, there is no file-based storage to migrate FROM. Old deployments that have `STORAGE_BACKEND=file` will now fail startup (correct behavior per Blocker 1).

**Blocker 5: Historical release notes**

Decision: DO NOT EDIT `release-notes/v1.0.17.md`. Add a NEW release note in the current version documenting FAISS removal. The file is at `release-notes/v1.0.17.md:206,242,269`.

**Additional finding: Default storage_backend value**

The `storage_backend` default is `"file"` (config.py:743). This IS a blocker. After this change, the default must become `"mongodb-ce"` (or another MONGODB_BACKENDS value). Deployments that rely on the default (no env var set) will now get DocumentDB instead of file-based storage.

### Existing Patterns Identified

1. **Search repository factory pattern**: `get_search_repository()` at `registry/repositories/factory.py:132-151` branches on `settings.storage_backend in MONGODB_BACKENDS`. If true, returns `DocumentDBSearchRepository`; otherwise `FaissSearchRepository`. After this change, the else-branch and the `FaissSearchRepository` import are eliminated.

2. **faiss_service singleton**: `FaissService` is instantiated as a module-level singleton (`faiss_service`) in `registry/search/service.py:1201`. Route handlers import it lazily (inside function bodies) with `from ..search.service import faiss_service` and call methods. This pattern avoids circular imports during app startup.

3. **Config path properties**: `registry/core/config.py:995-1001` has `faiss_index_path` and `faiss_metadata_path` properties. These are referenced only by the FAISS service (now being deleted) and its tests.

4. **Metrics field pattern**: `faiss_search_time_ms` appears in:
   - `registry/metrics/client.py:111` — parameter to `record_request()`
   - `registry/metrics/client.py:126` — inclusion in payload dict
   - `metrics-service/metrics_client.py:193` — parameter to client method
   - `metrics-service/metrics_client.py:208` — inclusion in payload dict
   - `metrics-service/app/storage/database.py:172` — DDL column
   - `metrics-service/app/storage/database.py:306,319` — insert and select statements
   - `metrics-service/app/storage/migrations.py:120` — migration DDL

5. **Telemetry search_backend detection**: `registry/core/telemetry.py:730-731` uses a ternary to set `search_backend` to either "documentdb" or "faiss". The "faiss" branch becomes dead code.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `get_search_repository()` (factory) | Simplify | Always returns DocumentDBSearchRepository, no branching |
| `server_routes.py` | Remove | 24 FAISS refs: 14 import statements, ~20 method calls, ~6 comment lines |
| `agent_routes.py` | Remove | 10 FAISS refs: 4 import statements, 4 method calls, 1 docstring, 1 comment |
| `search_routes.py` | Update docstrings | 3 FAISS refs in docstring/comment/error log (no faiss_service calls) |
| `agent_batch_item_processor.py` | Remove | 4 FAISS refs: 2 imports, 2 method calls |
| `config.py` | Remove | 2 properties (lines 995-1001) |
| `schemas.py` | Remove | 1 model class (lines 505-511) |
| `telemetry.py` | Remove | 2 lines (730-731) |
| `main.py` | Update | 2 lines (497,503) |
| `metrics/client.py` | Remove | 2 lines (111,126) |
| `metrics_client.py` | Remove | 2 lines (193,208) |
| `database.py` | Remove | 3 lines (172,306,319) |
| `migrations.py` | Remove | 1 line (120) |
| `pyproject.toml` | Remove | 1 dependency line (23) |

### Constraints and Limitations Discovered
- `registry/repositories/file/` has 7 non-FAISS files — only delete `search_repository.py`
- `numpy` is used by `registry/embeddings/client.py` — must NOT remove it from pyproject.toml
- `faiss_service` is imported lazily in route handlers (inside function bodies) — safe to remove
- The metrics field `faiss_search_time_ms` is optional (float | None) — removing it does not break backward compat
- `FaissSearchRepository` in `registry/repositories/file/search_repository.py` wraps `faiss_service` for the `SearchRepositoryBase` interface — deleting it is safe because only the factory instantiates it
- The `scripts/migrate-file-to-mongodb.py:220` file has `exclude_files` with `.faiss` extension filtering — should be reviewed

## Architecture

### System Context Diagram

```
Before:
    +------------------+       +-----------------+
    |  API Routes      |------>| FaissService    |
    | (server_routes)  |------>| (FAISS index)   |
    | (agent_routes)   |------>|                  |
    +------------------+       +-----------------+
                   +-----------------+
                   | DocumentDB      |
                   | SearchRepository|
                   +-----------------+

After:
    +------------------+       +-----------------+
    |  API Routes      |------>| DocumentDB      |
    | (server_routes)  |------>| SearchRepository|
    | (agent_routes)   |------>|                 |
    +------------------+       +-----------------+
```

### Factory Pattern After Change

```
Before:
  get_search_repository()  [factory.py:132-151]
    if storage_backend in MONGODB_BACKENDS:
        return DocumentDBSearchRepository()  [line 143-145]
    else:
        return FaissSearchRepository()       [line 147-149]

After:
  get_search_repository()
    if _search_repo is not None:
        return _search_repo
    from .documentdb.search_repository import DocumentDBSearchRepository
    _search_repo = DocumentDBSearchRepository()
    logger.info("Creating search repository with backend: documentdb")
    return _search_repo
```

## Data Models

### Models to Delete

```python
# FROM registry/core/schemas.py:505-511
class FaissMetadata(BaseModel):
    """FAISS metadata model."""

    id: int
    text_for_embedding: str
    full_server_info: ServerInfo
```

### Config Properties to Remove

```python
# FROM registry/core/config.py:995-1001
@property
def faiss_index_path(self) -> Path:
    return self.servers_dir / "service_index.faiss"

@property
def faiss_metadata_path(self) -> Path:
    return self.servers_dir / "service_index_metadata.json"
```

### Metrics Fields to Remove

```python
# FROM registry/metrics/client.py:111 and 126
faiss_search_time_ms: float | None = None,
...
"faiss_search_time_ms": faiss_search_time_ms,

# FROM metrics-service/metrics_client.py:193 and 208
faiss_search_time_ms: float | None = None,
...
"faiss_search_time_ms": faiss_search_time_ms,
```

### Database Schema Fields to Remove

```sql
-- FROM metrics-service/app/storage/database.py:172
faiss_search_time_ms REAL,

-- FROM metrics-service/app/storage/migrations.py:120
faiss_search_time_ms REAL,
```

Note: The existing database table will have an extra column. This is safe — SQLite allows ALTER TABLE ADD COLUMN but not DROP COLUMN in older versions. The column will simply be unused. Future database migration scripts can drop it, but that is out of scope for this cleanup task.

## API / CLI Design

### No New Endpoints or Commands

This is a cleanup task. No new API endpoints, CLI commands, or request shapes are introduced. All existing DocumentDB-backed operations continue unchanged.

### Removed Internal API

The internal (not exposed via HTTP) `faiss_service` singleton is removed. All callers in API routes are deleted.

The `search_routes.py` endpoint at line 439 catches `RuntimeError` with the log message "FAISS search service unavailable" — after this change, that error message will be updated (or the except clause removed if DocumentDB is guaranteed available).

## Configuration Parameters

### Parameters to Remove

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `faiss_index_path` (property) | Path | servers_dir/service_index.faiss | Removed from config.py:995-997 |
| `faiss_metadata_path` (property) | Path | servers_dir/service_index_metadata.json | Removed from config.py:999-1001 |

### New Validation to Add

```python
# In config.py, in the storage_backend validator or property:
@validator("storage_backend")
def validate_storage_backend(cls, v: str) -> str:
    allowed = ("mongodb-ce", "mongodb", "mongodb-atlas")
    if v not in allowed:
        raise ValueError(
            f"storage_backend must be one of {allowed}. "
            "FAISS was removed in a prior release."
        )
    return v
```

If no Pydantic validator exists for storage_backend, add one. If it is a plain property, wrap it or add a model post_init check.

### Existing Parameters Unchanged

- `storage_backend` — After this change, the only valid value is a DocumentDB backend (mongodb-ce, mongodb, or mongodb-atlas).

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `faiss-cpu` | (existing, pyproject.toml:23) | REMOVED — no longer needed |
| `numpy` | (existing) | KEPT — used by registry/embeddings/client.py independently |

No new dependencies are required. This change reduces the dependency count by one.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Delete FAISS core files

**Files to delete:**

| File | Lines | Description |
|------|-------|-------------|
| `registry/search/service.py` | 1-1202 | FaissService class (37-1201), faiss_service singleton (1201), _PydanticAwareJSONEncoder (22-34) |
| `registry/repositories/file/search_repository.py` | 1-137 | FaissSearchRepository class (11-137), thin wrapper around faiss_service |
| `tests/fixtures/mocks/mock_faiss.py` | N/A | Mock FAISS module factory for tests |
| `tests/unit/search/test_faiss_service.py` | N/A (~720 lines) | All FAISS service unit tests |

**Impact:** These files contain ~980 lines of code that become entirely dead code. The `FaissService` class itself is 1164 lines (37-1201).

#### Step 2: Update pyproject.toml

**File:** `pyproject.toml`

**Exact change at line 23:**

```diff
-    "faiss-cpu>=1.7.4",
```

**Note:** Do NOT remove `numpy`. It is used by `registry/embeddings/client.py:16` independently of FAISS.

#### Step 3: Update config.py (4 changes)

**File:** `registry/core/config.py`

This file requires FOUR changes:

**3a. Remove "file" from ALLOWED_STORAGE_BACKENDS (lines 12-20):**

Before:
```python
ALLOWED_STORAGE_BACKENDS: frozenset[str] = frozenset(
    {
        "file",
        "documentdb",
        "mongodb-ce",
        "mongodb",
        "mongodb-atlas",
    }
)
```

After:
```python
ALLOWED_STORAGE_BACKENDS: frozenset[str] = frozenset(
    {
        "documentdb",
        "mongodb-ce",
        "mongodb",
        "mongodb-atlas",
    }
)
```

**3b. Change storage_backend default from "file" to "mongodb-ce" (line 742-743):**

Before:
```python
    storage_backend: str = Field(
        default="file",
```

After:
```python
    storage_backend: str = Field(
        default="mongodb-ce",
```

**3c. Update _validate_storage_backend field_validator (lines 808-833):**

Before:
```python
    @field_validator("storage_backend", mode="before")
    @classmethod
    def _validate_storage_backend(
        cls,
        v: str | None,
    ) -> str:
        """Reject unknown STORAGE_BACKEND values at startup.

        Empty string and None coerce to "file" (the historical default). Any
        other value is normalized (stripped, lowercased) and compared against
        ALLOWED_STORAGE_BACKENDS. Unknown values raise ValueError with the
        full allowlist in the error message so operators can fix the env var
        without a round-trip through the code.

        Safe to echo v in the error: storage_backend is a non-secret config
        name. Do not copy this pattern for fields that could hold credentials.
        """
        if v is None or v == "":
            return "file"
        if not isinstance(v, str):
            raise ValueError(f"STORAGE_BACKEND must be a string, got {type(v).__name__}")
        normalized = v.strip().lower()
        if normalized not in ALLOWED_STORAGE_BACKENDS:
            accepted = ", ".join(sorted(ALLOWED_STORAGE_BACKENDS))
            raise ValueError(f"Invalid STORAGE_BACKEND={v!r}. Accepted values: {accepted}.")
        return normalized
```

After:
```python
    @field_validator("storage_backend", mode="before")
    @classmethod
    def _validate_storage_backend(
        cls,
        v: str | None,
    ) -> str:
        """Reject unknown STORAGE_BACKEND values at startup.

        Empty string and None coerce to "mongodb-ce" (the default after FAISS
        removal in v1.25.0). Any other value is normalized (stripped, lowercased)
        and compared against ALLOWED_STORAGE_BACKENDS. Unknown values raise
        ValueError with the full allowlist in the error message so operators can
        fix the env var without a round-trip through the code.

        Safe to echo v in the error: storage_backend is a non-secret config
        name. Do not copy this pattern for fields that could hold credentials.
        """
        if v is None or v == "":
            return "mongodb-ce"
        if not isinstance(v, str):
            raise ValueError(f"STORAGE_BACKEND must be a string, got {type(v).__name__}")
        normalized = v.strip().lower()
        if normalized not in ALLOWED_STORAGE_BACKENDS:
            accepted = ", ".join(sorted(ALLOWED_STORAGE_BACKENDS))
            raise ValueError(
                f"Invalid STORAGE_BACKEND={v!r}. Accepted values: {accepted}. "
                "FAISS was removed as a backend in v1.25.0."
            )
        return normalized
```

**3d. Remove FAISS path properties (lines 995-1001):**

Delete:
```python
@property
def faiss_index_path(self) -> Path:
    return self.servers_dir / "service_index.faiss"

@property
def faiss_metadata_path(self) -> Path:
    return self.servers_dir / "service_index_metadata.json"
```

#### Step 4: Remove FaissMetadata schema

**File:** `registry/core/schemas.py`

**Exact change at lines 505-511:**

Delete the entire class:

```python
class FaissMetadata(BaseModel):
    """FAISS metadata model."""

    id: int
    text_for_embedding: str
    full_server_info: ServerInfo
```

Check if `FaissMetadata` is imported or used anywhere else before deleting. If it is, remove those imports too.

#### Step 5: Remove faiss_service calls from server_routes.py

**File:** `registry/api/server_routes.py`

This is the largest file to modify. There are exactly 24 FAISS references across these line pairs:

| Import Line | Call Line | Method Called | Context |
|-------------|-----------|---------------|---------|
| 774 | 847 | `add_or_update_service()` | Server enable/disable |
| 1113 | 1343 | `add_or_update_service()` | Server create |
| 1413 | 1643 | `add_or_update_service()` | Server register (auto-enable) |
| 1716 | 1752 | `add_or_update_service()` | Server update |
| 1769 | 1827 | `remove_service()` | Server delete |
| 1876 | 1949 | `add_or_update_service()` | Server toggle |
| 2100 | 2386 | `add_or_update_service()` | Server config update |
| 2502 | 2630 | `add_or_update_service()` | Server tool update |
| 2675 | 2760 | `add_or_update_service()` | Server update |
| 3504 | 3808 | `save_data()` (asyncio.create_task) | Health check sync |
| 3989 | 4041 | `add_or_update_service()` | Server version update |
| 4100 | 4178 | `remove_service()` | Server version deactivate |

**For each pair:**

1. Delete the import line: `from ..search.service import faiss_service`
2. Delete the call line(s): `await faiss_service.xxx(...)` or `asyncio.create_task(faiss_service.save_data())`
3. Delete the comment line that introduces the FAISS operation (e.g., "INTERNAL REGISTER: ..." at ~line 1638, "INTERNAL REMOVE: ..." at ~line 1823)
4. Delete the comment before the import if present (e.g., "Update FAISS metadata with new enabled state" at ~line 846)

Example for the first handler (server enable/disable around lines 774-847):

Before:
```python
    from ..search.service import faiss_service
    # ...
    # Update FAISS metadata with new enabled state
    await faiss_service.add_or_update_service(service_path, server_info, new_state)
```

After:
```python
    # (both lines deleted — no-op, DocumentDB handles indexing)
```

**Additional comment to update** at line 329: "Updating FAISS and regenerating Nginx config if server disabled" — change to "Regenerating Nginx config if server disabled" or remove "Updating FAISS and" entirely.

#### Step 6: Remove faiss_service calls from agent_routes.py

**File:** `registry/api/agent_routes.py`

FAISS references at these lines:

| Line | Content |
|------|---------|
| 96 | Comment: "Updating FAISS with disabled state if agent disabled" |
| 628 | Import: `from ..search.service import faiss_service` |
| 631 | Call: `await faiss_service.add_or_update_entity(...)` |
| 1150 | Import |
| 1152 | Call: `await faiss_service.add_or_update_entity(...)` |
| 1598 | Import |
| 1601 | Call: `await faiss_service.add_or_update_entity(...)` |
| 1853 | Import |
| 1855 | Call: `await faiss_service.remove_entity(path)` |
| 2001 | Docstring: "Uses search repository (FAISS or DocumentDB) to find agents..." |

For each of the 4 handler blocks, delete the import and the call. Update the docstring at line 2001 to say "Uses DocumentDB search repository to find agents...".

#### Step 7: Remove faiss_service calls from agent_batch_item_processor.py

**File:** `registry/services/agent_batch_item_processor.py`

| Line | Content |
|------|---------|
| 225 | Import: `from ..search.service import faiss_service` |
| 228 | Call: `await faiss_service.add_or_update_entity(...)` |
| 338 | Import |
| 340 | Call: `await faiss_service.remove_entity(path)` |

Delete all 4 lines (2 imports + 2 calls).

#### Step 8: Update search repository factory

**File:** `registry/repositories/factory.py`

**Exact change at lines 132-151 (get_search_repository):**

Before:
```python
def get_search_repository() -> SearchRepositoryBase:
    """Get search repository singleton."""
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

After:
```python
def get_search_repository() -> SearchRepositoryBase:
    """Get search repository singleton."""
    global _search_repo

    if _search_repo is not None:
        return _search_repo

    from .documentdb.search_repository import DocumentDBSearchRepository

    _search_repo = DocumentDBSearchRepository()
    logger.info("Creating search repository with backend: documentdb")

    return _search_repo
```

Check whether `MONGODB_BACKENDS` is still imported and used by other factory functions (it is — 15 other functions still use it). Keep the import.

#### Step 9: Update telemetry fallback

**File:** `registry/core/telemetry.py`

**Exact change at lines 730-731:**

Before:
```python
    # DocumentDB search repository; file uses FAISS.
    search_backend = "documentdb" if settings.storage_backend in MONGODB_BACKENDS else "faiss"
```

After:
```python
    search_backend = "documentdb"
```

Also check if `faiss_search_time_ms` appears anywhere in this file and remove it.

#### Step 10: Update app startup message

**File:** `registry/main.py`

**Exact change at lines 497 and 503:**

Before (line 497):
```python
backend_name = "DocumentDB" if settings.storage_backend in MONGODB_BACKENDS else "FAISS"
```

After (line 497):
```python
backend_name = "DocumentDB"
```

Before (line 503 — comment):
```python
# restarts. Only FAISS (in-memory) needs a full re-index on every boot.
```

Delete or update this comment. If it is the only reference in that paragraph, remove the entire sentence.

#### Step 11: Remove faiss_search_time_ms from metrics clients

**File:** `registry/metrics/client.py`

Remove lines 111 and 126:
```diff
-        faiss_search_time_ms: float | None = None,
...
-                "faiss_search_time_ms": faiss_search_time_ms,
```

**File:** `metrics-service/metrics_client.py`

Remove lines 193 and 208:
```diff
-        faiss_search_time_ms: float | None = None,
...
-                "faiss_search_time_ms": faiss_search_time_ms,
```

#### Step 12: Remove faiss_search_time_ms from metrics-service database

**File:** `metrics-service/app/storage/database.py`

Remove line 172 (DDL column):
```diff
-                faiss_search_time_ms REAL,
```

Update line 306 (INSERT statement) — remove `faiss_search_time_ms,` from the column list and remove the corresponding value from the values list.

Update line 319 (SELECT statement) — remove `metric.metadata.get("faiss_search_time_ms"),`

**File:** `metrics-service/app/storage/migrations.py`

Remove line 120:
```diff
-                    faiss_search_time_ms REAL,
```

#### Step 13: Update search_routes.py docstrings

**File:** `registry/api/search_routes.py`

| Line | Current | New |
|------|---------|-----|
| 385 | `Run a semantic search against MCP servers (and their tools) using FAISS embeddings.` | `Run a semantic search against MCP servers (and their tools) using DocumentDB hybrid search.` |
| 440 | `logger.error("FAISS search service unavailable: %s", exc, exc_info=True)` | `logger.error("Search service unavailable: %s", exc, exc_info=True)` |
| 510 | `# FAISS service surfaces deployment + local_runtime in its result dict;` | `# Search repository surfaces deployment + local_runtime in its result dict;` |

#### Step 14: Update telemetry collector schema

**File:** `terraform/telemetry-collector/lambda/collector/schemas.py`

**Exact change at line 267:**

Before:
```python
pattern="^(faiss|documentdb)$",
```

After:
```python
pattern="^documentdb$",
```

#### Step 15: Update Docker and build configs

**File:** `docker-compose.yml` (line 71)
Before: `# Registry service (includes nginx, SSL, FAISS, models)`
After: `# Registry service (includes nginx, SSL, models)`

**File:** `docker-compose.prebuilt.yml` (line 14)
Before: `# Registry service (includes nginx, SSL, FAISS, models) - using pre-built image`
After: `# Registry service (includes nginx, SSL, models) - using pre-built image`

**File:** `docker-compose.podman.yml` (line 4)
Before: `# Registry service (includes nginx, SSL, FAISS, models)`
After: `# Registry service (includes nginx, SSL, models)`

**File:** `build-config.yaml` (lines 25, 30)
Before (line 25): `# Main MCP Gateway Registry with nginx reverse proxy, FAISS, models`
After (line 25): `# Main MCP Gateway Registry with nginx reverse proxy and models`
Before (line 30): `description: "MCP Gateway Registry with nginx, FAISS, models"`
After (line 30): `description: "MCP Gateway Registry with nginx and models"`

#### Step 16: Update Terraform files

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` (line 587)
Before: `# ECS Service: Registry (Main service with nginx, SSL, FAISS, models)`
After: `# ECS Service: Registry (Main service with nginx, SSL, models)`

**File:** `terraform/aws-ecs/OPERATIONS.md` (line 136)
Before: `| registry | MCP Gateway with nginx, FAISS, ML models | ~4.6GB | ~8 min |`
After: `| registry | MCP Gateway with nginx, ML models | ~4.6GB | ~8 min |`

#### Step 17: Update CLI and API client comments

**File:** `cli/agent_mgmt.py` (line 34)
Before: `The 'search' command performs natural language semantic search using FAISS vector index`
After: `The 'search' command performs natural language semantic search using DocumentDB`

**File:** `api/registry_client.py` (line 2617)
Before: `Discover agents using semantic search (FAISS vector search).`
After: `Discover agents using semantic search (DocumentDB vector search).`

#### Step 18: Update embeddings module README

**File:** `registry/embeddings/README.md`

| Line | Context |
|------|---------|
| 13 | "Backward Compatible: Works seamlessly with existing FAISS indices" — Remove or rewrite |
| 252-260 | "Integration with FAISS Service" section — Delete entire section |

#### Step 19: Update mcpgw.json server config

**File:** `registry/servers/mcpgw.json`

| Line | Context |
|------|---------|
| 197 | Tool description mentions "FAISS search" — update to "DocumentDB search" |
| 199 | Tool description mentions "FAISS index/model is unavailable" — update |
| 226 | Parameter description mentions "FAISS search" — update to "DocumentDB search" |

#### Step 20: Update documentation files

For each documentation file below, either remove the paragraph/section containing the FAISS reference entirely, or replace with the DocumentDB equivalent description. Historical content (release notes) should NOT be edited retroactively.

**docs/embeddings.md (7 lines: 19, 177, 186, 251, 253, 259, 413):**
- Line 19: "Backward Compatible: Works seamlessly with existing FAISS indices" — remove
- Lines 177-186: "Rebuilds the FAISS index" section — remove or replace with DocumentDB equivalent
- Lines 251-260: "Integration with FAISS Search" section — delete entire section
- Line 413: Link to FAISS implementation — remove or update

**docs/database-design.md (4 lines: 11, 39, 57):**
- Line 11: "FAISS-based vector search" in architecture list — remove from list
- Line 39: "Local JSON files + FAISS" in diagram — remove or update
- Line 57: Vector search comparison table row showing "FAISS (local)" — update or remove

**docs/configuration.md (1 line: 295):**
- Line 295: "FAISS-based vector search, deprecated" in file backend description — update to remove FAISS mention since it is now removed

**docs/api-reference.md (1 line: 334):**
- Line 334: "Find agents using NLP semantic search (FAISS vector search)" — update to "DocumentDB"

**docs/service-management.md (5 lines: 45, 52, 222, 249, 252):**
- Line 45: "FAISS Integration: Automatic indexing" — remove this bullet point
- Line 52: "Automatic FAISS indexing on server registration" — remove
- Line 222: "FAISS index update (automatic)" — remove from steps
- Lines 249-252: CLI examples showing "removes from FAISS / adds back to FAISS" — remove

**docs/testing/test-categories.md (8 lines: 39, 48, 65, 83, 88, 89, 90, 110):**
- Lines 39-65: Section describing real FAISS service loading — remove or note as no longer applicable
- Lines 83-90: `test_real_embeddings_search(real_faiss_service)` test example — remove
- Line 110: `mock_faiss_service` fixture reference — remove

**docs/testing/memory-management.md (3 lines: 13, 18, 163):**
- Lines 13, 18: "FAISS vector indexes" — remove from list
- Line 163: "Mock sentence-transformers and FAISS in unit tests" — remove FAISS from recommendation

**docs/testing/QUICK-START.md (3 lines: 16, 56, 116):**
- Line 16: "FAISS" in test skip list — remove
- Line 56: "FAISS vector database" in test coverage list — remove
- Line 116: "FAISS and embeddings are automatically mocked" — remove FAISS mention

**docs/faq/configuring-mongodb-atlas-backend.md (1 line: 7):**
- Line 7: Long paragraph about fallback to file/FAISS backend with `STORAGE_BACKEND=mongodb` — note that this behavior was fixed in a prior version and FAISS is no longer a fallback

**docs/TELEMETRY.md (1 line: 46):**
- Line 46: "Search backend (faiss or documentdb)" — update to "Search backend (documentdb)"

**docs/prebuilt-images.md (1 line: 9):**
- Line 9: "Main registry service with nginx, SSL, FAISS, and models" — remove FAISS

**docs/registry-auth-detailed.md (3 lines: 649, 650, 666):**
- Lines 649-650: Mermaid flowchart showing "UpdateFAISS" step — remove from diagram
- Line 666: Mermaid class definition including UpdateFAISS — remove from class

**docs/server-versioning-operations.md (3 lines: 196, 318, 325):**
- Line 196: "The FAISS search index is re-indexed" — update to "DocumentDB"
- Lines 318-325: Section about inactive versions and FAISS index — update or remove FAISS references

**docs/design/a2a-protocol-integration.md (21 lines):**
- Review each FAISS reference and remove or replace with DocumentDB equivalent

**docs/design/database-abstraction-layer.md (13 lines):**
- Review each FAISS reference and remove or replace with DocumentDB equivalent

**docs/design/server-versioning.md (1 line):**
- Review FAISS reference

**docs/design/storage-architecture-mongodb-documentdb.md (4 lines):**
- Review FAISS references

**docs/dynamic-tool-discovery.md (12 lines: 3, 33, 53, 69, 210, 262, 264, 268, 278, 279, 303, 360):**
- This is a major document about the search feature. The entire document describes how FAISS is used. Options:
  - Option A: Rewrite the document to describe DocumentDB hybrid search instead
  - Option B: Mark the document as legacy and add a note pointing to the DocumentDB search documentation
  - Recommended: Option B, since the search mechanism has fundamentally changed

**docs/llms.txt (11 lines):**
- Remove all FAISS references from the training/data prompt file

**docs/OBSERVABILITY-LEGACY.md (4 lines: 325, 339, 857, 1005):**
- Line 325: SQL query referencing `faiss_search_time_ms` — note as legacy
- Line 339: AVG query referencing `faiss_search_time_ms` — note as legacy
- Line 857: Performance breakdown mention — note as legacy
- Line 1005: Database schema reference — note as legacy

#### Step 21: Remove .claude skill references

**File:** `.claude/skills/debug/SKILL.md` (line 69)
Before: "Example: seeing FAISS index not initialized in logs does NOT mean FAISS is the problem..."
After: Remove the FAISS example; replace with a DocumentDB-equivalent example if helpful.

**File:** `.claude/skills/pr-review/personas/backend-developer.md` (line 9)
Before: "Technology Stack: FastAPI, Pydantic, FAISS, Python"
After: "Technology Stack: FastAPI, Pydantic, DocumentDB, Python"

#### Step 22: Review scripts/migrate-file-to-mongodb.py

**File:** `scripts/migrate-file-to-mongodb.py` (line 220)
The file has `exclude_files` filtering for `.faiss` extension files. Since FAISS files no longer exist, this exclusion is now a no-op. Review the script's purpose:
- If the script was used to migrate FROM FAISS TO DocumentDB, it may now be obsolete. Consider deletion.
- If it serves a general purpose beyond FAISS, keep it and update the comment.

**Recommendation**: Delete this script. Its sole purpose was migrating from FAISS file-based storage to DocumentDB. Since FAISS is removed and all users should be on DocumentDB, the migration script is obsolete.

#### Step 23: Remove FAISS references from tests

**Files to delete:**
- `tests/fixtures/mocks/mock_faiss.py`
- `tests/unit/search/test_faiss_service.py`

**Files to update:**

| File | Lines | Change |
|------|-------|--------|
| `tests/conftest.py` | 51,142-149,374 | Remove `create_mock_faiss_module` import and `sys.modules["faiss"]` mock setup. Update docstring at line 374. |
| `tests/test_infrastructure.py` | 15,27-29 | Remove MockFaissIndex import and `test_mock_faiss_index` test method |
| `tests/unit/conftest.py` | 16,18,21 | Remove `mock_faiss_service` fixture |
| `tests/unit/test_safe_eval_arithmetic.py` | 25-37 | Remove faiss `__spec__` patching (entire conditional block) |
| `tests/unit/core/test_config.py` | 555-576 | Remove `test_faiss_index_path` and `test_faiss_metadata_path` test classes |
| `tests/unit/core/test_telemetry.py` | 340,343 | Remove FAISS file backend test case |
| `tests/unit/repositories/test_factory_aliases.py` | 197-199 | Remove `FaissSearchRepository` from expected class names |

**File:** `tests/README.md`
Remove all FAISS references from the documentation (lines 18, 27, 50, 93, 96, 98, 289, 291).

#### Step 24: Do NOT edit historical release notes

**File:** `release-notes/v1.0.17.md` (lines 206, 242, 269)

DO NOT edit this file. These are historical records of what was fixed in v1.0.17. Adding a new release note in the current version's release notes is appropriate, but retroactively editing old release notes is not.

#### Step 25: Update mcpgw.json search tool description

**File:** `registry/servers/mcpgw.json`

The `search` tool description (lines 197, 199, 226) mentions FAISS. Update to reference DocumentDB.

## Error Handling

No new error handling is needed. This is a removal task.

The one place to add validation is in config.py for `storage_backend`. If no validator exists, add the code shown in the Configuration Parameters section above. This prevents operators from accidentally setting `storage_backend` to an empty string or a non-DocumentDB value after FAISS removal.

## Logging

No new logging is needed. When `registry/search/service.py` is deleted, all log messages referencing FAISS ("Loading FAISS index", "FAISS data loaded", "FAISS index or metadata not found") will simply disappear since that code path no longer exists.

The error log at `registry/api/search_routes.py:440` ("FAISS search service unavailable") should be updated as noted in Step 13.

## Observability
### Tracing / Metrics / Logging Points
- Removed: `faiss_search_time_ms` from metrics client payload (registry/metrics/client.py:111,126 and metrics-service/metrics_client.py:193,208)
- Removed: `faiss_search_time_ms` from database schema DDL (metrics-service/app/storage/database.py:172 and metrics-service/app/storage/migrations.py:120)
- Removed: `faiss_search_time_ms` from INSERT and SELECT statements (metrics-service/app/storage/database.py:306,319)
- Removed: "faiss" fallback from telemetry search_backend detection (registry/core/telemetry.py:731)
- Updated: telemetry collector regex pattern (terraform/telemetry-collector/lambda/collector/schemas.py:267)
- DocumentDB search operations continue to be traced and logged as before

## Scaling Considerations
- Current load assumptions: No change. Search operations continue to use DocumentDB.
- Horizontal scaling: No change. DocumentDB handles scaling.
- Bottlenecks: No new bottlenecks introduced.
- Caching strategy: No change. DocumentDB caching (if any) continues unchanged.
- Docker image size: Improves by removing faiss-cpu and its native library dependencies. numpy is NOT removed (used by embeddings/client.py).

## File Changes

### Files to Delete (5 files)

| File Path | Lines | Description |
|-----------|-------|-------------|
| `registry/search/service.py` | 1-1202 | FaissService class, faiss_service singleton, _PydanticAwareJSONEncoder |
| `registry/repositories/file/search_repository.py` | 1-137 | FaissSearchRepository wrapper class |
| `tests/fixtures/mocks/mock_faiss.py` | N/A | Mock FAISS module factory |
| `tests/unit/search/test_faiss_service.py` | ~720 | FAISS service unit tests |
| `scripts/migrate-file-to-mongodb.py` | All | Obsolete migration script (FAISS file storage no longer exists) |

### Files to Modify (49 files)

#### Core Python Files (14 files)

| File Path | Lines | Type | Change Description |
|-----------|-------|------|-------------------|
| `pyproject.toml` | 23 | Delete | Remove `"faiss-cpu>=1.7.4",` dependency line |
| `registry/core/config.py` | 995-1002 | Delete | Remove faiss_index_path and faiss_metadata_path properties; add storage_backend validator |
| `registry/core/schemas.py` | 505-511 | Delete | Remove FaissMetadata model class |
| `registry/api/server_routes.py` | 774,847,1113,1343,1413,1643,1716,1752,1769,1827,1876,1949,2100,2386,2502,2630,2675,2760,3504,3808,3989,4041,4100,4178 (+ comment lines ~329,846,1638,1823) | Delete | Remove 12 faiss_service imports, ~20 faiss_service calls, ~4 comment lines, ~1 comment text update |
| `registry/api/agent_routes.py` | 628,631,1150,1152,1598,1601,1853,1855 (+ comment at 96, docstring at 2001) | Delete | Remove 4 faiss_service imports, 4 faiss_service calls, 1 comment, update 1 docstring |
| `registry/api/search_routes.py` | 385,440,510 | Update | Update 3 docstring/comment/error log lines (no faiss_service calls in this file) |
| `registry/services/agent_batch_item_processor.py` | 225,228,338,340 | Delete | Remove 2 faiss_service imports, 2 faiss_service calls |
| `registry/repositories/factory.py` | 132-151 | Simplify | Remove else-branch from get_search_repository(); keep MONGODB_BACKENDS import (used by 15 other functions) |
| `registry/core/telemetry.py` | 730-731 | Update | Replace ternary with literal `"documentdb"` |
| `registry/main.py` | 497,503 | Update | Remove ternary; remove FAISS boot comment |
| `registry/metrics/client.py` | 111,126 | Delete | Remove faiss_search_time_ms parameter and dict entry |
| `registry/embeddings/README.md` | 13,252-260 | Delete | Remove backward compat mention; delete FAISS integration section |
| `registry/servers/mcpgw.json` | 197,199,226 | Update | Update 3 tool description references from "FAISS" to "DocumentDB" |

#### Metrics Service Files (4 files)

| File Path | Lines | Type | Change Description |
|-----------|-------|------|-------------------|
| `metrics-service/metrics_client.py` | 193,208 | Delete | Remove faiss_search_time_ms parameter and dict entry |
| `metrics-service/app/storage/database.py` | 172,306,319 | Delete | Remove DDL column; remove from INSERT and SELECT statements |
| `metrics-service/app/storage/migrations.py` | 120 | Delete | Remove DDL column from migration |
| `metrics-service/docs/database-schema.md` | Various | Update | Remove faiss_search_time_ms from schema documentation |
| `metrics-service/docs/api-reference.md` | Various | Update | Remove faiss_search_time_ms from API documentation |

#### Infrastructure Files (7 files)

| File Path | Lines | Type | Change Description |
|-----------|-------|------|-------------------|
| `docker-compose.yml` | 71 | Update | Remove FAISS from comment |
| `docker-compose.prebuilt.yml` | 14 | Update | Remove FAISS from comment |
| `docker-compose.podman.yml` | 4 | Update | Remove FAISS from comment |
| `build-config.yaml` | 25,30 | Update | Remove FAISS from comment and description |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | 587 | Update | Remove FAISS from comment |
| `terraform/aws-ecs/OPERATIONS.md` | 136 | Update | Remove FAISS from description |
| `terraform/telemetry-collector/lambda/collector/schemas.py` | 267 | Update | Change regex from "^(faiss|documentdb)$" to "^documentdb$" |

#### CLI/API Client Files (2 files)

| File Path | Lines | Type | Change Description |
|-----------|-------|------|-------------------|
| `cli/agent_mgmt.py` | 34 | Update | Remove FAISS mention in CLI help text |
| `api/registry_client.py` | 2617 | Update | Remove FAISS mention in docstring |

#### Scripts (1 file)

| File Path | Lines | Type | Change Description |
|-----------|-------|------|-------------------|
| `scripts/migrate-file-to-mongodb.py` | 220 | Delete (recommended) | Migration script is obsolete since FAISS is removed |

#### Test Files (7 files + 2 to delete)

| File Path | Lines | Type | Change Description |
|-----------|-------|------|-------------------|
| `tests/fixtures/mocks/mock_faiss.py` | N/A | DELETE | Entire file |
| `tests/unit/search/test_faiss_service.py` | ~720 | DELETE | Entire file |
| `tests/conftest.py` | 51,142-149,374 | Update | Remove mock import and sys.modules setup |
| `tests/test_infrastructure.py` | 15,27-29 | Update | Remove import and test method |
| `tests/unit/conftest.py` | 16,18,21 | Update | Remove mock fixture |
| `tests/unit/test_safe_eval_arithmetic.py` | 25-37 | Update | Remove faiss __spec__ patching block |
| `tests/unit/core/test_config.py` | 555-576 | Update | Remove 2 FAISS config test classes |
| `tests/unit/core/test_telemetry.py` | 340,343 | Update | Remove FAISS file backend test |
| `tests/unit/repositories/test_factory_aliases.py` | 197-199 | Update | Remove FaissSearchRepository from expected |
| `tests/README.md` | 18,27,50,93,96,98,289,291 | Update | Remove FAISS references |

#### Documentation Files (22 files)

| File Path | Ref Count | Type | Change Description |
|-----------|-----------|------|-------------------|
| `docs/embeddings.md` | 7 | Update/Delete | Remove backward compat; delete FAISS integration section |
| `docs/database-design.md` | 4 | Update | Remove FAISS from architecture list, diagram, and comparison table |
| `docs/configuration.md` | 1 | Update | Remove FAISS from file backend description |
| `docs/api-reference.md` | 1 | Update | Update search description |
| `docs/service-management.md` | 5 | Update | Remove FAISS integration bullets and CLI examples |
| `docs/testing/test-categories.md` | 8 | Update/Delete | Remove real FAISS test examples and fixture references |
| `docs/testing/memory-management.md` | 3 | Update | Remove FAISS from lists |
| `docs/testing/QUICK-START.md` | 3 | Update | Remove FAISS from skip/coverage lists |
| `docs/faq/configuring-mongodb-atlas-backend.md` | 1 | Update | Note FAISS fallback was fixed |
| `docs/TELEMETRY.md` | 1 | Update | Update search backend enum |
| `docs/prebuilt-images.md` | 1 | Update | Remove FAISS from image description |
| `docs/registry-auth-detailed.md` | 3 | Update | Remove UpdateFAISS from Mermaid diagram |
| `docs/server-versioning-operations.md` | 3 | Update | Update FAISS references to DocumentDB |
| `docs/design/a2a-protocol-integration.md` | 21 | Update | Remove/update all FAISS references |
| `docs/design/database-abstraction-layer.md` | 13 | Update | Remove FAISS sections |
| `docs/design/server-versioning.md` | 1 | Update | Remove FAISS mention |
| `docs/design/storage-architecture-mongodb-documentdb.md` | 4 | Update | Remove FAISS references |
| `docs/dynamic-tool-discovery.md` | 12 | DELETE or RETIRE | Entire document describes FAISS search — either rewrite for DocumentDB or mark as legacy |
| `docs/llms.txt` | 11 | Update | Remove all FAISS mentions |
| `docs/OBSERVABILITY-LEGACY.md` | 4 | Update | Mark faiss_search_time_ms references as legacy |
| `.claude/skills/debug/SKILL.md` | 1 | Update | Remove FAISS example |
| `.claude/skills/pr-review/personas/backend-developer.md` | 1 | Update | Update technology stack list |

#### Historical Notes (1 file — DO NOT EDIT)

| File Path | Ref Count | Type | Change Description |
|-----------|-----------|------|-------------------|
| `release-notes/v1.0.17.md` | 3 | SKIP | Historical content — DO NOT edit. Add new release note in current version instead. |

### Summary Counts

| Category | Count |
|----------|-------|
| Files to delete | 5 (service.py, search_repository.py, mock_faiss.py, test_faiss_service.py, migrate-file-to-mongodb.py*) |
| Files to modify | 49 |
| Files to skip (historical) | 1 (release-notes/v1.0.17.md) |
| Total files affected | 55 |
| Total non-test FAISS refs removed | 317 |
| Total test FAISS refs removed | ~383 |
| **Total FAISS refs removed** | **~700** |

*Subject to review — migration script may be kept if it serves general purposes beyond FAISS.

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted code (source) | ~1,340 |
| Deleted test code | ~750 |
| Modified code (imports, calls, comments) | ~250 |
| Modified docs (lines touched, not deleted) | ~150 |
| **Total net reduction** | **~1,500** |

## Testing Strategy
See `testing.md` for the complete test plan.

## Alternatives Considered

### Alternative 1: Soft Deprecation (keep FAISS code, deprecate via config flag)
**Description:** Add a `faiss_enabled: bool = False` config flag and deprecate gradually.
**Pros:** Zero breaking changes; operators can migrate at their own pace.
**Cons:** Leaves dead code in place indefinitely; no reduction in dependency footprint; contributor confusion persists.
**Why Rejected:** The task explicitly states FAISS is obsolete. There is no need to maintain dead code. A deprecation flag adds surface area without benefit.

### Alternative 2: Keep FaissSearchRepository as a no-op fallback
**Description:** Replace FaissSearchRepository with a stub that delegates to DocumentDB.
**Pros:** Preserves the factory pattern without branches.
**Cons:** Adds a meaningless stub class; the factory pattern was designed for a dual-backend reality that no longer exists.
**Why Rejected:** The factory pattern should be simplified, not patched. Since DocumentDB is the only backend, `get_search_repository()` should directly return it.

### Comparison Matrix

| Criteria | Chosen (Full Removal) | Alt 1 (Deprecation Flag) | Alt 2 (Stub) |
|----------|-----------------------|--------------------------|--------------|
| Complexity | Low | Medium | Medium |
| Code Reduction | ~1,500 lines net | ~0 lines net | ~10 lines net |
| Contributor Clarity | High | Low | Medium |
| Dependency Footprint | Reduced | Unchanged | Unchanged |
| Breaking Changes | None (FAISS is inactive) | None | None |

## Rollout Plan
- Phase 1: Implementation — delete and modify files per steps above
- Phase 2: Verification — run `grep -ri faiss .` to confirm zero results; run imports check
- Phase 3: Testing — verify DocumentDB search path works end-to-end
- Phase 4: Deployment — deploy and verify no regressions

## Open Questions
- Are there any released container images or Helm values that hardcode `STORAGE_BACKEND=file`? These will fail startup with the new validator (correct behavior, but operators need advance notice). Helm charts confirmed clean of FAISS references.
- Should `docs/dynamic-tool-discovery.md` be rewritten for DocumentDB or marked as legacy? (Recommended: Mark as legacy — the document describes FAISS search internals that no longer exist.)
- The `registry/repositories/file/` directory contains 7 non-FAISS files. After deleting `search_repository.py`, the directory will have 7 remaining files. Should `__init__.py` in that directory be checked for any FAISS exports?

## References
- DocumentDB search repository: `registry/repositories/documentdb/search_repository.py`
- Repository factory: `registry/repositories/factory.py`
- Storage backend config: `registry/core/config.py`
- Embeddings client (uses numpy independently): `registry/embeddings/client.py`
- Full FAISS reference map: see Codebase Analysis section above (53 files)