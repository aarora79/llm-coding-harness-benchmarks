# Low-Level Design: SSRF Hardening - Validate Outbound URLs

*Created: 2026-06-24*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)

## Overview

### Problem Statement
The MCP Gateway Registry contains SSRF (Server-Side Request Forgery) vulnerabilities in endpoints that fetch agent cards from user-supplied URLs. Specifically:

1. **POST /api/agents/{path:path}/health** - Makes outbound HTTP GET/HEAD requests to agent URLs without validation
2. **cli/agent_mgmt.py** - CLI commands that fetch agent cards via HTTP requests

These endpoints accept URLs from agent metadata and make HTTP requests to them without validating that the URLs point to legitimate external services, allowing authenticated users to potentially access internal services.

### Goals
- Prevent SSRF attacks by validating all outbound URLs before fetching
- Block requests to internal/private IP addresses and dangerous domains
- Provide configurable validation with sensible defaults
- Maintain backward compatibility with legitimate agent URLs
- Add comprehensive logging and alerting for blocked attempts
- Implement the solution using existing dependencies (no new external packages)

### Non-Goals
- Fixing SSRF in other parts of the system (auth providers, token generation, etc.)
- Implementing network-level security (firewalls, security groups)
- Rate limiting or DDoS protection
- Input validation for non-HTTP-related endpoints
- Changes to agent registration workflow (focus is on URL fetching)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| registry/api/agent_routes.py | FastAPI routes for agent management | Contains vulnerable /health endpoint |
| cli/agent_mgmt.py | CLI for agent management operations | Contains vulnerable health check function |
| registry/core/config.py | Settings and configuration management | Add SSRF validation configuration |
| registry/schemas/validation_models.py | Pydantic models for validation | Add URL validation utilities |
| registry/utils/url_validation.py | New file for URL validation logic | Core validation implementation |
| registry/utils/logging_utils.py | Logging utilities | Add SSRF attempt logging |
| tests/unit/test_url_validation.py | Unit tests | Test URL validation logic |
| tests/integration/test_agent_health_check.py | Integration tests | Test health check with validation |

### Existing Patterns Identified

1. **Settings Pattern**: Configuration uses Pydantic Settings model in registry/core/config.py
   - Files: registry/core/config.py, registry/core/settings.py
   - How a future implementer should follow this: Add SSRF-specific settings to the existing Settings class

2. **HTTP Client Pattern**: Uses httpx.AsyncClient in async routes
   - Files: registry/api/agent_routes.py (check_agent_health)
   - How a future implementer should follow this: Continue using httpx but wrap it with validation

3. **Error Handling**: Us