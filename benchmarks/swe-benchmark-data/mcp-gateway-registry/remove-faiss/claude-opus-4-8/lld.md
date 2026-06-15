# Low-Level Design: Remove FAISS and Consolidate Search

*Created: 2026-06-15*
*Author: Claude (claude-opus-4-8)*
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
FAISS (`faiss-cpu`) is the search engine for the default `file` storage backend only. It is
implemented as a ~1200-line singleton, `FaissService`, in `registry/search/service.py`, exposed
through `registry/repositories/file/search_repository.py` (`FaissSearchRepository`). The MongoDB /
DocumentDB backends use a separate, maintained hybrid-search implementation
(`registry/repositories/documentdb/search_repository.py`).

Two structural problems make FAISS obsolete:

1. **Abstraction leak / dual-write.** The HTTP write handlers
   (`registry/api/server_routes.py`, `registry/api/agent_routes.py`) and
   `registry/services/agent_batch_item_processor.py` import the `faiss_service` singleton
   **directly** (`from ..search.service import faiss_service`) and call it alongside the
   repository abstraction. So every server/agent mutation writes to FAISS *and* to
   `get_search_repository()`. The read path (`registry/api/search_routes.py`) and startup
   (`registry/main.py`) already go through `get_search_repository()` exclusively.
2. **Redundant capability.** The DocumentDB backend's MongoDB-CE fallback (`_client_side_search`)
   already performs pure-Python semantic search using `registry/utils/vector.py:cosine_similarity`
   and the provider-agnostic embeddings client (`registry/embeddings/client.py`). FAISS adds a heavy
   native wheel for a capability the codebase can already deliver without it.

### Goals
- Remove the `faiss-cpu` dependency and every `import faiss` from the codebase.
- Delete the `FaissService` singleton and the `.faiss` / `service_index_metadata.json` artifacts.
- Keep `STORAGE_BACKEND=file` (the historical default) fully functional for search by introducing a
  small in-memory file search repository that reuses the embeddings client and `cosine_similarity`.
- Route all search writes through `get_search_repository()`; no handler imports `registry.search`.
- Update all config, Terraform, Docker, CLI, docs, and tests accordingly.

### Non-Goals
- No change to the embeddings provider/model/dimensions or to `EMBEDDINGS_*` settings.
- No change to the DocumentDB/MongoDB search algorithm or to the `/api/search` request/response
  contract.
- No data migration (the file backend re-indexes from source documents on startup).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `registry/search/service.py` | `FaissService` singleton (`faiss_service`), ~1200 lines: index load/save, add/update/remove, `search_mixed`, keyword boost | **Deleted.** Heart of FAISS coupling. |
| `registry/repositories/file/search_repository.py` | `FaissSearchRepository` wraps `faiss_service` behind `SearchRepositoryBase` | **Rewritten** as `FileSearchRepository` with no FAISS. |
| `registry/repositories/documentdb/search_repository.py` | Maintained hybrid search (vector + keyword + RRF; HNSW or client-side cosine fallback) | **Unchanged.** Reference implementation and the "maintained alternative." |
| `registry/repositories/factory.py` | `get_search_repository()` chooses backend; line 147-149 imports `FaissSearchRepository` | **Edited** to import `FileSearchRepository`. |
| `registry/repositories/interfaces.py` | `SearchRepositoryBase` ABC; docstring mentions FAISS | **Edited** (docstring wording only). |
| `registry/embeddings/client.py`, `registry/embeddings/__init__.py` | Provider-agnostic embeddings (`create_embeddings_client`) | **Unchanged.** Reused by the new file repo. |
| `registry/utils/vector.py` | `cosine_similarity(a, b)` | **Unchanged.** Reused by the new file repo. |
| `registry/utils/metadata.py` | `flatten_metadata_to_text` | **Unchanged.** Optionally reused for keyword text. |
| `registry/core/config.py` | `faiss_index_path`, `faiss_metadata_path` properties (lines 996-1001) | **Edited** (properties removed). |
| `registry/core/schemas.py` | `FaissMetadata` Pydantic model (line 505) | **Removed** (no runtime importers found). |
| `registry/core/telemetry.py` | `search_backend = ... else "faiss"` (line 730-731) | **Edited** to a backend-accurate label. |
| `registry/api/server_routes.py` | 12 direct `faiss_service` call sites (toggle/register/update/delete/bulk) | **Edited** to use `get_search_repository()`. |
| `registry/api/agent_routes.py` | 4 direct `faiss_service` call sites | **Edited** to use `get_search_repository()`. |
| `registry/services/agent_batch_item_processor.py` | 2 direct `faiss_service` call sites | **Edited** to use `get_search_repository()`. |
| `registry/api/search_routes.py` | Read path; already repo-based; log string says "FAISS search service unavailable" (line 440) | **Edited** (log wording only). |
| `registry/main.py` | Startup init; already repo-based; `backend_name` label + comments mention FAISS (lines ~497-540) | **Edited** (labels/comments; logic stays). |
| `pyproject.toml` / `uv.lock` | `faiss-cpu>=1.7.4` (line 23) | **Edited / regenerated.** |
| `build_and_run.sh`, `cli/service_mgmt.sh`, `terraform/aws-ecs/scripts/service_mgmt.sh` | `.faiss` file checks, `verify_faiss_metadata()` | **Edited** (remove FAISS file handling). |
| `build-config.yaml` | Comments mention FAISS | **Edited** (comments). |
| `scripts/migrate-file-to-mongodb.py` | Excludes `*.faiss` / `service_index_metadata.json` from migration (lines 216-220) | **Edited** (exclusion list simplified). |
| Tests: `tests/fixtures/mocks/mock_faiss.py`, `tests/conftest.py`, `tests/unit/conftest.py`, `tests/unit/search/test_faiss_service.py`, `tests/unit/core/test_config.py`, `tests/test_infrastructure.py`, `tests/unit/test_safe_eval_arithmetic.py` | FAISS mocks/fixtures/tests | **Removed/rewritten** (see Testing Strategy). |
| Docs: `docs/embeddings.md`, `registry/embeddings/README.md`, `docs/configuration.md`, `docs/database-design.md`, `docs/TELEMETRY.md`, `docs/dynamic-tool-discovery.md`, `docs/design/*.md`, `docs/testing/*.md`, `docs/OBSERVABILITY-LEGACY.md`, `release-notes/v1.0.17.md`, `cli/agent_mgmt.py` help text, `api/registry_client.py` docstring, `registry/servers/mcpgw.json` descriptions | FAISS mentions | **Edited** (wording). |
| `metrics-service/**` | `faiss_search_time_ms` column/param in stored metrics schema | **Optional cleanup** (see Open Questions); not on the critical path. |

### Existing Patterns Identified
1. **Repository pattern via factory.** `registry/repositories/factory.py` selects a concrete
   repository per `settings.storage_backend`: `MONGODB_BACKENDS` -> `documentdb/*`, else `file/*`.
   - Files: `factory.py`, `interfaces.py`, `file/*.py`, `documentdb/*.py`
   - How to follow: the new `FileSearchRepository` must subclass `SearchRepositoryBase` and be the
     `else` branch in `get_search_repository()`. Handlers must never import a concrete repo or the
     search service; they call `get_search_repository()`.
2. **Lazy, latched embeddings.** `DocumentDBSearchRepository._embed_texts()` funnels every encode
   call through one lazily-created client and latches `_embedding_unavailable` on failure so search
   degrades to lexical-only instead of crashing.
   - How to follow: the new file repo reuses `create_embeddings_client(...)` with the same settings
     and the same lazy+latch pattern, falling back to keyword-only scoring when embeddings fail.
3. **Client-side cosine ranking.** `DocumentDBSearchRepository._client_side_search()` fetches all
   docs, computes `cosine_similarity(query_embedding, doc_embedding)`, and merges with a keyword
   `text_boost`. This is exactly the algorithm the file backend needs (its corpus is already fully
   in memory), minus MongoDB I/O.
4. **Grouped result schema.** All search methods return
   `{"servers", "tools", "agents", "skills", "virtual_servers"}` lists of dicts with
   `relevance_score`, `match_context`, `matching_tools`, etc. The new repo must emit the same shape.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `get_search_repository()` (factory) | Depends on | Returns `FileSearchRepository` for the `file` backend. |
| `registry/main.py` startup | Uses | Calls `search_repo.initialize()` then `index_server/agent/skill` for the file backend (already backend-gated; logic unchanged). |
| `server_routes.py`, `agent_routes.py`, `agent_batch_item_processor.py` | Replaces | Direct `faiss_service.*` calls become `get_search_repository().index_*/remove_entity`. |
| `search_routes.py` read path | Uses | Already calls `search_repo.search/search_by_tags/get_all_tags`; no logic change. |
| Embeddings client + `cosine_similarity` | Uses | Reused verbatim by the new file repo. |

### Constraints and Limitations Discovered
- **`file` backend must keep working.** `STORAGE_BACKEND` defaults to `file`
  (`config.py:_validate_storage_backend`). We cannot simply delete file-backend search; we must
  replace it with a non-FAISS implementation.
- **In-memory only is acceptable for `file`.** FAISS itself was in-memory and rebuilt from source on
  every boot (`main.py` only re-indexes for non-Mongo backends). The replacement keeps the same
  lifecycle: rebuild on startup, hold in a dict, no persisted index file.
- **Write paths currently dual-write.** Removing the direct `faiss_service` calls must not drop the
  index update; each removed call must be replaced by the equivalent `get_search_repository()` call.
  Note `server_routes.py` lines 851-854 already call `search_repo.index_server(...)` right after the
  FAISS call, so several sites only need the FAISS line deleted, not replaced.
- **`FaissMetadata` schema has no importers.** `grep` finds the class definition only; safe to
  delete. (Implementer must re-confirm with a fresh `grep` before deleting.)
- **`metrics_service` keeps historical rows.** `faiss_search_time_ms` is a stored SQLite column in a
  separate service. Dropping a column from a persisted schema is a data-migration concern; treat it
  as optional and out of the critical path (see Open Questions).

## Architecture

### System Context Diagram

```
                         /api/search (read)            register/update/delete/toggle (write)
                                |                                   |
                                v                                   v
                       search_routes.py                  server_routes.py / agent_routes.py
                                |                          agent_batch_item_processor.py
                                |                                   |
                                +-------------+      +--------------+
                                              v      v
                                   get_search_repository()   <-- factory, chooses by STORAGE_BACKEND
                                              |
              +-------------------------------+-------------------------------+
              | STORAGE_BACKEND in MONGODB_BACKENDS        else (file)        |
              v                                            v
   DocumentDBSearchRepository                     FileSearchRepository  (NEW, no FAISS)
   (vector + keyword + RRF;                       (in-memory corpus;
    HNSW or client-side cosine)                    cosine_similarity + keyword boost)
              |                                            |
              v                                            v
        embeddings client  <----------- shared ----------> embeddings client
        cosine_similarity  <----------- shared ----------> cosine_similarity
```

Before this change, the write handlers had a second arrow going directly to the `faiss_service`
singleton in `registry/search/service.py`. That module and that arrow are deleted.

### Sequence Diagram - Server registration (write path), after change

```
Client -> server_routes.register: POST /register
server_routes -> server_service.create/save server
server_routes -> get_search_repository(): repo            # was: from ..search.service import faiss_service
server_routes -> repo.index_server(path, info, enabled)   # was: faiss_service.add_or_update_service(...)
repo -> embeddings client.encode([text])                  # lazy, latched
repo -> (file) store doc + embedding in in-memory dict    # (mongo) upsert to collection
server_routes -> Client: 200 OK
```

### Sequence Diagram - Search (read path), unchanged

```
Client -> search_routes.search: POST /api/search
search_routes -> get_search_repository(): repo
search_routes -> repo.search(query, entity_types, max_results, ...)
repo -> embeddings client.encode([query])
repo -> rank by cosine_similarity + keyword boost
repo -> search_routes: grouped results
search_routes -> Client: filtered, access-checked results
```

### Component Diagram - new FileSearchRepository

```
FileSearchRepository(SearchRepositoryBase)
  - _docs: dict[str, dict]              # path -> {entity_type, name, description, tags,
  - _embedding_model | None            #          tools, metadata, embedding: list[float], is_enabled}
  - _embedding_unavailable: bool
  - _get_embedding_model()             # lazy create_embeddings_client(...)
  - _embed_texts(texts, *, context, latch_unavailable)   # mirrors DocumentDB funnel
  - initialize()                       # no-op / clear dict
  - index_server / index_agent / index_skill / index_virtual_server
  - remove_entity(path)
  - search(query, ...)                 # cosine + keyword, reuses scoring helpers
  - search_by_tags(tags, ...)          # exact tag filter over _docs
  - get_all_tags()
```

## Data Models

### New Models
No new public Pydantic models are introduced. The in-memory document dict mirrors the shape the
DocumentDB repository builds (`_id`/`path`, `entity_type`, `name`, `description`, `tags`,
`metadata_text`, `tools`, `embedding`, `is_enabled`, `status`), kept private to the repository.

### Model Changes
- **Remove** `FaissMetadata` (`registry/core/schemas.py:505`). Confirm zero importers first.

## API / CLI Design

### New Endpoints / Commands
None. This is a pure refactor.

### Modified Behavior (no contract change)
- `POST /api/search`, tag search, and `GET /api/tags` keep their request/response schema for every
  backend. For the `file` backend the engine changes from FAISS to in-memory cosine + keyword, but
  the response fields (`relevance_score`, `match_context`, `matching_tools`, grouped lists) are
  preserved.

**Invocation (unchanged):**
```bash
curl -sS -X POST "$REGISTRY_URL/api/search" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "time and weather", "entity_types": ["mcp_server","tool"], "max_results": 5}'
```

**Error Cases (unchanged):**
- 400: empty query and no tags.
- 503: search engine unavailable (the existing handler in `search_routes.py`; only the log wording
  changes from "FAISS search service unavailable" to "Search service unavailable").

## Configuration Parameters

### New Environment Variables
None.

### Removed / Changed
- **Remove** `Settings.faiss_index_path` and `Settings.faiss_metadata_path` properties
  (`config.py:996-1001`).
- No env vars are removed: FAISS was configured by derived `Path` properties, not by env vars.
  `STORAGE_BACKEND`, `EMBEDDINGS_*`, `SEARCH_FUSION_METHOD`, and `VECTOR_SEARCH_EF_SEARCH` are
  untouched (the latter two only affect the MongoDB backend).

### Deployment Surface Checklist
A future implementer must touch every surface that references the FAISS index files or the
`faiss-cpu` dependency:
- [ ] `pyproject.toml` (remove `faiss-cpu`)
- [ ] `uv.lock` (regenerate: `uv lock`)
- [ ] `build_and_run.sh` (`FAISS_FILES` array + startup file checks, lines ~245, 624-628)
- [ ] `cli/service_mgmt.sh` (`verify_faiss_metadata`, lines ~166-191, 613, 705)
- [ ] `terraform/aws-ecs/scripts/service_mgmt.sh` (`verify_faiss_metadata`, lines ~184-191, 631, 723)
- [ ] `build-config.yaml` (comment text, lines ~25, 30)
- [ ] `scripts/migrate-file-to-mongodb.py` (exclusion list, lines ~216-220)
- [ ] No Docker `docker-compose*.yml` change needed (no FAISS volume mounts exist; confirmed).

## New Dependencies
This change uses only existing dependencies. It **removes** `faiss-cpu`. The replacement relies on
`numpy` (already present, used by the embeddings client) and the standard-library `math` in
`registry/utils/vector.py`.

| Package | Action | Reason |
|---------|--------|--------|
| `faiss-cpu` | **Remove** | Obsolete; capability covered by embeddings client + `cosine_similarity`. |

## Implementation Details

### Step-by-Step Plan (for a future implementer)

Work in this order so the build is never left half-broken; run
`uv run python -m py_compile <file>` after each Python edit and `bash -n <file>` after each shell
edit (per CLAUDE.md).

#### Step 1: Add the new file search repository
**File:** `registry/repositories/file/search_repository.py` (rewrite in place)

Replace the `FaissSearchRepository` (which wraps `faiss_service`) with a self-contained
`FileSearchRepository`. Reuse the DocumentDB repo's scoring approach but over an in-memory dict.
Keep functions short (<= 50 lines), private helpers prefixed `_`, modern type hints (PEP 604/585).

```python
"""In-memory file-backend search repository (embeddings + keyword, no FAISS)."""

import logging
import re
from typing import Any

from ...core.config import settings
from ...schemas.agent_models import AgentCard
from ...utils.metadata import flatten_metadata_to_text
from ...utils.vector import cosine_similarity
from ..interfaces import SearchRepositoryBase

logger = logging.getLogger(__name__)


class FileSearchRepository(SearchRepositoryBase):
    """File-backend search: in-memory corpus ranked by cosine similarity + keyword boost.

    Mirrors DocumentDBSearchRepository's client-side ranking, but the corpus
    lives in a process-local dict rebuilt from source documents on startup
    (registry/main.py), exactly as the former FAISS index did.
    """

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}
        self._embedding_model = None
        self._embedding_unavailable: bool = False

    async def initialize(self) -> None:
        """Reset the in-memory corpus. Startup re-indexing repopulates it."""
        self._docs.clear()

    def _get_embedding_model(self):
        if self._embedding_model is None:
            from ...embeddings import create_embeddings_client

            self._embedding_model = create_embeddings_client(
                provider=settings.embeddings_provider,
                model_name=settings.embeddings_model_name,
                model_dir=settings.embeddings_model_dir,
                api_key=settings.embeddings_api_key,
                api_base=settings.embeddings_api_base,
                aws_region=settings.embeddings_aws_region,
                embedding_dimension=settings.embeddings_model_dimensions,
            )
        return self._embedding_model

    async def _embed_texts(
        self,
        texts: list[str],
        *,
        context: str,
        latch_unavailable: bool = True,
    ) -> list[list[float]] | None:
        """Single funnel for all encode calls (mirror of the DocumentDB repo)."""
        if latch_unavailable and self._embedding_unavailable:
            return None
        try:
            model = self._get_embedding_model()
            vectors = model.encode(texts)
            return [v.tolist() for v in vectors]
        except Exception as exc:
            logger.warning("Embedding model unavailable for %s: %s", context, exc)
            if latch_unavailable:
                self._embedding_unavailable = True
            return None
```

The indexing methods build the same doc shape the DocumentDB repo builds (compose
`text_for_embedding`, call `_embed_texts([...], latch_unavailable=False)`, store the doc with its
embedding in `self._docs[path]`). The search method tokenizes the query, computes
`cosine_similarity(query_embedding, doc["embedding"])` per doc, adds a keyword `text_boost`, and
formats grouped results.

To avoid duplicating ~300 lines of scoring/formatting, **extract the shared, backend-agnostic
helpers** (`_tokenize_query`, `_score_tool_relevance`, `_distribute_results`,
`_reciprocal_rank_fusion`, `_normalize_scores`, `_tool_extraction_limit`, and the grouped-result
formatter) into a new module `registry/repositories/search_scoring.py` and import them from both the
DocumentDB repo and the new file repo. This keeps both backends consistent and DRY (see Step 1a).

#### Step 1a: Extract shared scoring helpers (recommended)
**File:** `registry/repositories/search_scoring.py` (new)

Move the pure functions listed above out of `documentdb/search_repository.py` into this module and
import them back. They have no MongoDB dependency. The file repo then ranks identically to the
MongoDB-CE client-side path. If the implementer prefers a smaller diff, the file repo may inline a
minimal cosine+keyword ranker instead, but the response shape must match exactly.

#### Step 2: Point the factory at the new repository
**File:** `registry/repositories/factory.py` (lines ~146-149)

```python
    else:
        from .file.search_repository import FileSearchRepository

        _search_repo = FileSearchRepository()
```

#### Step 3: Rewrite write paths to use the repository
**Files:** `registry/api/server_routes.py`, `registry/api/agent_routes.py`,
`registry/services/agent_batch_item_processor.py`

For each site (server_routes lines 774/847, 1113/1343, 1413/1643, 1716/1752, 1769/1827, 1876/1949,
2100/2386, 2502/2630, 2675/2760, 3504/3808, 3989/4041, 4100/4178; agent_routes 628/631, 1150/1152,
1598/1601, 1853/1855; processor 225/228, 338/340):

- Delete `from ..search.service import faiss_service`.
- Replace `faiss_service.add_or_update_service(path, info, enabled)` and
  `faiss_service.add_or_update_entity(path, data, entity_type, enabled)` with
  `await get_search_repository().index_server(...)` / `index_agent(...)` as appropriate.
- Replace `faiss_service.remove_service(path)` / `faiss_service.remove_entity(path)` with
  `await get_search_repository().remove_entity(path)`.
- Delete `asyncio.create_task(faiss_service.save_data())` (server_routes:3808) - the new repo has no
  persisted index, so there is nothing to save.
- **De-duplicate:** several sites already call `get_search_repository().index_server(...)` right
  after the FAISS call (e.g. server_routes:851-854). There, just delete the FAISS line; do not add a
  second repo call.

```python
# Before
from ..search.service import faiss_service
...
await faiss_service.add_or_update_service(service_path, server_info, new_state)
from ..repositories.factory import get_search_repository
search_repo = get_search_repository()
await search_repo.index_server(service_path, server_info, new_state)

# After
from ..repositories.factory import get_search_repository
...
search_repo = get_search_repository()
await search_repo.index_server(service_path, server_info, new_state)
```

`index_agent` expects an `AgentCard`; the batch processor currently passes `card.model_dump()` to
`add_or_update_entity`. Pass the `AgentCard` object to `index_agent` instead (matching the
`SearchRepositoryBase` signature and the DocumentDB implementation).

#### Step 4: Delete the FAISS service
**File:** `registry/search/service.py` -> delete. Also delete `registry/search/__init__.py` if the
package becomes empty and nothing else imports `registry.search` (re-`grep` first).

#### Step 5: Remove FAISS config and schema
**Files:** `registry/core/config.py` (delete `faiss_index_path`, `faiss_metadata_path`, lines
996-1001), `registry/core/schemas.py` (delete `FaissMetadata`, line ~505 after confirming no
importers).

#### Step 6: Fix labels, comments, and telemetry
**Files:** `registry/main.py` (rename `backend_name` "FAISS" -> "in-memory" and update the comment at
~lines 504-505), `registry/api/search_routes.py:440` (log wording), `registry/core/telemetry.py:731`
(`search_backend` value: use `"file"` for the file backend rather than `"faiss"`; keep `"documentdb"`
for Mongo backends).

#### Step 7: Dependencies
**Files:** `pyproject.toml` (remove `"faiss-cpu>=1.7.4"`), then `uv lock` to regenerate `uv.lock`.

#### Step 8: Shell / Terraform / build config
**Files:** `build_and_run.sh` (remove `FAISS_FILES` and the post-startup `.faiss` existence checks),
`cli/service_mgmt.sh` and `terraform/aws-ecs/scripts/service_mgmt.sh` (remove `verify_faiss_metadata`
function and its call sites), `build-config.yaml` (comment text),
`scripts/migrate-file-to-mongodb.py` (drop `.faiss`/`service_index_metadata.json` from the exclusion
set, keeping `server_state.json`). Run `bash -n` on each shell script.

#### Step 9: Docs and help text
Update every file in the Codebase Analysis docs row to describe the unified search engine. Notably:
`docs/embeddings.md`, `registry/embeddings/README.md`, `docs/configuration.md`,
`docs/database-design.md`, `docs/TELEMETRY.md` (search_backend values), `docs/dynamic-tool-discovery.md`
(replace FAISS code example), `docs/design/*.md`, `cli/agent_mgmt.py` help text,
`api/registry_client.py` docstring (line 2617), and `registry/servers/mcpgw.json` tool descriptions
(lines 197, 199, 226) - reword "FAISS search" to "semantic search" without changing JSON keys.

#### Step 10: Tests
See Testing Strategy. Delete `tests/unit/search/test_faiss_service.py` and
`tests/fixtures/mocks/mock_faiss.py`; remove the auto-mock of `sys.modules["faiss"]` from
`tests/conftest.py`; drop FAISS path tests from `tests/unit/core/test_config.py`; update
`tests/test_infrastructure.py` and `tests/unit/test_safe_eval_arithmetic.py`; add a new
`tests/unit/search/test_file_search_repository.py`.

### Error Handling
- Embedding failures degrade gracefully: `_embed_texts` returns `None` and search falls back to
  keyword-only scoring (mirrors the DocumentDB repo). The 503 path in `search_routes.py` still
  catches `RuntimeError`.
- `index_*` failures are logged and swallowed per-entity during startup re-index (existing
  `main.py` try/except per item), so one bad document does not abort boot.

### Logging
- Keep INFO logs on index rebuild counts and search result counts, matching the existing style.
- Remove FAISS-specific debug logs (normalization norm checks, FAISS ID assignment).
- Use the standard logging format from CLAUDE.md; no emojis in new log strings (the existing emoji
  log lines in `main.py` may be left as-is to minimize churn, but new lines must be plain text).

## Observability
- **Telemetry:** `search_backend` tag changes its file-backend value from `"faiss"` to `"file"`.
  Document this in `docs/TELEMETRY.md`. Any dashboard filtering on `search_backend="faiss"` must be
  updated by operators (note in release notes).
- **Metrics:** the `metrics-service` `faiss_search_time_ms` field stops being populated by the
  registry. Leaving the column in place (always null going forward) is the low-risk default; see
  Open Questions for the optional rename/removal.

## Scaling Considerations
- The `file` backend is intended for small/dev deployments; its corpus already fit entirely in the
  FAISS in-memory index. The replacement holds the same data in a Python dict and does an O(N)
  cosine scan per query - identical asymptotics to the MongoDB-CE client-side fallback that already
  ships. For the registry sizes the file backend targets (tens to low-hundreds of entities) this is
  well within budget.
- Production/large deployments use a MongoDB-compatible backend with HNSW vector indexing, which is
  unaffected by this change.
- No new caching is introduced; the embedding model is loaded lazily once per process, as before.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `registry/repositories/search_scoring.py` | Shared backend-agnostic scoring/formatting helpers (recommended extraction). |
| `tests/unit/search/test_file_search_repository.py` | Unit tests for the new file search repository. |

### Modified Files

| File Path | Lines (approx) | Change Description |
|-----------|----------------|--------------------|
| `registry/repositories/file/search_repository.py` | ~137 -> ~250 | Rewritten as `FileSearchRepository` (no FAISS). |
| `registry/repositories/factory.py` | ~3 | Import/instantiate `FileSearchRepository`. |
| `registry/repositories/documentdb/search_repository.py` | ~20 | Import shared helpers from `search_scoring.py` (if Step 1a taken). |
| `registry/repositories/interfaces.py` | ~1 | Docstring wording (drop "FAISS"). |
| `registry/api/server_routes.py` | ~24 | Remove 12 FAISS imports/calls; keep/route through repo. |
| `registry/api/agent_routes.py` | ~8 | Remove 4 FAISS imports/calls. |
| `registry/services/agent_batch_item_processor.py` | ~4 | Remove 2 FAISS imports/calls. |
| `registry/api/search_routes.py` | ~1 | Log wording. |
| `registry/main.py` | ~5 | Label/comment wording; logic unchanged. |
| `registry/core/config.py` | -6 | Remove 2 FAISS path properties. |
| `registry/core/schemas.py` | -~10 | Remove `FaissMetadata`. |
| `registry/core/telemetry.py` | ~2 | `search_backend` value. |
| `pyproject.toml` | -1 | Remove `faiss-cpu`. |
| `uv.lock` | regen | `uv lock`. |
| `build_and_run.sh` | ~8 | Remove FAISS file array + checks. |
| `cli/service_mgmt.sh` | ~10 | Remove `verify_faiss_metadata` + calls. |
| `terraform/aws-ecs/scripts/service_mgmt.sh` | ~10 | Remove `verify_faiss_metadata` + calls. |
| `build-config.yaml` | ~2 | Comment text. |
| `scripts/migrate-file-to-mongodb.py` | ~3 | Exclusion-list cleanup. |
| Docs (see analysis) | ~30 | Reword FAISS mentions. |
| `cli/agent_mgmt.py`, `api/registry_client.py`, `registry/servers/mcpgw.json` | ~5 | Reword FAISS mentions in help/docstrings/descriptions. |

### Deleted Files

| File Path | Description |
|-----------|-------------|
| `registry/search/service.py` | `FaissService` singleton. |
| `registry/search/__init__.py` | If package empties and is unused. |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS test mock. |
| `tests/unit/search/test_faiss_service.py` | FAISS service test suite. |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (`FileSearchRepository` + `search_scoring.py`) | ~350 |
| New tests | ~250 |
| Modified code (routes, factory, config, telemetry, scripts, docs) | ~120 |
| Deleted code (FAISS service + mock + test suite) | ~2600 |
| **Net total** | **~ -1900 (net deletion)** |

## Testing Strategy
See `./testing.md` for the full plan. Summary:
- Grep-based proof that no `faiss`/`faiss-cpu`/`import faiss` remains in production code, deps,
  config, Terraform, Docker, CLI, or docs.
- Functional curl tests for `/api/search`, tag search, and `/api/tags` under `STORAGE_BACKEND=file`
  asserting the unchanged response schema.
- Backwards-compat tests: MongoDB backend search behavior unchanged; file-backend search returns the
  same field set as before.
- Unit tests for `FileSearchRepository` (index, remove, cosine ranking, keyword fallback, tag
  search, empty corpus).
- Full `uv run pytest tests/ -n 8` with no regressions.

## Alternatives Considered

### Alternative 1: Drop the `file` backend entirely and require MongoDB
**Description:** Delete file-backend search and make a MongoDB-compatible backend mandatory.
**Pros:** Smallest code surface; one search path only.
**Cons:** Breaks the historical default (`STORAGE_BACKEND=file`) and every lightweight/dev deployment
that has no MongoDB. Large blast radius beyond search (all `file/*` repositories).
**Why Rejected:** Out of scope and a breaking change; the task is to remove FAISS, not the file
backend.

### Alternative 2: Keep file-backend search but back it with SQLite/`sqlite-vss` or `chromadb`
**Description:** Swap FAISS for another embedded vector store.
**Pros:** Persisted index; potentially faster for larger corpora.
**Cons:** Adds a new dependency - directly contradicts "replace with the maintained alternative
already used elsewhere." More moving parts than the file backend needs.
**Why Rejected:** Violates the no-new-dependency intent; the maintained alternative is the existing
embeddings + cosine path.

### Alternative 3: In-memory cosine + keyword reusing the embeddings client (CHOSEN)
**Description:** Replace FAISS with a dict-backed repository that ranks via `cosine_similarity` and a
keyword boost, reusing the exact helpers the MongoDB-CE fallback already uses.
**Pros:** No new dependency; one consistent ranking algorithm across backends; net code deletion;
preserves the `file` default and the API contract.
**Cons:** O(N) scan per query (acceptable at file-backend scale); embeddings still required for
semantic ranking (already true today).
**Why Rejected:** Not rejected - selected.

### Comparison Matrix

| Criteria | Alt 3 (Chosen) | Alt 1 | Alt 2 |
|----------|----------------|-------|-------|
| New dependency | None | None | Yes |
| Breaking change | No | Yes | No |
| Code size | Net deletion | Largest deletion | Net addition |
| Consistency with Mongo path | High | N/A | Low |
| Risk | Low | High | Medium |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill) - follow Steps 1-10.
- Phase 2: Testing - run `./testing.md`; verify file and a MongoDB backend.
- Phase 3: Release notes - call out the `search_backend` telemetry value change
  (`faiss` -> `file`) and the dropped `faiss_search_time_ms` population.

## Open Questions
- **`metrics-service` `faiss_search_time_ms` column.** Keep (always null going forward), rename to a
  neutral `search_time_ms`, or drop with a migration? Recommendation: keep for now (zero migration
  risk) and track a separate cleanup ticket; the column is in a separate service's persisted schema.
- **`registry/search/` package.** Confirm nothing else imports `registry.search` before deleting the
  package `__init__.py`.
- **Emoji log lines in `main.py`.** Existing lines use emoji; CLAUDE.md forbids emoji in new logs.
  Leave existing lines untouched to minimize churn, or de-emoji as part of this change? Recommend
  leaving them to keep the diff focused on FAISS removal.

## References
- `registry/repositories/documentdb/search_repository.py` - the maintained hybrid search and the
  client-side cosine fallback that the file backend now mirrors.
- `registry/utils/vector.py`, `registry/embeddings/client.py` - the reused building blocks.
- `CLAUDE.md` - logging, Pydantic, type-hint, modularity, and security standards the implementation
  must satisfy.
