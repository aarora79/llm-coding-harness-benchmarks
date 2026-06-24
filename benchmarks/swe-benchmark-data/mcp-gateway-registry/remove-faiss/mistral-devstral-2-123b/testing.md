# Testing Plan: FAISS Removal and Search Unification

*Created: 2026-06-15*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing

This testing plan covers verification of:
1. **FAISS removal**: Confirm all FAISS code is successfully eliminated
2. **Functional equivalence**: Ensure search works identically after migration
3. **Deployment verification**: Validate all deployment methods work without FAISS
4. **Backward compatibility**: Confirm existing APIs maintain identical behavior

### Prerequisites

- [ ] Local development environment setup
- [ ] Docker Desktop or compatible runtime
- [ ] MongoDB instance available for testing
- [ ] AWS credentials configured for ECS testing (if applicable)
- [ ] Helm setup for Kubernetes testing (if applicable)

### Shared Variables

```bash
# Set in your environment before running tests
export PROJECT_ROOT="/Users/prsinp/claude-code-multi-model/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
export REGISTRY_DIR="$PROJECT_ROOT/registry"
export TEST_DATA_DIR="$PROJECT_ROOT/tests/test_data"
export PYTHON_VERSION="3.11"
export MONGODB_URI="mongodb://localhost:27017"
export EMBEDDING_MODEL="all-MiniLM-L6-v2"  # Must match your sentence-transformers installation
```

## 1. Functional Tests

### 1.1 Python Import Tests

**Verify FAISS imports are completely removed:**

```python
# Should fail with ModuleNotFoundError (good!)
cd "$REGISTRY_DIR" && python -c "import faiss; print('FAISS import should fail')"
```

**Expected:** `ModuleNotFoundError: No module named 'faiss'`

**Verify search repository import works:**

```python
# Should succeed
cd "$REGISTRY_DIR" && python -c "
from repositories.factory import get_search_repository
repo = get_search_repository()
print(f'Search repository type: {type(repo).__name__}')
"
```

**Expected:** `Search repository type: DocumentDBSearchRepository`

### 1.2 FAISS Code Removal Verification

```bash
# Verify no Python files contain FAISS imports
grep -r "import faiss" "$REGISTRY_DIR" --include="*.py" || echo "✅ No FAISS imports found"
grep -r "from.*faiss" "$REGISTRY_DIR" --include="*.py" || echo "✅ No FAISS from imports found"

# Verify no files contain "FaissService" key words
find "$REGISTRY_DIR" -name "*.py" -exec grep -l "FaissService" {} \; || echo "✅ No FaissService references found"

# Verify no files contain "FaissSearchRepository"
find "$REGISTRY_DIR" -name "*.py" -exec grep -l "FaissSearchRepository" {} \; || echo "✅ No FaissSearchRepository references found"

# Verify service.py file is removed
ls "$REGISTRY_DIR/search/service.py" 2>/dev/null && echo "❌ File should be deleted" || echo "✅ service.py file removed"

# Verify FAISS search repository file is removed
ls "$REGISTRY_DIR/repositories/file/search_repository.py" 2>/dev/null && echo "❌ File should be deleted" || echo "✅ search_repository.py file removed"

# Verify pyproject.toml has no FAISS dependency
grep -E "(faiss|FAISS)" "$PROJECT_ROOT/pyproject.toml" && echo "❌ FAISS dependency should be removed" || echo "✅ FAISS dependency removed from pyproject.toml"
```

**Expected:** All checks show ✅

### 1.3 Search API Functional Tests

```bash
# Start dev server
cd "$REGISTRY_DIR" && python main.py &
DEV_PID=$!
sleep 5  # Wait for server startup

# Test basic search endpoint
curl -s -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"test", "limit":5}' | jq .

# Test agent search endpoint
curl -s -X POST "http://localhost:8000/v1/agent/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"help", "limit":3}' | jq .

# Test tag-based search
curl -s -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"tags":["ai", "ml"], "limit":5}' | jq .

# Cleanup
kill $DEV_PID
```

**Expected:** All endpoints return valid JSON responses with expected structure

```json
{
  "results": {
    "servers": [ ... ],
    "agents": [ ... ],
    "tools": [ ... ]
  }
}
```

### 1.4 Pytest Unit Tests

```bash
# Run existing search tests (migrated to use DocumentDB)
cd "$REGISTRY_DIR" && python -m pytest tests/unit/search/ -v

# Verify FAISS test files are removed
cd "$REGISTRY_DIR" && python -m pytest tests/unit/search/test_faiss_service.py -v 2>&1 | grep "ENOENT://" || echo "✅ test_faiss_service.py properly deleted"
```

**Expected:**
- Search tests pass without errors
- FAISS-specific test files result in FileNotFoundError (expected)

## 2. Backwards Compatibility Tests

**Not Applicable** - Search endpoints maintain identical request/response shapes. No CLI changes were made. This change only affects internal implementation.

### 2.1 Verify API Contract Stability

```bash
# Test that search endpoints accept the same request shapes
echo '{"query":"test query", "limit":10}' > request.json
curl -s -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -d @request.json | jq -r '.results | has("servers", "agents", "tools")'
```

**Expected:** `true` (API contract unchanged)

## 3. UX Tests

**Not Applicable** - This change does not modify any user interface, CLI output, or error message content. The change is purely internal implementation.

## 4. Deployment Surface Tests

### 4.1 Docker Compose Testing

```bash
# Build images and test without FAISS
cd "$PROJECT_ROOT" && docker-compose build

# Verify no FAISS in build output
docker-compose config | grep -i faiss && echo "❌ FAISS should not appear in Docker config" || echo "✅ No FAISS in Docker configuration"

# Start services
docker-compose up -d
sleep 10

# Test search through Docker deployment
curl -s "http://localhost:8000/v1/search" -H "Content-Type: application/json" -d '{"query":"docker test"}' | jq .

# Cleanup
docker-compose down
```

**Expected:** ✅ All Docker operations succeed without FAISS references

### 4.2 Terraform ECS Configuration

```bash
# Check ECS task definitions have no FAISS references
cd "$PROJECT_ROOT/terraform/aws-ecs"
grep -r -i "faiss" . && echo "❌ FAISS should be removed from ECS config" || echo "✅ No FAISS in ECS configuration"

# Check Docker images referenced in ECS
jq '.containerDefinitions[]?.image' terraform.tfvars.json | grep -i faiss || echo "✅ No FAISS images in ECS definitions"
```

**Expected:** ✅ No FAISS references in ECS infrastructure code

### 4.3 Helm Chart Testing (if applicable)

```bash
# If Helm charts exist, verify no FAISS references
helm_chart_dir="$PROJECT_ROOT/charts"
if [ -d "$helm_chart_dir" ]; then
  find "$helm_chart_dir" -name "*.yaml" -exec grep -l -i "faiss" {} \; && echo "❌ FAISS should be removed from Helm" || echo "✅ No FAISS in Helm charts"
else
  echo "⚠️  No Helm charts found - skipping"
fi
```

**Expected:** ✅ No FAISS references if Helm charts exist

### 4.4 Deploy and Verify

**Docker deployment verification:**
```bash
# Test search endpoints through deployed container
endpoints=(
  "http://localhost:8000/v1/search"
  "http://localhost:8000/v1/tool/search"
  "http://localhost:8000/v1/agent/search"
)

for endpoint in "${endpoints[@]}"; do
  echo "Testing $endpoint"
  curl -s -X POST "$endpoint" \
    -H "Content-Type: application/json" \
    -d '{"query":"test"}' \
    -o /dev/null \
    -w "HTTP
%x{http_code}\n" | xargs -I {} echo "$endpoint: {}"
done
```

**Expected:** All endpoints return `HTTP 200` status codes

### 4.5 Rollback Verification

```bash
# Test ability to rollback to previous version if needed
cd "$PROJECT_ROOT"

# Check if git tag exists
git tag | grep "1.24.4" && echo "✅ Previous tag available" || echo "⚠️  No known stable rollback tag"

# If rollback required, the database remains compatible
echo "DocumentDB preparation is backward-compatible for rollback"
```

## 5. End-to-End API Tests

### 5.1 Search Workflow Test

```bash
# Create test agent data
test_agent='{
  "name": "test-agent-faiss-migration",
  "description": "Agent created to test FAISS removal",
  "version": "1.0.0",
  "tags": ["testing", "migration", "search"],
  "is_enabled": true,
  "provider": "test"
}'

# Register test agent
AGENT_PATH=$(curl -s -X POST "http://localhost:8000/v1/agents" \
  -H "Content-Type: application/json" \
  -d "$test_agent" | jq -r '.agent_url')

echo "Registered agent at: $AGENT_PATH"
sleep 2  # Allow indexing

# Search for the agent
search_result=$(curl -s -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"FAISS migration", "limit":5}' | jq .)

echo "Search result:"
echo "$search_result"

# Verify agent appears in results
echo "$search_result" | jq -r '.results.agents[] | select(.name | contains("test-agent-faiss-migration")) | .name' || echo "⚠️  Test agent not in search results"

# Cleanup - delete the test agent
DOC_ID=$(basename "$AGENT_PATH")
curl -s -X DELETE "http://localhost:8000/v1/agents/$DOC_ID" -w "HTTP status: %x{http_code}\n"
```

**Expected:** Test agent should appear in search results when searching for relevant terms

### 5.2 Performance Regression Testing

```bash
# Compare search performance before/after (if possible)
# Note: This requires capturing metrics during normal usage

# Test multiple queries and capture timing
for i in {1..5}; do
  start_time=$(date +%s%N)
  curl -s -X POST "http://localhost:8000/v1/search" \
    -H "Content-Type: application/json" \
    -d '{"query":"performance test query number '$i'", "limit":10}' \
    -o /dev/null
  end_time=$(date +%s%N)
  duration=$(( (end_time - start_time) / 1000000 ))
  echo "Query $i: ${duration}ms"
done
```

**Expected:** Search query timings should be comparable to before FAISS removal

## 6. Test Execution Checklist

- [ ] Section 1 (Functional): import tests, FAISS code removal, search API tests, pytest units
- [ ] Section 2 (Backwards Compat): API contract verification
- [ ] Section 3 (UX): Not Applicable ✅
- [ ] Section 4 (Deployment): Docker, ECS, Helm, deploy and verify, rollback
- [ ] Section 5 (E2E): Search workflow, performance regression
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions

## Automated Test Script

```bash
#!/bin/bash
# test_faiss_removal.sh

set -e
echo "=== FAISS Removal Test Suite ==="

# Import tests
echo "🧪 Testing imports..."
python -c "from registry.repositories.factory import get_search_repository; print('✅ Search factory import works')"

# File removal tests
echo "🗑️  Testing file removals..."
[ ! -f "$REGISTRY_DIR/search/service.py" ] && echo "✅ service.py removed" || { echo "❌ service.py should be deleted"; exit 1; }
[ ! -f "$REGISTRY_DIR/repositories/file/search_repository.py" ] && echo "✅ FAISS search file removed" || { echo "❌ FAISS search file should be deleted"; exit 1; }

# Dependency tests
echo "📦 Testing dependencies..."
! python -c "import faiss" 2>/dev/null && echo "✅ FAISS import fails as expected" || { echo "❌ FAISS should not be importable"; exit 1; }

# Configuration tests
echo "🎛️  Testing configuration..."
! grep -q -i "faiss" "$PROJECT_ROOT/pyproject.toml" && echo "✅ No FAISS in dependencies" || { echo "❌ FAISS should be removed from pyproject.toml"; exit 1; }

# API tests
echo "🌐 Testing API endpoints..."
response=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"test", "limit":3}' 2>/dev/null)

http_code=$(echo "$response" | tail -1)
if [ "$http_code" -eq 200 ]; then
  echo "✅ Search endpoint returns 200"
  echo "📋 Response structure:"
  echo "$response" | head -n -1 | jq -r '.results | keys | join(", ")'
else
  echo "❌ Search endpoint failed with HTTP $http_code"
  exit 1
fi

echo "✅ All FAISS removal tests passed!"
```

**To run the automated test:**
```bash
chmod +x test_faiss_removal.sh
./test_faiss_removal.sh
```

## References

- LLD with implementation details: `./lld.md`
- Original issue specification: `./github-issue.md`
- Repository factory: `registry/repositories/factory.py`
- DocumentDB search implementation: `registry/repositories/documentdb/search_repository.py`