# GitHub Issue: Remove EFS from terraform/aws-ecs/

## Title
Remove obsolete EFS file system and mount configuration from terraform/aws-ecs/

## Labels
- infra
- enhancement

## Description

### Problem Statement
`terraform/aws-ecs/` provisions an Amazon EFS file system (`module.efs` in `modules/mcp-gateway/storage.tf`) with mount targets in every private subnet, a dedicated security group, and six access points (`servers`, `models`, `logs`, `agents`, `auth_config`, `mcpgw_data`). This was needed when the registry, auth-server, and mcpgw-server persisted state to a shared network file system.

The application has since moved to S3/DocumentDB for persistent storage. The `registry` ECS service was already migrated off EFS (`ecs-services.tf` shows `mountPoints = []` and `volume = {}` with a comment "EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence"). However, `auth-server` and `mcpgw-server` still mount EFS access points:

- `auth-server` mounts `logs` (at `/app/logs`) and `auth_config` (at `/efs/auth_config`), and reads its scopes file via `SCOPES_CONFIG_PATH=/efs/auth_config/auth_config/scopes.yml`.
- `mcpgw-server` mounts `mcpgw_data` at `/app/data`, but no code in `servers/mcpgw/` writes to that path (verified: it is an unused mount).

Three of the six access points (`servers`, `models`, `agents`) are not mounted by any container at all - they are pure dead infrastructure, provisioned but never consumed.

Keeping EFS around costs money (mount targets, provisioned throughput), adds attack surface (a dedicated security group + NFS ingress rule), and adds operational complexity (a separate `scopes-init` ECS task and Docker image, `docker/Dockerfile.scopes-init`, whose only job is to seed EFS with `scopes.yml`).

### Proposed Solution
Remove the EFS module, its mount targets, security group, and all six access points from `modules/mcp-gateway/storage.tf`. Remove the EFS-related Terraform variables, outputs, and ECS task-definition `volume`/`mountPoints` blocks that reference it. Since `auth-server`'s scopes file is already baked into its container image (`docker/Dockerfile.auth` COPYs `auth_server/scopes.yml` to `/app/scopes.yml`, which is exactly where `registry/common/scopes_loader.py`'s fallback chain looks when `SCOPES_CONFIG_PATH` is unset), dropping the EFS mount and env var override is safe: the app falls back to the image-baked file. Audit logging is already DocumentDB-backed (file-based audit logging is deprecated per `registry/audit/service.py`) and application logs already go to `/var/log/containers/ai-registry` and stdout (captured by the ECS `awslogs` driver), so the `/app/logs` EFS mount is not needed either.

Retire the `scopes-init` ECS one-off task and its Docker image, since its sole purpose was to seed EFS.

### User Stories
- As an operator running `terraform apply` on this stack, I want no EFS resources created so that I am not billed for or responsible for securing an unused file system.
- As an operator running `terraform destroy` and re-`apply`, I want the plan to succeed cleanly with no dangling references to `module.efs`.
- As a developer reading `variables.tf` / `terraform.tfvars.example`, I want no leftover EFS configuration knobs that no longer do anything.

### Acceptance Criteria
- [ ] `module "efs"` and the standalone `aws_vpc_security_group_egress_rule.efs_all_outbound` resource are removed from `modules/mcp-gateway/storage.tf` (file deleted entirely).
- [ ] `efs_throughput_mode` and `efs_provisioned_throughput` variables are removed from `modules/mcp-gateway/variables.tf`.
- [ ] `efs_id`, `efs_arn`, `efs_access_points` outputs are removed from `modules/mcp-gateway/outputs.tf`.
- [ ] `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` outputs are removed from the root `terraform/aws-ecs/outputs.tf`.
- [ ] The `mcp-logs` and `auth-config` `mountPoints`/`volume` blocks are removed from the `auth-server` container/task definition in `ecs-services.tf`, and `SCOPES_CONFIG_PATH` is removed from its environment (or repointed to the image-baked path) so it falls back to the bundled `scopes.yml`.
- [ ] The `mcpgw-data` `mountPoints`/`volume` block is removed from the `mcpgw-server` container/task definition in `ecs-services.tf`.
- [ ] `terraform.tfvars.example` and root `variables.tf` have no dangling references (confirmed there are none today, but re-verify after the change).
- [ ] `scripts/post-deployment-setup.sh` no longer requires `mcp_gateway_efs_id` in its output-validation list and no longer calls `scripts/run-scopes-init-task.sh`.
- [ ] `scripts/run-scopes-init-task.sh`, `docker/Dockerfile.scopes-init`, and the `mcp-gateway-scopes-init` ECR repository/CodeBuild build step (`codebuild.tf`) are removed.
- [ ] `terraform validate` succeeds in `terraform/aws-ecs/`.
- [ ] `terraform plan` succeeds against an existing state and shows only resource *removals* (no unrelated diffs, no plan errors from dangling references).
- [ ] No remaining case-insensitive match for `efs` in `terraform/aws-ecs/**/*.tf` (excluding comments that explain the historical removal, if any are kept for context).

### Out of Scope
- Changes to `registry/common/scopes_loader.py` or any other application source code - the code path already supports operating without `SCOPES_CONFIG_PATH` set.
- Migrating `auth-server` scopes storage to S3 or DocumentDB - the existing DocumentDB-backed scopes repository (used when `storage_backend = "documentdb"`, the ECS default) already covers this; the image-baked YAML fallback covers the `storage_backend = "file"` case.
- Changes to the Helm/EKS deployment path (`charts/`) - it already has no EFS/volume wiring for these paths.
- Changes to `terraform/aws-ecs/modules/mcp-gateway/documentdb-elastic.tf.disabled` or any other already-disabled resource.

### Dependencies
- None. This is a self-contained infrastructure cleanup.

### Related Issues
- #1286 (this issue)
