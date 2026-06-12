# Low-Level Design: Remove FAISS from Codebase

*Created: 2026-06-12*
*Author: Claude (minimax-m2.5 benchmark)*
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
FAISS (Facebook AI Similarity Search) is an obsolete vector search library in the MCP Gateway Registry. It adds unnecessary native dependencies (`faiss-cpu>=1.7.4`), requires loading heavy ML models at startup, and is redundant because the DocumentDB-based search already provides superior hybrid search capabilities.

### Goals
1. Remove `faiss-cpu` dependency from `pyproject.toml`
2. Delete all FAISS imports and the FaissService class
3. Replace `FaissSearchRepository` with `FileSearchRepository` that uses keyword-only search
4. Remove FAISS references from documentation and infrastructure configs

### Non-Goals
- Implementing vector search in FileSearchRepository (keyword-only search)
- Modifying the DocumentDB search implementation
- Adding new search backends

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `pyproject.toml` | Python dependencies | Remove `faiss-cpu>=1.7.4` |
| `registry/search/service.py` | FaissService implementation | DELETE entire file |
| `registry/repositories/file/search_repository.py` | FaissSearchRepository | REPLACE with FileSearchRepository |
| `registry/repositories/factory.py` | Repository factory | Update to return FileSearchRepository |
| `registry/repositories/interfaces.py` | SearchRepositoryBase interface | Update docstring |
| `registry/core/config.py` | Configuration settings | Remove faiss_index_path, faiss_metadata_path |
| `docker-compose.yml` | Container config | Remove FAISS comments |
| `build-config.yaml` | Build configuration | Remove FAISS comments |
| `terraform/aws-ecs/OPERATIONS.md` | Terraform docs | Remove FAISS references |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS mock | DELETE file |
| `tests/conftest.py` | Test fixtures | Remove FAISS mock setup |
| `tests/unit/search/test_faiss_service.py` | FAISS tests | DELETE file |

### Existing Patterns Identified

1. **SearchRepositoryBase Interface**: All search repositories implement this abstract base class with methods: `initialize()`, `index_server()`, `index_agent()`, `index_skill()`, `index_virtual_server()`, `remove_entity()`, `search()`, `search_by_tags()`, `get_all_tags()`, `rebuild_index()`.

2. **DocumentDB Search Repository**: Uses hybrid search combining vector embeddings with keyword matching. Uses a fallback pattern for when vector search is unavailable (MongoDB CE).

3. **Repository Factory Pattern**: `get_search_repository()` in factory.py selects the implementation based on `settings.storage_backend`. For `file` backend, it currently returns `FaissSearchRepository`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| FaissSearchRepository | Implements | SearchRepositoryBase |
| factory.py | Uses | Creates FaissSearchRepository for file backend |
| server_routes.py | Imports | `from ..search.service import faiss_service` |
| agent_routes.py | Imports | `from ..search.service import faiss_service` |
| agent_batch_item_processor.py | Imports | `from ..search.service import faiss_service` |

## Architecture

### Current Architecture
```
storage_backend = "file"
    |
    v
factory.get_search_repository() --> FaissSearchRepository
                                        |
                                        +---> FaissService (imports faiss)
                                                    |
                                              +-- faiss.Index
                                              +-- metadata_store (dict)
                                              +-- embedding_model
```

### Target Architecture
```
storage_backend = "file"
    |
    v
factory.get_search_repository() --> FileSearchRepository
                                              |
                                        +---> KeywordSearchService
                                                    |
                                              +-- metadata_store (JSON file)
                                              +-- embedding_model (NOT REQUIRED)
```

### Sequence Diagram
```
GET /api/v1/servers/search?q=keyword

server_routes.py
    |
    v
get_search_repository()  [factory.py]
    |
    v
FileSearchRepository.search()
    |
    v
KeywordSearchService.search_keyword()
    |
    v
Return results from metadata_store
```

## Data Models

### SearchRepositoryBase Interface (unchanged)
The `FileSearchRepository` must implement all abstract methods from `SearchRepositoryBase`:

```python
class SearchRepositoryBase(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def index_server(self, path: str, server_info: dict, is_enabled: bool = False): ...

    @abstractmethod
    async def index_agent(self, path: str, agent_card: AgentCard, is_enabled: bool = False): ...

    @abstractmethod
    async def remove_entity(self, path: str): ...

    @abstractmethod
    async def search(self, query: str, entity_types: list[str] | None = None,
                     max_results: int = 10, include_draft: bool = False,
                     include_deprecated: bool = False, include_disabled: bool = False): ...
```

### New FileSearchRepository Model
```python
class FileSearchRepository(SearchRepositoryBase):
    """File-based search repository using keyword-only search."""

    def __init__(self):
        self._metadata_file: Path | None = None
        self._entities: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        """Load metadata from JSON file."""
        self._metadata_file = settings.servers_dir / "search_metadata.json"
        if self._metadata_file.exists():
            self._entities = json.loads(self._metadata_file.read_text())

    def _save_metadata(self) -> None:
        """Persist metadata to JSON file."""
        self._metadata_file.parent.mkdir(parents=True, exist_ok=True)
        self._metadata_file.write_text(json.dumps(self._entities, indent=2))

    async def index_server(self, path: str, server_info: dict, is_enabled: bool = False) -> None:
        """Index server for keyword search."""
        self._entities[path] = {
            "entity_type": "mcp_server",
            "path": path,
            "name": server_info.get("server_name", ""),
            "description": server_info.get("description", ""),
            "tags": server_info.get("tags", []),
            "is_enabled": is_enabled,
            "indexed_at": datetime.utcnow().isoformat(),
        }
        self._save_metadata()

    async def index_agent(self, path: str, agent_card: AgentCard, is_enabled: bool = False) -> None:
        """Index agent for keyword search."""
        self._entities[path] = {
            "entity_type": "a2a_agent",
            "path": path,
            "name": agent_card.name,
            "description": agent_card.description or "",
            "tags": agent_card.tags or [],
            "is_enabled": is_enabled,
            "indexed_at": datetime.utcnow().isoformat(),
        }
        self._save_metadata()

    async def index_skill(self, path: str, skill: Any, is_enabled: bool = False) -> None:
        """Index skill for keyword search."""
        self._entities[path] = {
            "entity_type": "skill",
            "path": path,
            "name": skill.name,
            "description": skill.description,
            "tags": skill.tags or [],
            "is_enabled": is_enabled,
            "indexed_at": datetime.utcnow().isoformat(),
        }
        self._save_metadata()

    async def index_virtual_server(self, path: str, virtual_server: Any, is_enabled: bool = False) -> None:
        """Index virtual server for keyword search."""
        self._entities[path] = {
            "entity_type": "virtual_server",
            "path": path,
            "name": virtual_server.server_name,
            "description": virtual_server.description or "",
            "tags": virtual_server.tags or [],
            "is_enabled": is_enabled,
            "indexed_at": datetime.utcnow().isoformat(),
        }
        self._save_metadata()

    async def remove_entity(self, path: str) -> None:
        """Remove entity from search index."""
        self._entities.pop(path, None)
        self._save_metadata()

    async def search(
        self,
        query: str,
        entity_types: list[str] | None = None,
        max_results: int = 10,
        include_draft: bool = False,
        include_deprecated: bool = False,
        include_disabled: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """Search entities using keyword matching."""
        query_lower = query.lower()
        tokens = query_lower.split()

        results: dict[str, list[dict[str, Any]]] = {
            "servers": [], "tools": [], "agents": [],
            "skills": [], "virtual_servers": []
        }

        for path, entity in self._entities.items():
            # Skip disabled entities
            if not include_disabled and not entity.get("is_enabled", True):
                continue

            # Filter by entity type
            if entity_types and entity.get("entity_type") not in entity_types:
                continue

            # Calculate relevance score based on keyword matches
            score = 0.0
            name = entity.get("name", "").lower()
            desc = entity.get("description", "").lower()
            tags = " ".join(entity.get("tags", [])).lower()

            for token in tokens:
                if token in name:
                    score += 3.0
                if token in desc:
                    score += 2.0
                if token in tags:
                    score += 1.5
                if token in path.lower():
                    score += 1.0

            if score > 0:
                result = {
                    "path": path,
                    "entity_type": entity.get("entity_type"),
                    "name": entity.get("name"),
                    "description": entity.get("description"),
                    "tags": entity.get("tags", []),
                    "is_enabled": entity.get("is_enabled", True),
                    "relevance_score": min(1.0, score / 10.0),
                }
                entity_type = entity.get("entity_type", "")
                if entity_type == "mcp_server":
                    results["servers"].append(result)
                elif entity_type == "a2a_agent":
                    results["agents"].append(result)
                elif entity_type == "skill":
                    results["skills"].append(result)
                elif entity_type == "virtual_server":
                    results["virtual_servers"].append(result)

        # Sort each category by relevance and limit
        for key in results:
            results[key] = sorted(results[key], key=lambda x: x.get("relevance_score", 0), reverse=True)[:max_results]

        return results

    async def search_by_tags(
        self,
        tags: list[str],
        entity_types: list[str] | None = None,
        max_results: int = 10,
        include_draft: bool = False,
        include_deprecated: bool = False,
        include_disabled: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """Search entities by exact tag match."""
        required_tags = {t.lower() for t in tags}
        results: dict[str, list[dict[str, Any]]] = {
            "servers": [], "tools": [], "agents": [],
            "skills": [], "virtual_servers": []
        }

        for path, entity in self._entities.items():
            if not include_disabled and not entity.get("is_enabled", True):
                continue
            if entity_types and entity.get("entity_type") not in entity_types:
                continue

            entity_tags = {t.lower() for t in entity.get("tags", [])}
            if required_tags.issubset(entity_tags):
                result = {
                    "path": path,
                    "name": entity.get("name"),
                    "description": entity.get("description"),
                    "tags": entity.get("tags", []),
                    "is_enabled": entity.get("is_enabled", True),
                    "relevance_score": 1.0,
                }
                entity_type = entity.get("entity_type", "")
                if entity_type == "mcp_server":
                    results["servers"].append(result)
                elif entity_type == "a2a_agent":
                    results["agents"].append(result)
                elif entity_type == "skill":
                    results["skills"].append(result)
                elif entity_type == "virtual_server":
                    results["virtual_servers"].append(result)

        for key in results:
            results[key] = results[key][:max_results]
        return results

    async def get_all_tags(self) -> list[str]:
        """Return sorted list of all unique tags."""
        tags_set: set[str] = set()
        for entity in self._entities.values():
            for tag in entity.get("tags", []):
                if tag:
                    tags_set.add(tag)
        return sorted(tags_set, key=str.lower)

    async def rebuild_index(self) -> None:
        """Rebuild index - for FileSearchRepository this is a no-op."""
        pass
```

## Implementation Details

### Step-by-Step Plan

#### Step 1: Remove FAISS Dependency
**File:** `pyproject.toml`
- Remove line: `"faiss-cpu>=1.7.4",`

#### Step 2: Delete FAISS Service and Tests
**Files to DELETE:**
- `registry/search/service.py` (FaissService class)
- `tests/fixtures/mocks/mock_faiss.py`
- `tests/unit/search/test_faiss_service.py`

#### Step 3: Update Repository Factory
**File:** `registry/repositories/factory.py`
- Change import: `from .file.search_repository import FileSearchRepository`
- Change line 147: `_search_repo = FileSearchRepository()`

#### Step 4: Replace FaissSearchRepository
**File:** `registry/repositories/file/search_repository.py`
- Replace entire content with new `FileSearchRepository` class

#### Step 5: Update Interface Docstring
**File:** `registry/repositories/interfaces.py`
- Line 1002: Change "FAISS or DocumentDB" to "file-based or DocumentDB"

#### Step 6: Update API Route Imports
**Files:**
- `registry/api/server_routes.py` - Remove all `from ..search.service import faiss_service` imports
- `registry/api/agent_routes.py` - Remove all `from ..search.service import faiss_service` imports
- `registry/services/agent_batch_item_processor.py` - Remove all `from ..search.service import faiss_service` imports

#### Step 7: Remove FAISS Config
**File:** `registry/core/config.py`
- Remove `faiss_index_path` property (~line 996)
- Remove `faiss_metadata_path` property (~line 1000)

#### Step 8: Remove Test Mocks
**File:** `tests/conftest.py`
- Remove FAISS mock setup lines

**File:** `tests/unit/conftest.py`
- Remove `mock_faiss_service()` fixture

**File:** `tests/unit/core/test_config.py`
- Remove `test_faiss_index_path` and `test_faiss_metadata_path` tests

**File:** `tests/unit/core/test_telemetry.py`
- Remove FAISS reference in search_backend test

**File:** `tests/unit/repositories/test_factory_aliases.py`
- Update assertion to not expect FaissSearchRepository

**File:** `tests/unit/test_safe_eval_arithmetic.py`
- Remove FAISS-related __spec__ patches

#### Step 9: Update Infrastructure Documentation
**Files:**
- `docker-compose.yml` - Remove FAISS from comments
- `docker-compose.podman.yml` - Remove FAISS from comments
- `docker-compose.prebuilt.yml` - Remove FAISS from comments
- `build-config.yaml` - Remove FAISS from descriptions
- `terraform/aws-ecs/OPERATIONS.md` - Remove FAISS from registry service description
- `tests/README.md` - Remove FAISS references and mock documentation

## File Changes

### Deleted Files

| File Path | Reason |
|-----------|--------|
| `registry/search/service.py` | Contains FaissService with FAISS imports |
| `tests/fixtures/mocks/mock_faiss.py` | FAISS mock implementation |
| `tests/unit/search/test_faiss_service.py` | Tests for FaissService |

### New Files

| File Path | Description |
|-----------|-------------|
| None | FileSearchRepository replaces FaissSearchRepository in-place |

### Modified Files

| File Path | Change Description |
|-----------|--------------------|
| `pyproject.toml` | Remove faiss-cpu dependency |
| `registry/repositories/file/search_repository.py` | Replace FaissSearchRepository with FileSearchRepository |
| `registry/repositories/factory.py` | Update import and instantiation |
| `registry/repositories/interfaces.py` | Update docstring |
| `registry/api/server_routes.py` | Remove faiss_service imports |
| `registry/api/agent_routes.py` | Remove faiss_service imports |
| `registry/services/agent_batch_item_processor.py` | Remove faiss_service imports |
| `registry/core/config.py` | Remove faiss_index_path, faiss_metadata_path |
| `tests/conftest.py` | Remove FAISS mock setup |
| `tests/unit/conftest.py` | Remove mock_faiss_service fixture |
| `tests/unit/core/test_config.py` | Remove FAISS config tests |
| `tests/unit/core/test_telemetry.py` | Remove FAISS reference |
| `tests/unit/repositories/test_factory_aliases.py` | Update assertion |
| `tests/unit/test_safe_eval_arithmetic.py` | Remove FAISS __spec__ patches |
| `docker-compose.yml` | Remove FAISS comments |
| `docker-compose.podman.yml` | Remove FAISS comments |
| `docker-compose.prebuilt.yml` | Remove FAISS comments |
| `build-config.yaml` | Remove FAISS descriptions |
| `terraform/aws-ecs/OPERATIONS.md` | Remove FAISS references |
| `tests/README.md` | Remove FAISS documentation |
| `tests/test_infrastructure.py` | Remove mock_faiss import |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted code | ~1500 |
| New code (FileSearchRepository) | ~200 |
| **Net Change** | **~-1300** |

## Testing Strategy

See `testing.md` for the comprehensive testing plan.

## Rollout Plan

- Phase 1: Implementation (code changes)
- Phase 2: Testing (unit, integration, E2E)
- Phase 3: Deployment (staging -> production)

## Open Questions

1. Should the search metadata JSON file be migrated from the old FAISS format? - **No**, rebuild index on startup is acceptable
2. Should we keep backward compatibility for existing indexed data? - **No**, rebuild from source collections

## References

- Existing `DocumentDBSearchRepository` implementation for API contract reference
- `SearchRepositoryBase` interface in `registry/repositories/interfaces.py`