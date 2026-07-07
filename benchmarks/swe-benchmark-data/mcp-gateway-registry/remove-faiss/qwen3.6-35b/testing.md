# Testing Plan: Remove FAISS from the codebase

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
Verify that removing FAISS does not break any working DocumentDB search paths, does not leave any dangling FAISS references, and does not cause import errors at app startup.

### Prerequisites
- [ ] Cloned repo at tag 1.24.4
- [ ] Python 3.11+ available
- [ ] `uv` or `pip` available for dependency management

### Shared Variables
```bash
REPO_ROOT="."  # benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/
```

## 1. Functional Tests
### 1.1 FAISS Import Removal Verification

**Command:**
```bash
cd "$REPO_ROOT"
grep -rni 'import faiss' --include='*.py' .
grep -rni 'from.*import.*faiss' --include='*.py' .
grep -rni 'from.*import.*Faiss' --include='*.py' .
```

**Expected:** Zero results in all three searches.

**Assertions:**
- No Python file imports the `faiss` module.
- No Python file imports `FaissService`, `FaissSearchRepository`, or `FaissMetadata`.

### 1.2 Dependency Removal Verification

**Command:**
```bash
cd "$REPO_ROOT"
grep 'faiss-cpu' pyproject.toml
```

**Expected:** Zero results.

**Assertions:**
- `pyproject.toml` does not contain `faiss-cpu`.

### 1.3 Config Property Removal Verification

**Command:**
```bash
cd "$REPO_ROOT"
grep -n 'faiss_index_path\|faiss_metadata_path' registry/core/config.py
```

**Expected:** Zero results.

**Assertions:**
- `registry/core/config.py` has no `faiss_index_path` or `faiss_metadata_path` properties.

### 1.4 Factory Pattern Verification

**Command:**
```bash
cd "$REPO_ROOT"
grep -n 'FaissSearchRepository' registry/repositories/factory.py
grep -n 'else:' registry/repositories/factory.py | head -20
```

**Expected:**
- Zero results for `FaissSearchRepository` in factory.py.
- The `get_search_repository()` function has no else-branch.

**Assertions:**
- `get_search_repository()` directly returns `DocumentDBSearchRepository()`.

### 1.5 FAISS Service File Deletion Verification

**Command:**
```bash
cd "$REPO_ROOT"
test -f registry/search/service.py && echo "FAIL: service.py still exists" || echo "OK: deleted"
test -f registry/repositories/file/search_repository.py && echo "FAIL: search_repository.py still exists" || echo "OK: deleted"
```

**Expected:** Both files reported as deleted.

### 1.6 App Startup Verification (Read-Only)

**Command:**
```bash
cd "$REPO_ROOT"
python -c "
import sys
sys.path.insert(0, '.')
# This will fail if any import error exists
from registry.core.config import settings
from registry.repositories import factory
print('Config loaded OK')
print(f'storage_backend: {settings.storage_backend}')
print(f'MONGODB_BACKENDS: {factory.MONGODB_BACKENDS}')
search_repo = factory.get_search_repository()
print(f'Search repository: {type(search_repo).__name__}')
assert 'DocumentDB' in type(search_repo).__name__, f'Expected DocumentDB repo, got {type(search_repo).__name__}'
print('PASS: App imports and factory work correctly')
"
```

**Expected:** Script completes with "PASS: App imports and factory work correctly".

**Assertions:**
- No ImportError from any module.
- `get_search_repository()` returns a `DocumentDBSearchRepository` instance.

## 2. Backwards Compatibility Tests
### 2.1 DocumentDB Search Path Unchanged

**Command:**
```bash
cd "$REPO_ROOT"
python -c "
import sys
sys.path.insert(0, '.')
from registry.repositories.factory import get_search_repository
repo = get_search_repository()
# Verify all required methods exist
assert hasattr(repo, 'search'), 'Missing search method'
assert hasattr(repo, 'add'), 'Missing add method'
assert hasattr(repo, 'remove'), 'Missing remove method'
print('PASS: DocumentDB search repository has all required methods')
"
```

**Expected:** Script completes successfully.

**Assertions:**
- DocumentDB search repository retains all methods required by callers.

### 2.2 API Route Handlers Still Importable

**Command:**
```bash
cd "$REPO_ROOT"
python -c "
import sys
sys.path.insert(0, '.')
# These imports were the primary consumers of faiss_service
from registry.api import server_routes
from registry.api import agent_routes
from registry.services import agent_batch_item_processor
print('PASS: All API route modules import successfully')
"
```

**Expected:** All modules import without errors.

**Assertions:**
- No ImportError from `server_routes`, `agent_routes`, or `agent_batch_item_processor`.

**Not Applicable** - No API endpoint signatures change. The search operations happen internally via DocumentDB repositories and are not exposed in request/response shapes.

## 3. UX Tests
### 3.1 Startup Message Clarity

**Command:**
```bash
cd "$REPO_ROOT"
grep -n 'FAISS\|DocumentDB' registry/main.py | head -10
```

**Expected:** No references to FAISS in startup messages.

**Assertions:**
- `registry/main.py` startup message references DocumentDB only.

### 3.2 Comment Cleanup in API Routes

**Command:**
```bash
cd "$REPO_ROOT"
grep -ni 'Updating FAISS\|Update FAISS\|FAISS index\|FAISS metadata' registry/api/server_routes.py registry/api/agent_routes.py
```

**Expected:** Zero results.

**Assertions:**
- No comments in API route files mention FAISS.

**Not Applicable** - No user-facing UI changes.

## 4. Deployment Surface Tests
### 4.1 Docker Compose Comment Cleanup

**Command:**
```bash
cd "$REPO_ROOT"
grep -n 'FAISS' docker-compose.yml docker-compose.prebuilt.yml docker-compose.podman.yml
```

**Expected:** Zero results.

**Assertions:**
- No docker-compose files contain "FAISS" in comments.

### 4.2 Build Config Comment Cleanup

**Command:**
```bash
cd "$REPO_ROOT"
grep -n 'FAISS' build-config.yaml
```

**Expected:** Zero results.

**Assertions:**
- `build-config.yaml` does not mention FAISS.

### 4.3 Terraform Comment Cleanup

**Command:**
```bash
cd "$REPO_ROOT"
grep -rn 'FAISS' terraform/
```

**Expected:** Zero results.

**Assertions:**
- No Terraform files mention FAISS.

### 4.4 Docker Image Dependency Reduction

**Command:**
```bash
cd "$REPO_ROOT"
uv pip compile pyproject.toml - 2>/dev/null | grep -i faiss || echo "PASS: no faiss in resolved dependencies"
```

**Expected:** "PASS: no faiss in resolved dependencies".

**Assertions:**
- `faiss-cpu` and any FAISS-related transitive dependencies are not in the compiled requirement set.

**Not Applicable** - No changes to actual Dockerfile, Helm values, or ECS task definitions are required. Only comment text changes in existing files.

## 5. End-to-End API Tests
### 5.1 DocumentDB Search Operations Still Work

**Command:**
```bash
cd "$REPO_ROOT"
python -c "
import sys
sys.path.insert(0, '.')
# Verify the full search chain works without FAISS
from registry.repositories.factory import get_search_repository, get_server_repository
from registry.core.config import settings

print(f'storage_backend: {settings.storage_backend}')

# Get search repository (should be DocumentDBSearchRepository)
search_repo = get_search_repository()
print(f'Search repo type: {type(search_repo).__name__}')
assert 'DocumentDB' in type(search_repo).__name__, f'Expected DocumentDB repo, got {type(search_repo).__name__}'

# Get server repository (should be DocumentDBServerRepository)
server_repo = get_server_repository()
print(f'Server repo type: {type(server_repo).__name__}')
assert 'DocumentDB' in type(server_repo).__name__, f'Expected DocumentDB server repo, got {type(server_repo).__name__}'

# Get agent repository (should be DocumentDBAgentRepository)
agent_repo = get_agent_repository()
print(f'Agent repo type: {type(agent_repo).__name__}')
assert 'DocumentDB' in type(agent_repo).__name__, f'Expected DocumentDB agent repo, got {type(agent_repo).__name__}'

print('PASS: All repository factories return DocumentDB implementations')
"
```

**Expected:** All factories return DocumentDB implementations.

**Assertions:**
- `get_search_repository()` returns `DocumentDBSearchRepository`.
- `get_server_repository()` returns `DocumentDBServerRepository`.
- `get_agent_repository()` returns `DocumentDBAgentRepository`.

### 5.2 Telemetry Search Backend Detection

**Command:**
```bash
cd "$REPO_ROOT"
grep -n 'faiss' registry/core/telemetry.py
```

**Expected:** Zero results.

**Assertions:**
- `registry/core/telemetry.py` does not contain "faiss" anywhere.

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes — all grep checks return zero results
- [ ] Section 2 (Backwards Compat) verified — DocumentDB search path unchanged
- [ ] Section 3 (UX) verified or marked Not Applicable — no UI changes
- [ ] Section 4 (Deployment) verified — Docker, build, and Terraform comments cleaned
- [ ] Section 5 (E2E) verified — repository factories return DocumentDB implementations
- [ ] `grep -ri faiss .` across the entire repository returns zero results (excluding git history)
- [ ] Existing tests pass without importing faiss (run `python -m pytest tests/ --collect-only` to verify no import errors)

## 7. Files Deleted Verification

**Command:**
```bash
cd "$REPO_ROOT"
echo "=== Files that should be deleted ==="
for f in \
  registry/search/service.py \
  registry/repositories/file/search_repository.py \
  tests/fixtures/mocks/mock_faiss.py \
  tests/unit/search/test_faiss_service.py; do
  if test -f "$f"; then
    echo "FAIL: $f still exists"
  else
    echo "OK: $f deleted"
  fi
done
```

**Expected:** All files reported as deleted.

## 8. Documentation Cleanup Verification

**Command:**
```bash
cd "$REPO_ROOT"
grep -rni 'faiss' --include='*.md' . 2>/dev/null | grep -v 'release-notes/' | head -50
```

**Expected:** Zero results (excluding historical release notes which should not be edited).

**Assertions:**
- No markdown documentation files contain "faiss" references.
- Historical release notes may still contain FAISS mentions (documenting what was true at that release) — these should NOT be edited.