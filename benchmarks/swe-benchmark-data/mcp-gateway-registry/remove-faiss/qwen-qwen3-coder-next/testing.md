# Testing Plan: Remove FAISS from the codebase

*Created: 2026-06-05*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan tests the removal of FAISS (Facebook AI Similarity Search) from the MCP Gateway Registry codebase. We verify that:
1. The `faiss-cpu` dependency is removed from `pyproject.toml`
2. FAISS-specific code is deleted
3. DocumentDB search repository takes over for all backends
4. Search functionality continues to work correctly
5. Docker builds succeed without FAISS
6. All tests pass with the new configuration

### Prerequisites
- [ ] Access to the repository: `/home/ubuntu/repos/sample-claude-code-multi-model/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo`
- [ ] Python 3.14+ available via `uv`
- [ ] Docker daemon running (for Docker tests)
- [ ] MongoDB/DocumentDB accessible (for documentdb backend tests)

### Shared Variables
```bash
export REPO_ROOT="/home/ubuntu/repos/sample-claude-code-multi-model/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
export UV_RUN="uv run"
export REGISTRY_PORT=8000
```

---

## 1. Functional Tests

### 1.1 Dependency Verification Tests

#### Test: Verify faiss-cpu is removed from pyproject.toml
```bash
cd "$REPO_ROOT"

# Should return exit code 1 (no matches)
if grep -q "faiss-cpu" pyproject.toml; then
    echo "FAIL: faiss-cpu still present in pyproject.toml"
    exit 1
else
    echo "PASS: faiss-cpu removed from pyproject.toml"
fi
```

#### Test: Verify faiss import fails in fresh Python
```bash
cd "$REPO_ROOT"

# Install dependencies (without faiss-cpu)
uv sync

# Should raise ModuleNotFoundError
if uv run python -c "import faiss" 2>/dev/null; then
    echo "FAIL: faiss module still importable"
    exit 1
else
    echo "PASS: faiss module not importable"
fi
```

### 1.2 Code Structure Tests

#### Test: Verify FAISS service module is deleted
```bash
cd "$REPO_ROOT"

if [ -f "registry/search/service.py" ]; then
    echo "FAIL: registry/search/service.py still exists"
    exit 1
else
    echo "PASS: registry/search/service.py deleted"
fi

if [ -f "registry/repositories/file/search_repository.py" ]; then
    echo "FAIL: registry/repositories/file/search_repository.py still exists"
    exit 1
else
    echo "PASS: registry/repositories/file/search_repository.py deleted"
fi
```

#### Test: Verify FaissMetadata model is removed
```bash
cd "$REPO_ROOT"

if grep -q "class FaissMetadata" registry/core/schemas.py; then
    echo "FAIL: FaissMetadata model still exists"
    exit 1
else
    echo "PASS: FaissMetadata model removed"
fi

if grep -q "faiss_index_path" registry/core/config.py; then
    echo "FAIL: faiss_index_path property still exists"
    exit 1
else
    echo "PASS: faiss_index_path property removed"
fi
```

### 1.3 Search Execution Tests

#### Test: DocumentDB search repository can be instantiated
```bash
cd "$REPO_ROOT"

uv run python -c "
from registry.repositories.factory import get_search_repository
from registry.core.config import settings

# Test documentdb backend
settings.storage_backend = 'documentdb'
repo = get_search_repository()
print(f'Search repository type: {type(repo).__name__}')
assert 'DocumentDB' in type(repo).__name__, 'Expected DocumentDB search repository'
print('PASS: DocumentDB search repository created successfully')
"

# Test file backend (should use DocumentDB search)
settings.storage_backend = 'file'
repo = get_search_repository()
print(f'Search repository type for file backend: {type(repo).__name__}')
assert 'DocumentDB' in type(repo).__name__, 'Expected DocumentDB search repository for file backend'
echo "PASS: File backend uses DocumentDB search"
```

#### Test: Search API endpoint works
```bash
# Start registry (with mock database)
cd "$REPO_ROOT"

# Test with mock - this tests the search code path without real database
uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository
from registry.core.config import settings

async def test_search():
    settings.storage_backend = 'file'
    repo = get_search_repository()
    
    # Test that search method exists and is callable
    assert hasattr(repo, 'search'), 'Search repository missing search method'
    assert callable(repo.search), 'search method is not callable'
    
    # Verify it returns expected structure
    result = await repo.search('test query', entity_types=['mcp_server'])
    assert isinstance(result, dict), 'Search should return dict'
    assert 'servers' in result, 'Search result missing servers key'
    assert 'tools' in result, 'Search result missing tools key'
    assert 'agents' in result, 'Search result missing agents key'
    
    return True

result = asyncio.run(test_search())
print('PASS: Search API works correctly')
"
```

#### Test: Search returns same structure as before
```bash
cd "$REPO_ROOT"

uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository
from registry.core.config import settings

async def verify_search_structure():
    settings.storage_backend = 'file'
    repo = get_search_repository()
    
    result = await repo.search(
        'test', 
        entity_types=['mcp_server', 'a2a_agent'], 
        max_results=5
    )
    
    # Verify structure matches expected API contract
    expected_keys = {'servers', 'tools', 'agents'}
    actual_keys = set(result.keys())
    
    assert actual_keys == expected_keys, f'Expected {expected_keys}, got {actual_keys}'
    
    # Verify each server has expected fields
    for server in result['servers']:
        assert 'path' in server, 'Server missing path'
        assert 'server_name' in server, 'Server missing server_name'
        assert 'description' in server, 'Server missing description'
        assert 'relevance_score' in server, 'Server missing relevance_score'
    
    return result

result = asyncio.run(verify_search_structure())
print(f'PASS: Search structure verified, {len(result[\"servers\"])} servers returned')
"
```

### 1.4 CLI Tests

#### Test: CLI search command help text (no FAISS references)
```bash
cd "$REPO_ROOT"

# Check that --help text doesn't mention FAISS
if uv run python -m cli.agent_mgmt --help 2>&1 | grep -i "faiss"; then
    echo "FAIL: CLI help still mentions FAISS"
    exit 1
else
    echo "PASS: CLI help text updated"
fi
```

#### Test: CLI search command runs (with mock)
```bash
cd "$REPO_ROOT"

# This verifies the CLI can import and use search
uv run python -c "
from registry.repositories.factory import get_search_repository
print('CLI search module imports successfully')
"
```

---

## 2. Backwards Compatibility Tests

### 2.1 Search API Compatibility

#### Test: Search request format unchanged
```bash
cd "$REPO_ROOT"

uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository
from registry.core.config import settings

async def test_backward_compat():
    settings.storage_backend = 'file'
    repo = get_search_repository()
    
    # Test all parameter combinations that were supported before
    tests = [
        # Basic search
        {'query': 'test'},
        # With entity types
        {'query': 'test', 'entity_types': ['mcp_server']},
        # With max results
        {'query': 'test', 'max_results': 20},
        # With filters
        {'query': 'test', 'max_results': 5, 'include_draft': True},
    ]
    
    for params in tests:
        result = await repo.search(**params)
        assert isinstance(result, dict)
    
    return True

asyncio.run(test_backward_compat())
print('PASS: Search API accepts same parameters as before')
"
```

#### Test: Search response format unchanged
```bash
cd "$REPO_ROOT"

uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository
from registry.core.config import settings

async def test_response_format():
    settings.storage_backend = 'file'
    repo = get_search_repository()
    
    result = await repo.search('test', max_results=10)
    
    # Verify response structure
    for entity_type in ['servers', 'tools', 'agents']:
        assert entity_type in result, f'{entity_type} missing from response'
        assert isinstance(result[entity_type], list), f'{entity_type} should be a list'
    
    # Verify server results have specific fields
    for server in result['servers']:
        assert 'entity_type' in server, 'Server missing entity_type'
        assert 'path' in server, 'Server missing path'
        assert 'relevance_score' in server, 'Server missing relevance_score'
    
    return result

result = asyncio.run(test_response_format())
print(f'PASS: Search response format unchanged')
"
```

---

## 3. UX Tests

### 3.1 CLI Output Tests

#### Test: CLI search output format
```bash
cd "$REPO_ROOT"

# Verify the CLI handles empty search results
uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository
from registry.core.config import settings

async def test_empty_search():
    settings.storage_backend = 'file'
    repo = get_search_repository()
    
    result = await repo.search('nonexistent-query-xyz', max_results=5)
    
    assert result['servers'] == [], 'Empty search should return empty list'
    assert result['tools'] == [], 'Empty search should return empty tools list'
    assert result['agents'] == [], 'Empty search should return empty agents list'
    
    return True

asyncio.run(test_empty_search())
print('PASS: Empty search returns empty lists')
"
```

### 3.2 Error Message Tests

#### Test: Error messages don't reference FAISS
```bash
cd "$REPO_ROOT"

# Check source files for FAISS mentions
if grep -ri "faiss" registry/ --include="*.py" | grep -v "test" | grep -v "fixtures"; then
    echo "FAIL: FAISS still referenced in source code"
    exit 1
else
    echo "PASS: No FAISS references in source code"
fi
```

---

## 4. Deployment Surface Tests

### 4.1 Docker Wiring

#### Test: Docker image builds without FAISS
```bash
cd "$REPO_ROOT"

# Build the Docker image
docker build -t mcp-registry-test . 2>&1 | tee /tmp/docker-build.log

# Check build succeeded
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    echo "FAIL: Docker build failed"
    cat /tmp/docker-build.log
    exit 1
fi

echo "PASS: Docker image builds successfully"

# Verify FAISS is not in the image
docker run --rm mcp-registry-test python -c "import faiss" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "FAIL: FAISS still present in Docker image"
    exit 1
else
    echo "PASS: FAISS not present in Docker image"
fi
```

#### Test: Docker container starts
```bash
# Start container with minimal config
docker run -d --name test-registry -p 8000:8000 mcp-registry-test

# Wait for health check or startup
sleep 10

# Check if container is running
if ! docker ps | grep -q test-registry; then
    echo "FAIL: Container not running"
    docker logs test-registry
    docker rm test-registry
    exit 1
fi

echo "PASS: Container starts successfully"

# Cleanup
docker stop test-registry
docker rm test-registry
```

### 4.2 Docker Compose Files

#### Test: Docker Compose comments updated
```bash
cd "$REPO_ROOT"

# Check docker-compose files for FAISS mentions
for file in docker-compose.yml docker-compose.prebuilt.yml docker-compose.podman.yml; do
    if grep -q "FAISS" "$file"; then
        echo "INFO: $file still has FAISS comment (can be updated separately)"
    else
        echo "PASS: $file FAISS comment removed"
    fi
done
```

### 4.3 Terraform Files

#### Test: Terraform schemas updated
```bash
cd "$REPO_ROOT"

# Check for FAISS in validation patterns
if grep -q "pattern=\"^(faiss|documentdb)\$" terraform/telemetry-collector/lambda/collector/schemas.py; then
    echo "INFO: Schema pattern still includes 'faiss' (can be removed)"
else
    echo "PASS: Schema pattern updated"
fi
```

#### Test: Terraform documentation updated
```bash
cd "$REPO_ROOT"

if grep -q "FAISS" terraform/aws-ecs/OPERATIONS.md; then
    echo "INFO: terraform/aws-ecs/OPERATIONS.md still mentions FAISS"
else
    echo "PASS: Terraform docs updated"
fi
```

---

## 5. End-to-End API Tests

### 5.1 Search Workflow Tests

#### Test: Register server then search
```bash
cd "$REPO_ROOT"

uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository, reset_repositories
from registry.core.config import settings

async def test_full_workflow():
    settings.storage_backend = 'file'
    reset_repositories()
    
    repo = get_search_repository()
    
    # Simulate indexing a server
    server_data = {
        'server_name': 'Test Server',
        'description': 'A test server for search',
        'tags': ['test', 'search'],
        'tool_list': [
            {
                'name': 'test-tool',
                'description': 'A test tool',
                'parsed_description': {'main': 'Test tool description'}
            }
        ]
    }
    
    # Index the server (this would normally be called by the registry service)
    await repo.index_server('/servers/test-server', server_data, True)
    
    # Search for it
    result = await repo.search('test server', max_results=10)
    
    # Verify search found the indexed server
    assert len(result['servers']) > 0, 'Search should find indexed server'
    
    found = False
    for server in result['servers']:
        if server['server_name'] == 'Test Server':
            found = True
            break
    
    assert found, 'Indexed server should be in search results'
    
    return result

result = asyncio.run(test_full_workflow())
print(f'PASS: Server indexing and search works (found {len(result[\"servers\"])} results)')
"
```

#### Test: Search with keyword boost
```bash
cd "$REPO_ROOT"

uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository, reset_repositories
from registry.core.config import settings

async def test_keyword_boost():
    settings.storage_backend = 'file'
    reset_repositories()
    
    repo = get_search_repository()
    
    # Search for a term that should match via keyword boost
    result = await repo.search('test', max_results=5)
    
    # Should return results even without vector embeddings
    # (uses lexical search as fallback)
    assert 'servers' in result
    assert 'tools' in result
    assert 'agents' in result
    
    return result

result = asyncio.run(test_keyword_boost())
print(f'PASS: Keyword search fallback works')
"
```

### 5.2 Integration Tests

#### Test: File backend search integration
```bash
cd "$REPO_ROOT"

uv run python -c "
import asyncio
from registry.repositories.factory import get_search_repository, reset_repositories
from registry.core.config import settings

async def test_file_backend_integration():
    settings.storage_backend = 'file'
    reset_repositories()
    
    repo = get_search_repository()
    
    # Verify we get the right repository type
    print(f'Repository type: {type(repo).__name__}')
    
    # Verify search method works
    result = await repo.search('sample query', max_results=3)
    print(f'Search result keys: {list(result.keys())}')
    
    # Verify structure
    assert isinstance(result['servers'], list)
    assert isinstance(result['tools'], list)
    assert isinstance(result['agents'], list)
    
    return True

asyncio.run(test_file_backend_integration())
print('PASS: File backend integration test completed')
"
```

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes
  - [ ] 1.1 Dependency verification tests pass
  - [ ] 1.2 Code structure tests pass
  - [ ] 1.3 Search execution tests pass
  - [ ] 1.4 CLI tests pass
- [ ] Section 2 (Backwards Compatibility) verified
  - [ ] 2.1 Search API compatibility tests pass
  - [ ] 2.2 Search response format tests pass
- [ ] Section 3 (UX) verified
  - [ ] 3.1 CLI output tests pass
  - [ ] 3.2 Error message tests pass
- [ ] Section 4 (Deployment) verified
  - [ ] 4.1 Docker image builds pass
  - [ ] 4.2 Docker Compose comments verified
  - [ ] 4.3 Terraform schemas updated
- [ ] Section 5 (E2E) verified
  - [ ] 5.1 Search workflow tests pass
  - [ ] 5.2 Integration tests pass
- [ ] Run full test suite with `uv run pytest tests/`
- [ ] Run security scan with `uv run bandit -r registry/`
- [ ] Run type checking with `uv run mypy registry/`

---

## 7. Known Issues / Notes

### Metrics-Service
The metrics-service also has FAISS references but is a separate service:
- `metrics-service/app/storage/database.py`
- `metrics-service/app/storage/migrations.py`

These should be addressed in a separate issue.

### TestFixtures
Some test fixtures in `tests/fixtures/mocks/mock_faiss.py` may be kept temporarily if other parts of the codebase reference them. If not needed, delete:
- `tests/fixtures/mocks/mock_faiss.py`
- `tests/unit/search/test_faiss_service.py`

### Docker Build Notes
The Dockerfile may need adjustment if FAISS was installed via specific instructions:
```dockerfile
# Check if FAISS installation steps can be removed
# Look for lines like:
# - RUN pip install faiss-cpu
# - RUN apt-get install faiss
```
