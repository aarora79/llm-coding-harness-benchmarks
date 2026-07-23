# Testing Plan: Remove FAISS from the codebase and documentation

*Created: 2026-07-23*
*Author: Claude*
*Status: Draft*

This plan verifies the corrected design in `./lld.md`: FAISS is deleted entirely, DocumentDB hybrid search becomes the single search path, all ~22 direct `faiss_service` call sites are migrated onto `SearchRepositoryBase`, and the `file` backend gets a `NullSearchRepository` (empty results plus a startup WARNING) rather than a hard raise.

There is no "result-parity vs FAISS" test group. FAISS is removed, so there is nothing to hold ranking parity against; DocumentDB fuses vector and keyword rankings with RRF, which deliberately differs from FAISS's multiplicative boost. The contract that must hold is **schema compatibility** on MongoDB-compatible backends (identical request/response shapes and `search_mode: "hybrid"`), plus **write-path integrity** (registration keeps updating the index) and **graceful degradation** on the `file` backend (empty results, a warning, no crash).

Tests follow the repository's existing pytest conventions: fixtures for shared data, `AsyncMock` search repositories injected as the DI dependency, `sys.modules` mocking of embeddings/LiteLLM in `conftest.py`, and the AAA (Arrange-Act-Assert) pattern. Crucially, the `faiss` auto-mock (`sys.modules["faiss"]` injection) is removed from `conftest.py`; no test may import a real or mocked `faiss`.

## Test Categories

1. Removal verification - no FAISS anywhere it must not be (CI guards)
2. Call-site migration - the ~22 `faiss_service` sites now go through the repository
3. Factory and startup behavior - backend selection, NullSearchRepository, no re-index
4. Backwards compatibility - API contract preserved on MongoDB backends
5. File-backend degradation - empty search, warning, no crash, storage still works
6. Write-path (indexing) integrity - register/toggle/delete update the DocumentDB index
7. Configuration - default flip, removed properties, validator fallback
8. Deployment surface - dependencies, Docker, compose env lines, Terraform, CLI scripts
9. Full-suite regression

---

## 1. Removal-Verification Tests

**Goal:** Enforce the acceptance criteria that FAISS is gone. These run as CI guards and are the cheapest, highest-signal checks.

| ID | Test | Method | Assert |
|----|------|--------|--------|
| R1 | No `import faiss` in source | `grep -rn "import faiss" registry/ tests/` | Zero matches |
| R2 | No `faiss_service` / FAISS symbols in source | `grep -rn "faiss_service\|FaissService\|FaissSearchRepository\|from .*search\.service" registry/` | Zero matches (this is the gate a bare `import faiss` grep misses) |
| R3 | `faiss-cpu` not a dependency | Parse `pyproject.toml`; `grep -n faiss uv.lock` | Not present in project deps or lockfile |
| R4 | `faiss` not importable and not needed | Run the suite in an environment where `faiss` is NOT installed and NOT mocked | Collection and all tests pass; `python -c "import faiss"` raises `ModuleNotFoundError` |
| R5 | Deleted files absent | Assert `registry/search/service.py`, `registry/repositories/file/search_repository.py`, `tests/fixtures/mocks/mock_faiss.py`, `tests/unit/search/test_faiss_service.py` do not exist | Files absent |
| R6 | conftest has no faiss mock | `grep -n faiss tests/conftest.py tests/unit/conftest.py` | Zero matches (no `create_mock_faiss_module`, no `sys.modules["faiss"]`, no `mock_faiss_service`) |
| R7 | No FAISS in infra/build (excluding history) | `grep -ri faiss` over `pyproject.toml`, `docker/`, `terraform/`, `docker-compose*.yml`, `build-config.yaml` | Zero matches, except the telemetry collector schema (keeps `faiss` during rollout) and the `faiss_search_time_ms` column if its migration is deferred; `release-notes/` excluded |
| R8 | `FaissMetadata` schema removed | `grep -rn FaissMetadata registry/ tests/` | Zero matches |

---

## 2. Call-Site Migration Tests (Step 0)

**Goal:** Prove every direct `faiss_service.*` production call now routes through `get_search_repository()`. This is the consensus blocker from `review.md`: 4/5 reviewers flagged the ~22 sites. Use an `AsyncMock` (or spy) search repository as the injected dependency and assert it is called.

| ID | Test | Act | Assert |
|----|------|-----|--------|
| M1 | Server register indexes via repo | `POST` register a server | `search_repo.index_server` awaited once with `(path, info, enabled)`; `faiss_service` not referenced |
| M2 | Server update re-indexes via repo | Update a server (site 1343, formerly FAISS-only) | `search_repo.index_server` awaited; result reflects new content |
| M3 | Server toggle re-indexes via repo | Toggle enabled/disabled (sites 1643/1752/1949) | `search_repo.index_server` awaited with the new `enabled` flag |
| M4 | Server refresh/batch re-index via repo | Trigger refresh paths (2386/2630/2760/4041) | `search_repo.index_server` awaited per server |
| M5 | Server delete removes via repo | Delete a server (1827, 4178) | `search_repo.remove_entity(path)` awaited once |
| M6 | `save_data` call removed | Inspect the former `save_data()` site (3808) | No `save_data` call remains; DocumentDB persists inline (NullSearchRepository no-ops) |
| M7 | Agent register/update index via repo | `POST`/`PUT` an agent (631, 1152, 1601) | `search_repo.index_agent` awaited once |
| M8 | Agent delete removes via repo | Delete an agent (1855) | `search_repo.remove_entity(path)` awaited once |
| M9 | Batch processor indexes via repo | Run `agent_batch_item_processor` add (228) and remove (340) | `index_agent` / `remove_entity` awaited per item; no `faiss_service` |
| M10 | Dual-write sites de-duplicated | Register (formerly FAISS at 847 + repo at 853-854) | Exactly one `index_server` call (the duplicate FAISS line is gone) |

---

## 3. Factory and Startup Behavior Tests

**Target:** `registry/repositories/factory.py::get_search_repository()` and `registry/main.py` startup.

| ID | Test | Assert |
|----|------|--------|
| F1 | MongoDB backend selects DocumentDB | `STORAGE_BACKEND` in `MONGODB_BACKENDS` -> `get_search_repository()` returns `DocumentDBSearchRepository` |
| F2 | File backend selects NullSearchRepository | `STORAGE_BACKEND=file` -> returns `NullSearchRepository` (NOT a raise, NOT DocumentDB) |
| F3 | Singleton caching preserved | Two calls return the same instance (`_search_repo` cache) |
| F4 | No `FaissSearchRepository` import path | Import `factory` with `faiss` absent from `sys.modules`; no `ImportError` |
| F5 | Startup does not re-index | Boot on a MongoDB backend; assert the FAISS re-index block is gone and only `search_repo.initialize()` runs |
| F6 | File backend boots (no crash) | Boot with `STORAGE_BACKEND=file`; lifespan completes; app serves requests |
| F7 | NullSearchRepository logs one warning | Capture logs on file-backend boot | WARNING present naming the mongodb-ce/documentdb alternatives; logged via `initialize()` |

---

## 4. Backwards-Compatibility Tests

**Target:** API contract on a MongoDB-compatible backend. Schema is preserved; ranking is not asserted byte-for-byte.

| ID | Test | Assert |
|----|------|--------|
| B1 | `POST /api/search/semantic` schema | Response has `query`, `search_mode: "hybrid"`, `servers`, `tools`, `agents`, `skills`, `virtual_servers`, and all `total_*` counts - identical keys to before |
| B2 | Result item shapes unchanged | `ServerSearchResult`/`ToolSearchResult`/`AgentSearchResult`/`SkillSearchResult`/`VirtualServerSearchResult` fields unchanged |
| B3 | 400 on empty query with no tags | Returns 400 with the existing message |
| B4 | 503 on repository/embedding failure | Returns 503 (log wording may change; status must not); no raw MongoDB URI in the log (scrubbed per `system_routes.py:213-222`) |
| B5 | `GET /api/search/tags` | Returns `{"tags": [...]}` shape unchanged |
| B6 | Search-quality smoke (not parity) | `scripts/evaluate_search.py` (or a marked integration test) on a fixed corpus shows relevant results at the top; assert no material regression, not exact ordering |

---

## 5. File-Backend Degradation Tests

**Target:** `NullSearchRepository` and the "keep file storage" scope.

| ID | Test | Act | Assert |
|----|------|-----|--------|
| N1 | Semantic search returns empty | file backend; `POST /api/search/semantic` | 200 with `{servers:[], tools:[], agents:[], skills:[], virtual_servers:[]}`, `search_mode: "hybrid"` |
| N2 | Tags returns empty | file backend; `GET /api/search/tags` | 200 with `{"tags": []}` |
| N3 | Indexing calls are no-ops | file backend; register a server | `NullSearchRepository.index_server` returns without error; no exception |
| N4 | File storage CRUD still works | file backend; register, get, list, delete a server via the non-search API | All succeed; server persisted to disk and retrievable (search is the only casualty) |
| N5 | No crash-loop | file backend; full lifespan startup then a request | App stays up; single warning; no repeated errors |

---

## 6. Write-Path (Indexing) Integrity Tests

**Goal:** On a MongoDB backend, mutations keep the DocumentDB index correct. This also closes the pre-existing bug where FAISS-only sites never updated DocumentDB (LLD Step 0). Use a spy repository recording `index_server`/`index_agent`/`remove_entity`.

| ID | Test | Act | Assert |
|----|------|-----|--------|
| I1 | Register -> searchable | Register a server; search for it | Found; exactly one `index_server` (no duplicate) |
| I2 | Toggle re-indexes | Toggle enabled/disabled | Search reflects new state; one index update |
| I3 | Update re-indexes | Change server metadata | Search returns updated content; one index update |
| I4 | Delete removes from index | Delete a server | Absent from search; one `remove_entity` |
| I5 | Agent lifecycle | Register/update/delete an agent | One index/remove per op; searchable/absent accordingly |
| I6 | Batch federation | Run `agent_batch_item_processor` on a batch | Each agent indexed once; removed agents dropped |
| I7 | No startup re-index needed | Restart the app | Previously registered entities still searchable (embeddings persist in DB; no `.faiss` file relied on) |

---

## 7. Configuration Tests

**Target:** `registry/core/config.py`.

| ID | Test | Assert |
|----|------|--------|
| G1 | Default backend flipped | With no env override, `settings.storage_backend == "mongodb-ce"` |
| G2 | Empty/None fallback fixed | `STORAGE_BACKEND=""` resolves to `mongodb-ce` (not `file`) via `_validate_storage_backend` |
| G3 | Unknown backend still fails fast | `STORAGE_BACKEND=bogus` raises `ValueError` listing the allowlist (unchanged behavior) |
| G4 | `file` still allowed for storage | `"file"` remains in `ALLOWED_STORAGE_BACKENDS` |
| G5 | FAISS path properties removed | `settings` has no `faiss_index_path` / `faiss_metadata_path`; `test_config.py::test_faiss_index_path` / `test_faiss_metadata_path` are deleted |
| G6 | Telemetry backend value | `search_backend` emits `documentdb` (or `none` for the null backend), never `faiss`; update `test_telemetry.py:343`, `test_telemetry_e2e.py:335`, `test_collector.py:458` |

---

## 8. Deployment-Surface Tests

**Goal:** Build and operational surface is correct after removal. The six compose env lines are load-bearing (LLD Deployment Surface Checklist).

| ID | Test | Assert |
|----|------|--------|
| D1 | Lockfile clean | `uv lock` regenerates; `grep faiss uv.lock` empty; `uv sync` succeeds |
| D2 | Container build succeeds | Image builds without the faiss native wheel; model bake step retained (serves DocumentDB embeddings) |
| D3 | No compose injects `file` | `grep -rn ":-file" docker-compose*.yml` empty (all six `STORAGE_BACKEND` lines flipped to `:-mongodb-ce`); CI guard |
| D4 | Default stack has working search | `docker-compose up` brings up `mongodb` + registry; `POST /api/search/semantic` returns hybrid results |
| D5 | CLI verify function removed | `grep -n verify_faiss_metadata cli/service_mgmt.sh terraform/aws-ecs/scripts/service_mgmt.sh` empty; scripts `bash -n` clean |
| D6 | Telemetry collector stays permissive | `terraform/telemetry-collector/lambda/collector/schemas.py` pattern still accepts `faiss` (rollout safety); NOT narrowed in this release |
| D7 | Terraform plan unaffected | `ecs-services.tf` comment-only change produces no resource diff |
| D8 | Entrypoint model check reworded | `docker/registry-entrypoint.sh` local-model check is generic (not FAISS-specific); still warns rather than hard-fails offline |

---

## 9. Full-Suite Regression

| ID | Test | Assert |
|----|------|--------|
| Z1 | `uv run pytest tests/` | All tests pass with no faiss mock present |
| Z2 | `uv run ruff check registry/ tests/` | Clean |
| Z3 | `uv run mypy registry/` | No new type errors (NullSearchRepository signatures match `SearchRepositoryBase`) |
| Z4 | `uv run bandit -r registry/` | Clean; no dangling `# nosec` from removed FAISS/test workarounds |
| Z5 | `mkdocs build` | Succeeds; no broken links after doc rewrites |
| Z6 | `openapi.json` regenerated | Regenerated from the app (not hand-edited); FAISS strings gone at former 3869/4420 |

---

## Test Data and Fixtures

- **`mock_search_repository` fixture** (`tests/conftest.py` 371-386): the standard `AsyncMock` search repo injected via `Depends(get_search_repo)`. Add `search_by_tags` / `get_all_tags` so the tags endpoint can be exercised. This replaces every `patch("registry.search.service.faiss_service")`.
- **Repository spy:** a thin wrapper recording `index_server` / `index_agent` / `remove_entity` calls for the write-path integrity tests (I1-I7) and call-site migration tests (M1-M10).
- **Deterministic embeddings mock:** the existing `conftest.py` embeddings mock so tests are reproducible and never download models. For B6 search-quality smoke, use the real `all-MiniLM-L6-v2` model in a marked slow/integration test against a live MongoDB-compatible backend.
- **No `faiss` fixtures:** delete `tests/fixtures/mocks/mock_faiss.py`, the `sys.modules["faiss"]` injection, and the `mock_faiss_service` fixture. Their absence is asserted by R5/R6.

## Test Environment

- Python 3.10+ per `requires-python`.
- Run the suite with `faiss` NOT installed and NOT mocked (R4) to prove independence.
- Primary functional suite runs against mocked repositories (fast, no DB). A smoke run against a live MongoDB-compatible backend (docker-compose `mongodb` service) confirms the DocumentDB path (B6, I-series) and that the default stack search works (D4).
- A dedicated `STORAGE_BACKEND=file` run exercises the NullSearchRepository degradation path (Section 5).

## Exit Criteria

- All Must-Fix conditions from `review.md` have covering tests: call-site migration (M1-M10), write-path integrity (I1-I7), file-backend degradation without crash (N1-N5, F6-F7), schema compatibility (B1-B5), and the no-faiss guards (R1-R8).
- `uv run pytest tests/` is green with no faiss mock present.
- `grep -rn "import faiss"` and `grep -rn "faiss_service"` over `registry/` are empty; `grep -ri faiss` over infra/build (excluding release notes, the deferred metric column, and the rollout-window collector schema) is empty.
- Default `docker-compose up` serves hybrid search; `STORAGE_BACKEND=file` boots, warns, and returns empty search.
