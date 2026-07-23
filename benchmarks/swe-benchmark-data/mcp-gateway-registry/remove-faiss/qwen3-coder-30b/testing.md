# Testing Plan: Remove FAISS from Codebase and Documentation

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan covers verification that FAISS has been completely removed from the codebase and that DocumentDB hybrid search functions as a complete replacement. The tests ensure no breaking changes to existing functionality and that deployment processes work correctly.

### Prerequisites
- [ ] Registry service running with DocumentDB backend
- [ ] FAISS dependencies removed from environment
- [ ] Docker images built without FAISS dependencies

### Shared Variables
```bash
export REGISTRY_URL="http://localhost:8000"
export SEARCH_QUERY="test search"
```

## 1. Functional Tests
### 1.1 curl / HTTP Tests
#### Test: Verify search functionality works with DocumentDB hybrid search
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/search/semantic" \
  -H "Content-Type: application/json" \
  -d '{"query": "'${SEARCH_QUERY}'", "max_results": 5}' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

**Expected Status:** 200 OK
**Expected Response:** Search results with relevance scores and matching tools
**Assertions:**
- Response contains search results for servers, agents, skills, etc.
- Relevance scores are between 0.0 and 1.0
- No FAISS-specific error messages in response

#### Test: Verify tag-based search works
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/search/semantic" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["test"], "max_results": 5}' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

**Expected Status:** 200 OK
**Expected Response:** Tag-filtered search results
**Assertions:**
- Results match the specified tags
- No FAISS-related failures

### 1.2 CLI Tests
#### Test: Verify CLI tools that may reference search functionality
**Command:**
```bash
uv run python -m registry.cli.search --query "${SEARCH_QUERY}" --max-results 5
```

**Expected Status:** Exit code 0
**Expected Output:** Search results in CLI format
**Assertions:**
- No errors related to FAISS imports
- Results formatted correctly

## 2. Backwards Compatibility Tests
### Test: Verify existing search API endpoints still work
**Command:**
```bash
curl -X GET "${REGISTRY_URL}/api/search/tags" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

**Expected Status:** 200 OK
**Expected Response:** List of all unique tags
**Assertions:**
- API returns expected tag list
- No breaking changes to API contract

### Test: Verify search results structure unchanged
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/search/semantic" \
  -H "Content-Type: application/json" \
  -d '{"query": "'${SEARCH_QUERY}'", "max_results": 1}' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

**Expected Status:** 200 OK
**Expected Response:** Consistent response structure
**Assertions:**
- Response schema unchanged
- All fields present and populated correctly
- No FAISS-specific fields in response

## 3. UX Tests
### Test: Verify UI search experience is unaffected
**Scenario:** User performs search through web interface
**Expected Behavior:**
- Search results load quickly
- No error messages about FAISS
- All search features work as before
**Assertions:**
- No console errors related to FAISS
- Search interface responds normally
- Results display correctly

## 4. Deployment Surface Tests
### 4.1 Docker wiring
**Test:** Build registry Docker image without FAISS
**Command:**
```bash
docker build -t test-registry:latest -f docker/Dockerfile.registry .
```

**Expected Status:** Build succeeds without FAISS installation errors
**Assertions:**
- Docker build completes successfully
- Image size is smaller (FAISS dependencies removed)
- No FAISS-related errors during build

### 4.2 Terraform / ECS wiring
**Test:** Verify no FAISS references in infrastructure code
**Command:**
```bash
grep -r "faiss" terraform/ || echo "No FAISS references found in Terraform"
```

**Expected Status:** Command returns "No FAISS references found in Terraform"
**Assertions:**
- No FAISS dependencies in Terraform configurations
- No FAISS installation steps in ECS task definitions

### 4.3 Helm / EKS wiring
**Test:** Verify Helm chart doesn't reference FAISS
**Command:**
```bash
grep -r "faiss" charts/ || echo "No FAISS references found in Helm charts"
```

**Expected Status:** Command returns "No FAISS references found in Helm charts"
**Assertions:**
- No FAISS dependencies in Helm chart templates
- No FAISS installation commands in Helm values

### 4.4 Deploy and verify
**Test:** Deploy to test environment and verify search works
**Command:**
```bash
docker run -d -p 8000:8000 --name test-registry test-registry:latest
sleep 5
curl -X POST "http://localhost:8000/api/search/semantic" \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "max_results": 1}'
```

**Expected Status:** 200 OK
**Assertions:**
- Container starts successfully
- Search endpoint responds with results
- No FAISS-related errors in logs

### 4.5 Rollback verification
**Test:** Verify rollback to previous version would work
**Command:**
```bash
# This would be tested in a real rollback scenario
echo "Rollback test - ensure FAISS can be reintroduced if needed"
```

**Expected Status:** Manual verification
**Assertions:**
- Previous version with FAISS can be restored
- No irreversible changes made to deployment process

## 5. End-to-End API Tests
### Test: Complete search workflow
**Scenario:** User registers a server, then searches for it
**Steps:**
1. Register a test server via API
2. Perform semantic search for server content
3. Verify results include the registered server
4. Verify no FAISS errors occur

**Expected Results:**
- Server registration succeeds
- Search returns registered server
- All operations complete without FAISS errors

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions