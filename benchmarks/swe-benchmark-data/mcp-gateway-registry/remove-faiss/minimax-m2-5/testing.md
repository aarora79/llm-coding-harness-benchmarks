# Testing Plan: Remove FAISS from Codebase

*Created: 2026-06-12*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This testing plan verifies that the FAISS removal was successful and that the new FileSearchRepository maintains the same API contract while providing keyword-only search functionality.

### Prerequisites
- Python 3.11+
- `uv` package manager
- No running services required (this is component testing)

### Shared Variables
```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
BENCH_DIR="$REPO_ROOT/benchmarks/swe-benchmark-data/mcp-gateway-registry"
export SEARCH_BACKEND="file"
```

## 1. Verify FAISS is Removed

### 1.1 No FAISS Imports
```bash
cd "$BENCH_DIR/repo"
grep -r "import faiss" --include="*.py" . && echo "FAIL: FAISS imports found" || echo "PASS: No FAISS imports"
```
**Expected:** No output, then "PASS: No FAISS imports"

### 1.2 No FAISS in Dependencies
```bash
cd "$BENCH_DIR/repo"
grep -i "faiss" pyproject.toml && echo "FAIL: FAISS in dependencies" || echo "PASS: No FAISS in dependencies"
```
**Expected:** No output, then "PASS: No FAISS in dependencies"

### 1.3 FaissService File Deleted
```bash
test -f "$BENCH_DIR/repo/registry/search/service.py" && echo "FAIL: FaissService still exists" || echo "PASS: FaissService deleted"
```
**Expected:** "PASS: FaissService deleted"

### 1.4 No FAISS Mock Files
```bash
test -f "$BENCH_DIR/repo/tests/fixtures/mocks/mock_faiss.py" && echo "FAIL: mock_faiss.py exists" || echo "PASS: mock_faiss.py deleted"
```
**Expected:** "PASS: mock_faiss.py deleted"

---

## 2. Functional Tests

### 2.1 Import Tests
Test that the FileSearchRepository can be imported without errors:

```bash
cd "$BENCH_DIR/repo"
python -c "
from registry.repositories.file.search_repository import FileSearchRepository
print('FileSearchRepository imported successfully')
print(f'Methods: {[m for m in dir(FileSearchRepository) if not m.startswith(\"_\")]}')
"
```
**Expected:** Print includes `search`, `index_server`, `index_agent`, `remove_entity`, `search_by_tags`, `get_all_tags`, `rebuild_index`

### 2.2 Repository Factory Test
Test that the factory returns the correct repository type:

```bash
cd "$BENCH_DIR/repo"
python -c "
import os
os.environ['STORAGE_BACKEND'] = 'file'
from registry.repositories.factory import get_search_repository
repo = get_search_repository()
print(f'Repository type: {type(repo).__name__}')
assert type(repo).__name__ == 'FileSearchRepository', 'Wrong repository type'
print('PASS: Factory returns FileSearchRepository')
"
```
**Expected:** "PASS: Factory returns FileSearchRepository"

### 2.3 Index Server Test
Test indexing a server:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

# Create temp servers dir
with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        server_info = {
            'server_name': 'test-server',
            'description': 'A test MCP server',
            'tags': ['test', 'mcp']
        }
        await repo.index_server('/servers/test', server_info, is_enabled=True)

        # Verify it was indexed
        assert '/servers/test' in repo._entities
        print('PASS: Server indexed successfully')

    asyncio.run(test())
"
```
**Expected:** "PASS: Server indexed successfully"

### 2.4 Keyword Search Test
Test basic keyword search:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        # Index test servers
        await repo.index_server('/servers/test1', {
            'server_name': 'GitHub MCP',
            'description': 'Connect to GitHub API',
            'tags': ['github', 'api']
        }, is_enabled=True)

        await repo.index_server('/servers/test2', {
            'server_name': 'Slack MCP',
            'description': 'Connect to Slack API',
            'tags': ['slack', 'messaging']
        }, is_enabled=True)

        # Search for 'github'
        results = await repo.search('github')
        print(f'Search results: {len(results[\"servers\"])} servers found')

        # Verify
        assert len(results['servers']) == 1
        assert results['servers'][0]['name'] == 'GitHub MCP'
        print('PASS: Keyword search works')

    asyncio.run(test())
"
```
**Expected:** "PASS: Keyword search works"

### 2.5 Tag Search Test
Test search_by_tags:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        await repo.index_server('/servers/test1', {
            'server_name': 'Server A',
            'description': 'Has tags',
            'tags': ['python', 'api']
        }, is_enabled=True)

        await repo.index_server('/servers/test2', {
            'server_name': 'Server B',
            'description': 'Other tags',
            'tags': ['nodejs', 'api']
        }, is_enabled=True)

        # Search by tag
        results = await repo.search_by_tags(['api'])
        print(f'Tag search results: {len(results[\"servers\"])} servers')

        assert len(results['servers']) == 2
        print('PASS: Tag search works')

    asyncio.run(test())
"
```
**Expected:** "PASS: Tag search works"

### 2.6 Get All Tags Test
Test get_all_tags:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        await repo.index_server('/servers/test1', {
            'server_name': 'Server',
            'description': 'Tags',
            'tags': ['python', 'api']
        }, is_enabled=True)

        tags = await repo.get_all_tags()
        print(f'Tags: {tags}')

        assert 'python' in tags
        assert 'api' in tags
        print('PASS: get_all_tags works')

    asyncio.run(test())
"
```
**Expected:** "PASS: get_all_tags works"

### 2.7 Remove Entity Test
Test removing an entity:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        await repo.index_server('/servers/test', {
            'server_name': 'Test',
            'description': 'Test server',
            'tags': ['test']
        }, is_enabled=True)

        assert '/servers/test' in repo._entities

        await repo.remove_entity('/servers/test')
        assert '/servers/test' not in repo._entities
        print('PASS: Remove entity works')

    asyncio.run(test())
"
```
**Expected:** "PASS: Remove entity works"

---

## 3. Backwards Compatibility Tests

### 3.1 SearchRepositoryBase Interface Compliance
Test that FileSearchRepository implements all required methods:

```bash
cd "$BENCH_DIR/repo"
python -c "
from registry.repositories.file.search_repository import FileSearchRepository
from registry.repositories.interfaces import SearchRepositoryBase
import inspect

# Get abstract methods
abstract_methods = [name for name, method in inspect.getmembers(SearchRepositoryBase, predicate=inspect.isfunction)
                    if getattr(method, '__isabstractmethod__', False)]

# Get implemented methods
implemented = [name for name in dir(FileSearchRepository) if not name.startswith('_')]

missing = set(abstract_methods) - set(implemented)
if missing:
    print(f'FAIL: Missing methods: {missing}')
else:
    print('PASS: All abstract methods implemented')
"
```
**Expected:** "PASS: All abstract methods implemented"

### 3.2 API Response Format Compatibility
Test that search results maintain expected format:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        await repo.index_server('/servers/test', {
            'server_name': 'TestServer',
            'description': 'A test server',
            'tags': ['test']
        }, is_enabled=True)

        results = await repo.search('test')

        # Check response structure
        assert 'servers' in results
        assert 'tools' in results
        assert 'agents' in results
        assert 'skills' in results
        assert 'virtual_servers' in results

        # Check server result format
        server = results['servers'][0]
        assert 'path' in server
        assert 'name' in server
        assert 'description' in server
        assert 'tags' in server
        assert 'is_enabled' in server
        assert 'relevance_score' in server

        print('PASS: API response format compatible')

    asyncio.run(test())
"
```
**Expected:** "PASS: API response format compatible"

---

## 4. UX Tests

### 4.1 Empty Search Results
Test that empty search returns valid structure:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        results = await repo.search('nonexistent')

        assert results['servers'] == []
        assert results['tools'] == []
        assert results['agents'] == []
        assert results['skills'] == []
        assert results['virtual_servers'] == []

        print('PASS: Empty search returns valid structure')

    asyncio.run(test())
"
```
**Expected:** "PASS: Empty search returns valid structure"

### 4.2 Case-Insensitive Search
Test that search is case-insensitive:

```bash
cd "$BENCH_DIR/repo"
python -c "
import asyncio
import os
import tempfile
os.environ['STORAGE_BACKEND'] = 'file'

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ['SERVERS_DIR'] = tmpdir
    from registry.repositories.file.search_repository import FileSearchRepository

    async def test():
        repo = FileSearchRepository()
        await repo.initialize()

        await repo.index_server('/servers/test', {
            'server_name': 'GITHUB',
            'description': 'Test',
            'tags': ['TEST']
        }, is_enabled=True)

        # Search with different case
        r1 = await repo.search('github')
        r2 = await repo.search('Github')
        r3 = await repo.search('GITHUB')

        assert len(r1['servers']) == 1
        assert len(r2['servers']) == 1
        assert len(r3['servers']) == 1

        print('PASS: Case-insensitive search works')

    asyncio.run(test())
"
```
**Expected:** "PASS: Case-insensitive search works"

---

## 5. Deployment Surface Tests

### 5.1 No Docker Changes Required
Verify no changes needed to docker-compose files (just comment updates):

```bash
cd "$BENCH_DIR/repo"
grep -c "FAISS" docker-compose.yml
```
**Expected:** Only in comments, if any

### 5.2 Python Version Compatibility
Test with the minimum supported Python version:

```bash
cd "$BENCH_DIR/repo"
python --version
```
**Expected:** Python 3.11+

---

## 6. Test Execution Checklist

Complete this checklist after running the tests:

- [ ] Section 1 (FAISS Removal Verification) - all pass
- [ ] Section 2 (Functional Tests) - all pass
- [ ] Section 3 (Backwards Compatibility) - all pass
- [ ] Section 4 (UX Tests) - all pass
- [ ] Section 5 (Deployment Surface) - verified

### Command to Run All Tests
```bash
cd "$BENCH_DIR/repo"

echo "=== Running FAISS Removal Verification ==="
# 1.1
grep -r "import faiss" --include="*.py" . && echo "FAIL" || echo "PASS"
# 1.2
grep -i "faiss" pyproject.toml && echo "FAIL" || echo "PASS"
# 1.3
test -f registry/search/service.py && echo "FAIL" || echo "PASS"
# 1.4
test -f tests/fixtures/mocks/mock_faiss.py && echo "FAIL" || echo "PASS"

echo "=== All tests complete ==="
```

---
## Notes

- Tests use temporary directories to avoid polluting the actual data
- The FileSearchRepository maintains backward compatibility with the existing API
- Search is keyword-only, not semantic/vector - this is the expected behavior after FAISS removal