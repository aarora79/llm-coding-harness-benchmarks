# GitHub Issue: Remove FAISS from the codebase and documentation

## Title
Remove obsolete FAISS dependency and replace with DocumentDB hybrid search

## Labels
- refactor
- tech-debt
- documentation

## Description

### Problem Statement

This repository uses FAISS (Facebook AI Similarity Search) as the vector search
backend for semantic search and hybrid search. FAISS has become an unnecessary
dependency that complicates deployment due to its native library requirements
(C/C++ build dependencies, platform-specific binary wheels).

FAISS has already been superseded by the DocumentDB native hybrid search
implementation (BM25 + vector k-NN) which is actively maintained and used in
production. The DocumentDB backend provides equal or better search quality with
a simpler operational footprint.

Keeping FAISS in the codebase creates:

- Deployment complexity (native library dependencies, torch/FAISS version
  conflicts)
- Dual maintenance burden (two vector search backends to maintain)
- Test complexity (FAISS mocking infrastructure that masks real behavior)
- Documentation drift (FAISS referenced in 60+ doc locations as the primary
  backend)

### Proposed Solution

Remove all FAISS code, dependencies, test fixtures, and documentation references.
Migrate the file-based storage backend's search path to use the DocumentDB hybrid
search repository directly, so that operators who use `storage_backend=file` (for
local development) still get vector-capable search without FAISS.

This change consolidates on the DocumentDB hybrid search implementation that
already exists in `registry/repositories/documentdb/search_repository.py`.

### User Stories

- As an operator, I want to deploy the registry without FAISS native library
  dependencies so that Docker builds are simpler and faster.
- As a developer, I want a single vector search implementation so that I do not
  need to maintain two separate backends.
- As a developer, I want test fixtures to not depend on FAISS mocks so that test
  infrastructure is simpler.
- As an end user, I want search to continue working exactly as before after the
  FAISS removal.

### Acceptance Criteria

- [ ] `faiss-cpu` removed from `pyproject.toml` and `uv.lock` regenerates cleanly
- [ ] `registry/search/service.py` (FaissService class) is deleted
- [ ] `registry/repositories/file/search_repository.py` (FaissSearchRepository)
      is deleted or rewritten to delegate to DocumentDBSearchRepository
- [ ] `registry/repositories/factory.py` routes all backends to
      DocumentDBSearchRepository
- [ ] No remaining `import faiss` or `from ... import faiss_service` in
      production source code under `registry/` or `auth_server/`
- [ ] Metrics service `faiss_search_time_ms` fields are removed or deprecated
- [ ] All FAISS references removed from documentation under `docs/` and
      `release-notes/`
- [ ] Shell scripts (`cli/service_mgmt.sh`, `terraform/aws-ecs/scripts/`) updated
      or FAISS verification removed
- [ ] Terraform/Helm/Docker references to FAISS updated in comments
- [ ] Test fixtures for FAISS (`mock_faiss.py`, `test_faiss_service.py`) deleted
- [ ] `tests/conftest.py` FAISS auto-mock removed
- [ ] Integration tests that patch `faiss_service` updated to use DocumentDB mock
- [ ] `uv sync` succeeds without FAISS
- [ ] Existing search API endpoints return the same response shape
- [ ] DocumentDB hybrid search correctly handles search requests from all callers
- [ ] No `grep -r "faiss" .` returns matches in source or docs (excluding this issue)

### Out of Scope

- Migration of existing FAISS index data to DocumentDB (operators should re-index)
- Removing `sentence-transformers` or embedding providers (embeddings are still
  needed by DocumentDB hybrid search)
- Removing the `file` storage backend for other repositories (servers, agents,
  skills, etc. continue to use file-based storage)
- Removing embeddings admin routes or embedding configuration

### Dependencies

- None. The DocumentDB hybrid search repository already implements the full
  `SearchRepositoryBase` interface.
- `sentence-transformers` and embedding providers remain needed for generating
  embeddings used by DocumentDB hybrid search.

### Related Issues

- #955 (storage backend configuration alignment)
- DocumentDB hybrid search design: `docs/design/hybrid-search-architecture.md`