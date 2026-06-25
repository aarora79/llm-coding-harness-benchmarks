# Testing Plan: SSRF Protection for Federation Client

*Created: 2026-06-24*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan tests that the federation client properly validates URLs and blocks requests to internal/private endpoints while allowing legitimate external federation traffic.

### Prerequisites
- [ ] MCP Gateway Registry deployed with federation support
- [ ] Admin access to configure peer registries
- [ ] Ability to monitor application logs

### 1. Functional Tests - CLI

**Test: Configure peer with blocked URL**
```bash
# Attempt to configure private IP endpoint
export REGISTRY_URL="http://localhost:8000"
export ADMIN_TOKEN="admin-token"

curl -X POST "${REGISTRY_URL}/api/federation/peers" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "peer_id": "blocked-peer",
    "endpoint": "http://10.0.0.1:8080",
    "federation_token": "test"
  }'

# Trigger sync and check logs
curl -X POST "${REGISTRY_URL}/api/federation/sync/blocked-peer" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"

# Check logs contain SSRF warning
docker logs mcp-registry | grep "SSRF protection"
```

**Expected Result**: Log shows `SSRF protection: Blocked URL resolving to private IP '10.0.0.1'`

## 2. Backwards Compatibility

Test that legitimate external URLs still work and existing configurations remain valid.

## 3. Security Tests

Simulate SSRF attack scenarios:
- Metadata endpoint access (169.254.169.254)
- Localhost access (127.0.0.1)
- Private network access (10.0.0.0/8, 192.168.0.0/16)

## 4. Configuration Tests

Verify Docker/Helm configuration properly sets the new environment variables.

## Summary

Complete test coverage ensures SSRF protection is effective and production-ready.
EOF