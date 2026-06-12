# Low-Level Design: Remove EFS from terraform-aws-ecs

*Created: 2026-06-12*
*Author: Claude (minimax-m2.5 benchmark)*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Implementation Details](#implementation-details)
5. [File Changes](#file-changes)
6. [Testing Strategy](#testing-strategy)
7. [Rollout Plan](#rollout-plan)

## Overview

### Problem Statement
Amazon EFS (Elastic File System) is no longer required for the MCP Gateway Registry ECS deployment. The application now uses DocumentDB for persistent storage and ephemeral container storage for runtime data. EFS adds unnecessary:
- Monthly costs (provisioned throughput)
- Network latency for I/O
- Security group complexity
- Operational overhead

### Goals
1. Remove all EFS resources from terraform-aws-ecs module
2. Remove EFS-related variables and outputs
3. Remove EFS volume configurations from ECS task definitions
4. Update documentation to remove EFS references

### Non-Goals
- Adding alternative storage solutions
- Modifying ECS task CPU/memory allocation
- Changes to docker-compose configurations

## Codebase Analysis

### Key Files Reviewed

| File | Purpose | EFS References |
|------|---------|----------------|
| `storage.tf` | EFS module definition | Full module + security group |
| `variables.tf` | EFS variables | 2 variables |
| `outputs.tf` | EFS outputs | 3 outputs |
| `ecs-services.tf` | ECS task definitions | 3 EFS volume configs |
| `OPERATIONS.md` | Operational docs | Storage requirements |
| `terraform/README.md` | Module README | Features list |

### Files with EFS References

```
terraform/aws-ecs/
├── modules/mcp-gateway/
│   ├── storage.tf              # module "efs" definition (DELETE)
│   ├── variables.tf            # 2 EFS variables (REMOVE)
│   ├── outputs.tf              # 3 EFS outputs (REMOVE)
│   └── ecs-services.tf         # EFS volume configs (REMOVE)
├── outputs.tf                  # 3 EFS outputs (REMOVE)
└── OPERATIONS.md               # EFS references (UPDATE)

terraform/
└── README.md                   # EFS references (UPDATE)
```

## Architecture

### Current Architecture
```
ECS Task Definition
    |
    +-- Volume: efs (logs)      --> EFS fs-xxx:/logs
    +-- Volume: efs (auth_config) --> EFS fs-xxx:/auth_config
    +-- Volume: efs (mcpgw_data)  --> EFS fs-xxx:/mcpgw_data
```

### Target Architecture
```
ECS Task Definition
    |
    +-- No EFS volumes
    +-- Uses ephemeral storage (default ECS container storage)
    +-- Uses DocumentDB for persistent data
```

## Implementation Details

### Step 1: Remove EFS Module (storage.tf)

**File:** `terraform/aws-ecs/modules/mcp-gateway/storage.tf`

**Action:** DELETE entire file (or truncate to minimal placeholder)

The file contains:
- `module "efs"` (lines 4-163) - DELETE
- `resource "aws_vpc_security_group_egress_rule" "efs_all_outbound"` (lines 169-182) - DELETE

### Step 2: Remove EFS Variables

**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`

Remove lines ~260-275:
```hcl
# DELETE THESE LINES:
variable "efs_throughput_mode" {
  description = "Throughput mode for EFS (bursting or provisioned)"
  type        = string
  default     = "bursting"
  nullable    = false

  validation {
    condition     = contains(["bursting", "provisioned"], var.efs_throughput_mode)
    error_message = "EFS throughput mode must be either 'bursting' or 'provisioned'."
  }
}

variable "efs_provisioned_throughput" {
  description = "Provisioned throughput in MiB/s for EFS (only used if throughput_mode is provisioned)"
  type        = number
  default     = 100
  nullable    = false
}
```

### Step 3: Remove EFS Outputs

**File:** `terraform/aws-ecs/modules/mcp-gateway/outputs.tf`

Remove lines 47-69:
```hcl
# DELETE THESE LINES:
# EFS outputs
output "efs_id" { ... }
output "efs_arn" { ... }
output "efs_access_points" { ... }
```

**File:** `terraform/aws-ecs/outputs.tf`

Remove:
```hcl
output "mcp_gateway_efs_id" { ... }
output "mcp_gateway_efs_arn" { ... }
output "mcp_gateway_efs_access_points" { ... }
```

### Step 4: Remove EFS Volume Configs

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`

#### Location 1: Lines 544-550 (registry service - logs)
```hcl
# DELETE:
efs_volume_configuration = {
  file_system_id     = module.efs.id
  access_point_id    = module.efs.access_points["logs"].id
}
```

#### Location 2: Lines 551-557 (registry service - auth_config)
```hcl
# DELETE:
efs_volume_configuration = {
  file_system_id     = module.efs.id
  access_point_id    = module.efs.access_points["auth_config"].id
}
```

#### Location 3: Lines 1861-1867 (auth service)
```hcl
# DELETE:
efs_volume_configuration = {
  file_system_id     = module.efs.id
  access_point_id    = module.efs.access_points["mcpgw_data"].id
}
```

Also remove any environment variables referencing EFS paths (e.g., `/efs/auth_config/...`).

### Step 5: Update Documentation

**File:** `terraform/aws-ecs/OPERATIONS.md`

Remove or update storage requirements section.

**File:** `terraform/README.md`

Remove EFS from features list:
```diff
- Amazon EFS for persistent storage
```

### Step 6: Terraform Validation

After changes, verify plan shows no EFS resources:
```bash
cd terraform/aws-ecs
terraform init
terraform plan 2>&1 | grep -i efs
# Should return no EFS-related resources
```

## File Changes

### Deleted Files

| File | Lines | Change |
|------|-------|--------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | ~183 | DELETE entire file |

### Modified Files

| File | Lines Changed | Change Description |
|------|---------------|---------------------|
| `variables.tf` | ~15 | Remove 2 EFS variables |
| `outputs.tf` (module) | ~22 | Remove 3 EFS outputs |
| `outputs.tf` (root) | ~15 | Remove 3 EFS outputs |
| `ecs-services.tf` | ~15 | Remove EFS volume configs |
| `OPERATIONS.md` | ~5 | Remove EFS references |
| `terraform/README.md` | ~1 | Remove EFS from features |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| Deleted | ~230 |
| Modified | ~60 |
| **Net Change** | **~-170** |

## Testing Strategy

See `testing.md` for comprehensive testing plan.

### Quick Validation Commands

```bash
# After changes, run from terraform/aws-ecs directory:
cd terraform/aws-ecs

# Initialize
terraform init

# Plan and verify no EFS
terraform plan 2>&1 | grep -i efs || echo "No EFS in plan"

# Validate syntax
terraform validate

# Format code
terraform fmt -recursive
```

## Rollout Plan

1. **Implementation Phase**: Make all Terraform changes
2. **Testing Phase**:
   - Run `terraform plan` to verify no resources created
   - Review the diff to confirm only deletions
3. **Deployment Phase**:
   - Apply changes in dev environment first
   - Verify ECS services restart correctly without EFS
   - Monitor application logs for any storage-related errors
   - Apply to staging/production

## Open Questions

1. **Data Migration**: Is any data currently on EFS that needs to be migrated? No - application uses DocumentDB for persistence.

2. **Backup Consideration**: Should EFS be retained but unused? No - completely remove to avoid costs and confusion.

3. **Rollback Plan**: If issues occur, what is the rollback strategy? Reverting Terraform state from version control.

## Risks

| Risk | Mitigation |
|------|------------|
| ECS tasks fail to start without EFS | Verify all containers handle missing /efs paths gracefully |
| Breaking change for existing deployments | Document migration path in release notes |
| Lost persistent data | Verify all data is in DocumentDB, not EFS |

## References

- AWS ECS documentation on storage: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/efs-volumes.html
- Terraform AWS EFS module: https://registry.terraform.io/modules/terraform-aws-modules/efs/aws