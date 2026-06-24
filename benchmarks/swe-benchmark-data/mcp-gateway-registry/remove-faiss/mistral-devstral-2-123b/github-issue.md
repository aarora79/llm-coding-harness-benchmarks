# GitHub Issue: Remove FAISS from codebase and documentation

## Title
Remove FAISS vector search dependency and implement unified DocumentDB search

## Labels
- enhancement
- refactor
- technical-debt
- performance

## Description

### Problem Statement
FAISS (Facebook AI Similarity Search) is currently used as a vector search solution in the mcp-gateway-registry, but it has become a technical debt issue:

- **Binary complexity**: FAISS adds binary dependencies that complicate Docker builds and increase image size
- **Maintenance burden**: FAISS is unmaintained in this codebase and functionally replaced by the existing DocumentDB search implementation
- **Supply chain surface**: Additional dependency increases security surface area
- **Inconsistent behavior**: Different search backends provide different results for the same queries

### Proposed Solution
1. **Remove FAISS completely**: Delete all FAISS imports, dependencies, and configuration
2. **Unify on DocumentDB search**: Use the existing `DocumentDBSearchRepository` for all storage backends
3. **Update factory pattern**: Modify the repository factory to always return `DocumentDBSearchRepository`
4. **Clean up code**: Remove FAISS-related tests, fixtures, and documentation

### User Stories
- As an **operator of mcp-gateway-registry (Docker/ECS/Helm)**, I want FAISS removed so that my deployment images are smaller and builds are faster
- As a **registry maintainer**, I want consistent search behavior across all deployments regardless of storage backend
- As a **developer**, I want to maintain less code by removing the obsolete FAISS implementation

### Acceptance Criteria
- [x] Remove `faiss-cpu` from `pyproject.toml` dependencies
- [x] Delete the `FaissSearchRepository` implementation and related files
- [x] Update `factory.py` to always return `DocumentDBSearchRepository`
- [x] Remove FAISS-related test files and fixtures
- [x] Delete all FAISS imports throughout the codebase
- [x] Remove FAISS configuration and documentation references
- [x] Ensure existing search behavior is preserved (embedding/search compatibility)
- [x] Verify Docker builds work without FAISS
- [x] Confirm Helm charts deploy successfully
- [x] Validate platform-level ECS deployments

### Out of Scope
- Changes to core search algorithms or ranking logic
- Modifications to DocumentDB schema or indexes
- Performance optimization of vector search queries
- Changes to embedding model providers or configurations

### Dependencies
- Python 3.11+ environment (already required)
- Existing DocumentDB infrastructure (already deployed)
- `sentence-transformers` for embeddings (already required)
- MongoDB/Motor for DocumentDB access (already required)

### Related Issues
- #420 (DocumentDB search performance improvements)
- #389 (Hybrid search feature parity across backends)