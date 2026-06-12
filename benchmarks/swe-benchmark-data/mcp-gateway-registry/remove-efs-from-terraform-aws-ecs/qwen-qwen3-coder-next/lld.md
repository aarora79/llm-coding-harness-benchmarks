# Low-Level Design: Remove EFS from terraform/aws-ecs/

*Created: 2026-06-12*
*Author: Qwen Qwen3-Coder-Next*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture Changes](#architecture-changes)
4. [File Changes](#file-changes)
5. [Implementation Details](#implementation-details)
6. [Validation](#validation)

## Overview
### Problem Statement
EFS (Elastic File System) is obsolete in the terraform/aws-ecs/ deployment. The infrastructure currently provisions EFS file systems, mount targets, security groups, and task-definition volume mounts. These resources need to be completely removed from the Terraform configuration.

### Goals
- Remove all EFS-related resources from terraform/aws-ecs/
- Remove all EFS-related variables, outputs, and references
- Ensure terraform validate and terraform plan succeed after changes

### Non-Goals
- Does not change the registry/core application code (Python)
- Does not change storage backends (DocumentDB, MongoDB, etc.)
- Does not modify ECS service logic, only Terraform configuration

## Codebase Analysis

### Key Files Reviewed

| File | Purpose | EFS-Related Changes Required |
|------|---------|------------------------------|
| `modules/mcp-gateway/storage.tf` | EFS file system, mount targets, security group, access points | **DELETE** entire file content - EFS module and related resources |
| `modules/mcp-gateway/variables.tf` | Module variables including EFS configuration | Remove `efs_throughput_mode` (line 260-268) and `efs_provisioned_throughput` (line 270-274) variables |
| `modules/mcp-gateway/outputs.tf` | Module outputs including EFS IDs | Remove `efs_id`, `efs_arn`, `efs_access_points` outputs |
| `modules/mcp-gateway/ecs-services.tf` | ECS services with EFS volume mounts | Remove EFS `volume` blocks from auth (lines 542-557) and mcpgw (lines 1859-1867); remove EFS `mountPoints` from auth container (lines 482-493) and mcpgw container (lines 803-809) |
| `modules/mcp-gateway/locals.tf` | Local values | No changes needed |
| `modules/mcp-gateway/data.tf` | Data sources | No changes needed |
| `modules/mcp-gateway/iam.tf` | IAM policies | No changes needed |
| `modules/mcp-gateway/networking.tf` | Networking configuration | No changes needed |
| `modules/mcp-gateway/monitoring.tf` | Monitoring resources | No changes needed |
| `modules/mcp-gateway/main.tf` | Module entry point | No changes needed |
| `modules/mcp-gateway/versions.tf` | Provider versions | No changes needed |
| `outputs.tf` | Root module outputs | Remove `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` outputs |

### Existing Patterns Identified
1. **EFS Module Usage**: The `terraform-aws-modules/efs/aws` module (version ~> 2.0) was used to create EFS resources with access points for different purposes (servers, models, logs, auth_config, agents, mcpgw_data).
2. **ECS Service Volume Mounts**: EFS volumes were attached to ECS tasks via `efs_volume_configuration` with access point references.
3. **Container Environment Variables**: The auth-server service had `SCOPES_CONFIG_PATH` pointing to `/efs/auth_config/auth_config/scopes.yml` - **Note: This path reference may need review as it suggests the container expects EFS-mapped paths**.

### Integration Points
- **auth-server service**: Used EFS for auth_config access point (mounted at `/efs/auth_config`)
- **mcpgw service**: Used EFS for mcpgw_data access point (mounted at `/app/data`)
- **registry service**: Has comment indicating EFS volumes were removed (lines 1367-1368), no changes needed

### Constraints and Limitations Discovered
1. The auth-server container expects paths like `/efs/auth_config/auth_config/scopes.yml`. When EFS is removed, this path must be changed to use local container paths or another storage mechanism.
2. The registry service already has comments indicating EFS volumes were removed (lines 1367-1368), so no changes needed there.

## Architecture Changes

### Before (With EFS)
```
ECS Tasks
   │
   ├─ auth-server ──> EFS (access_point: auth_config)
   ├─ mcpgw ────────> EFS (access_point: mcpgw_data)
   └─ registry ─────> No EFS (uses ephemeral storage)
```

### After (Without EFS)
```
ECS Tasks
   │
   ├─ auth-server ──> No EFS volumes (use local paths or container storage)
   ├─ mcpgw ────────> No EFS volumes (use local paths or container storage)
   └─ registry ─────> No EFS volumes (uses ephemeral storage)
```

## Data Models
*No data model changes required - this is a pure Terraform configuration cleanup.*

## Configuration Parameters

### To Be Removed Variables
| Variable | Current Default | Description |
|----------|-----------------|-------------|
| `efs_throughput_mode` | `"bursting"` | Throughput mode for EFS (bursting or provisioned) |
| `efs_provisioned_throughput` | `100` | Provisioned throughput in MiB/s for EFS |

### Environment Variable Paths to Review
The auth-server needs path updates for EFS-mapped configurations:
- `SCOPES_CONFIG_PATH`: Currently `/efs/auth_config/auth_config/scopes.yml`
  - After EFS removal, this may need to reference a local path inside the container
  - **Action needed: Verify the actual path inside the container image**

## New Dependencies
*No new dependencies required.*

This change uses only existing dependencies.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Remove EFS Module from storage.tf

**File:** `modules/mcp-gateway/storage.tf`

**Action:** Delete the ENTIRE file or comment out all content.

The file contains:
- `module "efs"` block with all access point definitions (servers, models, logs, agents, auth_config, mcpgw_data)
- `aws_vpc_security_group_egress_rule` for EFS all outbound

**Change:**
```hcl
# DELETE OR COMMENT OUT entire file content
# module "efs" { ... }
# resource "aws_vpc_security_group_egress_rule" "efs_all_outbound" { ... }
```

#### Step 2: Remove EFS volume blocks from ECS services

**File:** `modules/mcp-gateway/ecs-services.tf`

**Action for auth-server (lines 542-557):**
```hcl
# BEFORE (current volume block):
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

# AFTER (empty volume block):
volume = {}
```

**Action for mcpgw (lines 1859-1867):**
```hcl
# BEFORE:
volume = {
  mcpgw-data = {
    efs_volume_configuration = {
      file_system_id     = module.efs.id
      access_point_id    = module.efs.access_points["mcpgw_data"].id
      transit_encryption = "ENABLED"
    }
  }
}

# AFTER:
volume = {}
```

#### Step 3: Remove EFS mountPoints from ECS containers

**File:** `modules/mcp-gateway/ecs-services.tf`

**Action for auth-server container (lines 482-493):**
```hcl
# BEFORE:
mountPoints = [
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
]

# AFTER:
mountPoints = []
```

**Action for mcpgw container (lines 803-809):**
```hcl
# BEFORE:
mountPoints = [
  {
    sourceVolume  = "mcpgw-data"
    containerPath = "/app/data"
    readOnly      = false
  }
]

# AFTER:
mountPoints = []
```

#### Step 4: Remove EFS outputs from module outputs

**File:** `modules/mcp-gateway/outputs.tf`

**Remove lines 47-69:**
```hcl
# REMOVE these output blocks:
output "efs_id" { ... }
output "efs_arn" { ... }
output "efs_access_points" { ... }
```

#### Step 5: Remove EFS variables from module variables

**File:** `modules/mcp-gateway/variables.tf`

**Remove lines 259-274:**
```hcl
# REMOVE these variable blocks:
variable "efs_throughput_mode" { ... }
variable "efs_provisioned_throughput" { ... }
```

#### Step 6: Update root outputs.tf

**File:** `outputs.tf`

**Remove lines 67-81:**
```hcl
# REMOVE these output blocks:
output "mcp_gateway_efs_id" { ... }
output "mcp_gateway_efs_arn" { ... }
output "mcp_gateway_efs_access_points" { ... }
```

#### Step 7: Verify SCOPES_CONFIG_PATH path

**File:** `modules/mcp-gateway/ecs-services.tf` (auth-server container)

**Review needed for line 221:**
```hcl
{
  name  = "SCOPES_CONFIG_PATH"
  value = "/efs/auth_config/auth_config/scopes.yml"
}
```

**Action:** Verify if this path needs to be updated to reflect local container paths or if the container image handles this differently after EFS removal.

## Validation

### terraform validate
```bash
cd terraform/aws-ecs
terraform init
terraform validate
```

### terraform plan
```bash
terraform plan
```

Expected: Plan should show EFS-related resources being destroyed if they exist in any existing state, or no EFS resources if starting fresh.

## File Changes Summary

### Files to Modify

| File | Lines | Change Description |
|------|-------|--------------------|
| `modules/mcp-gateway/storage.tf` | 1-182 | Delete entire file - EFS module and security group |
| `modules/mcp-gateway/variables.tf` | ~14 | Remove efs_throughput_mode and efs_provisioned_throughput variables |
| `modules/mcp-gateway/outputs.tf` | ~23 | Remove efs_id, efs_arn, efs_access_points outputs |
| `modules/mcp-gateway/ecs-services.tf` | ~30 | Remove volume blocks and mountPoints for EFS |
| `outputs.tf` | ~15 | Remove mcp_gateway_efs_* outputs |
| **Total** | **~225** | |

### Estimation
- **Files Modified:** 5
- **Lines Removed:** ~225
- **Lines Added:** 0
- **Net Change:** -225 lines
