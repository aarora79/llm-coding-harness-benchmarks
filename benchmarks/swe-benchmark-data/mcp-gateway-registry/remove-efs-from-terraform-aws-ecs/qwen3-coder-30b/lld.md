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
EFS (Elastic File System) is no longer needed in the AWS ECS deployment as the application exclusively uses S3/DocumentDB for all persistent storage. Removing EFS will reduce infrastructure costs, simplify the deployment, and eliminate unnecessary complexity.

### Goals
- Remove all EFS resources from terraform/aws-ecs/
- Ensure no ECS task definitions reference EFS volumes
- Update configuration files to remove EFS-related variables
- Maintain compatibility with existing terraform operations

### Non-Goals
- Modify application code or data persistence mechanisms
- Change other infrastructure components beyond EFS in terraform/aws-ecs/
- Alter deployment workflows or processes

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/` | AWS ECS infrastructure definitions | Contains EFS resources to be removed |
| `variables.tf` | Terraform variables definition | Will need to remove EFS variables |
| `terraform.tfvars.example` | Example terraform variables | Will need to remove EFS variables |
| `main.tf` | Main Terraform configuration | May contain EFS resource definitions |
| `task-definitions.tf` | ECS task definition configurations | May contain EFS volume mounts |

### Existing Patterns Identified
1. **Terraform Resource Structure**: EFS resources typically follow a pattern of defining the file system, mount targets, and security groups
2. **Variable Declaration Pattern**: EFS-related variables are declared in variables.tf and referenced throughout the configuration
3. **Module Wiring**: EFS parameters are likely passed to modules that define ECS infrastructure

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| EFS Resources | Depends on | ECS task definitions that reference EFS volumes |
| ECS Task Definitions | Depends on | EFS volume mounts that need to be removed |
| Variables Module | Depends on | EFS variables that need to be removed |
| Module Parameters | Depends on | EFS-related parameters passed to modules |

### Constraints and Limitations Discovered
- Need to ensure no ECS services depend on EFS volumes before removal
- All EFS-related variables must be removed from configuration files
- Terraform state may need to be managed carefully during removal

## Architecture

### System Context Diagram
```
┌─────────────────┐    ┌─────────────────────┐
│   Application   │    │   Infrastructure    │
│                 │    │                     │
│  S3/DocumentDB  │◄───┤  Terraform/AWS ECS  │
│                 │    │                     │
└─────────────────┘    └─────────────────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │  EFS (Removed)    │
                    └───────────────────┘
```

### Sequence Diagram
```
1. Review EFS resources in terraform/aws-ecs/
2. Identify all EFS references in ECS task definitions
3. Remove EFS resources from Terraform
4. Remove EFS volume mounts from ECS task definitions
5. Update variables.tf and terraform.tfvars.example
6. Validate with terraform validate and terraform plan
```

### Component Diagram
```
┌─────────────────────────────────────┐
│        Terraform Configuration      │
├─────────────────────────────────────┤
│  variables.tf                       │
│  terraform.tfvars.example           │
│  main.tf                            │
│  task-definitions.tf                │
│  ...                                │
├─────────────────────────────────────┤
│  AWS ECS Infrastructure             │
│  ├─ EFS Resources (Removed)         │
│  ├─ ECS Cluster                     │
│  ├─ ECS Services                    │
│  └─ ECS Task Definitions            │
└─────────────────────────────────────┘
```

## Data Models

### New Models
None - This is an infrastructure change, not a data model change.

### Model Changes
None - This is an infrastructure change, not a data model change.

## API / CLI Design

### New Endpoints / Commands
None - This is an infrastructure change, not an API change.

## Configuration Parameters

### New Environment Variables
None - This is an infrastructure removal, not adding new configuration.

### Settings / Config Class Updates
None - This is an infrastructure removal, not adding new configuration.

### Deployment Surface Checklist
List every surface where EFS parameters must be removed:
- [ ] `variables.tf` - Remove EFS variables
- [ ] `terraform.tfvars.example` - Remove EFS variables  
- [ ] `main.tf` - Remove EFS resources
- [ ] `task-definitions.tf` - Remove EFS volume mounts
- [ ] Module parameters - Remove EFS-related parameters

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| None | None | This change uses only existing dependencies |

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Identify EFS Resources
**File:** `terraform/aws-ecs/*.tf`
**Lines:** All EFS resource definitions

```hcl
# Remove EFS file system
resource "aws_efs_file_system" "efs_file_system" {
  creation_token = "efs-file-system"
  # ... other EFS properties
}

# Remove EFS mount targets
resource "aws_efs_mount_target" "efs_mount_target" {
  file_system_id  = aws_efs_file_system.efs_file_system.id
  subnet_id       = aws_subnet.private_subnet.id
  security_groups = [aws_security_group.efs_sg.id]
}

# Remove EFS security group
resource "aws_security_group" "efs_sg" {
  name_prefix = "efs-sg-"
  # ... other security group properties
}
```

#### Step 2: Remove EFS Volume Mounts from ECS Task Definitions
**File:** `terraform/aws-ecs/task-definitions.tf`
**Lines:** EFS volume mount configurations

```hcl
# Remove EFS volume references from task definitions
resource "aws_ecs_task_definition" "task_definition" {
  # ... other task definition properties
  
  volume {
    name = "efs-volume"
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.efs_file_system.id
      root_directory = "/"
      transit_encryption = "ENABLED"
    }
  }
}
```

#### Step 3: Update Variables Configuration
**File:** `terraform/aws-ecs/variables.tf`
**Lines:** EFS variable declarations

```hcl
# Remove EFS variables
variable "efs_enabled" {
  description = "Enable EFS for the ECS cluster"
  type        = bool
  default     = false
}

variable "efs_file_system_id" {
  description = "ID of the EFS file system"
  type        = string
  default     = ""
}

# ... other EFS variables
```

#### Step 4: Update Example Variables
**File:** `terraform/aws-ecs/terraform.tfvars.example`
**Lines:** EFS variable examples

```hcl
# Remove EFS variable examples
# efs_enabled = false
# efs_file_system_id = ""
```

#### Step 5: Update Module Wiring
**File:** `terraform/aws-ecs/main.tf` or module files
**Lines:** EFS-related module parameters

```hcl
# Remove EFS parameters from module calls
module "ecs_cluster" {
  source = "../modules/ecs-cluster"
  
  # Remove EFS-related parameters
  # efs_enabled = var.efs_enabled
  # efs_file_system_id = var.efs_file_system_id
}
```

### Error Handling
- Validate that no ECS services reference EFS volumes before removing resources
- Ensure terraform state is properly managed during removal
- Handle cases where EFS resources may already be destroyed

### Logging
- Log successful removal of EFS resources
- Log any dependencies found that prevent removal
- Log validation results from terraform validate and plan

## Observability
### Tracing / Metrics / Logging Points
- Track EFS resource removal in deployment logs
- Monitor for any ECS services that still reference EFS volumes
- Validate terraform plan success/failure in CI/CD pipelines

## Scaling Considerations
- Current load assumptions: EFS is not used, so no scaling impact
- Horizontal scaling: No change needed as EFS is removed
- Bottlenecks: None related to EFS removal
- Caching strategy: No caching needed for this infrastructure change

## File Changes

### New Files
None - This is a removal task, not addition of new files.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/variables.tf` | ~15-30 | Remove all EFS-related variable declarations |
| `terraform/aws-ecs/terraform.tfvars.example` | ~5-10 | Remove EFS variable examples |
| `terraform/aws-ecs/main.tf` | ~20-40 | Remove EFS resource definitions |
| `terraform/aws-ecs/task-definitions.tf` | ~10-20 | Remove EFS volume mounts from task definitions |
| `terraform/aws-ecs/modules/*` | ~5-15 | Remove EFS parameters from module calls |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~0 |
| New tests | ~0 |
| Modified code | ~100 |
| **Total** | **~100** |

## Testing Strategy
This change is covered by the testing.md file that specifies validation procedures.

## Alternatives Considered

### Alternative 1: Disable EFS Instead of Removing
**Description:** Keep EFS resources but disable them through configuration
**Pros / Cons:** 
- Pros: Easier to revert if needed, maintains compatibility
- Cons: Still incurs cost and complexity, doesn't truly remove the burden
**Why Rejected:** The task explicitly states EFS is obsolete and should be removed completely

### Alternative 2: Gradual Migration Approach
**Description:** First update task definitions to remove EFS references, then remove resources
**Pros / Cons:** 
- Pros: More cautious approach, reduces risk of breaking existing deployments
- Cons: More complex, requires multiple steps and coordination
**Why Rejected:** The task description indicates EFS is already obsolete, so a direct removal approach is appropriate

### Comparison Matrix

| Criteria | Chosen | Alt 1 | Alt 2 |
|----------|--------|-------|-------|
| Simplicity | High | Med | Med |
| Risk | Low | Med | Med |
| Cost | Low | Med | Med |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill)
- Phase 2: Testing - Run terraform validate and terraform plan to ensure no errors
- Phase 3: Deployment - Apply the changes to production environment

## Open Questions
- Should we also remove EFS-related security group rules from other modules?
- Are there any other references to EFS in non-Terraform files?

## References
- AWS ECS Task Definition Documentation
- Terraform AWS Provider Documentation
- EFS Best Practices Documentation