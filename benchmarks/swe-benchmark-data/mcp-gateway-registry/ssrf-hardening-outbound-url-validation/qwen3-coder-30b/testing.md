# Testing Plan: SSRF Hardening - Validate Outbound URLs on Agent-Card Fetch and Health Check Endpoints

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan covers functional, backwards compatibility, and security testing for the SSRF hardening implementation. The goal is to ensure that outbound HTTP requests to user-supplied URLs are properly validated while maintaining existing functionality.

### Prerequisites
- [ ] Registry service running with the new SSRF protection changes
- [ ] Test environment with network access to simulate external URLs
- [ ] Admin user credentials for API access
- [ ] Sample agent and server registrations for testing

### Shared Variables
```bash
export REGISTRY_URL="http://localhost:7860"
export ADMIN_USER="admin"
export ADMIN_PASSWORD="password"
```

## 1. Functional Tests
### 1.1 curl / HTTP Tests
#### Test: Agent Health Check with Valid URL
**Endpoint:** `POST /api/agents/{path}/health`
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/agents/test-agent/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{"path": "/test-agent"}'
```
**Expected Status:** 200 OK
**Expected Response:** 
```json
{
  "agent_path": "/test-agent",
  "health_check_url": "https://example.com/.well-known/agent-card.json",
  "status": "healthy",
  "status_code": 200,
  "detail": null,
  "response_time_ms": 125,
  "last_checked_iso": "2026-07-22T10:30:00.000Z"
}
```

#### Test: Agent Health Check with Private IP (Should Block)
**Endpoint:** `POST /api/agents/{path}/health`
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/agents/test-agent/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{"path": "/test-agent", "url": "http://192.168.1.100/agent-card.json"}'
```
**Expected Status:** 400 Bad Request
**Expected Response:**
```json
{
  "detail": "URL failed SSRF validation - private/internal addresses are not allowed"
}
```

#### Test: Server Health Check with Valid URL
**Endpoint:** `POST /api/servers/{path}/health`
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/servers/test-server/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{"path": "/test-server"}'
```
**Expected Status:** 200 OK
**Expected Response:** 
```json
{
  "status": "healthy",
  "status_code": 200,
  "detail": null,
  "response_time_ms": 150,
  "last_checked_iso": "2026-07-22T10:30:00.000Z"
}
```

#### Test: Server Health Check with Private IP (Should Block)
**Endpoint:** `POST /api/servers/{path}/health`
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/servers/test-server/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{"path": "/test-server", "proxy_pass_url": "http://10.0.0.1/mcp"}'
```
**Expected Status:** 400 Bad Request
**Expected Response:**
```json
{
  "detail": "URL failed SSRF validation - private/internal addresses are not allowed"
}
```

### 1.2 CLI Tests
**Test:** Register agent with valid URL
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{
    "name": "Test Agent",
    "path": "/test-agent",
    "url": "https://example.com/agent-card.json"
  }'
```
**Expected Status:** 201 Created
**Expected Response:** Agent registration successful

## 2. Backwards Compatibility Tests
### Test: Existing Agent Card Fetch Still Works
**Endpoint:** `GET /api/agents/{path}`
**Command:**
```bash
curl -X GET "${REGISTRY_URL}/api/agents/test-agent" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}"
```
**Expected Status:** 200 OK
**Expected Response:** Agent card with existing fields

### Test: Existing Server Health Check Still Works
**Endpoint:** `POST /api/servers/{path}/health`
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/servers/test-server/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}"
```
**Expected Status:** 200 OK
**Expected Response:** Server health status

### Test: Valid URLs Still Function
**Command:** Test with a valid public URL
```bash
curl -X POST "${REGISTRY_URL}/api/agents/test-agent/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{"path": "/test-agent", "url": "https://httpbin.org/get"}'
```
**Expected Status:** 200 OK (or appropriate health status)

## 3. UX Tests
### Test: Error Messages Are Clear
**Scenario:** Attempt to register agent with private IP URL
**Command:**
```bash
curl -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{
    "name": "Test Agent",
    "path": "/test-agent",
    "url": "http://192.168.1.100/agent-card.json"
  }'
```
**Expected Result:** Clear error message about SSRF protection

### Test: Health Check UI Feedback
**Scenario:** Health check with blocked URL
**Expected Result:** User-friendly error message in UI showing why the check failed

## 4. Deployment Surface Tests
### 4.1 Docker wiring
**Test:** Verify new utility module is included in Docker image
```bash
docker run --rm mcpgateway/registry:latest find /app/registry/utils -name "url_validation.py"
```
**Expected Result:** File exists in container

### 4.2 Terraform / ECS wiring
**Test:** Check that no new environment variables are required
```bash
# Verify existing deployment configuration works unchanged
terraform plan
```
**Expected Result:** No new variables required for deployment

### 4.3 Helm / EKS wiring
**Test:** Helm chart can deploy without issues
```bash
helm template . --set image.tag=latest
```
**Expected Result:** Template renders without errors

### 4.4 Deploy and verify
**Test:** Deploy to staging environment and verify functionality
```bash
helm upgrade --install registry ./helm-chart \
  --set image.tag=latest \
  --namespace staging
```
**Expected Result:** Successful deployment with all endpoints functional

### 4.5 Rollback verification
**Test:** Verify rollback to previous version works correctly
```bash
helm rollback registry 1
```
**Expected Result:** Previous version deploys and functions correctly

## 5. End-to-End API Tests
### Test: Complete Agent Registration and Health Check Flow
1. Register agent with valid public URL
2. Perform health check on registered agent
3. Verify agent status is recorded
4. Attempt to register agent with private IP URL
5. Verify registration is blocked with appropriate error

**Command Sequence:**
```bash
# Step 1: Register agent
curl -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{
    "name": "Test Agent",
    "path": "/test-agent",
    "url": "https://httpbin.org/get"
  }'

# Step 2: Health check
curl -X POST "${REGISTRY_URL}/api/agents/test-agent/health" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}"

# Step 3: Verify registration blocked
curl -X POST "${REGISTRY_URL}/api/agents/register" \
  -H "Content-Type: application/json" \
  -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
  -d '{
    "name": "Bad Agent",
    "path": "/bad-agent",
    "url": "http://192.168.1.100/agent-card.json"
  }'
```

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions