# Low-Level Design: Remove EFS from terraform/aws-ecs

*Created: 2026-07-06*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [Configuration Parameters](#configuration-parameters)
6. [New Dependencies](#new-dependencies)
7. [Implementation Details](#implementation-details)
8. [Observability](#observability)
9. [File Changes](#file-changes)
10. [Testing Strategy](#testing-strategy)
11. [Alternatives Considered](#alternatives-considered)
12. [Rollout Plan](#rollout-plan)
13. [Open Questions](#open-questions)

## Overview

### Problem Statement
The terraform/aws-ecs deployment provisions an AWS EFS file system with mount targets in every private subnet, security groups, and six access points (servers, models, logs, agents, auth_config, mcpgw_data). The MCP Gateway Registry application has migrated to ephemeral container storage and DocumentDB for persistence, making the EFS file system an unused resource that costs money and adds complexity.

### Goals
- Remove all EFS resources from the terraform/aws-ecs module
- Remove EFS-related variables, outputs, and IAM permission documentation
- Ensure `terraform validate` and `terraform plan` pass cleanly
- Remove the IAM permission `elasticfilesystem:*` from README.md

### Non-Goals
- Removing DocumentDB (separate migration)
- Modifying the Docker image contents
- Removing EFS from Helm charts (charts/ directory)
- Removing EFS from Docker Compose (docker/ directory)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `modules/mcp-gateway/storage.tf` | EFS resource definitions (6 access points, 1 security group, mount targets) | Primary removal target |
| `modules/mcp-gateway/ecs-services.tf` | ECS task definitions for registry, auth-server, mcpgw | EFS volume configs and mount points to remove |
| `modules/mcp-gateway/variables.tf` | 2 EFS variables (efs_throughput_mode, efs_provisioned_throughput) | Remove variables |
| `modules/mcp-gateway/outputs.tf` | 3 EFS outputs (efs_id, efs_arn, efs_access_points) | Remove outputs |
| `outputs.tf` (root) | 3 EFS outputs passing through from module | Remove outputs |
| `variables.tf` (root) | No EFS variables (EFS config lives in the module) | No changes needed |
| `terraform.tfvars.example` | Example config | No EFS variables present (already commented/absent) |
| `README.md` | IAM permissions list | Remove `elasticfilesystem:*` |
| `scripts/post-deployment-setup.sh` | References EFS ID in scope init | Update/remove EFS references |
| `scripts/run-scopes-init-task.sh` | References EFS ID and access points | Update/remove EFS references |

### Existing Patterns Identified

1. **Module-based resource organization**: The `modules/mcp-gateway/` directory is a self-contained Terraform module with its own variables, outputs, resources, and data sources. It uses the `terraform-aws-modules/efs/aws` module (version ~2.0) for EFS provisioning.

2. **Pass-through outputs**: The root `outputs.tf` passes EFS outputs through from the mcp_gateway module. This pattern uses `module.mcp_gateway.efs_id` etc.

3. **ECS volume configuration**: The `ecs` module uses `efs_volume_configuration` blocks with `file_system_id`, `access_point_id`, and `transit_encryption = "ENABLED"` for each EFS volume.

4. **Registry already removed EFS**: The registry service task definition at line 1367-1369 already has `mountPoints = []` and `volume = {}` with the comment "EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence." This is the exact pattern to follow for auth-server and mcpgw.

5. **Auth-server SCOPES_CONFIG_PATH**: The auth-server uses `SCOPES_CONFIG_PATH` env var set to `/efs/auth_config/auth_config/scopes.yml`. However, the mcpgw service (line 822) uses `/app/auth_server/scopes.yml` as an alternative path, suggesting scopes.yml can be baked into the image.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `modules/mcp-gateway/storage.tf` | Depends on | `var.private_subnet_ids`, `var.vpc_id`, `local.name_prefix`, `local.common_tags`, `data.aws_vpc.vpc` |
| `modules/mcp-gateway/ecs-services.tf` | Depends on | `module.efs.id`, `module.efs.access_points[...]` for auth-server and mcpgw |
| Root `outputs.tf` | Depends on | `module.mcp_gateway.efs_id`, `module.mcp_gateway.efs_arn`, `module.mcp_gateway.efs_access_points` |
| `README.md` | Documents | `elasticfilesystem:*` IAM permission |
| Scripts | Consume | `mcp_gateway_efs_id` and `mcp_gateway_efs_access_points` from terraform outputs |

### Constraints and Limitations Discovered
- The EFS module creates mount targets in every private subnet (dynamic map via for loop).
- The EFS security group has an ingress rule for NFS (port 2049) from the VPC CIDR and a separate egress rule resource.
- The auth-server has TWO SCOPES_CONFIG_PATH values: one pointing to EFS (`/efs/auth_config/auth_config/scopes.yml`) and another to the image (`/app/auth_server/scopes.yml`). The EFS path is in the auth-server definition; the image path is in the mcpgw definition.
- The `terraform-aws-modules/efs/aws` module is used with version constraint `~> 2.0`.

## Architecture

### System Context

```
BEFORE:
  +------------------+     +------------------+     +------------------+
  |   VPC/Private    |     |  EFS File System |     | ECS Tasks        |
  |   Subnets        |     |  + Security Grp  |     |                  |
  |   +--------------|-----|--> 6 Access Pts  |     | Registry (no EFS)|
  |   |  Auth Server |     |  +------------------+     | Auth Server (EFS)|
  |   |  MCPGW       |     +------------------+     | MCPGW (EFS)      |
  |   +--------------|-----|------------------|
  +------------------+     +------------------+

AFTER:
  +------------------+     +------------------+
  |   VPC/Private    |     |  ECS Tasks       |
  |   Subnets        |     |                  |
  |   +--------------|-----| Registry         |
  |   |  Auth Server |     | Auth Server      |
  |   |  MCPGW       |     | MCPGW            |
  |   +--------------|-----+------------------|
  +------------------+     (ephemeral storage  |
                            + DocumentDB)
```

### Component Diagram

```
modules/mcp-gateway/
  +-------------------+       +-------------------+       +-------------------+
  |    storage.tf     |       |  ecs-services.tf  |       |   variables.tf    |
  |  (REMOVE ENTIRELY)|       |  (modify volumes) |       |  (remove 2 vars)  |
  | - module.efs      |       | - auth-server:    |       | - efs_throughput_ |
  | - efs sg          |       |   remove 2 volumes|       |   mode            |
  | - efs egress rule |       |   remove 2 mounts |       | - efs_provisioned |
  +-------------------+       | - mcpgw:          |       |   throughput       |
          |                   |   remove 1 volume |       +-------------------+
          v                   +-------------------+                  |
  +-------------------+                                +-------------------+
  |  outputs.tf       |<-------+                         |  outputs.tf       |
  |  (remove 3 efs    |        |                         |  (root)           |
  |   outputs)         |        |                         |  (remove 3 efs    |
  +-------------------+        |                         |   outputs)        |
                               |                         +-------------------+
                               v
                    +-------------------+
                    |  README.md        |
                    |  (remove IAM perm)|
                    +-------------------+
```

## Data Models

No new data models needed. The change only removes existing resources.

### Removed Variables

```hcl
# REMOVED from modules/mcp-gateway/variables.tf (lines 259-274)
variable "efs_throughput_mode" {
  description = "Throughput mode for EFS (bursting or provisioned)"
  type        = string
  default     = "bursting"
}

variable "efs_provisioned_throughput" {
  description = "Provisioned throughput in MiB/s for EFS"
  type        = number
  default     = 100
}
```

### Removed Outputs

```hcl
# REMOVED from modules/mcp-gateway/outputs.tf (lines 47-69)
output "efs_id"
output "efs_arn"
output "efs_access_points"

# REMOVED from root outputs.tf (lines 67-81)
output "mcp_gateway_efs_id"
output "mcp_gateway_efs_arn"
output "mcp_gateway_efs_access_points"
```

## Configuration Parameters

### Removed Variables

| Variable | Default | Removed From |
|----------|---------|-------------|
| `efs_throughput_mode` | `"bursting"` | modules/mcp-gateway/variables.tf |
| `efs_provisioned_throughput` | `100` | modules/mcp-gateway/variables.tf |

### Deployment Surface Checklist

| Surface | Change |
|---------|--------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | Delete entire file |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | Remove EFS volumes, mount points |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Remove 2 variables |
| `terraform/aws-ecs/modules/mcp-gateway/outputs.tf` | Remove 3 outputs |
| `terraform/aws-ecs/outputs.tf` | Remove 3 outputs |
| `terraform/aws-ecs/README.md` | Remove `elasticfilesystem:*` from IAM |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | Remove EFS references |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | Remove EFS references |

## New Dependencies

This change removes a dependency:

| Package | Type | Required By |
|---------|------|-------------|
| `terraform-aws-modules/efs/aws` ~> 2.0 | Terraform module | Removed |

No new dependencies are required.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Delete storage.tf (EFS resource definitions)

**File:** `modules/mcp-gateway/storage.tf`
**Action:** Delete the entire file (183 lines)

This file contains:
- `module.efs` (lines 1-163) - the EFS file system, 6 access points, mount targets, security group
- `aws_vpc_security_group_egress_rule.efs_all_outbound` (lines 169-182) - egress rule

The module depends on `var.private_subnet_ids`, `var.vpc_id`, `local.name_prefix`, `local.common_tags`, and `data.aws_vpc.vpc`. All these remain needed for other resources.

#### Step 2: Remove EFS volumes and mount points from auth-server

**File:** `modules/mcp-gateway/ecs-services.tf`
**Lines:** ~480-557 (auth-server service module)

Changes:
1. Remove `auth-config` mount point from `mountPoints` block (lines 488-492):
```hcl
# REMOVE:
{
  sourceVolume  = "auth-config"
  containerPath = "/efs/auth_config"
  readOnly      = false
}
```

2. Remove `auth-config` and `mcp-logs` entries from `volume` block (lines 542-557):
```hcl
# REMOVE:
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
```

3. Change `volume = {}` to remove the two entries (the volume block currently defines `mcp-logs` and `auth-config`).

4. Update `SCOPES_CONFIG_PATH` environment variable (line 221) from `/efs/auth_config/auth_config/scopes.yml` to `/app/auth_server/scopes.yml` (matching the mcpgw pattern at line 822).

#### Step 3: Remove EFS volumes from mcpgw service

**File:** `modules/mcp-gateway/ecs-services.tf`
**Lines:** ~1859-1867 (mcpgw service module)

Changes:
1. Remove the `volume` block (lines 1859-1867):
```hcl
# REMOVE entire volume block:
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
2. Change `volume = {}` to replace the block.

3. If `app_log_dir` environment variable points to a path under the EFS mount (e.g., `/efs/...`), change it to the default empty string or to the ephemeral storage path. Check line 1760 where `app_log_dir` is set for the mcpgw service.

#### Step 4: Remove EFS variables

**File:** `modules/mcp-gateway/variables.tf`
**Lines:** 259-274

Remove:
```hcl
# EFS Configuration
variable "efs_throughput_mode" { ... }
variable "efs_provisioned_throughput" { ... }
```

#### Step 5: Remove EFS outputs from module

**File:** `modules/mcp-gateway/outputs.tf`
**Lines:** 47-69

Remove:
```hcl
# EFS outputs
output "efs_id" { ... }
output "efs_arn" { ... }
output "efs_access_points" { ... }
```

#### Step 6: Remove EFS outputs from root

**File:** `terraform/aws-ecs/outputs.tf`
**Lines:** 67-81

Remove:
```hcl
# EFS Outputs
output "mcp_gateway_efs_id" { ... }
output "mcp_gateway_efs_arn" { ... }
output "mcp_gateway_efs_access_points" { ... }
```

#### Step 7: Update README.md

**File:** `terraform/aws-ecs/README.md`
**Line:** 1056

Remove `"elasticfilesystem:*",` from the IAM permissions JSON block (line 1056).

#### Step 8: Update scripts

**File:** `terraform/aws-ecs/scripts/post-deployment-setup.sh`
**Lines:** 12, 218, 549-561

Changes:
- Line 12: Remove "Initializes MCP scopes on EFS" from the script description
- Line 218: Remove `mcp_gateway_efs_id` from the outputs extraction
- Lines 549-561: Remove the "EFS mode (default)" block for scopes initialization, or update it to indicate the change

**File:** `terraform/aws-ecs/scripts/run-scopes-init-task.sh`
**Lines:** 173-184, 287-288

Changes:
- Lines 173-184: Remove or comment out EFS ID and access point extraction from terraform outputs
- Lines 287-288: Remove or comment out `efsVolumeConfiguration` from the task definition JSON
- Line 478: Update log message that references EFS mount

These scripts were used for initializing scopes on EFS. Since scopes.yml can now be provided via the Docker image or environment variable, the scripts should either be removed entirely or updated to use a non-EFS initialization path.

### Error Handling

Since this is a removal-only change, there are no runtime error cases to handle. Terraform will:
1. On `terraform plan`: show that the EFS resources will be destroyed
2. On `terraform apply`: destroy EFS resources (this is a destructive operation that platform engineers must be aware of)
3. The EFS file system has a 7-day pending-deletion state by default, which protects against accidental immediate loss

### Logging

No logging changes needed. The EFS `logs` access point is removed along with the EFS file system. The registry and auth-server already use CloudWatch logging via the ECS `cloudwatch_log_group_name` configuration.

## Observability

### Tracing / Metrics / Logging Points

No new observability points. The EFS access points and associated CloudWatch metrics (EFS throughput, burst balance) will disappear from monitoring as the resources are destroyed.

**Pre-removal note:** Before applying, platform engineers should review EFS CloudWatch metrics to confirm the file system has been unused (zero throughput, zero burst balance consumption).

## Scaling Considerations

- **Current load assumptions:** EFS is not currently serving any request traffic. The auth-server and mcpgw mount points are dead paths.
- **Horizontal scaling:** No impact. ECS task scaling operates independently of EFS.
- **Bottlenecks:** None. Removing EFS eliminates a potential bottleneck (NFS latency, EFS throughput limits).
- **Caching strategy:** N/A. No caching is involved in this change.

## File Changes

### Deleted Files

| File Path | Lines | Description |
|-----------|-------|-------------|
| `modules/mcp-gateway/storage.tf` | 183 | EFS storage resources (module, security group, egress rule) |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `modules/mcp-gateway/ecs-services.tf` | ~540-557 | Remove auth-server EFS volumes and mount points |
| `modules/mcp-gateway/ecs-services.tf` | ~1859-1867 | Remove mcpgw EFS volumes |
| `modules/mcp-gateway/ecs-services.tf` | ~221 | Update auth-server SCOPES_CONFIG_PATH |
| `modules/mcp-gateway/ecs-services.tf` | ~1760 | Check mcpgw app_log_dir if EFS-referencing |
| `modules/mcp-gateway/variables.tf` | 259-274 | Remove 2 EFS variables |
| `modules/mcp-gateway/outputs.tf` | 47-69 | Remove 3 EFS outputs |
| `terraform/aws-ecs/outputs.tf` | 67-81 | Remove 3 EFS outputs from root module |
| `terraform/aws-ecs/README.md` | 1056 | Remove `elasticfilesystem:*` IAM permission |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | 12, 218, 549-561 | Remove EFS references |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | 173-184, 287-288, 478 | Remove EFS references |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted (storage.tf) | -183 |
| Deleted (outputs - module) | -23 |
| Deleted (outputs - root) | -15 |
| Deleted (variables) | -16 |
| Modified (ecs-services.tf) | ~-10 |
| Modified (README.md) | -1 |
| Modified (scripts) | ~-20 |
| **Total** | **~-268** |

## Testing Strategy

See the companion `testing.md` document for the full testing plan.

### Summary

1. **Terraform validate**: `terraform -chdir=terraform/aws-ecs validate`
2. **Terraform plan**: `terraform -chdir=terraform/aws-ecs plan` - verify no EFS resources in plan output
3. **Grep verification**: `grep -rn "efs\|aws_efs\|file_system" --include="*.tf" terraform/aws-ecs/` - should return zero matches
4. **README check**: `grep -n "elasticfilesystem" terraform/aws-ecs/README.md` - should return zero matches
5. **Script verification**: Check that post-deployment-setup.sh and run-scopes-init-task.sh no longer reference EFS

## Alternatives Considered

### Alternative 1: Mark EFS as `lifecycle { prevent_destroy = true }` then migrate
**Description:** Add lifecycle protection to prevent accidental EFS deletion before the application fully migrates.
**Pros:** Safety net against accidental deletion.
**Cons:** Adds complexity for a resource that is already unused. The registry already removed its EFS mounts without this protection.
**Why Rejected:** The application has already migrated to ephemeral storage + DocumentDB. There is no need for a transitional protection layer.

### Alternative 2: Disable EFS via variable toggle
**Description:** Add an `enable_efs` variable that defaults to `false`, allowing gradual rollout.
**Pros:** Can roll out change without immediate destruction.
**Cons:** Adds a dead code path and variable that must be maintained forever. Introduces conditional complexity in the module.
**Why Rejected:** No existing deployments are actively using EFS (registry already removed mounts). A toggle adds maintenance burden for zero benefit.

### Comparison Matrix

| Criteria | Chosen (full removal) | Alt 1 (prevent_destroy) | Alt 2 (variable toggle) |
|----------|----------------------|-------------------------|-------------------------|
| Complexity | Low | Medium | High |
| Maintenance | None | Lifecycle rule forever | Dead code path |
| Cost savings | Immediate | Deferred | Deferred |
| Risk | Low (EFS unused) | Low | Low |

## Rollout Plan
- Phase 1: Apply terraform changes (remove EFS resources)
- Phase 2: Verify deployments still work without EFS
- Phase 3: Update documentation (README, IAM examples)
- Phase 4: Remove old EFS resources (terraform will handle destruction)

## Open Questions
1. **Scope initialization**: The `run-scopes-init-task.sh` script creates a dedicated ECS task to initialize scopes on EFS. After EFS removal, how should scopes.yml be managed? Options: bake into Docker image, use S3 + init container, or use ConfigMap/Secrets.
2. **Existing EFS file system**: For current deployments, `terraform apply` will attempt to destroy the EFS file system. This requires the file system to be empty or force deletion to be enabled. A warning should be added to the README for operators performing this change on live infrastructure.
3. **App log storage**: The mcpgw service writes app logs. If `app_log_dir` previously pointed to the EFS mount, it needs to use the container's ephemeral storage or DocumentDB centralized logging instead.

## References
- Registry EFS removal pattern: `modules/mcp-gateway/ecs-services.tf` lines 1367-1420 (comment: "EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence")
- Terraform AWS EFS module: `terraform-aws-modules/efs/aws` ~> 2.0