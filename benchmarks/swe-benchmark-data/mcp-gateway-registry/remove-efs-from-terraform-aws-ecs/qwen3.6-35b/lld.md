# Low-Level Design: Remove EFS from terraform/aws-ecs/

*Created: 2026-07-22*
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

The terraform/aws-ecs/ deployment provisions an AWS EFS file system with six access points (servers, models, logs, agents, auth_config, mcpgw_data), mount targets across all private subnets, and a dedicated security group. EFS is no longer needed because the application uses S3 and DocumentDB for all persistent storage. The registry ECS service has already migrated away from EFS (comment at `ecs-services.tf` line 1367: "EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence"). Retaining EFS adds unnecessary AWS costs (monthly file system charges, per-GB storage, data processing) and operational complexity.

### Goals

- Delete all EFS resources from Terraform configuration
- Remove EFS volume mounts from ECS task definitions (auth service and MCPGW service)
- Remove EFS outputs that are consumed by deployment scripts
- Update deployment scripts to remove EFS dependencies
- Ensure `terraform validate` and `terraform plan` succeed with no EFS references

### Non-Goals

- Modifying the Python application code (only Terraform config changes)
- Removing S3 or DocumentDB infrastructure
- Modifying Docker container definitions or images
- Updating Keycloak, CloudFront, or ALB configurations

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `modules/mcp-gateway/storage.tf` | EFS resource definition (183 lines) | Entire file to be deleted; defines the EFS file system, 6 access points, mount targets, and security group |
| `modules/mcp-gateway/variables.tf` | Module variable definitions (~1316 lines) | Lines 259-274 contain `efs_throughput_mode` and `efs_provisioned_throughput` variables to be removed |
| `modules/mcp-gateway/outputs.tf` | Module outputs (~188 lines) | Lines 47-69 define `efs_id`, `efs_arn`, `efs_access_points` outputs to be removed |
| `modules/mcp-gateway/ecs-services.tf` | ECS service definitions (2257+ lines) | Auth service (lines 220-221, 483-493, 542-557) and MCPGW service (lines 1805, 1859-1867) reference EFS |
| `outputs.tf` (root) | Root module outputs | Lines 67-81 pass through EFS outputs to be removed |
| `main.tf` | Root module wiring (372 lines) | No EFS variables passed through from root to child module |
| `scripts/run-scopes-init-task.sh` | EFS-dependent scopes initialization script (488 lines) | Reads `mcp_gateway_efs_id` and `mcp_gateway_efs_access_points.auth_config` from terraform outputs to create an ECS task with EFS volume mounts |
| `scripts/post-deployment-setup.sh` | Post-deployment validation script | Line 218 validates `mcp_gateway_efs_id` as a required output; lines 549-567 fall through to "EFS mode (default)" when DocumentDB is absent |
| `README.md` | Deployment documentation | Line 817 lists "EFS Shared Storage" in features; line 1056 lists `elasticfilesystem:*` in IAM permissions |

### Existing Patterns Identified

1. **Service migration pattern**: The registry service was already migrated away from EFS. The pattern applied was:
   - Set `volume = {}` on the ECS service block (no volumes)
   - Changed `SCOPES_CONFIG_PATH` from `/efs/auth_config/auth_config/scopes.yml` to `/app/auth_server/scopes.yml`
   - Removed EFS output passthrough from root `outputs.tf`
   - Left comments explaining the migration: "EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence"
   This pattern should be followed for the auth service and MCPGW service.

2. **Module variable wiring**: EFS variables (`efs_throughput_mode`, `efs_provisioned_throughput`) exist only in the child module's `variables.tf`. They are NOT passed through `main.tf` from the root module. They rely entirely on defaults. This simplifies the change since no root-level wiring needs updating.

3. **Output passthrough pattern**: EFS outputs follow the pattern of defining them in the child module then passing them through the root module's `outputs.tf`. Both layers need to be updated.

4. **Script dependencies**: `run-scopes-init-task.sh` is a standalone script that:
   - Extracts `mcp_gateway_efs_id` and `mcp_gateway_efs_access_points.auth_config` from terraform outputs
   - Builds a Docker image
   - Registers a new ECS task definition (`mcp-gateway-scopes-init`) with an `efsVolumeConfiguration` block
   - Runs the task to write `scopes.yml` to the EFS mount at `/auth_config/scopes.yml`
   This script is entirely EFS-dependent. With DocumentDB as the default `storage_backend`, the equivalent is `run-documentdb-init.sh`.

5. **EFS module version**: The EFS is provisioned via `terraform-aws-modules/efs/aws` version `~> 2.0`. The access points are created with POSIX user `gid=1000, uid=1000` and directory permissions `755`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Auth ECS Service | Uses EFS volumes | Mounts `logs` access point at `/app/logs` and `auth_config` at `/efs/auth_config` |
| Auth ECS Service | References `module.efs` | `SCOPES_CONFIG_PATH` env var points to EFS mount path |
| MCPGW ECS Service | Uses EFS volume | Mounts `mcpgw_data` access point at `/app/data` |
| `run-scopes-init-task.sh` | Reads EFS outputs | Extracts EFS ID and access point ID from terraform outputs |
| `post-deployment-setup.sh` | Validates EFS output | Checks `mcp_gateway_efs_id` is non-empty; falls through to EFS init when DocumentDB is absent |
| README.md | Documents IAM permissions | Lists `elasticfilesystem:*` as required IAM permission |

### Constraints and Limitations Discovered

- **Auth service depends on EFS for `SCOPES_CONFIG_PATH`**: The auth service sets `SCOPES_CONFIG_PATH` to `/efs/auth_config/auth_config/scopes.yml`. The registry service already migrated to `/app/auth_server/scopes.yml` (line 822 of `ecs-services.tf`). The auth service should follow the same pattern.
- **Auth service depends on EFS for logs**: The auth service mounts the `logs` access point at `/app/logs`. This volume mount needs to be removed. Logs should go to CloudWatch instead.
- **MCPGW depends on EFS for data**: The MCPGW service mounts the `mcpgw_data` access point at `/app/data`. This needs to be replaced with a non-EFS storage mechanism.
- **No EFS variables at root level**: The EFS variables are only defined in the child module. They are NOT passed through `main.tf`. This means root `variables.tf` and `terraform.tfvars.example` have no EFS entries.
- **6 access points but only 3 used**: The storage.tf defines 6 access points (servers, models, logs, agents, auth_config, mcpgw_data) but only 3 are mounted by ECS services (logs, auth_config, mcpgw_data). The servers, models, and agents access points serve no purpose.
- **EFS security group egress workaround**: The storage.tf includes a manual `aws_vpc_security_group_egress_rule` resource to work around a limitation in the EFS Terraform module (avoiding the module's default port 2049 egress which causes `InvalidParameterValue` errors). This workaround will be deleted along with the EFS module.

## Architecture

### System Context (Before)

```
+--------------------------+
|   terraform/aws-ecs/     |
|                          |
|  +-------------------+   |  EFS File System (storage.tf)
|  | module "efs"      |   |  - 6 access points (servers, models, logs,
|  | - 6 access points |   |    agents, auth_config, mcpgw_data)
|  | - mount targets   |   |  - security group (port 2049 ingress + egress)
|  | - security group  |   |
|  +--------+----------+   |
|           |              |
|  +--------v----------+   |  Auth Service (ecs_service_auth)
|  | module.ecs_service|   |  - mounts: logs (/app/logs), auth_config (/efs/auth_config)
|  |   _auth           |   |  - SCOPES_CONFIG_PATH = /efs/auth_config/auth_config/scopes.yml
|  +-------------------+   |
|  +--------v----------+   |  MCPGW Service (ecs_service_mcpgw)
|  | module.ecs_service|   |  - mounts: mcpgw_data (/app/data)
|  |   _mcpgw          |   |
|  +-------------------+   |
|  +-------------------+   |  Registry Service (already migrated)
|  | module.ecs_service|   |  - volume = {} (no EFS mounts)
|  |   _registry       |   |  - SCOPES_CONFIG_PATH = /app/auth_server/scopes.yml
|  +-------------------+   |
+--------------------------+
```

### System Context (After)

```
+--------------------------+
|   terraform/aws-ecs/     |
|                          |
|  +-------------------+   |
|  | module.ecs_service|   |  Auth Service
|  |   _auth           |   |  - volume = {} (no EFS mounts)
|  |                   |   |  - SCOPES_CONFIG_PATH = /app/auth_server/scopes.yml
|  +-------------------+   |  Logs -> CloudWatch (via ECS/Fargate logging)
|  +-------------------+   |
|  | module.ecs_service|   |  MCPGW Service
|  |   _mcpgw          |   |  - volume = {} (no EFS mounts)
|  +-------------------+   |
|  +-------------------+   |  Registry Service (unchanged)
|  | module.ecs_service|   |  - volume = {} (no EFS mounts)
|  |   _registry       |   |
|  +-------------------+   |
+--------------------------+
  EFS module "efs" DELETED
  storage.tf DELETED
  6 access points DELETED
  mount targets DELETED
  EFS security group DELETED
```

### Component Changes

1. **storage.tf** - Entire file deleted. The `terraform-aws-modules/efs/aws` module v2.0 and its companion `aws_vpc_security_group_egress_rule` resource are removed.

2. **Auth service** (ecs-services.tf):
   - `volume = {}` replaces the current EFS volume block (lines 542-557)
   - EFS mount points removed: `mcp-logs` at `/app/logs` and `auth-config` at `/efs/auth_config`
   - `SCOPES_CONFIG_PATH` changed from `/efs/auth_config/auth_config/scopes.yml` to `/app/auth_server/scopes.yml`

3. **MCPGW service** (ecs-services.tf):
   - `volume = {}` replaces the current EFS volume block (lines 1859-1867)
   - EFS mount point removed: `mcpgw-data` at `/app/data`

4. **Module outputs** (modules/mcp-gateway/outputs.tf):
   - `efs_id`, `efs_arn`, `efs_access_points` outputs removed (lines 47-69)

5. **Root outputs** (outputs.tf):
   - `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` outputs removed (lines 67-81)

6. **Module variables** (modules/mcp-gateway/variables.tf):
   - `efs_throughput_mode` variable removed (lines 259-267)
   - `efs_provisioned_throughput` variable removed (lines 270-274)

7. **Scripts**:
   - `run-scopes-init-task.sh`: Entirely EFS-dependent. Should be deprecated or replaced with a graceful error message
   - `post-deployment-setup.sh`: `mcp_gateway_efs_id` removed from required outputs list; EFS initialization fallback removed

8. **README.md**: `elasticfilesystem:*` removed from IAM permissions list; "EFS Shared Storage" removed from features

## Data Models

No new data models are introduced. This change removes infrastructure resources, not application data.

## API / CLI Design

No new API endpoints or CLI commands are introduced. The only CLI change is the deprecation of `run-scopes-init-task.sh`.

## Configuration Parameters

### Variables Removed

| Variable | Type | Default | Module | Description |
|----------|------|---------|--------|-------------|
| `efs_throughput_mode` | string | `"bursting"` | modules/mcp-gateway | Throughput mode for EFS (bursting or provisioned) |
| `efs_provisioned_throughput` | number | `100` | modules/mcp-gateway | Provisioned throughput in MiB/s for EFS |

### No New Variables

This change does not introduce new configuration parameters. The existing variables that control storage (`storage_backend`, `documentdb_*`) remain unchanged.

### Deployment Surface Checklist

- [ ] `modules/mcp-gateway/variables.tf` - Remove EFS variables
- [ ] `modules/mcp-gateway/outputs.tf` - Remove EFS outputs
- [ ] `outputs.tf` (root) - Remove EFS output passthrough
- [ ] `scripts/post-deployment-setup.sh` - Remove EFS from required outputs
- [ ] `README.md` - Remove `elasticfilesystem:*` from IAM permissions
- [ ] `terraform.tfvars.example` - No EFS entries to remove (already none present)

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| None | - | This change removes dependencies, it does not add any |

Explicitly: No new dependencies are required. The change only removes the `terraform-aws-modules/efs/aws` v2.0 module reference (which is pinned via source URL, not as a standalone dependency).

## Implementation Details

### Step-by-Step Plan

#### Step 1: Delete storage.tf

**File:** `modules/mcp-gateway/storage.tf` (entire file, 183 lines)

**Action:** Delete the file entirely.

This file contains:
- `module "efs"` block (lines 4-163) - provisions the EFS file system, 6 access points, and mount targets
- `aws_vpc_security_group_egress_rule.efs_all_outbound` resource (lines 169-182) - workaround for EFS module egress limitation

```bash
rm terraform/aws-ecs/modules/mcp-gateway/storage.tf
```

#### Step 2: Remove EFS Variables from modules/mcp-gateway/variables.tf

**File:** `modules/mcp-gateway/variables.tf`
**Lines:** 259-274

**Action:** Remove the EFS variable section.

Remove these two variables and the `# EFS Configuration` comment header:

```hcl
# EFS Configuration
variable "efs_throughput_mode" {
  description = "Throughput mode for EFS (bursting or provisioned)"
  type        = string
  default     = "bursting"
  validation {
    condition     = contains(["bursting", "provisioned"], var.efs_throughput_mode)
    error_message = "EFS throughput mode must be either 'bursting' or 'provisioned'."
  }
}

variable "efs_provisioned_throughput" {
  description = "Provisioned throughput in MiB/s for EFS (only used if throughput_mode is provisioned)"
  type        = number
  default     = 100
}
```

Keep the `additional_tags` variable (lines 276-280) that immediately follows.

#### Step 3: Remove EFS Outputs from modules/mcp-gateway/outputs.tf

**File:** `modules/mcp-gateway/outputs.tf`
**Lines:** 47-69

**Action:** Remove the three EFS output blocks:
- `output "efs_id"` (lines 48-52)
- `output "efs_arn"` (lines 54-58)
- `output "efs_access_points"` (lines 60-69)

Keep the Service Discovery outputs and all subsequent outputs.

#### Step 4: Remove EFS Volume Configs from Auth Service in ecs-services.tf

**File:** `modules/mcp-gateway/ecs-services.tf`
**Lines:** 542-557 (volume config), 482-493 (mount points), 220-221 (SCOPES_CONFIG_PATH)

**Action:**

a) Replace the EFS volume block (lines 542-557) with an empty volume:

Before:
```hcl
  volume = {
    mcp-logs = {
      efs_volume_configuration = {
        file_system_id     = module.efs.id
        access_point_id    = module.efs.access_points["logs"].id
        transit_encryption = "ENABLED"
      }
    }
    auth-config = {
      efs_volume_configuration = {
        file_system_id     = module.efs.id
        access_point_id    = module.efs.access_points["auth_config"].id
        transit_encryption = "ENABLED"
      }
    }
  }
```

After:
```hcl
  volume = {}
```

b) Remove EFS mount points from the container definition. Remove:
```hcl
        {
          sourceVolume  = "mcp-logs"
          containerPath = "/app/logs"
          readOnly      = false
        },
        {
          sourceVolume  = "auth-config"
          containerPath = "/efs/auth_config"
          readOnly      = false
        }
```

c) Change `SCOPES_CONFIG_PATH` from EFS path to bundled path:

Before:
```hcl
        {
          name  = "SCOPES_CONFIG_PATH"
          value = "/efs/auth_config/auth_config/scopes.yml"
        },
```

After:
```hcl
        {
          name  = "SCOPES_CONFIG_PATH"
          value = "/app/auth_server/scopes.yml"
        },
```

#### Step 5: Remove EFS Volume Config from MCPGW Service in ecs-services.tf

**File:** `modules/mcp-gateway/ecs-services.tf`
**Lines:** 1859-1867 (volume config), 1803-1809 (mount point)

**Action:**

a) Replace the EFS volume block (lines 1859-1867) with an empty volume:

Before:
```hcl
  volume = {
    mcpgw-data = {
      efs_volume_configuration = {
        file_system_id     = module.efs.id
        access_point_id    = module.efs.access_points["mcpgw_data"].id
        transit_encryption = "ENABLED"
      }
    }
  }
```

After:
```hcl
  volume = {}
```

b) Remove the EFS mount point from the container definition. Remove:
```hcl
        {
          sourceVolume  = "mcpgw-data"
          containerPath = "/app/data"
          readOnly      = false
        }
```

#### Step 6: Remove EFS Outputs from Root outputs.tf

**File:** `outputs.tf`
**Lines:** 67-81

**Action:** Remove the three EFS output blocks and the `# EFS Outputs` comment header:

```hcl
# EFS Outputs
output "mcp_gateway_efs_id" { ... }
output "mcp_gateway_efs_arn" { ... }
output "mcp_gateway_efs_access_points" { ... }
```

Keep the Monitoring Outputs section that immediately follows.

#### Step 7: Update run-scopes-init-task.sh

**File:** `scripts/run-scopes-init-task.sh`
**Lines:** 3, 173-187, 284-298

**Action:** This script is specifically designed to initialize scopes.yml on EFS. After EFS is removed, the script should be deprecated. The most conservative approach is to replace the script with a version that fails gracefully:

```bash
#!/bin/bash
# This script has been deprecated. EFS has been removed from the deployment.
# Please bundle scopes.yml in the container image at
# /app/auth_server/scopes.yml instead.
echo "ERROR: EFS has been removed from the deployment."
echo "Please bundle scopes.yml in the container image at /app/auth_server/scopes.yml"
exit 1
```

Alternatively, the file can be deleted entirely if the script is not referenced elsewhere.

#### Step 8: Update post-deployment-setup.sh

**File:** `scripts/post-deployment-setup.sh`

**Actions:**

a) Line 218: Remove `"mcp_gateway_efs_id"` from the `required_outputs` array:

Before:
```bash
    local required_outputs=(
        "vpc_id"
        "ecs_cluster_name"
        "ecs_cluster_arn"
        "mcp_gateway_url"
        "mcp_gateway_auth_url"
        "keycloak_url"
        "mcp_gateway_efs_id"
    )
```

After:
```bash
    local required_outputs=(
        "vpc_id"
        "ecs_cluster_name"
        "ecs_cluster_arn"
        "mcp_gateway_url"
        "mcp_gateway_auth_url"
        "keycloak_url"
    )
```

b) Lines 549-567: The `_initialize_scopes` function checks for `documentdb_endpoint`. If DocumentDB is NOT present, it falls through to "EFS mode (default)" and runs `run-scopes-init-task.sh`. This fallback should be removed or converted to a no-op since EFS is no longer available. When using DocumentDB (the default `storage_backend`), the script already takes the DocumentDB path, so only the fallback needs to be removed.

#### Step 9: Update README.md

**File:** `README.md`

**Actions:**

a) Line 817: Remove the "EFS Shared Storage" feature line:
```
- **EFS Shared Storage** - Persistent storage for models, logs, and configuration
```

b) Line 1056: Remove `elasticfilesystem:*` from the IAM permissions JSON block:

Before:
```json
        "lambda:*",
        "elasticfilesystem:*",
        "ec2:*",
```

After:
```json
        "lambda:*",
        "ec2:*",
```

## Error Handling

- Terraform validate/plan will fail if any module still references `module.efs.id` or `module.efs.access_points[...]` after the changes. Verify all references are removed.
- The auth service `SCOPES_CONFIG_PATH` must point to a valid path in the container image. If the file does not exist at the new path (`/app/auth_server/scopes.yml`), the service will fail to start. Ensure the container image includes `scopes.yml`.
- If operators have existing terraform state with EFS resources, `terraform plan` will show a destroy-only plan for those resources. This is expected and should be applied to clean up, but operators must ensure EFS data is backed up or accepted as lost before applying.

## Logging

No application logging changes are required. This is an infrastructure-only change. Logs from ECS/Fargate tasks continue to stream to CloudWatch Logs regardless of EFS mount status.

## Observability

### CloudWatch/Alarms

No CloudWatch or alarm changes are needed. The ECS services will continue to generate logs and metrics regardless of whether they mount EFS volumes.

### ECS Service Health Checks

The auth service health check uses `nc -z localhost 18888` and the MCPGW health check uses `nc -z localhost 8003`. Neither depends on EFS being mounted, so health checks remain unchanged.

## Scaling Considerations

- **Current load assumptions**: EFS was previously providing shared storage for auth-config and mcpgw-data. Removing EFS means each ECS task gets its own local ephemeral storage. For the auth service, this is acceptable because `scopes.yml` is a configuration file read on startup (not a shared mutable state). For MCPGW, data persistence must now rely on the DocumentDB/S3 backend rather than local file storage.
- **Horizontal scaling**: The auth service runs with autoscaling (min 2, max 4 tasks). Each replica gets its own copy of the bundled `scopes.yml`, which is the desired behavior for a config file.
- **Bottlenecks**: Removing EFS eliminates the NFS port 2049 dependency and the EFS throughput constraint entirely. This improves reliability (no EFS throttling) and reduces cold-start latency (no NFS mount).
- **Cost**: EFS costs are eliminated. Monthly savings include: file system charge (per-GB/month), data processing fees, and I/O costs. The exact savings depend on data volume and access patterns.

## File Changes

### Deleted Files

| File Path | Lines Removed | Description |
|-----------|---------------|-------------|
| `modules/mcp-gateway/storage.tf` | 183 | EFS file system, 6 access points, mount targets, security group, egress workaround |
| `scripts/run-scopes-init-task.sh` | 488 | EFS-dependent scopes initialization (or deprecate in place) |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|---------------------|
| `modules/mcp-gateway/variables.tf` | ~16 (lines 259-274) | Remove `efs_throughput_mode` and `efs_provisioned_throughput` variables |
| `modules/mcp-gateway/outputs.tf` | ~23 (lines 47-69) | Remove `efs_id`, `efs_arn`, `efs_access_points` outputs |
| `modules/mcp-gateway/ecs-services.tf` | ~30 | Auth service: remove EFS volume config, mount points, update SCOPES_CONFIG_PATH |
| `modules/mcp-gateway/ecs-services.tf` | ~15 | MCPGW service: remove EFS volume config, mount point |
| `outputs.tf` (root) | ~15 (lines 67-81) | Remove `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` |
| `scripts/post-deployment-setup.sh` | ~25 | Remove EFS from required outputs, remove EFS initialization fallback |
| `README.md` | ~2 | Remove "EFS Shared Storage" feature, remove `elasticfilesystem:*` from IAM permissions |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted code | ~671 |
| Modified code | ~90 |
| Total net change | ~-581 |

## Testing Strategy

See `testing.md` for the full testing plan covering functional, backwards-compatibility, deployment, and E2E tests.

## Alternatives Considered

### Alternative 1: Keep EFS but Remove Unused Access Points

**Description:** Keep the EFS file system but remove access points that are no longer used (servers, models, agents) while retaining logs, auth_config, and mcpgw_data.

**Pros:** Less code change, preserves existing data on EFS.

**Cons:** Does not achieve the goal of removing EFS; retains AWS costs; does not simplify the deployment.

**Why Rejected:** The task explicitly asks to remove EFS entirely, not to trim it.

### Alternative 2: Migrate EFS Data to S3 with Mountpoint for S3

**Description:** Keep the same access pattern but use S3 with Mountpoint (S3 FUSE) instead of EFS.

**Pros:** Preserves the existing container configuration with minimal changes; S3 is cheaper than EFS for this use case.

**Cons:** Adds a new dependency (Mountpoint for S3); changes the filesystem semantics (S3 has different consistency guarantees than EFS); more complex than simply removing EFS.

**Why Rejected:** Over-engineered. The application already uses DocumentDB for persistence, and `scopes.yml` is a static config file that can be bundled in the container image.

### Comparison Matrix

| Criteria | Chosen (Remove EFS) | Keep Trimmed EFS | S3 + Mountpoint |
|----------|---------------------|-------------------|-----------------|
| Complexity | Low | Medium | High |
| Cost Reduction | Full | Partial | Partial |
| Code Changes | Extensive (infra only) | Minimal | Moderate |
| Operational Risk | Low | Medium | Medium |

## Rollout Plan

- **Phase 1: Staging** - Deploy Terraform changes to a staging environment first. Verify ECS services start correctly with the new config paths.
- **Phase 2: Verify** - Confirm no EFS resources are in the plan: `terraform plan | grep -c aws_efs` should be 0.
- **Phase 3: Production** - Apply to production. Existing EFS resources will be destroyed by terraform. Operators must accept that data on EFS will be lost.
- **Phase 4: Post-deploy** - Run `run-scopes-init-task.sh` should now fail gracefully. If scopes.yml is not bundled, deploy an updated container image first.

## Open Questions

1. Does the auth service container image already include `scopes.yml` at `/app/auth_server/scopes.yml`? If not, the container image must be rebuilt before the Terraform change is applied.
2. Does the MCPGW service actually use the data stored at `/app/data` (the EFS mount point)? If so, what happens to that data when EFS is removed? Is it re-created from DocumentDB on startup?
3. Should `run-scopes-init-task.sh` be completely deleted, or should it be deprecated in place with a clear error message?
4. What data currently exists on the EFS file system, and can it be safely lost?

## References

- Reference issue: https://github.com/agentic-community/mcp-gateway-registry/issues/1286
- Registry service migration comment: `modules/mcp-gateway/ecs-services.tf` lines 1367, 1419
- Auth service SCOPES_CONFIG_PATH (non-EFS): `modules/mcp-gateway/ecs-services.tf` line 822