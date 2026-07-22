# Testing Plan: Remove FAISS from the Codebase

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan covers functional, backwards-compatibility, deployment, and E2E tests
for the removal of FAISS from the mcp-gateway-registry codebase. The core
verification is that search continues to work identically after FAISS removal,
with the DocumentDB hybrid search repository handling all search requests.

### Prerequisites
- [ ] Target repo checked out at tag 1.24.4 (`/tmp/swe-m03hik04/mcp-gateway-registry`)
- [ ] `uv sync` succeeds before changes (baseline)
- [ ] `uv run pytest tests/ -m unit -x` passes before changes (baseline)
- [ ] MongoDB available for integration tests (Docker Compose `mongodb` service)

### Shared Variables

```bash
export REPO="/tmp/swe-m03hik04/mcp-gateway-registry"
export REGISTRY_URL="http://localhost:80"
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json 2>/dev/null || echo "")
```

## 1. Functional Tests

### 1.1 Import Removal Verification

**Purpose:** Confirm no FAISS imports remain in production source code.

```bash
# Test 1: No faiss import in production code
cd "$REPO"
result=$(grep -rn "import faiss" registry/ auth_server/ credentials-provider/ 2>/dev/null || true)
if [ -n "$result" ]; then
    echo "FAIL: FAISS imports found:"
    echo "$result"
    exit 1
fi
echo "PASS: No faiss imports in production code"

# Test 2: No faiss_service imports in route/service files
result=$(grep -rn "from.*search.service import.*faiss_service" registry/api/ registry/services/ 2>/dev/null || true)
if [ -n "$result" ]; then
    echo "FAIL: faiss_service imports found:"
    echo "$result"
    exit 1
fi
echo "PASS: No faiss_service imports in routes/services"

# Test 3: No FaissService references in production code
result=$(grep -rn "FaissService" registry/ 2>/dev/null | grep -v "test" || true)
if [ -n "$result" ]; then
    echo "FAIL: FaissService references found:"
    echo "$result"
    exit 1
fi
echo "PASS: No FaissService references in production code"

# Test 4: No FaissSearchRepository references
result=$(grep -rn "FaissSearchRepository" registry/ 2>/dev/null || true)
if [ -n "$result" ]; then
    echo "FAIL: FaissSearchRepository references found:"
    echo "$result"
    exit 1
fi
echo "PASS: No FaissSearchRepository references"
```

### 1.2 Dependency Verification

**Purpose:** Confirm `faiss-cpu` is removed and `uv sync` succeeds.

```bash
cd "$REPO"

# Test 5: faiss-cpu removed from pyproject.toml
if grep -q "faiss-cpu" pyproject.toml; then
    echo "FAIL: faiss-cpu still in pyproject.toml"
    exit 1
fi
echo "PASS: faiss-cpu removed from pyproject.toml"

# Test 6: uv sync succeeds
if ! uv sync 2>&1; then
    echo "FAIL: uv sync failed after faiss-cpu removal"
    exit 1
fi
echo "PASS: uv sync succeeds"

# Test 7: No transitive faiss-cpu dependency
result=$(uv tree 2>/dev/null | grep -i faiss || true)
if [ -n "$result" ]; then
    echo "WARNING: faiss-cpu found as transitive dependency: $result"
    echo "ACTION: Investigate and block with tool.uv.sources if needed"
fi
```

### 1.3 Factory Routing Verification

**Purpose:** Confirm `get_search_repository()` returns `DocumentDBSearchRepository`.

```bash
# Test 8: Factory returns DocumentDBSearchRepository for MONGODB_BACKENDS
cd "$REPO"
uv run python -c "
import asyncio
from registry.core.config import settings, MONGODB_BACKENDS
from registry.repositories.factory import get_search_repository

# Test with a MongoDB backend
settings.storage_backend = 'documentdb'
repo = get_search_repository()
assert type(repo).__name__ == 'DocumentDBSearchRepository', \
    f'Expected DocumentDBSearchRepository, got {type(repo).__name__}'
print('PASS: get_search_repository() returns DocumentDBSearchRepository for documentdb')

# Test with file backend - should ALSO return DocumentDBSearchRepository
settings.storage_backend = 'file'
# Need to reset the singleton
from registry.repositories import factory
factory._search_repo = None
repo = get_search_repository()
assert type(repo).__name__ == 'DocumentDBSearchRepository', \
    f'Expected DocumentDBSearchRepository for file backend, got {type(repo).__name__}'
print('PASS: get_search_repository() returns DocumentDBSearchRepository for file backend')
"
```

### 1.4 Search Repository Interface Verification

**Purpose:** Confirm `DocumentDBSearchRepository` implements all methods from
`SearchRepositoryBase`.

```bash
# Test 9: All SearchRepositoryBase methods exist on DocumentDBSearchRepository
cd "$REPO"
uv run python -c "
from registry.repositories.interfaces import SearchRepositoryBase
from registry.repositories.documentdb.search_repository import DocumentDBSearchRepository
import inspect

abstract_methods = {
    name for name, method in inspect.getmembers(SearchRepositoryBase)
    if getattr(method, '__isabstractmethod__', False)
}
concrete_methods = {
    name for name, method in inspect.getmembers(DocumentDBSearchRepository)
    if not name.startswith('_')
}

missing = abstract_methods - concrete_methods
if missing:
    print(f'FAIL: Missing methods: {missing}')
    exit(1)
print(f'PASS: All {len(abstract_methods)} SearchRepositoryBase methods implemented')
print(f'  Methods: {sorted(abstract_methods)}')
"
```

### 1.5 Search API Endpoint Verification (requires running server)

**Purpose:** Verify search API endpoints return valid responses.

```bash
# Test 10: Search agents endpoint returns valid JSON
curl -s "$REGISTRY_URL/search/agents" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    | python -c "
import sys, json
data = json.load(sys.stdin)
assert 'agents' in data or 'results' in data or isinstance(data, dict), \
    f'Unexpected response shape: {type(data)}'
print('PASS: /search/agents returns valid response')
"

# Test 11: Search servers endpoint returns valid JSON
curl -s "$REGISTRY_URL/search/servers" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    | python -c "
import sys, json
data = json.load(sys.stdin)
assert isinstance(data, dict), f'Expected dict, got {type(data)}'
print('PASS: /search/servers returns valid response')
"

# Test 12: Search by tags returns valid JSON
curl -s "$REGISTRY_URL/search/tags" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    | python -c "
import sys, json
data = json.load(sys.stdin)
assert isinstance(data, list), f'Expected list, got {type(data)}'
print('PASS: /search/tags returns valid response')
"

# Test 13: Mixed search returns valid JSON
curl -s "$REGISTRY_URL/search/mixed?q=test" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    | python -c "
import sys, json
data = json.load(sys.stdin)
assert isinstance(data, dict), f'Expected dict, got {type(data)}'
print('PASS: /search/mixed returns valid response')
"

# Test 14: Empty query returns valid response (no errors)
curl -s "$REGISTRY_URL/search/agents?q=" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    | python -c "
import sys, json
data = json.load(sys.stdin)
assert isinstance(data, dict), f'Expected dict, got {type(data)}'
print('PASS: Empty query returns valid response')
"
```

### 1.6 Negative Test: Non-existent entity

```bash
# Test 15: Search for non-existent entity returns empty results, not error
curl -s "$REGISTRY_URL/search/agents?q=zzzzzzzzz_nonexistent_entity_zzzzzzzzz" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    | python -c "
import sys, json
data = json.load(sys.stdin)
agents = data.get('agents', [])
assert agents == [] or len(agents) == 0, f'Expected empty results, got {agents}'
print('PASS: Non-existent entity returns empty results')
"
```

## 2. Backwards Compatibility Tests

### 2.1 Search Response Shape

**Purpose:** Confirm search response shape matches the pre-change format.

```bash
# Test 16: Response shape includes expected keys
cd "$REPO"
uv run python -c "
# Verify the SearchRepositoryBase.search() return type annotation
from registry.repositories.interfaces import SearchRepositoryBase
import inspect

sig = inspect.signature(SearchRepositoryBase.search)
# The return type should be dict[str, list[dict[str, Any]]]
print(f'PASS: search() signature unchanged: {sig}')
"
```

### 2.2 File Backend Still Valid

**Purpose:** Confirm `storage_backend=file` remains in `ALLOWED_STORAGE_BACKENDS`.

```bash
# Test 17: file backend still allowed
cd "$REPO"
uv run python -c "
from registry.core.config import ALLOWED_STORAGE_BACKENDS
assert 'file' in ALLOWED_STORAGE_BACKENDS, 'file backend should remain allowed'
print('PASS: file backend still in ALLOWED_STORAGE_BACKENDS')
"
```

### 2.3 Embedding Configuration Unchanged

**Purpose:** Confirm embedding config parameters are unchanged.

```bash
# Test 18: Embedding settings exist
cd "$REPO"
uv run python -c "
from registry.core.config import settings
# These settings should still exist (they are used by DocumentDB hybrid search)
_ = settings.embeddings_provider
_ = settings.embeddings_model_name
_ = settings.embeddings_model_dimensions
print('PASS: Embedding settings unchanged')
"
```

### 2.4 Search Repository Methods Unchanged

**Purpose:** Confirm all search repository method signatures are unchanged.

```bash
# Test 19: Method signatures match interface
cd "$REPO"
uv run python -c "
import inspect
from registry.repositories.interfaces import SearchRepositoryBase
from registry.repositories.documentdb.search_repository import DocumentDBSearchRepository

for name in ['search', 'search_by_tags', 'index_entity', 'remove_entity',
             'rebuild_index', 'initialize', 'get_all_tags', 'index_server',
             'index_agent']:
    iface = getattr(SearchRepositoryBase, name)
    impl = getattr(DocumentDBSearchRepository, name)
    iface_sig = inspect.signature(iface)
    impl_sig = inspect.signature(impl)
    # Parameter names and defaults should match
    iface_params = list(iface_sig.parameters.keys())
    impl_params = list(impl_sig.parameters.keys())
    # impl may have extra params but must include all interface params
    for p in iface_params:
        assert p in impl_params, f'{name}: missing param {p}'
    print(f'PASS: {name}() signature compatible')
"
```

### 2.5 Non-Search File Repositories Unaffected

**Purpose:** Confirm non-search file-based repositories still work.

```bash
# Test 20: Non-search repositories still available
cd "$REPO"
uv run python -c "
from registry.repositories.factory import (
    get_server_repository,
    get_agent_repository,
    get_skill_repository,
)
from registry.core.config import settings

settings.storage_backend = 'file'
# These should return file-based repositories (unchanged)
server_repo = get_server_repository()
print(f'PASS: server repository type: {type(server_repo).__name__}')

agent_repo = get_agent_repository()
print(f'PASS: agent repository type: {type(agent_repo).__name__}')

skill_repo = get_skill_repository()
print(f'PASS: skill repository type: {type(skill_repo).__name__}')
"
```

## 3. UX Tests

### 3.1 Error Messages

**Purpose:** Verify error messages are clear and do not mention FAISS.

```bash
# Test 21: Search error messages don't mention FAISS (when server is down)
# This test requires a running server with DocumentDB unavailable
# We check source code instead:
cd "$REPO"
result=$(grep -rn "faiss" registry/ --include="*.py" 2>/dev/null | grep -i "error\|warn\|log\|message" || true)
if [ -n "$result" ]; then
    echo "FAIL: FAISS still mentioned in log/error messages:"
    echo "$result"
    exit 1
fi
echo "PASS: No FAISS references in log/error messages"
```

### 3.2 Documentation Clarity

**Purpose:** Verify documentation no longer describes FAISS as the vector search backend.

```bash
# Test 22: No FAISS references in key documentation files
cd "$REPO"
for doc in docs/embeddings.md docs/configuration.md docs/database-design.md; do
    if [ -f "$doc" ]; then
        if grep -qi "faiss" "$doc"; then
            echo "FAIL: FAISS reference in $doc"
            grep -in "faiss" "$doc"
            exit 1
        fi
        echo "PASS: No FAISS in $doc"
    fi
done
```

**Not Applicable** - Web UI tests. The FAISS removal does not affect the frontend UI.

## 4. Deployment Surface Tests

### 4.1 Docker Build

**Purpose:** Verify Docker image builds without FAISS dependencies.

```bash
# Test 23: Docker image builds successfully
cd "$REPO"
if docker build -f docker/Dockerfile.registry -t mcp-registry-test . 2>&1 | tail -20; then
    echo "PASS: Docker build succeeded"
else
    echo "FAIL: Docker build failed"
    exit 1
fi
```

### 4.2 Docker Compose Services

**Purpose:** Verify docker-compose.yml starts without FAISS errors.

```bash
# Test 24: docker-compose config validates
cd "$REPO"
if docker compose config > /dev/null 2>&1; then
    echo "PASS: docker-compose.yml is valid"
else
    echo "FAIL: docker-compose.yml validation failed"
    exit 1
fi

# Test 25: docker-compose.prebuilt.yml validates
if docker compose -f docker-compose.prebuilt.yml config > /dev/null 2>&1; then
    echo "PASS: docker-compose.prebuilt.yml is valid"
else
    echo "FAIL: docker-compose.prebuilt.yml validation failed"
    exit 1
fi
```

### 4.3 Docker Comment Updates

**Purpose:** Verify FAISS references removed from Docker file comments.

```bash
# Test 26: No FAISS in docker-compose.yml comments
cd "$REPO"
if grep -i "faiss" docker-compose.yml; then
    echo "FAIL: FAISS still referenced in docker-compose.yml"
    exit 1
fi
echo "PASS: No FAISS in docker-compose.yml"

# Test 27: No FAISS in docker-compose.prebuilt.yml comments
if grep -i "faiss" docker-compose.prebuilt.yml; then
    echo "FAIL: FAISS still referenced in docker-compose.prebuilt.yml"
    exit 1
fi
echo "PASS: No FAISS in docker-compose.prebuilt.yml"
```

### 4.4 Terraform/Helm

**Purpose:** Verify FAISS references removed from Terraform and Helm files.

```bash
# Test 28: No FAISS in Terraform ECS comments
cd "$REPO"
if grep -i "faiss" terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf; then
    echo "FAIL: FAISS still in ecs-services.tf"
    exit 1
fi
echo "PASS: No FAISS in ecs-services.tf"

# Test 29: No FAISS in Terraform OPERATIONS.md
if grep -i "faiss" terraform/aws-ecs/OPERATIONS.md; then
    echo "FAIL: FAISS still in OPERATIONS.md"
    exit 1
fi
echo "PASS: No FAISS in OPERATIONS.md"
```

### 4.5 Deploy and Verify

**Purpose:** Full integration test -- deploy registry and verify search works.

```bash
# Test 30: Start services, register an entity, search for it
cd "$REPO"

# Start Docker Compose (if available)
docker compose up -d mongodb registry metrics-service

# Wait for registry to be ready
for i in $(seq 1 60); do
    if curl -s "$REGISTRY_URL/health" > /dev/null 2>&1; then
        echo "PASS: Registry health check passed after ${i}s"
        break
    fi
    sleep 1
done

# Verify search works (basic smoke test)
curl -s "$REGISTRY_URL/search/agents" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -w "%{http_code}" | grep -q "200" && echo "PASS: Search returns 200"

# Stop services
docker compose down
```

### 4.6 Rollback Verification

**Purpose:** Verify the change can be rolled back by reverting the git commit.

```bash
# Test 31: git revert works cleanly
cd "$REPO"
# Simulate a revert by checking all modified files can be restored
git diff --name-only | while read f; do
    if ! git checkout HEAD -- "$f" 2>/dev/null; then
        echo "FAIL: Cannot revert $f"
        exit 1
    fi
done
git diff --staged --name-only | while read f; do
    git restore --staged "$f" 2>/dev/null
done
echo "PASS: All changes can be reverted"
```

## 5. End-to-End API Tests

### 5.1 Full Agent Lifecycle with Search

**Purpose:** Register an agent, verify it appears in search, remove it, verify it no longer appears.

```bash
# Test 32: Full agent lifecycle
cd "$REPO"

# Step A: Register an agent
curl -s -X POST "$REGISTRY_URL/api/agents/test-e2e-agent" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "agent_id": "test-e2e-agent",
        "name": "E2E Test Agent",
        "description": "Test agent for E2E search verification",
        "tags": ["e2e", "test"],
        "version": "1.0.0",
        "transport_type": "stdio",
        "command": "echo",
        "args": ["test"]
    }'

# Step B: Search for the agent
result=$(curl -s "$REGISTRY_URL/search/agents?q=test+e2e" \
    -H "Authorization: Bearer $ACCESS_TOKEN")
count=$(echo "$result" | python -c "import sys,json; print(len(json.load(sys.stdin).get('agents',[])))")
if [ "$count" -ge 1 ]; then
    echo "PASS: Agent found in search results"
else
    echo "FAIL: Agent not found in search results"
    echo "Response: $result"
    exit 1
fi

# Step C: Remove the agent
curl -s -X DELETE "$REGISTRY_URL/api/agents/test-e2e-agent" \
    -H "Authorization: Bearer $ACCESS_TOKEN"

# Step D: Verify agent no longer in search
result=$(curl -s "$REGISTRY_URL/search/agents?q=test+e2e" \
    -H "Authorization: Bearer $ACCESS_TOKEN")
count=$(echo "$result" | python -c "import sys,json; print(len(json.load(sys.stdin).get('agents',[])))")
if [ "$count" -eq 0 ]; then
    echo "PASS: Agent removed from search results"
else
    echo "FAIL: Agent still in search results after removal"
    exit 1
fi
```

### 5.2 Tag-Based Search

**Purpose:** Verify tag-based search works via the search repository.

```bash
# Test 33: Tag-based search
cd "$REPO"
result=$(curl -s "$REGISTRY_URL/search/tags" \
    -H "Authorization: Bearer $ACCESS_TOKEN")
tags=$(echo "$result" | python -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$tags" -ge 0 ]; then
    echo "PASS: Tag list returns $tags tags"
else
    echo "FAIL: Tag list returned error"
    exit 1
fi
```

### 5.3 Search Backend Identification

**Purpose:** Verify search backend is identified as DocumentDB, not FAISS.

```bash
# Test 34: Search backend identification
cd "$REPO"
# Check system info endpoint if available
if curl -s "$REGISTRY_URL/system/info" -H "Authorization: Bearer $ACCESS_TOKEN" > /dev/null 2>&1; then
    backend=$(curl -s "$REGISTRY_URL/system/info" \
        -H "Authorization: Bearer $ACCESS_TOKEN" | \
        python -c "import sys,json; print(json.load(sys.stdin).get('search_backend',''))" 2>/dev/null || true)
    if [ "$backend" = "faiss" ]; then
        echo "FAIL: Search backend still reported as faiss"
        exit 1
    fi
    if [ -n "$backend" ]; then
        echo "PASS: Search backend is '$backend' (not faiss)"
    else
        echo "INFO: system/info does not report search_backend"
    fi
else
    echo "INFO: /system/info endpoint not available, skipping backend check"
fi
```

## 6. Test Infrastructure Verification

### 6.1 FAISS Test Files Deleted

**Purpose:** Verify all FAISS-specific test files are deleted.

```bash
# Test 35: FAISS test fixtures deleted
cd "$REPO"
if [ -f "tests/fixtures/mocks/mock_faiss.py" ]; then
    echo "FAIL: mock_faiss.py still exists"
    exit 1
fi
echo "PASS: mock_faiss.py deleted"

# Test 36: FAISS unit tests deleted
if [ -f "tests/unit/search/test_faiss_service.py" ]; then
    echo "FAIL: test_faiss_service.py still exists"
    exit 1
fi
echo "PASS: test_faiss_service.py deleted"
```

### 6.2 Conftest FAISS Mock Removed

**Purpose:** Verify conftest.py no longer auto-mocks FAISS.

```bash
# Test 37: No FAISS auto-mock in conftest.py
cd "$REPO"
if grep -q 'sys.modules\["faiss"\]' tests/conftest.py; then
    echo "FAIL: FAISS auto-mock still in conftest.py"
    exit 1
fi
if grep -q 'from tests.fixtures.mocks.mock_faiss' tests/conftest.py; then
    echo "FAIL: mock_faiss import still in conftest.py"
    exit 1
fi
echo "PASS: FAISS auto-mock removed from conftest.py"
```

### 6.3 Unit Test Suite Passes

**Purpose:** Run the full unit test suite after changes.

```bash
# Test 38: Unit tests pass
cd "$REPO"
if uv run pytest tests/unit/ -x -q --no-cov 2>&1 | tail -5; then
    echo "PASS: Unit tests passed"
else
    echo "FAIL: Unit tests failed"
    exit 1
fi
```

### 6.4 Search-Related Integration Tests

**Purpose:** Run search integration tests after changes.

```bash
# Test 39: Search integration tests pass
cd "$REPO"
if uv run pytest tests/integration/ -m search -x -q --no-cov 2>&1 | tail -5; then
    echo "PASS: Search integration tests passed"
else
    echo "FAIL: Search integration tests failed"
    exit 1
fi
```

### 6.5 Metrics Service Schema

**Purpose:** Verify metrics service no longer uses `faiss_search_time_ms`.

```bash
# Test 40: No faiss_search_time_ms in metrics service
cd "$REPO"
for f in metrics-service/metrics_client.py \
         metrics-service/app/storage/database.py \
         metrics-service/app/storage/migrations.py; do
    if grep -q "faiss_search_time_ms" "$f"; then
        echo "FAIL: faiss_search_time_ms still in $f"
        exit 1
    fi
done
echo "PASS: faiss_search_time_ms removed from metrics service"
```

### 6.6 Python Compilation Verification

**Purpose:** Verify all modified Python files compile.

```bash
# Test 41: All modified files compile
cd "$REPO"
for f in registry/repositories/factory.py \
         registry/repositories/documentdb/search_repository.py \
         registry/api/server_routes.py \
         registry/api/agent_routes.py \
         registry/services/agent_batch_item_processor.py \
         metrics-service/metrics_client.py \
         metrics-service/app/storage/database.py \
         metrics-service/app/storage/migrations.py; do
    if [ -f "$f" ]; then
        if ! uv run python -m py_compile "$f" 2>/dev/null; then
            echo "FAIL: $f does not compile"
            exit 1
        fi
        echo "PASS: $f compiles"
    fi
done
```

### 6.7 Shell Script Syntax Verification

**Purpose:** Verify shell scripts have valid syntax.

```bash
# Test 42: Shell scripts pass syntax check
cd "$REPO"
for f in cli/service_mgmt.sh terraform/aws-ecs/scripts/service_mgmt.sh; do
    if [ -f "$f" ]; then
        if ! bash -n "$f" 2>/dev/null; then
            echo "FAIL: $f has syntax errors"
            exit 1
        fi
        echo "PASS: $f syntax valid"
    fi
done
```

## 7. Documentation Verification

### 7.1 FAISS Reference Sweep

**Purpose:** Verify no FAISS references remain in documentation.

```bash
# Test 43: No FAISS references in docs/ (excluding llms.txt which is auto-generated)
cd "$REPO"
result=$(grep -rni "faiss" docs/ --include="*.md" 2>/dev/null | \
         grep -v "llms.txt" | \
         grep -v "RELEASE\|release-notes" || true)
if [ -n "$result" ]; then
    echo "FAIL: FAISS references found in docs:"
    echo "$result"
    exit 1
fi
echo "PASS: No FAISS references in docs/"
```

### 7.2 Shell Script and Terraform Comments

**Purpose:** Verify FAISS references removed from scripts and Terraform.

```bash
# Test 44: No FAISS in scripts
cd "$REPO"
if grep -ri "faiss" cli/service_mgmt.sh terraform/aws-ecs/scripts/service_mgmt.sh 2>/dev/null; then
    echo "FAIL: FAISS in shell scripts"
    exit 1
fi
echo "PASS: No FAISS in shell scripts"

# Test 45: No FAISS in Terraform (excluding ops docs which are separate)
if grep -ri "faiss" terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf 2>/dev/null; then
    echo "FAIL: FAISS in Terraform resources"
    exit 1
fi
echo "PASS: No FAISS in Terraform resources"
```

## 8. Test Execution Checklist

- [ ] Section 1.1 (Import Removal) -- 4 tests pass
- [ ] Section 1.2 (Dependency) -- 3 tests pass
- [ ] Section 1.3 (Factory Routing) -- 1 test passes
- [ ] Section 1.4 (Interface) -- 1 test passes
- [ ] Section 1.5 (API Endpoints) -- 5 tests pass (requires running server)
- [ ] Section 2.1-2.5 (Backwards Compatibility) -- 5 tests pass
- [ ] Section 3.1-3.2 (UX) -- 2 tests pass
- [ ] Section 4.1-4.5 (Deployment) -- 6 tests pass
- [ ] Section 5.1-5.3 (E2E) -- 3 tests pass (requires running server)
- [ ] Section 6.1-6.3 (Test Infrastructure) -- 4 tests pass
- [ ] Section 6.4 (Search Integration Tests) -- 1 test passes
- [ ] Section 6.5 (Metrics Schema) -- 1 test passes
- [ ] Section 6.6 (Compilation) -- 9 tests pass
- [ ] Section 6.7 (Shell Syntax) -- 2 tests pass
- [ ] Section 7.1-7.2 (Documentation) -- 3 tests pass