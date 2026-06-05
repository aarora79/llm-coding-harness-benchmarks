# GitHub Issue: Remove FAISS from the codebase and documentation

## Title
Remove FAISS and replace with DocumentDB hybrid search

## Labels
- enhancement
- refactor
- infra
- documentation

## Description

### Problem Statement
FAISS (Facebook AI Similarity Search) is an obsolete vector search library in this repository. The codebase now uses a maintained DocumentDB-based hybrid search implementation that provides equivalent functionality with better operational characteristics (serverless, scalable, no local vector index management). Keeping FAISS in the codebase creates unnecessary dependencies, potential maintenance burden, and confusion for future developers.

### Proposed Solution
1. Remove `faiss-cpu` dependency from `pyproject.toml`
2. Delete the FAISS-specific search service (`registry/search/service.py`)
3. Update repository factory to remove FAISS path
4. Update configuration to remove FAISS file paths
5. Delete `FaissSearchRepository` implementation
6. Clean up all mock FAISS files and test files related to FAISS
7. Update documentation to remove FAISS references
8. Update Terraform and Docker configurations to remove FAISS references

### User Stories
- As a developer, I want to remove obsolete FAISS code so the codebase is easier to maintain
- As a new team member, I want clear documentation about the search implementation without references to deprecated technologies
- As an operator, I want fewer dependencies to reduce potential security vulnerabilities

### Acceptance Criteria
- [ ] `faiss-cpu` dependency removed from `pyproject.toml`
- [ ] `registry/search/` directory deleted
- [ ] `FaissSearchRepository` removed from `registry/repositories/file/search_repository.py`
- [ ] `registry/repositories/factory.py` updated to remove FAISS code paths
- [ ] `Settings` class updated to remove `faiss_index_path` and `faiss_metadata_path` properties
- [ ] All FAISS-related imports removed from codebase
- [ ] All FAISS-related documentation removed or updated
- [ ] `uv run python -m py_compile` passes on all remaining Python files
- [ ] Tests pass with new configuration (using DocumentDB/file backend search)
- [ ] Docker builds still work
- [ ] No FAISS references remain in `grep -r faiss` against the codebase

### Out of Scope
- Changing the search API contract (search endpoints remain the same)
- Changing the embedding model configuration
- Modifying the DocumentDB search implementation
- Updating the metrics-service (which also has FAISS references)
- Changing from MongoDB to another database backend

### Dependencies
- None - this is a self-contained refactor

### Related Issues
- Issue #955 (search optimization)
