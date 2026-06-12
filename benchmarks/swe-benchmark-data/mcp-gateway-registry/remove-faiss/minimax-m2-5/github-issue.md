# GitHub Issue: Remove FAISS from the Codebase and Documentation

## Title
Remove FAISS from the codebase and documentation - replace with FileSearchRepository

## Labels
- refactor
- deprecated
- enhancement

## Description

### Problem Statement
FAISS (Facebook AI Similarity Search) is an obsolete vector search library in this repository. It is no longer maintained and has been replaced by a simpler, more efficient file-based search implementation that uses the same `SearchRepositoryBase` interface. The codebase currently maintains two search implementations:
1. `FaissSearchRepository` - uses FAISS for vector search
2. `DocumentDBSearchRepository` - uses DocumentDB/MongoDB with hybrid search (text + vector)

The FAISS implementation should be removed because:
- It adds an unnecessary native dependency (`faiss-cpu`)
- It requires loading a large ML model at startup
- The DocumentDB implementation already provides superior hybrid search capabilities
- Simplicity: file-based search without external ML dependencies

### Proposed Solution
Replace the FAISS-based search with a simpler file-based search that uses keyword matching only (no vector search). The `FileSearchRepository` should be implemented to use the existing `DocumentDBSearchRepository` patterns but without any vector/FAISS dependencies.

### User Stories
- As an operator, I want the MCP Gateway Registry to start without loading heavy ML models so that startup is faster and memory usage is lower
- As a developer, I want to remove the `faiss-cpu` dependency so that the project has fewer native dependencies to manage
- As an operator, I want search functionality to work without FAISS so that the service runs reliably in environments where FAISS cannot be installed

### Acceptance Criteria
- [ ] Remove `faiss-cpu` from `pyproject.toml` dependencies
- [ ] Remove all `import faiss` statements from the codebase
- [ ] Delete the `registry/search/service.py` file (FaissService)
- [ ] Replace `FaissSearchRepository` with a new `FileSearchRepository` that uses keyword-only search
- [ ] Update `registry/repositories/factory.py` to instantiate `FileSearchRepository` instead of `FaissSearchRepository`
- [ ] Remove FAISS-related configuration properties from config (`faiss_index_path`, `faiss_metadata_path`)
- [ ] Remove FAISS mock from tests (`tests/fixtures/mocks/mock_faiss.py` and references in `conftest.py`)
- [ ] Update `docker-compose.yml`, `docker-compose.podman.yml`, `docker-compose.prebuilt.yml`, and `build-config.yaml` to remove FAISS comments
- [ ] Update `terraform/aws-ecs/OPERATIONS.md` to remove FAISS references
- [ ] Update `tests/README.md` to remove FAISS references
- [ ] Remove FAISS-related test files (`tests/unit/search/test_faiss_service.py`)
- [ ] Ensure search API endpoints work correctly with the new FileSearchRepository

### Out of Scope
- Implementing vector search in the new FileSearchRepository (will use keyword-only search)
- Modifying the DocumentDB search implementation (it remains unchanged)
- Adding new search backends beyond the file-based keyword search

### Dependencies
- None - this is a removal task

### Related Issues
- Part of effort to simplify MCP Gateway Registry dependencies