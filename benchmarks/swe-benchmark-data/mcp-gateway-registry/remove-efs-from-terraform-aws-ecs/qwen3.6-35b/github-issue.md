# GitHub Issue: Remove EFS from terraform/aws-ecs deployment

## Title
Remove EFS file system, mount targets, and security groups from terraform/aws-ecs

## Labels
- enhancement
- refactor
- infra

## Description

### Problem Statement
The MCP Gateway Registry terraform/aws-ecs deployment provisions an AWS EFS file system with mount targets, security groups, and six access points (servers, models, logs, agents, auth_config, mcpgw_data). The registry application has migrated away from EFS: it now uses ephemeral container storage for temporary files and DocumentDB for persistent data. The EFS infrastructure remains as unused resources that:

1. Cost money (provisioned throughput if throughput_mode is "provisioned", even bursting mode has a baseline cost).
2. Add complexity to the Terraform state and deployment surface.
3. Require `elasticfilesystem:*` IAM permissions that platform engineers must grant but never use.
4. Still appear in terraform plan output, confusing operators about whether EFS is still needed.

### Proposed Solution
Remove all EFS-related resources from the terraform/aws-ecs module:

1. **Remove the EFS module** (`module.efs`) and its security group + egress rules from `modules/mcp-gateway/storage.tf`.
2. **Remove EFS volume configurations** from the auth-server and mcpgw ECS task definitions in `modules/mcp-gateway/ecs-services.tf` (registry already has EFS volumes removed).
3. **Remove EFS-related variables** (`efs_throughput_mode`, `efs_provisioned_throughput`) from `modules/mcp-gateway/variables.tf`.
4. **Remove EFS outputs** from `modules/mcp-gateway/outputs.tf` and the root `outputs.tf`.
5. **Remove the EFS mount target** from the auth-server `mountPoints` block and the EFS volume block.
6. **Update IAM permissions documentation** in README.md to remove `elasticfilesystem:*`.
7. **Update scripts** that reference EFS IDs from terraform outputs (post-deployment-setup.sh, run-scopes-init-task.sh).
8. **Ensure auth-server and mcpgw** still function without the EFS mounts by verifying that their config files (e.g., scopes.yml) can be provided via the existing Docker image layers or environment-based configuration paths.

### User Stories
- As a platform engineer, I want the terraform plan to not show EFS resources so I see only the infrastructure we actually use.
- As a platform engineer, I want to skip granting `elasticfilesystem:*` IAM permissions so deployment is simpler and less error-prone.
- As a platform engineer, I want the deployment to not incur EFS costs so we save money.

### Acceptance Criteria
- [ ] `module.efs` removed from `modules/mcp-gateway/storage.tf`
- [ ] EFS security group (`aws_vpc_security_group_egress_rule.efs_all_outbound`) removed from `modules/mcp-gateway/storage.tf`
- [ ] `efs_volume_configuration` blocks removed from auth-server and mcpgw ECS task definitions in `modules/mcp-gateway/ecs-services.tf`
- [ ] EFS mount points removed from auth-server container definition
- [ ] `var.efs_throughput_mode` and `var.efs_provisioned_throughput` variables removed from `modules/mcp-gateway/variables.tf`
- [ ] EFS outputs (`efs_id`, `efs_arn`, `efs_access_points`) removed from `modules/mcp-gateway/outputs.tf`
- [ ] EFS outputs (`mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points`) removed from root `outputs.tf`
- [ ] `terraform validate` passes successfully at tag 1.24.4
- [ ] `terraform plan` shows no EFS-related changes (no `aws_efs_file_system`, `aws_efs_mount_target`, `aws_efs_access_point`, `aws_vpc_security_group_*` for EFS)
- [ ] README.md no longer lists `elasticfilesystem:*` in the IAM permissions section
- [ ] Scripts in `terraform/aws-ecs/scripts/` that reference EFS IDs are updated or removed (post-deployment-setup.sh, run-scopes-init-task.sh)
- [ ] `terraform.tfvars.example` has no EFS-related variables (if any exist)
- [ ] No remaining references to `module.efs`, `aws_efs_`, or `efs_` in any `.tf` files under `terraform/aws-ecs/`

### Out of Scope
- Removing DocumentDB (that is a separate migration)
- Modifying the Docker image contents (scope.yml injection is handled by the image build)
- Removing EFS from Helm charts (charts/ directory is a separate deployment surface)
- Removing EFS from Docker Compose (docker/ directory is a separate deployment surface)

### Dependencies
- The auth-server must be able to find scopes.yml at a path other than the EFS mount. If scopes.yml is baked into the Docker image at `/app/auth_server/scopes.yml` (line 822 of ecs-services.tf shows this path exists as an alternative), this change is safe.
- The mcpgw app_log_dir must be writable to the container's ephemeral storage.

### Related Issues
- None identified at this time