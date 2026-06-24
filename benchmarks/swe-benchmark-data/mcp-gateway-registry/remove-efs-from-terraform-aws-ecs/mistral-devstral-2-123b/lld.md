# Low-Level Design: Remove EFS from Terraform AWS ECS Deployment

*Created: 2026-06-15*
*Author: Mistral AI*
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
The current MCP Gateway Registry Terraform deployment uses Amazon EFS for persistent storage across ECS services. This design decision was made to provide shared file system access, but it introduces performance overhead, cost, complexity, and maintenance challenges. The registry service has already been partially migrated to DocumentDB and CloudWatch, but remnants of EFS configuration remain.

### Goals
- Remove all EFS dependencies from Terraform AWS ECS deployment
- Reduce infrastructure complexity and cost
- Improve deployment reliability and performance
- Complete the transition to cloud-native storage patterns
- Maintain backward compatibility where feasible

### Non-Goals
- Automatically migrate existing EFS data in production deployments
- Change storage backend for other deployed services
- Modify Helm chart deployments (Terraform ECS focus only)
- Update backward compatibility with DocumentDB changes

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | EFS file system module | Primary target for removal |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS service definitions | Remove EFS volume mounts and references |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module variables | Remove EFS-related variables |
| `terraform/aws-ecs/modules/mcp-gateway/outputs.tf` | Module outputs | Remove EFS output references |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | EFS initialization script | Will be deprecated/removed |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | Post-deployment automation | Remove EFS initialization calls |

### Existing Patterns Identified

**Pattern: Storage Migration Already In Progress**
- Files: `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` (lines 1367-1370)
- Evidence: Registry service already has EFS volumes removed (comment: "EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence")
- Implication: This change completes the existing migration pattern

**Pattern: Infrastructure Observability**
- Files: All Terraform modules use structured logging and output patterns
- How to follow: Maintain consistent output patterns, add appropriate comments for future migration paths

**Pattern: Security and Access Management**
- Files: Access points with specific POSIX permissions (UID/GID 1000)
- How to replace: Use AWS Secrets Manager and SSM Parameter Store for configuration storage

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| ECS Services | Depends on | Auth-server uses EFS for `/efs/auth_config` mount, MC Gateway uses `/app/data` mount |
| Terraform Variables | Uses | `efs_throughput_mode`, `efs_provisioned_throughput` variables analyze and plan EFS configuration |
| Post-Deployment Scripts | Extends | `run-scopes-init-task.sh` initializes EFS-based configuration that must be ported to Secrets Manager |
| Output References | Depends on | EFS IDs, ARNs, and access point IDs are output for external use |

### Constraint: Partial Completion
The registry service already removed EFS (evident from comments), leaving an inconsistent state. We must complete this across all services.

## Architecture

### Current System Context Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        MCP Gateway Registry                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌───────────┐    │
│  │         │    │         │    │         │    │           │    │
│  │ ECS     │    │ ECS     │    │ ECS     │    │           │    │
│  │ Auth-   │    │ Registry│    │ MC      │    │           │    │
│  │ Server  │    │         │    │ Gateway │    │           │    │
│  │         │    │         │    │         │    │           │    │
│  └────┬────┘    └────┬────┘    └────┬────┘    │           │    │
│       │               │               │         │ EFS       │    │
│  ┌────▼────┐  ┌─────▼─────┐  ┌────▼────┐   │ File      │    │
│  │Config   │  │CloudWatch │  │Temp     │   │ System    │    │
│  │Files    │  │Logs       │  │Files    │   │ /app      │    │
│  │/efs/    │  │           │  │/app/data│   │ -Server  │    │
│  │auth_config│  └───────────┘  │         │   │ -Models   │    │
│  └─────────┘                └─────────┘   │ -Logs     │    │
│                                            └───────────┘    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Proposed System Context Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        MCP Gateway Registry                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                       │
│  │         │    │         │    │         │                       │
│  │ ECS     │    │ ECS     │    │ ECS     │                       │
│  │ Auth-   │    │ Registry│    │ MC      │      REMOVED         │
│  │ Server  │    │         │    │ Gateway │                       │
│  │         │    │         │    │         │                       │
│  └────┬────┘    └────┬────┘    └────┬────┘                       │
│       │               │               │                             │
│  ┌────▼────┐  ┌─────▼─────┐  ┌────▼────┐                           │
│  │Secrets  │  │CloudWatch │  │Ephemeral│                           │
│  │Mngr/SSM │  │Logs       │  │Storage │                           │
│  │Config   │  │           │  │/tmp     │                           │
│  └─────────┘  └───────────┘  └─────────┘                           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Sequence Diagram: Current vs Proposed

**Current Flow:**
```
User → ECS Auth-Server →Mount EFS /efs/auth_config →Read scopes.yml from EFS
ECS MC Gateway →Mount EFS /app/data →Write/Read files from EFS
ECS Registry →No EFS (already migrated) →Use DocumentDB/CloudWatch
```

**Proposed Flow:**
```
User → ECS Auth-Server →Fetch config from Secrets Manager/SSM →Read scopes.yml from parameter store
ECS MC Gateway →Use ephemeral storage →Write/Read files from local disk
ECS Registry →No changes →Use DocumentDB/CloudWatch (unchanged)
```

## Data Models

### Current Storage Patterns (To Be Removed)

**EFS Storage Module:**
```hcl
module "efs" {
  source  = "terraform-aws-modules/efs/aws"
  version = "~> 2.0"

  name             = "${local.name_prefix}-efs"
  creation_token   = "${local.name_prefix}-efs"
  performance_mode = "generalPurpose"
  throughput_mode  = var.efs_throughput_mode

  # 6 Access Points for different paths
  access_points = {
    servers = { ... }
    models = { ... }
    logs = { ... }
    auth_config = { ... }
    agents = { ... }
    auth_config = { ... }
    mcpgw_data = { ... }
  }
}
```

### Model Changes (Removals)

1. **Updated Auth Server Configuration:**
   - Move from `/efs/auth_config/scopes.yml` file mount to Secrets Manager retrieval
   - Add `SCOPES_CONFIG_PARAMETER` environment variable pointing to SSM parameter

2. **Updated MC Gateway Storage:**
   - Change from EFS `/app/data` mount to local ephemeral storage
   - Update logging configuration to use local filesystem + CloudWatch only

## API / CLI Design

### New Endpoints / Commands
No new API endpoints required. This is an infrastructure-only change.

### Modified CLI Commands

1. **Post-Deployment Script Changes:**

   **Current:** `run-scopes-init-task.sh` writes to EFS
   ```bash
   # Old: Write scopes.yml to EFS mount
   aws ecs run-task --task-definition scopes-init \
     --volumes=[{name:"auth-config", efsVolumeConfiguration:{fileSystemId:$EFS_ID}}]
   ```

   **New:** `initialize-scopes-config.sh` writes to SSM Parameter Store
   ```bash
   # New: Write scopes.yml to SSM Parameter Store
   aws ssm put-parameter --name "/mcp-gateway/scopes.yml" \
     --value "$(cat scopes.yml)" \
     --type String --overwrite
   ```

### Deployment Surface Changes

| Surface | Change | Justification |
|---------|--------|--------------|
| Terraform outputs | Remove EFS IDs/ARNs | No longer needed |
| CloudWatch Logs | Add EFS removal validation logs | Operational verification |
| SSM Parameters | Add scopes configuration | Replaces EFS-mounted config |

## Configuration Parameters

### Settings / Config Class Updates

**Remove from `variables.tf`:**
```hcl
# Remove these variables
enabled = false  # Mark EFS as disabled
variable "efs_throughput_mode" {}
variable "efs_provisioned_throughput" {}
```

**Environment Variables to Update:**
- `SCOPES_CONFIG_PATH` → Change from `/efs/auth_config/scopes.yml` to `/var/runtime/scopes.yml` (local path)
- Add `SCOPES_CONFIG_SSM_PARAMETER` for SSM-based retrieval

## New Dependencies

**No new dependencies required.** This change uses only existing dependencies:
- AWS Secrets Manager (already used for secrets)
- SSM Parameter Store (already used for configuration)
- DocumentDB (already used by registry)
- CloudWatch Logs (already used for logging)

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Update Auth Server Container Definition

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~220, ~470

```hcl
# Before: EFS volume mount for auth_config
environment = [
  {
    name  = "SCOPES_CONFIG_PATH"
    value = "/efs/auth_config/scopes.yml"
  }
]

mountPoints = [
  {
    sourceVolume  = "auth-config"
    containerPath = "/efs/auth_config"
    readOnly      = false
  }
]

volume = {
  auth-config = {
    efs_volume_configuration = {
      fileSystemId     = module.efs.id
      accessPointId    = module.efs.accessPoints["auth_config"].id
      transitEncryption = "ENABLED"
    }
  }
}

# After: Local files with SSM parameter retrieval
environment = [
  {
    name  = "SCOPES_CONFIG_PATH"
    value = "/var/runtime/scopes.yml"
  },
  {
    name  = "SCOPES_CONFIG_SSM_PARAMETER"
    value = "/mcp-gateway/${var.environment}/scopes.yml"
  }
]

# Remove mountPoints and volume entries for auth-config
```

**File:** `terraform/aws-ecs/modules/mcp-gateway/storage.tf`
**Action:** DELETE ENTIRE FILE

**File:** Remove volume configuration blocks (mcp-logs)
```hcl
# Remove these blocks entirely:
volume = {
  mcp-logs = { ... }
  auth-config = { ... }
}
```

#### Step 2: Remove EFS Variables

**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** ~259-273

```hcl
# Remove EFS section:
# variable "efs_throughput_mode" { ... }
# variable "efs_provisioned_throughput" { ... }
```

#### Step 3: Remove EFS Outputs

**File:** `terraform/aws-ecs/modules/mcp-gateway/outputs.tf`
**Lines:** ~47-69

```hcl
# Remove EFS outputs:
# output "efs_id" { ... }
# output "efs_arn" { ... }
# output "efs_access_points" { ... }
```

#### Step 4: Update Post-Deployment Scripts

**File:** `terraform/aws-ecs/scripts/run-scopes-init-task.sh`
**Action:** Decommission/Omit from workflow

Add migration notes to header:
```bash
# DEPRECATED: This script writes configuration to EFS storage.
# As of v1.25.0, configuration is stored in SSM Parameter Store.
# Use initialize-scopes-config.sh instead or update your configuration.
```

**File:** Create new scopes initialization script
**File:** `terraform/aws-ecs/scripts/initialize-scopes-config.sh`

```bash
#!/bin/bash
aws ssm put-parameter \
  --name "/mcp-gateway/${ENVIRONMENT}/scopes.yml" \
  --value "$(cat scopes-config/scopes.yml)" \
  --type String \
  --overwrite
```

#### Step 5: Add Registry Service Volume Cleanup

**File:** DocumentDB already in use, but ensure comments are updated
**Lines:** ~1367-1370

```hcl
# Confirm registry is on DocumentDB:
# EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence
# Logs go to CloudWatch only
mountPoints = []

# EFS volumes removed - registry uses ephemeral storage and DocumentDB for persistence
volume = {}
```

### Error Handling Strategy

- **Taint Replacement**: If cluster destruction fails due to EFS dependencies, use `terraform taint` to mark resources for replacement
- **Pre-Checks**: Add validation to verify no running tasks use EFS before destruction
- **Backup Guidance**: Provide independent EFS backup script for migration path

### Logging Strategy

Add operational validation logs:
```hcl
# In ECS service health checks:
resource "aws_ecs_service" "auth" {
  health_check_path = "/health"
  health_check_interval = 30
  # Add validation that SSM parameter exists before startup
}
```

## Observability

### Validation Metrics

Add validation to post-deployment:
```bash
# Verify SSM parameter exists
aws ssm describe-parameters \
  --filters "Key=Name,Values=/mcp-gateway/prod/scopes.yml" \
  --query "length(Parameters)" \
  --output text | grep -q "1" || (echo "Missing SSM parameter"; exit 1)
```

### Rollback Detection

Monitor CloudWatch for:
- `NoSuchKey` errors from SSM
- `ResourceinitializationError` from ECS
- EFS-related API calls should drop to zero

## Scaling Considerations

### Positive Impact
- **Improved Scaling**: Faster container starts without EFS mount overhead
- **Reduced Latency**: No network latency for configuration retrieval
- **Cost Savings**: Eliminate EFS storage costs and NFS operation overhead

### Potential Bottlenecks
- SSM Parameter Store has lower size limits than EFS (8KB for Advanced parameters, 4KB for Standard)
- Solution: Break large configurations into smaller parameters or useSSM documents

### Horizontal Scaling
- ECS services benefit from faster startup times
- Auto-scalingresponds faster without EFS mount delays
- DocumentDB provides sufficient horizontal scaling for persistence needs

## File Changes

### Files to Be Deleted

1. `terraform/aws-ecs/modules/mcp-gateway/storage.tf` (entire file)

### Files to Be Modified

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~100, ~150, ~215 | Remove EFS mountPoints and volumes |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | ~259-273 | Remove EFS variables |
| `terraform/aws-ecs/modules/mcp-gateway/outputs.tf` | ~47-69 | Remove EFS outputs |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | Header | Add deprecation notice |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | ~180-220 | Remove EFS initialization calls |

### Constructive Editing Required

**Models and ECS:** Replace EFS with service-specific alternatives
- Auth-server: SSM Parameter Store + Secrets Manager
- MC Gateway: Local ephemeral storage
- Registry: Already uses DocumentDB

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Code removed | ~163 |
| Code modified | ~50 |
| Documentation updates | ~100 |
| **Total Impact** | **~313 lines (net reduction)** |

## Testing Strategy

See `testing.md` for detailed test plan covering functional, backward compatibility, UX, deployment surface, and end-to-end tests.

## Alternatives Considered

### Alternative 1: Keep EFS with Conditional Logic
**Description:** Maintain EFS but make it optional via feature flag
**Pros:** Backward compatibility, gradual migration path
**Cons:** Complex conditional logic, technical debt persists
**Why Rejected:** Complexity outweighs benefit; clean break preferred

### Alternative 2: Migrate All Services to Single EFS Pattern
**Description:** Standardize all services on EFS (including registry)
**Pros:** Consistency across services
**Cons:** Goes against cloud-native, continues overhead, increases cost
**Why Rejected:** Opposes goal of reducing complexity and cost

### Alternative 3: Use S3 Instead of EFS
**Description:** Replace EFS with S3 for storage
**Pros:** Cloud-native, scalable, well-understood
**Cons:** Not compatible with container filesystem mounts, requires application code changes
**Why Rejected:** Application code changes out of scope; SSM/Secrets Manager better fit for configuration

### Comparison Matrix

| Criteria | EFS Removal (Chosen) | Keep EFS (Current) | Adopt S3 |
|----------|----------------------|-------------------|----------|
| Complexity | Low | High | Medium |
| Cost | Low | High | Medium |
| Performance | High | Medium | Low |
| Cloud-Native | Yes | No | Yes |
| Implementation Scope | Limited (Infrastructure) | None | Broad (Application + Infrastructure) |
| Migration Risk | Medium (requires planning) | None | High (application changes) |

## Rollout Plan

### Phase 1: Pre-Migration Preparation
- Document current EFS usage patterns
- Verify all services have alternative storage ready
- Create backup procedures for EFS data
- Publish migration guide for users

### Phase 2: Code Changes
- Implement Terraform changes in development branch
- Test with clean slate (no EFS resources)
- Validate service functionality
- Update CI/CD pipelines to skip EFS creation

### Phase 3: Graduated Testing
- Create new deployment from scratch (no migration)
- Validate services start successfully
- Test configuration retrieval patterns
- Load test to verify performance improvements

### Phase 4: Deployment Options
- **New Deployments Only**: Most straightforward approach
- **Migrated Deployments**: Require manual EFS data export/import to new storage
- **Hybrid**: Not recommended due to complexity

## Open Questions

1. Confirmation needed on EFS usage patterns in production deployments
2. Validation that all configuration fits within SSM size limits
3. Guidelines for breaking large configurations into multiple parameters
4. Need for fallback mechanism during transitional period

## References

- AWS Documentation: [Migrating from EFS to alternative storage](https://docs.aws.amazon.com/efs/latest/ug/migration.html)
- Terraform Best Practices: [Managing storage in Terraform](https://developer.hashicorp.com/terraform/tutorials/aws/aws-storage)
- MCP Gateway Registry Documentation: Existing storage patterns in use by registry service
