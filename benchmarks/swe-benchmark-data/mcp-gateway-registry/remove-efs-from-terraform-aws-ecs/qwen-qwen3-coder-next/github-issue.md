# GitHub Issue: Remove EFS from terraform/aws-ecs/

## Title
Remove EFS from terraform/aws-ecs/ deployment

## Labels
- refactor
- infra
- terraform
- aws

## Description

### Problem Statement
EFS (Elastic File System) is obsolete in this deployment and needs to be removed. The current infrastructure creates EFS file systems, mount targets, security groups, and task-definition volume mounts that reference EFS. These resources are no longer needed and should be entirely removed from the terraform/aws-ecs/ module.

### Proposed Solution
1. Delete the EFS file system module from `storage.tf`
2. Remove mount targets configuration from EFS module
3. Remove EFS security group and related rules
4. Remove access point definitions (servers, models, logs, auth_config, mcpgw_data)
5. Remove all `volume` blocks referencing EFS from ECS service definitions
6. Remove `efs_id`, `efs_arn`, and `efs_access_points` outputs from module
7. Remove `efs_throughput_mode` and `efs_provisioned_throughput` variables
8. Remove `mountPoints` configurations that reference EFS from ECS task definitions
9. Update `outputs.tf` to remove EFS-related outputs
10. Update `variables.tf` to remove EFS-related variables
11. Update `variables.tf` and `terraform.tfvars.example` to reflect the change
12. Verify `terraform validate` and `terraform plan` succeed with the changes

### User Stories
- As a maintainer, I want to remove EFS from the terraform/aws-ecs/ module so that new deployments do not provision unnecessary EFS resources.
- As a developer, I want to clean up the codebase so that references to EFS are completely removed, reducing confusion and maintenance overhead.

### Acceptance Criteria
- [ ] EFS file system resource is deleted from the Terraform configuration
- [ ] EFS mount targets are removed
- [ ] EFS security group and all related rules are removed
- [ ] All EFS access point definitions are removed
- [ ] All ECS task definition volume mounts referencing EFS are removed
- [ ] All `efs_*` outputs (`efs_id`, `efs_arn`, `efs_access_points`) are removed from module outputs
- [ ] All `efs_*` variables (`efs_throughput_mode`, `efs_provisioned_throughput`) are removed from module variables
- [ ] `terraform validate` succeeds without errors
- [ ] `terraform plan` succeeds without EFS-related resource changes (destroy or plan succeeds cleanly)

### Out of Scope
- Removing EFS from the registry/core application code (Python application code)
- Modifying ECS services that do not currently use EFS volumes
- Changing the storage backend configuration (DocumentDB, MongoDB, etc.)
- Updating any documentation outside of the terraform/aws-ecs/ directory

### Dependencies
- No external dependencies required

### Related Issues
- None
