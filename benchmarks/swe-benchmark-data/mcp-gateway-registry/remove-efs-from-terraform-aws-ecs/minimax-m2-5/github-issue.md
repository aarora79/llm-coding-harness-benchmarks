# GitHub Issue: Remove EFS from terraform-aws-ecs

## Title
Remove EFS resources from terraform-aws-ecs - migrate to ephemeral/instance storage

## Labels
- refactor
- terraform
- infra
- cleanup

## Description

### Problem Statement
Amazon EFS (Elastic File System) is no longer needed for the MCP Gateway Registry ECS deployment. The application now uses:
- **DocumentDB** for persistent data storage (authentication, registry metadata)
- **Ephemeral container storage** for runtime data (temp files, caches)
- **S3** for large file storage (container images, artifacts)

EFS adds unnecessary cost and complexity:
- Monthly costs for provisioned throughput
- Additional security group rules
- EFS access point management
- Network latency for I/O operations

### Proposed Solution
Remove all EFS resources from the terraform-aws-ecs module:
1. Delete the `module "efs"` resource in `storage.tf`
2. Remove EFS variables from `variables.tf`
3. Remove EFS outputs from `outputs.tf`
4. Remove EFS volume configurations from ECS task definitions in `ecs-services.tf`
5. Update documentation (README.md, OPERATIONS.md)
6. Remove EFS security group rules

### User Stories
- As an operator, I want to reduce AWS costs by removing unused EFS resources
- As a DevOps engineer, I want to simplify the Terraform configuration by removing unused infrastructure
- As a security engineer, I want to reduce the attack surface by removing unnecessary network access

### Acceptance Criteria
- [ ] Remove `module "efs"` from `storage.tf`
- [ ] Remove EFS variables: `efs_throughput_mode`, `efs_provisioned_throughput`
- [ ] Remove EFS outputs: `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points`
- [ ] Remove EFS outputs from module: `efs_id`, `efs_arn`, `efs_access_points`
- [ ] Remove EFS `efs_volume_configuration` blocks from ECS task definitions
- [ ] Remove EFS security group egress rule
- [ ] Update `OPERATIONS.md` to remove EFS from storage requirements
- [ ] Update `terraform/README.md` to remove EFS from features list
- [ ] Ensure Terraform plan shows no EFS resources

### Out of Scope
- Modifying the ECS task definition CPU/memory allocation
- Adding new storage solutions (EFS removal only)
- Changes to the docker-compose configurations (not ECS-specific)

### Dependencies
- None

### Related Issues
- Part of infrastructure simplification effort