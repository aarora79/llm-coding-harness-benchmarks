# GitHub Issue: Remove FAISS from the codebase and documentation

## Title
Remove FAISS — obsolete file-based vector search backend and all references

## Labels
- cleanup
- tech-debt
- docs
- dependencies

## Description

### Problem Statement
The mcp-gateway-registry project migrated from FAISS (a file-based vector search library) to DocumentDB hybrid search for all vector operations. FAISS remains as dead code: unused imports, stale pip dependencies, configuration properties, API route calls, documentation references, and test fixtures. This creates confusion for contributors who may think FAISS is still active, and causes operators to pull unnecessary native dependencies (faiss-cpu, faiss-gpu) into container images.

### Proposed Solution
Remove FAISS entirely:
1. Delete the FAISS service module (`registry/search/service.py`) and its search repository (`registry/repositories/file/search_repository.py`)
2. Remove the `faiss-cpu` dependency from `pyproject.toml`
3. Remove `faiss_index_path` and `faiss_metadata_path` properties from config
4. Remove all `faiss_service` imports and calls from API routes, agents routes, and batch processors
5. Update the search repository factory to no longer support the file-based (FAISS) fallback — always use DocumentDB
6. Clean up FAISS references from schemas, telemetry, metrics, and CLI modules
7. Remove or rewrite all documentation referencing FAISS
8. Remove FAISS mock fixtures and test files

Expected outcome: `storage_backend` defaults to and only accepts a DocumentDB backend. All search operations route through `DocumentDBSearchRepository`. No imports of `faiss`, `FaissService`, or `FaissSearchRepository` remain anywhere in the codebase.

### User Stories
- As a contributor, I want the codebase to contain no references to FAISS so I am not confused about whether it is active.
- As an operator, I want Docker images and deployments to exclude FAISS dependencies so container images are smaller and native library issues are avoided.
- As a developer, I want search to use only DocumentDB so the codebase is simpler to maintain.

### Acceptance Criteria
- [ ] `faiss-cpu` removed from `pyproject.toml` dependencies
- [ ] No Python file imports `faiss` or references `FaissService`, `FaissSearchRepository`, or `faiss_service`
- [ ] `registry/search/service.py` (the FAISS service module) deleted
- [ ] `registry/repositories/file/search_repository.py` deleted
- [ ] `faiss_index_path` and `faiss_metadata_path` removed from `registry/core/config.py`
- [ ] `FaissMetadata` schema removed from `registry/core/schemas.py`
- [ ] Search repository factory (`get_search_repository()`) no longer branches to `FaissSearchRepository`; always returns `DocumentDBSearchRepository`
- [ ] All `faiss_service.add_or_update_service`, `faiss_service.remove_service`, `faiss_service.add_or_update_agent`, `faiss_service.remove_agent`, `faiss_service.add_or_update_entity`, `faiss_service.save_data` calls removed from API routes and batch processors
- [ ] `faiss_search_time_ms` field removed from metrics/telemetry schemas and client code
- [ ] Comments in API routes updated to remove FAISS mentions
- [ ] All documentation files updated: `docs/`, `release-notes/`, `terraform/`, `cli/`, `api/`, and any other markdown with FAISS references
- [ ] FAISS mock fixture (`tests/fixtures/mocks/mock_faiss.py`) and related test files removed
- [ ] Docker, docker-compose, and build config comments updated to remove FAISS
- [ ] `grep -ri faiss` across the entire repository returns zero results (excluding git history)
- [ ] Existing tests pass without importing faiss

### Out of Scope
- Changes to the DocumentDB search implementation itself (existing and working)
- Migration of existing FAISS index data (no data migration needed since DocumentDB is the active backend)
- Adding new features

### Dependencies
- None. DocumentDB search already works and is the active backend.

### Related Issues
- N/A (this is a standalone cleanup task)