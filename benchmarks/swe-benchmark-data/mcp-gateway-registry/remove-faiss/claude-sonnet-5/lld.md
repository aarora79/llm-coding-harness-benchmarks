# Low-Level Design: Remove FAISS, consolidate on DocumentDB hybrid search

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
The registry ships two parallel search implementations: `FaissService`/`FaissSearchRepository` (used when `STORAGE_BACKEND=file`, the current default) and `DocumentDBSearchRepository` (used for `documentdb`/`mongodb`/`mongodb-ce`/`mongodb-atlas`). The two paths have already drifted: several routes in `registry/api/server_routes.py` and `registry/api/agent_routes.py` write to `faiss_service` only, with no equivalent DocumentDB indexing call, meaning file-backend and DocumentDB-backend deployments are not functionally equivalent today. FAISS also lacks true vector removal, has no built-in hybrid fusion, and requires a full in-memory index rebuild on every process restart. `docs/configuration.md` already marks the file/FAISS backend "DEPRECATED" and steers users to `mongodb-ce`/`documentdb`. This design removes FAISS entirely and makes `DocumentDBSearchRepository` the sole search implementation, replacing every FAISS-only call site with the existing repository-factory abstraction so search-index freshness is preserved for every existing operation.

### Goals
- Delete all FAISS source code, config/schema surface, and the `faiss-cpu` dependency.
- Preserve every existing search-affecting behavior (register, update, toggle, delete, tool refresh) for both current storage backends by routing all indexing through `get_search_repository()`.
- Remove FAISS-specific build, Docker, and ops-script logic.
- Update or delete FAISS-specific tests; no reduction in overall test coverage for search behavior.
- Rewrite documentation so no file describes FAISS as a current or available backend.

### Non-Goals
- Changing the DocumentDB ranking algorithm (RRF, soft-cap distribution, score normalization).
- Migrating existing file-backend server/agent data into MongoDB (handled by the pre-existing `scripts/migrate-file-to-mongodb.py`).
- Modifying the embeddings generation layer (`registry/embeddings/client.py`), which is already backend-agnostic.
- Changing `servers/mcpgw/server.py`'s `intelligent_tool_finder` behavior — it is already deprecated and already calls the registry's `/api/search/semantic` HTTP endpoint with no direct FAISS dependency; only its documentation needs updating.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/search/service.py` | `FaissService`: in-memory FAISS index, embedding calls, keyword boost, hybrid scoring | Entire file deleted |
| `registry/repositories/file/search_repository.py` | `FaissSearchRepository`: thin adapter over `faiss_service` implementing `SearchRepositoryBase` | Entire file deleted |
| `registry/repositories/documentdb/search_repository.py` | `DocumentDBSearchRepository`: native `$vectorSearch` + keyword regex + RRF fusion + lexical/client-side fallbacks | Becomes the sole search implementation; no changes needed to its internals |
| `registry/repositories/factory.py` | `get_search_repository()` backend switch | Simplified to always return `DocumentDBSearchRepository` |
| `registry/repositories/interfaces.py` | `SearchRepositoryBase` ABC | Docstring wording updated ("FAISS or DocumentDB" to "DocumentDB"); interface unchanged |
| `registry/core/config.py` | `Settings.faiss_index_path`/`faiss_metadata_path` properties, `storage_backend` field/default | FAISS properties deleted; default reconsidered (see Alternatives) |
| `registry/core/schemas.py` | `FaissMetadata` Pydantic model | Deleted |
| `registry/api/server_routes.py` | ~14 call sites reading/writing `faiss_service` directly, roughly half with no DocumentDB equivalent | Each FAISS-only call site gets a `get_search_repository()` call added or the FAISS call replaced |
| `registry/api/agent_routes.py` | `register_agent`, `toggle_agent`, `update_agent`, `delete_agent` call `faiss_service` only, with no DocumentDB indexing at all | Each of these 4 functions gains a `get_search_repository()` index/remove call |
| `cli/agent_mgmt.py` | Two `faiss_service` call sites (agent registration/removal in the CLI path) | Same replacement pattern |
| `registry/main.py` | Startup `lifespan()`: FAISS-only "rebuild in-memory index on every boot" branch | Branch deleted; only the DocumentDB-persistent path remains |
| `registry/core/telemetry.py` | `search_backend = "documentdb" if ... else "faiss"` in heartbeat payload | Ternary collapses; backward-compat decision needed (see Observability) |
| `registry/metrics/client.py`, `metrics-service/metrics_client.py`, `metrics-service/app/storage/database.py`, `metrics-service/app/storage/migrations.py` | `faiss_search_time_ms` field/column threaded through metrics pipeline | Field renamed with a migration decision (see Observability) |
| `build_and_run.sh` | FAISS index file cleanup-warning block (~37 lines) and post-startup verification block (~16 lines) | Both blocks deleted |
| `cli/service_mgmt.sh`, `terraform/aws-ecs/scripts/service_mgmt.sh` | `verify_faiss_metadata()` greps `service_index_metadata.json` inside the container | Function rewritten to query the search API instead |
| `pyproject.toml` / `uv.lock` | `faiss-cpu>=1.7.4` dependency | Removed; lockfile regenerated |
| `tests/fixtures/mocks/mock_faiss.py`, `tests/conftest.py` | Module-level `sys.modules["faiss"]` auto-mock installed for every test | Mock module deleted; auto-mock removed from conftest |
| `tests/unit/search/test_faiss_service.py` | 64 tests exercising `FaissService` directly | Deleted; behavior-level coverage gap flagged for `DocumentDBSearchRepository` (see Testing Strategy) |
| Documentation (20+ files enumerated in File Changes) | FAISS described as current backend in varying depth | Edited per-file; `docs/design/hybrid-search-architecture.md` is the reference doc and needs zero changes |

### Existing Patterns Identified
1. **Repository factory with graceful "no file backend" collapse**: `get_skill_repository()` (`factory.py` lines ~224-245) and `get_virtual_server_repository()` (lines ~270-291) already have no file-backend implementation and unconditionally return the DocumentDB class in both branches of their `if/else`. This is the exact precedent to follow for `get_search_repository()` — collapse the `if backend in MONGODB_BACKENDS: ... else: ...` down to always returning `DocumentDBSearchRepository()`.
   - Files: `registry/repositories/factory.py`
   - How a future implementer should follow this: mirror the two existing functions' structure (a single `if _search_repo is not None: return _search_repo` guard, then unconditional instantiation), not a bare `return DocumentDBSearchRepository()` with no singleton caching.
2. **Dual-write during a prior partial migration**: several handlers (`toggle_service_route`, `internal_toggle_service`, `edit_server_submit`, `toggle_service_api`) already call both `faiss_service.add_or_update_service(...)` and `get_search_repository().index_server(...)` back-to-back. This shows the intended replacement idiom already in use elsewhere in the same file — the FAISS call in these functions can simply be deleted, leaving the existing DocumentDB call intact.
   - Files: `registry/api/server_routes.py`
3. **FAISS-only call sites with no DocumentDB equivalent** (the functional gap this design must close, not just a deletion): `register_service` (form endpoint), `internal_register_service`, `clear_security_pending_local`, `internal_remove_service`, `get_service_tools` (tool refresh), `refresh_service`, `remove_service_api`, and the async `faiss_service.save_data()` task in `server_routes.py`; and `register_agent`, `toggle_agent`, `update_agent`, `delete_agent` in `agent_routes.py`.
   - How a future implementer should follow this: for each, add the missing `get_search_repository()` call using the same call shape already present in the dual-write examples above (`index_server`/`index_agent` for add-or-update, `remove_entity` for delete). Do **not** simply delete the FAISS call and leave nothing — that would regress search-index freshness for these paths.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry/repositories/factory.py::get_search_repository()` | Simplified factory | Always returns `DocumentDBSearchRepository`; callers (`search_routes.py`, `server_routes.py`, `agent_routes.py`, `main.py`) need no changes to their call signature since they already consume the abstract `SearchRepositoryBase` |
| `registry/api/search_routes.py` | Already backend-agnostic | No functional change; only stale comment/log-string cleanup ("FAISS embeddings", "FAISS search service unavailable") |
| `registry/main.py::lifespan()` | Startup sequencing | FAISS-only rebuild-on-boot branch removed; DocumentDB's persistent-index branch becomes the only path |
| `registry/core/telemetry.py` | Heartbeat payload | `search_backend` field becomes a hardcoded `"documentdb"` string (see Observability for compatibility decision) |
| `docker-compose.yml` `mongodb` + `mongodb-init` services | Already bundled | No new infra needed; these services already exist and are the target for the new default `storage_backend` |

### Constraints and Limitations Discovered
- **File-backend deployments still need search.** Since `FaissSearchRepository` has no independent vector-search logic of its own (it is a pure delegate to `faiss_service`), removing FAISS means file-backend deployments (`STORAGE_BACKEND=file`, still a supported value per `ALLOWED_STORAGE_BACKENDS`) can no longer perform vector/hybrid search unless they also connect to MongoDB/DocumentDB purely for the search collection. This design resolves it by changing the **default** value of `storage_backend` (see Configuration Parameters and Alternatives Considered) rather than by implementing a new brute-force/in-memory search for the file backend — building a third search implementation would reintroduce the "two parallel paths" problem this issue is meant to eliminate, and `docs/configuration.md` already documents the file backend as deprecated.
- **`FaissSearchRepository.rebuild_index()` is already broken.** It calls `self.faiss_service.rebuild_index()`, a method that does not exist on `FaissService` — this would raise `AttributeError` if ever invoked. This confirms the method has no in-use callers and nothing needs to be preserved from it.
- **Metrics schema has deployed history.** `faiss_search_time_ms` is a live SQLite column name in `metrics-service/app/storage/database.py`'s `discovery_metrics` table. Renaming it outright would break any existing `metrics.db` file's column continuity for historical rows. This design keeps the column name as a frozen historical artifact (see Observability) rather than issuing a destructive rename migration, since this is a benchmarking/analytics-only concern, not a functional search regression risk.

## Architecture

### System Context Diagram (after this change)

```
                         +-------------------------+
                         |   registry/api/*_routes |
                         |   (search, server,      |
                         |    agent routes)         |
                         +------------+-------------+
                                      |
                                      | get_search_repository()
                                      v
                         +-------------------------+
                         | SearchRepositoryBase     |  (unchanged ABC)
                         | (interfaces.py)          |
                         +------------+-------------+
                                      |
                                      | sole implementation
                                      v
                         +-------------------------+
                         | DocumentDBSearchRepository|
                         | (documentdb/search_       |
                         |  repository.py)           |
                         +------------+-------------+
                                      |
                     +----------------+-----------------+
                     v                                   v
          +--------------------+              +--------------------+
          | $vectorSearch HNSW  |              | Keyword regex match |
          | (or client-side     |              | (_build_keyword_    |
          |  cosine fallback)   |              |  match_filter)       |
          +----------+---------+              +----------+-----------+
                     |                                    |
                     +-------------------+----------------+
                                         v
                              Reciprocal Rank Fusion
                              (unchanged, existing code)
```

The `FaissService`/`FaissSearchRepository` box and its direct connections to `server_routes.py`/`agent_routes.py` are removed entirely; every caller now goes exclusively through `get_search_repository()`.

### Sequence Diagram — server registration (after change)

```
Client -> server_routes.register_service
       -> server_service.register (writes JSON/Mongo doc)
       -> get_search_repository() -> DocumentDBSearchRepository
       -> search_repo.index_server(path, server_info, is_enabled)
       -> (embeds text, upserts doc with embedding + text/tags fields)
       <- 201 Created
```

This replaces the previous flow where `register_service`'s "New server" branch called `faiss_service.add_or_update_service(path, server_entry, is_enabled)` directly and never called `get_search_repository()` at all.

### Component Diagram
No new components. `DocumentDBSearchRepository` and `SearchRepositoryBase` are unchanged; `FaissService` and `FaissSearchRepository` are removed. `registry/repositories/factory.py::get_search_repository()` is the only component whose internals change.

## Data Models

### Models Removed
```python
# registry/core/schemas.py -- DELETE
class FaissMetadata(BaseModel):
    """FAISS metadata model."""

    id: int
    text_for_embedding: str
    full_server_info: ServerInfo
```

No new Pydantic models are introduced. `DocumentDBSearchRepository` already stores embedding + text/tags fields as plain MongoDB document fields (not a separate Pydantic model) inside its `index_server`/`index_agent` methods.

### Model Changes
`registry/core/config.py::Settings` loses two properties:
```python
# DELETE
@property
def faiss_index_path(self) -> Path:
    return self.servers_dir / "service_index.faiss"

@property
def faiss_metadata_path(self) -> Path:
    return self.servers_dir / "service_index_metadata.json"
```

`registry/repositories/interfaces.py::SearchRepositoryBase` docstring is reworded from "Abstract base class for semantic/hybrid search using FAISS or DocumentDB." to "Abstract base class for semantic/hybrid search using DocumentDB." No method signatures change.

## API / CLI Design

No HTTP endpoint signatures, request/response shapes, or CLI flags change. `POST /api/search/semantic`, `GET /api/search/tags`, and every server/agent CRUD route keep identical request/response contracts — only their internal indexing call target changes from `faiss_service` to `get_search_repository()`. This is intentionally an invisible-to-clients refactor per the task's "must not break existing search behaviour" constraint.

**One CLI-adjacent behavior change**: `cli/service_mgmt.sh` and `terraform/aws-ecs/scripts/service_mgmt.sh`'s `verify_faiss_metadata()` currently greps a file (`service_index_metadata.json`) that will no longer exist. Post-change, verification is done via an HTTP call:

```bash
# New verify_search_index(), replacing verify_faiss_metadata()
verify_search_index() {
  local service_name="$1"
  local should_exist="$2"
  local response
  response=$(curl -s -X POST "${REGISTRY_URL}/api/search/semantic" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"${service_name}\", \"max_results\": 5}")
  if echo "$response" | grep -q "\"${service_name}\""; then
    found="true"
  else
    found="false"
  fi
  if [ "$found" == "$should_exist" ]; then
    echo "PASS: search index state for '${service_name}' matches expected (${should_exist})"
    return 0
  else
    echo "FAIL: search index state for '${service_name}' expected ${should_exist}, got ${found}"
    return 1
  fi
}
```

## Configuration Parameters

### Removed
| Property | Type | Location | Notes |
|----------|------|----------|-------|
| `faiss_index_path` | `Path` property | `registry/core/config.py` | Not an env var; derived path, deleted with no replacement needed |
| `faiss_metadata_path` | `Path` property | `registry/core/config.py` | Same |

### Changed
| Variable Name | Type | Old Default | New Default | Required | Description |
|---------------|------|--------------|--------------|----------|--------------|
| `STORAGE_BACKEND` | str | `file` | `mongodb-ce` | No | Now defaults to the already-bundled `mongodb-ce` Docker Compose service so a fresh install gets DocumentDB-backed search out of the box, consistent with `docs/configuration.md`'s existing deprecation notice for the file backend. `file` remains an accepted value for entity storage (servers/agents JSON), but search always uses DocumentDB regardless of this setting once `get_search_repository()` is simplified. |

No other environment variables change. `EMBEDDINGS_*`, `DOCUMENTDB_*`, `MONGODB_CONNECTION_STRING`, `VECTOR_SEARCH_EF_SEARCH`, `SEARCH_FUSION_METHOD` are all unaffected — `.env.example` already documents these correctly and needs no edits for this change beyond the `STORAGE_BACKEND` default line.

### Deployment Surface Checklist
- [ ] `.env.example` — update the `STORAGE_BACKEND` default comment/value if the default changes (see Alternatives Considered for the decision to make this explicit rather than silent).
- [ ] `docker-compose.yml`, `docker-compose.podman.yml`, `docker-compose.prebuilt.yml` — update `STORAGE_BACKEND=${STORAGE_BACKEND:-file}` to `${STORAGE_BACKEND:-mongodb-ce}` in both the registry and any other service block that references it (lines ~198, ~454 in `docker-compose.yml`; equivalent lines in the other two compose files).
- [ ] `docker-compose.dhi.yml` — confirmed no FAISS or `STORAGE_BACKEND` default override; no change needed beyond verifying it inherits the same env var.
- [ ] `build-config.yaml` — remove "FAISS" from the two description strings (registry image comment and `description` field).
- [ ] Terraform/Helm — no FAISS-specific Terraform variables exist (confirmed no `FAISS_*` var in `terraform/`); only doc/script text in `terraform/aws-ecs/OPERATIONS.md` and `terraform/aws-ecs/scripts/service_mgmt.sh` needs updating.

## New Dependencies

This change uses only existing dependencies. No new packages are added. `faiss-cpu` is removed from `pyproject.toml`; `numpy`, `scikit-learn`, `torch`, and `sentence-transformers` all remain because they are used elsewhere (numpy is a transitive dependency of `sentence-transformers`/`scikit-learn`/`torch`'s ecosystem; scikit-learn and torch are unrelated to FAISS; sentence-transformers is the default embeddings provider used identically by both search paths).

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Simplify the search repository factory
**File:** `registry/repositories/factory.py`
**Lines:** ~132-151

```python
def get_search_repository() -> SearchRepositoryBase:
    """Get search repository singleton."""
    global _search_repo

    if _search_repo is not None:
        return _search_repo

    from .documentdb.search_repository import DocumentDBSearchRepository

    _search_repo = DocumentDBSearchRepository()
    return _search_repo
```

#### Step 2: Delete FAISS source files
**Files:** `registry/search/service.py` (entire file), `registry/repositories/file/search_repository.py` (entire file)

Confirm no other module imports `FaissService`/`faiss_service`/`FaissSearchRepository` before deleting (grep for `from registry.search.service` and `FaissSearchRepository` across `registry/`, `cli/`, `tests/`).

#### Step 3: Remove FAISS-specific config and schema
**File:** `registry/core/config.py`, lines ~995-1001 — delete `faiss_index_path`/`faiss_metadata_path` properties.
**File:** `registry/core/schemas.py`, lines ~505-510 — delete `FaissMetadata`.

#### Step 4: Replace FAISS-only call sites in `server_routes.py`
**File:** `registry/api/server_routes.py`

For each of the ~14 call sites, delete the `from ..search.service import faiss_service` import and the `faiss_service.*` call. For the sites already dual-writing (`toggle_service_route` L846-854, `internal_toggle_service` L1948-1955, `edit_server_submit` L2384-2395, `toggle_service_api` L4040-4047), simply delete the FAISS lines — the DocumentDB call already present is sufficient.

For the FAISS-only sites (`register_service` L1341-1343, `internal_register_service` L1638-1643, `clear_security_pending_local` L1752, `internal_remove_service` L1826-1827, `get_service_tools` L2629-2633, `refresh_service` L2759-2760, `remove_service_api` L4177-4178), replace the FAISS call with the equivalent repository call:

```python
# Before (FAISS-only, register_service):
await faiss_service.add_or_update_service(path, server_entry, is_enabled)

# After:
from ..repositories.factory import get_search_repository

search_repo = get_search_repository()
await search_repo.index_server(path, server_entry, is_enabled)
```

```python
# Before (FAISS-only, internal_remove_service / remove_service_api):
await faiss_service.remove_service(service_path)

# After:
from ..repositories.factory import get_search_repository

search_repo = get_search_repository()
await search_repo.remove_entity(service_path)
```

The async persistence task at L3806-3808 (`asyncio.create_task(faiss_service.save_data())`) is deleted outright with no replacement — `DocumentDBSearchRepository` persists on every write via `replace_one(upsert=True)`, so there is no equivalent "flush to disk" step needed.

#### Step 5: Add missing indexing calls in `agent_routes.py`
**File:** `registry/api/agent_routes.py`

`register_agent` (L628-636), `toggle_agent` (L1150-1157), `update_agent` (L1598-1606), `delete_agent` (L1853-1855) currently call **only** `faiss_service`. Replace each with the corresponding `get_search_repository()` call:

```python
# register_agent / toggle_agent / update_agent (add-or-update):
from ..repositories.factory import get_search_repository

search_repo = get_search_repository()
await search_repo.index_agent(path, agent_card, is_enabled)

# delete_agent (remove):
search_repo = get_search_repository()
await search_repo.remove_entity(path)
```

Also update the module-level docstring at L96 ("Updating FAISS with disabled state...") and the `discover_agents_semantic` docstring at L2001 ("Uses search repository (FAISS or DocumentDB)...") to drop the FAISS mention.

#### Step 6: Update `cli/agent_mgmt.py`
**File:** `cli/agent_mgmt.py`, lines ~225-228, ~338-340 — same replacement pattern as Step 5. Also reword the module docstring at L34 describing the `search` subcommand from "using FAISS vector index" to "using DocumentDB hybrid search".

#### Step 7: Simplify `registry/main.py` startup
**File:** `registry/main.py`, lines ~492-552 (the `lifespan()` search-initialization block)

```python
# After:
search_repo = get_search_repository()
logger.info("Initializing DocumentDB search service...")
await search_repo.initialize()
logger.info("DocumentDB search index is persistent, skipping startup re-index")

logger.info("Loading agent cards and state...")
await agent_service.load_agents_and_state()
```

Delete the `backend_name` ternary and the entire `if settings.storage_backend not in MONGODB_BACKENDS:` re-index branch (the old servers/agents/skills rebuild loop) since it existed only to compensate for FAISS's in-memory nature.

#### Step 8: Remove FAISS build/ops logic
**File:** `build_and_run.sh` — delete the FAISS-file-cleanup-warning block (~lines 242-278) and the post-startup FAISS verification block (~lines 620-635).
**Files:** `cli/service_mgmt.sh` (lines ~166-189, ~613, ~705), `terraform/aws-ecs/scripts/service_mgmt.sh` (lines ~184-207, ~631, ~723) — replace `verify_faiss_metadata()` with `verify_search_index()` per the API/CLI Design section above, and update both call sites.

#### Step 9: Remove the dependency
**File:** `pyproject.toml`, line ~23 — delete `"faiss-cpu>=1.7.4",`. Regenerate `uv.lock` via `uv lock` (implementer runs this; out of scope for this design doc to execute).

#### Step 10: Documentation pass
See File Changes below for the full list; each doc gets FAISS wording removed or (for `docs/embeddings.md`, `registry/embeddings/README.md`, `docs/design/database-abstraction-layer.md`) a more substantial rewrite of FAISS-centric sections. `docs/design/hybrid-search-architecture.md` is left untouched.

### Error Handling
No new error paths are introduced. `DocumentDBSearchRepository.search()` already handles the case where the embedding model is unavailable (`_lexical_only_search` fallback) and the case where the underlying Mongo doesn't support `$vectorSearch` (`_client_side_search` fallback via `OperationFailure` code 31082 detection) — both existed before this change and require no modification. The generic `except RuntimeError` handler in `search_routes.py` (currently logging "FAISS search service unavailable") is kept but its log message is reworded to "search service unavailable" since it's a backend-agnostic catch, not FAISS-specific logic.

### Logging
Update the `backend_name` f-string logging in `main.py` (Step 7) to drop the conditional and log "DocumentDB" directly. Update `registry/core/telemetry.py`'s `search_backend` computation (see Observability) with an explicit comment explaining why the value is now a constant.

## Observability

### `search_backend` telemetry field (registry/core/telemetry.py)
**Decision**: keep the field name `search_backend` in the heartbeat payload for backward compatibility with existing telemetry consumers/dashboards, but hardcode its value to `"documentdb"` once FAISS is removed, with a comment noting the field is now always `"documentdb"` and retained only for schema stability:

```python
# All storage backends now use DocumentDB-backed search; the "faiss" value
# is retired but the field name is kept for telemetry-schema stability.
search_backend = "documentdb"
```

`tests/integration/test_telemetry_e2e.py::test_heartbeat_payload_search_backend_file` (which asserts `payload["search_backend"] == "faiss"`) must be deleted or rewritten to assert `"documentdb"` for both backend configurations.

### `faiss_search_time_ms` metrics field
**Decision**: keep the parameter name and SQLite column name `faiss_search_time_ms` unchanged in `registry/metrics/client.py`, `metrics-service/metrics_client.py`, `metrics-service/app/storage/database.py`, and `metrics-service/app/storage/migrations.py`. Renaming a persisted SQLite column requires a migration and risks breaking any external dashboard/query keyed on that column name, for a purely cosmetic gain. Document in a code comment at each of the four call sites that the name is historical and no longer FAISS-specific (the underlying search operation, whichever repository serves it, still has a "vector search duration" concept worth measuring). This is a deliberate scope-limiting decision — a future issue can consider a proper column-rename migration if desired, but it is out of scope here since the task is FAISS code removal, not a metrics schema redesign.

### Tracing / Metrics / Logging Points
No new spans/metrics are added. Existing `emit_discovery_metric` calls continue to fire with the same field names (see above). `logger.info` calls in `main.py`'s startup path are simplified per Step 7 but no logging is removed from the request-handling paths.

## Scaling Considerations
- **Current load assumptions**: unchanged. `DocumentDBSearchRepository` already handles concurrent read/write load via MongoDB/DocumentDB's native concurrency control; this was already true for every non-file deployment before this change.
- **Horizontal scaling**: removing FAISS actually improves horizontal scalability for file-backend deployments that previously relied on an in-memory, single-process FAISS index (which cannot be shared across multiple registry replicas). Once search always goes through DocumentDB, multiple registry instances can share one search index consistently.
- **Bottlenecks**: none introduced. The `$vectorSearch` HNSW index and RRF fusion logic are unchanged.
- **Caching strategy**: unchanged; `DocumentDBSearchRepository` does not introduce new caching behavior as part of this design.

## File Changes

### New Files
None.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `registry/repositories/factory.py` | ~20 | Simplify `get_search_repository()` to always return `DocumentDBSearchRepository` |
| `registry/repositories/interfaces.py` | ~1 | Reword `SearchRepositoryBase` docstring |
| `registry/core/config.py` | ~10 | Delete `faiss_index_path`/`faiss_metadata_path` properties |
| `registry/core/schemas.py` | ~6 | Delete `FaissMetadata` |
| `registry/api/server_routes.py` | ~60 | Remove/replace ~14 `faiss_service` call sites per Step 4 |
| `registry/api/agent_routes.py` | ~40 | Add missing `get_search_repository()` calls per Step 5; update 2 docstrings |
| `cli/agent_mgmt.py` | ~15 | Replace 2 call sites; reword docstring |
| `registry/main.py` | ~55 | Delete FAISS-only rebuild-on-boot branch per Step 7 |
| `registry/core/telemetry.py` | ~5 | Hardcode `search_backend = "documentdb"` |
| `build_and_run.sh` | ~53 | Delete two FAISS-file bash blocks |
| `cli/service_mgmt.sh` | ~25 | Replace `verify_faiss_metadata()` with `verify_search_index()`; update 2 call sites |
| `terraform/aws-ecs/scripts/service_mgmt.sh` | ~25 | Same as above |
| `pyproject.toml` | 1 | Remove `faiss-cpu` dependency line |
| `uv.lock` | (generated) | Regenerate via `uv lock` |
| `build-config.yaml` | 2 | Remove "FAISS" from description strings |
| `docker-compose.yml`, `docker-compose.podman.yml`, `docker-compose.prebuilt.yml` | ~4 each | Update `STORAGE_BACKEND` default; remove "FAISS" from comments |
| `.env.example` | ~2 | Update `STORAGE_BACKEND` default value/comment |
| `docs/embeddings.md` | substantial | Rewrite "Migration Between Providers" and "Integration with FAISS Search" sections against DocumentDB; remove FAISS bullet/link |
| `registry/embeddings/README.md` | substantial | Same rewrite as above (near-duplicate file) |
| `docs/database-design.md` | small | Delete File-backend/FAISS rows from diagrams/comparison table |
| `docs/design/database-abstraction-layer.md` | substantial | Delete File-backend implementation subsection, class-diagram/tree references to `FaissSearchRepository` |
| `docs/design/storage-architecture-mongodb-documentdb.md` | small | Delete File/FAISS column from overview diagrams/tables |
| `docs/configuration.md` | small | Shrink/delete "File Backend (Deprecated)" subsection's FAISS bullet |
| `docs/api-reference.md` | 1 line | Reword semantic-search endpoint description |
| `docs/dynamic-tool-discovery.md` | substantial | Rewrite FAISS code sample describing `intelligent_tool_finder`; clarify it delegates to `/api/search/semantic` |
| `docs/faq/configuring-mongodb-atlas-backend.md` | 1 line | Optional light reword of historical callout (may be left as-is; see Alternatives) |
| `docs/prebuilt-images.md` | 1 line | Remove "FAISS" from image description table cell |
| `docs/registry-auth-detailed.md` | small | Rename Mermaid node `UpdateFAISS` to `UpdateSearchIndex` |
| `docs/server-versioning-operations.md` | 3 lines | Reword "FAISS search index" to "search index" |
| `docs/service-management.md` | 5 lines | Reword FAISS bullets/comments to generic search-index language |
| `docs/testing/QUICK-START.md`, `docs/testing/memory-management.md`, `docs/testing/test-categories.md` | small each | Update mock/fixture descriptions once `mock_faiss.py`/`mock_faiss_service` are removed |
| `docs/llms.txt`, `README.md` | 0 | No FAISS references found; no changes |
| `terraform/aws-ecs/OPERATIONS.md` | 1 line | Remove "FAISS" from image-contents table cell |
| `docs/OBSERVABILITY-LEGACY.md`, `docs/TELEMETRY.md`, `docs/design/a2a-protocol-integration.md`, `docs/design/server-versioning.md`, `metrics-service/docs/api-reference.md`, `metrics-service/docs/database-schema.md`, `tests/README.md` | small each | Additional files surfaced during codebase analysis, not in the original 21-doc scan list; verify and remove any FAISS wording found |
| `tests/conftest.py` | ~20 | Remove `sys.modules["faiss"]` auto-mock and its import |
| `tests/unit/conftest.py` | ~14 | Delete unused `mock_faiss_service` fixture |
| `tests/integration/test_server_lifecycle.py` | ~15 | Delete `mock_faiss_service` fixture; remove from autouse chain |
| `tests/integration/test_telemetry_e2e.py` | ~5 | Update `search_backend` assertion to `"documentdb"` for both cases |
| `tests/integration/test_tool_level_access.py` | ~20 | Remove `fake_faiss`/patch blocks (4 occurrences) |
| `tests/unit/core/test_config.py` | ~30 | Delete `test_faiss_index_path`/`test_faiss_metadata_path` |
| `tests/unit/repositories/test_factory_aliases.py` | ~5 | Update `test_file_backend_behavior` assertion to match simplified factory behavior |
| `tests/unit/api/test_agent_routes.py`, `test_server_routes.py`, `test_skill_inline_content.py`, `test_agent_batch_item_processor.py` | small each | Remove `patch("registry.search.service.faiss_service", ...)` calls |
| `tests/unit/api/test_search_routes.py`, `test_search_routes_local_server.py`, `test_check_duplicates_endpoints.py`, `test_agent_routes_patch_batch.py`, `tests/unit/services/test_duplicate_check_service.py` | cosmetic each | Rename `faiss`-prefixed fixtures/variables/error strings; no logic change |
| `tests/unit/test_safe_eval_arithmetic.py` | ~13 | Evaluate whether the `sys.modules["faiss"]` `__spec__` workaround (needed for `transformers`'s `find_spec` probing) is still required once the mock module is removed; keep or replace as needed |

### Removed Files

| File Path | Description |
|-----------|-------------|
| `registry/search/service.py` | `FaissService` and module singleton |
| `registry/repositories/file/search_repository.py` | `FaissSearchRepository` |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS mock module |
| `tests/unit/search/test_faiss_service.py` | 64 FAISS-specific unit tests |
| `tests/integration/test_search_integration.py` | Fully-skipped, FAISS-only integration test file with no salvageable DocumentDB coverage (see Testing Strategy for the replacement plan) |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~40 (replacement `get_search_repository()` calls across routes/CLI) |
| New tests | ~150 (replacement search-repository integration test; see Testing Strategy) |
| Modified code | ~350 |
| Deleted code (source + tests) | ~1,900 (service.py ~1,200 + file/search_repository.py ~140 + test_faiss_service.py ~500 + mock_faiss.py ~255, minus overlaps) |
| Documentation edits | ~25 files touched, mostly small diffs; 3 files substantially rewritten |
| **Total (excl. doc line count)** | **~540** |

## Testing Strategy
See `testing.md` for the full plan. In summary: (1) grep-based assertions that no `faiss` import/reference remains in source; (2) backwards-compatibility tests confirming `/api/search/semantic` and `/api/search/tags` responses are unchanged in shape; (3) a new `tests/unit/repositories/test_documentdb_search_repository.py`-style black-box test suite covering `initialize`/`index_server`/`index_agent`/`remove_entity`/`search` end-to-end against `DocumentDBSearchRepository` (closing the coverage gap identified during codebase analysis, since today only its private RRF/distribution helper functions are unit-tested, not the class as a whole); (4) full regression run of the existing test suite after all FAISS mocking is removed from `conftest.py`.

## Alternatives Considered

### Alternative 1: Keep `storage_backend` default as `"file"`, add a DocumentDB-only search fallback for file-backend deployments
**Description:** Instead of changing the default, keep `file` as the default entity-storage backend but make `get_search_repository()` always connect to DocumentDB/MongoDB for search regardless of the entity-storage choice, requiring every deployment to have MongoDB connection settings configured even in "file" mode.
**Pros:** No default-value change, smaller diff to `.env.example`/`docker-compose*.yml`.
**Cons:** Forces every file-backend deployment (including simple local dev setups that chose `file` specifically to avoid running MongoDB) to stand up a MongoDB/DocumentDB connection just for search, which is a worse experience than today and contradicts the "simple, no external dependencies" pitch of the file backend documented in `docs/configuration.md`.
**Why Rejected:** `docs/configuration.md` already deprecates the file backend and recommends `mongodb-ce` for local dev; changing the default aligns behavior with what the docs already tell users to do, and Docker Compose already bundles a zero-config `mongodb`/`mongodb-init` service, so the operational cost of the new default is low.

### Alternative 2: Implement a brute-force in-memory cosine-similarity search for the file backend instead of removing FAISS's role entirely
**Description:** Replace `FaissSearchRepository` with a new lightweight repository that loads all entities into memory and computes cosine similarity with `registry/utils/vector.py::cosine_similarity` (already used by DocumentDB's own client-side fallback), avoiding any FAISS dependency while still supporting file-backend-only deployments without MongoDB.
**Pros:** Preserves a fully standalone file-backend deployment option with zero external dependencies.
**Cons:** Reintroduces a second, parallel search implementation — exactly the maintenance burden this issue exists to eliminate — and would need its own hybrid/keyword-fusion logic to match feature parity with DocumentDB's RRF-based search, which is nontrivial net-new code for a backend that is already documented as deprecated.
**Why Rejected:** Contradicts the task's explicit direction to "replace any remaining vector-search needs with the maintained DocumentDB hybrid search alternative already used elsewhere in the repo," and duplicates effort for a deprecated code path.

### Comparison Matrix

| Criteria | Chosen (change default) | Alt 1 (keep default, force Mongo) | Alt 2 (in-memory cosine) |
|----------|--------------------------|-------------------------------------|----------------------------|
| Aligns with existing deprecation docs | Yes | Partial | No |
| New code required | None | None | ~200-300 lines |
| Operational simplicity for new installs | High (bundled Mongo) | Low (forced dependency, same default) | High but duplicates FAISS's role |
| Risk of regressing existing file-backend users | Low (docs already steer away from `file`) | Medium (silent new hard dependency) | Low |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill).
- Phase 2: Testing — run the full plan in `testing.md`, including the backwards-compatibility suite against both `mongodb-ce` and `documentdb` storage backends.
- Phase 3: Deployment — since `STORAGE_BACKEND` default changes, document this explicitly in release notes as a behavior change for anyone relying on the implicit `file` default with no `STORAGE_BACKEND` env var set; existing deployments that explicitly set `STORAGE_BACKEND=file` are unaffected by the default change but will need MongoDB/DocumentDB connectivity for search once `get_search_repository()` no longer supports a file-only search path.

## Open Questions
- Should `docs/faq/configuring-mongodb-atlas-backend.md`'s historical callout (describing a past release's silent fallback to "the local file/FAISS backend") be reworded or left as-is, since it describes a past bug rather than current behavior? This design leaves it untouched by default; a documentation reviewer can decide.
- Should the `search_backend` telemetry field be removed entirely in a future release once enough deployments have reported `"documentdb"`, rather than kept indefinitely as a constant? Out of scope for this issue; flagged for a follow-up.

## References
- `docs/design/hybrid-search-architecture.md` — authoritative description of the target DocumentDB hybrid search architecture; already accurate and requires no changes.
- `docs/configuration.md` — existing deprecation notice for the file/FAISS backend that motivates the `storage_backend` default change.
- Upstream issue: https://github.com/agentic-community/mcp-gateway-registry/issues/1285
