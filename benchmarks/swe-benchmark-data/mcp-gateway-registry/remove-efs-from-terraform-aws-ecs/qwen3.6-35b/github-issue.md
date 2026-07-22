# GitHub Issue: Remove EFS from terraform/aws-ecs/

## Title
Remove EFS storage layer from terraform/aws-ecs/ deployment

## Labels
- enhancement
- refactor
- infra
- tech-debt

## Description

### Problem Statement

The MCP Gateway Registry deployment uses AWS EFS (Elastic File System) for persistent storage of configuration files, logs, and application data. However, EFS is no longer needed in this deployment. The application has migrated to S3 and DocumentDB for all persistent storage needs:

- The registry ECS service has already removed its EFS volume mounts and switched to ephemeral storage with DocumentDB persistence.
- Application data is persisted in DocumentDB (MongoDB-compatible).
- Logs are shipped to CloudWatch Logs.
- Configuration (scopes.yml) is managed through DocumentDB init scripts.
- EFS adds unnecessary infrastructure cost (per GB/month storage + IOPS) and operational complexity (security groups, mount targets, NFS protocol).

Removing EFS will:
- Reduce monthly AWS costs (EFS storage + data transfer costs)
- Simplify the Terraform configuration
- Remove a single point of failure in the network topology
- Reduce security attack surface (fewer security groups, fewer open ports)

### Proposed Solution

Remove all EFS-related Terraform resources and ECS task definition volume mounts from the `terraform/aws-ecs/` module:

1. Delete the EFS file system, mount targets, access points, and security group
2. Remove EFS volume mounts and volume configurations from active ECS task definitions (auth-server and mcpgw)
3. Clean up EFS-related variables, outputs, and module references
4. Remove or update post-deployment scripts that depend on EFS
5. Update documentation that references EFS

### User Stories

- As an operator deploying via Terraform, I want the deployment to not provision EFS resources so that my infrastructure is simpler and cheaper.
- As a Site Reliability Engineer, I want no EFS mount points in any ECS task definition so that service failures are not caused by EFS network issues.
- As a cost-conscious organization, I want to eliminate EFS costs so that my monthly AWS bill is lower.

### Acceptance Criteria

- [ ] The EFS module (`terraform-aws-modules/efs/aws`) is removed from `storage.tf` and the file itself is deleted
- [ ] EFS mount targets (one per private subnet) are removed
- [ ] EFS security group and egress rules are removed
- [ ] All six EFS access points (`servers`, `models`, `logs`, `agents`, `auth_config`, `mcpgw_data`) are removed
- [ ] Auth-server ECS service no longer mounts EFS volumes (`mcp-logs`, `auth-config`)
- [ ] Auth-server `SCOPES_CONFIG_PATH` environment variable is updated to reference a non-EFS path (e.g., the DocumentDB init path at `/app/auth_server/scopes.yml`)
- [ ] MCPGW ECS service no longer mounts EFS volume (`mcpgw-data`)
- [ ] EFS-related variables (`efs_throughput_mode`, `efs_provisioned_throughput`) are removed from `variables.tf`
- [ ] EFS-related outputs (`efs_id`, `efs_arn`, `efs_access_points`) are removed from `outputs.tf` (module and root level)
- [ ] The `run-scopes-init-task.sh` script (EFS-only scopes initialization) is deleted or made inert
- [ ] The `post-deployment-setup.sh` script no longer references EFS in required outputs or initialization flow
- [ ] The root-level `terraform.tfvars.example` file does not contain EFS-specific configuration
- [ ] `terraform validate` succeeds with no errors
- [ ] `terraform plan` succeeds with no EFS-related resources in the planned changes
- [ ] Documentation references to EFS are updated or removed

### Out of Scope

- Migration of existing EFS data to S3/DocumentDB (should be done before this change, documented separately)
- Changes to the Helm charts directory (`charts/`) - those target Kubernetes deployments, not ECS
- Changes to the application Python code (the application already supports non-EFS storage via DocumentDB)
- Changes to the registry ECS service - it already has EFS removed (only comments remain)
- Demo server services (CurrentTime, RealServerFakeTools, Flight Booking, Travel Assistant) - they do not mount EFS

### Dependencies

- Existing EFS data must be migrated before this change is applied in production
- Operators must ensure `storage_backend` is set to `documentdb` in their Terraform configuration, as the scopes initialization path depends on DocumentDB

### Related Issues

- GitHub Issue #1286: Remove EFS from terraform/aws-ecs/